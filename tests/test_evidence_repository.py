import sqlite3
from pathlib import Path

import pytest

from core.evidence import Evidence
from database.evidence_repository import EvidenceRepository
from database.migration import CURRENT_VERSION, initialize_database


def make_log(record_id="log-001", **overrides):
    data = {
        "evidence_type": "log",
        "record_id": record_id,
        "timestamp": "2026-11-01T09:14:50Z",
        "uid": "10086231",
        "trace_id": "trace-001",
        "service": "live-service",
        "level": "ERROR",
        "error_code": "E1001",
        "message": "rtmp connect timeout",
    }
    data.update(overrides)
    return Evidence.from_dict(data)


@pytest.fixture
def repository(db_path, workspace_id):
    repo = EvidenceRepository(db_path)
    source_id = repo.get_or_create_source(
        workspace_id,
        name="本地证据文件",
        source_type="log",
        connector_type="file",
    )
    return repo, workspace_id, source_id


def test_database_version_after_migration(db_path):
    assert initialize_database(db_path) == CURRENT_VERSION

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        conn.close()

    assert "evidence_records" in tables
    assert "evidence_links" in tables


def test_migration_upgrades_existing_v1_database(tmp_path):
    """模拟线上已有 v1 库，验证可以增量升级到最新版本。"""

    db_path = Path(tmp_path) / "legacy.db"

    schema_sql = (
        Path(__file__).resolve().parent.parent
        / "database"
        / "schema.sql"
    ).read_text(encoding="utf-8")

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(schema_sql)
        conn.execute(
            """
            INSERT INTO schema_migrations
            (version, description, applied_at)
            VALUES (1, 'Initial database schema', '2026-11-01T00:00:00Z')
            """
        )
        conn.commit()
    finally:
        conn.close()

    assert initialize_database(db_path) == CURRENT_VERSION

    conn = sqlite3.connect(db_path)
    try:
        versions = [
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
    finally:
        conn.close()

    assert versions == [1, CURRENT_VERSION]


def test_insert_evidence_reuses_existing_record(repository):
    repo, workspace_id, source_id = repository
    evidence = make_log()

    first = repo.insert_evidence(workspace_id, source_id, evidence)
    second = repo.insert_evidence(workspace_id, source_id, make_log())

    assert first["inserted"] is True
    assert second["inserted"] is False
    assert first["evidence_id"] == second["evidence_id"]


def test_insert_evidence_deduplicates_by_content_hash(repository):
    repo, workspace_id, source_id = repository

    first = repo.insert_evidence(
        workspace_id, source_id, make_log(record_id=None)
    )
    second = repo.insert_evidence(
        workspace_id, source_id, make_log(record_id=None)
    )

    assert first["inserted"] is True
    assert second["inserted"] is False


def test_bulk_insert_reports_counts(repository):
    repo, workspace_id, source_id = repository

    result = repo.bulk_insert_evidence(
        workspace_id,
        source_id,
        [
            make_log("log-001"),
            make_log("log-002"),
            make_log("log-001"),
        ],
    )

    assert result["total"] == 3
    assert result["inserted"] == 2
    assert result["skipped"] == 1
    assert result["failed"] == 0
    assert repo.count_evidence(workspace_id) == 2


def test_find_candidates_matches_identity_keys(repository):
    repo, workspace_id, source_id = repository

    repo.bulk_insert_evidence(
        workspace_id,
        source_id,
        [
            make_log("log-001"),                       # 同 UID / Trace
            make_log("log-002", uid="99999"),          # 仅同 Trace
            make_log(
                "log-003",
                uid="88888",
                trace_id="trace-other",
                error_code="E9000",
                timestamp="2026-11-01T12:00:00Z",
            ),
        ],
    )

    anchor_filter = {
        "trace_id": "trace-001",
        "uid": "10086231",
        "error_code": "E1001",
        "start": "2026-11-01T08:45:00Z",
        "end": "2026-11-01T09:45:00Z",
    }

    candidates = repo.find_candidates(workspace_id, anchor_filter)

    ids = {item.record_id for item in candidates}
    assert ids == {"log-001", "log-002"}


def test_find_candidates_limits_service_matches_to_time_window(repository):
    """服务名相同但时间远离的日志不应进入候选。"""

    repo, workspace_id, source_id = repository

    repo.bulk_insert_evidence(
        workspace_id,
        source_id,
        [
            make_log("log-in-window"),
            make_log(
                "log-far-away",
                uid="88888",
                error_code="E2001",
                timestamp="2026-11-01T18:00:00Z",
            ),
        ],
    )

    anchor_filter = {
        "uid": "10086231",
        "error_code": "E1001",
        "service": "live-service",
        "start": "2026-11-01T08:45:00Z",
        "end": "2026-11-01T09:45:00Z",
    }

    candidates = repo.find_candidates(workspace_id, anchor_filter)

    assert [item.record_id for item in candidates] == ["log-in-window"]


def test_find_candidates_returns_empty_without_conditions(repository):
    repo, workspace_id, _ = repository

    assert repo.find_candidates(workspace_id, {}) == []


def test_replace_links_and_list_linked_evidence(repository, feedback_repository, make_feedback):
    repo, workspace_id, source_id = repository

    result = repo.bulk_insert_evidence(
        workspace_id, source_id, [make_log("log-001")]
    )
    assert result["inserted"] == 1

    evidence = repo.list_evidence(workspace_id)[0]

    feedback_id = make_feedback()

    repo.replace_links(
        workspace_id=workspace_id,
        anchor_type="feedback",
        anchor_id=feedback_id,
        links=[
            {
                "evidence_id": evidence.evidence_id,
                "score": 55.0,
                "strength": "strong",
                "matched_keys": ["uid", "error_code"],
                "reasons": ["UID 一致：10086231"],
            }
        ],
    )

    links = repo.list_links(workspace_id, "feedback", feedback_id)
    assert len(links) == 1
    assert links[0]["matched_keys"] == ["uid", "error_code"]

    detail = repo.list_linked_evidence(workspace_id, "feedback", feedback_id)
    assert detail[0]["evidence_id"] == evidence.evidence_id
    assert detail[0]["message"] == "rtmp connect timeout"
    assert detail[0]["link_score"] == 55.0


def test_replace_links_rebuilds_instead_of_duplicating(repository, make_feedback):
    repo, workspace_id, source_id = repository

    repo.bulk_insert_evidence(
        workspace_id, source_id, [make_log("log-001"), make_log("log-002")]
    )
    evidences = repo.list_evidence(workspace_id)

    feedback_id = make_feedback()

    repo.replace_links(
        workspace_id,
        "feedback",
        feedback_id,
        [
            {
                "evidence_id": evidences[0].evidence_id,
                "score": 50,
                "strength": "medium",
            },
        ],
    )
    repo.replace_links(
        workspace_id,
        "feedback",
        feedback_id,
        [
            {"evidence_id": evidences[0].evidence_id, "score": 60, "strength": "strong"},
            {"evidence_id": evidences[1].evidence_id, "score": 40, "strength": "medium"},
        ],
    )

    links = repo.list_links(workspace_id, "feedback", feedback_id)

    assert len(links) == 2


def test_replace_links_rejects_unknown_anchor_type(repository):
    repo, workspace_id, _ = repository

    with pytest.raises(ValueError, match="锚点类型"):
        repo.replace_links(workspace_id, "unknown", "x", [])
