"""跨系统查询服务测试：过滤、统一结构、关联与原始追溯。"""

from core.evidence import Evidence
from services.evidence_service import EvidenceService
from services.multi_source_query_service import MultiSourceQueryService


class FakeConnector:
    def __init__(self):
        self.closed = False

    def test_connection(self):
        return {"success": True, "message": "ok"}

    def query(self, **kwargs):
        return [{
            "id": "es-1",
            "source": {
                "@timestamp": "2026-11-01T09:14:50Z",
                "uid": "10086231",
                "trace_id": "trace-0001",
                "service": "直播SDK",
                "error_code": "E1001",
                "message": "外部日志：进入房间失败",
            },
        }]

    def normalize(self, records):
        return [{
            "evidence_type": "log",
            "record_id": "es-1",
            "timestamp": "2026-11-01T09:14:50Z",
            "uid": "10086231",
            "trace_id": "trace-0001",
            "service": "直播SDK",
            "error_code": "E1001",
            "message": "外部日志：进入房间失败",
            "raw_data": records[0]["source"],
        }]

    def close(self):
        self.closed = True


def _seed_local_evidence(db_path, workspace_id):
    service = EvidenceService(db_path=db_path)
    service.ingest_connector_records(
        workspace_id,
        [{
            "evidence_type": "log",
            "record_id": "log-0001",
            "timestamp": "2026-11-01T09:14:50Z",
            "uid": "10086231",
            "trace_id": "trace-0001",
            "service": "直播SDK",
            "error_code": "E1001",
            "message": "rtmp connect timeout",
            "raw_data": {"record_id": "log-0001"},
        }],
        source_name="测试日志",
        source_type="log",
        connector_type="file",
    )
    service.ingest_connector_records(
        workspace_id,
        [{
            "evidence_type": "metric",
            "record_id": "metric-1",
            "timestamp": "2026-11-01T09:16:00Z",
            "service": "直播SDK",
            "title": "live_player_init_fail_rate",
            "attributes": {"metric_value": 0.182},
            "raw_data": {"metric": {"__name__": "live_player_init_fail_rate"}},
        }],
        source_name="测试指标",
        source_type="metric",
        connector_type="prometheus",
    )


def test_query_filters_and_merges_local_sources(db_path, workspace_id, make_feedback):
    feedback_id = make_feedback(description="进入直播间后一直加载无法播放")
    _seed_local_evidence(db_path, workspace_id)

    service = MultiSourceQueryService(db_path=db_path)
    result = service.query(
        workspace_id,
        filters={
            "products": ["直播SDK"],
            "uid": "10086231",
            "keyword": "直播",
            "limit": 100,
        },
    )

    assert result["counts"]["total"] >= 2
    assert result["counts"]["by_platform"]["本地反馈"] == 1
    assert result["sources"][0]["available"] is True
    assert any(hit["record_id"] == "log-0001" for hit in result["hits"])

    # 指标服务相同但 UID 为空，不应该被 UID 过滤带进来。
    assert not any(hit["record_id"] == "metric-1" for hit in result["hits"])
    assert all(hit["traceable"] for hit in result["hits"])


def test_associate_and_trace_raw_record(db_path, workspace_id, make_feedback):
    feedback_id = make_feedback()
    _seed_local_evidence(db_path, workspace_id)

    service = MultiSourceQueryService(db_path=db_path)
    record = service.feedback_repository.get_feedback_record(
        workspace_id, feedback_id
    )
    anchor = Evidence.from_feedback_record(record)

    result = service.query(
        workspace_id,
        filters={"uid": "10086231", "evidence_types": ["log"], "limit": 50},
    )
    hits = service.associate(result["hits"], anchor, window_minutes=30)

    log_hit = next(hit for hit in hits if hit["record_id"] == "log-0001")
    assert log_hit["score"] > 0
    assert "uid" in log_hit["matched_keys"]
    assert "error_code" in log_hit["matched_keys"]

    traced = service.trace(workspace_id, log_hit)
    assert traced["raw"]["record_id"] == "log-0001"
    assert traced["source"]["name"] == "测试日志"
    assert traced["trace"]["trace_id"] == "trace-0001"


def test_external_connector_result_is_normalized_and_traceable(db_path, workspace_id):
    service = MultiSourceQueryService(
        db_path=db_path,
        connector_factory=lambda connector_type, config: FakeConnector(),
    )

    result = service.query(
        workspace_id,
        filters={"uid": "10086231", "limit": 20},
        external_sources=[{
            "id": "es_demo",
            "connector_type": "elasticsearch",
            "config": {"hosts": ["http://demo"]},
            "query": {"index": "app-logs"},
            "display_name": "演示 ES",
        }],
    )

    assert result["sources"][-1]["available"] is True
    assert result["sources"][-1]["count"] == 1
    hit = next(hit for hit in result["hits"] if hit["source_id"] == "es_demo")
    assert hit["platform"] == "演示 ES"
    assert hit["traceable"] is True
    assert hit["raw"]["uid"] == "10086231"


def test_unavailable_external_source_keeps_status_without_fake_data(db_path, workspace_id):
    class BrokenConnector(FakeConnector):
        def test_connection(self):
            return {"success": False, "message": "连接失败"}

    service = MultiSourceQueryService(
        db_path=db_path,
        connector_factory=lambda connector_type, config: BrokenConnector(),
    )

    result = service.query(
        workspace_id,
        external_sources=[{
            "id": "es_broken",
            "connector_type": "elasticsearch",
            "config": {"hosts": ["http://demo"]},
        }],
    )

    status = next(item for item in result["sources"] if item["id"] == "es_broken")
    assert status["available"] is False
    assert status["count"] == 0
    assert not any(hit["source_id"] == "es_broken" for hit in result["hits"])
