from datetime import datetime, timedelta, timezone

import pytest

from core.evidence import (
    Evidence,
    format_timestamp,
    parse_timestamp,
    time_delta_seconds,
    within_window,
)


def test_parse_timestamp_accepts_iso_with_z():
    value = parse_timestamp("2026-11-01T09:15:22Z")

    assert value == datetime(2026, 11, 1, 9, 15, 22, tzinfo=timezone.utc)


def test_parse_timestamp_treats_naive_string_as_utc():
    value = parse_timestamp("2026-11-01 09:15:22")

    assert value.tzinfo == timezone.utc
    assert format_timestamp(value) == "2026-11-01T09:15:22Z"


def test_parse_timestamp_handles_epoch_and_invalid_values():
    assert parse_timestamp(1762000000).year == 2025
    assert parse_timestamp("") is None
    assert parse_timestamp("不是时间") is None
    assert parse_timestamp(None) is None


def test_format_timestamp_converts_to_utc():
    beijing = datetime(
        2026, 11, 1, 17, 15, 22,
        tzinfo=timezone.utc,
    ).astimezone()

    assert format_timestamp(beijing) == "2026-11-01T17:15:22Z"


def test_evidence_rejects_unknown_type():
    with pytest.raises(ValueError, match="不支持的证据类型"):
        Evidence(evidence_type="unknown")


def test_from_feedback_record_maps_unified_fields():
    record = {
        "id": "fb-001",
        "uid": "10086231",
        "feedback_time": "2026-11-01T09:15:22Z",
        "product": "直播SDK",
        "version": "8.6.2",
        "platform": "iOS",
        "error_code": "E1001",
        "description": "进入直播间后一直加载无法播放",
        "source_id": "src-001",
        "raw_payload": '{"原始反馈ID": "F001"}',
        "extra_data": '{"设备品牌": "Apple"}',
    }

    evidence = Evidence.from_feedback_record(record)

    assert evidence.evidence_type == "feedback"
    assert evidence.record_id == "fb-001"
    assert evidence.uid == "10086231"
    assert evidence.service == "直播SDK"
    assert evidence.error_code == "E1001"
    assert evidence.message == "进入直播间后一直加载无法播放"
    assert evidence.raw_data == {"原始反馈ID": "F001"}
    assert evidence.attributes["version"] == "8.6.2"
    assert evidence.attributes["extra_data"] == {"设备品牌": "Apple"}


def test_from_feedback_record_reads_trace_id_from_extra_data():
    record = {
        "id": "fb-002",
        "uid": "10086231",
        "feedback_time": "2026-11-01T09:15:22Z",
        "extra_data": '{"traceId": "trace-001", "requestId": "req-001"}',
    }

    evidence = Evidence.from_feedback_record(record)

    assert evidence.trace_id == "trace-001"
    assert evidence.request_id == "req-001"


def test_from_connector_record_keeps_unknown_fields_in_attributes():
    record = {
        "evidence_type": "log",
        "connector_type": "elasticsearch",
        "record_id": "log-001",
        "timestamp": "2026-11-01T09:14:50Z",
        "uid": "10086231",
        "service": "live-service",
        "message": "rtmp connect timeout",
        "host": "node-01",
        "raw_data": {"level": "ERROR"},
    }

    evidence = Evidence.from_connector_record(record)

    assert evidence.evidence_type == "log"
    assert evidence.attributes["host"] == "node-01"
    assert "host" not in evidence.attributes.get("raw_data", {})
    assert evidence.raw_data == {"level": "ERROR"}


def test_from_connector_record_falls_back_to_full_record_as_raw_data():
    record = {"evidence_type": "log", "record_id": "log-002"}

    evidence = Evidence.from_connector_record(record)

    assert evidence.raw_data == record


def test_content_hash_is_stable_and_sensitive_to_content():
    base = Evidence(
        evidence_type="log",
        record_id="log-001",
        timestamp=parse_timestamp("2026-11-01T09:14:50Z"),
        message="rtmp connect timeout",
    )
    same = Evidence(
        evidence_type="log",
        record_id="log-001",
        timestamp=parse_timestamp("2026-11-01T09:14:50Z"),
        message="rtmp connect timeout",
    )
    other = Evidence(
        evidence_type="log",
        record_id="log-001",
        timestamp=parse_timestamp("2026-11-01T09:14:50Z"),
        message="player init failed",
    )

    assert base.content_hash() == same.content_hash()
    assert base.content_hash() != other.content_hash()


def test_identity_keys_skip_empty_values():
    evidence = Evidence(
        evidence_type="log",
        uid="10086231",
        trace_id="trace-001",
        service="live-service",
    )

    assert evidence.identity_keys() == {
        "trace_id": "trace-001",
        "uid": "10086231",
        "service": "live-service",
    }


def test_time_helpers_handle_missing_timestamps():
    start = parse_timestamp("2026-11-01T09:00:00Z")
    end = parse_timestamp("2026-11-01T09:10:00Z")

    assert time_delta_seconds(start, end) == 600
    assert time_delta_seconds(start, None) is None
    assert within_window(start, end, timedelta(minutes=30))
    assert not within_window(
        start, end, timedelta(minutes=5)
    )
    # 时间缺失时不阻断关联
    assert within_window(None, end, timedelta(minutes=5))
