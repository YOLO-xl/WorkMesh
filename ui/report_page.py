"""报告中心：诊断报告沉淀与 LLM 接入配置。"""

import re

from datetime import datetime

import streamlit as st

from html import escape

from database.feedback_repository import FeedbackRepository
from database.migration import initialize_database
from database.report_repository import ReportRepository
from services.diagnosis_service import CONFIDENCE_LABELS
from services.llm_config import (
    CONFIG_PATH,
    CUSTOM_MODEL_OPTION,
    LLM_PROVIDERS,
    PROVIDER_BY_ID,
    LLMSettings,
    clear_settings,
    config_source_label,
    detect_provider,
    resolve_settings,
    save_settings,
    test_connection,
)
from ui.theme import empty_state, hero, section, tag


SEVERITY_LABELS = {"high": "高", "medium": "中", "low": "低"}


def _on_llm_provider_change() -> None:
    """切换服务商时同步地址与模型，避免把上一家的地址带过去。"""

    provider = PROVIDER_BY_ID.get(st.session_state.get("llm_provider"))

    if not provider:
        return

    st.session_state["llm_base_url"] = provider.base_url
    st.session_state["llm_model_choice"] = (
        provider.default_model if provider.models else CUSTOM_MODEL_OPTION
    )
    st.session_state["llm_custom_model"] = ""
    st.session_state["llm_api_key"] = ""


def _reset_llm_form_state() -> None:
    for key in (
        "llm_provider",
        "llm_base_url",
        "llm_model_choice",
        "llm_custom_model",
        "llm_api_key",
        "llm_timeout",
    ):
        st.session_state.pop(key, None)


def _fmt_time(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M"
        )
    except ValueError:
        return value[:16]


def _safe_file_name(title: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "_", title or "diagnosis")
    return f"{cleaned[:40] or 'diagnosis'}.md"


def render(db_path=None) -> None:
    hero(
        "报告中心",
        "诊断结论沉淀成可检索、可导出的报告；LLM 接入在同一个页面配置，改完即生效",
        ["Archive", "Traceable", "Configurable"],
    )

    initialize_database(db_path)
    feedback_repository = FeedbackRepository(db_path)
    report_repository = ReportRepository(db_path)
    workspace_id = feedback_repository.get_default_workspace()

    stats = report_repository.stats(workspace_id)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("报告总数", stats["total"])
    c2.metric("近 7 天新增", stats["recent_total"])
    c3.metric("覆盖反馈", stats["covered_feedback"])
    c4.metric("LLM 生成", stats["llm_total"])

    tab_reports, tab_llm = st.tabs(["诊断报告", "LLM 配置"])

    with tab_reports:
        _render_reports(report_repository, workspace_id)

    with tab_llm:
        _render_llm_config()


def _render_reports(report_repository: ReportRepository, workspace_id: str) -> None:
    section("报告列表", "按时间倒序；点开即看全文、下载 Markdown 或删除")

    col1, col2 = st.columns([3, 1.2])

    with col1:
        keyword = st.text_input(
            "搜索报告",
            placeholder="标题、产品、错误码或正文关键词",
            key="report_keyword",
            label_visibility="collapsed",
        )

    with col2:
        generated_by_label = st.selectbox(
            "生成方式",
            options=["全部", "规则诊断", "LLM 参与"],
            key="report_generated_by",
            label_visibility="collapsed",
        )

    generated_by = {"全部": None, "规则诊断": "rule", "LLM 参与": "llm"}[
        generated_by_label
    ]

    reports = report_repository.list_reports(
        workspace_id,
        keyword=keyword or None,
        generated_by=generated_by,
        limit=100,
    )

    if not reports:
        empty_state(
            "还没有诊断报告",
            "到「单条诊断」生成一次诊断并保存，报告会出现在这里；"
            "同一反馈、同一内容不会重复入库。",
        )
        return

    st.caption(f"共 {len(reports)} 份报告")

    for report in reports:
        severity = SEVERITY_LABELS.get(report["severity"], report["severity"] or "—")
        confidence = CONFIDENCE_LABELS.get(
            report["confidence"], report["confidence"] or "—"
        )
        head = report["error_code"] or "无错误码"
        label = (
            f"{_fmt_time(report['created_at'])} · {report['product'] or '未知产品'}"
            f" · {head} · {report['title']}"
        )

        with st.expander(label):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("严重程度", severity)
            c2.metric("证据充分度", confidence)
            c3.metric("关联证据", report["matched_count"])
            c4.metric("上下文证据", report["context_count"])

            mode = "规则 + LLM" if report["generated_by"] == "llm" else "规则诊断"
            model_note = f" · {report['model']}" if report["model"] else ""
            st.caption(
                f"生成方式：{mode}{model_note}｜窗口 {report['window_minutes']} 分钟"
                f"｜最低分 {report['min_score']:g}｜反馈：{report['feedback_summary'] or '—'}"
            )

            st.markdown(report["markdown"])

            download_col, delete_col = st.columns([3, 1])

            with download_col:
                st.download_button(
                    "下载 Markdown",
                    data=report["markdown"].encode("utf-8"),
                    file_name=_safe_file_name(report["title"]),
                    mime="text/markdown",
                    key=f"report_download_{report['id']}",
                )

            with delete_col:
                pending_key = "report_delete_pending"
                is_pending = st.session_state.get(pending_key) == report["id"]

                if is_pending:
                    st.warning("确认删除这份报告？")

                    if st.button(
                        "确认删除",
                        key=f"report_delete_confirm_{report['id']}",
                        type="primary",
                    ):
                        report_repository.delete(report["id"])
                        st.session_state.pop(pending_key, None)
                        st.success("报告已删除。")
                        st.rerun()

                    if st.button("取消", key=f"report_delete_cancel_{report['id']}"):
                        st.session_state.pop(pending_key, None)
                        st.rerun()
                else:
                    if st.button("删除", key=f"report_delete_{report['id']}"):
                        st.session_state[pending_key] = report["id"]
                        st.rerun()


def _render_llm_config() -> None:
    settings = resolve_settings()

    section(
        "LLM 接入",
        "兼容 OpenAI 接口的官方服务、网关或自建代理；未配置时诊断自动走规则路径",
    )

    status_kind = "正常" if settings.available else "未配置"
    detected_provider = detect_provider(settings.base_url)
    provider_name = PROVIDER_BY_ID[detected_provider].label
    st.markdown(
        f"""
        <div class="wm-panel">
            <p class="wm-panel-title">当前状态
                <span>{tag(status_kind, "low" if settings.available else "medium")}</span>
            </p>
            <div class="wm-panel-row"><span>配置来源</span>
                <span class="wm-row-meta">{config_source_label(settings.source)}</span></div>
            <div class="wm-panel-row"><span>服务商</span>
                <span class="wm-row-meta">{escape(provider_name)}</span></div>
            <div class="wm-panel-row"><span>模型</span>
                <span class="wm-row-meta">{escape(settings.model)}</span></div>
            <div class="wm-panel-row"><span>接口地址</span>
                <span class="wm-row-meta">{escape(settings.base_url or "OpenAI 默认地址")}</span></div>
            <div class="wm-panel-row"><span>API Key</span>
                <span class="wm-row-meta">{settings.masked_key() or "未填写"}</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if settings.source == "env":
        st.info(
            "当前 API Key 来自环境变量，优先级高于页面配置。"
            "要使用页面里保存的配置，需要先移除对应的 OPENAI_* 环境变量。"
        )

    current_key = "" if settings.source == "env" else settings.api_key

    provider_ids = [provider.id for provider in LLM_PROVIDERS]
    provider_labels = {provider.id: provider.label for provider in LLM_PROVIDERS}

    st.session_state.setdefault("llm_provider", detected_provider)
    active_provider = PROVIDER_BY_ID.get(
        st.session_state["llm_provider"],
        PROVIDER_BY_ID[detected_provider],
    )

    st.session_state.setdefault(
        "llm_base_url", settings.base_url or active_provider.base_url
    )

    saved_model = (settings.model or "").strip()
    model_options = list(active_provider.models)

    if saved_model and saved_model not in model_options:
        model_options.append(saved_model)

    if active_provider.models:
        model_options.append(CUSTOM_MODEL_OPTION)

    if active_provider.models:
        default_choice = (
            saved_model
            if saved_model in active_provider.models
            else CUSTOM_MODEL_OPTION
        )
        st.session_state.setdefault("llm_model_choice", default_choice)

    if "llm_custom_model" not in st.session_state:
        st.session_state["llm_custom_model"] = (
            saved_model if saved_model not in active_provider.models else ""
        )

    provider_id = st.selectbox(
        "服务商",
        options=provider_ids,
        format_func=lambda value: provider_labels[value],
        key="llm_provider",
        on_change=_on_llm_provider_change,
        help="只列出 OpenAI 兼容入口；自建网关选「自定义」。",
    )

    provider = PROVIDER_BY_ID[provider_id]

    if provider.note:
        st.caption(provider.note)

    if provider.models:
        model_choice = st.selectbox(
            "模型",
            options=model_options,
            key="llm_model_choice",
        )

        if model_choice == CUSTOM_MODEL_OPTION:
            model = st.text_input(
                "模型名称",
                placeholder="填写服务商实际提供的模型 ID",
                key="llm_custom_model",
            )
        else:
            model = model_choice
    else:
        model = st.text_input(
            "模型名称",
            placeholder="填写服务商实际提供的模型 ID",
            key="llm_custom_model",
        )

    base_url = st.text_input(
        "接口地址（Base URL）",
        placeholder="https://api.openai.com/v1 或自建网关地址",
        key="llm_base_url",
        help="切换服务商会自动填入官方兼容地址，也可以手动改成网关地址。",
    )

    col_key, col_timeout = st.columns([2, 1])

    with col_key:
        api_key = st.text_input(
            "API Key",
            value="",
            type="password",
            placeholder=settings.masked_key() or "sk-...",
            key="llm_api_key",
            help="留空表示沿用已保存的 Key；切换服务商后请填写新 Key。",
        )

    with col_timeout:
        timeout = st.number_input(
            "超时（秒）",
            min_value=5,
            max_value=120,
            value=int(settings.timeout),
            step=5,
            key="llm_timeout",
        )

    draft_key = api_key.strip() or current_key
    model_name = model.strip() or provider.default_model

    draft = LLMSettings(
        api_key=draft_key,
        base_url=base_url.strip(),
        model=model_name,
        timeout=float(timeout),
        source=settings.source,
    )

    action1, action2, action3 = st.columns([1, 1, 1])

    with action1:
        if st.button("保存配置", type="primary", key="llm_save"):
            save_settings(
                api_key=api_key if api_key.strip() else None,
                base_url=base_url,
                model=model_name,
                timeout=timeout,
            )
            st.success("配置已保存，下一次诊断立即生效。")
            st.rerun()

    with action2:
        if st.button("测试连接", key="llm_test"):
            if not draft.available:
                st.error("先填写 API Key 再测试。")
            else:
                result = test_connection(draft)
                if result["ok"]:
                    st.success(
                        f"{result['message']}（{result['latency_ms']} ms）"
                    )
                else:
                    st.error(result["message"])

    with action3:
        if st.button("清除本机配置", key="llm_clear"):
            clear_settings()
            _reset_llm_form_state()
            st.success("本机配置已清除；若仍显示已配置，说明环境变量还在生效。")
            st.rerun()

    st.caption(
        f"本机配置保存在 `{CONFIG_PATH}`，权限 600，已加入 .gitignore；"
        "也可以改用 `.env` 里的 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL。"
    )
