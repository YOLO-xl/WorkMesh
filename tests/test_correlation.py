from datetime import timedelta

from core.correlation import (
    candidate_filter,
    correlate,
    score_pair,
)
from core.evidence import Evidence


def make_feedback(**overrides):
    data = {
        "evidence_type": "feedback",
        "record_id": "fb-001",
        "timestamp": "2026-11-01T09:15:22Z",
        "uid": "10086231",
        "service": "直播SDK",
        "error_code": "E1001",
        "message": "进入直播间后一直加载无法播放",
    }
    data.update(overrides)
    return Evidence.from_dict(data)


def make_log(**overrides):
    data = {
        "evidence_type": "log",
        "record_id": "log-001",
        "timestamp": "2026-11-01T09:14:50Z",
        "uid": "10086231",
        "service": "直播SDK",
        "error_code": "E1001",
        "message": "rtmp connect timeout",
    }
    data.update(overrides)
    return Evidence.from_dict(data)


def test_trace_id_match_is_strong_even_outside_window():
    anchor = make_feedback(trace_id="trace-001")
    candidate = make_log(
        trace_id="trace-001",
        timestamp="2026-11-01T18:00:00Z",
    )

    result = score_pair(anchor, candidate)

    assert result is not None
    assert result.strength == "strong"
    assert "trace_id" in result.matched_keys
    assert any("超出关联窗口" in reason for reason in result.reasons)


def test_uid_and_error_code_in_window_is_strong():
    result = score_pair(make_feedback(), make_log())

    assert result is not None
    assert result.strength == "strong"
    assert set(result.matched_keys) & {"uid", "error_code"} == {
        "uid",
        "error_code",
    }
    assert result.score == 65.0


def test_uid_only_in_window_is_medium():
    result = score_pair(
        make_feedback(),
        make_log(error_code="E2001"),
    )

    assert result is not None
    assert result.strength == "medium"


def test_uid_outside_window_is_not_correlated():
    result = score_pair(
        make_feedback(),
        make_log(
            error_code="E2001",
            timestamp="2026-11-01T12:00:00Z",
        ),
    )

    assert result is None


def test_service_and_time_alone_do_not_correlate():
    result = score_pair(
        make_feedback(),
        make_log(uid="99999", error_code="E2001"),
    )

    assert result is None


def test_score_is_capped_at_100():
    anchor = make_feedback(
        trace_id="trace-001",
        request_id="req-001",
    )
    candidate = make_log(
        trace_id="trace-001",
        request_id="req-001",
        timestamp="2026-11-01T09:15:20Z",
    )

    result = score_pair(anchor, candidate)

    assert result.score == 100.0
    assert {"trace_id", "request_id"} <= set(result.matched_keys)


def test_identical_evidence_is_skipped():
    anchor = make_feedback()

    assert score_pair(anchor, anchor) is None


def test_correlate_filters_by_score_and_orders_results():
    anchor = make_feedback()

    candidates = [
        make_log(record_id="log-strong"),
        make_log(record_id="log-uid-only", error_code="E2001"),
        make_log(
            record_id="log-outside",
            error_code="E2001",
            timestamp="2026-11-01T12:00:00Z",
        ),
    ]

    results = correlate(anchor, candidates, min_score=30)

    assert [item.evidence.record_id for item in results] == [
        "log-strong",
        "log-uid-only",
    ]
    assert results[0].score > results[1].score


def test_correlate_respects_limit():
    anchor = make_feedback()
    candidates = [
        make_log(record_id=f"log-{index}") for index in range(5)
    ]

    results = correlate(anchor, candidates, limit=2)

    assert len(results) == 2


def test_candidate_filter_builds_time_window_and_keys():
    anchor = make_feedback(trace_id="trace-001")

    result = candidate_filter(anchor, timedelta(minutes=30))

    assert result["trace_id"] == "trace-001"
    assert result["uid"] == "10086231"
    assert result["error_code"] == "E1001"
    assert result["start"] == "2026-11-01T08:45:22Z"
    assert result["end"] == "2026-11-01T09:45:22Z"
    assert result["anchor_record_id"] == "fb-001"


def test_candidate_filter_without_timestamp_has_no_range():
    anchor = make_feedback(timestamp=None)

    result = candidate_filter(anchor, timedelta(minutes=30))

    assert result["start"] is None
    assert result["end"] is None
