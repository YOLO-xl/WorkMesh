"""连接器配置的统一默认值与构造入口。

这里只负责把页面输入整理成现有连接器可消费的配置字典：
- 默认地址与超时集中维护，避免各页面重复写死端口；
- 认证字段按连接器现有契约透传；
- 不改变 ConnectorSourceProvider 的调用方式和连接器实现。
"""

from __future__ import annotations

from typing import Any, Iterable

DEFAULT_ELASTICSEARCH_URL = "http://localhost:9200"
DEFAULT_PROMETHEUS_URL = "http://localhost:9091"
DEFAULT_CONNECTOR_TIMEOUT = 10.0


def _normalize_hosts(
    hosts: str | Iterable[str] | None,
) -> list[str]:
    if hosts is None:
        return [DEFAULT_ELASTICSEARCH_URL]

    if isinstance(hosts, str):
        items = hosts.split(",")
    else:
        items = list(hosts)

    normalized = [str(item).strip() for item in items if str(item).strip()]
    return normalized or [DEFAULT_ELASTICSEARCH_URL]


def build_connector_configs(
    *,
    elasticsearch_urls: str | Iterable[str] | None = None,
    prometheus_url: str | None = None,
    request_timeout: float = DEFAULT_CONNECTOR_TIMEOUT,
    elasticsearch_index: str | None = None,
    elasticsearch_username: str | None = None,
    elasticsearch_password: str | None = None,
    elasticsearch_api_key: str | None = None,
    prometheus_bearer_token: str | None = None,
    prometheus_username: str | None = None,
    prometheus_password: str | None = None,
) -> dict[str, dict[str, Any]]:
    """构造 ES/Prometheus 的现有连接器配置。

    认证值只在调用时存在，函数不把它们写入文件或日志。
    """

    timeout = float(request_timeout)

    elasticsearch: dict[str, Any] = {
        "hosts": _normalize_hosts(elasticsearch_urls),
        "request_timeout": timeout,
    }
    if elasticsearch_index and elasticsearch_index.strip():
        elasticsearch["index"] = elasticsearch_index.strip()

    if elasticsearch_api_key and elasticsearch_api_key.strip():
        elasticsearch["api_key"] = elasticsearch_api_key.strip()
    elif elasticsearch_username and elasticsearch_username.strip():
        elasticsearch["username"] = elasticsearch_username.strip()
        elasticsearch["password"] = elasticsearch_password or ""

    prometheus: dict[str, Any] = {
        "base_url": (prometheus_url or DEFAULT_PROMETHEUS_URL).strip(),
        "request_timeout": timeout,
    }

    if prometheus_bearer_token and prometheus_bearer_token.strip():
        prometheus["bearer_token"] = prometheus_bearer_token.strip()
    elif prometheus_username and prometheus_username.strip():
        prometheus["username"] = prometheus_username.strip()
        prometheus["password"] = prometheus_password or ""

    return {
        "elasticsearch": elasticsearch,
        "prometheus": prometheus,
    }
