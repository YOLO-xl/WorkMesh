import pytest

from database.evidence_repository import EvidenceRepository
from services.evidence_service import EvidenceService


LOG_RECORDS = [
    {
        "evidence_type": "log",
        "record_id": "log-001",
        "timestamp": "2026-11-01T09:14:50Z",
        "uid": "10086231",
        "request_id": "req-001",
        "service": "直播SDK",
        "level": "ERROR",
        "error_code": "E1001",
        "message": "rtmp connect timeout",
    },
    {
        "evidence_type": "log",
        "record_id": "log-002",
        "timestamp": "2026-11-01T09:20:00Z",
        "uid": "10086231",
        "service": "直播SDK",
        "level": "ERROR",
        "error_code": "E1001",
        "message": "player init failed",
    },
    {
        "evidence_type": "log",
        "record_id": "log-003",
        "timestamp": "2026-11-01T12:00:00Z",
        "uid": "99999",
        "service": "其他服务",
        "error_code": "E9000",
        "message": "无关日志",
    },
]


@pytest.fixture
def service(db_path):
    return EvidenceService(db_path=db_path)


def test_ingest_connector_records_and_build_context(
    service, db_path, workspace_id, make_feedback
):
    feedback_id = make_feedback()

    ingest = service.ingest_connector_records(
        workspace_id,
        LOG_RECORDS,
        source_name="本地证据文件",
        source_type="log",
        connector_type="file",
    )

    assert ingest["inserted"] == 3
    assert ingest["failed"] == 0

    context = service.build_context(
        workspace_id,
        feedback_id,
        window_minutes=30,
        min_score=30,
    )

    assert context["counts"]["candidates"] == 2
    assert context["counts"]["matched"] == 2
    assert context["counts"]["strong"] == 2
    assert context["counts"]["persisted"] == 2

    labels = {fact["label"] for fact in context["facts"]}
    assert labels == {"UID", "错误码", "服务/产品"}
    assert all(
        fact["status"] == "已验证事实" for fact in context["facts"]
    )

    statuses = {item["status"] for item in context["hypotheses"]}
    assert "待验证" in statuses

    assert "关联证据" in context["summary"]

    links = EvidenceRepository(db_path).list_links(
        workspace_id, "feedback", feedback_id
    )
    assert len(links) == 2


def test_build_context_is_idempotent(
    service, db_path, workspace_id, make_feedback
):
    feedback_id = make_feedback()

    service.ingest_connector_records(
        workspace_id, LOG_RECORDS, source_name="本地证据文件"
    )

    service.build_context(workspace_id, feedback_id)
    service.build_context(workspace_id, feedback_id)

    links = EvidenceRepository(db_path).list_links(
        workspace_id, "feedback", feedback_id
    )

    assert len(links) == 2


def test_build_context_without_evidence_reports_gap(
    service, workspace_id, make_feedback
):
    feedback_id = make_feedback()

    context = service.build_context(workspace_id, feedback_id)

    assert context["counts"]["matched"] == 0
    assert context["matches"] == []
    assert any(
        "接入" in item["statement"]
        for item in context["hypotheses"]
    )


def test_build_context_raises_for_unknown_feedback(service, workspace_id):
    with pytest.raises(LookupError):
        service.build_context(workspace_id, "not-exists")


def test_build_context_includes_related_feedback(
    service, workspace_id, make_feedback
):
    first_id = make_feedback(description="第一条反馈")
    second_id = make_feedback(
        uid="10086231",
        error_code="E1002",
        feedback_time="2026-11-01T10:10:00Z",
        description="第二条反馈",
    )

    context = service.build_context(
        workspace_id,
        first_id,
        window_minutes=120,
        min_score=30,
    )

    matched_ids = {
        item["evidence"].record_id for item in context["matches"]
    }

    assert second_id in matched_ids
    assert context["counts"]["related_feedback"] >= 1

    related_match = next(
        item for item in context["matches"]
        if item["evidence"].record_id == second_id
    )
    assert related_match["evidence"].evidence_type == "feedback"
    assert related_match["persisted"] is False
