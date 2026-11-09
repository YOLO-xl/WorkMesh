"""故障排查流程服务。

把一条反馈串成可复核的五段链路：

1. 反馈接入：反馈本身作为锚点，保留原始字段。
2. 多系统查询：本地反馈、本地证据与可选外部连接器统一检索。
3. 证据关联：按 Trace / Request / UID / 错误码 / 服务 / 时间窗口打分。
4. 场景判定：执行配置化排查步骤与规则。
5. 结论与追溯：事实、假设、动作和原始证据逐条对应。

服务只编排现有能力，不重复实现查询、关联或场景执行。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from core.evidence import Evidence, format_timestamp
from database.evidence_repository import EvidenceRepository
from database.feedback_repository import FeedbackRepository
from services.diagnosis_service import DiagnosisService
from services.evidence_service import EvidenceService, TYPE_LABELS
from services.multi_source_query_service import MultiSourceQueryService


STATUS_LABELS = {
    "done": "完成",
    "empty": "无数据",
    "partial": "部分完成",
    "blocked": "阻塞",
    "limited": "有边界",
    "none": "未命中场景",
}


class TroubleshootingService:
    """端到端故障排查编排。"""

    def __init__(
        self,
        diagnosis_service: Optional[DiagnosisService] = None,
        query_service: Optional[MultiSourceQueryService] = None,
        feedback_repository: Optional[FeedbackRepository] = None,
        evidence_repository: Optional[EvidenceRepository] = None,
        db_path=None,
    ):
        self.feedback_repository = (
            feedback_repository or FeedbackRepository(db_path)
        )
        self.evidence_repository = (
            evidence_repository or EvidenceRepository(db_path)
        )

        self.evidence_service = EvidenceService(
            evidence_repository=self.evidence_repository,
            feedback_repository=self.feedback_repository,
            db_path=db_path,
        )
        self.diagnosis_service = diagnosis_service or DiagnosisService(
            evidence_service=self.evidence_service,
            db_path=db_path,
        )
        self.query_service = query_service or MultiSourceQueryService(
            feedback_repository=self.feedback_repository,
            evidence_repository=self.evidence_repository,
            db_path=db_path,
        )

    # ------------------------------------------------------------------
    # 样例
    # ------------------------------------------------------------------
    def find_live_sample(self, workspace_id: str) -> Optional[dict]:
        """找「进入直播间失败」的内置样例，优先 F001。"""

        records = self.feedback_repository.list_feedback(
            workspace_id, page=1, page_size=500
        )["records"]

        for record in records:
            if str(record.get("external_id") or "") == "F001":
                return record

        for record in records:
            if (
                record.get("product") == "直播SDK"
                and record.get("error_code") == "E1001"
                and "进入直播间" in str(record.get("description") or "")
            ):
                return record

        return None

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def run(
        self,
        workspace_id: str,
        feedback_id: str,
        window_minutes: int = 30,
        min_score: float = 30.0,
        manual_answers: Optional[dict[str, Any]] = None,
        external_sources: Optional[list[dict]] = None,
    ) -> dict:
        diagnosis = self.diagnosis_service.build(
            workspace_id=workspace_id,
            feedback_id=feedback_id,
            window_minutes=window_minutes,
            min_score=min_score,
            use_llm=False,
            manual_answers=manual_answers or {},
        )

        context = diagnosis["context"]
        anchor: Evidence = context["anchor"]
        scenario = diagnosis.get("scenario")

        query_filters = self._query_filters(anchor, window_minutes)
        query_result = self.query_service.query(
            workspace_id,
            filters=query_filters,
            external_sources=external_sources,
        )

        query_hits = self.query_service.associate(
            query_result["hits"],
            anchor,
            window_minutes=window_minutes,
            min_score=min_score,
        )

        chain = self._build_chain(workspace_id, context, scenario)
        stages = self._build_stages(
            anchor=anchor,
            context=context,
            query_result=query_result,
            query_hits=query_hits,
            scenario=scenario,
            diagnosis=diagnosis,
        )

        return {
            "anchor": anchor.to_dict(),
            "anchor_record": context.get("anchor_record"),
            "query": {
                **query_result,
                "hits": query_hits,
            },
            "context": context,
            "diagnosis": diagnosis,
            "scenario": scenario,
            "chain": chain,
            "stages": stages,
            "traceability": self._traceability(chain, scenario),
            "markdown": diagnosis.get("markdown") or "",
        }

    def _query_filters(
        self,
        anchor: Evidence,
        window_minutes: int,
    ) -> dict:
        start = end = None
        if anchor.timestamp:
            start = format_timestamp(
                anchor.timestamp - timedelta(minutes=max(window_minutes, 1))
            )
            end = format_timestamp(
                anchor.timestamp + timedelta(minutes=max(window_minutes, 1))
            )

        return {
            "products": [anchor.service] if anchor.service else [],
            "error_codes": [anchor.error_code] if anchor.error_code else [],
            "uid": anchor.uid,
            "service": anchor.service,
            "start": start,
            "end": end,
            "evidence_types": ["log", "metric", "trace", "change", "note"],
            "limit": 200,
        }

    def _build_chain(
        self,
        workspace_id: str,
        context: dict,
        scenario: Optional[dict],
    ) -> list[dict]:
        anchor: Evidence = context["anchor"]
        chain = [self._chain_item(
            workspace_id,
            evidence=anchor,
            role="anchor",
            score=None,
            strength="anchor",
            relation="anchor",
            matched_keys=[],
            reasons=["用户反馈作为排查锚点"],
            raw=context.get("anchor_record") or anchor.raw_data,
        )]

        for match in context.get("matches") or []:
            evidence = match["evidence"]
            chain.append(self._chain_item(
                workspace_id,
                evidence=evidence,
                role="evidence",
                score=match.get("score"),
                strength=match.get("strength") or "weak",
                relation=match.get("relation") or "correlates",
                matched_keys=match.get("matched_keys") or [],
                reasons=match.get("reasons") or [],
                raw=evidence.raw_data or {},
            ))

        return chain

    def _chain_item(
        self,
        workspace_id: str,
        evidence: Evidence,
        role: str,
        score: Optional[float],
        strength: str,
        relation: str,
        matched_keys: list[str],
        reasons: list[str],
        raw: Any,
    ) -> dict:
        source = None
        if evidence.source_id:
            source = self.evidence_repository.get_source(
                workspace_id, evidence.source_id
            )

        return {
            "role": role,
            "evidence_id": evidence.evidence_id,
            "record_id": evidence.record_id,
            "source_id": evidence.source_id,
            "source_name": (
                source.get("name") if source
                else ("用户反馈" if evidence.evidence_type == "feedback" else "—")
            ),
            "source_type": (
                source.get("source_type") if source else evidence.evidence_type
            ),
            "connector_type": evidence.connector_type,
            "type": evidence.evidence_type,
            "type_label": TYPE_LABELS.get(
                evidence.evidence_type, evidence.evidence_type
            ),
            "timestamp": format_timestamp(evidence.timestamp),
            "uid": evidence.uid,
            "request_id": evidence.request_id,
            "trace_id": evidence.trace_id,
            "service": evidence.service,
            "level": evidence.level,
            "error_code": evidence.error_code,
            "title": evidence.title,
            "message": evidence.message,
            "summary": evidence.display_label(),
            "score": score,
            "strength": strength,
            "relation": relation,
            "matched_keys": matched_keys,
            "reasons": reasons,
            "raw": raw or {},
            "traceable": bool(evidence.evidence_id or raw),
        }

    # ------------------------------------------------------------------
    # 阶段
    # ------------------------------------------------------------------
    def _build_stages(
        self,
        anchor: Evidence,
        context: dict,
        query_result: dict,
        query_hits: list[dict],
        scenario: Optional[dict],
        diagnosis: dict,
    ) -> list[dict]:
        counts = context.get("counts") or {}

        query_sources = query_result.get("sources") or []
        unavailable = [
            item for item in query_sources if not item.get("available")
        ]
        query_status = "done" if query_hits else "empty"
        if unavailable and query_hits:
            query_status = "partial"

        correlation_status = "done"
        if not counts.get("matched"):
            correlation_status = "empty"
        elif not counts.get("strong"):
            correlation_status = "partial"

        if scenario:
            scenario_status = scenario.get("status") or "partial"
        else:
            scenario_status = "none"

        has_conclusion = bool(diagnosis.get("conclusions"))
        conclusion_status = "done" if has_conclusion else "limited"

        return [
            {
                "key": "feedback",
                "title": "反馈接入",
                "status": "done",
                "summary": (
                    f"{anchor.service or '—'} / {anchor.error_code or '—'}，"
                    f"锚点时间 {format_timestamp(anchor.timestamp)}"
                ),
                "metrics": [
                    {"label": "反馈来源", "value": "用户反馈"},
                    {"label": "UID", "value": anchor.uid or "—"},
                ],
            },
            {
                "key": "query",
                "title": "多系统查询",
                "status": query_status,
                "summary": (
                    f"{len(query_hits)} 条命中，覆盖 "
                    f"{len(query_result.get('counts', {}).get('by_platform') or {})} 个平台"
                ),
                "metrics": [
                    {"label": "命中", "value": len(query_hits)},
                    {
                        "label": "不可用源",
                        "value": len(unavailable),
                    },
                ],
            },
            {
                "key": "correlation",
                "title": "证据关联",
                "status": correlation_status,
                "summary": (
                    f"候选 {counts.get('candidates', 0)} 条，"
                    f"关联 {counts.get('matched', 0)} 条，"
                    f"强关联 {counts.get('strong', 0)} 条"
                ),
                "metrics": [
                    {"label": "强关联", "value": counts.get("strong", 0)},
                    {"label": "上下文", "value": counts.get("context", 0)},
                ],
            },
            {
                "key": "scenario",
                "title": "场景判定",
                "status": scenario_status,
                "summary": (
                    f"{scenario.get('name') or scenario.get('scenario_id')}，"
                    f"状态 {STATUS_LABELS.get(scenario_status, scenario_status)}，"
                    f"置信度 {scenario.get('confidence') or '—'}"
                    if scenario
                    else "未命中故障场景，使用通用诊断"
                ),
                "metrics": [
                    {
                        "label": "命中分",
                        "value": (
                            (scenario.get("match") or {}).get("score", 0)
                            if scenario else 0
                        ),
                    },
                    {
                        "label": "缺失源",
                        "value": len(scenario.get("missing_sources") or [])
                        if scenario else 0,
                    },
                ],
            },
            {
                "key": "conclusion",
                "title": "结论与追溯",
                "status": conclusion_status,
                "summary": (
                    f"{len(diagnosis.get('conclusions') or [])} 条结论，"
                    f"{len(scenario.get('evidence_ids') or []) if scenario else 0} 条证据可回看"
                ),
                "metrics": [
                    {
                        "label": "结论",
                        "value": len(diagnosis.get("conclusions") or []),
                    },
                    {
                        "label": "可追溯证据",
                        "value": (
                            len(scenario.get("evidence_ids") or [])
                            if scenario else 0
                        ),
                    },
                ],
            },
        ]

    def _traceability(
        self,
        chain: list[dict],
        scenario: Optional[dict],
    ) -> dict:
        chain_ids = {
            item["evidence_id"] for item in chain if item.get("evidence_id")
        }
        conclusion_ids = []
        missing = []

        if scenario:
            for item in (scenario.get("facts") or []) + (
                scenario.get("hypotheses") or []
            ):
                for evidence_id in item.get("evidence_ids") or []:
                    if evidence_id not in conclusion_ids:
                        conclusion_ids.append(evidence_id)
                    if evidence_id not in chain_ids and evidence_id not in missing:
                        missing.append(evidence_id)

        return {
            "chain_evidence_ids": sorted(chain_ids),
            "conclusion_evidence_ids": conclusion_ids,
            "missing_evidence_ids": missing,
            "all_conclusions_traceable": not missing,
        }

    # ------------------------------------------------------------------
    # 原始追溯
    # ------------------------------------------------------------------
    def trace(self, workspace_id: str, evidence_id: str) -> Optional[dict]:
        evidence = self.evidence_repository.get_evidence(
            workspace_id, evidence_id
        )
        if evidence is None:
            return None

        source = None
        if evidence.source_id:
            source = self.evidence_repository.get_source(
                workspace_id, evidence.source_id
            )

        return {
            "evidence_id": evidence.evidence_id,
            "record_id": evidence.record_id,
            "source": source,
            "type": evidence.evidence_type,
            "timestamp": format_timestamp(evidence.timestamp),
            "trace_id": evidence.trace_id,
            "request_id": evidence.request_id,
            "raw": evidence.raw_data,
            "attributes": evidence.attributes,
        }
