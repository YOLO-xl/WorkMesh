"""智能诊断页：证据驱动的诊断结论与可下载报告。"""

import pandas as pd
import streamlit as st

from core.correlation import DEFAULT_MIN_SCORE, DEFAULT_WINDOW_MINUTES
from database.feedback_repository import FeedbackRepository
from database.migration import initialize_database
from database.report_repository import ReportRepository
from services.diagnosis_service import DiagnosisService
from services.connector_config import (
    DEFAULT_ELASTICSEARCH_URL,
    DEFAULT_PROMETHEUS_URL,
    build_connector_configs,
)
from services.connector_source_provider import ConnectorSourceProvider
from services.evidence_service import EvidenceService
from ui.theme import hero, section


def _feedback_label(record: dict) -> str:
    created = (record.get("feedback_time") or "—")[:16]
    description = (record.get("description") or "")[:24]
    return f"{created} | {record.get('error_code') or '—'} | {description}"


def render(db_path=None) -> None:
    hero(
        "单条诊断",
        "针对一条反馈：先用可解释规则算出证据，再让模型写结论；每条结论必须能回溯到证据 ID",
        ["Single Feedback", "Evidence Grounded", "Citation Checked"],
    )

    initialize_database(db_path)
    feedback_repository = FeedbackRepository(db_path)
    workspace_id = feedback_repository.get_default_workspace()

    with st.expander("外部连接器（可选）"):
        use_connectors = st.checkbox(
            "诊断时连接真实 Elasticsearch 与 Prometheus",
            value=False,
            key="diagnosis_use_connectors",
            help="不勾选时只使用本地反馈与已入库证据；勾选后按命中场景的 connector 数据源查询外部服务。",
        )
        es_url = st.text_input(
            "Elasticsearch 地址",
            value=DEFAULT_ELASTICSEARCH_URL,
            key="diagnosis_es_url",
            disabled=not use_connectors,
        )
        prom_url = st.text_input(
            "Prometheus 地址",
            value=DEFAULT_PROMETHEUS_URL,
            key="diagnosis_prom_url",
            disabled=not use_connectors,
        )

    connector_provider = None
    if use_connectors:
        connector_provider = ConnectorSourceProvider(
            build_connector_configs(
                elasticsearch_urls=es_url,
                prometheus_url=prom_url,
            )
        )

    service = DiagnosisService(
        evidence_service=EvidenceService(db_path=db_path),
        db_path=db_path,
        connector_provider=connector_provider,
    )

    records = feedback_repository.list_feedback(
        workspace_id, page=1, page_size=100
    )["records"]

    if not records:
        st.info("还没有反馈数据，请先到「数据接入」导入反馈文件。")
        return

    section("选择分析对象", "以一条反馈为锚点，关联窗口内的日志与指标")
    record_ids = [record["id"] for record in records]
    preselected = st.session_state.get("diagnosis_feedback")

    if preselected and preselected not in record_ids:
        st.session_state.pop("diagnosis_feedback", None)
        preselected = None

    if preselected:
        st.caption("已从问题簇带入一条反馈，可直接生成诊断。")

    feedback_id = st.selectbox(
        "反馈记录",
        options=record_ids, index=record_ids.index(preselected) if preselected else 0,
        format_func=lambda value: _feedback_label(
            next(record for record in records if record["id"] == value)
        ),
        key="diagnosis_feedback",
    )

    col1, col2 = st.columns(2)

    with col1:
        window_minutes = st.slider(
            "关联时间窗口（分钟）",
            min_value=5,
            max_value=240,
            value=DEFAULT_WINDOW_MINUTES,
            step=5,
            key="diagnosis_window",
        )

    with col2:
        min_score = st.slider(
            "最低关联分数",
            min_value=10,
            max_value=80,
            value=int(DEFAULT_MIN_SCORE),
            step=5,
            key="diagnosis_min_score",
        )

    if service.llm_available:
        use_llm = st.checkbox(
            "使用 LLM 润色推断（仍强制校验证据引用）",
            value=True,
            key="diagnosis_use_llm",
        )
        st.caption(
            f"当前模型：{service.llm_model}｜配置来源：{service.llm_source}"
        )
    else:
        use_llm = False
        st.caption(
            "未检测到 LLM 配置：可在「报告中心 → LLM 配置」中填写，"
            "或设置 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL。"
            "当前使用规则诊断。"
        )

    auto_archive = st.checkbox(
        "生成后自动归档到报告中心",
        value=True,
        key="diagnosis_auto_archive",
        help="同一反馈、同一内容不会重复归档。",
    )

    selected_record = next(
        (item for item in records if item["id"] == feedback_id), {}
    )
    trace_default = bool(
        selected_record.get("trace_id") or selected_record.get("request_id")
    )
    has_trace = st.checkbox(
        "已有请求级 Trace ID / Request ID",
        value=trace_default,
        key="diagnosis_has_trace",
        help="故障场景会用它判断能否继续做请求级排查；没有就标为未验证。",
    )

    if st.button("生成诊断", type="primary", key="diagnosis_run"):
        try:
            result = service.build(
                workspace_id=workspace_id,
                feedback_id=feedback_id,
                window_minutes=window_minutes,
                min_score=min_score,
                use_llm=use_llm,
                manual_answers={"trace_lookup": has_trace},
            )
        except LookupError as exc:
            st.error(str(exc))
            return

        feedback = next(
            (item for item in records if item["id"] == feedback_id), {}
        )

        st.session_state["diagnosis_result"] = result
        st.session_state["diagnosis_meta"] = {
            "feedback": feedback,
            "workspace_id": workspace_id,
            "window_minutes": window_minutes,
            "min_score": min_score,
            "model": service.llm_model if result.get("generated_by") == "llm" else None,
            "db_path": str(db_path) if db_path else None,
        }

        st.session_state["diagnosis_report"] = None

        if auto_archive:
            report_id, created = ReportRepository(db_path).save(
                workspace_id,
                feedback,
                result,
                model=st.session_state["diagnosis_meta"]["model"],
                window_minutes=window_minutes,
                min_score=min_score,
            )
            st.session_state["diagnosis_report"] = {
                "id": report_id,
                "created": created,
            }

    result = st.session_state.get("diagnosis_result")

    if not result:
        st.caption("点击「生成诊断」开始分析。")
        return

    _render_result(result, st.session_state.get("diagnosis_meta") or {})


def _render_scenario(scenario: dict) -> None:
    """展示故障场景命中结果、排查清单与结论边界。"""

    st.subheader("故障场景")
    st.caption(
        f"命中：{scenario.get('name')}（{scenario.get('scenario_id')} · "
        f"{scenario.get('version')}）"
    )

    status_labels = {
        "verified": "已验证",
        "partial": "部分验证",
        "blocked": "关键数据缺失",
    }
    confidence_labels = {
        "high": "高",
        "medium": "中",
        "low": "低",
    }

    c1, c2, c3 = st.columns(3)
    c1.metric("场景状态", status_labels.get(scenario.get("status"), scenario.get("status")))
    c2.metric(
        "场景置信度",
        confidence_labels.get(scenario.get("confidence"), scenario.get("confidence")),
    )
    c3.metric("缺失数据源", len(scenario.get("missing_sources") or []))

    reasons = (scenario.get("match") or {}).get("reasons") or []
    if reasons:
        st.caption("命中理由：" + "；".join(reasons))

    step_labels = {
        "pass": "通过",
        "fail": "未通过",
        "unknown": "未验证",
        "skipped": "跳过",
    }

    rows = [
        [
            step.get("title"),
            step_labels.get(step.get("status"), step.get("status")),
            step.get("observation") or "—",
            step.get("reason") or "—",
        ]
        for step in scenario.get("steps") or []
    ]

    if rows:
        st.dataframe(
            pd.DataFrame(rows, columns=["排查步骤", "状态", "观测值", "说明"]),
            width="stretch",
            hide_index=True,
        )

    hypotheses = scenario.get("hypotheses") or []
    if hypotheses:
        st.markdown("**场景假设（待验证）**")
        for item in hypotheses:
            confidence = confidence_labels.get(
                item.get("confidence"), item.get("confidence")
            )
            st.caption(
                f"置信度 {confidence}｜{item.get('statement')}"
            )

    missing = scenario.get("missing_sources") or []
    if missing:
        st.warning(
            "缺失数据源："
            + "、".join(item.get("id") or "" for item in missing)
            + "。相关步骤未验证，结论已降级。"
        )
        reasons = "；".join(
            f"{item.get('id') or ''}：{item.get('reason')}"
            for item in missing
            if item.get("reason")
        )
        if reasons:
            st.caption("缺失原因：" + reasons)

    limits = scenario.get("limits") or []
    if limits:
        st.caption("结论边界：" + "；".join(limits))


def _render_result(result: dict, meta: dict) -> None:
    st.divider()

    severity = {
        "high": "高",
        "medium": "中",
        "low": "低",
    }.get(result.get("severity"), result.get("severity"))
    generated = "规则诊断" if result.get("generated_by") == "rule" else "规则 + LLM"
    confidence = {
        "high": "高",
        "medium": "中",
        "low": "低",
    }.get(result.get("confidence"), result.get("confidence"))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("严重程度", severity)
    c2.metric("证据充分度", confidence)
    c3.metric("关联证据", result["context_counts"]["matched"])
    c4.metric("上下文证据", result["context_counts"].get("context", 0))

    st.caption(f"生成方式：{generated}")

    archived = st.session_state.get("diagnosis_report")
    if archived:
        st.caption(
            "已归档到报告中心。"
            if archived["created"]
            else "该内容已存在于报告中心，未重复归档。"
        )

    if result.get("llm_error"):
        st.warning(result["llm_error"])

    if result.get("scenario"):
        _render_scenario(result["scenario"])

    st.markdown(result.get("markdown_body") or result["markdown"])

    refs = result.get("evidence_refs") or []

    if refs:
        with st.expander("证据引用明细"):
            st.dataframe(
                pd.DataFrame(refs).rename(
                    columns={
                        "id": "证据 ID",
                        "type": "类型",
                        "relation": "关系",
                        "score": "分数",
                        "label": "摘要",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    action_col, download_col = st.columns([1, 1.4])

    with action_col:
        if st.button("保存到报告中心", key="diagnosis_archive"):
            feedback = meta.get("feedback") or {}
            report_id, created = ReportRepository(meta.get("db_path")).save(
                meta["workspace_id"],
                feedback,
                result,
                model=meta.get("model"),
                window_minutes=meta.get("window_minutes", 30),
                min_score=meta.get("min_score", 30),
            )
            st.session_state["diagnosis_report"] = {
                "id": report_id,
                "created": created,
            }
            st.success("已归档，可在「报告中心」查看。" if created else "这份报告已经归档过了。")

    with download_col:
        st.download_button(
            "下载诊断报告（Markdown）",
            data=result["markdown"].encode("utf-8"),
            file_name="workmesh_diagnosis.md",
            mime="text/markdown",
        )
