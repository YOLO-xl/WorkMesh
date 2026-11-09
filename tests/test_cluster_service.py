import pytest

from services.cluster_service import ClusterService, text_similarity
from services.evidence_service import EvidenceService


@pytest.fixture
def cluster_service(db_path):
    return ClusterService(db_path=db_path)


def test_same_product_and_error_code_forms_one_cluster(
    cluster_service, make_feedback, workspace_id
):
    for index in range(3):
        make_feedback(
            uid=f"10086{index}",
            error_code="E1001",
            description="进入直播间后一直加载无法播放",
        )

    result = cluster_service.build(workspace_id, with_evidence=False)
    clusters = result["clusters"]

    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster["size"] == 3
    assert cluster["unique_users"] == 3
    assert cluster["title"].startswith("直播SDK")
    assert cluster["severity"] == "low"
    assert result["stats"]["coverage"] == 1.0


def test_different_error_codes_split_into_clusters(
    cluster_service, make_feedback, workspace_id
):
    for _ in range(2):
        make_feedback(error_code="E1001")
    for _ in range(2):
        make_feedback(error_code="E2001", description="直播播放过程中频繁卡顿")

    result = cluster_service.build(workspace_id, with_evidence=False)

    assert len(result["clusters"]) == 2
    assert {item["error_code"] for item in result["clusters"]} == {"E1001", "E2001"}


def test_small_group_becomes_singleton(
    cluster_service, make_feedback, workspace_id
):
    make_feedback(error_code="E1001")
    make_feedback(error_code="E1001")
    make_feedback(error_code="E9999", description="只有一条的怪问题")

    result = cluster_service.build(workspace_id, min_size=2, with_evidence=False)

    assert len(result["clusters"]) == 1
    assert len(result["singletons"]) == 1
    assert result["singletons"][0]["error_code"] == "E9999"


def test_phenomena_grouped_by_keyword(
    cluster_service, make_feedback, workspace_id
):
    make_feedback(error_code="E1001", description="进入直播间后一直加载无法播放")
    make_feedback(error_code="E1001", description="直播间加载不出来")
    make_feedback(error_code="E1001", description="进入直播间后黑屏")

    result = cluster_service.build(workspace_id, with_evidence=False)
    phenomena = result["clusters"][0]["phenomena"]

    assert phenomena[0]["label"] == "加载"
    assert phenomena[0]["count"] == 2
    assert any(item["label"] == "黑屏" for item in phenomena)


def test_cluster_counts_log_evidence(
    cluster_service, make_feedback, workspace_id, db_path
):
    make_feedback(
        error_code="E1001", feedback_time="2026-11-01 09:10:00"
    )
    make_feedback(
        error_code="E1001",
        uid="10086232",
        feedback_time="2026-11-01 09:30:00",
    )
    service = EvidenceService(db_path=db_path)

    service.ingest_connector_records(
        workspace_id,
        [
            {
                "evidence_type": "log",
                "record_id": "log-9001",
                "timestamp": "2026-11-01T09:20:00Z",
                "service": "直播SDK",
                "error_code": "E1001",
                "message": "player init failed",
            }
        ],
        source_name="测试日志",
        source_type="log",
        connector_type="file",
    )

    result = cluster_service.build(workspace_id)
    cluster = result["clusters"][0]

    assert cluster["evidence"]["log"] == 1
    assert cluster["evidence"]["total"] >= 1


def test_empty_workspace_returns_zero_stats(cluster_service, workspace_id):
    result = cluster_service.build(workspace_id)

    assert result["clusters"] == []
    assert result["singletons"] == []
    assert result["stats"]["total"] == 0
    assert result["stats"]["cluster_count"] == 0


def test_text_similarity_is_symmetric_and_bounded():
    assert text_similarity(None, "加载") == 0.0
    assert 0.0 <= text_similarity("直播间加载失败", "直播间加载不出来") <= 1.0
    assert text_similarity("直播间加载失败", "直播间加载失败") == 1.0
