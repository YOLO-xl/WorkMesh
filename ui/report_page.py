"""报告中心：诊断报告沉淀与 LLM 接入配置。"""

import re

from datetime import datetime

import streamlit as st

from html import escape

from database.cluster_report_repository import ClusterReportRepository
from database.feedback_repository import FeedbackRepository
from database.migration import initialize_database
from database.report_repository import ReportRepository
from database.report_tracking_repository import ReportTrackingRepository
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


TRACKING_LABELS = {
    "pending": "待处理",
    "handled": "已处理",
    "ignored": "已忽略",
}
TRACKING_OPTION_TO_STATUS = {
    "待处理": "pending",
    "已处理": "handled",
    "已忽略": "ignored",
}
TRACKING_FILTERS = ["全部状态", "待处理", "已处理", "已忽略"]


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
        "单条诊断与问题簇报告统一沉淀；可检索、可下载，LLM 接入在同一页配置",
        ["Archive", "Batch", "Configurable"],
    )

    initialize_database(db_path)
    feedback_repository = FeedbackRepository(db_path)
    report_repository = ReportRepository(db_path)
    cluster_report_repository = ClusterReportRepository(db_path)
    tracking_repository = ReportTrackingRepository(db_path)
    workspace_id = feedback_repository.get_default_workspace()

    stats = report_repository.stats(workspace_id)
    cluster_stats = cluster_report_repository.stats(workspace_id)
    total_reports = stats["total"] + cluster_stats["total"]

    tracking_stats = tracking_repository.stats(workspace_id)
    pending_reports = max(
        0,
        total_reports - tracking_stats["handled"] - tracking_stats["ignored"],
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("报告总数", total_reports)
    c2.metric("待处理", pending_reports)
    c3.metric("近 7 天新增", stats["recent_total"] + cluster_stats["recent_total"])
    c4.metric("覆盖反馈", stats["covered_feedback"])
    c5.metric("簇报告", cluster_stats["total"])

    tab_reports, tab_clusters, tab_llm = st.tabs(
        [
            f"诊断报告 {stats['total']}",
            f"簇报告 {cluster_stats['total']}",
            "LLM 配置",
        ]
    )

    with tab_reports:
        _render_reports(report_repository, tracking_repository, workspace_id)

    with tab_clusters:
        _render_cluster_reports(
            cluster_report_repository, tracking_repository, workspace_id
        )

    with tab_llm:
        _render_llm_config()


def _show_tracking_flash(key: str) -> None:
    message = st.session_state.pop(key, None)
    if message:
        st.success(message)


def _tracking_line(entry: dict) -> str:
    label = TRACKING_LABELS.get(entry.get("status"), "待处理")
    parts = [f"处理：{label}"]

    if entry.get("owner"):
        parts.append(f"负责人：{entry['owner']}")
    if entry.get("note"):
        note = str(entry["note"])
        parts.append(f"备注：{note[:80]}{'…' if len(note) > 80 else ''}")
    if entry.get("updated_at"):
        parts.append(f"更新：{_fmt_time(entry['updated_at'])}")

    return "｜".join(parts)


def _render_tracking_form(
    tracking_repository: ReportTrackingRepository,
    workspace_id: str,
    scope: str,
    report_id: str,
    entry: dict,
    flash_key: str,
) -> None:
    st.markdown("**处理记录**")

    current_label = TRACKING_LABELS.get(entry.get("status"), "待处理")
    status_options = [item for item in TRACKING_OPTION_TO_STATUS]

    with st.form(key=f"tracking_form_{scope}_{report_id}"):
        c1, c2, c3 = st.columns([1.1, 1.2, 2.4])

        with c1:
            status_label = st.selectbox(
                "处理状态",
                status_options,
                index=status_options.index(current_label),
                key=f"tracking_status_{scope}_{report_id}",
            )

        with c2:
            owner = st.text_input(
                "负责人",
                value=entry.get("owner") or "",
                key=f"tracking_owner_{scope}_{report_id}",
            )

        with c3:
            note = st.text_input(
                "备注",
                value=entry.get("note") or "",
                placeholder="例如：已联系研发，等待版本修复",
                max_chars=500,
                key=f"tracking_note_{scope}_{report_id}",
            )

        submitted = st.form_submit_button("保存处理记录")

    if submitted:
        tracking_repository.upsert(
            workspace_id,
            scope,
            report_id,
            status=TRACKING_OPTION_TO_STATUS[status_label],
            owner=owner,
            note=note,
        )
        st.session_state[flash_key] = "处理记录已保存。"
        st.rerun()


def _render_bulk_tracking(
    tracking_repository: ReportTrackingRepository,
    workspace_id: str,
    scope: str,
    reports: list[dict],
    flash_key: str,
) -> None:
    with st.expander(f"批量处理当前 {len(reports)} 份报告"):
        st.caption(
            "按当前筛选结果批量更新。可先用产品、严重度或处理状态缩小范围；"
            "负责人 / 备注留空时保留各报告原值。"
        )

        with st.form(key=f"bulk_tracking_{scope}"):
            c1, c2, c3 = st.columns([1.1, 1.2, 2.4])

            with c1:
                batch_status_label = st.selectbox(
                    "批量状态",
                    list(TRACKING_OPTION_TO_STATUS),
                    index=1,
                    key=f"bulk_tracking_status_{scope}",
                )

            with c2:
                batch_owner = st.text_input(
                    "批量负责人",
                    placeholder="留空保留原值",
                    key=f"bulk_tracking_owner_{scope}",
                )

            with c3:
                batch_note = st.text_input(
                    "批量备注",
                    placeholder="留空保留原值",
                    max_chars=500,
                    key=f"bulk_tracking_note_{scope}",
                )

            confirmed = st.checkbox(
                f"确认更新这 {len(reports)} 份报告",
                key=f"bulk_tracking_confirm_{scope}",
            )
            submitted = st.form_submit_button("批量更新")

        if submitted:
            if not confirmed:
                st.warning("请先勾选确认，再执行批量更新。")
                return

            changed = tracking_repository.bulk_update(
                workspace_id,
                scope,
                [report["id"] for report in reports],
                status=TRACKING_OPTION_TO_STATUS[batch_status_label],
                owner=batch_owner,
                note=batch_note,
            )
            st.session_state[flash_key] = f"已批量更新 {changed} 份报告。"
            st.rerun()


def _render_reports(
    report_repository: ReportRepository,
    tracking_repository: ReportTrackingRepository,
    workspace_id: str,
) -> None:
    section("报告列表", "按时间倒序；点开即看全文、下载 Markdown 或删除")
    _show_tracking_flash("tracking_flash_feedback")

    col1, col2, col3 = st.columns([2.6, 1.2, 1.2])

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

    with col3:
        status_filter = st.selectbox(
            "处理状态",
            options=TRACKING_FILTERS,
            key="report_tracking_filter",
            label_visibility="collapsed",
        )

    generated_by = {"全部": None, "规则诊断": "rule", "LLM 参与": "llm"}[
        generated_by_label
    ]

    base_reports = report_repository.list_reports(
        workspace_id,
        keyword=keyword or None,
        generated_by=generated_by,
        limit=100,
    )

    if not base_reports:
        empty_state(
            "还没有诊断报告",
            "到「单条诊断」生成一次诊断并保存，报告会出现在这里；"
            "同一反馈、同一内容不会重复入库。",
        )
        return

    tracking_entries = tracking_repository.get_many(
        workspace_id,
        "feedback",
        [report["id"] for report in base_reports],
    )

    selected_status = TRACKING_OPTION_TO_STATUS.get(status_filter)
    reports = (
        base_reports
        if not selected_status
        else [
            report
            for report in base_reports
            if tracking_entries[report["id"]]["status"] == selected_status
        ]
    )

    if not reports:
        empty_state("没有匹配的报告", "换个生成方式、处理状态或关键词再试。")
        return

    st.caption(f"共 {len(reports)} / {len(base_reports)} 份报告")
    _render_bulk_tracking(
        tracking_repository,
        workspace_id,
        "feedback",
        reports,
        "tracking_flash_feedback",
    )

    for report in reports:
        entry = tracking_entries[report["id"]]
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
            st.caption(_tracking_line(entry))
            st.markdown(report["markdown"])
            _render_tracking_form(
                tracking_repository,
                workspace_id,
                "feedback",
                report["id"],
                entry,
                "tracking_flash_feedback",
            )

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
                        tracking_repository.delete(
                            workspace_id, "feedback", report["id"]
                        )
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


def _previous_snapshots(
    report_repository: ClusterReportRepository,
    workspace_id: str,
    reports: list[dict],
) -> dict[str, dict]:
    """同一产品 + 错误码的上一份快照，用于展示变化。"""

    keys = {
        (
            report["product"],
            report["error_code"],
            None if report["error_code"] else report["title"],
        )
        for report in reports
    }
    previous: dict[str, dict] = {}

    for product, error_code, title in keys:
        history = report_repository.issue_history(
            workspace_id,
            product,
            error_code,
            title=title,
            limit=100,
        )
        for index in range(1, len(history)):
            previous[history[index]["id"]] = history[index - 1]

    return previous


def _render_cluster_reports(
    report_repository: ClusterReportRepository,
    tracking_repository: ReportTrackingRepository,
    workspace_id: str,
) -> None:
    section(
        "簇报告",
        "从「问题聚类」对某个簇生成；含影响面、现象分布、抽样诊断与共性证据",
    )
    _show_tracking_flash("tracking_flash_cluster")

    all_reports = report_repository.list_reports(workspace_id, limit=200)

    if not all_reports:
        empty_state(
            "还没有簇报告",
            "到「问题聚类」展开一个簇，点「生成簇报告」；"
            "报告会汇总影响面、现象分布，并抽样成员做单条诊断。",
        )

        if st.button("去问题聚类生成", key="cluster_report_goto_cluster"):
            st.session_state["page_navigation"] = "问题聚类"
            st.session_state["wm_view"] = "module"
            st.rerun()

        return

    keyword = st.text_input(
        "搜索簇报告",
        placeholder="标题、产品、错误码或正文关键词",
        key="cluster_report_keyword",
        label_visibility="collapsed",
    )

    products = sorted(
        {report["product"] for report in all_reports if report["product"]}
    )
    product_options = ["全部产品"] + products

    product_col, severity_col, status_col = st.columns([1.4, 1.2, 1.2])

    with product_col:
        product_filter = st.selectbox(
            "按产品筛选",
            product_options,
            key="cluster_report_product",
            label_visibility="collapsed",
        )

    with severity_col:
        severity_filter = st.selectbox(
            "按严重程度筛选",
            ["全部严重度", "高", "中", "低"],
            key="cluster_report_severity",
            label_visibility="collapsed",
        )

    with status_col:
        status_filter = st.selectbox(
            "处理状态",
            options=TRACKING_FILTERS,
            key="cluster_report_tracking_filter",
            label_visibility="collapsed",
        )

    severity_map = {"高": "high", "中": "medium", "低": "low"}
    selected_severity = severity_map.get(severity_filter)
    selected_status = TRACKING_OPTION_TO_STATUS.get(status_filter)

    tracking_entries = tracking_repository.get_many(
        workspace_id,
        "cluster",
        [report["id"] for report in all_reports],
    )

    reports = []
    keyword_lower = (keyword or "").strip().lower()

    for report in all_reports:
        if product_filter != "全部产品" and report["product"] != product_filter:
            continue
        if selected_severity and report["severity"] != selected_severity:
            continue
        if (
            selected_status
            and tracking_entries[report["id"]]["status"] != selected_status
        ):
            continue
        if keyword_lower:
            haystack = " ".join(
                str(report.get(key) or "")
                for key in ("title", "product", "error_code", "markdown")
            ).lower()
            if keyword_lower not in haystack:
                continue
        reports.append(report)

    if not reports:
        empty_state(
            "没有匹配的簇报告",
            "换个产品、严重程度、处理状态或关键词再试。",
        )
        return

    st.caption(f"共 {len(reports)} / {len(all_reports)} 份簇报告")
    _render_bulk_tracking(
        tracking_repository,
        workspace_id,
        "cluster",
        reports,
        "tracking_flash_cluster",
    )

    previous_snapshots = _previous_snapshots(
        report_repository, workspace_id, reports
    )

    for report in reports:
        entry = tracking_entries[report["id"]]
        severity = SEVERITY_LABELS.get(report["severity"], report["severity"] or "—")
        confidence = CONFIDENCE_LABELS.get(
            report["confidence"], report["confidence"] or "—"
        )
        head = report["error_code"] or "无错误码"
        label = (
            f"{_fmt_time(report['created_at'])} · {report['product'] or '未知产品'}"
            f" · {head} · {report['size']} 条反馈 · {report['title']}"
        )

        with st.expander(label):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("反馈数", report["size"])
            c2.metric("涉及用户", report["unique_users"])
            c3.metric("关联证据", report["evidence_total"])
            c4.metric("证据充分度", confidence)

            previous = previous_snapshots.get(report["id"])
            if previous:
                size_delta = report["size"] - previous["size"]
                user_delta = report["unique_users"] - previous["unique_users"]
                evidence_delta = report["evidence_total"] - previous["evidence_total"]
                if size_delta == user_delta == evidence_delta == 0:
                    trend = (
                        f"较上次（{_fmt_time(previous['created_at'])}）："
                        "成员与证据无变化"
                    )
                else:
                    trend = (
                        f"较上次（{_fmt_time(previous['created_at'])}）："
                        f"反馈 {size_delta:+d} / 用户 {user_delta:+d} / "
                        f"证据 {evidence_delta:+d}"
                    )
            else:
                trend = "首次快照"

            st.caption(f"严重程度：{severity}｜生成方式：规则汇总｜{trend}")
            st.caption(_tracking_line(entry))
            st.markdown(report["markdown"])
            _render_tracking_form(
                tracking_repository,
                workspace_id,
                "cluster",
                report["id"],
                entry,
                "tracking_flash_cluster",
            )

            download_col, delete_col = st.columns([3, 1])

            with download_col:
                st.download_button(
                    "下载 Markdown",
                    data=report["markdown"].encode("utf-8"),
                    file_name=_safe_file_name(report["title"]),
                    mime="text/markdown",
                    key=f"cluster_report_download_{report['id']}",
                )

            with delete_col:
                pending_key = "cluster_report_delete_pending"
                is_pending = st.session_state.get(pending_key) == report["id"]

                if is_pending:
                    st.warning("确认删除这份簇报告？")

                    if st.button(
                        "确认删除",
                        key=f"cluster_report_delete_confirm_{report['id']}",
                        type="primary",
                    ):
                        report_repository.delete(report["id"])
                        tracking_repository.delete(
                            workspace_id, "cluster", report["id"]
                        )
                        st.session_state.pop(pending_key, None)
                        st.success("簇报告已删除。")
                        st.rerun()

                    if st.button(
                        "取消",
                        key=f"cluster_report_delete_cancel_{report['id']}",
                    ):
                        st.session_state.pop(pending_key, None)
                        st.rerun()
                else:
                    if st.button(
                        "删除",
                        key=f"cluster_report_delete_{report['id']}",
                    ):
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
