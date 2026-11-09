"""跨系统查询服务。

把本地反馈、本地证据和外部连接器（Elasticsearch / Prometheus）的查询结果
统一成同一种 hit 结构，再做过滤、去重、关联和原始记录追溯。

这一层只做查询编排，不负责写入；外部查询结果需要落库时，
仍由 ``EvidenceService.ingest_connector_records`` 统一处理。
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Callable, Iterable, Optional

from connectors import connector_registry
from connectors.base import BaseConnector
from core.correlation import (
    DEFAULT_MIN_SCORE,
    DEFAULT_WINDOW_MINUTES,
    score_pair,
)
from core.evidence import Evidence, format_timestamp
from database.evidence_repository import EvidenceRepository
from database.feedback_repository import FeedbackRepository


PLATFORM_LABELS = {
    "feedback": "本地反馈",
    "evidence": "本地证据",
    "elasticsearch": "Elasticsearch",
    "prometheus": "Prometheus",
    "opensearch": "OpenSearch",
    "loki": "Loki",
}

TYPE_LABELS = {
    "feedback": "用户反馈",
    "log": "日志",
    "metric": "指标",
    "trace": "链路追踪",
    "change": "变更记录",
    "note": "人工备注",
}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(item) for item in value if str(item).strip()]


def _clamp_limit(value: Any, default: int = 200) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = default
    return min(max(limit, 1), 500)


def _hit_sort_key(hit: dict) -> str:
    return str(hit.get("timestamp") or "")


class MultiSourceQueryService:
    """跨反馈、证据与外部系统的统一查询入口。"""

    def __init__(
        self,
        feedback_repository: Optional[FeedbackRepository] = None,
        evidence_repository: Optional[EvidenceRepository] = None,
        db_path=None,
        connector_factory: Optional[Callable[[str, dict], BaseConnector]] = None,
    ):
        self.feedback_repository = (
            feedback_repository or FeedbackRepository(db_path)
        )
        self.evidence_repository = (
            evidence_repository or EvidenceRepository(db_path)
        )
        self.connector_factory = connector_factory or connector_registry.create

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def query(
        self,
        workspace_id: str,
        filters: Optional[dict] = None,
        external_sources: Optional[Iterable[dict]] = None,
    ) -> dict:
        """按过滤条件查询本地与外部数据源。

        返回结构：
        - hits：统一后的查询结果，按时间倒序
        - counts：按平台/类型统计
        - sources：每个数据源的可用状态和条数
        """

        filters = dict(filters or {})
        limit = _clamp_limit(filters.get("limit"))

        hits: list[dict] = []
        sources: list[dict] = []

        feedback_hits = self._query_local_feedback(
            workspace_id, filters, limit
        )
        hits.extend(feedback_hits)
        sources.append({
            "id": "local_feedback",
            "platform": PLATFORM_LABELS["feedback"],
            "available": True,
            "count": len(feedback_hits),
            "message": "",
        })

        evidence_hits = self._query_local_evidence(
            workspace_id, filters, limit
        )
        hits.extend(evidence_hits)
        sources.append({
            "id": "local_evidence",
            "platform": PLATFORM_LABELS["evidence"],
            "available": True,
            "count": len(evidence_hits),
            "message": "",
        })

        for index, spec in enumerate(external_sources or []):
            source_id = str(spec.get("id") or f"external_{index + 1}")
            try:
                external_hits, message = self._query_external(
                    source_id, spec, filters
                )
                hits.extend(external_hits)
                sources.append({
                    "id": source_id,
                    "platform": spec.get("display_name")
                    or PLATFORM_LABELS.get(
                        spec.get("connector_type"),
                        spec.get("connector_type") or source_id,
                    ),
                    "available": True,
                    "count": len(external_hits),
                    "message": message,
                })
            except Exception as exc:
                sources.append({
                    "id": source_id,
                    "platform": spec.get("display_name")
                    or PLATFORM_LABELS.get(
                        spec.get("connector_type"),
                        spec.get("connector_type") or source_id,
                    ),
                    "available": False,
                    "count": 0,
                    "message": str(exc),
                })

        hits = self._apply_python_filters(hits, filters)
        hits = self._dedupe(hits)
        hits.sort(key=_hit_sort_key, reverse=True)

        if len(hits) > limit:
            hits = hits[:limit]

        return {
            "hits": hits,
            "counts": self._counts(hits),
            "sources": sources,
            "filters": filters,
        }

    def _query_local_feedback(
        self,
        workspace_id: str,
        filters: dict,
        limit: int,
    ) -> list[dict]:
        records = self.feedback_repository.query_feedback(
            workspace_id,
            products=_as_list(filters.get("products")),
            error_codes=_as_list(filters.get("error_codes")),
            uid=filters.get("uid") or None,
            keyword=filters.get("keyword") or None,
            start=filters.get("start") or None,
            end=filters.get("end") or None,
            limit=limit,
        )

        hits = []
        for record in records:
            evidence = Evidence.from_feedback_record(record)
            hits.append(self._hit_from_evidence(
                evidence=evidence,
                source_id="local_feedback",
                platform="本地反馈",
                raw=record,
            ))
        return hits

    def _query_local_evidence(
        self,
        workspace_id: str,
        filters: dict,
        limit: int,
    ) -> list[dict]:
        error_codes = _as_list(filters.get("error_codes"))
        records = self.evidence_repository.query_evidence(
            workspace_id,
            evidence_types=_as_list(filters.get("evidence_types")),
            service=filters.get("service") or None,
            uid=filters.get("uid") or None,
            error_code=error_codes[0] if len(error_codes) == 1 else None,
            trace_id=filters.get("trace_id") or None,
            request_id=filters.get("request_id") or None,
            titles=_as_list(filters.get("titles")),
            keyword=filters.get("keyword") or None,
            start=filters.get("start") or None,
            end=filters.get("end") or None,
            limit=limit,
        )

        hits = []
        for evidence in records:
            hits.append(self._hit_from_evidence(
                evidence=evidence,
                source_id="local_evidence",
                platform="本地证据",
                raw=evidence.raw_data or {},
            ))
        return hits

    def _query_external(
        self,
        source_id: str,
        spec: dict,
        filters: dict,
    ) -> tuple[list[dict], str]:
        connector_type = str(spec.get("connector_type") or "").strip()
        if not connector_type:
            raise ValueError(f"外部数据源 {source_id} 缺少 connector_type")

        connector = self.connector_factory(
            connector_type, spec.get("config") or {}
        )

        try:
            test = connector.test_connection()
            if not test.get("success"):
                raise RuntimeError(test.get("message") or "连接测试失败")

            query_args = dict(spec.get("query") or {})
            for key in ("uid", "error_code", "keyword", "limit"):
                if filters.get(key) and key not in query_args:
                    query_args[key] = filters[key]

            if connector_type == "elasticsearch":
                if filters.get("start") and "start_time" not in query_args:
                    query_args["start_time"] = filters["start"]
                if filters.get("end") and "end_time" not in query_args:
                    query_args["end_time"] = filters["end"]
                query_args.pop("start", None)
                query_args.pop("end", None)
                query_args.setdefault("size", _clamp_limit(filters.get("limit")))
            elif connector_type == "prometheus":
                # Prometheus 的即时查询默认取当前态，不强行套反馈的历史窗口；
                # 需要历史区间时由 spec.query 显式传 use_window=true 或 start/end。
                use_window = bool(query_args.pop("use_window", False))
                if use_window:
                    query_args.setdefault("start", filters.get("start"))
                    query_args.setdefault("end", filters.get("end"))

            raw_records = connector.query(**query_args)
            normalized = connector.normalize(raw_records)
        finally:
            connector.close()

        hits = []
        for record in normalized:
            evidence = Evidence.from_connector_record(
                record,
                default_type=spec.get("source_type") or "log",
            )
            hits.append(self._hit_from_evidence(
                evidence=evidence,
                source_id=source_id,
                platform=spec.get("display_name")
                or PLATFORM_LABELS.get(connector_type, connector_type),
                raw=evidence.raw_data or record,
                connector_type=connector_type,
            ))

        return hits, ""

    # ------------------------------------------------------------------
    # 统一结构
    # ------------------------------------------------------------------
    @staticmethod
    def _hit_from_evidence(
        evidence: Evidence,
        source_id: str,
        platform: str,
        raw: Any,
        connector_type: Optional[str] = None,
    ) -> dict:
        return {
            "hit_id": (
                f"{source_id}:{evidence.evidence_id or evidence.record_id}"
            ),
            "source_id": source_id,
            "platform": platform,
            "connector_type": connector_type or evidence.connector_type,
            "kind": evidence.evidence_type,
            "kind_label": TYPE_LABELS.get(
                evidence.evidence_type, evidence.evidence_type
            ),
            "record_id": evidence.record_id,
            "evidence_id": evidence.evidence_id,
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
            "raw": raw if raw is not None else {},
            "traceable": bool(evidence.evidence_id or raw),
            "evidence": evidence,
        }

    @staticmethod
    def _counts(hits: list[dict]) -> dict:
        by_platform: dict[str, int] = {}
        by_kind: dict[str, int] = {}

        for hit in hits:
            platform = hit.get("platform") or "未知"
            kind = hit.get("kind_label") or hit.get("kind") or "未知"
            by_platform[platform] = by_platform.get(platform, 0) + 1
            by_kind[kind] = by_kind.get(kind, 0) + 1

        return {
            "total": len(hits),
            "by_platform": by_platform,
            "by_kind": by_kind,
        }

    def _apply_python_filters(self, hits: list[dict], filters: dict) -> list[dict]:
        error_codes = _as_list(filters.get("error_codes"))
        if len(error_codes) > 1:
            hits = [
                hit for hit in hits
                if not hit.get("error_code")
                or hit.get("error_code") in error_codes
            ]

        keyword = str(filters.get("keyword") or "").strip().lower()
        if keyword:
            haystack_keys = (
                "summary", "message", "title", "service", "error_code",
                "record_id", "trace_id", "request_id", "uid",
            )
            filtered = []
            for hit in hits:
                text = " ".join(
                    str(hit.get(key) or "") for key in haystack_keys
                ).lower()
                if not text.strip():
                    text = json.dumps(
                        hit.get("raw") or {}, ensure_ascii=False
                    ).lower()
                if keyword in text:
                    filtered.append(hit)
            hits = filtered

        return hits

    @staticmethod
    def _dedupe(hits: list[dict]) -> list[dict]:
        seen = set()
        deduped = []

        for hit in hits:
            key = (
                hit.get("source_id"),
                hit.get("evidence_id") or hit.get("record_id"),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(hit)

        return deduped

    # ------------------------------------------------------------------
    # 关联与追溯
    # ------------------------------------------------------------------
    def associate(
        self,
        hits: Iterable[dict],
        anchor: Evidence,
        window_minutes: int = DEFAULT_WINDOW_MINUTES,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> list[dict]:
        """给查询结果附加与锚点的关联分数，不删除低分结果。"""

        window = timedelta(minutes=max(int(window_minutes), 1))
        results = []

        for hit in hits:
            evidence = hit.get("evidence")
            if not isinstance(evidence, Evidence):
                evidence = Evidence.from_dict(hit.get("raw") or {})
                hit["evidence"] = evidence

            match = score_pair(
                anchor,
                evidence,
                window=window,
            )

            item = dict(hit)
            if match is None:
                item["score"] = 0.0
                item["strength"] = "none"
                item["relation"] = "unrelated"
                item["matched_keys"] = []
                item["reasons"] = []
            else:
                item["score"] = match.score
                item["strength"] = match.strength
                item["relation"] = match.relation
                item["matched_keys"] = match.matched_keys
                item["reasons"] = match.reasons
                item["below_threshold"] = match.score < min_score

            results.append(item)

        results.sort(
            key=lambda item: (item.get("score") or 0, item.get("timestamp") or ""),
            reverse=True,
        )
        return results

    def trace(
        self,
        workspace_id: str,
        hit: dict,
    ) -> dict:
        """返回某条命中结果的原始记录与来源信息。"""

        evidence_id = hit.get("evidence_id")
        evidence = None

        if evidence_id:
            evidence = self.evidence_repository.get_evidence(
                workspace_id, evidence_id
            )

        if evidence is None:
            evidence = hit.get("evidence")
            if not isinstance(evidence, Evidence):
                evidence = Evidence.from_dict(hit.get("raw") or {})

        source = None
        if evidence and evidence.source_id:
            source = self.evidence_repository.get_source(
                workspace_id, evidence.source_id
            )

        return {
            "hit_id": hit.get("hit_id"),
            "source_id": hit.get("source_id"),
            "platform": hit.get("platform"),
            "source": source,
            "record_id": evidence.record_id if evidence else hit.get("record_id"),
            "evidence_id": evidence.evidence_id if evidence else evidence_id,
            "raw": evidence.raw_data if evidence else hit.get("raw"),
            "attributes": evidence.attributes if evidence else {},
            "timestamp": (
                format_timestamp(evidence.timestamp)
                if evidence else hit.get("timestamp")
            ),
            "trace": {
                "trace_id": evidence.trace_id if evidence else None,
                "request_id": evidence.request_id if evidence else None,
            },
        }
