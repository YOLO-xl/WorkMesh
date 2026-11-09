"""问题聚类页：把同类反馈归簇，看影响面，再跳到单条深度诊断。"""

import pandas as pd
import streamlit as st
from html import escape

from database.feedback_repository import FeedbackRepository
from services.cluster_service import (
    ClusterService,
    DEFAULT_MIN_SIZE,
    severity_label,
)
from ui.theme import data_list, empty_state, hero, section, stepper, tag


def _cluster_card(cluster: dict) -> str:
    severity_kind = cluster["severity"]
    evidence = cluster["evidence"]

    evidence_text = (
        f"证据 {evidence['total']} 条"
        if evidence["total"]
        else "暂无证据"
    )

    phenomena = " ".join(
        tag(f"{item['label']} {item['count']}", "low")
        for item in cluster["phenomena"][:4]
    )

    return f"""
    <div class="wm-cluster">
        <div class="wm-cluster-top">
            <p class="wm-cluster-title">{escape(cluster['title'])}</p>
            {tag('严重 ' + severity_label(severity_kind), severity_kind)}
            {tag(f"{cluster['size']} 条反馈", 'medium')}
            {tag(f"{cluster['unique_users']} 位用户", 'low')}
            {tag(evidence_text, 'low' if not evidence['total'] else 'medium')}
        </div>
        <p class="wm-cluster-sub">{escape(cluster['summary'])}</p>
        <p class="wm-cluster-sub">
            时间 {escape(cluster['first_seen'] or '—')} → {escape(cluster['last_seen'] or '—')}
            · 版本 {", ".join(escape(v['name']) for v in cluster['versions'][:3]) or '—'}
            · 平台 {", ".join(escape(p['name']) for p in cluster['platforms'][:3]) or '—'}
        </p>
        <div class="wm-chips" style="margin-top:0.55rem">{phenomena}</div>
    </div>
    """


def render(db_path=None) -> None:
    hero(
        "问题聚类",
        "同类反馈自动归簇：先看清是几个问题、影响了多少人，再决定查哪一条",
        ["Rule Based Clustering", "Impact First", "Drill Down"],
    )

    feedback_repository = FeedbackRepository(db_path)
    workspace_id = feedback_repository.get_default_workspace()

    section("聚类口径", "产品 + 错误码分组，簇内再分现象；不做语义猜测")
    st.caption(
        "同一产品的同一错误码视为同一类问题；错误码缺失时按描述文本相似度归并。"
        "簇只表示「看起来是同一批问题」，不构成根因结论。"
    )

    col1, col2 = st.columns([1, 1])

    with col1:
        min_size = st.slider(
            "最小簇规模",
            min_value=2,
            max_value=6,
            value=DEFAULT_MIN_SIZE,
            step=1,
            key="cluster_min_size",
            help="小于该规模的反馈会进入「孤立反馈」，不单独成簇。",
        )

    with col2:
        with_evidence = st.checkbox(
            "统计簇关联证据",
            value=True,
            key="cluster_with_evidence",
            help="按簇的产品与时间范围统计日志/指标数量，用于判断证据是否充足。",
        )

    data = ClusterService(db_path=db_path).build(
        workspace_id,
        min_size=min_size,
        with_evidence=with_evidence,
    )

    stats = data["stats"]
    clusters = data["clusters"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("反馈总量", stats["total"])
    c2.metric("问题簇", stats["cluster_count"])
    c3.metric("已归簇反馈", stats["clustered"])
    c4.metric("孤立反馈", stats["singleton_count"])

    if not clusters:
        empty_state(
            "没有达到最小规模的问题簇",
            "降低「最小簇规模」，或先导入更多反馈数据。",
        )
        _render_singletons(data["singletons"])
        return

    section("问题簇列表", "按影响面排序，点开哪一张就在哪一张下面展开详情")
    stepper(
        ["聚类定位", "选定簇", "查看成员与现象", "跳到单条诊断"],
        active=0,
    )

    selected_id = st.session_state.get("cluster_selected")

    for cluster in clusters:
        st.markdown(_cluster_card(cluster), unsafe_allow_html=True)

        is_open = cluster["cluster_id"] == selected_id
        button_col, meta_col = st.columns([1, 3])

        with button_col:
            st.button(
                "收起详情" if is_open else "查看簇详情",
                key=f"cluster_open_{cluster['cluster_id']}",
                use_container_width=True,
                on_click=_toggle_cluster,
                args=(cluster["cluster_id"],),
            )

        with meta_col:
            st.caption(
                f"排序分 {cluster['score']} · 时间集中度 {int(cluster['concentration'] * 100)}% "
                f"· 代表反馈 {cluster['representative'].get('external_id') or '—'}"
            )

        # 详情就地展开，避免点了按钮却要滚到页面底部才看到变化
        if is_open:
            _render_detail(cluster, workspace_id, db_path)

    _render_singletons(data["singletons"])


def _render_detail(cluster: dict, workspace_id: str, db_path) -> None:
    section(
        f"簇详情 · {cluster['title']}",
        "成员、现象分组与证据情况",
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("反馈数", cluster["size"])
    c2.metric("涉及用户", cluster["unique_users"])
    c3.metric("关联日志", cluster["evidence"]["log"])
    c4.metric("关联指标", cluster["evidence"]["metric"])

    st.caption(
        f"严重程度 {severity_label(cluster['severity'])} · "
        f"排序分 {cluster['score']} · "
        f"时间范围 {cluster['first_seen'] or '—'} → {cluster['last_seen'] or '—'}"
    )

    section("现象分组", "同一错误码下用户实际感知到的表现")
    rows = [
        [item["label"], f"{item['count']} 条", " / ".join(item["samples"][:2])]
        for item in cluster["phenomena"]
    ]
    data_list(
        ["现象", "条数", "典型描述"],
        rows,
        widths=["0.7fr", "0.5fr", "3fr"],
    )

    section("成员反馈", "按时间倒序")
    rows = [
        [
            (member.get("feedback_time") or "—")[:16],
            member.get("external_id") or "—",
            member.get("uid") or "—",
            f"{member.get('version') or '—'} / {member.get('platform') or '—'}",
            (member.get("description") or "—")[:36],
            member.get("status") or "—",
        ]
        for member in sorted(
            cluster["members"],
            key=lambda item: item.get("feedback_time") or "",
            reverse=True,
        )
    ]
    data_list(
        ["时间", "反馈号", "UID", "版本 / 平台", "描述", "状态"],
        rows,
        widths=["1.2fr", "0.7fr", "0.8fr", "1.1fr", "2.4fr", "0.7fr"],
    )

    representative = cluster["representative"]
    action_col, download_col, note_col = st.columns([1.2, 1.2, 2])

    with action_col:
        st.button(
            "用代表反馈做单条诊断",
            key=f"cluster_diagnose_{cluster['cluster_id']}",
            type="primary",
            use_container_width=True,
            on_click=_goto_diagnosis,
            args=(representative["id"],),
        )

    with download_col:
        export_df = pd.DataFrame(
            [
                {
                    "反馈号": member.get("external_id"),
                    "时间": member.get("feedback_time"),
                    "UID": member.get("uid"),
                    "版本": member.get("version"),
                    "平台": member.get("platform"),
                    "描述": member.get("description"),
                    "状态": member.get("status"),
                }
                for member in cluster["members"]
            ]
        )
        st.download_button(
            "导出簇成员 CSV",
            data=export_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"workmesh_cluster_{cluster['cluster_id']}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with note_col:
        st.caption(
            "簇是「看起来同一批问题」，不是故障结论；"
            "点代表反馈进入单条诊断，才会按标识与时间窗口算证据。"
        )

    if cluster["evidence"]["total"] == 0:
        st.warning(
            "这个簇在时间范围内没有接入日志或指标证据，"
            "结论只能停在「用户反馈层面」。先去「证据关联」接入该服务的日志或指标。"
        )


def _render_singletons(singletons: list[dict]) -> None:
    if not singletons:
        return

    with st.expander(f"孤立反馈（{len(singletons)} 条）", expanded=False):
        rows = [
            [
                (item.get("feedback_time") or "—")[:16],
                item.get("product") or "—",
                item.get("error_code") or "—",
                (item.get("description") or "—")[:40],
            ]
            for item in singletons
        ]
        data_list(
            ["时间", "产品", "错误码", "描述"],
            rows,
            widths=["1.2fr", "0.9fr", "0.7fr", "2.6fr"],
            empty="没有孤立反馈",
        )


def _toggle_cluster(cluster_id: str) -> None:
    """再次点击同一簇时收起详情。"""

    if st.session_state.get("cluster_selected") == cluster_id:
        st.session_state["cluster_selected"] = None
    else:
        st.session_state["cluster_selected"] = cluster_id


def _goto_diagnosis(feedback_id: str) -> None:
    st.session_state["diagnosis_feedback"] = feedback_id
    st.session_state["page_navigation"] = "单条诊断"
