from typing import Type

from connectors.base import BaseConnector


class ConnectorRegistry:
    """WorkMesh 连接器注册中心。"""

    def __init__(self):
        self._connectors: dict[str, Type[BaseConnector]] = {}

    def register(
        self,
        connector_class: Type[BaseConnector],
    ) -> None:
        """注册一个连接器实现类。"""
        connector_type = connector_class.connector_type

        if not connector_type or connector_type == "base":
            raise ValueError("连接器必须定义有效的 connector_type")

        if connector_type in self._connectors:
            raise ValueError(f"连接器类型已注册：{connector_type}")

        self._connectors[connector_type] = connector_class

    def get(self, connector_type: str) -> Type[BaseConnector]:
        """根据类型获取连接器实现类。"""
        if connector_type not in self._connectors:
            raise KeyError(f"未注册的连接器类型：{connector_type}")

        return self._connectors[connector_type]

    def create(
        self,
        connector_type: str,
        config: dict,
    ) -> BaseConnector:
        """创建指定类型的连接器实例。"""
        connector_class = self.get(connector_type)
        return connector_class(config)

    def list_connectors(self) -> list[dict[str, str]]:
        """列出已注册的连接器类型。"""
        return [
            {
                "connector_type": connector_class.connector_type,
                "display_name": connector_class.display_name,
            }
            for connector_class in self._connectors.values()
        ]


# 全局注册中心实例
connector_registry = ConnectorRegistry()
