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
    """检查必填字段、单条记录质量和重复记录。"""
    issues = []
    row_issues = []
    required_fields = REQUIRED_FIELDS
    empty_columns = {}

    for field in required_fields:
        if field not in df.columns:
            issues.append(f"必填字段 {field} 尚未映射或全部为空。")
            empty_columns[field] = None
            continue

        values = df[field]
        empty = values.isna() | values.astype(str).str.strip().eq("")
        empty_columns[field] = empty

        if empty.all():
            issues.append(f"必填字段 {field} 尚未映射或全部为空。")

    if "feedback_time" in df.columns:
        invalid_feedback_time = pd.to_datetime(
            df["feedback_time"], errors="coerce"
        ).isna()
    else:
        invalid_feedback_time = pd.Series(True, index=df.index)

    row_reasons = {}

    for position in range(len(df)):
        reasons = []

        for field in required_fields:
            if field not in df.columns:
                reasons.append(f"缺少必填字段列：{field}")
            elif empty_columns[field].iloc[position]:
                reasons.append(f"缺少必填字段：{field}")
            elif field == "feedback_time" and invalid_feedback_time.iloc[position]:
                reasons.append("反馈时间无效")

        if reasons:
            row_reasons[position] = reasons

    duplicate_positions = set()

    if "feedback_id" in df.columns:
        ids = df["feedback_id"]
        has_id = ids.notna() & ids.astype(str).str.strip().ne("")

        id_positions = [
            i for i in range(len(df)) if has_id.iloc[i]
        ]

        if id_positions:
            duplicate_mask = ids.iloc[id_positions].duplicated(keep="first")
            duplicate_positions.update(
                position
                for position, is_duplicate in zip(id_positions, duplicate_mask)
                if is_duplicate
            )

        missing_id_positions = [
            i for i in range(len(df)) if not has_id.iloc[i]
        ]
    else:
        missing_id_positions = list(range(len(df)))

    fallback_fields = ["uid", "feedback_time", "description"]

    if all(field in df.columns for field in fallback_fields):
        fallback_df = df.iloc[missing_id_positions][fallback_fields].copy()

        complete_key = pd.Series(True, index=fallback_df.index)
        for field in fallback_fields:
            complete_key &= (
                fallback_df[field].notna()
                & fallback_df[field].astype(str).str.strip().ne("")
            )

        complete_positions = [
            position
            for position, is_complete in zip(
                missing_id_positions, complete_key
            )
            if is_complete
        ]

        if complete_positions:
            complete_df = df.iloc[complete_positions][fallback_fields]
            duplicate_mask = complete_df.duplicated(keep="first")
            duplicate_positions.update(
                position
                for position, is_duplicate in zip(
                    complete_positions, duplicate_mask
                )
                if is_duplicate
            )

    for position in duplicate_positions:
        row_reasons.setdefault(position, []).append("记录重复")

    for position, reasons in sorted(row_reasons.items()):
        row_issues.append({
            "row": position + 1,
            "reasons": "；".join(reasons),
        })

    return {
        "issues": issues,
        "row_issues": row_issues,
        "duplicate_count": len(duplicate_positions),
        "record_count": len(df),
        "valid_count": len(df) - len(row_reasons),
    }
