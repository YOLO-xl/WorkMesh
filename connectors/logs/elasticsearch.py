from datetime import datetime, timezone
from typing import Any

from elasticsearch import Elasticsearch
from elasticsearch.exceptions import (
    AuthenticationException,
    ConnectionError,
    ApiError,
)

from connectors.base import BaseConnector


class ElasticsearchConnector(BaseConnector):
    """WorkMesh Elasticsearch 日志连接器。"""

    connector_type = "elasticsearch"
    display_name = "Elasticsearch"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)

        if not config.get("hosts"):
            raise ValueError("Elasticsearch 配置缺少 hosts")

        self.client = None

    def connect(self) -> None:
        options = {
            "hosts": self.config["hosts"],
            "request_timeout": self.config.get("request_timeout", 10),
        }

        if self.config.get("api_key"):
            options["api_key"] = self.config["api_key"]
        elif self.config.get("username") and self.config.get("password"):
            options["basic_auth"] = (
                self.config["username"],
                self.config["password"],
            )

        if "verify_certs" in self.config:
            options["verify_certs"] = self.config["verify_certs"]

        self.client = Elasticsearch(**options)

    def test_connection(self) -> dict[str, Any]:
        try:
            if self.client is None:
                self.connect()

            info = self.client.info()
            self._connected = True

            return {
                "success": True,
                "message": "Elasticsearch 连接成功",
                "cluster_name": info.get("cluster_name"),
                "version": info.get("version", {}).get("number"),
            }

        except AuthenticationException:
            self._connected = False
            return {
                "success": False,
                "message": "认证失败，请检查账号或 API Key",
            }

        except ConnectionError:
            self._connected = False
            return {
                "success": False,
                "message": "无法连接 Elasticsearch，请检查地址和网络",
            }

        except ApiError as exc:
            self._connected = False
            return {
                "success": False,
                "message": f"Elasticsearch 请求失败：{exc}",
            }
        except Exception as exc:
            self._connected = False
            return {
                "success": False,
                "message": f"连接测试发生意外错误：{exc}",
            }

    def query(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self.client is None:
            raise RuntimeError("请先建立 Elasticsearch 连接")

        index = kwargs.get("index") or self.config.get("index")
        if not index:
            raise ValueError("查询必须指定 index")

        filters = []

        uid = kwargs.get("uid")
        if uid:
            filters.append({"term": {"uid": uid}})

        error_code = kwargs.get("error_code")
        if error_code:
            filters.append({"term": {"error_code": error_code}})

        start_time = kwargs.get("start_time")
        end_time = kwargs.get("end_time")

        if start_time or end_time:
            time_range = {}
            if start_time:
                time_range["gte"] = start_time
            if end_time:
                time_range["lte"] = end_time

            filters.append({
                "range": {
                    kwargs.get("time_field", "@timestamp"): time_range
                }
            })

        keyword = kwargs.get("keyword")
        if keyword:
            filters.append({
                "query_string": {
                    "query": keyword
                }
            })

        body = {
            "query": {
                "bool": {
                    "filter": filters
                }
            },
            "sort": [
                {
                    kwargs.get("time_field", "@timestamp"): {
                        "order": "desc"
                    }
                }
            ],
        }

        response = self.client.search(
            index=index,
            query=body["query"],
            sort=body["sort"],
            size=min(max(int(kwargs.get("size", 100)), 1), 1000),
        )

        hits = response.get("hits", {}).get("hits", [])

        return [
            {
                "id": hit.get("_id"),
                "index": hit.get("_index"),
                "source": hit.get("_source", {}),
            }
            for hit in hits
        ]

    def normalize(
        self,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized = []

        for record in records:
            source = record.get("source", {})

            normalized.append({
                "evidence_type": "log",
                "connector_type": self.connector_type,
                "record_id": record.get("id"),
                "timestamp": source.get("@timestamp"),
                "uid": source.get("uid"),
                "request_id": source.get("request_id"),
                "trace_id": source.get("trace_id"),
                "service": source.get("service"),
                "level": source.get("level"),
                "error_code": source.get("error_code"),
                "message": source.get("message"),
                "raw_data": source,
            })

        return normalized

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

        self._connected = False
