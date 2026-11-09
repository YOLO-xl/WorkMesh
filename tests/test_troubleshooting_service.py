"""故障排查流程测试：直播进入房间样例的端到端链路。"""

from services.evidence_service import EvidenceService
from services.troubleshooting_service import TroubleshootingService


def _seed_live_evidence(db_path, workspace_id):
    service = EvidenceService(db_path=db_path)
    service.ingest_connector_records(
        workspace_id,
        [
            {
                "evidence_type": "log",
                "record_id": "log-0001",
                "timestamp": "2026-11-01T09:14:50Z",
                "uid": "10086231",
                "request_id": "req-0001",
                "trace_id": "trace-0001",
                "service": "直播SDK",
                "level": "ERROR",
                "error_code": "E1001",
                "message": "rtmp connect timeout: 推流地址连接超时 10s",
                "raw_data": {"record_id": "log-0001", "trace_id": "trace-0001"},
            },
            {
                "evidence_type": "log",
                "record_id": "log-0002",
                "timestamp": "2026-11-01T09:15:05Z",
                "uid": "10086231",
                "request_id": "req-0001",
                "trace_id": "trace-0001",
                "service": "直播SDK",
                "level": "ERROR",
                "error_code": "E1001",
                "message": "player init failed: 播放器初始化失败",
                "raw_data": {"record_id": "log-0002", "trace_id": "trace-0001"},
            },
        ],
        source_name="直播日志样例",
        source_type="log",
        connector_type="file",
    )
    service.ingest_connector_records(
        workspace_id,
        [
            {
                "evidence_type": "metric",
                "record_id": "metric-player",
                "timestamp": "2026-11-01T09:16:00Z",
                "service": "直播SDK",
                "title": "live_player_init_fail_rate",
                "attributes": {"metric_value": 0.182},
                "raw_data": {"metric": {"__name__": "live_player_init_fail_rate"}},
            },
            {
                "evidence_type": "metric",
                "record_id": "metric-latency",
                "timestamp": "2026-11-01T09:16:00Z",
                "service": "直播SDK",
                "title": "live_room_enter_p99_ms",
                "attributes": {"metric_value": 4280},
                "raw_data": {"metric": {"__name__": "live_room_enter_p99_ms"}},
            },
        ],
        source_name="直播指标样例",
        source_type="metric",
        connector_type="prometheus",
    )


def test_live_troubleshooting_flow_end_to_end(db_path, workspace_id, make_feedback):
    make_feedback(
        feedback_id="F001",
        uid="10086231",
        product="直播SDK",
        error_code="E1001",
        description="进入直播间后一直加载无法播放",
    )
    _seed_live_evidence(db_path, workspace_id)

    service = TroubleshootingService(db_path=db_path)
    sample = service.find_live_sample(workspace_id)
    assert sample["external_id"] == "F001"

    result = service.run(workspace_id, sample["id"])

    assert result["scenario"]["scenario_id"] == "live.enter_room_failure"
    assert result["scenario"]["status"] == "verified"
    assert result["scenario"]["signals"]["player_init_fail_rate"] == 0.182
    assert result["scenario"]["signals"]["enter_latency_p99"] == 4280

    assert [stage["key"] for stage in result["stages"]] == [
        "feedback",
        "query",
        "correlation",
        "scenario",
        "conclusion",
    ]

    chain = result["chain"]
    assert any(item["record_id"] == "log-0001" for item in chain)
    assert any(item["record_id"] == "log-0002" for item in chain)
    assert result["traceability"]["all_conclusions_traceable"] is True
    assert result["traceability"]["missing_evidence_ids"] == []

    query_ids = {hit["record_id"] for hit in result["query"]["hits"]}
    assert "log-0001" in query_ids
    assert "log-0002" in query_ids


def test_trace_returns_raw_record_and_source(db_path, workspace_id, make_feedback):
    make_feedback(feedback_id="F001")
    _seed_live_evidence(db_path, workspace_id)

    service = TroubleshootingService(db_path=db_path)
    # 直接用证据链里的日志记录做追溯。
    evidence = service.evidence_repository.query_evidence(
        workspace_id, evidence_types=["log"]
    )[0]
    traced = service.trace(workspace_id, evidence.evidence_id)

    assert traced["record_id"] in {"log-0001", "log-0002"}
    assert traced["source"]["name"] == "直播日志样例"
    assert traced["raw"]["trace_id"] == "trace-0001"
