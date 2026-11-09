"""首屏（首页）：一句话说明系统做什么，再给出模块入口。

刻意保持简短：不在这里堆统计、流程和长篇说明，
那些内容属于各功能页本身。
"""

import streamlit as st

from services.cluster_service import ClusterService
from ui.theme import hero, section


CAPABILITIES = [
    (
        "证据统一",
        "反馈、日志、指标放进同一张证据表。",
        "不再在四个系统之间来回翻。",
    ),
    (
        "关联可解释",
        "每次关联都有分数、命中键和理由。",
        "能说清「为什么这条日志算证据」。",
    ),
    (
        "结论可回溯",
        "事实与推断分开，结论必须引用证据。",
        "换个人接手也能复核。",
    ),
]

MODULES = [
    ("数据接入", "导入反馈，字段自动映射与质量校验。", "数据接入"),
    ("证据关联", "接入日志 / 指标，围绕反馈算关联。", "证据关联"),
    ("问题聚类", "同类反馈自动成簇，先看影响面。", "问题聚类"),
    ("反馈分析", "版本、平台、错误码的分布与趋势。", "反馈分析"),
    ("单条诊断", "针对一条反馈生成带引用的诊断。", "单条诊断"),
    ("报告中心", "沉淀诊断报告，按问题回看。", "报告中心"),
]


def _open_page(page: str) -> None:
    st.session_state.page_navigation = page
    st.session_state.wm_view = "module"


def render(feedback_repository, evidence_repository, workspace_id: str) -> None:
    hero(
        "WorkMesh",
        "把一条用户反馈，变成一份能回溯证据的排查结论",
        ["Evidence Driven", "Explainable", "Rule + LLM"],
    )

    analytics = feedback_repository.get_feedback_analytics(workspace_id)
    overview = analytics["overview"]
    evidence_total = evidence_repository.count_evidence(workspace_id)

    clusters = ClusterService(
        feedback_repository=feedback_repository,
        evidence_repository=evidence_repository,
    ).build(workspace_id, with_evidence=False)
    cluster_count = clusters["stats"]["cluster_count"]

    st.markdown(
        f"""
        <div class="wm-status-bar" style="justify-content:space-between">
            <span>反馈 <b>{overview['total_feedback']}</b></span>
            <span>证据 <b>{evidence_total}</b></span>
            <span>问题簇 <b>{cluster_count}</b></span>
            <span>工作空间 <b>{workspace_id[:8]}…</b></span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    action_col, preview_col, _ = st.columns([1, 1, 3])

    with action_col:
        st.button(
            "进入工作台",
            type="primary",
            use_container_width=True,
            on_click=_open_page,
            args=("数据接入",),
        )

    with preview_col:
        st.button(
            "先看问题聚类",
            use_container_width=True,
            on_click=_open_page,
            args=("问题聚类",),
        )

    section("能做什么", "三件事，一句话")

    columns = st.columns(3)

    for column, (title, line, value) in zip(columns, CAPABILITIES):
        with column:
            st.markdown(
                f"""
                <div class="wm-card" style="min-height:8.2rem">
                    <p class="wm-card-title">{title}</p>
                    <p class="wm-card-text">{line}</p>
                    <p class="wm-card-text" style="color:#64748B;margin-top:0.45rem">{value}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

    section("模块", "按排查阶段进入")

    for row_start in (0, 3):
        columns = st.columns(3)

        for offset, (title, description, page) in enumerate(
            MODULES[row_start:row_start + 3]
        ):
            with columns[offset]:
                st.markdown(
                    f"""
                    <div class="wm-card" style="min-height:6.2rem">
                        <p class="wm-card-title">{title}</p>
                        <p class="wm-card-text">{description}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.button(
                    f"进入{title}",
                    key=f"landing_go_{page}",
                    use_container_width=True,
                    on_click=_open_page,
                    args=(page,),
                )

    st.caption(
        "提示：页面支持深链接，例如 http://localhost:8501/?page=问题聚类"
    )
