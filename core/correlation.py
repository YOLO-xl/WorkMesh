"""WorkMesh 证据关联引擎。

关联规则是显式、可解释的加法打分，不做黑盒相似度：

- Trace ID / Request ID 一致：强标识，可跨时间窗口直接关联。
- UID / 错误码 / 服务名一致：需要落在同一时间窗口内才算证据。
- 时间接近：仅作为加分项，绝不单独构成关联。

输出包含分数、命中键和中文理由，供界面直接展示，也便于后续 AI 引用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Iterable, Optional

from core.evidence import (
    Evidence,
    format_timestamp,
    time_delta_seconds,
    within_window,
)


DEFAULT_WINDOW_MINUTES = 30
NEAR_WINDOW_MINUTES = 5
DEFAULT_MIN_SCORE = 30.0

# 只有这些键命中，才允许产生关联。
IDENTITY_KEYS = ("trace_id", "request_id", "uid")

# 指标、变更、备注这类证据通常没有用户标识，
# 与锚点同服务、同时间窗口时按「上下文证据」收录，明确不构成归因。
CONTEXT_EVIDENCE_TYPES = ("metric", "change", "note")


def _strength(matched_keys: list[str]) -> str:
    """按命中键判定关联强度。

    不用纯分数阈值：反馈锚点通常没有 Trace ID，
    但「同一用户 + 同一错误码 + 同一时间窗口」本身已经是很强的证据。
    """

    matched = set(matched_keys)

    if "trace_id" in matched or "request_id" in matched:
        return "strong"

    if "uid" in matched and "error_code" in matched:
        return "strong"

    if "uid" in matched or "error_code" in matched:
        return "medium"

    return "weak"



@dataclass(frozen=True)
class CorrelationWeights:
    """各关联键的权重，可按业务场景覆盖。"""

    trace_id: float = 45.0
    request_id: float = 40.0
    uid: float = 25.0
    error_code: float = 20.0
    service: float = 10.0
    time_proximity: float = 10.0
    # 上下文证据（指标/变更/备注）的基准分，保证同服务同窗口即可入选。
    context: float = 15.0


@dataclass
class CorrelationResult:
    """一条候选证据与锚点证据的关联结果。"""

    evidence: Evidence
    score: float
    matched_keys: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    strength: str = "weak"
    time_delta_seconds: Optional[float] = None
    relation: str = "correlates"

    def to_dict(self) -> dict:
        return {
            "evidence": self.evidence,
            "score": self.score,
            "matched_keys": self.matched_keys,
            "reasons": self.reasons,
            "strength": self.strength,
            "time_delta_seconds": self.time_delta_seconds,
            "relation": self.relation,
        }


def candidate_filter(
    anchor: Evidence,
    window: timedelta,
) -> dict:
    """构造候选证据的粗筛条件，交给 Repository 做 SQL 查询。

    粗筛故意放宽：只要任一标识命中，或落在时间窗口内即可进入精算，
    避免 SQL 逻辑与打分层重复。
    """

    start = None
    end = None

    if anchor.timestamp is not None:
        start = format_timestamp(anchor.timestamp - window)
        end = format_timestamp(anchor.timestamp + window)

    result = {
        name: getattr(anchor, name)
        for name in IDENTITY_KEYS + ("error_code", "service")
        if getattr(anchor, name)
    }

    result["start"] = start
    result["end"] = end
    result["anchor_record_id"] = anchor.record_id
    result["anchor_evidence_id"] = anchor.evidence_id

    return result


def _same_record(anchor: Evidence, candidate: Evidence) -> bool:
    if anchor is candidate:
        return True

    if anchor.evidence_id and candidate.evidence_id:
        return anchor.evidence_id == candidate.evidence_id

    return (
        anchor.evidence_type == candidate.evidence_type
        and anchor.record_id is not None
        and anchor.record_id == candidate.record_id
        and anchor.source_id == candidate.source_id
    )


def score_pair(
    anchor: Evidence,
    candidate: Evidence,
    *,
    weights: CorrelationWeights = CorrelationWeights(),
    window: timedelta = timedelta(minutes=DEFAULT_WINDOW_MINUTES),
) -> Optional[CorrelationResult]:
    """计算两条证据的关联分数；不构成关联时返回 None。"""

    if _same_record(anchor, candidate):
        return None

    delta = time_delta_seconds(anchor.timestamp, candidate.timestamp)
    in_window = within_window(anchor.timestamp, candidate.timestamp, window)

    matched_keys: list[str] = []
    reasons: list[str] = []
    score = 0.0

    # 强标识：时间窗口不阻断，但超出时在理由中标注。
    if anchor.trace_id and anchor.trace_id == candidate.trace_id:
        matched_keys.append("trace_id")
        score += weights.trace_id
        reasons.append(f"Trace ID 一致：{anchor.trace_id}")
        if not in_window:
            reasons.append("时间超出关联窗口，但 Trace ID 一致，仍视为同一链路")

    if anchor.request_id and anchor.request_id == candidate.request_id:
        matched_keys.append("request_id")
        score += weights.request_id
        reasons.append(f"Request ID 一致：{anchor.request_id}")

        if not in_window:
            reasons.append("时间超出关联窗口，但 Request ID 一致，仍视为同一请求")

    # 弱标识：必须落在同一时间窗口内。
    if in_window and anchor.uid and anchor.uid == candidate.uid:
        matched_keys.append("uid")
        score += weights.uid
        reasons.append(f"UID 一致：{anchor.uid}")

    if in_window and anchor.error_code and anchor.error_code == candidate.error_code:
        matched_keys.append("error_code")
        score += weights.error_code
        reasons.append(f"错误码一致：{anchor.error_code}")

    if in_window and anchor.service and anchor.service == candidate.service:
        matched_keys.append("service")
        score += weights.service
        reasons.append(f"服务/产品一致：{anchor.service}")

    near_window = min(window, timedelta(minutes=NEAR_WINDOW_MINUTES))
    if delta is not None and delta <= near_window.total_seconds():
        score += weights.time_proximity
        reasons.append(f"时间接近：相差 {int(delta)} 秒")
        if "time" not in matched_keys:
            matched_keys.append("time")

    if anchor.timestamp is None or candidate.timestamp is None:
        reasons.append("部分证据缺少时间戳，时间维度未参与判定")

    # 必须至少有一个真正的身份标识，时间接近不能单独成立。
    has_identity = any(key in matched_keys for key in IDENTITY_KEYS)

    relation = "correlates"
    strength = _strength(matched_keys) if has_identity else "weak"

    if not has_identity:
        # 指标/变更/备注没有用户标识，同服务且同窗口时作为上下文证据，
        # 这是「相关线索」而不是「同一问题的证据」，因此不参与事实判定。
        is_context = (
            candidate.evidence_type in CONTEXT_EVIDENCE_TYPES
            and in_window
            and "service" in matched_keys
        )

        if not is_context:
            return None

        relation = "context"
        score += weights.context
        reasons.append(
            "仅为同服务、同时间窗口的上下文证据，不单独构成故障归因"
        )

    score = round(min(score, 100.0), 1)

    return CorrelationResult(
        evidence=candidate,
        score=score,
        matched_keys=matched_keys,
        reasons=reasons,
        strength=strength,
        time_delta_seconds=delta,
        relation=relation,
    )


def correlate(
    anchor: Evidence,
    candidates: Iterable[Evidence],
    *,
    weights: CorrelationWeights = CorrelationWeights(),
    window: timedelta = timedelta(minutes=DEFAULT_WINDOW_MINUTES),
    min_score: float = DEFAULT_MIN_SCORE,
    limit: int = 50,
) -> list[CorrelationResult]:
    """批量关联候选证据，按分数从高到低返回。"""

    results = []

    for candidate in candidates:
        result = score_pair(
            anchor,
            candidate,
            weights=weights,
            window=window,
        )

        if result is None:
            continue

        if result.score < min_score:
            continue

        results.append(result)

    results.sort(
        key=lambda item: (
            -item.score,
            item.time_delta_seconds
            if item.time_delta_seconds is not None
            else float("inf"),
        )
    )

    return results[: max(limit, 0)]
