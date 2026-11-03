import streamlit as st
import pandas as pd

from services.data_import_service import (
    read_uploaded_file,
    detect_field_mapping,
    build_standard_dataframe,
    validate_dataframe,
    FIELD_ALIASES,
    REQUIRED_FIELDS,
)

st.set_page_config(
    page_title="WorkMesh | Enterprise AI Workspace",
    page_icon="🔗",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "page_navigation" not in st.session_state:
    st.session_state.page_navigation = "工作台首页"

def go_to_page(page):
    st.session_state.page_navigation = page


st.sidebar.title("WorkMesh")
st.sidebar.caption("Enterprise AI Workspace")
st.sidebar.divider()

page = st.sidebar.radio(
    "工作空间",
    ["工作台首页", "数据接入", "反馈分析", "AI 分析", "报告中心"],
    key="page_navigation",
)


# 首页
if page == "工作台首页":
    st.title("WorkMesh")
    st.caption("Connect Everything. Work Smarter.")
    st.write("企业多来源信息的 AI 分析工作台")
    st.divider()

    st.subheader("数据概览")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("反馈总量", "—")
    col2.metric("问题类别", "—")
    col3.metric("待处理问题", "—")
    col4.metric("数据来源", "0")

    st.divider()
    st.subheader("工作空间")

    col1, col2 = st.columns(2)

    with col1:
        with st.container(border=True):
            st.subheader("数据接入")
            st.write("导入 Excel、CSV 或连接企业业务系统。")
            st.button(
                "进入数据接入",
                use_container_width=True,
                on_click=go_to_page,
                args=("数据接入",),
            )

        with st.container(border=True):
            st.subheader("反馈分析")
            st.write("识别高频问题、版本异常和反馈趋势。")
            st.button(
                "进入反馈分析",
                use_container_width=True,
                on_click=go_to_page,
                args=("反馈分析",),
            )

    with col2:
        with st.container(border=True):
            st.subheader("AI 分析")
            st.write("基于业务数据生成分析结论和排查建议。")
            st.button(
                "进入 AI 分析",
                use_container_width=True,
                on_click=go_to_page,
                args=("AI 分析",),
            )

        with st.container(border=True):
            st.subheader("报告中心")
            st.write("查看和管理自动生成的分析报告。")
            st.button(
                "进入报告中心",
                use_container_width=True,
                on_click=go_to_page,
                args=("报告中心",),
            )


# 数据接入
elif page == "数据接入":
    st.title("数据接入")
    st.caption("导入企业反馈数据，并转换为 WorkMesh 统一数据格式。")
    st.divider()

    uploaded_file = st.file_uploader(
        "上传反馈数据",
        type=["csv", "xlsx", "xls"],
        help="支持 CSV 和 Excel 文件。当前版本仅进行本地预览，不会上传到云端。",
    )

    if uploaded_file is not None:
        try:
            raw_df = read_uploaded_file(uploaded_file)

            if raw_df.empty:
                st.warning("文件中没有数据记录。")
            else:
                st.success(
                    f"文件读取成功：{len(raw_df)} 条记录，"
                    f"{len(raw_df.columns)} 个原始字段。"
                )

                st.subheader("原始数据预览")
                st.dataframe(raw_df.head(10), use_container_width=True)

                st.divider()
                st.subheader("字段识别与映射")
                st.write(
                    "系统已尝试自动识别字段。"
                    "请检查映射结果，必要时手动调整。"
                )

                detected = detect_field_mapping(raw_df.columns)
                options = ["不映射"] + list(raw_df.columns)
                mapping = {}

                for field, aliases in FIELD_ALIASES.items():
                    default_column = detected[field]
                    default_index = (
                        options.index(default_column)
                        if default_column in options
                        else 0
                    )

                    selected = st.selectbox(
                        f"{field}"
                        + ("（必填）" if field in REQUIRED_FIELDS else "（选填）"),
                        options,
                        index=default_index,
                        key=f"mapping_{field}",
                    )

                    mapping[field] = (
                        None if selected == "不映射" else selected
                    )

                if st.button("生成标准化预览", type="primary"):
                    standard_df = build_standard_dataframe(
                        raw_df, mapping
                    )
                    validation = validate_dataframe(standard_df)

                    st.session_state["import_preview"] = standard_df
                    st.session_state["import_validation"] = validation
                    st.session_state["import_filename"] = uploaded_file.name

                if "import_preview" in st.session_state:
                    standard_df = st.session_state["import_preview"]
                    validation = st.session_state["import_validation"]

                    st.divider()
                    st.subheader("数据质量检查")

                    c1, c2, c3 = st.columns(3)
                    c1.metric("记录数量", validation["record_count"])
                    c2.metric("重复记录", validation["duplicate_count"])
                    c3.metric("必填字段", f"{len(REQUIRED_FIELDS)} 项")

                    if validation["issues"]:
                        for issue in validation["issues"]:
                            st.warning(issue)
                    else:
                        st.success("必填字段检查通过，未发现明显缺失问题。")

                    st.subheader("标准化数据预览")
                    st.dataframe(
                        standard_df.head(20),
                        use_container_width=True,
                    )

                    st.download_button(
                        "下载标准化 CSV",
                        data=standard_df.to_csv(
                            index=False
                        ).encode("utf-8-sig"),
                        file_name="workmesh_normalized_feedback.csv",
                        mime="text/csv",
                    )

                    st.info(
                        "当前版本仅支持预览和下载，尚未写入 SQLite 数据库。"
                    )

        except Exception as exc:
            st.error(f"文件处理失败：{exc}")


# 其他模块
else:
    st.title(page)
    st.info("该模块正在开发中，后续版本开放。")
    st.button(
        "返回工作台首页",
        on_click=go_to_page,
        args=("工作台首页",),
    )
