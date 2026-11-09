"""工作台首页：顶栏之下分三层——欢迎区、核心模块、工作动态与系统状态。

模块信息集中在 MODULES 里配置，新增模块只需要加一条配置；
卡片整块可点击（一个透明按钮覆盖整张卡片）。
"""

from datetime import datetime

import streamlit as st

from services.cluster_service import ClusterService
from services.diagnosis_service import DiagnosisService
from ui.theme import (
    empty_state,
    group_label,
    icon,
    panel,
    section,
    tag,
)


MODULES = [
    {
        "key": "ingest",
        "name": "数据接入",
        "icon": "database",
        "group": "core",
        "desc": "导入反馈数据，自动识别字段并做质量校验。",
        "tags": ["CSV / Excel", "字段映射", "质量校验"],
        "page": "数据接入",
    },
    {
        "key": "evidence",
        "name": "证据关联",
        "icon": "link",
        "group": "core",
        "desc": "接入日志与指标，围绕反馈计算关联强度。",
        "tags": ["日志", "指标", "关联打分"],
        "page": "证据关联",
    },
    {
        "key": "cluster",
        "name": "问题聚类",
        "icon": "cluster",
        "group": "core",
        "desc": "同类反馈自动成簇，按影响面决定先查哪条。",
        "tags": ["自动成簇", "影响面排序", "现象标签"],
        "page": "问题聚类",
    },
    {
        "key": "diagnosis",
        "name": "单条诊断",
        "icon": "stethoscope",
        "group": "core",
        "desc": "针对一条反馈生成诊断，事实与推断分开。",
        "tags": ["事实 / 推断", "引用校验", "Markdown 报告"],
        "page": "单条诊断",
    },
    {
        "key": "analytics",
        "name": "反馈分析",
        "icon": "chart",
        "group": "support",
        "desc": "看版本、平台与错误码的分布和趋势。",
        "tags": ["版本分布", "平台占比", "趋势"],
        "page": "反馈分析",
    },
    {
        "key": "reports",
        "name": "报告中心",
        "icon": "document",
        "group": "support",
        "desc": "沉淀诊断报告，按问题维度回看结论。",
        "tags": ["报告沉淀", "按问题回看"],
        "page": "报告中心",
    },
]


def _open_page(page: str) -> None:
    st.session_state.page_navigation = page
    st.session_state.wm_view = "module"
    st.session_state.wm_deeplink = page
    st.query_params["page"] = page
    st.query_params.pop("view", None)
    remember_visit(page)


def remember_visit(page: str) -> None:
    visited = st.session_state.setdefault("wm_recent", [])
    stamp = datetime.now().strftime("%H:%M")

    visited = [item for item in visited if item[0] != page]
    visited.insert(0, (page, stamp))
    st.session_state.wm_recent = visited[:5]


def _fmt_time(value) -> str:
    """面板里展示的时间统一为 YYYY-MM-DD HH:MM。"""

    return str(value).replace("T", " ").replace("Z", "")[:16]


def _greeting() -> str:
    hour = datetime.now().hour

    if hour < 6:
        return "夜深了"
    if hour < 12:
        return "上午好"
    if hour < 18:
        return "下午好"
    return "晚上好"


def _card_html(module: dict, status: str) -> str:
    tags_html = "".join(tag(item) for item in module["tags"])

    return f"""
    <div class="wm-module-icon">{icon(module["icon"], 18)}</div>
    <p class="wm-module-name">{module["name"]}</p>
    <p class="wm-module-desc">{module["desc"]}</p>
    <div class="wm-module-tags">{tags_html}</div>
    <div class="wm-module-status">{status}</div>
    <div class="wm-module-cta">进入模块 {icon("arrow", 14)}</div>
    """


def _render_welcome(
    overview: dict,
    cluster_count: int,
    evidence_total: int,
) -> None:
    now = datetime.now()
    weekday = "一二三四五六日"[now.weekday()]
    greeting = _greeting()

    with st.container(key="wm_welcome"):
        st.markdown(
            f"""
            <p class="wm-greeting">{greeting}，欢迎使用<span>智能工作台</span></p>
            <p class="wm-welcome-line">
                把一条用户反馈，变成一份能回溯证据的排查结论：
                先聚类看影响面，再对单条反馈做证据驱动诊断。
            </p>
            <div class="wm-welcome-meta">
                <span>{now.year}年{now.month}月{now.day}日 星期{weekday} · {now.strftime('%H:%M')}</span>
                <span>反馈 {overview['total_feedback']} · 证据 {evidence_total} · 问题簇 {cluster_count}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.write("")

        action_cols = st.columns([1, 1, 1, 1, 2.2])

        with action_cols[0]:
            st.button(
                "导入反馈",
                key="home_quick_ingest",
                type="primary",
                use_container_width=True,
                on_click=_open_page,
                args=("数据接入",),
            )

        with action_cols[1]:
            st.button(
                "看问题聚类",
                key="home_quick_cluster",
                use_container_width=True,
                on_click=_open_page,
                args=("问题聚类",),
            )

        with action_cols[2]:
            st.button(
                "诊断一条反馈",
                key="home_quick_diagnosis",
                use_container_width=True,
                on_click=_open_page,
                args=("单条诊断",),
            )

        with action_cols[3]:
            st.button(
                "查证据",
                key="home_quick_evidence",
                use_container_width=True,
                on_click=_open_page,
                args=("证据关联",),
            )


def _status_map(
    overview: dict,
    evidence_total: int,
    cluster_stats: dict,
    llm_ready: bool,
) -> dict:
    return {
        "ingest": f"已接入 <b>{overview['total_feedback']}</b> 条反馈 · {overview['unique_products']} 个产品",
        "evidence": f"证据库 <b>{evidence_total}</b> 条 · 3 类来源",
        "cluster": f"<b>{cluster_stats['cluster_count']}</b> 个问题簇 · 覆盖 {cluster_stats['coverage'] * 100:.0f}%",
        "diagnosis": "规则诊断可用" + ("" if llm_ready else " · LLM 未配置"),
        "analytics": f"<b>{overview['unique_users']}</b> 位用户 · {overview['unique_products']} 个产品",
        "reports": "规划中 · 暂无报告",
    }


def _render_module(module: dict, status: str) -> None:
    with st.container(key=f"modcard-{module['key']}"):
        st.markdown(_card_html(module, status), unsafe_allow_html=True)
        st.button(
            f"进入{module['name']}",
            key=f"open_{module['key']}",
            on_click=_open_page,
            args=(module["page"],),
        )


def _render_modules(statuses: dict, query: str) -> None:
    matches = [
        module
        for module in MODULES
        if not query
        or query.lower() in module["name"].lower()
        or query.lower() in module["desc"].lower()
        or any(query.lower() in item.lower() for item in module["tags"])
    ]

    section("核心模块", "按业务重要性分组，点击卡片进入")

    if query:
        st.caption(f"搜索「{query}」· 命中 {len(matches)} / {len(MODULES)} 个模块")

    if not matches:
        empty_state(
            "没有匹配的模块",
            "试试模块名（如「聚类」）、功能标签（如「指标」）或错误码（如「E1001」）。",
        )
        return

    core = [module for module in matches if module["group"] == "core"]
    support = [module for module in matches if module["group"] == "support"]

    if core:
        group_label("核心排查链路", "从反馈到结论" if not query else "")
        columns = st.columns(4)

        for index, module in enumerate(core):
            with columns[index % 4]:
                _render_module(module, statuses[module["key"]])

    if support:
        group_label("分析与沉淀", "看分布、留结论")
        columns = st.columns(4)

        for index, module in enumerate(support):
            with columns[index % 4]:
                _render_module(module, statuses[module["key"]])


def _render_workspace(
    overview: dict,
    records: list[dict],
    evidence_total: int,
    clusters: dict,
    llm_ready: bool,
) -> None:
    section("工作动态", "帮助你快速回到上次的工作上下文")

    left, middle, right = st.columns([1.15, 1.15, 1.1])

    with left:
        recent = st.session_state.get("wm_recent", [])

        if recent:
            panel("最近访问", [(page, stamp) for page, stamp in recent], "本次会话")
        else:
            panel("最近访问", [("还没有访问记录", "进入模块后自动记录")], "本次会话")

    with middle:
        pending = []

        without_evidence = sum(
            1
            for cluster in clusters["clusters"]
            if cluster["evidence"]["total"] == 0
        )

        if without_evidence:
            pending.append((f"{without_evidence} 个问题簇还没有关联证据", "去补证据"))
        if clusters["stats"]["singleton_count"]:
            pending.append(
                (f"{clusters['stats']['singleton_count']} 条孤立反馈未成簇", "去看聚类")
            )
        if not llm_ready:
            pending.append(("LLM 未配置，诊断只走规则", "补配置"))

        if not pending:
            pending.append(("没有待处理事项", "保持关注"))

        panel("待处理事项", pending, f"共 {len(pending)} 项")

    with right:
        panel(
            "运行状态",
            [
                ("数据库", "正常"),
                ("反馈 / 证据", f"{overview['total_feedback']} / {evidence_total}"),
                ("问题簇", str(clusters["stats"]["cluster_count"])),
                ("LLM", "未配置" if not llm_ready else "已配置"),
            ],
            "本地 SQLite",
        )

    activity_col, tools_col = st.columns([2.3, 1.15])

    with activity_col:
        rows = []

        for record in records[:4]:
            label = (
                f"{record.get('external_id') or '—'} · "
                f"{record.get('product') or '未分类'} · "
                f"{record.get('error_code') or '无错误码'}"
            )
            rows.append((label, _fmt_time(record.get("feedback_time"))))

        panel("最新进入系统的反馈", rows or [("暂无反馈", "导入后出现在这里")], "按时间倒序")

    with tools_col:
        panel("常用工具", [("刷新首页数据", "重新读取"), ("页面深链接", "?page=问题聚类")], "快捷方式")

        st.button("刷新数据", key="home_refresh", use_container_width=True, on_click=st.rerun)


def render(feedback_repository, evidence_repository, workspace_id: str) -> None:
    analytics = feedback_repository.get_feedback_analytics(workspace_id)
    overview = analytics["overview"]
    records = feedback_repository.list_feedback(
        workspace_id, page=1, page_size=6
    )["records"]

    evidence_total = evidence_repository.count_evidence(workspace_id)

    clusters = ClusterService(
        feedback_repository=feedback_repository,
        evidence_repository=evidence_repository,
    ).build(workspace_id, with_evidence=True)

    llm_ready = DiagnosisService().llm_available
    query = (st.session_state.get("wm_search") or "").strip()

    statuses = _status_map(
        overview, evidence_total, clusters["stats"], llm_ready
    )

    _render_welcome(overview, clusters["stats"]["cluster_count"], evidence_total)

    if query:
        matched_records = [
            record
            for record in records
            if query.lower() in str(record.get("external_id") or "").lower()
            or query.lower() in str(record.get("error_code") or "").lower()
        ]

        if matched_records:
            record = matched_records[0]
            st.markdown(
                f"找到反馈 `{record.get('external_id')}`（"
                f"{record.get('product')} · {record.get('error_code') or '无错误码'}），"
                "可以直接对它做单条诊断。"
            )
            st.button(
                "用这条反馈做诊断",
                key="home_search_diagnosis",
                on_click=_open_page,
                args=("单条诊断",),
            )

    _render_modules(statuses, query)
    _render_workspace(overview, records, evidence_total, clusters, llm_ready)
