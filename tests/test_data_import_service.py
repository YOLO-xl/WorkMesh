import pandas as pd

from services.data_import_service import validate_dataframe


def make_dataframe():
    return pd.DataFrame({
        "feedback_id": ["F001", "F002", "F003"],
        "uid": ["U001", "U002", "U003"],
        "feedback_time": pd.to_datetime([
            "2026-11-01 10:00:00",
            "2026-11-01 11:00:00",
            "2026-11-01 12:00:00",
        ]),
        "source": ["App", "Web", "App"],
        "description": ["登录失败", "页面卡顿", "支付异常"],
    })


def test_valid_dataframe():
    result = validate_dataframe(make_dataframe())

    assert result["issues"] == []
    assert result["row_issues"] == []
    assert result["record_count"] == 3
    assert result["valid_count"] == 3


def test_missing_required_values_are_invalid_rows():
    df = make_dataframe()
    df.loc[0, "description"] = ""
    df.loc[1, "feedback_time"] = pd.NaT
    df.loc[2, "source"] = None

    result = validate_dataframe(df)

    assert result["valid_count"] == 0
    assert len(result["row_issues"]) == 3


def test_entire_required_column_empty_blocks_import():
    df = make_dataframe()
    df["source"] = None

    result = validate_dataframe(df)

    assert any("source" in issue for issue in result["issues"])


def test_missing_required_column_does_not_raise_key_error():
    df = make_dataframe().drop(columns=["description"])

    result = validate_dataframe(df)

    assert any("description" in issue for issue in result["issues"])
    assert result["valid_count"] == 0


def test_duplicate_feedback_id_is_counted():
    df = make_dataframe()
    df.loc[2, "feedback_id"] = "F001"

    result = validate_dataframe(df)

    assert result["duplicate_count"] == 1


def test_duplicate_records_without_feedback_id_use_fallback_fields():
    df = make_dataframe()
    df["feedback_id"] = [None, None, "F003"]
    df.loc[1, "uid"] = df.loc[0, "uid"]
    df.loc[1, "feedback_time"] = df.loc[0, "feedback_time"]
    df.loc[1, "description"] = df.loc[0, "description"]

    result = validate_dataframe(df)

    assert result["duplicate_count"] == 1
    assert result["valid_count"] == 2
    assert result["row_issues"] == [
        {"row": 2, "reasons": "记录重复"}
    ]


def test_mixed_feedback_id_and_missing_id_use_separate_duplicate_rules():
    df = make_dataframe()
    df.loc[3] = df.loc[2].copy()
    df["feedback_id"] = ["F001", "F001", None, None]

    df.loc[3, "uid"] = df.loc[2, "uid"]
    df.loc[3, "feedback_time"] = df.loc[2, "feedback_time"]
    df.loc[3, "description"] = df.loc[2, "description"]

    result = validate_dataframe(df)

    assert result["duplicate_count"] == 2
    assert result["valid_count"] == 2
    assert [item["row"] for item in result["row_issues"]] == [2, 4]


def test_duplicate_and_required_field_error_are_counted_once():
    df = make_dataframe()
    df.loc[1, "feedback_id"] = "F001"
    df.loc[1, "description"] = ""

    result = validate_dataframe(df)

    assert result["duplicate_count"] == 1
    assert result["valid_count"] == 2
    assert len(result["row_issues"]) == 1
    assert result["row_issues"][0]["row"] == 2
    assert "缺少必填字段：description" in result["row_issues"][0]["reasons"]
    assert "记录重复" in result["row_issues"][0]["reasons"]
