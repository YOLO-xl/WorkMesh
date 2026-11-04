from abc import ABC, abstractmethod
from typing import Any


class BaseConnector(ABC):
    """
    WorkMesh 统一数据连接器接口。

    所有预置连接器和自定义连接器都应遵循此接口。
    """

    connector_type: str = "base"
    display_name: str = "Base Connector"

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._connected = False

    @abstractmethod
    def connect(self) -> None:
        """建立与外部系统的连接。"""
        raise NotImplementedError

    @abstractmethod
    def test_connection(self) -> dict[str, Any]:
        """测试连接，返回测试状态和说明。"""
        raise NotImplementedError

    @abstractmethod
    def query(self, **kwargs: Any) -> list[dict[str, Any]]:
        """查询外部系统数据。"""
        raise NotImplementedError

    @abstractmethod
    def normalize(
        self, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """将外部数据转换为 WorkMesh 统一格式。"""
        raise NotImplementedError

    def close(self) -> None:
        """释放连接资源。"""
        self._connected = False
