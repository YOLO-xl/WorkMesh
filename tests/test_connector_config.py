"""连接器默认配置、认证透传和资源释放的回归测试。"""

import secrets
from pathlib import Path
from typing import Any

import pytest

from connectors.metrics.prometheus import PrometheusConnector, PrometheusError
from services.connector_config import (
    DEFAULT_CONNECTOR_TIMEOUT,
    DEFAULT_ELASTICSEARCH_URL,
    DEFAULT_PROMETHEUS_URL,
    build_connector_configs,
)
from services.connector_source_provider import ConnectorSourceProvider


ROOT = Path(__file__).resolve().parents[1]
UI_FILES = [
    ROOT / "ui" / "diagnosis_page.py",
    ROOT / "ui" / "evidence_page.py",
    ROOT / "ui" / "scenario_page.py",
    ROOT / "ui" / "troubleshooting_page.py",
]


def test_connector_defaults_are_unified():
    assert DEFAULT_ELASTICSEARCH_URL == "http://localhost:9200"
    assert DEFAULT_PROMETHEUS_URL == "http://localhost:9091"
    assert DEFAULT_CONNECTOR_TIMEOUT == 10.0

    configs = build_connector_configs()
    assert configs["elasticsearch"]["hosts"] == ["http://localhost:9200"]
    assert configs["prometheus"]["base_url"] == "http://localhost:9091"
    assert configs["elasticsearch"]["request_timeout"] == 10.0
    assert configs["prometheus"]["request_timeout"] == 10.0


def test_all_ui_pages_use_shared_defaults_and_not_9090():
    for path in UI_FILES:
        text = path.read_text(encoding="utf-8")
        assert "DEFAULT_ELASTICSEARCH_URL" in text, path
        assert "DEFAULT_PROMETHEUS_URL" in text, path
        assert "http://localhost:9090" not in text, path


def test_env_example_uses_host_port_9091():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "PROMETHEUS_BASE_URL=http://localhost:9091" in text
    assert "PROMETHEUS_BASE_URL=http://localhost:9090" not in text


def test_address_timeout_and_index_can_be_overridden():
    configs = build_connector_configs(
        elasticsearch_urls="http://es.example:9201, http://es2.example:9201",
        prometheus_url="http://prom.example:9999",
        request_timeout=3,
        elasticsearch_index="app-live-*",
    )
    es = configs["elasticsearch"]
    prom = configs["prometheus"]

    assert es["hosts"] == [
        "http://es.example:9201",
        "http://es2.example:9201",
    ]
    assert es["index"] == "app-live-*"
    assert es["request_timeout"] == 3.0
    assert prom["base_url"] == "http://prom.example:9999"
    assert prom["request_timeout"] == 3.0


def test_elasticsearch_basic_auth_is_passed_through():
    username = "user-" + secrets.token_hex(4)
    password = "pass-" + secrets.token_hex(4)
    es = build_connector_configs(
        elasticsearch_username=username,
        elasticsearch_password=password,
    )["elasticsearch"]

    assert es["username"] == username
    assert es["password"] == password
    assert "api_key" not in es


def test_elasticsearch_api_key_takes_precedence_over_basic_auth():
    username = "user-" + secrets.token_hex(4)
    password = "pass-" + secrets.token_hex(4)
    api_key = "key-" + secrets.token_hex(4)
    es = build_connector_configs(
        elasticsearch_username=username,
        elasticsearch_password=password,
        elasticsearch_api_key=api_key,
    )["elasticsearch"]

    assert es["api_key"] == api_key
    assert "username" not in es
    assert "password" not in es


def test_prometheus_token_and_basic_auth_are_passed_through():
    token = "token-" + secrets.token_hex(4)
    username = "user-" + secrets.token_hex(4)
    password = "pass-" + secrets.token_hex(4)
    token_config = build_connector_configs(
        prometheus_bearer_token=token,
    )["prometheus"]
    basic_config = build_connector_configs(
        prometheus_username=username,
        prometheus_password=password,
    )["prometheus"]

    assert token_config["bearer_token"] == token
    assert "username" not in token_config
    assert basic_config["username"] == username
    assert basic_config["password"] == password
    assert "bearer_token" not in basic_config


def test_source_id_config_overrides_type_config_and_closes_connector(monkeypatch):
    calls: list[dict[str, Any]] = []
    closed: list[bool] = []

    class FakePrometheusConnector:
        def __init__(self, config: dict[str, Any]):
            self.config = config
            calls.append(config)

        def query(self, **kwargs: Any) -> list[dict[str, Any]]:
            return [{"metric": {"__name__": "up"}, "value": [1, "1"]}]

        def normalize(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return records

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        "services.connector_source_provider.PrometheusConnector",
        FakePrometheusConnector,
    )

    provider = ConnectorSourceProvider(
        {
            "prom_live": {
                "base_url": "http://source-id.example:9091",
                "request_timeout": 3,
            },
            "prometheus": {
                "base_url": "http://type.example:9091",
            },
        }
    )
    source = {
        "id": "prom_live",
        "connector_type": "prometheus",
        "query": {"promql": "up"},
    }

    records = provider(source, {})

    assert records[0]["metric"]["__name__"] == "up"
    assert calls[0]["base_url"] == "http://source-id.example:9091"
    assert calls[0]["request_timeout"] == 3
    assert closed == [True]


def test_prometheus_empty_result_is_not_treated_as_error():
    def empty_http_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "success",
            "data": {"resultType": "vector", "result": []},
        }

    connector = PrometheusConnector(
        {"base_url": "http://prom.example:9091", "http_get": empty_http_get}
    )
    records = connector.query(query="missing_metric")

    assert records == []
    connector.close()
    assert connector._connected is False


def test_prometheus_connection_failure_is_reported():
    def failing_http_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"status": "error", "error": "boom"}

    connector = PrometheusConnector(
        {"base_url": "http://prom.example:9091", "http_get": failing_http_get}
    )

    status = connector.test_connection()
    assert status["success"] is False
    assert "boom" in status["message"]

    with pytest.raises(PrometheusError):
        connector.query(query="up")

    connector.close()
    assert connector._connected is False
