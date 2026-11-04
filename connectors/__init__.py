from connectors.registry import ConnectorRegistry, connector_registry
from connectors.logs import ElasticsearchConnector


# 注册内置连接器
connector_registry.register(ElasticsearchConnector)


__all__ = [
    "ConnectorRegistry",
    "connector_registry",
    "ElasticsearchConnector",
]
