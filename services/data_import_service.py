import pandas as pd
from io import BytesIO

# WorkMesh 统一反馈字段
FIELD_ALIASES = {
    "feedback_id": ["feedback_id", "反馈ID", "工单ID", "ticket_id", "id"],
    "uid": ["uid", "用户ID", "用户id", "user_id", "userid"],
    "feedback_time": ["feedback_time", "反馈时间", "创建时间", "时间", "created_at"],
    "source": ["source", "来源", "反馈来源", "渠道"],
    "product": ["product", "产品", "业务线", "产品名称"],
    "version": ["version", "版本", "产品版本", "sdk_version"],
    "platform": ["platform", "平台", "系统", "操作系统"],
    "device": ["device", "设备", "设备型号", "机型"],
    "error_code": ["error_code", "错误码", "异常码", "错误代码"],
    "description": ["description", "问题描述", "反馈内容", "问题内容", "内容"],
    "status": ["status", "状态", "处理状态"],
}

REQUIRED_FIELDS = ["feedback_time", "source", "description"]


def read_uploaded_file(uploaded_file):
    """读取 CSV 或 Excel 文件。"""
    filename = uploaded_file.name.lower()
    content = uploaded_file.getvalue()

    if filename.endswith(".csv"):
        last_error = None

        for encoding in ["utf-8-sig", "utf-8", "gb18030"]:
            try:
                return pd.read_csv(BytesIO(content), encoding=encoding)
            except UnicodeDecodeError as exc:
                last_error = exc

        raise ValueError(f"CSV 编码无法识别：{last_error}")

    if filename.endswith((".xlsx", ".xls")):
        return pd.read_excel(BytesIO(content))

    raise ValueError("暂不支持该文件格式，请上传 CSV 或 Excel 文件。")


def normalize_column_name(name):
    """标准化表头，忽略大小写和常见分隔符。"""
    return (
        str(name)
        .strip()
        .lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
    )


def detect_field_mapping(columns):
    """根据字段别名自动识别原始列。"""
    mapping = {}
    normalized_columns = {
        normalize_column_name(column): column
        for column in columns
    }

    for standard_field, aliases in FIELD_ALIASES.items():
        matched = None

        for alias in aliases:
            key = normalize_column_name(alias)
            if key in normalized_columns:
                matched = normalized_columns[key]
                break

        mapping[standard_field] = matched

    return mapping


def build_standard_dataframe(df, mapping):
    """根据映射关系生成统一字段的数据表。"""
    result = pd.DataFrame(index=df.index)

    for field in FIELD_ALIASES:
        source_column = mapping.get(field)

        if source_column and source_column in df.columns:
            result[field] = df[source_column]
        else:
            result[field] = None

    result["feedback_time"] = pd.to_datetime(
        result["feedback_time"],
        errors="coerce"
    )

    return result


def validate_dataframe(df):
    """检查缺失字段和重复记录。"""
    issues = []

    for field in REQUIRED_FIELDS:
        if df[field].isna().all():
            issues.append(f"必填字段 {field} 尚未映射或全部为空。")

    empty_description = (
        df["description"].isna()
        | df["description"].astype(str).str.strip().eq("")
    )

    if empty_description.any():
        issues.append(
            f"有 {int(empty_description.sum())} 条记录缺少问题描述。"
        )

    if df["feedback_time"].isna().any():
        issues.append(
            f"有 {int(df['feedback_time'].isna().sum())} 条记录的反馈时间无效。"
        )

    if df["feedback_id"].notna().any():
        duplicate_count = int(
            df["feedback_id"].duplicated(keep="first").sum()
        )
    else:
        duplicate_count = int(
            df.duplicated(
                subset=["uid", "feedback_time", "description"],
                keep="first"
            ).sum()
        )

    return {
        "issues": issues,
        "duplicate_count": duplicate_count,
        "record_count": len(df),
    }
