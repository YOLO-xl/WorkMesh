"""反馈分析页：分布图 + 近期反馈。"""

import pandas as pd
import streamlit as st

from ui.theme import data_list, hero, section


def _distribution_chart(rows: list[dict], name_label: str, height: int = 220) -> None:
    if not rows:
        st.info("暂无数据。")
        return

    df = pd.DataFrame(rows).rename(columns={"name": name_label, "count": "数量"})
    st.bar_chart(df.set_index(name_label), color="#6366F1", height=height)


def render(feedback_repository, workspace_id: str) -> None:
    hero(
        "反馈分析",
        "按版本、平台、错误码拆解反馈分布，定位高频问题面",
        ["Distribution", "Error Codes", "Trend"],
    )

    analytics = feedback_repository.get_feedback_analytics(workspace_id)
    overview = analytics["overview"]

    section("总览", "工作空间内的反馈规模")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("反馈总量", overview["total_feedback"])
    col2.metric("独立用户", overview["unique_users"])
    col3.metric("涉及产品", overview["unique_products"])
    col4.metric(
        "版本数",
        len(analytics["version_distribution"]),
    )

    section("错误码分布", "按出现次数排序，Top 10")
    _distribution_chart(analytics["error_code_distribution"], "错误码")

    col_left, col_right = st.columns(2)
    with col_left:
        section("版本分布", "Top 10")
        _distribution_chart(analytics["version_distribution"], "版本", height=200)
    with col_right:
        section("平台分布", "Top 10")
        _distribution_chart(analytics["platform_distribution"], "平台", height=200)

    section("最新反馈", "按创建时间倒序")
    result = feedback_repository.list_feedback(workspace_id, page_size=20)
    records = result["records"]

    if not records:
        st.info("暂无反馈记录，可先到「数据接入」导入。")
        return

    rows = [
        [
            (record.get("feedback_time") or "—")[:16],
            record.get("external_id") or "—",
            record.get("product") or "—",
            record.get("error_code") or "—",
            f"{record.get('version') or '—'} / {record.get('platform') or '—'}",
            (record.get("description") or "—")[:34],
        ]
        for record in records
    ]
    data_list(
        ["时间", "反馈号", "产品", "错误码", "版本 / 平台", "描述"],
        rows,
        widths=["1.2fr", "0.7fr", "0.9fr", "0.7fr", "1.1fr", "2.4fr"],
    )
    st.caption(f"共 {result['total']} 条反馈，当前展示最近 {len(records)} 条。")
