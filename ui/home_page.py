"""工作台首页：运行概览 + 工作空间入口。"""

import pandas as pd
import streamlit as st

from ui.theme import hero, section, status_bar


def _short(text: str, length: int = 8) -> str:
    return text if len(text) <= length else f"{text[:length]}…"


def render(
    feedback_repository,
    evidence_repository,
    workspace_id: str,
) -> None:
    hero(
        "WorkMesh",
        "企业技术支持工作台 · 多源证据关联 · 智能诊断",
        ["System Online", "Schema v2", "Evidence Driven"],
    )

    analytics = feedback_repository.get_feedback_analytics(workspace_id)
    overview = analytics["overview"]
    evidence_total = evidence_repository.count_evidence(workspace_id)

    status_bar(
        [
            ("工作空间", _short(workspace_id)),
            ("数据库", "SQLite · Schema v2"),
            ("证据类型", "feedback / log / metric / trace / change / note"),
            ("状态", "本地运行"),
        ]
    )

    section("运行概览", "实时读取本地数据库")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("反馈总量", overview["total_feedback"])
    col2.metric("独立用户", overview["unique_users"])
    col3.metric("已接入证据", evidence_total)
    col4.metric("反馈来源产品", overview["unique_products"])

    if analytics["daily_trend"]:
        section("反馈趋势", "按天聚合的反馈量")
        trend_df = pd.DataFrame(analytics["daily_trend"])
        trend_df = trend_df.rename(
            columns={"feedback_date": "日期", "feedback_count": "反馈数"}
        )
        st.bar_chart(trend_df.set_index("日期"), color="#22D3EE", height=220)

    section("工作空间", "按任务阶段进入")
    entries = [
        ("数据接入", "导入 CSV / Excel 反馈，字段自动映射与质量校验。", "数据接入"),
        ("证据关联", "接入日志与指标证据，围绕反馈锚点做多源关联。", "证据关联"),
        ("反馈分析", "版本、平台、错误码分布与高频问题识别。", "反馈分析"),
        ("智能诊断", "基于证据生成诊断结论，每条结论必须引用证据。", "智能诊断"),
    ]

    for row_start in (0, 2):
        columns = st.columns(2)
        for offset, (title, text, page) in enumerate(entries[row_start:row_start + 2]):
            with columns[offset]:
                st.markdown(
                    f"""
                    <div class="wm-card" style="margin-bottom:0.6rem">
                        <p class="wm-card-title">{title}</p>
                        <p class="wm-card-text">{text}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.button(
                    f"进入{title}",
                    key=f"home_go_{page}",
                    use_container_width=True,
                    on_click=st.session_state.__setitem__,
                    args=("page_navigation", page),
                )
