"""问题簇报告的生成、归档与检索测试。"""

import pytest

from database.cluster_report_repository import ClusterReportRepository
from services.cluster_report_service import ClusterReportService
from services.cluster_service import ClusterService


@pytest.fixture
def cluster(db_path, workspace_id, make_feedback):
    for _ in range(3):
        make_feedback(
            product="直播SDK",
            error_code="E1001",
            description="进入直播间后一直加载无法播放",
        )

    data = ClusterService(db_path=db_path).build(
        workspace_id, min_size=2, with_evidence=False
    )
    assert data["clusters"]
    return data["clusters"][0]


@pytest.fixture
def cluster_report(db_path, workspace_id, cluster):
    report = ClusterReportService(db_path=db_path).build(
        workspace_id, cluster, sample_size=2
    )
    return report


def test_cluster_report_contains_facts_and_samples(cluster_report, cluster):
    markdown = cluster_report["markdown"]

    assert cluster_report["title"].startswith("[簇报告]")
    assert cluster_report["generated_by"] == "rule"
    assert cluster_report["sampled_count"] == 2
    assert cluster["size"] == 3

    assert "影响面：3 条反馈" in markdown
    assert "簇表示" in markdown
    assert "## 关键事实" in markdown
    assert "## 抽样诊断" in markdown
    assert "## 结论边界" in markdown
    assert "不代表全部 3 条都做过诊断" in markdown


def test_cluster_report_confidence_reflects_sampled_evidence(cluster_report):
    assert cluster_report["confidence"] in ("medium", "high")
    assert cluster_report["evidence_refs"]


def test_cluster_report_repository_roundtrip(
    db_path, workspace_id, cluster, cluster_report
):
    repository = ClusterReportRepository(db_path)

    report_id, created = repository.save(workspace_id, cluster, cluster_report)
    same_id, created_again = repository.save(workspace_id, cluster, cluster_report)

    assert created is True
    assert created_again is False
    assert report_id == same_id

    listed = repository.list_reports(workspace_id, keyword="E1001")
    assert len(listed) == 1
    assert listed[0]["size"] == 3

    stored = repository.get(report_id)
    assert stored["markdown"].startswith("# 直播SDK")
    assert stored["payload"]["sampled_count"] == 2

    stats = repository.stats(workspace_id)
    assert stats["total"] == 1
    assert stats["covered_clusters"] == 1

    assert repository.delete(report_id) is True
    assert repository.get(report_id) is None
