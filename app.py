import streamlit as st
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

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
from database.evidence_repository import EvidenceRepository
from ui.theme import apply_theme, hero
from ui.landing_page import remember_visit, render as render_landing_page
from ui.topbar import render as render_topbar
from ui.analytics_page import render as render_analytics_page
from ui.evidence_page import render as render_evidence_page
from ui.cluster_page import render as render_cluster_page
from ui.diagnosis_page import render as render_diagnosis_page


# 初始化数据库并创建 Repository
initialize_database()
feedback_repository = FeedbackRepository()
evidence_repository = EvidenceRepository()


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

apply_theme()

# 首屏与功能模块是两层：首屏不参与侧边栏导航。
PAGES = [
    "数据接入",
    "证据关联",
    "问题聚类",
    "反馈分析",
    "单条诊断",
    "报告中心",
]

if "wm_view" not in st.session_state:
    st.session_state.wm_view = "landing"

if "page_navigation" not in st.session_state:
    st.session_state.page_navigation = None

# 深链接：?page=问题聚类 直接进模块，?view=home 回首屏。
requested_page = st.query_params.get("page")
requested_view = st.query_params.get("view")

if (
    requested_page in PAGES
    and st.session_state.get("wm_deeplink") != requested_page
):
    st.session_state.page_navigation = requested_page
    st.session_state.wm_view = "module"
    st.session_state.wm_deeplink = requested_page

elif requested_view == "home" and st.session_state.get("wm_deeplink") != "home":
    st.session_state.page_navigation = None
    st.session_state.wm_view = "landing"
    st.session_state.wm_deeplink = "home"


def go_to_page(page):
    st.session_state.page_navigation = page
    st.session_state.wm_view = "module"
    st.session_state.wm_deeplink = page
    st.query_params["page"] = page
    st.query_params.pop("view", None)
    remember_visit(page)


def go_home():
    st.session_state.page_navigation = None
    st.session_state.wm_view = "landing"
    st.session_state.wm_deeplink = "home"
    st.query_params["view"] = "home"
    st.query_params.pop("page", None)


def enter_module():
    st.session_state.wm_view = "module"


st.sidebar.markdown(
    """
    <div class="wm-brand" style="margin:0.2rem 0 0.5rem">
        <span class="wm-brand-mark" style="color:#FFFFFF">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.6" stroke-linecap="round"
                 stroke-linejoin="round"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/></svg>
        </span>
        <div>
            <div class="wm-brand-name">WorkMesh</div>
            <div class="wm-brand-sub">导航</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.sidebar.divider()

if st.session_state.wm_view == "module":
    st.sidebar.button(
        "← 返回首页",
        on_click=go_home,
        use_container_width=True,
    )

st.sidebar.radio(
    "模块",
    PAGES,
    index=None,
    key="page_navigation",
    on_change=enter_module,
)

st.sidebar.divider()
st.sidebar.caption("v0.6.0-dev · 本地运行")

current_label = (
    "首页"
    if st.session_state.wm_view == "landing"
    else (st.session_state.page_navigation or PAGES[0])
)

topbar_records = feedback_repository.list_feedback(
    feedback_repository.get_default_workspace(), page=1, page_size=4
)["records"]

render_topbar(
    current_label,
    topbar_records,
    feedback_repository.get_default_workspace(),
)


# 首屏：独立的介绍层，不进入模块导航
if st.session_state.wm_view == "landing":
    render_landing_page(
        feedback_repository,
        evidence_repository,
        feedback_repository.get_default_workspace(),
    )
    st.stop()


page = st.session_state.page_navigation or PAGES[0]


# 数据接入
if page == "数据接入":
    hero(
        "数据接入",
        "导入企业反馈数据，自动识别字段并转换为 WorkMesh 统一格式",
        ["CSV / Excel", "Field Mapping", "Quality Gate"],
    )
    st.write("")

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


# 证据关联
elif page == "证据关联":
    render_evidence_page()


# 问题聚类
elif page == "问题聚类":
    render_cluster_page()


# 反馈分析
elif page == "反馈分析":
    render_analytics_page(feedback_repository, feedback_repository.get_default_workspace())


# 单条诊断
elif page == "单条诊断":
    render_diagnosis_page()


# 其他模块
else:
    hero(
        page,
        "该模块正在开发中，后续版本开放",
        ["Coming Soon"],
    )
    st.write("")
    st.caption("可用顶部「← 返回首页」回工作台。")
