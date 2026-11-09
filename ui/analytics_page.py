"""反馈分析页：反馈全景（现象/功能分布）+ 版本平台分布 + 近期反馈。"""

from collections import Counter

import pandas as pd
import streamlit as st

from services.symptom_lexicon import extract_features, extract_symptoms
from ui.theme import data_list, hero, section


def _distribution_chart(rows: list[dict], name_label: str, height: int = 220) -> None:
    if not rows:
        st.info("暂无数据。")
        return

    df = pd.DataFrame(rows).rename(columns={"name": name_label, "count": "数量"})
    st.bar_chart(df.set_index(name_label), color="#6366F1", height=height)


def _render_symptom_overview(records: list[dict]) -> None:
    """反馈全景：普通用户不带错误码，也能看出「在抱怨什么现象、落在哪个功能」。"""

    symptom_counts: Counter[str] = Counter()
    feature_counts: Counter[str] = Counter()
    tagged = 0

    for record in records:
        symptoms = extract_symptoms(record.get("description"))
        features = extract_features(record.get("description"))
        symptom_counts.update(symptoms)
        feature_counts.update(features)
        if symptoms:
            tagged += 1

    no_code = sum(1 for record in records if not str(record.get("error_code") or "").strip())
    total = len(records) or 1

    section("反馈全景", "先把描述归约成现象与功能，再看分布；比词云更准，也能下钻")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("可识别现象", f"{tagged}/{len(records)}", help="描述里能命中现象词典的反馈数")
    m2.metric("无错误码反馈", no_code)
    m3.metric("无错误码占比", f"{no_code / total * 100:.0f}%")
    m4.metric("提及功能数", len(feature_counts))

    if not records:
        st.info("暂无反馈数据。")
        return

    if not symptom_counts:
        st.info("当前反馈里还没有命中现象词典的描述，可在「问题聚类」的未归类池里补充说法。")
        return

    chart_col, side_col = st.columns([1.4, 1])
    with chart_col:
        st.caption("现象分布（条数）")
        rows = [
            {"现象": label, "条数": count}
            for label, count in symptom_counts.most_common(10)
        ]
        df = pd.DataFrame(rows).set_index("现象")
        st.bar_chart(df, color="#6366F1", height=260)

    with side_col:
        st.caption("功能提及分布（条数）")
        if feature_counts:
            feature_rows = [
                {"功能": name, "条数": count}
                for name, count in feature_counts.most_common(8)
            ]
            st.bar_chart(
                pd.DataFrame(feature_rows).set_index("功能"),
                color="#8B5CF6",
                height=260,
            )
        else:
            st.caption("描述里还没有提到具体功能。")

    options = ["全部现象"] + [label for label, _ in symptom_counts.most_common(10)]
    picked = st.selectbox("按现象下钻", options, key="analytics_symptom_filter")

    if picked == "全部现象":
        return

    matched = [
        record for record in records
        if picked in extract_symptoms(record.get("description"))
    ]
    rows = [
        [
            (record.get("feedback_time") or "—")[:16],
            record.get("external_id") or "—",
            record.get("product") or "—",
            record.get("error_code") or "无",
            (record.get("description") or "—")[:40],
        ]
        for record in matched[:15]
    ]
    st.caption(f"「{picked}」命中 {len(matched)} 条，展示最近 {min(len(matched), 15)} 条。")
    data_list(
        ["时间", "反馈号", "产品", "错误码", "描述"],
        rows,
        widths=["1.2fr", "0.7fr", "0.9fr", "0.6fr", "2.8fr"],
        empty="该现象下没有反馈",
    )


def render(feedback_repository, workspace_id: str) -> None:
    hero(
        "反馈分析",
        "按版本、平台、错误码拆解反馈分布，定位高频问题面",
        ["Distribution", "Error Codes", "Trend"],
    )

    analytics = feedback_repository.get_feedback_analytics(workspace_id)
    overview = analytics["overview"]

    all_records = feedback_repository.list_feedback(
        workspace_id, page_size=500
    )["records"]

    section("总览", "工作空间内的反馈规模")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("反馈总量", overview["total_feedback"])
    col2.metric("独立用户", overview["unique_users"])
    col3.metric("涉及产品", overview["unique_products"])
    col4.metric(
        "版本数",
        len(analytics["version_distribution"]),
    )

    _render_symptom_overview(all_records)

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
