"""智能诊断页：证据驱动的诊断结论与可下载报告。"""

import pandas as pd
import streamlit as st

from core.correlation import DEFAULT_MIN_SCORE, DEFAULT_WINDOW_MINUTES
from database.feedback_repository import FeedbackRepository
from database.migration import initialize_database
from services.diagnosis_service import DiagnosisService
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

    service = DiagnosisService(
        evidence_service=EvidenceService(db_path=db_path),
        db_path=db_path,
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
        st.caption(f"当前模型：{service.llm_model}")
    else:
        use_llm = False
        st.caption(
            "未检测到 LLM 配置：设置 OPENAI_API_KEY / OPENAI_BASE_URL / "
            "OPENAI_MODEL 后可用。当前使用规则诊断。"
        )

    if st.button("生成诊断", type="primary", key="diagnosis_run"):
        try:
            result = service.build(
                workspace_id=workspace_id,
                feedback_id=feedback_id,
                window_minutes=window_minutes,
                min_score=min_score,
                use_llm=use_llm,
            )
        except LookupError as exc:
            st.error(str(exc))
            return

        st.session_state["diagnosis_result"] = result

    result = st.session_state.get("diagnosis_result")

    if not result:
        st.caption("点击「生成诊断」开始分析。")
        return

    _render_result(result)


def _render_result(result: dict) -> None:
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

    if result.get("llm_error"):
        st.warning(result["llm_error"])

    st.markdown(result["markdown"])

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

    st.download_button(
        "下载诊断报告（Markdown）",
        data=result["markdown"].encode("utf-8"),
        file_name="workmesh_diagnosis.md",
        mime="text/markdown",
    )
