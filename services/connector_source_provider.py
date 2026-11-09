"""把故障场景里的 connector source 接到真实连接器。

场景配置只描述“查什么”，连接地址/凭据由调用方通过 configs 注入，
避免把密码写进可提交的 JSON。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from connectors.metrics.prometheus import PrometheusConnector
from connectors.logs.elasticsearch import ElasticsearchConnector


class ConnectorSourceProvider:
    """ScenarioRunner 可注入的连接器查询函数。"""

    def __init__(
        self,
        configs: dict[str, dict[str, Any]],
        connector_factory: Optional[Callable[[str, dict], Any]] = None,
    ):
        self.configs = configs or {}
        self.connector_factory = connector_factory

    def __call__(self, source: dict, feedback: dict) -> list[dict]:
        connector_type = source.get("connector_type")
        config = self.configs.get(source.get("id")) or self.configs.get(connector_type)
        if not config:
            raise ValueError(f"未配置 {connector_type} 连接信息")

        query = dict(source.get("query") or {})
        if not query:
            raise ValueError(f"场景 {source.get('id')} 缺少查询条件")

        if connector_type == "elasticsearch":
            return self._query_elasticsearch(config, query)
        if connector_type == "prometheus":
            return self._query_prometheus(config, query)
        raise ValueError(f"暂不支持的连接器：{connector_type}")

    def _query_elasticsearch(self, config: dict, query: dict) -> list[dict]:
        connector = ElasticsearchConnector(config)
        connector.connect()
        status = connector.test_connection()
        if not status.get("success"):
            connector.close()
            raise ConnectionError(status.get("message") or "Elasticsearch 连接失败")

        try:
            extra_filters = query.pop("filters", None)
            if extra_filters:
                query["filters"] = extra_filters
            records = connector.query(**query)
            return connector.normalize(records)
        finally:
            connector.close()

    def _query_prometheus(self, config: dict, query: dict) -> list[dict]:
        connector = PrometheusConnector(config)
        try:
            promql = query.pop("promql", None) or query.pop("query", None)
            if not promql:
                raise ValueError("Prometheus 场景缺少 promql")
            use_window = bool(query.pop("use_window", False))
            if use_window:
                if query.get("start_time") and query.get("end_time"):
                    records = connector.query(
                        query=promql,
                        start=query["start_time"],
                        end=query["end_time"],
                    )
                else:
                    records = connector.query(query=promql)
            else:
                records = connector.query(
                    query=promql,
                    time=query.get("time"),
                )
            return connector.normalize(records)
        finally:
            connector.close()
