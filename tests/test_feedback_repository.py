import sqlite3
from pathlib import Path

from database.migration import CURRENT_VERSION, initialize_database
from database.feedback_repository import FeedbackRepository


def test_bulk_insert_handles_duplicate_feedback_id(tmp_path):
    db_path = Path(tmp_path) / "test_workmesh.db"

    # 初始化数据库
    assert initialize_database(db_path) == CURRENT_VERSION

    repo = FeedbackRepository(db_path)

    # 创建工作空间和文件数据源
    workspace_id = repo.get_default_workspace()

    source_id = repo.get_or_create_file_source(
        workspace_id,
        source_type="csv",
    )

    source_id_again = repo.get_or_create_file_source(
        workspace_id,
        source_type="csv",
    )

    assert source_id == source_id_again

    # 创建导入批次
    batch_id = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="test.csv",
        total_records=3,
    )

    records = [
        {
            "feedback_id": "TEST-001",
            "uid": "10001",
            "feedback_time": "2026-11-03T10:00:00Z",
            "source": "App",
            "product": "WorkMesh",
            "version": "1.0.0",
            "platform": "iOS",
            "description": "登录失败",
            "raw_payload": {"原始反馈ID": "TEST-001"},
        },
        {
            "feedback_id": "TEST-002",
            "uid": "10002",
            "feedback_time": "2026-11-03T11:00:00Z",
            "source": "App",
            "product": "WorkMesh",
            "version": "1.0.0",
            "platform": "Android",
            "description": "页面加载缓慢",
            "raw_payload": {"原始反馈ID": "TEST-002"},
        },
        {
            "feedback_id": "TEST-001",
            "uid": "10003",
            "feedback_time": "2026-11-03T12:00:00Z",
            "source": "App",
            "product": "WorkMesh",
            "version": "1.0.0",
            "platform": "iOS",
            "description": "重复 ID 测试",
        },
    ]

    result = repo.bulk_insert_feedback(batch_id, records)

    assert result["total_records"] == 3
    assert result["success_records"] == 2
    assert result["failed_records"] == 1
    assert result["status"] == "partially_failed"

    query_result = repo.list_feedback(
        workspace_id,
        page=1,
        page_size=20,
    )

    assert query_result["total"] == 2


def test_has_imported_file_checks_completed_and_partially_failed(tmp_path):
    db_path = Path(tmp_path) / "test_import_hash.db"

    initialize_database(db_path)
    repo = FeedbackRepository(db_path)

    workspace_id = repo.get_default_workspace()
    source_id = repo.get_or_create_file_source(
        workspace_id,
        source_type="csv",
    )

    file_hash = "test_sha256_hash"

    # 尚未导入的文件应返回 False
    assert repo.has_imported_file(source_id, file_hash) is False

    # 创建已完成的导入批次
    completed_batch = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="completed.csv",
        file_hash=file_hash,
        total_records=0,
    )

    repo.bulk_insert_feedback(completed_batch, [])

    assert repo.has_imported_file(source_id, file_hash) is True

    # 新文件哈希，创建部分失败批次
    partial_hash = "partial_sha256_hash"

    partial_batch = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="partial.csv",
        file_hash=partial_hash,
        total_records=2,
    )

    repo.bulk_insert_feedback(
        partial_batch,
        [
            {
                "feedback_id": "DUPLICATE-001",
                "description": "第一条记录",
            },
            {
                "feedback_id": "DUPLICATE-001",
                "description": "重复记录",
            },
        ],
    )

    assert repo.has_imported_file(source_id, partial_hash) is True


def test_bulk_insert_marks_batch_failed_when_all_records_fail(tmp_path):
    db_path = Path(tmp_path) / "test_all_failed.db"
    initialize_database(db_path)

    repo = FeedbackRepository(db_path)
    workspace_id = repo.get_default_workspace()
    source_id = repo.get_or_create_file_source(
        workspace_id,
        source_type="csv",
    )

    batch_id = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="all_failed.csv",
        total_records=2,
    )

    records = [
        {
            "feedback_id": "FAIL-001",
            "description": "第一条",
            "raw_payload": "invalid",
        },
        {
            "feedback_id": "FAIL-002",
            "description": "第二条",
            "raw_payload": "invalid",
        },
    ]

    result = repo.bulk_insert_feedback(batch_id, records)

    assert result["total_records"] == 2
    assert result["success_records"] == 0
    assert result["failed_records"] == 2
    assert result["status"] == "failed"
    assert len(result["errors"]) == 2

    query_result = repo.list_feedback(
        workspace_id,
        page=1,
        page_size=20,
    )
    assert query_result["total"] == 0


def test_bulk_insert_rolls_back_on_database_error(tmp_path, monkeypatch):
    db_path = Path(tmp_path) / "test_rollback.db"
    initialize_database(db_path)

    repo = FeedbackRepository(db_path)
    workspace_id = repo.get_default_workspace()
    source_id = repo.get_or_create_file_source(
        workspace_id,
        source_type="csv",
    )

    batch_id = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="rollback.csv",
        total_records=2,
    )

    original_connect = repo._connect
    connection = original_connect()

    class ConnectionProxy:
        def __init__(self, conn):
            self.conn = conn
            self.insert_count = 0

        def execute(self, sql, parameters=()):
            if "INSERT INTO feedback_records" in sql:
                self.insert_count += 1
                if self.insert_count == 2:
                    raise sqlite3.OperationalError(
                        "模拟数据库写入异常"
                    )
            return self.conn.execute(sql, parameters)

        def commit(self):
            return self.conn.commit()

        def rollback(self):
            return self.conn.rollback()

        def close(self):
            return self.conn.close()

    proxy = ConnectionProxy(connection)
    monkeypatch.setattr(repo, "_connect", lambda: proxy)

    records = [
        {"feedback_id": "ROLLBACK-001", "description": "第一条"},
        {"feedback_id": "ROLLBACK-002", "description": "第二条"},
    ]

    try:
        try:
            repo.bulk_insert_feedback(batch_id, records)
            assert False, "预期发生 sqlite3.OperationalError"
        except sqlite3.OperationalError as exc:
            assert "模拟数据库写入异常" in str(exc)
    finally:
        monkeypatch.setattr(repo, "_connect", original_connect)

    query_result = repo.list_feedback(
        workspace_id,
        page=1,
        page_size=20,
    )
    assert query_result["total"] == 0

    with original_connect() as check_conn:
        batch = check_conn.execute(
            "SELECT status, error_summary FROM import_batches WHERE id = ?",
            (batch_id,),
        ).fetchone()

    assert batch["status"] == "failed"
    assert batch["error_summary"] is not None
