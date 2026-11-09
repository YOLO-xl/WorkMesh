"""故障场景模型测试：配置校验、匹配、执行与诊断接入。"""

import json

import pytest

from services.diagnosis_service import DiagnosisService
from services.evidence_service import EvidenceService
from services.fault_scenario_service import (
    FaultScenarioRegistry,
    ScenarioRunner,
    evaluate_condition,
    match_scenario,
    validate_scenario,
)


class FakeFeedbackRepository:
    def __init__(self, records=None):
        self.records = records or []
        self.calls = []

    def query_feedback(self, workspace_id, **kwargs):
        self.calls.append(kwargs)
        return list(self.records)


class FakeEvidenceRepository:
    def __init__(self, records=None):
        self.records = records or []

    def query_evidence(self, workspace_id, **kwargs):
        return list(self.records)


def _login_feedback(**overrides):
    record = {
        "id": "fb-login-1",
        "uid": "10086231",
        "feedback_time": "2026-11-01T09:15:22Z",
        "product": "账号服务",
        "version": "1.0",
        "platform": "iOS",
        "error_code": "E5001",
        "description": "收不到验证码，登录不上",
    }
    record.update(overrides)
    return record


def _scenario(**overrides):
    data = {
        "schema_version": 1,
        "id": "test.scenario",
        "name": "测试场景",
        "version": "1",
        "owner": "test",
        "priority": 10,
        "enabled": True,
        "effective_from": "2026-11-01",
        "match": {"products": ["账号服务"]},
        "sources": [
            {
                "id": "feedback_recent",
                "kind": "feedback",
                "required": True,
                "window_minutes": 30,
                "query": {"products": ["账号服务"]},
            }
        ],
        "signals": [
            {"id": "recent_feedback_count", "source": "feedback_recent", "kind": "count"}
        ],
        "steps": [
            {
                "id": "confirm_scope",
                "title": "确认反馈面",
                "uses": ["feedback_recent"],
                "observe": "recent_feedback_count",
                "criteria": {
                    "pass": {"signal": "recent_feedback_count", "op": "gte", "value": 2},
                    "fail": {"signal": "recent_feedback_count", "op": "lt", "value": 2},
                },
                "on_missing": "mark_unverified",
            }
        ],
        "rules": [
            {
                "id": "action",
                "priority": 1,
                "when": {"signal": "recent_feedback_count", "op": "gte", "value": 2},
                "then": {
                    "type": "action",
                    "statement": "有多条反馈",
                    "confidence": "low",
                    "evidence_refs": ["S:feedback_recent.recent_feedback_count"],
                },
                "actions": ["继续排查"],
            }
        ],
        "output_policy": {
            "max_confidence_without_trace": "medium",
            "forbid_root_cause": True,
            "min_evidence_refs_for_hypothesis": 1,
            "require_any_of_sources": ["feedback_recent"],
            "disclaimer": "仅用于测试",
        },
    }
    data.update(overrides)
    return data


def test_registry_loads_all_three_scenarios_without_errors():
    registry = FaultScenarioRegistry()

    assert registry.errors == []
    assert {item["id"] for item in registry.enabled()} == {
        "login.code_delivery",
        "payment.result_mismatch",
        "generic.triage",
    }


def test_validate_scenario_rejects_unknown_source_and_placeholder():
    scenario = _scenario()
    scenario["signals"][0]["source"] = "not-exists"
    scenario["sources"][0]["query"] = {"uid": "{{anchor.not_exists}}"}

    errors = validate_scenario(scenario)

    assert any("不存在的 source" in error for error in errors)
    assert any("不支持的占位符" in error for error in errors)


def test_validate_scenario_rejects_root_cause_wording():
    scenario = _scenario()
    scenario["rules"][0]["then"]["type"] = "hypothesis"
    scenario["rules"][0]["then"]["statement"] = "根因就是客户端"

    errors = validate_scenario(scenario)

    assert any("禁用词" in error for error in errors)


def test_match_prefers_specific_scenario_over_generic():
    login = _login_feedback()
    scenarios = FaultScenarioRegistry().enabled()

    scenario, score, reasons = match_scenario(login, scenarios)

    assert scenario["id"] == "login.code_delivery"
    assert score >= 40
    assert any("产品命中" in reason for reason in reasons)


def test_match_payment_and_generic():
    payment = _login_feedback(
        product="支付中心",
        error_code="E6001",
        description="支付失败，扣款后订单状态没更新",
    )
    scenarios = FaultScenarioRegistry().enabled()

    scenario, _, _ = match_scenario(payment, scenarios)
    assert scenario["id"] == "payment.result_mismatch"

    unknown = _login_feedback(
        product="未知产品",
        error_code="E9999",
        description="页面打不开",
    )
    scenario, score, reasons = match_scenario(unknown, scenarios)
    assert scenario["id"] == "generic.triage"
    assert reasons[0] == "通用兜底场景"


def test_runner_uses_external_connectors_when_provided():
    feedback = _login_feedback()
    scenario = FaultScenarioRegistry().get("login.code_delivery")

    def provider(source, record):
        if source["id"] == "es_sms":
            return []
        if source["id"] == "prom_sms":
            return [{"value": 0.08}]
        return []

    run = ScenarioRunner(
        feedback_repository=FakeFeedbackRepository([feedback, feedback, feedback]),
        evidence_repository=FakeEvidenceRepository(),
        connector_provider=provider,
    ).run("workspace", feedback, scenario)

    assert run["status"] == "partial"
    assert run["confidence"] == "medium"
    assert any(item["rule_id"] == "provider_side" for item in run["hypotheses"])
    assert run["signals"]["provider_error_rate"] == 0.08
    assert "trace_lookup" in [item["id"] for item in run["missing_sources"]]


def test_runner_marks_verified_when_all_sources_and_steps_are_available():
    feedback = _login_feedback()
    scenario = FaultScenarioRegistry().get("login.code_delivery")

    def provider(source, record):
        if source["id"] == "es_sms":
            return [{"uid": "10086231", "error_code": "E5001"}]
        if source["id"] == "prom_sms":
            return [{"value": 0.02}]
        return []

    run = ScenarioRunner(
        feedback_repository=FakeFeedbackRepository([feedback, feedback, feedback]),
        evidence_repository=FakeEvidenceRepository(),
        connector_provider=provider,
        manual_answers={"trace_lookup": True},
    ).run("workspace", feedback, scenario)

    assert run["status"] == "verified"
    assert any(
        item["rule_id"] == "account_or_device_side"
        for item in run["hypotheses"]
    )
    assert all(step["status"] != "unknown" for step in run["steps"])


def test_runner_blocks_when_required_source_is_unavailable():
    scenario = _scenario(
        sources=[
            {
                "id": "es_required",
                "kind": "connector",
                "connector_type": "elasticsearch",
                "required": True,
                "window_minutes": 30,
                "query": {"index": "logs-*"},
            }
        ],
        signals=[
            {"id": "log_count", "source": "es_required", "kind": "count"}
        ],
        steps=[
            {
                "id": "check_log",
                "title": "查日志",
                "uses": ["es_required"],
                "observe": "log_count",
                "criteria": {
                    "pass": {"signal": "log_count", "op": "gte", "value": 1},
                    "fail": {"signal": "log_count", "op": "eq", "value": 0},
                },
                "on_missing": "block",
            }
        ],
        rules=[
            {
                "id": "action",
                "priority": 1,
                "when": {"signal": "log_count", "op": "gte", "value": 1},
                "then": {
                    "type": "action",
                    "statement": "有日志",
                    "confidence": "low",
                    "evidence_refs": ["S:es_required.log_count"],
                },
            }
        ],
    )

    run = ScenarioRunner(
        feedback_repository=FakeFeedbackRepository([_login_feedback()]),
        evidence_repository=FakeEvidenceRepository(),
    ).run("workspace", _login_feedback(), scenario)

    assert run["status"] == "blocked"
    assert run["confidence"] == "low"
    assert run["next_actions"]


def test_condition_tree_supports_null_and_boolean_logic():
    values = {"a": None, "b": 5, "c": True}

    assert evaluate_condition({"signal": "a", "op": "is_null"}, values)
    assert evaluate_condition({"signal": "b", "op": "gte", "value": 5}, values)
    assert evaluate_condition(
        {"all": [
            {"signal": "b", "op": "gte", "value": 5},
            {"any": [
                {"signal": "c", "op": "eq", "value": True},
                {"signal": "a", "op": "exists"},
            ]},
        ]},
        values,
    )
    assert not evaluate_condition({"signal": "a", "op": "gte", "value": 1}, values)


def test_query_feedback_filters_product_code_and_time(
    feedback_repository, workspace_id, make_feedback
):
    target = make_feedback(product="账号服务", error_code="E5001")
    make_feedback(product="支付中心", error_code="E6001")

    records = feedback_repository.query_feedback(
        workspace_id,
        products=["账号服务"],
        error_codes=["E5001"],
        start="2026-11-01T09:00:00Z",
        end="2026-11-01T10:00:00Z",
    )

    assert [record["id"] for record in records] == [target]


def test_diagnosis_build_attaches_scenario_run(
    db_path, workspace_id, make_feedback
):
    feedback_id = make_feedback(
        product="账号服务",
        error_code="E5001",
        uid="10086231",
        description="收不到验证码，登录不上",
    )
    service = DiagnosisService(
        evidence_service=EvidenceService(db_path=db_path),
    )

    result = service.build(workspace_id, feedback_id, use_llm=False)

    assert result["scenario"]["scenario_id"] == "login.code_delivery"
    assert result["scenario"]["status"] in ("partial", "blocked")
    assert "故障场景" in result["markdown"]
    assert "## 故障场景" not in result["markdown_body"]
    assert any("补充数据源" in item["action"] for item in result["next_steps"])
