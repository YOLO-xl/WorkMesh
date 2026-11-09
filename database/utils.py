"""WorkMesh 数据层公共工具。

Repository 共用的主键生成、UTC 时间、Pandas 类型归一和 JSON 序列化。
"""

import json
import math
import uuid

from datetime import datetime, timezone
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


def normalize_value(value: Any) -> Any:
    """将 Pandas/Numpy 等类型转换为可存储的 Python 基础类型。"""

    if value is None:
        return None

    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()

    if hasattr(value, "item") and callable(value.item):
        try:
            value = value.item()
        except (ValueError, TypeError):
            pass

    if isinstance(value, float) and math.isnan(value):
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    if isinstance(value, Path):
        return str(value)

    return value


def json_dumps(data: Any) -> str:
    """生成兼容 SQLite TEXT 字段的 JSON 字符串。"""

    return json.dumps(
        data,
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
