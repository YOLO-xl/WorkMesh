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
        self.last_total = 0
        self.last_aggregations: dict[str, Any] = {}

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
        """执行查询并返回记录列表，保留兼容原有调用方。"""

        return self.search(**kwargs)["records"]

    def search(self, **kwargs: Any) -> dict[str, Any]:
        """执行查询，返回记录、总数和聚合结果。

        支持 uid / error_code / trace_id / request_id / service / level
        的 term 过滤，支持 ``filters`` 额外条件、``search_after`` 分页、
        ``aggs`` 聚合和 ``highlight``，并保留 ES 命中元数据。
        """

        if self.client is None:
            raise RuntimeError("请先建立 Elasticsearch 连接")

        index = kwargs.get("index") or self.config.get("index")
        if not index:
            raise ValueError("查询必须指定 index")

        time_field = kwargs.get("time_field", "@timestamp")
        filters = self._build_filters(kwargs, time_field)
        keyword = kwargs.get("keyword")
        if keyword:
            filters.append({"query_string": {"query": keyword}})

        sort = kwargs.get("sort")
        if not sort:
            sort = [{time_field: {"order": "desc"}}]
            if kwargs.get("search_after"):
                # _id 默认不可排序，用 _doc 作为同时间戳下的翻页兜底。
                sort.append({"_doc": "asc"})
        size = min(max(int(kwargs.get("size", 100)), 0), 1000)

        search_params: dict[str, Any] = {
            "index": index,
            "query": {"bool": {"filter": filters}},
            "sort": sort,
            "size": size,
            "track_total_hits": True,
        }

        if kwargs.get("search_after"):
            search_params["search_after"] = kwargs["search_after"]
        if kwargs.get("aggs"):
            search_params["aggs"] = kwargs["aggs"]
        if kwargs.get("highlight"):
            search_params["highlight"] = kwargs["highlight"]

        response = self.client.search(**search_params)
        hits = response.get("hits", {}).get("hits", [])

        self.last_total = self._extract_total(response.get("hits", {}))
        self.last_aggregations = response.get("aggregations") or {}

        records = [
            {
                "id": hit.get("_id"),
                "index": hit.get("_index"),
                "score": hit.get("_score"),
                "sort": hit.get("sort"),
                "highlight": hit.get("highlight"),
                "source": hit.get("_source", {}),
            }
            for hit in hits
        ]

        return {
            "records": records,
            "total": self.last_total,
            "aggregations": self.last_aggregations,
        }

    def _build_filters(
        self,
        kwargs: dict[str, Any],
        time_field: str,
    ) -> list[dict[str, Any]]:
        """组装 ES filter 子句，避免把任意 DSL 直接透传给查询层。"""

        filters: list[dict[str, Any]] = []

        for field in (
            "uid",
            "error_code",
            "trace_id",
            "request_id",
            "service",
            "level",
        ):
            value = kwargs.get(field)
            if value:
                filters.append({"term": {field: value}})

        start_time = kwargs.get("start_time")
        end_time = kwargs.get("end_time")
        if start_time or end_time:
            time_range = {}
            if start_time:
                time_range["gte"] = start_time
            if end_time:
                time_range["lte"] = end_time
            filters.append({"range": {time_field: time_range}})

        extra = kwargs.get("filters")
        if isinstance(extra, list):
            filters.extend(extra)
        elif isinstance(extra, dict):
            for field, value in extra.items():
                if value in (None, ""):
                    continue
                if isinstance(value, (list, tuple, set)):
                    values = [item for item in value if item not in (None, "")]
                    if values:
                        filters.append({"terms": {field: values}})
                elif isinstance(value, dict):
                    if any(key in value for key in ("gte", "lte", "gt", "lt")):
                        filters.append({"range": {field: value}})
                    else:
                        filters.append({"term": {field: value}})
                else:
                    filters.append({"term": {field: value}})

        return filters

    @staticmethod
    def _extract_total(hits: dict[str, Any]) -> int:
        total = hits.get("total")
        if isinstance(total, dict):
            return int(total.get("value") or 0)
        try:
            return int(total or 0)
        except (TypeError, ValueError):
            return 0

    def normalize(
        self,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized = []

        for record in records:
            source = record.get("source", {})
            attributes = {}
            for key in ("index", "score", "sort", "highlight"):
                if record.get(key) not in (None, "", []):
                    attributes[key] = record.get(key)

            normalized.append({
                "evidence_type": "log",
                "connector_type": self.connector_type,
                "record_id": source.get("record_id") or record.get("id"),
                "timestamp": source.get("@timestamp") or source.get("timestamp"),
                "uid": source.get("uid"),
                "request_id": source.get("request_id"),
                "trace_id": source.get("trace_id"),
                "service": source.get("service"),
                "level": source.get("level"),
                "error_code": source.get("error_code"),
                "message": source.get("message"),
                "attributes": attributes,
                "raw_data": source,
            })

        return normalized

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

        self._connected = False
