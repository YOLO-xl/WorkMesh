import pytest

from connectors import connector_registry
from connectors.logs import ElasticsearchConnector


class SuccessClient:
    def info(self):
        return {
            "cluster_name": "workmesh-test",
            "version": {"number": "9.5.1"},
        }

    def close(self):
        pass


class FailureClient:
    def info(self):
        raise RuntimeError("模拟连接失败")

    def close(self):
        pass


class MockSearchClient:
    def __init__(self):
        self.search_params = None

    def search(self, **kwargs):
        self.search_params = kwargs
        return {
            "hits": {
                "hits": [
                    {
                        "_id": "log-001",
                        "_index": "app-logs",
                        "_source": {
                            "@timestamp": "2026-11-03T14:32:00Z",
                            "uid": "user-001",
                            "request_id": "req-001",
                            "trace_id": "trace-001",
                            "service": "live-service",
                            "level": "ERROR",
                            "error_code": "E1001",
                            "message": "直播间初始化失败",
                        },
                    }
                ]
            }
        }


def test_connector_initialization():
    connector = ElasticsearchConnector({
        "hosts": ["http://localhost:9200"],
    })

    assert connector.connector_type == "elasticsearch"
    assert connector.display_name == "Elasticsearch"
    assert connector._connected is False


def test_missing_hosts_raises_error():
    with pytest.raises(ValueError, match="hosts"):
        ElasticsearchConnector({})


def test_connection_success():
    connector = ElasticsearchConnector({
        "hosts": ["http://localhost:9200"],
    })
    connector.client = SuccessClient()

    result = connector.test_connection()

    assert result["success"] is True
    assert result["cluster_name"] == "workmesh-test"
    assert result["version"] == "9.5.1"
    assert connector._connected is True


def test_connection_failure_resets_status():
    connector = ElasticsearchConnector({
        "hosts": ["http://localhost:9200"],
    })
    connector._connected = True
    connector.client = FailureClient()

    result = connector.test_connection()

    assert result["success"] is False
    assert "模拟连接失败" in result["message"]
    assert connector._connected is False


def test_query_and_normalize():
    connector = ElasticsearchConnector({
        "hosts": ["http://localhost:9200"],
        "index": "app-logs",
    })
    mock_client = MockSearchClient()
    connector.client = mock_client

    records = connector.query(
        uid="user-001",
        error_code="E1001",
        start_time="2026-11-03T14:00:00Z",
        end_time="2026-11-03T15:00:00Z",
    )

    assert len(records) == 1
    assert records[0]["id"] == "log-001"

    params = mock_client.search_params
    assert params["index"] == "app-logs"
    assert params["size"] == 100
    assert {"term": {"uid": "user-001"}} in params["query"]["bool"]["filter"]
    assert {"term": {"error_code": "E1001"}} in params["query"]["bool"]["filter"]

    normalized = connector.normalize(records)

    assert len(normalized) == 1
    assert normalized[0]["evidence_type"] == "log"
    assert normalized[0]["connector_type"] == "elasticsearch"
    assert normalized[0]["trace_id"] == "trace-001"
    assert normalized[0]["message"] == "直播间初始化失败"
    assert normalized[0]["raw_data"]["error_code"] == "E1001"


def test_connector_registered_and_creatable():
    registered = connector_registry.list_connectors()

    assert {
        "connector_type": "elasticsearch",
        "display_name": "Elasticsearch",
    } in registered

    connector = connector_registry.create(
        "elasticsearch",
        {"hosts": ["http://localhost:9200"]},
    )

    assert isinstance(connector, ElasticsearchConnector)
