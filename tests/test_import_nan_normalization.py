import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from database.feedback_repository import FeedbackRepository
from database.migration import initialize_database
from database.utils import json_dumps
from services.data_import_service import (
    build_import_records,
    build_standard_dataframe,
    detect_field_mapping,
    read_uploaded_file,
    validate_dataframe,
)


class _UploadedFile:
    def __init__(self, content: bytes, name: str):
        self._content = content
        self.name = name

    def getvalue(self):
        return self._content


def _make_repo(tmp_path, filename="test.db"):
    db_path = Path(tmp_path) / filename
    initialize_database(db_path)
    repo = FeedbackRepository(db_path)
    workspace_id = repo.get_default_workspace()
    source_id = repo.get_or_create_file_source(
        workspace_id,
        source_type="csv",
    )
    return repo, workspace_id, source_id


def _csv_bytes(optional_field=None):
    headers = [
        "feedback_id",
        "uid",
        "feedback_time",
        "source",
        "product",
        "version",
        "platform",
        "device",
        "error_code",
        "description",
        "note",
    ]
    values = {
        "feedback_id": "F001",
        "uid": "U001",
        "feedback_time": "2026-11-10 10:00:00",
        "source": "App",
        "product": "WorkMesh",
        "version": "1.2.0",
        "platform": "iOS",
        "device": "iPhone 15",
        "error_code": "E1001",
        "description": "登录失败",
        "note": "raw note",
    }
    if optional_field:
        values[optional_field] = ""

    line = ",".join(values[header] for header in headers)
    return (",".join(headers) + "\n" + line + "\n").encode("utf-8")


def _csv_records(tmp_path, optional_field=None):
    repo, workspace_id, source_id = _make_repo(
        tmp_path,
        filename=f"csv_{optional_field or 'normal'}.db",
    )
    uploaded = _UploadedFile(
        _csv_bytes(optional_field),
        "feedback.csv",
    )
    raw_df = read_uploaded_file(uploaded)
    mapping = detect_field_mapping(raw_df.columns)
    standard_df = build_standard_dataframe(raw_df, mapping)

    validation = validate_dataframe(standard_df)
    records = build_import_records(
        raw_df,
        standard_df,
        mapping,
        row_numbers=[1],
    )
    return repo, workspace_id, source_id, validation, records


def _base_record(record_id="F001", **overrides):
    record = {
        "feedback_id": record_id,
        "uid": "U001",
        "feedback_time": "2026-11-10T10:00:00Z",
        "source": "App",
        "source_type": "App",
        "product": "WorkMesh",
        "version": "1.2.0",
        "platform": "iOS",
        "device": "iPhone 15",
        "error_code": "E1001",
        "description": "登录失败",
        "status": "new",
        "raw_payload": {},
        "extra_data": {},
    }
    record.update(overrides)
    return record


def test_normal_csv_import_writes_all_rows(tmp_path):
    repo, workspace_id, source_id, validation, records = _csv_records(tmp_path)

    assert validation["issues"] == []
    assert validation["valid_count"] == 1
    assert len(records) == 1
    json_dumps(records[0]["raw_payload"])

    batch_id = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="feedback.csv",
        file_hash="normal-hash",
        total_records=len(records),
    )
    result = repo.bulk_insert_feedback(batch_id, records)

    assert result["status"] == "completed"
    assert result["success_records"] == 1
    assert result["failed_records"] == 0
    assert repo.list_feedback(workspace_id)["total"] == 1
    assert repo.has_imported_file(source_id, "normal-hash") is True


def test_real_non_live_sample_imports_all_100_rows(tmp_path):
    repo, workspace_id, source_id = _make_repo(tmp_path, "real_sample.db")
    sample_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "sample"
        / "feedback_sample_non_live_2026_11.csv"
    )

    uploaded = _UploadedFile(sample_path.read_bytes(), sample_path.name)
    raw_df = read_uploaded_file(uploaded)
    mapping = detect_field_mapping(raw_df.columns)
    standard_df = build_standard_dataframe(raw_df, mapping)
    validation = validate_dataframe(standard_df)

    assert validation["issues"] == []
    assert validation["valid_count"] == 100

    records = build_import_records(
        raw_df,
        standard_df,
        mapping,
        row_numbers=list(range(1, 101)),
    )
    assert len(records) == 100
    assert records[96]["_import_row"] == 97
    assert records[96]["error_code"] is None
    assert records[96]["raw_payload"]["error_code"] is None

    batch_id = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name=sample_path.name,
        file_hash="non-live-sample-hash",
        total_records=100,
    )
    result = repo.bulk_insert_feedback(batch_id, records)

    assert result["status"] == "completed"
    assert result["success_records"] == 100
    assert result["failed_records"] == 0
    assert result["errors"] == []
    assert repo.list_feedback(workspace_id)["total"] == 100
    assert repo.has_imported_file(source_id, "non-live-sample-hash") is True


@pytest.mark.parametrize(
    "optional_field",
    ["error_code", "version", "platform", "device"],
)
def test_empty_optional_fields_do_not_drop_row(tmp_path, optional_field):
    repo, workspace_id, source_id, validation, records = _csv_records(
        tmp_path,
        optional_field=optional_field,
    )

    assert validation["valid_count"] == 1
    record = records[0]
    assert record[optional_field] is None
    assert record["raw_payload"][optional_field] is None
    json_dumps(record["raw_payload"])

    batch_id = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name=f"{optional_field}.csv",
        total_records=1,
    )
    result = repo.bulk_insert_feedback(batch_id, records)

    assert result["success_records"] == 1
    assert result["failed_records"] == 0

    stored = repo.list_feedback(workspace_id)["records"][0]
    raw_payload = json.loads(stored["raw_payload"])
    assert raw_payload[optional_field] is None


def test_nested_dict_nan_is_normalized():
    raw_df = pd.DataFrame([{
        "feedback_id": "F001",
        "uid": "U001",
        "feedback_time": pd.Timestamp("2026-11-10 10:00:00"),
        "source": "App",
        "product": "WorkMesh",
        "description": "嵌套 dict",
        "context": {
            "error": float("nan"),
            "nested": {"latency": np.inf, "empty": pd.NaT},
        },
    }])
    standard_df = build_standard_dataframe(
        raw_df,
        detect_field_mapping(raw_df.columns),
    )

    records = build_import_records(raw_df, standard_df, {})

    assert records[0]["raw_payload"]["context"]["error"] is None
    assert records[0]["raw_payload"]["context"]["nested"]["latency"] is None
    assert records[0]["raw_payload"]["context"]["nested"]["empty"] is None
    assert records[0]["extra_data"]["context"]["error"] is None
    json_dumps(records[0]["raw_payload"])
    json_dumps(records[0]["extra_data"])


def test_nested_list_nan_is_normalized():
    raw_df = pd.DataFrame([{
        "feedback_id": "F001",
        "uid": "U001",
        "feedback_time": pd.Timestamp("2026-11-10 10:00:00"),
        "source": "App",
        "product": "WorkMesh",
        "description": "嵌套 list",
        "metrics": [1.0, float("nan"), np.inf, {"value": pd.NaT}],
    }])
    standard_df = build_standard_dataframe(
        raw_df,
        detect_field_mapping(raw_df.columns),
    )

    records = build_import_records(raw_df, standard_df, {})

    assert records[0]["raw_payload"]["metrics"] == [
        1.0,
        None,
        None,
        {"value": None},
    ]
    assert records[0]["extra_data"]["metrics"][1] is None
    json_dumps(records[0]["raw_payload"])


def test_pandas_numpy_scalars_are_converted_to_native_types():
    raw_df = pd.DataFrame([{
        "feedback_id": np.str_("F001"),
        "uid": np.int64(42),
        "feedback_time": pd.Timestamp("2026-11-10 10:00:00"),
        "source": np.str_("App"),
        "product": np.float64(1.25),
        "version": np.float64(np.nan),
        "platform": pd.NaT,
        "device": np.bool_(True),
        "description": "标量转换",
    }])
    standard_df = build_standard_dataframe(
        raw_df,
        detect_field_mapping(raw_df.columns),
    )

    records = build_import_records(raw_df, standard_df, {})

    record = records[0]
    assert record["feedback_id"] == "F001"
    assert isinstance(record["feedback_id"], str)
    assert record["uid"] == 42
    assert type(record["uid"]) is int
    assert record["product"] == 1.25
    assert type(record["product"]) is float
    assert record["version"] is None
    assert record["platform"] is None
    assert record["device"] is True
    assert type(record["device"]) is bool
    assert record["feedback_time"] == "2026-11-10T10:00:00Z"
    json_dumps(record)


def test_partially_failed_batch_can_retry_without_duplicates(tmp_path):
    repo, workspace_id, source_id = _make_repo(tmp_path, "retry.db")

    records = [
        _base_record("F001"),
        _base_record(
            "F002",
            description="第二条",
            raw_payload="invalid",
            _import_row=2,
        ),
    ]

    first_batch = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="partial.csv",
        file_hash="partial-hash",
        total_records=2,
    )
    first_result = repo.bulk_insert_feedback(first_batch, records)

    assert first_result["status"] == "partially_failed"
    assert first_result["success_records"] == 1
    assert first_result["failed_records"] == 1
    assert repo.has_imported_file(source_id, "partial-hash") is False

    retry_records, previously_imported = repo.filter_new_feedback_records(
        source_id,
        records,
    )
    assert previously_imported == 1
    assert [record["feedback_id"] for record in retry_records] == ["F002"]

    # 模拟用户修正失败行后再次提交。
    retry_records[0]["raw_payload"] = {}

    retry_batch = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="partial.csv",
        file_hash="partial-hash",
        total_records=len(retry_records),
    )
    retry_result = repo.bulk_insert_feedback(retry_batch, retry_records)

    assert retry_result["status"] == "completed"
    assert retry_result["success_records"] == 1
    assert retry_result["failed_records"] == 0
    assert repo.list_feedback(workspace_id)["total"] == 2
    assert repo.has_imported_file(source_id, "partial-hash") is True


def test_retry_without_feedback_id_uses_fallback_key(tmp_path):
    repo, workspace_id, source_id = _make_repo(tmp_path, "retry_no_id.db")

    records = [
        _base_record(
            None,
            feedback_id=None,
            uid="U-NO-ID",
            description="无外部 ID 的记录",
        ),
        _base_record(
            None,
            feedback_id=None,
            uid="U-FAIL",
            description="无外部 ID 的失败记录",
            raw_payload="invalid",
        ),
    ]

    first_batch = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="no_id.csv",
        file_hash="no-id-hash",
        total_records=2,
    )
    first_result = repo.bulk_insert_feedback(first_batch, records)
    assert first_result["status"] == "partially_failed"

    retry_records, previously_imported = repo.filter_new_feedback_records(
        source_id,
        records,
    )
    assert previously_imported == 1
    assert len(retry_records) == 1
    assert retry_records[0]["uid"] == "U-FAIL"

    retry_records[0]["raw_payload"] = {}

    retry_batch = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="no_id.csv",
        file_hash="no-id-hash",
        total_records=1,
    )
    retry_result = repo.bulk_insert_feedback(retry_batch, retry_records)

    assert retry_result["status"] == "completed"
    assert repo.list_feedback(workspace_id)["total"] == 2


def test_failed_batch_allows_retry_but_completed_blocks_it(tmp_path):
    repo, workspace_id, source_id = _make_repo(tmp_path, "status_semantics.db")

    failed_batch = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="failed.csv",
        file_hash="failed-hash",
        total_records=1,
    )
    failed_result = repo.bulk_insert_feedback(
        failed_batch,
        [_base_record(raw_payload="invalid")],
    )

    assert failed_result["status"] == "failed"
    assert repo.has_imported_file(source_id, "failed-hash") is False

    completed_batch = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="completed.csv",
        file_hash="completed-hash",
        total_records=1,
    )
    completed_result = repo.bulk_insert_feedback(
        completed_batch,
        [_base_record("F-COMPLETED")],
    )

    assert completed_result["status"] == "completed"
    assert repo.has_imported_file(source_id, "completed-hash") is True


def test_completed_empty_batch_does_not_block_retry(tmp_path):
    repo, workspace_id, source_id = _make_repo(tmp_path, "empty_completed.db")

    batch_id = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="empty.csv",
        file_hash="empty-hash",
        total_records=0,
    )
    result = repo.bulk_insert_feedback(batch_id, [])

    assert result["status"] == "completed"
    assert result["success_records"] == 0
    assert repo.has_imported_file(source_id, "empty-hash") is False


def test_failed_row_keeps_original_row_number_and_reason(tmp_path):
    repo, workspace_id, source_id = _make_repo(tmp_path, "failed_row.db")

    batch_id = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="failed_row.csv",
        total_records=1,
    )
    result = repo.bulk_insert_feedback(
        batch_id,
        [
            _base_record(
                "F-BAD",
                raw_payload={"bad": float("nan")},
                extra_data="invalid",
                _import_row=7,
            )
        ],
    )

    assert result["status"] == "failed"
    errors = repo.get_batch_errors(batch_id)
    assert len(errors) == 1
    assert errors[0]["row"] == 7
    assert "extra_data 必须是字典" in errors[0]["error"]
    assert errors[0]["feedback_id"] == "F-BAD"
    assert errors[0]["raw_payload"] == {"bad": None}


def test_same_uid_without_time_and_description_is_not_deduplicated(tmp_path):
    repo, _, source_id = _make_repo(tmp_path, "same_uid_no_key.db")

    records = [
        _base_record(
            None,
            feedback_id=None,
            uid="U-SAME",
            feedback_time=None,
            description=None,
        ),
        _base_record(
            None,
            feedback_id=None,
            uid="U-SAME",
            feedback_time=None,
            description=None,
        ),
    ]

    new_records, skipped = repo.filter_new_feedback_records(
        source_id,
        records,
    )

    assert len(new_records) == 2
    assert skipped == 0


def test_same_time_different_description_is_not_deduplicated(tmp_path):
    repo, _, source_id = _make_repo(tmp_path, "same_time_diff_desc.db")

    records = [
        _base_record(
            None,
            feedback_id=None,
            uid="U001",
            feedback_time="2026-11-10T10:00:00Z",
            description="描述 A",
        ),
        _base_record(
            None,
            feedback_id=None,
            uid="U001",
            feedback_time="2026-11-10T10:00:00Z",
            description="描述 B",
        ),
    ]

    new_records, skipped = repo.filter_new_feedback_records(
        source_id,
        records,
    )

    assert len(new_records) == 2
    assert skipped == 0


def test_different_uid_with_same_time_and_description_is_not_deduplicated(
    tmp_path,
):
    repo, _, source_id = _make_repo(tmp_path, "diff_uid_same_content.db")

    records = [
        _base_record(
            None,
            feedback_id=None,
            uid="U001",
            feedback_time="2026-11-10T10:00:00Z",
            description="相同描述",
        ),
        _base_record(
            None,
            feedback_id=None,
            uid="U002",
            feedback_time="2026-11-10T10:00:00Z",
            description="相同描述",
        ),
    ]

    new_records, skipped = repo.filter_new_feedback_records(
        source_id,
        records,
    )

    assert len(new_records) == 2
    assert skipped == 0


def test_duplicate_feedback_id_in_same_batch_keeps_one(tmp_path):
    repo, _, source_id = _make_repo(tmp_path, "same_batch_external.db")

    records = [
        _base_record("F-DUP", description="第一条"),
        _base_record("F-DUP", description="第二条"),
    ]

    new_records, skipped = repo.filter_new_feedback_records(
        source_id,
        records,
    )

    assert len(new_records) == 1
    assert new_records[0]["description"] == "第一条"
    assert skipped == 1


def test_duplicate_fallback_key_in_same_batch_keeps_one(tmp_path):
    repo, _, source_id = _make_repo(tmp_path, "same_batch_fallback.db")

    records = [
        _base_record(
            None,
            feedback_id=None,
            uid="U001",
            feedback_time="2026-11-10T10:00:00Z",
            description="同一条反馈",
        ),
        _base_record(
            None,
            feedback_id=None,
            uid="U001",
            feedback_time="2026-11-10T10:00:00Z",
            description="同一条反馈",
        ),
    ]

    new_records, skipped = repo.filter_new_feedback_records(
        source_id,
        records,
    )

    assert len(new_records) == 1
    assert skipped == 1


def test_blank_time_or_description_never_builds_fallback_key(tmp_path):
    repo, _, source_id = _make_repo(tmp_path, "blank_key.db")

    records = [
        _base_record(
            None,
            feedback_id=None,
            uid="U-SAME",
            feedback_time="",
            description="   ",
        ),
        _base_record(
            None,
            feedback_id=None,
            uid="U-SAME",
            feedback_time="   ",
            description="",
        ),
    ]

    new_records, skipped = repo.filter_new_feedback_records(
        source_id,
        records,
    )

    assert len(new_records) == 2
    assert skipped == 0


def test_database_existing_record_is_still_skipped_on_retry(tmp_path):
    repo, workspace_id, source_id = _make_repo(tmp_path, "existing_retry.db")

    batch_id = repo.create_import_batch(
        workspace_id=workspace_id,
        source_id=source_id,
        file_name="existing.csv",
        total_records=1,
    )
    repo.bulk_insert_feedback(batch_id, [_base_record("F-EXISTING")])

    records = [
        _base_record("F-EXISTING", description="已存在，重试"),
        _base_record(
            "F-NEW",
            uid="U-NEW",
            feedback_time="2026-11-10T11:00:00Z",
            description="新记录",
        ),
    ]

    new_records, skipped = repo.filter_new_feedback_records(
        source_id,
        records,
    )

    assert [record["feedback_id"] for record in new_records] == ["F-NEW"]
    assert skipped == 1
