"""WorkMesh 深色科技风视觉系统。

所有页面共用一套设计令牌和组件：
- 深空底色 + 网格 + 青色光晕
- 玻璃面板、发光指标卡、渐变标题
- 侧边栏导航与侧边状态区

页面只调用 apply_theme() / hero() / section() 等函数，不各自写样式。
"""

import streamlit as st
from html import escape

ACCENT = "#22D3EE"
ACCENT_ALT = "#6366F1"

_CSS = """
<style>
:root {
    --wm-bg: #070B14;
    --wm-panel: rgba(15, 23, 42, 0.72);
    --wm-border: rgba(34, 211, 238, 0.16);
    --wm-border-strong: rgba(34, 211, 238, 0.45);
    --wm-accent: #22D3EE;
    --wm-accent-alt: #6366F1;
    --wm-text: #E2E8F0;
    --wm-muted: #94A3B8;
    --wm-glow: rgba(34, 211, 238, 0.16);
}

/* ---------- 全局底色 ---------- */
.stApp {
    background:
        radial-gradient(circle at 12% -10%, rgba(34, 211, 238, 0.14), transparent 42%),
        radial-gradient(circle at 88% 8%, rgba(99, 102, 241, 0.16), transparent 45%),
        radial-gradient(circle at 50% 120%, rgba(14, 116, 144, 0.16), transparent 55%),
        var(--wm-bg);
    color: var(--wm-text);
}

/* 细网格叠加，模拟控制台背景 */
.stApp::before {
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    background-image:
        linear-gradient(rgba(34, 211, 238, 0.045) 1px, transparent 1px),
        linear-gradient(90deg, rgba(34, 211, 238, 0.045) 1px, transparent 1px);
    background-size: 34px 34px;
    mask-image: radial-gradient(circle at 50% 30%, rgba(0, 0, 0, 0.9), transparent 82%);
    z-index: 0;
}

.block-container {
    padding-top: 2.2rem;
    padding-bottom: 3rem;
    max-width: 1240px;
}

/* ---------- 字体与标题 ---------- */
h1, h2, h3, h4 {
    letter-spacing: 0.02em;
    color: var(--wm-text);
}

h2, h3 {
    font-weight: 600;
}

[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li {
    color: #CBD5E1;
}

[data-testid="stMarkdownContainer"] a {
    color: var(--wm-accent);
}

/* ---------- 侧边栏 ---------- */
[data-testid="stSidebar"] {
    background:
        linear-gradient(180deg, rgba(10, 17, 32, 0.96), rgba(7, 11, 20, 0.96));
    border-right: 1px solid var(--wm-border);
}

[data-testid="stSidebar"] > div:first-child {
    padding-top: 1.1rem;
}

[data-testid="stSidebar"] [role="radiogroup"] > div {
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 0.15rem 0.35rem;
    transition: all 0.18s ease;
}

[data-testid="stSidebar"] [role="radiogroup"] > div:hover {
    border-color: var(--wm-border);
    background: rgba(34, 211, 238, 0.06);
}

[data-testid="stSidebar"] [role="radiogroup"] label {
    color: #CBD5E1;
    font-size: 0.92rem;
}

/* ---------- 指标卡 ---------- */
div[data-testid="stMetric"] {
    background: linear-gradient(160deg, rgba(14, 24, 42, 0.92), rgba(11, 18, 32, 0.72));
    border: 1px solid var(--wm-border);
    border-radius: 14px;
    padding: 1rem 1.15rem 0.95rem;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.04),
        0 0 0 rgba(34, 211, 238, 0);
    transition: all 0.2s ease;
    position: relative;
    overflow: hidden;
}

div[data-testid="stMetric"]::after {
    content: "";
    position: absolute;
    left: 0;
    top: 0;
    width: 3px;
    height: 100%;
    background: linear-gradient(180deg, var(--wm-accent), rgba(34, 211, 238, 0));
    opacity: 0.7;
}

div[data-testid="stMetric"]:hover {
    border-color: var(--wm-border-strong);
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.05),
        0 0 26px var(--wm-glow);
    transform: translateY(-2px);
}

div[data-testid="stMetric"] label,
div[data-testid="stMetricLabel"] {
    color: var(--wm-muted) !important;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    font-size: 0.7rem !important;
}

div[data-testid="stMetricValue"] {
    color: #F1F5F9;
    font-variant-numeric: tabular-nums;
    font-weight: 600;
    letter-spacing: 0.01em;
}

/* ---------- 按钮 ---------- */
.stButton > button {
    border-radius: 10px;
    border: 1px solid rgba(34, 211, 238, 0.35);
    background: linear-gradient(135deg, rgba(8, 145, 178, 0.22), rgba(37, 99, 235, 0.22));
    color: #E2E8F0;
    font-weight: 600;
    letter-spacing: 0.03em;
    transition: all 0.18s ease;
}

.stButton > button:hover {
    border-color: var(--wm-accent);
    background: linear-gradient(135deg, rgba(34, 211, 238, 0.32), rgba(99, 102, 241, 0.32));
    box-shadow: 0 0 22px var(--wm-glow);
    color: #FFFFFF;
}

.stButton > button:focus:not(:active) {
    border-color: var(--wm-accent);
    box-shadow: 0 0 0 2px rgba(34, 211, 238, 0.2);
}

.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #0891B2, #2563EB);
    border-color: rgba(34, 211, 238, 0.6);
}

.stButton > button:disabled {
    border-color: rgba(148, 163, 184, 0.2);
    background: rgba(30, 41, 59, 0.5);
    color: #64748B;
    box-shadow: none;
}

/* ---------- 页签 ---------- */
[data-baseweb="tab-list"] {
    gap: 0.4rem;
    background: transparent;
    border-bottom: 1px solid var(--wm-border);
    padding-bottom: 0.35rem;
}

[data-baseweb="tab"] {
    border-radius: 8px;
    background: rgba(15, 23, 42, 0.6);
    border: 1px solid transparent;
    color: var(--wm-muted);
    padding: 0.3rem 1rem;
}

[data-baseweb="tab"][aria-selected="true"] {
    color: var(--wm-accent);
    border-color: var(--wm-border-strong);
    background: rgba(34, 211, 238, 0.08);
}

/* ---------- 输入与容器 ---------- */
div[data-baseweb="input"],
div[data-baseweb="select"] > div,
div[data-baseweb="textarea"] {
    background: rgba(15, 23, 42, 0.75) !important;
    border: 1px solid var(--wm-border) !important;
    border-radius: 10px !important;
    color: var(--wm-text) !important;
}

div[data-baseweb="input"]:focus-within,
div[data-baseweb="select"]:focus-within {
    border-color: var(--wm-border-strong) !important;
    box-shadow: 0 0 0 2px rgba(34, 211, 238, 0.12) !important;
}

[data-testid="stFileUploader"] {
    background: rgba(15, 23, 42, 0.6);
    border: 1px dashed rgba(34, 211, 238, 0.3);
    border-radius: 12px;
    padding: 0.6rem;
}

[data-testid="stExpander"] {
    border: 1px solid var(--wm-border);
    border-radius: 12px;
    background: rgba(15, 23, 42, 0.5);
}

[data-testid="stDataFrame"] {
    border: 1px solid var(--wm-border);
    border-radius: 12px;
    overflow: hidden;
}

[data-testid="stAlert"] {
    border-radius: 10px;
    border: 1px solid rgba(34, 211, 238, 0.18);
    background: rgba(15, 23, 42, 0.65);
}

hr {
    border-color: rgba(34, 211, 238, 0.12);
}

/* ---------- 滚动条 ---------- */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}

::-webkit-scrollbar-track {
    background: rgba(7, 11, 20, 0.6);
}

::-webkit-scrollbar-thumb {
    background: rgba(34, 211, 238, 0.28);
    border-radius: 8px;
}

::-webkit-scrollbar-thumb:hover {
    background: rgba(34, 211, 238, 0.45);
}

/* ==================== 自定义组件 ==================== */
.wm-hero {
    position: relative;
    padding: 1.6rem 1.8rem;
    border-radius: 18px;
    border: 1px solid var(--wm-border);
    background:
        linear-gradient(140deg, rgba(8, 145, 178, 0.14), rgba(15, 23, 42, 0.6) 45%, rgba(99, 102, 241, 0.12));
    box-shadow: 0 0 32px rgba(34, 211, 238, 0.06);
    overflow: hidden;
    margin-bottom: 0.6rem;
}

.wm-hero::after {
    content: "";
    position: absolute;
    right: -60px;
    top: -80px;
    width: 220px;
    height: 220px;
    background: radial-gradient(circle, rgba(34, 211, 238, 0.18), transparent 70%);
    pointer-events: none;
}

.wm-hero-title {
    margin: 0;
    font-size: 2rem;
    font-weight: 700;
    letter-spacing: 0.03em;
    background: linear-gradient(92deg, #F8FAFC 0%, #22D3EE 52%, #818CF8 100%);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    line-height: 1.25;
}

.wm-hero-sub {
    margin: 0.45rem 0 0;
    color: var(--wm-muted);
    font-size: 0.92rem;
    letter-spacing: 0.02em;
}

.wm-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 1rem;
}

.wm-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.28rem 0.7rem;
    border-radius: 999px;
    border: 1px solid rgba(34, 211, 238, 0.28);
    background: rgba(34, 211, 238, 0.07);
    color: #A5F3FC;
    font-size: 0.72rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}

.wm-chip-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--wm-accent);
    box-shadow: 0 0 8px var(--wm-accent);
}

.wm-section {
    display: flex;
    align-items: baseline;
    gap: 0.7rem;
    margin: 1.6rem 0 0.5rem;
    padding-left: 0.75rem;
    border-left: 3px solid var(--wm-accent);
}

.wm-section-title {
    font-size: 1.02rem;
    font-weight: 600;
    color: #E2E8F0;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin: 0;
}

.wm-section-desc {
    color: var(--wm-muted);
    font-size: 0.82rem;
    margin: 0;
}

.wm-card {
    border: 1px solid var(--wm-border);
    border-radius: 14px;
    padding: 1.15rem 1.3rem;
    background: linear-gradient(160deg, rgba(15, 23, 42, 0.85), rgba(9, 14, 26, 0.7));
    transition: all 0.2s ease;
    height: 100%;
}

.wm-card:hover {
    border-color: var(--wm-border-strong);
    box-shadow: 0 0 24px var(--wm-glow);
}

.wm-card-title {
    color: #F1F5F9;
    font-size: 1rem;
    font-weight: 600;
    margin: 0 0 0.35rem;
}

.wm-card-text {
    color: var(--wm-muted);
    font-size: 0.84rem;
    line-height: 1.6;
    margin: 0;
}

.wm-stat {
    font-variant-numeric: tabular-nums;
    color: var(--wm-accent);
    font-weight: 700;
    font-size: 1.35rem;
    letter-spacing: 0.02em;
}

.wm-mono {
    font-family: "SFMono-Regular", "JetBrains Mono", Menlo, Consolas, monospace;
    font-size: 0.8rem;
    color: #A5F3FC;
}

.wm-status-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 1.6rem;
    padding: 0.85rem 1.2rem;
    border-radius: 12px;
    border: 1px solid var(--wm-border);
    background: rgba(10, 17, 32, 0.7);
    font-size: 0.8rem;
    color: var(--wm-muted);
    margin: 0.6rem 0;
}

.wm-status-bar span b {
    color: #E2E8F0;
    font-weight: 600;
    letter-spacing: 0.04em;
}

/* ---------- 流程步骤条 ---------- */
.wm-steps {
    display: flex;
    flex-wrap: wrap;
    gap: 0.55rem;
    margin: 0.7rem 0 0.2rem;
}

.wm-step {
    flex: 1 1 170px;
    padding: 0.7rem 0.85rem;
    border-radius: 12px;
    border: 1px solid rgba(34, 211, 238, 0.12);
    background: rgba(13, 21, 36, 0.6);
    font-size: 0.8rem;
    color: #64748B;
    transition: all 0.18s ease;
}

.wm-step-active {
    border-color: var(--wm-border-strong);
    background: linear-gradient(140deg, rgba(8, 145, 178, 0.18), rgba(15, 23, 42, 0.7));
    color: #E2E8F0;
    box-shadow: 0 0 20px rgba(34, 211, 238, 0.1);
}

.wm-step-index {
    color: var(--wm-accent);
    font-weight: 700;
    margin-right: 0.35rem;
    font-variant-numeric: tabular-nums;
}

/* ---------- 深色列表（替代默认表格） ---------- */
.wm-list {
    border: 1px solid var(--wm-border);
    border-radius: 12px;
    overflow: hidden;
    background: rgba(13, 21, 36, 0.55);
}

.wm-list-head {
    display: grid;
    gap: 0.9rem;
    padding: 0.55rem 1rem;
    font-size: 0.68rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #64748B;
    background: rgba(10, 17, 32, 0.7);
    border-bottom: 1px solid rgba(34, 211, 238, 0.1);
}

.wm-list-row {
    display: grid;
    gap: 0.9rem;
    padding: 0.68rem 1rem;
    font-size: 0.84rem;
    color: #CBD5E1;
    border-bottom: 1px solid rgba(34, 211, 238, 0.06);
    transition: background 0.15s ease;
    align-items: center;
}

.wm-list-row:hover {
    background: rgba(34, 211, 238, 0.05);
}

.wm-list-row:last-child {
    border-bottom: none;
}

.wm-list-row .wm-strong {
    color: #F1F5F9;
    font-weight: 600;
}

.wm-list-row .wm-dim {
    color: #64748B;
    font-size: 0.78rem;
}

.wm-list-empty {
    padding: 1.6rem 1rem;
    text-align: center;
    color: #64748B;
    font-size: 0.84rem;
}

/* ---------- 标签 ---------- */
.wm-tag {
    display: inline-block;
    padding: 0.12rem 0.55rem;
    border-radius: 999px;
    font-size: 0.72rem;
    letter-spacing: 0.05em;
    border: 1px solid rgba(34, 211, 238, 0.28);
    background: rgba(34, 211, 238, 0.08);
    color: #A5F3FC;
    white-space: nowrap;
}

.wm-tag-high {
    border-color: rgba(251, 146, 60, 0.45);
    background: rgba(251, 146, 60, 0.1);
    color: #FDBA74;
}

.wm-tag-medium {
    border-color: rgba(34, 211, 238, 0.4);
    background: rgba(34, 211, 238, 0.1);
    color: #67E8F9;
}

.wm-tag-low {
    border-color: rgba(148, 163, 184, 0.28);
    background: rgba(148, 163, 184, 0.08);
    color: #94A3B8;
}

/* ---------- 空状态 ---------- */
.wm-empty {
    border: 1px dashed rgba(34, 211, 238, 0.25);
    border-radius: 14px;
    padding: 1.8rem 1.2rem;
    text-align: center;
    background: rgba(13, 21, 36, 0.45);
    margin: 0.4rem 0;
}

.wm-empty-title {
    color: #E2E8F0;
    font-size: 0.95rem;
    margin: 0 0 0.3rem;
}

.wm-empty-text {
    color: #64748B;
    font-size: 0.82rem;
    margin: 0;
}

/* ---------- 簇卡片 ---------- */
.wm-cluster {
    border: 1px solid var(--wm-border);
    border-left: 3px solid var(--wm-accent);
    border-radius: 12px;
    padding: 0.9rem 1.1rem;
    background: linear-gradient(150deg, rgba(15, 23, 42, 0.85), rgba(9, 14, 26, 0.62));
    transition: all 0.18s ease;
    margin-bottom: 0.55rem;
}

.wm-cluster:hover {
    border-color: var(--wm-border-strong);
    box-shadow: 0 0 22px rgba(34, 211, 238, 0.1);
}

.wm-cluster-top {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    flex-wrap: wrap;
    margin-bottom: 0.3rem;
}

.wm-cluster-title {
    color: #F1F5F9;
    font-size: 0.98rem;
    font-weight: 600;
    margin: 0;
}

.wm-cluster-sub {
    color: #94A3B8;
    font-size: 0.8rem;
    margin: 0.15rem 0 0;
    line-height: 1.55;
}
</style>
"""


def apply_theme() -> None:
    """注入全局样式；同一会话只注入一次。"""

    if st.session_state.get("wm_theme_applied"):
        return

    st.markdown(_CSS, unsafe_allow_html=True)
    st.session_state["wm_theme_applied"] = True


def hero(
    title: str,
    subtitle: str = "",
    chips: list[str] | None = None,
) -> None:
    """页面顶部主视觉：渐变标题 + 副标题 + 状态胶囊。"""

    chip_html = ""

    if chips:
        chip_html = (
            '<div class="wm-chips">'
            + "".join(
                f'<span class="wm-chip">'
                f'<span class="wm-chip-dot"></span>{chip}</span>'
                for chip in chips
            )
            + "</div>"
        )

    st.markdown(
        f"""
        <div class="wm-hero">
            <p class="wm-hero-title">{title}</p>
            <p class="wm-hero-sub">{subtitle}</p>
            {chip_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def section(title: str, description: str = "") -> None:
    """带青色竖线的区块标题。"""

    st.markdown(
        f"""
        <div class="wm-section">
            <p class="wm-section-title">{title}</p>
            <p class="wm-section-desc">{description}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def card(title: str, text: str) -> None:
    """静态信息卡（不会换行成 widget）。"""

    st.markdown(
        f"""
        <div class="wm-card">
            <p class="wm-card-title">{title}</p>
            <p class="wm-card-text">{text}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_bar(items: list[tuple[str, str]]) -> None:
    """一行状态条：左侧字段名，右侧值。"""

    body = "".join(
        f"<span>{label} <b>{value}</b></span>"
        for label, value in items
    )

    st.markdown(
        f'<div class="wm-status-bar">{body}</div>',
        unsafe_allow_html=True,
    )


def stepper(steps: list[str], active: int = -1) -> None:
    """流程步骤条；active 为当前步骤下标，-1 表示只展示不强调。"""

    blocks = []

    for index, step in enumerate(steps, start=1):
        active_class = " wm-step-active" if index - 1 == active else ""
        blocks.append(
            f'<div class="wm-step{active_class}">'
            f'<span class="wm-step-index">{index:02d}</span>{step}</div>'
        )

    st.markdown(
        '<div class="wm-steps">' + "".join(blocks) + "</div>",
        unsafe_allow_html=True,
    )


def data_list(
    headers: list[str],
    rows: list[list[str]],
    widths: list[str] | None = None,
    empty: str = "暂无数据",
) -> None:
    """深色列表，替代默认 dataframe 的灰底方块。

    rows 里的内容一律按纯文本转义；需要标签时先用 tag() 生成。
    """

    template = " ".join(widths or ["1fr"] * len(headers))

    if not rows:
        st.markdown(
            f'<div class="wm-list"><div class="wm-list-empty">{escape(empty)}</div></div>',
            unsafe_allow_html=True,
        )
        return

    head = "".join(f"<div>{escape(str(item))}</div>" for item in headers)

    body = "".join(
        '<div class="wm-list-row" style="grid-template-columns:'
        + template
        + '">'
        + "".join(f"<div>{escape(str(cell))}</div>" for cell in row)
        + "</div>"
        for row in rows
    )

    st.markdown(
        f'<div class="wm-list">'
        f'<div class="wm-list-head" style="grid-template-columns:{template}">{head}</div>'
        f"{body}</div>",
        unsafe_allow_html=True,
    )


def empty_state(title: str, text: str = "") -> None:
    """空状态占位，避免页面出现大片空白。"""

    st.markdown(
        f'<div class="wm-empty">'
        f'<p class="wm-empty-title">{escape(title)}</p>'
        f'<p class="wm-empty-text">{escape(text)}</p>'
        f"</div>",
        unsafe_allow_html=True,
    )


def tag(text: str, kind: str = "info") -> str:
    """生成一个标签的 HTML 片段，供 data_list 之外的卡片拼接使用。"""

    kind_class = {
        "high": " wm-tag-high",
        "medium": " wm-tag-medium",
        "low": " wm-tag-low",
    }.get(kind, "")

    return f'<span class="wm-tag{kind_class}">{escape(text)}</span>'
