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


class RichSearchClient:
    def __init__(self):
        self.search_params = None

    def search(self, **kwargs):
        self.search_params = kwargs
        return {
            "hits": {
                "total": {"value": 12, "relation": "eq"},
                "hits": [
                    {
                        "_id": "log-001",
                        "_index": "app-logs",
                        "_score": 1.2,
                        "sort": [1790000000000, 3],
                        "_source": {
                            "@timestamp": "2026-11-03T14:32:00Z",
                            "record_id": "log-001",
                            "uid": "user-001",
                            "service": "live-service",
                            "message": "直播间初始化失败",
                        },
                        "highlight": {"message": ["<em>直播间</em>初始化失败"]},
                    }
                ],
            },
            "aggregations": {
                "by_service": {
                    "buckets": [{"key": "live-service", "doc_count": 12}]
                }
            },
        }


def test_search_supports_filters_pagination_aggs_and_metadata():
    connector = ElasticsearchConnector({
        "hosts": ["http://localhost:9200"],
        "index": "app-logs",
    })
    mock_client = RichSearchClient()
    connector.client = mock_client

    result = connector.search(
        filters={
            "service": "live-service",
            "uid": ["user-001", "user-002"],
            "@timestamp": {"gte": "2026-11-03T14:00:00Z"},
        },
        search_after=[1790000000000, 3],
        aggs={"by_service": {"terms": {"field": "service"}}},
        highlight={"fields": {"message": {}}},
        size=2,
    )

    params = mock_client.search_params
    assert params["index"] == "app-logs"
    assert params["size"] == 2
    assert params["track_total_hits"] is True
    assert params["search_after"] == [1790000000000, 3]
    assert params["sort"] == [
        {"@timestamp": {"order": "desc"}},
        {"_doc": "asc"},
    ]
    assert {"terms": {"uid": ["user-001", "user-002"]}} in params["query"]["bool"]["filter"]
    assert {"range": {"@timestamp": {"gte": "2026-11-03T14:00:00Z"}}} in params["query"]["bool"]["filter"]
    assert params["aggs"] == {"by_service": {"terms": {"field": "service"}}}
    assert params["highlight"] == {"fields": {"message": {}}}

    assert result["total"] == 12
    assert result["aggregations"]["by_service"]["buckets"][0]["key"] == "live-service"
    assert result["records"][0]["score"] == 1.2
    assert result["records"][0]["sort"] == [1790000000000, 3]
    assert result["records"][0]["highlight"]["message"][0].startswith("<em>")

    normalized = connector.normalize(result["records"])
    assert normalized[0]["record_id"] == "log-001"
    assert normalized[0]["attributes"]["index"] == "app-logs"
    assert normalized[0]["attributes"]["sort"] == [1790000000000, 3]
    assert normalized[0]["attributes"]["highlight"]["message"][0].startswith("<em>")


def test_search_allows_zero_size_for_aggregation_only_query():
    connector = ElasticsearchConnector({
        "hosts": ["http://localhost:9200"],
        "index": "app-logs",
    })
    mock_client = RichSearchClient()
    connector.client = mock_client

    result = connector.search(size=0, aggs={"by_service": {"terms": {"field": "service"}}})

    assert mock_client.search_params["size"] == 0
    assert result["total"] == 12
    assert result["aggregations"]["by_service"]["buckets"][0]["key"] == "live-service"
