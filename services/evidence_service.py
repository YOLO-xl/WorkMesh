"""WorkMesh 证据关联服务。

把「一条用户反馈」变成可追溯的问题上下文：
1. 反馈记录转换为 Evidence 锚点。
2. 从已接入的外部证据和其他反馈中粗筛候选。
3. 用可解释规则精算关联分数。
4. 把关联结果落库，并区分「已验证事实」与「待验证假设」。
"""

from datetime import timedelta
from typing import Iterable, Optional

from core.correlation import (
    DEFAULT_MIN_SCORE,
    DEFAULT_WINDOW_MINUTES,
    CorrelationResult,
    candidate_filter,
    correlate,
)
from core.evidence import Evidence
from database.evidence_repository import EvidenceRepository
from database.feedback_repository import FeedbackRepository


IDENTITY_LABELS = {
    "trace_id": "Trace ID",
    "request_id": "Request ID",
    "uid": "UID",
    "error_code": "错误码",
    "service": "服务/产品",
}

TYPE_LABELS = {
    "feedback": "用户反馈",
    "log": "日志",
    "metric": "指标",
    "trace": "链路追踪",
    "change": "变更记录",
    "note": "人工备注",
}


class EvidenceService:
    """证据接入与关联编排。"""

    def __init__(
        self,
        evidence_repository: Optional[EvidenceRepository] = None,
        feedback_repository: Optional[FeedbackRepository] = None,
        db_path=None,
    ):
        self.feedback_repository = (
            feedback_repository or FeedbackRepository(db_path)
        )
        self.evidence_repository = (
            evidence_repository or EvidenceRepository(db_path)
        )

    # ------------------------------------------------------------------
    # 接入
    # ------------------------------------------------------------------
    def ingest_connector_records(
        self,
        workspace_id: str,
        records: Iterable[dict],
        source_name: str,
        source_type: str = "log",
        connector_type: Optional[str] = None,
    ) -> dict:
        """写入连接器标准化后的证据记录。"""

        records = list(records)

        source_id = self.evidence_repository.get_or_create_source(
            workspace_id=workspace_id,
            name=source_name,
            source_type=source_type,
            connector_type=connector_type,
        )

        evidences = []

        for record in records:
            evidence = Evidence.from_connector_record(record)
            evidence.source_id = source_id
            if evidence.connector_type is None:
                evidence.connector_type = connector_type
            evidences.append(evidence)

        result = self.evidence_repository.bulk_insert_evidence(
            workspace_id=workspace_id,
            source_id=source_id,
            evidences=evidences,
        )

        result["source_id"] = source_id
        result["source_name"] = source_name

        return result

    # ------------------------------------------------------------------
    # 关联
    # ------------------------------------------------------------------
    def build_context(
        self,
        workspace_id: str,
        feedback_id: str,
        window_minutes: int = DEFAULT_WINDOW_MINUTES,
        min_score: float = DEFAULT_MIN_SCORE,
        include_related_feedback: bool = True,
        persist: bool = True,
        limit: int = 50,
    ) -> dict:
        """为一条反馈生成证据上下文。

        返回结构：
        - anchor：反馈锚点证据
        - matches：按分数排序的关联证据
        - facts：由标识一致直接得出的事实
        - hypotheses：需要人工或日志内容进一步确认的假设
        """

        record = self.feedback_repository.get_feedback_record(
            workspace_id, feedback_id
        )

        if record is None:
            raise LookupError(f"反馈记录不存在：{feedback_id}")

        anchor = Evidence.from_feedback_record(record)

        window = timedelta(minutes=max(int(window_minutes), 1))
        anchor_filter = candidate_filter(anchor, window)

        candidates = self.evidence_repository.find_candidates(
            workspace_id=workspace_id,
            anchor_filter=anchor_filter,
            limit=200,
        )

        related_feedback = []

        if include_related_feedback:
            related_rows = self.feedback_repository.find_related_feedback(
                workspace_id=workspace_id,
                anchor_id=feedback_id,
                uid=anchor.uid,
                error_code=anchor.error_code,
                start_time=anchor_filter.get("start"),
                end_time=anchor_filter.get("end"),
                limit=20,
            )
            related_feedback = [
                Evidence.from_feedback_record(row) for row in related_rows
            ]

        results = correlate(
            anchor,
            list(candidates) + related_feedback,
            window=window,
            min_score=min_score,
            limit=limit,
        )

        matches = []

        for result in results:
            item = result.to_dict()
            item["evidence_id"] = result.evidence.evidence_id
            item["persisted"] = bool(result.evidence.evidence_id)
            matches.append(item)

        if persist:
            persisted_links = [
                {
                    "evidence_id": result.evidence.evidence_id,
                    "score": result.score,
                    "strength": result.strength,
                    "matched_keys": result.matched_keys,
                    "reasons": result.reasons,
                }
                for result in results
                if result.evidence.evidence_id
            ]

            self.evidence_repository.replace_links(
                workspace_id=workspace_id,
                anchor_type="feedback",
                anchor_id=feedback_id,
                links=persisted_links,
            )

        facts = self._build_facts(anchor, results)
        hypotheses = self._build_hypotheses(
            anchor, results, len(candidates), len(related_feedback), min_score
        )

        return {
            "anchor": anchor,
            "anchor_record": record,
            "matches": matches,
            "facts": facts,
            "hypotheses": hypotheses,
            "window_minutes": window_minutes,
            "min_score": min_score,
            "counts": {
                "candidates": len(candidates),
                "related_feedback": len(related_feedback),
                "matched": len(results),
                "strong": sum(1 for item in results if item.strength == "strong"),
                "medium": sum(1 for item in results if item.strength == "medium"),
                "persisted": sum(1 for item in matches if item["persisted"]),
            },
            "summary": self._build_summary(anchor, results, len(candidates)),
        }

    # ------------------------------------------------------------------
    # 事实与假设
    # ------------------------------------------------------------------
    def _build_facts(
        self,
        anchor: Evidence,
        results: list[CorrelationResult],
    ) -> list[dict]:
        """标识一致属于可验证事实，逐键汇总并保留证据 ID。"""

        facts = []

        for key, label in IDENTITY_LABELS.items():
            value = getattr(anchor, key)
            if not value:
                continue

            hits = [
                result
                for result in results
                if key in result.matched_keys
            ]

            if not hits:
                continue

            facts.append({
                "key": key,
                "label": label,
                "value": value,
                "evidence_count": len(hits),
                "evidence_ids": [
                    result.evidence.evidence_id
                    or result.evidence.record_id
                    or "—"
                    for result in hits
                ],
                "statement": (
                    f"{label}「{value}」同时出现在 {len(hits)} 条关联证据中"
                ),
                "status": "已验证事实",
            })

        return facts

    def _build_hypotheses(
        self,
        anchor: Evidence,
        results: list[CorrelationResult],
        candidate_count: int,
        related_feedback_count: int,
        min_score: float,
    ) -> list[dict]:
        """只给出可被证据支撑的假设，并明确标注待验证。"""

        hypotheses = []

        if not results:
            hypotheses.append({
                "statement": (
                    "当前时间窗口内没有找到可关联的证据。"
                    "建议先接入对应服务的日志，或扩大时间窗口后重试。"
                ),
                "basis": [],
                "status": "待验证",
            })
            return hypotheses

        log_results = [
            item for item in results
            if item.evidence.evidence_type != "feedback"
        ]

        if anchor.error_code and log_results:
            same_code = [
                item for item in log_results
                if item.evidence.error_code == anchor.error_code
            ]

            if same_code:
                hypotheses.append({
                    "statement": (
                        f"错误码「{anchor.error_code}」在窗口内被 "
                        f"{len(same_code)} 条日志命中，"
                        "故障方向可能一致；具体根因仍需结合日志内容确认。"
                    ),
                    "basis": [
                        item.evidence.evidence_id or item.evidence.record_id
                        for item in same_code
                    ],
                    "status": "待验证",
                })

        if related_feedback_count > 0:
            hypotheses.append({
                "statement": (
                    f"同一 UID/错误码在窗口内还有 {related_feedback_count} 条反馈，"
                    "可能是同一用户的连续问题，也可能是同类问题开始扩散。"
                ),
                "basis": [],
                "status": "待验证",
            })

        if not any(item.strength == "strong" for item in results):
            hypotheses.append({
                "statement": (
                    f"关联证据最高分低于强关联阈值，"
                    f"仅为参考证据（当前阈值 {min_score} 分）。"
                    "建议补充 Trace ID / Request ID 后再判定。"
                ),
                "basis": [],
                "status": "证据不足",
            })

        if candidate_count == 0:
            hypotheses.append({
                "statement": (
                    "该工作空间尚未接入外部证据，"
                    "当前结论只基于反馈数据本身。"
                ),
                "basis": [],
                "status": "待验证",
            })

        return hypotheses

    def _build_summary(
        self,
        anchor: Evidence,
        results: list[CorrelationResult],
        candidate_count: int,
    ) -> str:
        if not results:
            return (
                f"反馈「{anchor.message or anchor.record_id}」在候选 "
                f"{candidate_count} 条证据中未发现可关联项。"
            )

        strong = sum(1 for item in results if item.strength == "strong")
        top = results[0]

        return (
            f"共找到 {len(results)} 条关联证据，其中强关联 {strong} 条；"
            f"最高分 {top.score} 分，来自 "
            f"{TYPE_LABELS.get(top.evidence.evidence_type, top.evidence.evidence_type)}"
            f"（{top.evidence.display_label()[:60]}）。"
        )
