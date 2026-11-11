"""T05：外部连接器结果进入五段诊断链路的回归测试。"""

import json
from pathlib import Path

import pytest

from core.evidence import Evidence
from database.evidence_repository import EvidenceRepository
from services.diagnosis_service import DiagnosisService
from services.evidence_service import EvidenceService
from services.fault_scenario_service import FaultScenarioRegistry
from services.multi_source_query_service import MultiSourceQueryService
from services.troubleshooting_service import TroubleshootingService


def _scenario_registry(tmp_path, overrides=None):
    source_dir = Path(__file__).resolve().parents[1] / "configs" / "fault_scenarios"
    scenario = json.loads(
        (source_dir / "login.code_delivery.json").read_text(encoding="utf-8")
    )

    for source in scenario["sources"]:
        values = (overrides or {}).get(source["id"])
        if values:
            source.update(values)

    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    (scenario_dir / "login.code_delivery.json").write_text(
        json.dumps(scenario, ensure_ascii=False),
        encoding="utf-8",
    )
    return FaultScenarioRegistry(str(scenario_dir))


def _service_with_registry(db_path, registry):
    query_service = MultiSourceQueryService(
        db_path=db_path,
        connector_factory=lambda connector_type, config: FakeExternalConnector(
            config
        ),
    )
    base_service = TroubleshootingService(
        db_path=db_path,
        query_service=query_service,
    )
    diagnosis = DiagnosisService(
        evidence_service=base_service.evidence_service,
        scenario_registry=registry,
    )
    return TroubleshootingService(
        diagnosis_service=diagnosis,
        query_service=query_service,
        db_path=db_path,
    )


class FakeExternalConnector:
    """按 config 返回固定标准化记录，不连接真实 ES / Prometheus。"""

    def __init__(self, config):
        self.config = config or {}

    def test_connection(self):
        if self.config.get("connection_error"):
            return {
                "success": False,
                "message": self.config["connection_error"],
            }
        return {"success": True, "message": "ok"}

    def query(self, **kwargs):
        if self.config.get("query_error"):
            raise RuntimeError(self.config["query_error"])
        return list(self.config.get("records") or [])

    def normalize(self, records):
        return list(records)

    def close(self):
        pass


def _service(db_path, connector_factory=None):
    query_service = MultiSourceQueryService(
        db_path=db_path,
        connector_factory=connector_factory or (
            lambda connector_type, config: FakeExternalConnector(config)
        ),
    )
    return TroubleshootingService(
        db_path=db_path,
        query_service=query_service,
    )


def _external_source(
    source_id="es_live",
    records=None,
    connector_type="elasticsearch",
    **config,
):
    return {
        "id": source_id,
        "connector_type": connector_type,
        "config": {
            "records": records or [],
            **config,
        },
        "query": {"index": "app-live-*", "size": 100},
        "display_name": f"外部 {source_id}",
    }


def _es_log(record_id="es-1", message="外部日志：进入房间失败"):
    return {
        "evidence_type": "log",
        "record_id": record_id,
        "timestamp": "2026-11-01T09:14:50Z",
        "uid": "10086231",
        "request_id": "req-0001",
        "trace_id": "trace-0001",
        "service": "直播SDK",
        "level": "ERROR",
        "error_code": "E1001",
        "message": message,
        "raw_data": {
            "id": record_id,
            "trace_id": "trace-0001",
            "message": message,
        },
    }


def _prom_metric(record_id, title, value):
    return {
        "evidence_type": "metric",
        "record_id": record_id,
        "timestamp": "2026-11-01T09:16:00Z",
        "service": "直播SDK",
        "title": title,
        "attributes": {"metric_value": value},
        "raw_data": {
            "metric": {"__name__": title},
            "value": [1793524560.0, str(value)],
        },
    }


def _prepare_feedback(make_feedback):
    return make_feedback(
        product="直播SDK",
        error_code="E1001",
        description="进入直播间后一直加载无法播放",
    )


def _prepare_sms_feedback(make_feedback):
    return make_feedback(
        product="账号服务",
        error_code="E5001",
        description="登录验证码一直收不到",
    )


def _sms_log(record_id, message):
    record = _es_log(record_id, message)
    record.update({
        "service": "sms-gateway",
        "error_code": "E5001",
        "message": message,
    })
    return record


def _single_external_hit_query(
    source_id="es_orphan",
    status=None,
    include_hit=True,
    record_id="orphan-1",
):
    evidence = Evidence(
        evidence_type="log",
        record_id=record_id,
        source_id=source_id,
        evidence_id=f"ev-{record_id}",
        service="sms-gateway",
        error_code="E5001",
        message="外部命中记录",
    )
    hits = []
    if include_hit:
        hits.append({
            "source_id": source_id,
            "evidence_id": f"ev-{record_id}",
            "kind": "log",
            "evidence": evidence,
        })

    sources = []
    if status is not None:
        sources.append({"id": source_id, **status})

    return {"hits": hits, "sources": sources}


def test_es_result_enters_unified_evidence_and_diagnosis(
    db_path, workspace_id, make_feedback
):
    feedback_id = _prepare_feedback(make_feedback)
    service = _service(db_path)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[_external_source(records=[_es_log()])],
    )

    ingest = result["query"]["external_ingest"][0]
    assert ingest["inserted"] == 1
    assert ingest["failed"] == 0

    assert service.evidence_repository.count_evidence(workspace_id, "log") == 1
    assert any(
        item["record_id"] == "es-1"
        for item in result["chain"]
    )
    assert any("E1001" in fact["statement"] for fact in result["context"]["facts"])

    links = service.evidence_repository.list_links(
        workspace_id, "feedback", feedback_id
    )
    assert any(
        link["evidence_id"] == result["chain"][1]["evidence_id"]
        for link in links
    )


def test_external_records_feed_connector_scenario_sources(
    db_path, workspace_id, make_feedback
):
    feedback_id = make_feedback(
        product="账号服务",
        error_code="E5001",
        description="登录验证码一直收不到",
    )
    sms_log = _es_log("sms-1", "短信网关发送失败")
    sms_log.update({
        "service": "sms-gateway",
        "error_code": "E5001",
        "message": "短信网关发送失败",
    })

    result = _service(db_path).run(
        workspace_id,
        feedback_id,
        external_sources=[_external_source(
            source_id="es_sms",
            records=[sms_log],
            query={"index": "app-sms-*", "size": 100},
        )],
    )

    assert result["scenario"]["scenario_id"] == "login.code_delivery"
    es_source = result["scenario"]["sources"]["es_sms"]
    assert es_source["available"] is True
    assert es_source["count"] >= 1
    assert result["scenario"]["signals"]["same_uid_log_count"] == 1


def test_prometheus_result_enters_scenario_and_conclusions(
    db_path, workspace_id, make_feedback
):
    feedback_id = _prepare_feedback(make_feedback)
    service = _service(db_path)

    records = [
        _prom_metric("prom-1", "live_player_init_fail_rate", 0.182),
        _prom_metric("prom-2", "live_room_enter_p99_ms", 4280),
    ]

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[_external_source(
            source_id="prom_live",
            records=records,
            connector_type="prometheus",
        )],
    )

    assert result["query"]["external_ingest"][0]["inserted"] == 2
    assert result["scenario"]["signals"]["player_init_fail_rate"] == 0.182
    assert result["scenario"]["signals"]["enter_latency_p99"] == 4280.0
    assert any(
        item["type"] == "metric"
        and item["title"] == "live_player_init_fail_rate"
        for item in result["chain"]
    )
    assert any(
        item["kind"] == "inference"
        and "指标侧" in item["statement"]
        for item in result["diagnosis"]["conclusions"]
    )


def test_local_feedback_and_evidence_flow_remains_compatible(
    db_path, workspace_id, make_feedback
):
    feedback_id = _prepare_feedback(make_feedback)
    EvidenceService(db_path=db_path).ingest_connector_records(
        workspace_id,
        [{
            "evidence_type": "log",
            "record_id": "local-1",
            "timestamp": "2026-11-01T09:14:50Z",
            "uid": "10086231",
            "trace_id": "trace-local",
            "service": "直播SDK",
            "level": "ERROR",
            "error_code": "E1001",
            "message": "本地日志",
            "raw_data": {"record_id": "local-1"},
        }],
        source_name="本地日志",
        source_type="log",
        connector_type="file",
    )

    result = _service(db_path).run(workspace_id, feedback_id)

    assert any(item["record_id"] == "local-1" for item in result["chain"])
    assert any(
        hit["record_id"] == "local-1"
        for hit in result["query"]["hits"]
    )
    assert result["stages"][0]["key"] == "feedback"
    assert result["stages"][-1]["key"] == "conclusion"


def test_repeated_external_query_reuses_evidence(
    db_path, workspace_id, make_feedback
):
    feedback_id = _prepare_feedback(make_feedback)
    service = _service(db_path)
    sources = [_external_source(records=[_es_log()])]

    first = service.run(workspace_id, feedback_id, external_sources=sources)
    second = service.run(workspace_id, feedback_id, external_sources=sources)

    assert first["query"]["external_ingest"][0]["inserted"] == 1
    assert second["query"]["external_ingest"][0]["inserted"] == 0
    assert second["query"]["external_ingest"][0]["skipped"] == 1
    assert service.evidence_repository.count_evidence(workspace_id, "log") == 1

    links = service.evidence_repository.list_links(
        workspace_id, "feedback", feedback_id
    )
    assert len(links) == 1


def test_one_external_source_failure_does_not_block_another(
    db_path, workspace_id, make_feedback
):
    feedback_id = _prepare_feedback(make_feedback)
    service = _service(db_path)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source(
                source_id="es_broken",
                connection_error="连接失败",
            ),
            _external_source(
                source_id="es_ok",
                records=[_es_log("es-ok-1")],
            ),
        ],
    )

    broken = next(
        item for item in result["query"]["external_ingest"]
        if item["source_id"] == "es_broken"
    )
    ok = next(
        item for item in result["query"]["external_ingest"]
        if item["source_id"] == "es_ok"
    )

    assert broken["available"] is False
    assert broken["inserted"] == 0
    assert ok["inserted"] == 1
    assert any(item["record_id"] == "es-ok-1" for item in result["chain"])


def test_empty_external_result_keeps_uncertainty(
    db_path, workspace_id, make_feedback
):
    feedback_id = _prepare_feedback(make_feedback)
    service = _service(db_path)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[_external_source(records=[])],
    )

    assert result["query"]["external_ingest"][0]["total"] == 0
    assert result["context"]["facts"] == []
    assert any(
        "没有找到" in item["statement"] or "接入" in item["statement"]
        for item in result["context"]["hypotheses"]
    )
    assert result["diagnosis"]["conclusions"][0]["confidence"] == "low"
    assert result["traceability"]["missing_sources"]


def test_external_evidence_source_and_query_are_traceable(
    db_path, workspace_id, make_feedback
):
    feedback_id = _prepare_feedback(make_feedback)
    service = _service(db_path)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[_external_source(records=[_es_log()])],
    )

    evidence = next(
        item for item in result["chain"]
        if item["record_id"] == "es-1"
    )
    traced = service.trace(workspace_id, evidence["evidence_id"])

    assert traced["source"]["name"] == "外部 es_live"
    assert traced["record_id"] == "es-1"
    assert traced["raw"]["trace_id"] == "trace-0001"
    assert traced["attributes"]["connector_query"] == {
        "index": "app-live-*",
        "size": 100,
    }

    by_source = {
        item["source_name"]: item
        for item in result["traceability"]["evidence_by_source"]
    }
    assert by_source["外部 es_live"]["connector_type"] == "elasticsearch"
    assert by_source["外部 es_live"]["record_ids"] == ["es-1"]


def test_merge_links_preserves_previous_history(
    db_path, workspace_id, make_feedback
):
    feedback_id = _prepare_feedback(make_feedback)
    service = EvidenceService(db_path=db_path)
    repository = EvidenceRepository(db_path)

    first = service.ingest_connector_records(
        workspace_id,
        [_es_log("es-old", "旧外部日志")],
        source_name="旧外部源",
        source_type="log",
        connector_type="elasticsearch",
    )
    second = service.ingest_connector_records(
        workspace_id,
        [_es_log("es-new", "新外部日志")],
        source_name="新外部源",
        source_type="log",
        connector_type="elasticsearch",
    )

    repository.replace_links(
        workspace_id,
        "feedback",
        feedback_id,
        [{
            "evidence_id": first["records"][0]["evidence_id"],
            "score": 55,
            "strength": "strong",
        }],
    )
    repository.merge_links(
        workspace_id,
        "feedback",
        feedback_id,
        [{
            "evidence_id": second["records"][0]["evidence_id"],
            "score": 40,
            "strength": "medium",
        }],
    )

    links = repository.list_links(workspace_id, "feedback", feedback_id)
    assert {
        link["evidence_id"] for link in links
    } == {
        first["records"][0]["evidence_id"],
        second["records"][0]["evidence_id"],
    }


def test_scenario_explicit_external_source_isolates_records(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(
        tmp_path,
        overrides={
            "es_sms": {"external_source_id": "es_sms"},
        },
    )
    service = _service_with_registry(db_path, registry)

    sources = [
        _external_source(
            "es_live",
            records=[_sms_log("live-1", "来自 es_live 的日志")],
        ),
        _external_source(
            "es_sms",
            records=[_sms_log("sms-1", "来自 es_sms 的日志")],
        ),
    ]
    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=sources,
    )

    assert result["scenario"]["scenario_id"] == "login.code_delivery"
    sms_source = result["scenario"]["sources"]["es_sms"]
    assert sms_source["available"] is True
    assert sms_source["count"] == 1
    assert result["scenario"]["signals"]["same_uid_log_count"] == 1

    provider = service._build_external_connector_provider(
        result["query"], sources
    )
    base_source = {
        "connector_type": "elasticsearch",
        "external_source_id": "es_sms",
        "query": {
            "filters": {
                "service": "sms-gateway",
                "uid": "10086231",
                "error_code": "E5001",
            }
        },
    }
    assert [
        item["record_id"] for item in provider(base_source, {})
    ] == ["sms-1"]
    assert provider(base_source, {})[0]["attributes"]["external_source_id"] == (
        "es_sms"
    )

    live_source = {
        **base_source,
        "external_source_id": "es_live",
    }
    assert [
        item["record_id"] for item in provider(live_source, {})
    ] == ["live-1"]


def test_scenario_without_source_id_is_ambiguous_for_multiple_same_type_sources(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(tmp_path)
    service = _service_with_registry(db_path, registry)

    sources = [
        _external_source(
            "es_live",
            records=[_sms_log("live-1", "来自 es_live 的日志")],
        ),
        _external_source(
            "es_sms",
            records=[_sms_log("sms-1", "来自 es_sms 的日志")],
        ),
    ]
    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=sources,
    )

    sms_source = result["scenario"]["sources"]["es_sms"]
    assert sms_source["available"] is False
    assert sms_source["count"] == 0
    assert "歧义" in sms_source["reason"]
    assert "external_source_id" in sms_source["reason"]

    provider = service._build_external_connector_provider(
        result["query"], sources
    )
    with pytest.raises(ValueError, match="外部数据源歧义"):
        provider({
            "connector_type": "elasticsearch",
            "query": {},
        }, {})


def test_explicit_external_source_type_mismatch_is_rejected(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(
        tmp_path,
        overrides={
            "es_sms": {"external_source_id": "prom_live"},
        },
    )
    service = _service_with_registry(db_path, registry)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source(
                "prom_live",
                records=[_sms_log("prom-wrong-1", "这条记录类型不匹配")],
                connector_type="prometheus",
            )
        ],
    )

    sms_source = result["scenario"]["sources"]["es_sms"]
    assert sms_source["available"] is False
    assert sms_source["count"] == 0
    assert "类型不匹配" in sms_source["reason"]
    assert "prom_live" in sms_source["reason"]
    assert "实际连接器类型=prometheus" in sms_source["reason"]
    assert "要求连接器类型=elasticsearch" in sms_source["reason"]


def test_explicit_external_source_reverse_type_mismatch_is_rejected(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(
        tmp_path,
        overrides={
            "prom_sms": {"external_source_id": "es_live"},
        },
    )
    service = _service_with_registry(db_path, registry)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source(
                "es_live",
                records=[_sms_log("es-wrong-1", "这条 ES 记录不能进 Prom 源")],
            )
        ],
    )

    prom_source = result["scenario"]["sources"]["prom_sms"]
    assert prom_source["available"] is False
    assert prom_source["count"] == 0
    assert "类型不匹配" in prom_source["reason"]
    assert "es_live" in prom_source["reason"]
    assert "实际连接器类型=elasticsearch" in prom_source["reason"]
    assert "要求连接器类型=prometheus" in prom_source["reason"]


def test_auto_match_ignores_failed_es_source(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(tmp_path)
    service = _service_with_registry(db_path, registry)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source(
                "es_failed",
                query_error="ES 查询超时",
            ),
            _external_source(
                "es_good",
                records=[_sms_log("sms-good-1", "唯一成功的 ES 日志")],
            ),
        ],
    )

    sms_source = result["scenario"]["sources"]["es_sms"]
    assert sms_source["available"] is True
    assert sms_source["count"] == 1
    assert sms_source["reason"] is None
    assert result["scenario"]["signals"]["same_uid_log_count"] == 1

    failed_ingest = next(
        item for item in result["query"]["external_ingest"]
        if item["source_id"] == "es_failed"
    )
    assert failed_ingest["available"] is False
    assert failed_ingest["failed"] == 0


def test_auto_match_ignores_successful_es_source_without_records(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(tmp_path)
    service = _service_with_registry(db_path, registry)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source(
                "es_empty",
                records=[],
            ),
            _external_source(
                "es_good",
                records=[_sms_log("sms-good-2", "有记录的唯一 ES 日志")],
            ),
        ],
    )

    sms_source = result["scenario"]["sources"]["es_sms"]
    assert sms_source["available"] is True
    assert sms_source["count"] == 1
    assert sms_source["reason"] is None

    empty_ingest = next(
        item for item in result["query"]["external_ingest"]
        if item["source_id"] == "es_empty"
    )
    assert empty_ingest["available"] is True
    assert empty_ingest["total"] == 0


def test_two_successful_es_sources_with_records_are_still_ambiguous(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(tmp_path)
    service = _service_with_registry(db_path, registry)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source(
                "es_a",
                records=[_sms_log("a-1", "es_a 日志")],
            ),
            _external_source(
                "es_b",
                records=[_sms_log("b-1", "es_b 日志")],
            ),
        ],
    )

    sms_source = result["scenario"]["sources"]["es_sms"]
    assert sms_source["available"] is False
    assert sms_source["count"] == 0
    assert "歧义" in sms_source["reason"]
    assert "es_a" in sms_source["reason"]
    assert "es_b" in sms_source["reason"]


def test_explicit_failed_es_source_does_not_fallback(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(
        tmp_path,
        overrides={
            "es_sms": {"external_source_id": "es_failed"},
        },
    )
    service = _service_with_registry(db_path, registry)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source(
                "es_failed",
                query_error="指定源不可用",
            ),
            _external_source(
                "es_good",
                records=[_sms_log("sms-should-not-be-used", "不能自动切换")],
            ),
        ],
    )

    sms_source = result["scenario"]["sources"]["es_sms"]
    assert sms_source["available"] is False
    assert sms_source["count"] == 0
    assert "es_failed" in sms_source["reason"]
    assert "查询失败" in sms_source["reason"]
    assert "es_good" not in sms_source["reason"]

    good_ingest = next(
        item for item in result["query"]["external_ingest"]
        if item["source_id"] == "es_good"
    )
    assert good_ingest["inserted"] == 1


def test_all_es_sources_failed_reports_unavailable(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(tmp_path)
    service = _service_with_registry(db_path, registry)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source("es_a", query_error="A 连接失败"),
            _external_source("es_b", query_error="B 查询失败"),
        ],
    )

    sms_source = result["scenario"]["sources"]["es_sms"]
    assert sms_source["available"] is False
    assert sms_source["count"] == 0
    assert "均查询失败" in sms_source["reason"]
    assert "es_a" in sms_source["reason"]
    assert "es_b" in sms_source["reason"]


def test_all_es_sources_empty_report_no_evidence(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(tmp_path)
    service = _service_with_registry(db_path, registry)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source("es_empty_a", records=[]),
            _external_source("es_empty_b", records=[]),
        ],
    )

    sms_source = result["scenario"]["sources"]["es_sms"]
    assert sms_source["available"] is False
    assert sms_source["count"] == 0
    assert "均查询成功" in sms_source["reason"]
    assert "无有效入库证据" in sms_source["reason"]
    assert "es_empty_a" in sms_source["reason"]
    assert "es_empty_b" in sms_source["reason"]


def test_missing_source_status_does_not_auto_match(
    db_path, workspace_id, make_feedback
):
    source = _external_source(
        "es_orphan",
        records=[_sms_log("orphan-1", "有证据但没有查询状态")],
    )
    service = _service(db_path)
    evidence = Evidence(
        evidence_type="log",
        record_id="orphan-1",
        source_id="es_orphan",
        evidence_id="ev-orphan",
        service="sms-gateway",
        error_code="E5001",
        message="有证据但没有查询状态",
    )
    query_result = {
        "hits": [{
            "source_id": "es_orphan",
            "evidence_id": "ev-orphan",
            "kind": "log",
            "evidence": evidence,
        }],
        "sources": [],
    }

    provider = service._build_external_connector_provider(
        query_result,
        [source],
    )
    with pytest.raises(ValueError, match="无查询状态"):
        provider({"connector_type": "elasticsearch"}, {})

    compat_provider = service._build_external_connector_provider(
        query_result,
        [source],
        allow_missing_status=True,
    )
    assert len(compat_provider({
        "connector_type": "elasticsearch",
    }, {})) == 1


def test_explicit_missing_status_source_does_not_return_records(
    db_path, workspace_id, make_feedback
):
    source = _external_source(
        "es_orphan",
        records=[_sms_log("orphan-1", "显式指定但没有查询状态")],
    )
    service = _service(db_path)
    evidence = Evidence(
        evidence_type="log",
        record_id="orphan-1",
        source_id="es_orphan",
        evidence_id="ev-orphan",
        service="sms-gateway",
        error_code="E5001",
        message="显式指定但没有查询状态",
    )
    query_result = {
        "hits": [{
            "source_id": "es_orphan",
            "evidence_id": "ev-orphan",
            "kind": "log",
            "evidence": evidence,
        }],
        "sources": [],
    }

    for allow_missing in (False, True):
        provider = service._build_external_connector_provider(
            query_result,
            [source],
            allow_missing_status=allow_missing,
        )
        with pytest.raises(ValueError, match="无查询状态"):
            provider({
                "connector_type": "elasticsearch",
                "external_source_id": "es_orphan",
            }, {})


def test_explicit_source_without_connector_type_is_config_error(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(
        tmp_path,
        overrides={
            "es_sms": {
                "external_source_id": "es_sms",
                "connector_type": None,
            },
        },
    )
    service = _service_with_registry(db_path, registry)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source(
                "es_sms",
                records=[_sms_log("sms-1", "类型校验缺失时不能放行")],
            )
        ],
    )

    sms_source = result["scenario"]["sources"]["es_sms"]
    assert sms_source["available"] is False
    assert sms_source["count"] == 0
    assert "配置错误" in sms_source["reason"]
    assert "connector_type" in sms_source["reason"]
    assert "es_sms" in sms_source["reason"]


def test_valid_status_type_and_evidence_still_match(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(
        tmp_path,
        overrides={
            "es_sms": {"external_source_id": "es_sms"},
        },
    )
    service = _service_with_registry(db_path, registry)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source(
                "es_sms",
                records=[_sms_log("sms-valid", "状态类型证据都有效")],
            )
        ],
    )

    sms_source = result["scenario"]["sources"]["es_sms"]
    assert sms_source["available"] is True
    assert sms_source["count"] == 1
    assert result["scenario"]["signals"]["same_uid_log_count"] == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"available": None}, "查询状态无效"),
        ({}, "缺少 available 字段"),
        ({"available": "true"}, "查询状态无效"),
        ({"available": 1}, "查询状态无效"),
    ],
    ids=["none", "missing", "string-true", "number-one"],
)
def test_explicit_invalid_available_values_are_rejected(
    db_path, status, expected
):
    source = _external_source("es_orphan")
    service = _service(db_path)
    query_result = _single_external_hit_query(
        source_id="es_orphan",
        status=status,
    )

    provider = service._build_external_connector_provider(
        query_result,
        [source],
    )
    with pytest.raises(ValueError) as exc_info:
        provider({
            "connector_type": "elasticsearch",
            "external_source_id": "es_orphan",
        }, {})

    message = str(exc_info.value)
    assert "外部数据源不可用" in message
    assert "es_orphan" in message
    assert expected in message


def test_explicit_available_false_is_rejected(db_path):
    source = _external_source("es_orphan")
    service = _service(db_path)
    query_result = _single_external_hit_query(
        source_id="es_orphan",
        status={
            "available": False,
            "message": "连接失败",
        },
    )

    provider = service._build_external_connector_provider(
        query_result,
        [source],
    )
    with pytest.raises(ValueError) as exc_info:
        provider({
            "connector_type": "elasticsearch",
            "external_source_id": "es_orphan",
        }, {})

    message = str(exc_info.value)
    assert "查询失败" in message
    assert "连接失败" in message


def test_explicit_available_true_without_evidence_is_rejected(db_path):
    source = _external_source("es_orphan")
    service = _service(db_path)
    query_result = _single_external_hit_query(
        source_id="es_orphan",
        status={"available": True},
        include_hit=False,
    )

    provider = service._build_external_connector_provider(
        query_result,
        [source],
    )
    with pytest.raises(ValueError, match="无有效入库证据"):
        provider({
            "connector_type": "elasticsearch",
            "external_source_id": "es_orphan",
        }, {})


def test_explicit_available_true_with_evidence_returns_records(db_path):
    source = _external_source("es_orphan")
    service = _service(db_path)
    query_result = _single_external_hit_query(
        source_id="es_orphan",
        status={"available": True},
    )

    provider = service._build_external_connector_provider(
        query_result,
        [source],
    )
    records = provider({
        "connector_type": "elasticsearch",
        "external_source_id": "es_orphan",
    }, {})

    assert [item["record_id"] for item in records] == ["orphan-1"]


def test_single_external_source_still_auto_matches(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(tmp_path)
    service = _service_with_registry(db_path, registry)

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source(
                "es_sms",
                records=[_sms_log("sms-1", "只有短信网关一个 ES 源")],
            )
        ],
    )

    sms_source = result["scenario"]["sources"]["es_sms"]
    assert sms_source["available"] is True
    assert sms_source["count"] == 1
    assert result["scenario"]["signals"]["same_uid_log_count"] == 1


def test_no_external_run_does_not_reuse_previous_provider(
    db_path, workspace_id, make_feedback
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    service = _service(db_path)

    first = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source(
                "es_sms",
                records=[_sms_log("sms-1", "第一次外部日志")],
            )
        ],
    )
    assert first["scenario"]["sources"]["es_sms"]["available"] is True

    second = service.run(workspace_id, feedback_id)

    sms_source = second["scenario"]["sources"]["es_sms"]
    assert sms_source["available"] is False
    assert sms_source["count"] == 0
    assert sms_source["reason"] == "未配置连接器 Provider"
    assert service.diagnosis_service.connector_provider is None


def test_ingest_failure_in_one_source_does_not_block_another(
    db_path, workspace_id, make_feedback, tmp_path, monkeypatch
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(
        tmp_path,
        overrides={
            "es_sms": {"external_source_id": "es_sms"},
        },
    )
    service = _service_with_registry(db_path, registry)

    original_ingest = service.evidence_service.ingest_connector_records

    def flaky_ingest(workspace_id, records, source_name, **kwargs):
        if source_name == "外部 es_bad":
            raise RuntimeError("模拟入库失败")
        return original_ingest(workspace_id, records, source_name, **kwargs)

    monkeypatch.setattr(
        service.evidence_service,
        "ingest_connector_records",
        flaky_ingest,
    )

    result = service.run(
        workspace_id,
        feedback_id,
        external_sources=[
            _external_source(
                "es_bad",
                records=[_sms_log("bad-1", "这条证据会入库失败")],
            ),
            _external_source(
                "es_sms",
                records=[_sms_log("sms-1", "正常短信日志")],
            ),
        ],
    )

    bad = next(
        item for item in result["query"]["external_ingest"]
        if item["source_id"] == "es_bad"
    )
    good = next(
        item for item in result["query"]["external_ingest"]
        if item["source_id"] == "es_sms"
    )

    assert bad["failed"] == 1
    assert bad["inserted"] == 0
    assert good["failed"] == 0
    assert good["inserted"] == 1
    assert any(item["record_id"] == "sms-1" for item in result["chain"])
    assert result["scenario"]["sources"]["es_sms"]["count"] == 1


def test_custom_t04_connector_provider_still_works(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(tmp_path)

    def custom_provider(source, feedback):
        if source.get("id") == "es_sms":
            return [_sms_log("custom-sms", "T04 自定义 Provider 日志")]
        return []

    base_service = TroubleshootingService(db_path=db_path)
    diagnosis = DiagnosisService(
        evidence_service=base_service.evidence_service,
        scenario_registry=registry,
        connector_provider=custom_provider,
    )
    service = TroubleshootingService(
        diagnosis_service=diagnosis,
        db_path=db_path,
    )

    result = service.run(workspace_id, feedback_id)

    assert result["scenario"]["sources"]["es_sms"]["available"] is True
    assert result["scenario"]["sources"]["es_sms"]["count"] == 1
    assert result["scenario"]["signals"]["same_uid_log_count"] == 1
    assert service.diagnosis_service.connector_provider is custom_provider


def test_build_level_provider_overrides_instance_default(
    db_path, workspace_id, make_feedback, tmp_path
):
    feedback_id = _prepare_sms_feedback(make_feedback)
    registry = _scenario_registry(tmp_path)
    base_service = TroubleshootingService(db_path=db_path)

    empty_provider = lambda source, feedback: []
    override_provider = lambda source, feedback: (
        [_sms_log("build-override", "build 参数 Provider")]
        if source.get("id") == "es_sms"
        else []
    )

    diagnosis = DiagnosisService(
        evidence_service=base_service.evidence_service,
        scenario_registry=registry,
        connector_provider=empty_provider,
    )

    overridden = diagnosis.build(
        workspace_id,
        feedback_id,
        connector_provider=override_provider,
    )
    assert overridden["scenario"]["sources"]["es_sms"]["count"] == 1
    assert overridden["scenario"]["signals"]["same_uid_log_count"] == 1

    defaulted = diagnosis.build(workspace_id, feedback_id)
    assert defaulted["scenario"]["sources"]["es_sms"]["available"] is False
    assert defaulted["scenario"]["signals"]["same_uid_log_count"] == 0
