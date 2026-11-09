"""报告处理记录（状态 / 负责人 / 备注）的回归测试。"""

import pytest

from database.connection import get_connection
from database.report_tracking_repository import ReportTrackingRepository


@pytest.fixture
def tracking_repository(db_path):
    return ReportTrackingRepository(db_path)


def test_migration_creates_report_tracking(db_path):
    conn = get_connection(db_path)
    rows = conn.execute("PRAGMA table_info(report_tracking)").fetchall()
    columns = {row["name"] for row in rows}

    assert {"workspace_id", "report_scope", "report_id", "status", "owner", "note",
            "updated_at"} <= columns
    assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 5


def test_untracked_report_defaults_to_pending(
    tracking_repository, workspace_id
):
    entry = tracking_repository.get(workspace_id, "feedback", "unknown-report")

    assert entry["status"] == "pending"
    assert entry["owner"] is None
    assert entry["note"] is None
    assert entry["updated_at"] is None


def test_upsert_persists_status_owner_and_note(
    tracking_repository, workspace_id
):
    entry = tracking_repository.upsert(
        workspace_id,
        "cluster",
        "report-1",
        status="handled",
        owner="  张三  ",
        note="  已联系研发，等待版本修复  ",
    )

    assert entry["status"] == "handled"
    assert entry["owner"] == "张三"
    assert entry["note"] == "已联系研发，等待版本修复"
    assert entry["updated_at"]

    changed = tracking_repository.upsert(
        workspace_id,
        "cluster",
        "report-1",
        status="ignored",
        owner="李四",
        note="重复问题，合并处理",
    )

    assert changed["status"] == "ignored"
    assert changed["owner"] == "李四"
    assert changed["note"] == "重复问题，合并处理"


def test_get_many_merges_defaults_and_rows(
    tracking_repository, workspace_id
):
    tracking_repository.upsert(
        workspace_id, "feedback", "report-1", status="handled", owner="张三"
    )

    entries = tracking_repository.get_many(
        workspace_id, "feedback", ["report-1", "report-2"]
    )

    assert entries["report-1"]["status"] == "handled"
    assert entries["report-2"]["status"] == "pending"


def test_stats_counts_each_status(tracking_repository, workspace_id):
    tracking_repository.upsert(workspace_id, "feedback", "a", status="pending")
    tracking_repository.upsert(workspace_id, "feedback", "b", status="handled")
    tracking_repository.upsert(workspace_id, "cluster", "c", status="ignored")

    assert tracking_repository.stats(workspace_id) == {
        "pending": 1,
        "handled": 1,
        "ignored": 1,
    }


def test_delete_removes_tracking_row(tracking_repository, workspace_id):
    tracking_repository.upsert(
        workspace_id, "feedback", "report-1", status="handled"
    )

    assert tracking_repository.delete(workspace_id, "feedback", "report-1") is True
    assert tracking_repository.get(workspace_id, "feedback", "report-1")[
        "status"
    ] == "pending"


def test_rejects_unknown_scope_and_status(tracking_repository, workspace_id):
    with pytest.raises(ValueError):
        tracking_repository.get(workspace_id, "unknown", "report-1")

    with pytest.raises(ValueError):
        tracking_repository.upsert(
            workspace_id, "feedback", "report-1", status="done"
        )
