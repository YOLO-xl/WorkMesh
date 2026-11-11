"""Prometheus 指标连接器。

只依赖标准库（urllib）发起 HTTP 请求，避免为单一连接器引入额外 SDK；
测试通过注入 `http_get` 函数完全离线运行。
"""

import base64
import hashlib
import json
import math
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from connectors.base import BaseConnector


SERVICE_LABEL_KEYS = (
    "service",
    "exported_job",
    "job",
    "app",
    "component",
    "product",
)

EVIDENCE_LEVEL_BY_METRIC_KIND = {
    "counter": "info",
    "gauge": "info",
}


class PrometheusError(RuntimeError):
    """Prometheus 查询失败。"""


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _short_hash(text: str, length: int = 10) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


class PrometheusConnector(BaseConnector):
    """WorkMesh Prometheus 指标连接器。

    支持 instant query（`/api/v1/query`）与 range query
    （`/api/v1/query_range`），把返回的点位归一化为 metric 证据。
    """

    connector_type = "prometheus"
    display_name = "Prometheus"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)

        base_url = config.get("base_url") or config.get("url")
        if not base_url:
            raise ValueError("Prometheus 配置缺少 base_url")

        self.base_url = str(base_url).rstrip("/")
        self.timeout = float(config.get("request_timeout", 10))
        self.max_points = int(config.get("max_points", 500))
        self._version: str | None = None

        # 允许测试或上层注入传输函数，避免真实网络依赖。
        self._http_get = config.get("http_get") or self._default_http_get

    # ------------------------------------------------------------------
    # 传输层
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}

        token = self.config.get("bearer_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
            return headers

        username = self.config.get("username")
        password = self.config.get("password")

        if username:
            credentials = f"{username}:{password or ''}".encode("utf-8")
            encoded = base64.b64encode(credentials).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"

        return headers

    def _default_http_get(self, path: str, params: dict[str, Any]) -> dict:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"

        request = Request(url, headers=self._headers())

        with urlopen(request, timeout=self.timeout) as response:
            body = response.read().decode("utf-8")

        return json.loads(body)

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        try:
            payload = self._http_get(path, params)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise PrometheusError(
                    "认证失败，请检查 Prometheus 账号或令牌"
                ) from exc
            raise PrometheusError(
                f"Prometheus 返回 HTTP {exc.code}：{path}"
            ) from exc
        except URLError as exc:
            raise PrometheusError(
                f"无法连接 Prometheus：{exc.reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise PrometheusError("Prometheus 响应不是合法 JSON") from exc

        if not isinstance(payload, dict):
            raise PrometheusError("Prometheus 响应结构异常")

        if payload.get("status") != "success":
            error = payload.get("error") or "unknown error"
            raise PrometheusError(f"Prometheus 查询失败：{error}")

        return payload

    # ------------------------------------------------------------------
    # BaseConnector 接口
    # ------------------------------------------------------------------
    def connect(self) -> None:
        payload = self._get("/api/v1/status/buildinfo", {})
        self._version = (payload.get("data") or {}).get("version")
        self._connected = True

    def test_connection(self) -> dict[str, Any]:
        try:
            if not self._connected:
                self.connect()

            return {
                "success": True,
                "message": "Prometheus 连接成功",
                "version": self._version,
                "base_url": self.base_url,
            }

        except PrometheusError as exc:
            self._connected = False
            return {"success": False, "message": str(exc)}

        except Exception as exc:  # pragma: no cover - 兜底
            self._connected = False
            return {
                "success": False,
                "message": f"连接测试发生意外错误：{exc}",
            }

    def query(self, **kwargs: Any) -> list[dict[str, Any]]:
        promql = (kwargs.get("query") or kwargs.get("promql") or "").strip()
        if not promql:
            raise ValueError("查询必须提供 PromQL（query 参数）")

        start = kwargs.get("start")
        end = kwargs.get("end")

        if start and end:
            step = kwargs.get("step") or _default_step(start, end)
            payload = self._get(
                "/api/v1/query_range",
                {
                    "query": promql,
                    "start": start,
                    "end": end,
                    "step": step,
                },
            )
        else:
            params = {"query": promql}
            if kwargs.get("time"):
                params["time"] = kwargs["time"]
            payload = self._get("/api/v1/query", params)

        return self._parse_result(payload, promql)

    def parse_response(
        self,
        payload: dict,
        promql: str = "",
    ) -> list[dict[str, Any]]:
        """解析 Prometheus HTTP 响应体（支持离线导入或粘贴响应）。"""

        if not isinstance(payload, dict):
            raise PrometheusError("Prometheus 响应结构异常")

        if payload.get("status") != "success":
            error = payload.get("error") or "unknown error"
            raise PrometheusError(f"Prometheus 查询失败：{error}")

        return self._parse_result(payload, promql)

    def _parse_result(self, payload: dict, promql: str) -> list[dict[str, Any]]:
        data = payload.get("data") or {}
        result_type = data.get("resultType")
        result = data.get("result") or []

        records: list[dict[str, Any]] = []

        if result_type == "vector":
            for item in result:
                value = item.get("value") or [None, None]
                records.append({
                    "metric": item.get("metric") or {},
                    "timestamp": _to_float(value[0]),
                    "value": _to_float(value[1]),
                    "query": promql,
                })

        elif result_type == "matrix":
            for series in result:
                points = series.get("values") or []

                # 只保留最近 max_points 个点，避免一次导入把库撑大。
                for point in points[-self.max_points:]:
                    if len(point) < 2:
                        continue
                    records.append({
                        "metric": series.get("metric") or {},
                        "timestamp": _to_float(point[0]),
                        "value": _to_float(point[1]),
                        "query": promql,
                    })

        elif result_type in ("scalar", "string"):
            # scalar/string 同样返回 [timestamp, value]。
            if result and len(result) >= 2:
                records.append({
                    "metric": {},
                    "timestamp": _to_float(result[0]),
                    "value": _to_float(result[1]),
                    "query": promql,
                })

        else:
            raise PrometheusError(f"暂不支持的 Prometheus 结果类型：{result_type}")

        return records

    def normalize(
        self,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """兼容原有调用方，只返回正常归一化结果。"""

        normalized, _ = self.normalize_with_report(records)
        return normalized

    def normalize_with_report(
        self,
        records: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """逐个处理指标数据点，返回正常结果和被跳过的异常点及原因。

        单个异常点不会中断同一批次其他点的处理。
        """

        normalized: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for index, record in enumerate(records):
            try:
                if not isinstance(record, dict):
                    raise ValueError("指标记录必须是字典")

                metric = record.get("metric") or {}
                if not isinstance(metric, dict):
                    raise ValueError("metric 字段必须是字典")

                raw_value = record.get("value")
                value, value_reason = _coerce_metric_value(raw_value)
                if value_reason:
                    skipped.append({
                        "index": index,
                        "reason": value_reason,
                        "value": raw_value,
                        "record": record,
                    })
                    continue

                raw_timestamp = record.get("timestamp")
                timestamp, timestamp_reason = _coerce_timestamp(
                    raw_timestamp
                )
                if timestamp_reason:
                    skipped.append({
                        "index": index,
                        "reason": timestamp_reason,
                        "timestamp": raw_timestamp,
                        "record": record,
                    })
                    continue

                query = record.get("query") or ""
                labels = {
                    key: value
                    for key, value in metric.items()
                    if key != "__name__"
                }

                service = None
                for key in SERVICE_LABEL_KEYS:
                    if metric.get(key):
                        service = str(metric[key])
                        break

                name = metric.get("__name__") or "prometheus_metric"

                label_text = ",".join(
                    f"{key}={value}"
                    for key, value in sorted(labels.items())
                )
                message = f"{name} = {_format_value(value)}"
                if label_text:
                    message = f"{message}（{label_text}）"

                record_id = (
                    f"prom:{_short_hash(query)}:"
                    f"{_short_hash(json.dumps(labels, sort_keys=True))}"
                    f":{int(timestamp) if timestamp else 0}"
                )

                normalized.append({
                    "evidence_type": "metric",
                    "connector_type": self.connector_type,
                    "record_id": record_id,
                    "timestamp": timestamp,
                    "service": service,
                    "error_code": metric.get("error_code") or metric.get("code"),
                    "message": message,
                    "title": name,
                    "attributes": {
                        "metric_name": name,
                        "metric_value": value,
                        "labels": labels,
                        "promql": query,
                    },
                    "raw_data": record,
                })

            except Exception as exc:
                skipped.append({
                    "index": index,
                    "reason": f"记录处理失败：{exc}",
                    "record": record,
                })

        return normalized, skipped

    def close(self) -> None:
        self._connected = False


def _coerce_metric_value(value: Any) -> tuple[float | None, str | None]:
    """把指标值转换为有限浮点数；None 表示无值，异常值返回跳过原因。"""

    if value is None:
        return None, None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, "无法解析为数值"

    if math.isnan(number):
        return None, "NaN"
    if math.isinf(number):
        return None, "+Inf" if number > 0 else "-Inf"

    return number, None


def _coerce_timestamp(value: Any) -> tuple[float | None, str | None]:
    """校验时间戳；非有限时间戳不能用于生成稳定 record_id。"""

    if value is None:
        return None, None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, "无法解析时间戳"

    if not math.isfinite(number):
        return None, "时间戳非有限值"

    return number, None


def _format_value(value: float | None) -> str:
    if value is None:
        return "无值"

    try:
        number = float(value)
    except (TypeError, ValueError):
        return "无值"

    if not math.isfinite(number):
        return "无值"

    if abs(number) < 1e15 and number == int(number):
        return str(int(number))
    return f"{number:.4g}"


def _default_step(start: Any, end: Any) -> str:
    """按区间长度粗算采样步长，避免用户漏填 step。"""

    try:
        span = float(end) - float(start)
    except (TypeError, ValueError):
        return "60s"

    return f"{max(int(span / 60), 15)}s"
