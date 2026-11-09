"""诊断报告 Repository 与 LLM 配置的回归测试。"""

from types import SimpleNamespace

import pytest

from database.report_repository import ReportRepository
from services import llm_config
from services.llm_config import (
    CUSTOM_PROVIDER_ID,
    LLM_PROVIDERS,
    PROVIDER_BY_ID,
    detect_provider,
)


def _result(markdown: str = "# 报告\n\n内容", generated_by: str = "rule") -> dict:
    return {
        "title": "[E1001] 进入直播间后一直加载无法播放",
        "severity": "high",
        "confidence": "medium",
        "generated_by": generated_by,
        "markdown": markdown,
        "context_counts": {"matched": 5, "context": 4},
    }


def _feedback(feedback_id: str) -> dict:
    return {
        "id": feedback_id,
        "product": "直播SDK",
        "error_code": "E1001",
        "description": "进入直播间后一直加载无法播放",
    }


@pytest.fixture
def report_repository(db_path):
    return ReportRepository(db_path)


@pytest.fixture
def saved_report(report_repository, workspace_id, make_feedback):
    feedback_id = make_feedback()
    report_id, created = report_repository.save(
        workspace_id,
        _feedback(feedback_id),
        _result(),
        model=None,
        window_minutes=30,
        min_score=30,
    )
    assert created is True
    return report_id


def test_save_is_idempotent(report_repository, workspace_id, make_feedback):
    feedback_id = make_feedback()
    first, created_first = report_repository.save(
        workspace_id, _feedback(feedback_id), _result("同一份"), min_score=30
    )
    second, created_second = report_repository.save(
        workspace_id, _feedback(feedback_id), _result("同一份"), min_score=30
    )

    assert created_first is True
    assert created_second is False
    assert first == second


def test_list_filters_by_keyword_and_generated_by(
    report_repository, workspace_id, make_feedback
):
    feedback_id = make_feedback()
    report_repository.save(workspace_id, _feedback(feedback_id), _result("规则报告"))
    report_repository.save(
        workspace_id,
        _feedback(feedback_id),
        _result("LLM 报告", generated_by="llm"),
        model="gpt-4o-mini",
    )

    all_reports = report_repository.list_reports(workspace_id)
    llm_reports = report_repository.list_reports(workspace_id, generated_by="llm")
    keyword_reports = report_repository.list_reports(workspace_id, keyword="LLM 报告")

    assert len(all_reports) == 2
    assert len(llm_reports) == 1
    assert llm_reports[0]["model"] == "gpt-4o-mini"
    assert len(keyword_reports) == 1


def test_stats_cover_recent_and_llm(report_repository, workspace_id, saved_report, make_feedback):
    feedback_id = make_feedback()
    report_repository.save(
        workspace_id,
        _feedback(feedback_id),
        _result("第二份", generated_by="llm"),
        model="gpt-4o-mini",
    )

    stats = report_repository.stats(workspace_id)

    assert stats["total"] == 2
    assert stats["recent_total"] == 2
    assert stats["covered_feedback"] >= 1
    assert stats["llm_total"] == 1


def test_get_and_delete(report_repository, saved_report):
    report = report_repository.get(saved_report)

    assert report["markdown"].startswith("# 报告")
    assert report["payload"]["severity"] == "high"

    assert report_repository.delete(saved_report) is True
    assert report_repository.get(saved_report) is None


@pytest.fixture(autouse=True)
def isolate_llm_config(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_config, "CONFIG_DIR", tmp_path / ".workmesh")
    monkeypatch.setattr(
        llm_config, "CONFIG_PATH", tmp_path / ".workmesh" / "llm.local.json"
    )
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "OPENAI_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)


def _env(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_env_overrides_file(monkeypatch):
    llm_config.save_settings("file-key", "http://file.example/v1", "file-model")

    settings = llm_config.resolve_settings()
    assert settings.source == "file"
    assert settings.api_key == "file-key"

    _env(
        monkeypatch,
        OPENAI_API_KEY="env-key",
        OPENAI_BASE_URL="http://env.example/v1",
        OPENAI_MODEL="env-model",
    )

    settings = llm_config.resolve_settings()
    assert settings.source == "env"
    assert settings.api_key == "env-key"
    assert settings.base_url == "http://env.example/v1"
    assert settings.model == "env-model"


def test_save_keeps_key_and_masks():
    llm_config.save_settings("sk-1234567890", "http://api.example/v1", "m1")

    settings = llm_config.save_settings(None, "http://api.example/v2", "m2")

    assert settings.api_key == "sk-1234567890"
    assert settings.base_url == "http://api.example/v2"
    assert settings.masked_key() == "sk-1****7890"
    assert llm_config.CONFIG_PATH.stat().st_mode & 0o777 == 0o600


def test_clear_removes_file():
    llm_config.save_settings("sk-test", "", "m")
    assert llm_config.CONFIG_PATH.exists()

    llm_config.clear_settings()

    assert not llm_config.CONFIG_PATH.exists()
    assert llm_config.resolve_settings().source == "none"


def test_connection_without_key_reports_error():
    result = llm_config.test_connection(llm_config.LLMSettings())

    assert result["ok"] is False
    assert "API Key" in result["message"]


def test_provider_presets_cover_mainstream_services():
    provider_ids = [provider.id for provider in LLM_PROVIDERS]
    base_urls = [provider.base_url for provider in LLM_PROVIDERS]

    assert set(provider_ids) >= {
        "openai", "deepseek", "qwen", "kimi", "zhipu", "siliconflow", "ollama"
    }
    assert len(provider_ids) == len(set(provider_ids))
    assert len(base_urls) == len(set(base_urls))
    assert PROVIDER_BY_ID[CUSTOM_PROVIDER_ID].models == ()

    for provider in LLM_PROVIDERS:
        assert provider.label
        if provider.id != CUSTOM_PROVIDER_ID:
            assert provider.base_url.startswith("http")
            assert provider.models


def test_detect_provider_from_base_url():
    assert detect_provider("") == "openai"
    assert detect_provider("https://api.deepseek.com/v1/") == "deepseek"
    assert detect_provider("https://dashscope.aliyuncs.com/compatible-mode/v1") == "qwen"
    assert detect_provider("http://localhost:11434/v1") == "ollama"
    assert detect_provider("https://gateway.example.com/v1") == CUSTOM_PROVIDER_ID


def test_connection_with_fake_client():
    class FakeCompletions:
        def create(self, **kwargs):
            assert kwargs["model"] == "test-model"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="pong"))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    result = llm_config.test_connection(
        llm_config.LLMSettings(api_key="sk-test", model="test-model"),
        client_factory=lambda settings: fake_client,
    )

    assert result["ok"] is True
    assert "pong" in result["message"]
    assert isinstance(result["latency_ms"], int)
