from pathlib import Path
from tempfile import TemporaryDirectory

from database.migration import initialize_database
from database.feedback_repository import FeedbackRepository


with TemporaryDirectory() as temp_dir:
    db_path = Path(temp_dir) / "test_workmesh.db"

    # 初始化独立测试数据库
    print("数据库迁移版本：", initialize_database(db_path))

    repo = FeedbackRepository(db_path)

    # 创建默认工作空间
    workspace_id = repo.get_default_workspace()

    # 创建并复用文件数据源
    source_id = repo.get_or_create_file_source(
        workspace_id,
        source_type="csv",
    )
    source_id_again = repo.get_or_create_file_source(
        workspace_id,
        source_type="csv",
    )

    print("数据源复用：", source_id == source_id_again)

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
            # 故意重复 TEST-001，验证唯一约束
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

    print("导入结果：", result)

    print(
        "批次统计：",
        repo.get_batch_statistics(batch_id),
    )

    query_result = repo.list_feedback(
        workspace_id,
        page=1,
        page_size=20,
    )

    print("数据库实际记录数：", query_result["total"])
    print("查询结果：", query_result["records"])

    assert result["success_records"] == 2
    assert result["failed_records"] == 1
    assert result["status"] == "partially_failed"
    assert query_result["total"] == 2

    print("\nRepository 基础测试全部通过。")
