"""顶部导航栏：品牌 / 当前页面 / 全局搜索 / 通知 / 帮助 / 个人入口。

所有入口都具备真实行为：搜索会过滤首页模块，通知读最近反馈，
帮助给使用说明，个人入口给工作空间与运行信息。
"""

import streamlit as st

from ui.theme import empty_state, icon


VERSION = "v0.7.0-dev"


def _brand_html() -> str:
    return f"""
    <div class="wm-brand">
        <span class="wm-brand-mark" style="color:#FFFFFF">{icon("spark", 16)}</span>
        <div>
            <div class="wm-brand-name">WorkMesh</div>
            <div class="wm-brand-sub">AI Workspace</div>
        </div>
    </div>
    """


def _crumb_html(current_label: str) -> str:
    return (
        '<div class="wm-page-crumb">工作台'
        f'<span style="color:#475569">/</span><b>{current_label}</b></div>'
    )


def render(
    current_label: str,
    recent_feedback: list[dict],
    workspace_id: str,
) -> None:
    with st.container(key="wm_topbar"):
        brand_col, crumb_col, search_col, bell_col, help_col, user_col = st.columns(
            [1.9, 1.6, 3.2, 1.0, 1.0, 1.05],
            vertical_alignment="center",
        )

        with brand_col:
            st.markdown(_brand_html(), unsafe_allow_html=True)

        with crumb_col:
            st.markdown(_crumb_html(current_label), unsafe_allow_html=True)

        with search_col:
            st.text_input(
                "全局搜索",
                key="wm_search",
                placeholder="搜索模块、标签或反馈编号（如 E1001 / F001）",
                label_visibility="collapsed",
            )

        with bell_col:
            with st.popover("通知", use_container_width=True):
                st.markdown("**最近系统动态**")

                if recent_feedback:
                    for record in recent_feedback[:4]:
                        st.markdown(
                            f"- `{record.get('external_id') or '—'}` "
                            f"{record.get('product') or '未分类'} · "
                            f"{record.get('error_code') or '无错误码'}"
                            f"（{str(record.get('feedback_time'))[:16]}）"
                        )
                else:
                    empty_state("暂无动态", "导入反馈后，这里会显示最新进入系统的反馈。")

                st.caption("通知仅汇总本工作空间内的数据变化，不做推送。")

        with help_col:
            with st.popover("帮助", use_container_width=True):
                st.markdown(
                    """
                    **怎么用**

                    1. 首页卡片按业务分组，点卡片任意位置即可进入模块。
                    2. 顶部搜索框输入模块名、标签或错误码，可快速定位。
                    3. 想直接分享页面？用深链接：
                       `?page=问题聚类`（进模块）、`?view=home`（回首页）。

                    **口径文档**
                    - [证据模型](https://github.com/YOLO-xl/WorkMesh/blob/main/docs/EVIDENCE_MODEL.md)
                    - [路线图](https://github.com/YOLO-xl/WorkMesh/blob/main/docs/ROADMAP.md)
                    """
                )

        with user_col:
            with st.popover("YOLO", use_container_width=True):
                st.markdown("**当前工作空间**")
                st.code(workspace_id, language=None)
                st.caption(f"版本 {VERSION} · 本地运行 · SQLite 存储")
                st.divider()
                st.caption("尚未配置 LLM，单条诊断目前只走规则路径。")
