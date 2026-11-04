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

from database.migration import initialize_database
from database.feedback_repository import FeedbackRepository


# 初始化数据库并创建 Repository
initialize_database()
feedback_repository = FeedbackRepository()


def build_import_records(raw_df, standard_df, mapping):
    """将标准化数据转换为 Repository 可写入的记录。"""
    records = []

    mapped_columns = {
        column for column in mapping.values()
        if column is not None
    }

    for position in range(len(standard_df)):
        standard_row = standard_df.iloc[position]
        raw_row = raw_df.iloc[position]

        record = standard_row.to_dict()

        # 反馈来源属于反馈记录字段，不是文件连接器类型。
        record["source_type"] = record.get("source")

        # 保留原始行，便于后续追溯。
        record["raw_payload"] = raw_row.to_dict()

        # 保留未映射的原始字段。
        record["extra_data"] = {
            str(column): raw_row[column]
            for column in raw_df.columns
            if column not in mapped_columns
        }

        records.append(record)

    return records

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
        import hashlib

        current_file_hash = hashlib.sha256(
            uploaded_file.getvalue()
        ).hexdigest()

        preview_hash = st.session_state.get("import_file_hash")

        if preview_hash and preview_hash != current_file_hash:
            for key in (
                "import_preview",
                "import_validation",
                "import_filename",
                "import_raw_df",
                "import_mapping",
                "import_file_hash",
                "last_import_result",
            ):
                st.session_state.pop(key, None)

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
                    st.session_state["import_raw_df"] = raw_df.copy()
                    st.session_state["import_mapping"] = mapping.copy()
                    st.session_state["import_file_hash"] = __import__(
                        "hashlib"
                    ).sha256(uploaded_file.getvalue()).hexdigest()

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

                    st.divider()
                    st.subheader("导入数据库")

                    can_import = (
                        not validation["issues"]
                        and validation["valid_count"] > 0
                    )

                    if validation["issues"]:
                        for issue in validation["issues"]:
                            st.error(issue)

                    if validation["row_issues"]:
                        st.warning(
                            f"有 {len(validation['row_issues'])} 条记录存在质量问题，"
                            "导入时将跳过这些记录。"
                        )
                        st.dataframe(
                            pd.DataFrame(validation["row_issues"]),
                            use_container_width=True,
                        )

                    st.caption(
                        f"预计有效记录：{validation['valid_count']} 条"
                    )

                    if not can_import:
                        st.warning("当前数据不满足导入条件，暂时不能导入。")

                    if st.button(
                        "确认导入数据库",
                        type="primary",
                        disabled=not can_import,
                        key="confirm_database_import",
                    ):
                        try:
                            workspace_id = (
                                feedback_repository.get_default_workspace()
                            )

                            source_id = (
                                feedback_repository.get_or_create_file_source(
                                    workspace_id=workspace_id,
                                    source_type="file",
                                    name="本地文件导入",
                                )
                            )

                            file_hash = st.session_state["import_file_hash"]
                            raw_data = st.session_state["import_raw_df"]
                            mapping_data = st.session_state["import_mapping"]

                            if feedback_repository.has_imported_file(
                                source_id,
                                file_hash,
                            ):
                                st.warning(
                                    "该文件已经导入过。为避免重复写入，本次操作已拦截。"
                                )
                            else:
                                # 根据校验结果筛选有效记录。
                                invalid_positions = {
                                    item["row"] - 1
                                    for item in validation["row_issues"]
                                }
                                valid_positions = [
                                    i for i in range(len(standard_df))
                                    if i not in invalid_positions
                                ]

                                valid_standard_df = (
                                    standard_df.iloc[valid_positions].reset_index(drop=True)
                                )
                                valid_raw_df = (
                                    raw_data.iloc[valid_positions].reset_index(drop=True)
                                )

                                # 先构造记录，再创建批次，避免转换失败留下 running 批次。
                                records = build_import_records(
                                    valid_raw_df,
                                    valid_standard_df,
                                    mapping_data,
                                )

                                batch_id = (
                                    feedback_repository.create_import_batch(
                                        workspace_id=workspace_id,
                                        source_id=source_id,
                                        file_name=st.session_state["import_filename"],
                                        file_size=len(uploaded_file.getvalue()),
                                        file_hash=file_hash,
                                        total_records=len(records),
                                    )
                                )

                                result = (
                                    feedback_repository.bulk_insert_feedback(
                                        batch_id=batch_id,
                                        records=records,
                                    )
                                )

                                result["skipped_records"] = len(invalid_positions)
                                result["original_total_records"] = len(standard_df)
                                result["file_hash"] = file_hash

                                st.session_state["last_import_result"] = result

                        except Exception as exc:
                            st.error(f"数据库导入失败：{exc}")

                    result = st.session_state.get("last_import_result")

                    if result and result.get("file_hash") == st.session_state.get(
                        "import_file_hash"
                    ):
                        st.divider()
                        st.subheader("最近一次导入结果")

                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric(
                            "总记录数",
                            result["original_total_records"],
                        )
                        c2.metric(
                            "成功写入",
                            result["success_records"],
                        )
                        c3.metric(
                            "写入失败",
                            result["failed_records"],
                        )
                        c4.metric(
                            "跳过记录",
                            result["skipped_records"],
                        )

                        st.caption(
                            f"批次 ID：{result['batch_id']} | "
                            f"状态：{result['status']}"
                        )

                        if result["errors"]:
                            st.warning("部分记录写入失败：")
                            st.dataframe(
                                pd.DataFrame(result["errors"]),
                                use_container_width=True,
                            )
                        elif result["skipped_records"] > 0:
                            st.success("有效记录已写入，异常记录已跳过。")
                        else:
                            st.success("本批数据已全部成功写入 SQLite。")

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
