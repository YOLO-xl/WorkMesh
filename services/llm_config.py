"""LLM 接入配置。

优先级：环境变量 > 本机配置文件。
- 环境变量适合 CI / 容器 / 临时验证，进程启动时读取。
- 本机配置文件由「报告中心 → LLM 配置」写入，改完立即生效，不需要重启。
- 文件只存本机开发凭据，放在 .workmesh/ 下并已加入 .gitignore。
"""

from __future__ import annotations

import json
import os
import time

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / ".workmesh"
CONFIG_PATH = CONFIG_DIR / "llm.local.json"

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class LLMSettings:
    api_key: str = ""
    base_url: str = ""
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    source: str = "none"

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def masked_key(self) -> str:
        return mask_secret(self.api_key)


@dataclass
class LLMConfigDraft:
    api_key: str = ""
    base_url: str = ""
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT
    extra: dict = field(default_factory=dict)


def mask_secret(value: Optional[str]) -> str:
    """只展示头尾各 4 位，避免把完整密钥带到界面或日志里。"""

    value = (value or "").strip()

    if not value:
        return ""

    if len(value) <= 8:
        return "****"

    return f"{value[:4]}****{value[-4:]}"


def _read_file() -> dict:
    if not CONFIG_PATH.exists():
        return {}

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    return data if isinstance(data, dict) else {}


def resolve_settings() -> LLMSettings:
    """环境变量优先，其次本机配置文件。"""

    file_config = _read_file()

    file_key = str(file_config.get("api_key") or "").strip()
    file_base = str(file_config.get("base_url") or "").strip()
    file_model = str(file_config.get("model") or "").strip()
    file_timeout = file_config.get("timeout")

    env_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    env_base = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    env_model = (os.environ.get("OPENAI_MODEL") or "").strip()
    env_timeout = os.environ.get("OPENAI_TIMEOUT")

    api_key = env_key or file_key
    base_url = env_base or file_base
    model = env_model or file_model or DEFAULT_MODEL

    raw_timeout = env_timeout or file_timeout or DEFAULT_TIMEOUT

    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    if env_key:
        source = "env"
    elif file_key:
        source = "file"
    else:
        source = "none"

    return LLMSettings(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=timeout,
        source=source,
    )


def save_settings(
    api_key: Optional[str],
    base_url: Optional[str],
    model: Optional[str],
    timeout: float = DEFAULT_TIMEOUT,
) -> LLMSettings:
    """写入本机配置；api_key 为 None 时保留原值，传空字符串则清除。"""

    current = _read_file()

    if api_key is None:
        next_key = str(current.get("api_key") or "")
    else:
        next_key = api_key.strip()

    payload = {
        "api_key": next_key,
        "base_url": (base_url or "").strip(),
        "model": (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "timeout": float(timeout),
    }

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass

    temp_path = CONFIG_PATH.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(temp_path, 0o600)
    temp_path.replace(CONFIG_PATH)

    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass

    return resolve_settings()


def clear_settings() -> None:
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


def test_connection(
    settings: Optional[LLMSettings] = None,
    client_factory: Optional[Callable[[LLMSettings], object]] = None,
) -> dict:
    """发一次最小请求验证地址、密钥和模型是否可用。"""

    settings = settings or resolve_settings()

    if not settings.available:
        return {"ok": False, "message": "还没有配置 API Key。", "latency_ms": None}

    if client_factory is None:
        from openai import OpenAI

        def client_factory(current: LLMSettings) -> object:
            return OpenAI(
                api_key=current.api_key,
                base_url=current.base_url or None,
                timeout=current.timeout,
            )

    started = time.perf_counter()

    try:
        client = client_factory(settings)
        response = client.chat.completions.create(
            model=settings.model,
            messages=[{"role": "user", "content": "只回复 pong"}],
            max_tokens=8,
            temperature=0,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as exc:  # 网络、鉴权、模型名错误都统一回报
        return {
            "ok": False,
            "message": f"连接失败：{exc}",
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    return {
        "ok": True,
        "message": f"连接成功，模型返回：{content[:40] or '（空响应）'}",
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def config_source_label(source: str) -> str:
    return {
        "env": "环境变量",
        "file": "本机配置文件",
        "none": "未配置",
    }.get(source, source)
