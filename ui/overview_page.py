"""系统概览页（首屏）：系统是什么、解决什么问题、各模块怎么用。"""

import streamlit as st

from services.cluster_service import ClusterService, severity_label
from ui.theme import data_list, empty_state, hero, section, status_bar, stepper


MODULES = [
    (
        "数据接入",
        "导入 CSV / Excel 反馈，自动识别字段并做质量校验，不合格的数据不落库。",
        "解决：反馈散在工单、问卷、客服群里，字段还不统一。",
        "数据接入",
    ),
    (
        "证据关联",
        "接入日志、指标、变更记录，围绕一条反馈按标识与时间窗口找相关证据。",
        "解决：排查时要人肉在多个系统里翻日志，还说不清为什么相关。",
        "证据关联",
    ),
    (
        "问题聚类",
        "把同类反馈自动归成问题簇，给出影响面、发生时段和主要现象。",
        "解决：几十条反馈看不出是几个问题，也不知道先修哪个。",
        "问题聚类",
    ),
    (
        "反馈分析",
        "版本、平台、错误码分布与趋势，快速看清问题集中在哪。",
        "解决：缺少整体视角，判断不了是普遍问题还是个别用户。",
        "反馈分析",
    ),
    (
        "单条诊断",
        "针对一条反馈生成诊断结论，事实与推断分开，每条结论必须引用证据。",
        "解决：结论靠经验口口相传，换个同事就要重新查一遍。",
        "单条诊断",
    ),
    (
        "报告中心",
        "沉淀诊断报告，按时间与问题回看。",
        "解决：排查过程无法复用，同类问题第二次发生时从零开始。",
        "报告中心",
    ),
]


def _short(text: str, length: int = 8) -> str:
    return text if len(text) <= length else f"{text[:length]}…"


def render(feedback_repository, evidence_repository, workspace_id: str) -> None:
    hero(
        "WorkMesh",
        "企业技术支持工作台：把一条用户反馈变成一份能回溯证据的排查结论",
        ["Evidence Driven", "Explainable Correlation", "Rule + LLM"],
    )

    section("系统定位", "为什么需要一个工作台，而不是再写几个查询脚本")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            """
            <div class="wm-card">
                <p class="wm-card-title">现在排查一条反馈长什么样</p>
                <p class="wm-card-text">
                在工单系统看用户描述，去日志平台按关键词搜，翻监控曲线确认时间点，
                再问一句「上周是不是改过什么」。结论靠记忆，证据靠截图，
                下次同类问题基本重来一遍。
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            """
            <div class="wm-card">
                <p class="wm-card-title">WorkMesh 把它固化成流程</p>
                <p class="wm-card-text">
                反馈、日志、指标、变更、备注放进统一证据模型；
                关联规则可解释、可复算；结论必须能指回证据 ID，
                事实和推断永远分开写。换个人接手，看到的是同一份证据链。
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    section("解决什么问题", "三类典型场景")
    pain_points = [
        (
            "「这是不是一个普遍问题？」",
            "问题聚类把同类反馈归簇，直接给出影响用户数、集中时段和主要现象。",
        ),
        (
            "「为什么这条日志算证据？」",
            "关联过程带分数、命中键和逐条理由，不是黑盒相似度。",
        ),
        (
            "「这个结论可信吗？」",
            "事实来自字段一致，推断单独标注置信度；没有证据支撑的结论不会出现。",
        ),
    ]

    columns = st.columns(3)

    for column, (question, answer) in zip(columns, pain_points):
        with column:
            st.markdown(
                f"""
                <div class="wm-card">
                    <p class="wm-card-title">{question}</p>
                    <p class="wm-card-text">{answer}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

    section("模块地图", "按排查阶段选择入口")
    _render_modules()

    section("一次排查的完整路径", "从原始反馈到可下载的报告")
    stepper(
        [
            "导入反馈数据",
            "接入日志 / 指标证据",
            "聚类定位或选定单条反馈",
            "生成证据上下文与诊断",
            "沉淀报告复盘",
        ]
    )

    section("当前系统状态", "实时读取本地数据库")
    analytics = feedback_repository.get_feedback_analytics(workspace_id)
    overview = analytics["overview"]
    evidence_total = evidence_repository.count_evidence(workspace_id)

    cluster_result = ClusterService(
        feedback_repository=feedback_repository,
        evidence_repository=evidence_repository,
    ).build(workspace_id, with_evidence=False)
    stats = cluster_result["stats"]

    status_bar(
        [
            ("工作空间", _short(workspace_id)),
            ("数据库", "SQLite · Schema v2"),
            ("证据类型", "feedback · log · metric · trace · change · note"),
            ("诊断模式", "规则诊断可用 · LLM 可选"),
        ]
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("反馈总量", overview["total_feedback"])
    c2.metric("已接入证据", evidence_total)
    c3.metric("问题簇", stats["cluster_count"])
    c4.metric("孤立反馈", stats["singleton_count"])

    clusters = cluster_result["clusters"]

    if clusters:
        section("影响面最大的问题簇", "按反馈量、用户量与证据量排序")
        rows = []

        for cluster in clusters[:4]:
            rows.append(
                [
                    cluster["title"],
                    f'{cluster["size"]} 条 / {cluster["unique_users"]} 人',
                    severity_label(cluster["severity"]),
                    cluster["phenomena"][0]["label"] if cluster["phenomena"] else "—",
                    cluster["last_seen"] or "—",
                ]
            )

        data_list(
            ["问题簇", "影响面", "严重程度", "主要现象", "最近出现"],
            rows,
            widths=["1.6fr", "1fr", "0.8fr", "1fr", "1.4fr"],
        )
    else:
        empty_state(
            "还没有可聚类的问题",
            "先在「数据接入」导入反馈，聚类页会自动按产品与错误码归簇。",
        )


def _render_modules() -> None:
    for row_start in (0, 3):
        columns = st.columns(3)

        for offset, item in enumerate(MODULES[row_start:row_start + 3]):
            title, description, value, page = item

            with columns[offset]:
                st.markdown(
                    f"""
                    <div class="wm-card" style="min-height:9.5rem">
                        <p class="wm-card-title">{title}</p>
                        <p class="wm-card-text">{description}</p>
                        <p class="wm-card-text" style="color:#64748B;margin-top:0.5rem">{value}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.button(
                    f"进入{title}",
                    key=f"overview_go_{page}",
                    use_container_width=True,
                    on_click=st.session_state.__setitem__,
                    args=("page_navigation", page),
                )
