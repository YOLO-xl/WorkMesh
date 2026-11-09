"""Prometheus 连接器测试：注入 http_get，完全离线。"""

import json
import pytest

from connectors import connector_registry
from connectors.metrics.prometheus import PrometheusConnector, PrometheusError
from core.evidence import Evidence
from urllib.error import HTTPError


BUILDINFO = {"status": "success", "data": {"version": "2.53.0"}}

VECTOR = {
    "status": "success",
    "data": {
        "resultType": "vector",
        "result": [
            {
                "metric": {"__name__": "http_errors_total", "job": "live-service"},
                "value": [1762000000.0, "17"],
            }
        ],
    },
}

MATRIX = {
    "status": "success",
    "data": {
        "resultType": "matrix",
        "result": [
            {
                "metric": {"__name__": "http_request_duration_p99", "service": "live-service"},
                "values": [
                    [1762000000.0, "820"],
                    [1762000060.0, "910"],
                    [1762000120.0, "1300"],
                ],
            }
        ],
    },
}


def make_connector(payloads):
    calls = []

    def http_get(path, params):
        calls.append((path, params))
        value = payloads.get(path)
        if isinstance(value, Exception):
            raise value
        return value

    connector = PrometheusConnector(
        {"base_url": "http://prometheus:9090/", "http_get": http_get}
    )
    return connector, calls


def test_registry_exposes_prometheus():
    types = [item["connector_type"] for item in connector_registry.list_connectors()]
    assert "prometheus" in types


def test_requires_base_url():
    with pytest.raises(ValueError):
        PrometheusConnector({})


def test_base_url_trailing_slash_is_stripped():
    connector, _ = make_connector({})
    assert connector.base_url == "http://prometheus:9090"


def test_connection_success():
    connector, calls = make_connector({"/api/v1/status/buildinfo": BUILDINFO})

    result = connector.test_connection()

    assert result["success"] is True
    assert result["version"] == "2.53.0"
    assert calls[0][0] == "/api/v1/status/buildinfo"


def test_connection_auth_failure():
    error = HTTPError("http://prometheus", 401, "Unauthorized", {}, None)
    connector, _ = make_connector({"/api/v1/status/buildinfo": error})

    result = connector.test_connection()

    assert result["success"] is False
    assert "认证失败" in result["message"]


def test_query_instant_returns_points():
    connector, calls = make_connector({"/api/v1/query": VECTOR})

    records = connector.query(query="sum(http_errors_total)")

    assert len(records) == 1
    assert records[0]["value"] == 17.0
    assert records[0]["timestamp"] == 1762000000.0
    assert calls[0][1] == {"query": "sum(http_errors_total)"}


def test_query_range_returns_all_points():
    connector, calls = make_connector({"/api/v1/query_range": MATRIX})

    records = connector.query(
        query="http_request_duration_p99",
        start=1762000000,
        end=1762000120,
        step="60s",
    )

    assert len(records) == 3
    assert calls[0][1]["step"] == "60s"


def test_query_range_caps_points():
    payload = {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"__name__": "cpu"},
                    "values": [[1762000000 + i, "1"] for i in range(50)],
                }
            ],
        },
    }
    connector = PrometheusConnector({
        "base_url": "http://prometheus:9090",
        "max_points": 5,
        "http_get": lambda path, params: payload,
    })

    records = connector.query(query="cpu", start=1, end=2, step="15s")

    assert len(records) == 5
    assert records[-1]["timestamp"] == 1762000049


def test_query_without_promql_raises():
    connector, _ = make_connector({})

    with pytest.raises(ValueError):
        connector.query(query="  ")


def test_query_error_status_raises():
    connector = PrometheusConnector({
        "base_url": "http://prometheus:9090",
        "http_get": lambda path, params: {
            "status": "error",
            "error": "bad query",
        },
    })

    with pytest.raises(PrometheusError):
        connector.query(query="bad(")


def test_normalize_builds_metric_evidence():
    connector, _ = make_connector({})
    # 用固定 payload 解析，避免依赖网络。
    records = connector._parse_result(VECTOR, "sum(http_errors_total)")

    normalized = connector.normalize(records)
    evidence = Evidence.from_connector_record(normalized[0])

    assert evidence.evidence_type == "metric"
    assert evidence.service == "live-service"
    assert evidence.title == "http_errors_total"
    assert evidence.attributes["metric_value"] == 17.0
    assert evidence.attributes["labels"] == {"job": "live-service"}
    assert evidence.timestamp is not None
    assert "17" in evidence.message
    assert normalized[0]["record_id"].startswith("prom:")


def test_normalize_prefers_service_label_over_job():
    connector, _ = make_connector({})
    records = connector._parse_result(MATRIX, "p99")

    normalized = connector.normalize(records)

    assert all(item["service"] == "live-service" for item in normalized)
    assert len({item["record_id"] for item in normalized}) == len(normalized)
