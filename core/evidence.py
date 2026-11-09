"""WorkMesh 统一证据模型。

外部系统（反馈、日志、指标、链路追踪、变更记录）接入后统一转换为 Evidence，
使跨数据源关联和 AI 辅助诊断建立在同一套结构上。

设计约束（对应 docs/DATA_MODEL.md 与 docs/ROADMAP.md）：
1. 保留原始数据，标准化不覆盖 raw_data。
2. 只做结构归一，不做语义改写。
3. 任何分析结论都应能回溯到这里的记录。
"""

from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional


# 证据类型白名单。新增类型时同步修改 database/schema.sql 的 CHECK 约束。
EVIDENCE_TYPES = (
    "feedback",
    "log",
    "metric",
    "trace",
    "change",
    "note",
)

# 可用于跨源关联的标识字段。
IDENTITY_FIELDS = (
    "trace_id",
    "request_id",
    "uid",
    "error_code",
    "service",
)

_FALLBACK_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d",
)


def parse_timestamp(value: Any) -> Optional[datetime]:
    """把各种时间表示统一转换为 UTC 时间。

    支持 datetime、epoch 秒/毫秒、ISO 8601（含 Z）以及常见日期格式。
    无法解析时返回 None，由调用方决定如何处理，避免静默丢数据。
    """

    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return _to_utc(value)

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    if hasattr(value, "to_pydatetime"):
        return parse_timestamp(value.to_pydatetime())

    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")

    try:
        return _to_utc(datetime.fromisoformat(normalized))
    except ValueError:
        pass

    for fmt in _FALLBACK_TIME_FORMATS:
        try:
            return _to_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue

    return None


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_timestamp(value: Optional[datetime]) -> Optional[str]:
    """按数据库约定输出 UTC ISO 8601 字符串。"""

    if value is None:
        return None

    return (
        _to_utc(value)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _load_json(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _find_key(data: Mapping[str, Any], candidates: tuple[str, ...]) -> Any:
    """在同一字段的多种常见写法中取第一个非空值。"""

    for name in candidates:
        value = data.get(name)
        if value not in (None, ""):
            return value

    return None


def _clean(value: Any) -> Any:
    """把 Pandas/Numpy 标量转换为可存储的基础类型。"""

    if value is None:
        return None

    if hasattr(value, "item") and callable(value.item):
        try:
            value = value.item()
        except (ValueError, TypeError):
            pass

    if isinstance(value, float) and value != value:  # NaN
        return None

    return value


@dataclass
class Evidence:
    """跨数据源统一的证据记录。

    一条 Evidence 对应外部系统中的一条原始记录（一个反馈、一行日志、一次指标采样）。
    关联分析不修改它，只在 evidence_links 中记录它与其他证据的关系。
    """

    evidence_type: str
    record_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    uid: Optional[str] = None
    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    service: Optional[str] = None
    level: Optional[str] = None
    error_code: Optional[str] = None
    title: Optional[str] = None
    message: Optional[str] = None
    attributes: dict[str, Any] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict)
    connector_type: Optional[str] = None
    source_id: Optional[str] = None
    evidence_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.evidence_type not in EVIDENCE_TYPES:
            raise ValueError(
                f"不支持的证据类型：{self.evidence_type}，"
                f"可选值：{', '.join(EVIDENCE_TYPES)}"
            )

        if isinstance(self.timestamp, str):
            self.timestamp = parse_timestamp(self.timestamp)

        if self.timestamp is not None and not isinstance(self.timestamp, datetime):
            self.timestamp = parse_timestamp(self.timestamp)

        for name in (
            "record_id",
            "uid",
            "request_id",
            "trace_id",
            "service",
            "level",
            "error_code",
            "title",
            "message",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                setattr(self, name, str(value))

        self.attributes = _load_json(self.attributes)
        self.raw_data = _load_json(self.raw_data)

    # ------------------------------------------------------------------
    # 派生信息
    # ------------------------------------------------------------------
    def identity_keys(self) -> dict[str, str]:
        """返回可用于关联的非空标识字段。"""

        return {
            name: getattr(self, name)
            for name in IDENTITY_FIELDS
            if getattr(self, name)
        }

    def content_hash(self) -> str:
        """内容指纹，用于缺少外部 ID 时的去重。"""

        payload = "|".join(
            [
                self.evidence_type,
                self.record_id or "",
                self.timestamp.isoformat() if self.timestamp else "",
                self.service or "",
                self.error_code or "",
                (self.message or "")[:200],
            ]
        )

        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def display_label(self) -> str:
        """用于界面展示的一行摘要。"""

        parts = [
            self.service or "",
            self.level or "",
            self.error_code or "",
        ]
        head = " ".join(part for part in parts if part)

        if head and self.message:
            return f"{head} | {self.message}"
        return self.message or head or self.record_id or self.evidence_type

    # ------------------------------------------------------------------
    # 转换
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """转换为数据库/JSON 可存储的字典。"""

        return {
            "evidence_type": self.evidence_type,
            "record_id": self.record_id,
            "timestamp": format_timestamp(self.timestamp),
            "uid": self.uid,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "service": self.service,
            "level": self.level,
            "error_code": self.error_code,
            "title": self.title,
            "message": self.message,
            "attributes": self.attributes,
            "raw_data": self.raw_data,
            "connector_type": self.connector_type,
            "source_id": self.source_id,
            "evidence_id": self.evidence_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Evidence":
        return cls(
            evidence_type=data.get("evidence_type") or "note",
            record_id=data.get("record_id") or data.get("id"),
            timestamp=parse_timestamp(data.get("timestamp")),
            uid=_clean(data.get("uid")),
            request_id=_clean(data.get("request_id")),
            trace_id=_clean(data.get("trace_id")),
            service=_clean(data.get("service")),
            level=_clean(data.get("level")),
            error_code=_clean(data.get("error_code")),
            title=_clean(data.get("title")),
            message=_clean(data.get("message")),
            attributes=_load_json(data.get("attributes")),
            raw_data=_load_json(data.get("raw_data")),
            connector_type=data.get("connector_type"),
            source_id=data.get("source_id"),
            evidence_id=data.get("evidence_id"),
        )

    @classmethod
    def from_feedback_record(cls, record: Mapping[str, Any]) -> "Evidence":
        """把 feedback_records 的一行转换为反馈证据。

        反馈时间、UID、错误码是主要关联键；产品/版本/平台等放入 attributes，
        既保留检索价值，又不污染统一字段。
        """

        attributes = {
            "product": _clean(record.get("product")),
            "version": _clean(record.get("version")),
            "platform": _clean(record.get("platform")),
            "device": _clean(record.get("device")),
            "source_type": _clean(record.get("source_type")),
            "status": _clean(record.get("status")),
            "external_id": _clean(record.get("external_id")),
        }

        attributes = {
            key: value for key, value in attributes.items() if value
        }

        raw_payload = _load_json(record.get("raw_payload"))

        extra_data = _load_json(record.get("extra_data"))
        if extra_data:
            attributes["extra_data"] = extra_data

        # 部分反馈会携带 trace_id / request_id，可直接用于强关联。
        trace_id = _find_key(
            extra_data,
            ("trace_id", "traceId", "trace"),
        )
        request_id = _find_key(
            extra_data,
            ("request_id", "requestId", "request"),
        )

        product = record.get("product")

        return cls(
            evidence_type="feedback",
            record_id=str(record.get("id")) if record.get("id") else None,
            timestamp=parse_timestamp(record.get("feedback_time")),
            uid=_clean(record.get("uid")),
            request_id=_clean(request_id),
            trace_id=_clean(trace_id),
            error_code=_clean(record.get("error_code")),
            service=_clean(product),
            title=f"{product} 用户反馈" if product else "用户反馈",
            message=_clean(record.get("description")),
            attributes=attributes,
            raw_data=raw_payload,
            source_id=record.get("source_id"),
        )

    @classmethod
    def from_connector_record(
        cls,
        record: Mapping[str, Any],
        default_type: str = "log",
    ) -> "Evidence":
        """把 BaseConnector.normalize() 的输出转换为证据。

        已知字段进入统一字段，其余字段保留在 attributes 中，避免信息丢失。
        """

        attributes = {
            key: value
            for key, value in dict(record).items()
            if key
            not in {
                "evidence_type",
                "connector_type",
                "record_id",
                "id",
                "index",
                "timestamp",
                "uid",
                "request_id",
                "trace_id",
                "service",
                "level",
                "error_code",
                "message",
                "title",
                "attributes",
                "raw_data",
                "source_id",
            }
        }

        raw_data = _load_json(record.get("raw_data"))

        if not raw_data:
            # 连接器未提供 raw_data 时，用整条记录兜底，保证可追溯。
            raw_data = dict(record)

        return cls(
            evidence_type=record.get("evidence_type") or default_type,
            record_id=(
                record.get("record_id")
                or record.get("id")
                or record.get("external_id")
            ),
            timestamp=parse_timestamp(record.get("timestamp")),
            uid=_clean(record.get("uid")),
            request_id=_clean(record.get("request_id")),
            trace_id=_clean(record.get("trace_id")),
            service=_clean(record.get("service")),
            level=_clean(record.get("level")),
            error_code=_clean(record.get("error_code")),
            title=_clean(record.get("title")),
            message=_clean(record.get("message")),
            attributes=_load_json(record.get("attributes")) or attributes,
            raw_data=raw_data,
            connector_type=record.get("connector_type"),
            source_id=record.get("source_id"),
        )


def time_delta_seconds(
    left: Optional[datetime],
    right: Optional[datetime],
) -> Optional[float]:
    """返回两条证据的时间差（秒）。任一时间为空返回 None。"""

    if left is None or right is None:
        return None

    return abs((right - left).total_seconds())


def within_window(
    left: Optional[datetime],
    right: Optional[datetime],
    window: timedelta,
) -> bool:
    """判断两条证据是否落在同一时间窗口内。

    时间缺失时不阻断关联，由调用方在关联理由中标注「时间未知」。
    """

    delta = time_delta_seconds(left, right)

    if delta is None:
        return True

    return delta <= window.total_seconds()
