import itertools

import pytest

from database.feedback_repository import FeedbackRepository
from database.migration import initialize_database


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "workmesh.db"
    initialize_database(path)
    return path


@pytest.fixture
def feedback_repository(db_path):
    return FeedbackRepository(db_path)


@pytest.fixture
def workspace_id(feedback_repository):
    return feedback_repository.get_default_workspace()


@pytest.fixture
def make_feedback(feedback_repository, workspace_id):
    """插入一条反馈并返回其 ID；字段可通过关键字覆盖。"""

    source_id = feedback_repository.get_or_create_file_source(
        workspace_id, source_type="csv"
    )
    counter = itertools.count(1)

    def _make(**overrides):
        index = next(counter)

        record = {
            "feedback_id": f"F{index:03d}",
            "uid": "10086231",
            "feedback_time": "2026-11-01T09:15:22Z",
            "source": "客服工单",
            "product": "直播SDK",
            "version": "8.6.2",
            "platform": "iOS",
            "device": "iPhone 15",
            "error_code": "E1001",
            "description": "进入直播间后一直加载无法播放",
        }
        record.update(overrides)

        batch_id = feedback_repository.create_import_batch(
            workspace_id=workspace_id,
            source_id=source_id,
            file_name=f"batch-{index}.csv",
            total_records=1,
        )

        result = feedback_repository.bulk_insert_feedback(
            batch_id, [record]
        )
        assert result["success_records"] == 1

        rows = feedback_repository.list_feedback(
            workspace_id, page=1, page_size=50
        )["records"]

        return next(
            row["id"]
            for row in rows
            if row["external_id"] == record["feedback_id"]
        )

    return _make
