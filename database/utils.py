"""WorkMesh 数据层公共工具。

Repository 共用的主键生成、UTC 时间、Pandas 类型归一和 JSON 序列化。
"""

import json
import math
import uuid

import pandas as pd

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


def generate_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _is_missing(value: Any) -> bool:
    """判断 pandas/numpy 的标量空值，避免把 NaT/NaN 留在 JSON 中。"""

    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False

    # pd.isna 对数组返回数组；本函数只用于标量。
    try:
        return bool(result)
    except (TypeError, ValueError):
        return False


def normalize_value(value: Any) -> Any:
    """递归转换 Pandas/Numpy 等类型为可 JSON 序列化的基础类型。

    规则：
    - NaN / NaT / pd.NA / 正负 Inf -> None
    - numpy 标量 -> Python 原生类型
    - pandas 时间类型 -> UTC ISO 字符串
    - dict / list / tuple 递归处理
    """

    if value is None:
        return None

    # 容器必须先递归，避免 pd.isna 对 list/dict 返回数组。
    if isinstance(value, dict):
        return {
            str(key): normalize_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [normalize_value(item) for item in value]

    if _is_missing(value):
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    if isinstance(value, date):
        return value.isoformat()

    if hasattr(value, "to_pydatetime"):
        try:
            value = value.to_pydatetime()
        except (TypeError, ValueError):
            pass

    if hasattr(value, "item") and callable(value.item):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass

    # numpy 标量转换后可能重新变成 NaN/Inf。
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    return value


def json_dumps(data: Any) -> str:
    """生成兼容 SQLite TEXT 字段的 JSON 字符串。"""

    return json.dumps(
        normalize_value(data),
        ensure_ascii=False,
        default=str,
        allow_nan=False,
    )


def json_loads(data: Any, default: Any = None) -> Any:
    """安全解析 JSON 字段，失败时返回默认值。"""

    if data is None or data == "":
        return default

    if not isinstance(data, str):
        return data

    try:
        return json.loads(data)
    except (ValueError, TypeError):
        return default
