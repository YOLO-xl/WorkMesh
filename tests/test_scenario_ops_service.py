"""故障场景运营统计测试。"""

from services.scenario_ops_service import ScenarioOpsService


def test_overview_lists_enabled_scenarios_and_config_health():
    service = ScenarioOpsService()

    overview = service.overview()

    assert overview["config_error_count"] == 0
    assert overview["enabled_count"] >= 5
    ids = {item["id"] for item in overview["scenarios"]}
    assert "payment.refund_consultation" in ids
    refund = next(
        item for item in overview["scenarios"]
        if item["id"] == "payment.refund_consultation"
    )
    assert refund["connector_sources"]
    assert refund["required_sources"] == ["feedback_recent"]


def test_evaluate_reports_coverage_and_partial_status(
    db_path, workspace_id, make_feedback
):
    for index in range(3):
        make_feedback(
            external_id=f"E6003-{index}",
            product="支付中心",
            error_code="E6003",
            uid=f"2100029{index}",
            description="申请退款三天了，进度一直显示处理中",
        )

    service = ScenarioOpsService(db_path=db_path)
    result = service.evaluate(workspace_id, limit=50)

    assert result["evaluated"] == 3
    assert result["summary"]["specific_matched"] == 3
    assert result["summary"]["coverage_rate"] == 1.0
    assert result["summary"]["partial"] == 3
    assert result["summary"]["blocked"] == 0

    refund = next(
        item for item in result["scenario_stats"]
        if item["id"] == "payment.refund_consultation"
    )
    assert refund["matched"] == 3
    assert refund["partial"] == 3
    assert all(
        row["status"] == "partial"
        for row in result["rows"]
        if row["scenario_id"] == "payment.refund_consultation"
    )


def test_evaluate_keeps_fallback_rows_visible(
    db_path, workspace_id, make_feedback
):
    make_feedback(
        external_id="UNKNOWN-1",
        product="未知产品",
        error_code="E9999",
        description="页面打不开",
    )

    result = ScenarioOpsService(db_path=db_path).evaluate(workspace_id, limit=20)

    assert result["summary"]["fallback"] == 1
    assert result["rows"][0]["scenario_id"] == "generic.triage"
    assert result["rows"][0]["scenario_name"] == "通用分诊"
