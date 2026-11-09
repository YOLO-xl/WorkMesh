"""WorkMesh 视觉系统（Modern Tech / Enterprise SaaS）。

设计令牌：以深蓝黑为底，蓝紫渐变作为强调色，玻璃面板 + 细边框 + 克制阴影。
页面只调用 apply_theme() / hero() / section() / icon() 等，不各自写样式。
"""

import streamlit as st
from html import escape

ACCENT = "#6366F1"
ACCENT_SOFT = "#8B5CF6"
INFO = "#60A5FA"

_CSS = """
<style>
:root {
    --wm-bg: #070A12;
    --wm-bg-soft: #0B1020;
    --wm-panel: rgba(16, 23, 38, 0.72);
    --wm-panel-strong: rgba(19, 27, 45, 0.88);
    --wm-border: rgba(148, 163, 184, 0.14);
    --wm-border-hover: rgba(129, 140, 248, 0.45);
    --wm-primary: #6366F1;
    --wm-primary-soft: #8B5CF6;
    --wm-info: #60A5FA;
    --wm-grad: linear-gradient(135deg, #3B82F6 0%, #7C3AED 100%);
    --wm-text: #E6EDF7;
    --wm-text-soft: #C3CFE2;
    --wm-muted: #8A9AB4;
    --wm-faint: #64748B;
    --wm-radius: 16px;
    --wm-radius-sm: 10px;
    --wm-shadow: 0 12px 32px rgba(2, 6, 23, 0.42);
    --wm-shadow-hover: 0 18px 42px rgba(2, 6, 23, 0.55);
    --wm-glow: rgba(99, 102, 241, 0.16);
    --wm-ease: cubic-bezier(0.22, 0.61, 0.36, 1);
}

html, body, .stApp {
    font-family: "Inter", "SF Pro Text", -apple-system, "PingFang SC",
                 "Helvetica Neue", "Microsoft YaHei", sans-serif;
}

.stApp {
    background:
        radial-gradient(900px 420px at 18% -12%, rgba(59, 130, 246, 0.10), transparent 60%),
        radial-gradient(720px 380px at 92% -6%, rgba(124, 58, 237, 0.10), transparent 62%),
        linear-gradient(180deg, var(--wm-bg) 0%, var(--wm-bg-soft) 46%, var(--wm-bg) 100%);
    color: var(--wm-text);
}

.block-container {
    /* 顶部留出 Streamlit 固定 header（Deploy / 菜单）的高度，否则内容会被压住 */
    padding-top: 4.6rem;
    padding-bottom: 3.2rem;
    max-width: 1320px;
}

h1, h2, h3, h4 {
    color: var(--wm-text);
    letter-spacing: 0.01em;
    font-weight: 600;
}

[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li {
    color: var(--wm-text-soft);
    font-size: 0.9rem;
    line-height: 1.7;
}

[data-testid="stMarkdownContainer"] a {
    color: var(--wm-info);
    text-decoration: none;
}

[data-testid="stCaptionContainer"] {
    color: var(--wm-faint);
}

/* ---------- 顶部导航 ---------- */
/* 吸顶要挂在外层 stElementContainer 上：内层容器的高度只有自身高度，
   sticky 会被父级高度夹住而失效 */
div[data-testid="stLayoutWrapper"]:has(div[class*="st-key-wm_topbar"]) {
    position: sticky;
    top: 4.3rem;
    z-index: 40;
}

div[class*="st-key-wm_topbar"] {
    background: rgba(9, 13, 24, 0.96);
    border: 1px solid var(--wm-border);
    border-radius: var(--wm-radius);
    padding: 0.55rem 1rem;
    margin-bottom: 1.1rem;
    backdrop-filter: blur(18px) saturate(120%);
    box-shadow: 0 10px 30px rgba(2, 6, 23, 0.55);
}

div[class*="st-key-wm_topbar"] [data-testid="stHorizontalBlock"] {
    align-items: center;
    gap: 0.55rem;
    flex-wrap: nowrap !important;
}

div[class*="st-key-wm_topbar"] [data-testid="stHorizontalBlock"] > div {
    min-width: 0;
}

div[class*="st-key-wm_topbar"] [data-testid="stTextInput"] {
    margin: 0;
}

div[class*="st-key-wm_topbar"] [data-testid="stTextInput"] label {
    display: none;
}

div[class*="st-key-wm_topbar"] [data-testid="stTextInput"] input {
    height: 2.1rem;
    background: rgba(15, 23, 42, 0.7);
    border: 1px solid var(--wm-border);
    border-radius: var(--wm-radius-sm);
    color: var(--wm-text);
    font-size: 0.82rem;
}

div[class*="st-key-wm_topbar"] [data-testid="stPopover"] button {
    height: 2.1rem;
    white-space: nowrap;
    min-height: 2.1rem;
    padding: 0 0.45rem;
    font-size: 0.76rem;
    border-radius: var(--wm-radius-sm);
    background: rgba(23, 32, 52, 0.6);
}

div[class*="st-key-wm_topbar"] .stButton > button {
    height: 2.1rem;
}

.wm-brand {
    display: flex;
    align-items: center;
    gap: 0.6rem;
}

.wm-brand-mark {
    width: 30px;
    height: 30px;
    border-radius: 9px;
    background: var(--wm-grad);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 4px 14px rgba(99, 102, 241, 0.32);
}

.wm-brand-name {
    font-size: 1.02rem;
    font-weight: 650;
    letter-spacing: 0.01em;
    color: #F3F7FF;
}

.wm-brand-sub {
    font-size: 0.68rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--wm-faint);
}

.wm-page-crumb {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    color: var(--wm-muted);
    font-size: 0.8rem;
    white-space: nowrap;
}

.wm-page-crumb b {
    color: var(--wm-text);
    font-weight: 600;
}

@media (max-width: 900px) {
    div[class*="st-key-wm_topbar"] {
        padding: 0.45rem 0.6rem;
    }

    .wm-brand-sub,
    .wm-brand-name {
        display: none;
    }

    .wm-page-crumb {
        font-size: 0.72rem;
    }

    div[class*="st-key-wm_topbar"] [data-testid="stTextInput"] input {
        font-size: 0.72rem;
        height: 1.9rem;
    }

    div[class*="st-key-wm_topbar"] [data-testid="stPopover"] button {
        height: 1.9rem;
        min-height: 1.9rem;
        padding: 0 0.3rem;
        font-size: 0.7rem;
    }

    .wm-greeting {
        font-size: 1.3rem;
    }
}

/* ---------- 侧边栏 ---------- */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, rgba(9, 13, 24, 0.97), rgba(7, 10, 18, 0.97));
    border-right: 1px solid var(--wm-border);
}

[data-testid="stSidebar"] > div:first-child {
    padding-top: 1rem;
}

[data-testid="stSidebar"] [role="radiogroup"] > div {
    border: 1px solid transparent;
    border-radius: var(--wm-radius-sm);
    padding: 0.15rem 0.35rem;
    transition: background 0.18s var(--wm-ease), border-color 0.18s var(--wm-ease);
}

[data-testid="stSidebar"] [role="radiogroup"] > div:hover {
    border-color: var(--wm-border);
    background: rgba(99, 102, 241, 0.07);
}

[data-testid="stSidebar"] [role="radiogroup"] label {
    color: var(--wm-text-soft);
    font-size: 0.88rem;
}

/* ---------- 欢迎区 ---------- */
div[class*="st-key-wm_welcome"] {
    position: relative;
    background:
        radial-gradient(520px 200px at 8% 0%, rgba(59, 130, 246, 0.14), transparent 70%),
        radial-gradient(460px 200px at 96% 10%, rgba(124, 58, 237, 0.12), transparent 70%),
        var(--wm-panel);
    border: 1px solid var(--wm-border);
    border-radius: 20px;
    padding: 1.15rem 1.4rem 1.1rem;
    margin-bottom: 1.4rem;
    backdrop-filter: blur(12px);
    box-shadow: var(--wm-shadow);
    overflow: hidden;
}

div[class*="st-key-wm_welcome"]::after {
    content: "";
    position: absolute;
    inset: 0;
    border-radius: 20px;
    pointer-events: none;
    background: linear-gradient(120deg, rgba(99, 102, 241, 0.10), transparent 42%);
}

.wm-greeting {
    margin: 0;
    font-size: 1.62rem;
    font-weight: 650;
    letter-spacing: 0.01em;
    color: #F4F8FF;
    line-height: 1.3;
}

.wm-greeting span {
    background: linear-gradient(92deg, #93C5FD 0%, #C4B5FD 100%);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
}

.wm-welcome-line {
    margin: 0.4rem 0 0;
    color: var(--wm-muted);
    font-size: 0.88rem;
    line-height: 1.6;
    max-width: 46rem;
}

.wm-welcome-meta {
    margin-top: 0.55rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1.1rem;
    color: var(--wm-faint);
    font-size: 0.76rem;
    letter-spacing: 0.02em;
}

/* ---------- 区块标题 ---------- */
.wm-section {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.8rem;
    margin: 1.7rem 0 0.7rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid rgba(148, 163, 184, 0.10);
}

.wm-section-title {
    font-size: 0.98rem;
    font-weight: 620;
    color: #EDF2FB;
    letter-spacing: 0.01em;
    margin: 0;
}

.wm-section-desc {
    color: var(--wm-faint);
    font-size: 0.78rem;
    margin: 0;
}

.wm-group-label {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    margin: 1.15rem 0 0.35rem;
    font-size: 0.8rem;
    color: var(--wm-muted);
    letter-spacing: 0.04em;
}

.wm-group-label::before {
    content: "";
    width: 4px;
    height: 14px;
    border-radius: 2px;
    background: var(--wm-grad);
    opacity: 0.85;
}

.wm-group-label span {
    color: var(--wm-faint);
}

/* ---------- 模块卡片（整卡可点击） ---------- */
div[class*="st-key-modcard-"] {
    position: relative;
    display: flex;
    flex-direction: column;
    min-height: 14.5rem;
    padding: 1.05rem 1.15rem 0.85rem;
    border: 1px solid var(--wm-border);
    border-radius: var(--wm-radius);
    background: linear-gradient(165deg, rgba(20, 28, 47, 0.86), rgba(11, 16, 30, 0.78));
    box-shadow: var(--wm-shadow);
    transition:
        transform 0.22s var(--wm-ease),
        border-color 0.22s var(--wm-ease),
        box-shadow 0.22s var(--wm-ease);
}

div[class*="st-key-modcard-"]:hover {
    transform: translateY(-3px);
    border-color: var(--wm-border-hover);
    box-shadow: var(--wm-shadow-hover);
}

div[class*="st-key-modcard-"] [data-testid="stElementContainer"] {
    position: static;
}

div[class*="st-key-modcard-"] .stButton {
    position: absolute;
    inset: 0;
    z-index: 2;
}

div[class*="st-key-modcard-"] .stButton > button {
    width: 100%;
    height: 100%;
    opacity: 0;
    border: none;
    background: transparent;
    cursor: pointer;
}

div[class*="st-key-modcard-"] .stButton > button:hover {
    opacity: 0;
    box-shadow: none;
    transform: none;
}

.wm-module-icon {
    width: 34px;
    height: 34px;
    border-radius: 10px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: rgba(99, 102, 241, 0.12);
    border: 1px solid rgba(129, 140, 248, 0.26);
    color: #A5B4FC;
    margin-bottom: 0.7rem;
}

.wm-module-name {
    margin: 0;
    font-size: 1.02rem;
    font-weight: 620;
    color: #F3F7FF;
    letter-spacing: 0.01em;
}

.wm-module-desc {
    margin: 0.3rem 0 0.7rem;
    color: var(--wm-muted);
    font-size: 0.82rem;
    line-height: 1.6;
}

.wm-module-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 0.32rem;
    margin-bottom: 0.7rem;
}

.wm-module-status {
    margin-top: auto;
    display: flex;
    align-items: center;
    gap: 0.4rem;
    color: var(--wm-faint);
    font-size: 0.74rem;
    font-variant-numeric: tabular-nums;
}

.wm-module-status b {
    color: #C7D2FE;
    font-weight: 600;
}

.wm-module-cta {
    margin: 0.55rem 0 0;
    white-space: nowrap;
    color: #A5B4FC;
    font-size: 0.78rem;
    letter-spacing: 0.02em;
    display: flex;
    align-items: center;
    gap: 0.3rem;
}

/* ---------- 信息面板 ---------- */
.wm-panel {
    border: 1px solid var(--wm-border);
    border-radius: var(--wm-radius);
    background: var(--wm-panel);
    padding: 1rem 1.15rem;
    height: 100%;
    backdrop-filter: blur(10px);
}

.wm-panel-title {
    margin: 0 0 0.55rem;
    font-size: 0.86rem;
    font-weight: 600;
    color: #DCE5F5;
    letter-spacing: 0.03em;
    display: flex;
    align-items: center;
    justify-content: space-between;
}

.wm-panel-title span {
    color: var(--wm-faint);
    font-size: 0.72rem;
    font-weight: 400;
}

.wm-panel-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem;
    padding: 0.42rem 0;
    border-bottom: 1px dashed rgba(148, 163, 184, 0.10);
    font-size: 0.8rem;
    color: var(--wm-text-soft);
}

.wm-panel-row:last-child {
    border-bottom: none;
}

.wm-panel-row .wm-row-meta {
    color: var(--wm-faint);
    font-size: 0.72rem;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}

/* ---------- 指标 ---------- */
div[data-testid="stMetric"] {
    background: var(--wm-panel);
    border: 1px solid var(--wm-border);
    border-radius: var(--wm-radius);
    padding: 0.95rem 1.1rem 0.85rem;
    box-shadow: var(--wm-shadow);
    transition: border-color 0.2s var(--wm-ease), transform 0.2s var(--wm-ease);
    backdrop-filter: blur(10px);
}

div[data-testid="stMetric"]:hover {
    border-color: var(--wm-border-hover);
    transform: translateY(-2px);
}

div[data-testid="stMetric"] label,
div[data-testid="stMetricLabel"] {
    color: var(--wm-muted) !important;
    font-size: 0.74rem !important;
    letter-spacing: 0.04em;
}

div[data-testid="stMetricValue"] {
    color: #F1F5FD;
    font-weight: 640;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.01em;
}

/* ---------- 按钮 ---------- */
.stButton > button {
    border-radius: var(--wm-radius-sm);
    border: 1px solid rgba(148, 163, 184, 0.22);
    background: rgba(23, 32, 52, 0.72);
    color: var(--wm-text-soft);
    font-weight: 560;
    font-size: 0.83rem;
    letter-spacing: 0.01em;
    transition:
        background 0.18s var(--wm-ease),
        border-color 0.18s var(--wm-ease),
        transform 0.18s var(--wm-ease),
        box-shadow 0.18s var(--wm-ease);
}

.stButton > button:hover {
    border-color: var(--wm-border-hover);
    background: rgba(99, 102, 241, 0.16);
    color: #F3F7FF;
    transform: translateY(-1px);
    box-shadow: 0 8px 18px rgba(2, 6, 23, 0.32);
}

.stButton > button:focus:not(:active) {
    border-color: rgba(129, 140, 248, 0.7);
    box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.18);
}

.stButton > button[kind="primary"] {
    background: var(--wm-grad);
    border-color: rgba(129, 140, 248, 0.55);
    color: #FFFFFF;
    box-shadow: 0 8px 20px rgba(79, 70, 229, 0.28);
}

.stButton > button[kind="primary"]:hover {
    background: linear-gradient(135deg, #4F8DF7 0%, #8B5CF6 100%);
    box-shadow: 0 10px 24px rgba(79, 70, 229, 0.38);
}

.stButton > button:disabled {
    border-color: rgba(148, 163, 184, 0.14);
    background: rgba(30, 41, 59, 0.4);
    color: #56637A;
    box-shadow: none;
}

/* ---------- 输入 / 选择 ---------- */
div[data-baseweb="input"],
div[data-baseweb="select"] > div,
div[data-baseweb="textarea"] {
    background: rgba(15, 23, 42, 0.75) !important;
    border: 1px solid var(--wm-border) !important;
    border-radius: var(--wm-radius-sm) !important;
    color: var(--wm-text) !important;
    transition: border-color 0.18s var(--wm-ease), box-shadow 0.18s var(--wm-ease);
}

div[data-baseweb="input"]:focus-within,
div[data-baseweb="select"]:focus-within,
div[data-baseweb="textarea"]:focus-within {
    border-color: rgba(129, 140, 248, 0.6) !important;
    box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.14) !important;
}

[data-testid="stFileUploader"] {
    background: rgba(15, 23, 42, 0.6);
    border: 1px dashed rgba(129, 140, 248, 0.28);
    border-radius: var(--wm-radius);
    padding: 0.6rem;
}

[data-testid="stExpander"] {
    border: 1px solid var(--wm-border);
    border-radius: var(--wm-radius);
    background: rgba(15, 23, 42, 0.55);
}

[data-baseweb="tab-list"] {
    gap: 0.35rem;
    background: transparent;
    border-bottom: 1px solid rgba(148, 163, 184, 0.12);
    padding-bottom: 0.35rem;
}

[data-baseweb="tab"] {
    border-radius: var(--wm-radius-sm);
    background: rgba(15, 23, 42, 0.6);
    border: 1px solid transparent;
    color: var(--wm-muted);
    padding: 0.3rem 1rem;
}

[data-baseweb="tab"][aria-selected="true"] {
    color: #C7D2FE;
    border-color: rgba(129, 140, 248, 0.4);
    background: rgba(99, 102, 241, 0.12);
}

[data-testid="stAlert"] {
    border-radius: var(--wm-radius-sm);
    border: 1px solid rgba(129, 140, 248, 0.22);
    background: rgba(15, 23, 42, 0.68);
}

hr {
    border-color: rgba(148, 163, 184, 0.14);
}

/* ---------- 滚动条 ---------- */
::-webkit-scrollbar {
    width: 8px;
    height: 8px;
}

::-webkit-scrollbar-track {
    background: rgba(7, 10, 18, 0.6);
}

::-webkit-scrollbar-thumb {
    background: rgba(129, 140, 248, 0.26);
    border-radius: 8px;
}

::-webkit-scrollbar-thumb:hover {
    background: rgba(129, 140, 248, 0.4);
}

/* ---------- 首屏 hero（其他页面继续使用） ---------- */
.wm-hero {
    position: relative;
    padding: 1.35rem 1.5rem;
    border-radius: 20px;
    border: 1px solid var(--wm-border);
    background:
        radial-gradient(460px 180px at 10% 0%, rgba(59, 130, 246, 0.12), transparent 70%),
        radial-gradient(420px 180px at 94% 6%, rgba(124, 58, 237, 0.10), transparent 70%),
        var(--wm-panel);
    box-shadow: var(--wm-shadow);
    overflow: hidden;
    margin-bottom: 0.7rem;
}

.wm-hero-title {
    margin: 0;
    font-size: 1.5rem;
    font-weight: 650;
    letter-spacing: 0.01em;
    color: #F4F8FF;
    line-height: 1.3;
}

.wm-hero-sub {
    margin: 0.4rem 0 0;
    color: var(--wm-muted);
    font-size: 0.88rem;
    letter-spacing: 0.01em;
}

.wm-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.42rem;
    margin-top: 0.85rem;
}

.wm-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.22rem 0.6rem;
    border-radius: 999px;
    border: 1px solid rgba(129, 140, 248, 0.24);
    background: rgba(99, 102, 241, 0.09);
    color: #B6C2F5;
    font-size: 0.68rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}

.wm-chip-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--wm-primary-soft);
}

/* ---------- 文本工具 ---------- */
.wm-card {
    border: 1px solid var(--wm-border);
    border-radius: var(--wm-radius);
    padding: 1.05rem 1.2rem;
    background: var(--wm-panel);
    transition: border-color 0.2s var(--wm-ease), transform 0.2s var(--wm-ease);
    height: 100%;
}

.wm-card:hover {
    border-color: var(--wm-border-hover);
    transform: translateY(-2px);
}

.wm-card-title {
    color: #EDF2FB;
    font-size: 0.94rem;
    font-weight: 600;
    margin: 0 0 0.3rem;
}

.wm-card-text {
    color: var(--wm-muted);
    font-size: 0.82rem;
    line-height: 1.65;
    margin: 0;
}

.wm-stat {
    font-variant-numeric: tabular-nums;
    color: #C7D2FE;
    font-weight: 650;
    font-size: 1.3rem;
    letter-spacing: 0.01em;
}

.wm-mono {
    font-family: "SFMono-Regular", "JetBrains Mono", Menlo, Consolas, monospace;
    font-size: 0.78rem;
    color: #A5B4FC;
}

.wm-status-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1.5rem;
    padding: 0.75rem 1.1rem;
    border-radius: var(--wm-radius-sm);
    border: 1px solid var(--wm-border);
    background: rgba(13, 19, 34, 0.7);
    font-size: 0.78rem;
    color: var(--wm-muted);
    margin: 0.5rem 0;
}

.wm-status-bar span b {
    color: var(--wm-text-soft);
    font-weight: 600;
    letter-spacing: 0.02em;
    font-variant-numeric: tabular-nums;
}

/* ---------- 流程步骤条 ---------- */
.wm-steps {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin: 0.6rem 0 0.2rem;
}

.wm-step {
    flex: 1 1 170px;
    padding: 0.65rem 0.8rem;
    border-radius: var(--wm-radius-sm);
    border: 1px solid rgba(148, 163, 184, 0.14);
    background: rgba(13, 21, 36, 0.62);
    font-size: 0.79rem;
    color: var(--wm-faint);
    transition: border-color 0.18s var(--wm-ease), color 0.18s var(--wm-ease);
}

.wm-step-active {
    border-color: rgba(129, 140, 248, 0.4);
    background: rgba(99, 102, 241, 0.12);
    color: var(--wm-text-soft);
}

.wm-step-index {
    color: #A5B4FC;
    font-weight: 650;
    margin-right: 0.35rem;
    font-variant-numeric: tabular-nums;
}

/* ---------- 列表 ---------- */
.wm-list {
    border: 1px solid var(--wm-border);
    border-radius: var(--wm-radius-sm);
    overflow: hidden;
    background: rgba(13, 19, 34, 0.58);
}

.wm-list-head {
    display: grid;
    gap: 0.9rem;
    padding: 0.5rem 1rem;
    font-size: 0.66rem;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: var(--wm-faint);
    background: rgba(10, 16, 30, 0.75);
    border-bottom: 1px solid rgba(148, 163, 184, 0.10);
}

.wm-list-row {
    display: grid;
    gap: 0.9rem;
    padding: 0.62rem 1rem;
    font-size: 0.82rem;
    color: var(--wm-text-soft);
    border-bottom: 1px solid rgba(148, 163, 184, 0.06);
    transition: background 0.15s var(--wm-ease);
    align-items: center;
}

.wm-list-row:hover {
    background: rgba(99, 102, 241, 0.07);
}

.wm-list-row:last-child {
    border-bottom: none;
}

.wm-list-row .wm-strong {
    color: #EDF2FB;
    font-weight: 600;
}

.wm-list-row .wm-dim {
    color: var(--wm-faint);
    font-size: 0.76rem;
}

.wm-list-empty {
    padding: 1.5rem 1rem;
    text-align: center;
    color: var(--wm-faint);
    font-size: 0.82rem;
}

/* ---------- 标签 ---------- */
.wm-tag {
    display: inline-block;
    padding: 0.1rem 0.5rem;
    border-radius: 6px;
    font-size: 0.7rem;
    letter-spacing: 0.02em;
    border: 1px solid rgba(129, 140, 248, 0.24);
    background: rgba(99, 102, 241, 0.08);
    color: #B6C2F5;
    white-space: nowrap;
}

.wm-tag-high {
    border-color: rgba(251, 146, 60, 0.34);
    background: rgba(251, 146, 60, 0.1);
    color: #FDBA74;
}

.wm-tag-medium {
    border-color: rgba(96, 165, 250, 0.34);
    background: rgba(96, 165, 250, 0.1);
    color: #93C5FD;
}

.wm-tag-low {
    border-color: rgba(148, 163, 184, 0.2);
    background: rgba(148, 163, 184, 0.07);
    color: #94A3B8;
}

/* ---------- 空状态 ---------- */
.wm-empty {
    border: 1px dashed rgba(148, 163, 184, 0.22);
    border-radius: var(--wm-radius);
    padding: 1.6rem 1.1rem;
    text-align: center;
    background: rgba(13, 19, 34, 0.45);
    margin: 0.35rem 0;
}

.wm-empty-title {
    color: var(--wm-text-soft);
    font-size: 0.88rem;
    margin: 0 0 0.25rem;
}

.wm-empty-text {
    color: var(--wm-faint);
    font-size: 0.78rem;
    margin: 0;
}

/* ---------- 簇卡片 ---------- */
.wm-cluster {
    border: 1px solid var(--wm-border);
    border-left: 3px solid var(--wm-primary);
    border-radius: var(--wm-radius-sm);
    padding: 0.85rem 1.05rem;
    background: var(--wm-panel);
    transition: border-color 0.18s var(--wm-ease), transform 0.18s var(--wm-ease);
    margin-bottom: 0.55rem;
}

.wm-cluster:hover {
    border-color: var(--wm-border-hover);
    transform: translateY(-1px);
}

.wm-cluster-top {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    flex-wrap: wrap;
    margin-bottom: 0.28rem;
}

.wm-cluster-title {
    color: #EDF2FB;
    font-size: 0.94rem;
    font-weight: 600;
    margin: 0;
}

.wm-cluster-sub {
    color: var(--wm-muted);
    font-size: 0.78rem;
    margin: 0.12rem 0 0;
    line-height: 1.55;
}

/* ---------- 轻量入场动效 ---------- */
@keyframes wm-slide-up {
    from { transform: translateY(6px); }
    to { transform: translateY(0); }
}

.wm-hero,
div[class*="st-key-wm_welcome"] {
    animation: wm-slide-up 0.42s ease-out;
}

div[data-testid="stMetric"] {
    animation: wm-slide-up 0.3s ease-out;
}

@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        animation-duration: 0.001ms !important;
        transition-duration: 0.001ms !important;
    }
}
</style>
"""


# 统一线性图标（stroke 风格），避免彩色 emoji 破坏整体调性。
_ICONS = {
    "database": '<ellipse cx="12" cy="5" rx="7" ry="3"/><path d="M5 5v6c0 1.7 3.1 3 7 3s7-1.3 7-3V5"/><path d="M5 11v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.2 1.2"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.2-1.2"/>',
    "cluster": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "chart": '<path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M22 20H2"/>',
    "stethoscope": '<path d="M6 3v6a4 4 0 0 0 8 0V3"/><path d="M10 13v3a4 4 0 0 0 8 0v-2"/><circle cx="19" cy="9" r="2"/>',
    "document": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/><path d="M9 12h6M9 16h6"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "bell": '<path d="M18 8a6 6 0 1 0-12 0c0 6-2 7-2 7h16s-2-1-2-7"/><path d="M10.5 20a2 2 0 0 0 3 0"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.8.4-1 .9-1 1.7"/><path d="M12 17h.01"/>',
    "spark": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    "arrow": '<path d="M5 12h13"/><path d="M13 6l6 6-6 6"/>',
}


def icon(name: str, size: int = 18) -> str:
    """返回一枚线性图标的 HTML 片段。"""

    path = _ICONS.get(name, _ICONS["spark"])

    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
        f'stroke-linejoin="round">{path}</svg>'
    )


def apply_theme() -> None:
    """注入全局样式。

    Streamlit 每次 rerun 都会重建元素树，样式必须每次都写出去，
    否则页面跳转后样式会整体丢失（曾出现卡片样式消失的问题）。
    """

    st.markdown(_CSS, unsafe_allow_html=True)


def hero(
    title: str,
    subtitle: str = "",
    chips: list[str] | None = None,
) -> None:
    """页面顶部主视觉：标题 + 副标题 + 能力胶囊。"""

    chip_html = ""

    if chips:
        chip_html = (
            '<div class="wm-chips">'
            + "".join(
                f'<span class="wm-chip">'
                f'<span class="wm-chip-dot"></span>{escape(chip)}</span>'
                for chip in chips
            )
            + "</div>"
        )

    st.markdown(
        f"""
        <div class="wm-hero">
            <p class="wm-hero-title">{escape(title)}</p>
            <p class="wm-hero-sub">{escape(subtitle)}</p>
            {chip_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def section(title: str, description: str = "") -> None:
    """区块标题。"""

    st.markdown(
        f"""
        <div class="wm-section">
            <p class="wm-section-title">{escape(title)}</p>
            <p class="wm-section-desc">{escape(description)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def group_label(text: str, note: str = "") -> None:
    """模块分组标签。"""

    suffix = f'<span>{escape(note)}</span>' if note else ""
    st.markdown(
        f'<div class="wm-group-label">{escape(text)}{suffix}</div>',
        unsafe_allow_html=True,
    )


def card(title: str, text: str) -> None:
    """静态信息卡。"""

    st.markdown(
        f"""
        <div class="wm-card">
            <p class="wm-card-title">{escape(title)}</p>
            <p class="wm-card-text">{escape(text)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def panel(title: str, rows: list[tuple[str, str]], note: str = "") -> None:
    """信息面板：标题 + 若干行（左文本 / 右元信息）。"""

    rows_html = "".join(
        f'<div class="wm-panel-row"><span>{escape(left)}</span>'
        f'<span class="wm-row-meta">{escape(right)}</span></div>'
        for left, right in rows
    )

    st.markdown(
        f"""
        <div class="wm-panel">
            <p class="wm-panel-title">{escape(title)}<span>{escape(note)}</span></p>
            {rows_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def status_bar(items: list[tuple[str, str]]) -> None:
    """一行状态条：左侧字段名，右侧值。"""

    body = "".join(
        f"<span>{escape(label)} <b>{escape(value)}</b></span>"
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
            f'<span class="wm-step-index">{index:02d}</span>{escape(step)}</div>'
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
    """生成一个标签的 HTML 片段，供卡片拼接使用。"""

    kind_class = {
        "high": " wm-tag-high",
        "medium": " wm-tag-medium",
        "low": " wm-tag-low",
    }.get(kind, "")

    return f'<span class="wm-tag{kind_class}">{escape(text)}</span>'
