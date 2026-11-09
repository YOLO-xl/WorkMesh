"""诊断服务测试：规则诊断与 LLM 引用校验。"""

import json

import pytest

from services import llm_config
from services.diagnosis_service import DiagnosisService
from services.evidence_service import EvidenceService


LOG_RECORDS = [
    {
        "evidence_type": "log",
        "record_id": "log-001",
        "timestamp": "2026-11-01T09:14:50Z",
        "uid": "10086231",
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
]

METRIC_RECORDS = [
    {
        "evidence_type": "metric",
        "record_id": "metric-001",
        "timestamp": "2026-11-01T09:16:00Z",
        "service": "直播SDK",
        "title": "live_player_init_fail_rate",
        "message": "live_player_init_fail_rate = 0.182",
        "attributes": {"metric_value": 0.182},
    }
]


@pytest.fixture
def prepared(db_path, workspace_id, make_feedback):
    feedback_id = make_feedback()
    evidence_service = EvidenceService(db_path=db_path)

    evidence_service.ingest_connector_records(
        workspace_id, LOG_RECORDS, source_name="日志", source_type="log"
    )
    evidence_service.ingest_connector_records(
        workspace_id,
        METRIC_RECORDS,
        source_name="指标",
        source_type="metric",
        connector_type="prometheus",
    )

    return workspace_id, feedback_id, evidence_service


def test_rule_diagnosis_separates_facts_from_inference(
    prepared, db_path, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    workspace_id, feedback_id, evidence_service = prepared
    service = DiagnosisService(evidence_service=evidence_service)

    result = service.build(workspace_id, feedback_id, use_llm=False)

    assert result["generated_by"] == "rule"
    kinds = {item["kind"] for item in result["conclusions"]}
    assert kinds == {"fact", "inference"}

    fact = next(item for item in result["conclusions"] if item["kind"] == "fact")
    assert fact["confidence"] == "high"

    assert result["severity"] == "medium"
    assert result["confidence"] == "high"
    assert "## 结论" in result["markdown"]


def test_rule_diagnosis_cites_existing_evidence(prepared, db_path):
    workspace_id, feedback_id, evidence_service = prepared
    service = DiagnosisService(evidence_service=evidence_service)

    result = service.build(workspace_id, feedback_id, use_llm=False)

    ref_ids = {ref["id"] for ref in result["evidence_refs"]}
    assert ref_ids

    for item in result["conclusions"]:
        for evidence_id in item["evidence_ids"]:
            assert evidence_id in ref_ids

    for ref_id in ref_ids:
        assert ref_id in result["markdown"]


def test_llm_without_config_reports_error(
    prepared, db_path, monkeypatch, tmp_path
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(llm_config, "CONFIG_PATH", tmp_path / "llm.local.json")
    workspace_id, feedback_id, evidence_service = prepared
    service = DiagnosisService(evidence_service=evidence_service)

    result = service.build(workspace_id, feedback_id, use_llm=True)

    assert result["generated_by"] == "rule"
    assert "未配置 LLM" in result["llm_error"]


def test_llm_output_is_merged_when_citations_valid(prepared, db_path):
    workspace_id, feedback_id, evidence_service = prepared
    rule_service = DiagnosisService(evidence_service=evidence_service)
    ref_id = rule_service.build(workspace_id, feedback_id)["evidence_refs"][0]["id"]

    def fake_llm(prompt: str) -> str:
        return json.dumps(
            {
                "severity": "high",
                "conclusions": [
                    {
                        "statement": "播放链路在窗口内出现连续失败",
                        "evidence_ids": [ref_id],
                        "confidence": "high",
                    }
                ],
                "next_steps": [
                    {
                        "action": "检查推流地址可用性",
                        "rationale": "日志显示 rtmp 连接超时",
                        "evidence_ids": [ref_id],
                    }
                ],
            },
            ensure_ascii=False,
        )

    service = DiagnosisService(
        evidence_service=evidence_service,
        llm_client=fake_llm,
    )

    result = service.build(workspace_id, feedback_id, use_llm=True)

    assert result["generated_by"] == "llm"
    assert result["severity"] == "high"
    assert any(
        item["statement"] == "播放链路在窗口内出现连续失败"
        for item in result["conclusions"]
    )
    # 事实仍然来自规则层
    assert any(item["kind"] == "fact" for item in result["conclusions"])
    assert result["next_steps"][0]["action"] == "检查推流地址可用性"


def test_llm_citing_unknown_evidence_falls_back(prepared, db_path):
    workspace_id, feedback_id, evidence_service = prepared

    service = DiagnosisService(
        evidence_service=evidence_service,
        llm_client=lambda prompt: json.dumps({
            "conclusions": [
                {
                    "statement": "编造的结论",
                    "evidence_ids": ["not-exist-id"],
                    "confidence": "high",
                }
            ]
        }),
    )

    result = service.build(workspace_id, feedback_id, use_llm=True)

    assert result["generated_by"] == "rule"
    assert "不存在的证据" in result["llm_error"]


def test_llm_without_citations_falls_back(prepared, db_path):
    workspace_id, feedback_id, evidence_service = prepared

    service = DiagnosisService(
        evidence_service=evidence_service,
        llm_client=lambda prompt: json.dumps({
            "conclusions": [
                {"statement": "没有引用证据的结论", "evidence_ids": []}
            ]
        }),
    )

    result = service.build(workspace_id, feedback_id, use_llm=True)

    assert result["generated_by"] == "rule"
    assert "没有任何证据引用" in result["llm_error"]
