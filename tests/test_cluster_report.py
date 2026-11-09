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

    history = repository.issue_history(
        workspace_id, cluster["product"], cluster["error_code"]
    )
    assert [item["id"] for item in history] == [report_id]

    stored = repository.get(report_id)
    assert stored["markdown"].startswith("# 直播SDK")
    assert stored["payload"]["sampled_count"] == 2

    stats = repository.stats(workspace_id)
    assert stats["total"] == 1
    assert stats["covered_clusters"] == 1

    assert repository.delete(report_id) is True
    assert repository.get(report_id) is None


def test_issue_history_keeps_multiple_snapshots(
    db_path, workspace_id, cluster, cluster_report
):
    repository = ClusterReportRepository(db_path)

    repository.save(workspace_id, cluster, cluster_report)

    second = dict(cluster_report)
    second["markdown"] = cluster_report["markdown"] + "\n<!-- 第二轮快照 -->\n"
    repository.save(workspace_id, cluster, second)

    history = repository.issue_history(
        workspace_id, cluster["product"], cluster["error_code"]
    )

    assert len(history) == 2
    assert history[0]["id"] != history[1]["id"]


def test_sample_size_can_exceed_three(
    db_path, workspace_id, make_feedback
):
    for _ in range(6):
        make_feedback(
            product="直播SDK",
            error_code="E1001",
            description="进入直播间后一直加载无法播放",
        )

    data = ClusterService(db_path=db_path).build(
        workspace_id, min_size=2, with_evidence=False
    )
    report = ClusterReportService(db_path=db_path).build(
        workspace_id, data["clusters"][0], sample_size=5
    )

    assert report["sampled_count"] == 5
    assert "从簇内抽样 5 条反馈做单条诊断" in report["markdown"]


def test_issue_history_separates_no_code_titles(
    db_path, workspace_id, cluster, cluster_report
):
    repository = ClusterReportRepository(db_path)
    no_code_cluster = {**cluster, "error_code": "", "title": "直播SDK · 加载"}

    first = {**cluster_report, "title": "[簇报告] 直播SDK · 加载"}
    second = {
        **cluster_report,
        "title": "[簇报告] 直播SDK · 黑屏",
        "markdown": cluster_report["markdown"] + "\n<!-- 黑屏 -->\n",
    }

    repository.save(workspace_id, no_code_cluster, first)
    repository.save(workspace_id, no_code_cluster, second)

    history = repository.issue_history(
        workspace_id, no_code_cluster["product"], "", title=first["title"]
    )

    assert len(history) == 1
    assert history[0]["title"] == first["title"]
