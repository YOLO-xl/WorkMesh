from connectors.registry import ConnectorRegistry, connector_registry
from connectors.logs import ElasticsearchConnector
from connectors.metrics import PrometheusConnector


# 注册内置连接器
connector_registry.register(ElasticsearchConnector)
connector_registry.register(PrometheusConnector)


__all__ = [
    "ConnectorRegistry",
    "connector_registry",
    "ElasticsearchConnector",
    "PrometheusConnector",
]
