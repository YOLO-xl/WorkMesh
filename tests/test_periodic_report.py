"""周期报告的归档、去重与聚合口径。"""

from database.periodic_report_repository import PeriodicReportRepository
from services.periodic_report_service import PeriodicReportService


def _build(repo, workspace_id, start="2026-11-01", end="2026-11-03", product=None):
    service = PeriodicReportService(repo.db_path)
    return service.build(workspace_id, start, end, product)


def test_periodic_report_aggregates_feedback_and_issues(
    db_path, workspace_id, make_feedback
):
    make_feedback(uid="u1", error_code="E1001")
    make_feedback(uid="u2", error_code="E1001")
    make_feedback(
        uid="u3",
        product="账号服务",
        error_code="E4001",
        description="登录后一直转圈",
    )
    make_feedback(
        uid="u4",
        error_code=None,
        description="进入直播间一直黑屏转圈",
    )
    make_feedback(
        uid="u5",
        feedback_time="2026-11-05T09:15:22Z",
        error_code="E1001",
    )

    result = _build(PeriodicReportRepository(db_path), workspace_id)
    stats = result["stats"]

    assert stats["feedback_total"] == 4
    assert stats["unique_users"] == 4
    assert stats["daily"] == {"2026-11-01": 4}
    assert stats["top_issues"][0]["key"] == "直播SDK · E1001"
    assert stats["top_issues"][0]["count"] == 2
    assert stats["top_issues"][0]["users"] == 2
    assert stats["reports"]["pending"] == 0
    assert "结论边界" in result["markdown"]
    assert "不能直接等同于根因" in result["markdown"]


def test_periodic_report_product_filter(db_path, workspace_id, make_feedback):
    make_feedback(product="直播SDK")
    make_feedback(product="账号服务", error_code="E4001")

    result = _build(
        PeriodicReportRepository(db_path),
        workspace_id,
        product="直播SDK",
    )

    assert result["stats"]["feedback_total"] == 1
    assert result["stats"]["product"] == "直播SDK"
    assert "[周期报告]" in result["title"]
    assert "直播SDK" in result["title"]


def test_periodic_report_archive_is_deduplicated(
    db_path, workspace_id, make_feedback
):
    make_feedback()
    repository = PeriodicReportRepository(db_path)
    result = _build(repository, workspace_id)

    report_id, created = repository.save(
        workspace_id,
        result["period_start"],
        result["period_end"],
        result["stats"]["product"],
        result,
    )
    assert created is True

    same_id, created_again = repository.save(
        workspace_id,
        result["period_start"],
        result["period_end"],
        result["stats"]["product"],
        result,
    )
    assert created_again is False
    assert same_id == report_id

    reports = repository.list_reports(workspace_id)
    assert len(reports) == 1
    assert reports[0]["stats"]["feedback_total"] == 1
    assert "结论边界" in reports[0]["markdown"]


def test_periodic_report_delete_and_stats(
    db_path, workspace_id, make_feedback
):
    make_feedback()
    repository = PeriodicReportRepository(db_path)
    result = _build(repository, workspace_id)
    report_id, _ = repository.save(
        workspace_id,
        result["period_start"],
        result["period_end"],
        result["stats"]["product"],
        result,
    )

    assert repository.stats(workspace_id)["total"] == 1
    assert repository.get(report_id)["id"] == report_id
    assert repository.delete(report_id) is True
    assert repository.get(report_id) is None
    assert repository.delete(report_id) is False
