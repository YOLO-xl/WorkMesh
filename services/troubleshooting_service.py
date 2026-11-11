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
        link_mode: str = "merge",
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
            link_mode=link_mode,
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
        # 先做外部查询并把结果落入统一证据表，再生成诊断上下文。
        # 这样 ES / Prometheus 证据会和本地证据一样参与事实、假设与场景判定，
        # 而不是只停留在查询结果展示层。
        feedback_record = self.feedback_repository.get_feedback_record(
            workspace_id, feedback_id
        )

        if feedback_record is None:
            raise LookupError(f"反馈记录不存在：{feedback_id}")

        anchor = Evidence.from_feedback_record(feedback_record)
        query_filters = self._query_filters(anchor, window_minutes)
        query_result = self.query_service.query(
            workspace_id,
            filters=query_filters,
            external_sources=external_sources,
        )

        external_ingest = self._ingest_external_hits(
            workspace_id,
            query_result,
            external_sources,
        )
        connector_provider = self._build_external_connector_provider(
            query_result,
            external_sources,
        )

        diagnosis = self.diagnosis_service.build(
            workspace_id=workspace_id,
            feedback_id=feedback_id,
            window_minutes=window_minutes,
            min_score=min_score,
            use_llm=False,
            manual_answers=manual_answers or {},
            connector_provider=connector_provider,
        )

        context = diagnosis["context"]
        anchor = context["anchor"]
        scenario = diagnosis.get("scenario")

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

        query_payload = {
            **query_result,
            "hits": query_hits,
            "external_ingest": external_ingest,
        }

        return {
            "anchor": anchor.to_dict(),
            "anchor_record": context.get("anchor_record"),
            "query": query_payload,
            "context": context,
            "diagnosis": diagnosis,
            "scenario": scenario,
            "chain": chain,
            "stages": stages,
            "traceability": self._traceability(
                chain, scenario, diagnosis=diagnosis, context=context
            ),
            "markdown": diagnosis.get("markdown") or "",
        }

    # ------------------------------------------------------------------
    # 外部证据入库
    # ------------------------------------------------------------------
    def _ingest_external_hits(
        self,
        workspace_id: str,
        query_result: dict,
        external_sources: Optional[list[dict]],
    ) -> list[dict]:
        """把外部查询 hit 通过统一 Evidence 入口落库。

        查询服务只负责查询；这里负责把外部结果转成 Evidence 并写入
        evidence_records。写入失败时保留每个数据源的明确状态，不影响其他源。
        """

        specs = list(external_sources or [])
        status_by_id = {
            str(item.get("id")): item
            for item in (query_result.get("sources") or [])
            if item.get("id")
        }

        reports = []

        for index, spec in enumerate(specs):
            source_id = str(spec.get("id") or f"external_{index + 1}")
            source_hits = [
                hit for hit in (query_result.get("hits") or [])
                if hit.get("source_id") == source_id
            ]
            status = status_by_id.get(source_id)

            report = {
                "source_id": source_id,
                "source_name": spec.get("display_name") or source_id,
                "connector_type": spec.get("connector_type"),
                "query": spec.get("query") or {},
                "available": bool(status.get("available")) if status else False,
                "total": len(source_hits),
                "inserted": 0,
                "skipped": 0,
                "failed": 0,
                "error": "",
            }

            if status is not None:
                status["query"] = spec.get("query") or {}
                status["persisted"] = 0

            if not source_hits:
                reports.append(report)
                continue

            source_type = str(spec.get("source_type") or "").strip()
            if not source_type:
                connector_type = str(spec.get("connector_type") or "")
                source_type = "metric" if connector_type == "prometheus" else (
                    source_hits[0].get("kind") or "log"
                )

            records = [
                self._external_hit_record(hit, spec)
                for hit in source_hits
            ]

            try:
                result = self.evidence_service.ingest_connector_records(
                    workspace_id,
                    records,
                    source_name=report["source_name"],
                    source_type=source_type,
                    connector_type=spec.get("connector_type"),
                )

                persisted = 0
                result_records = result.get("records") or []

                for hit, item in zip(source_hits, result_records):
                    evidence_id = item.get("evidence_id")
                    hit["evidence_id"] = evidence_id
                    hit["persisted"] = bool(evidence_id)
                    hit["traceable"] = bool(evidence_id or hit.get("raw"))

                    evidence = hit.get("evidence")
                    if isinstance(evidence, Evidence):
                        evidence.evidence_id = evidence_id
                        evidence.source_id = result.get("source_id")

                    if evidence_id:
                        persisted += 1

                report.update({
                    "inserted": result.get("inserted", 0),
                    "skipped": result.get("skipped", 0),
                    "failed": result.get("failed", 0),
                    "error": "; ".join(
                        str(item.get("error") or "")
                        for item in (result.get("errors") or [])
                    ),
                    "source_db_id": result.get("source_id"),
                })

                if status is not None:
                    status["persisted"] = persisted
                    status["skipped"] = result.get("skipped", 0)
                    status["failed"] = result.get("failed", 0)
                    if result.get("failed"):
                        status["message"] = (
                            f"查询成功，{result.get('failed')} 条证据入库失败"
                        )

            except Exception as exc:
                report["failed"] = len(source_hits)
                report["error"] = str(exc)
                if status is not None:
                    status["persisted"] = 0
                    status["failed"] = len(source_hits)
                    status["ingest_error"] = str(exc)
                    status["message"] = f"证据入库失败：{exc}"

            reports.append(report)

        return reports

    def _build_external_connector_provider(
        self,
        query_result: dict,
        external_sources: Optional[list[dict]],
        allow_missing_status: bool = False,
    ):
        """构建本次诊断专用的场景 Connector Provider。

        Provider 按外部数据源 ID 分组，避免同类型数据源（例如多个 ES）
        相互混用。场景配置显式指定 external_source_id 时只读取该源；
        未指定时只在本轮查询成功且存在有效入库记录的同类型源中自动匹配，
        多个可用源报歧义，没有可用源报明确不可用。
        返回的 Provider 仅传给本次 DiagnosisService.build()，不修改实例状态。

        allow_missing_status 仅用于内部直接构造 query_result 的兼容调用；
        正常 run() 流程不传入，缺少 source status 时按不可用处理。
        """

        if not external_sources:
            return None

        specs = [spec for spec in external_sources if isinstance(spec, dict)]
        specs_by_id: dict[str, dict] = {}
        source_ids_by_connector: dict[str, list[str]] = {}

        for index, spec in enumerate(specs):
            source_id = str(spec.get("id") or f"external_{index + 1}")
            specs_by_id[source_id] = spec
            connector_type = str(spec.get("connector_type") or "").strip()
            if connector_type:
                source_ids_by_connector.setdefault(connector_type, []).append(
                    source_id
                )

        source_ids_by_connector = {
            connector_type: sorted(set(source_ids))
            for connector_type, source_ids in source_ids_by_connector.items()
        }

        status_by_id = {
            str(item.get("id")): item
            for item in (query_result.get("sources") or [])
            if item.get("id")
        }

        records_by_source: dict[str, list[dict]] = {}

        for hit in query_result.get("hits") or []:
            if not hit.get("evidence_id"):
                continue

            source_id = str(hit.get("source_id") or "")
            spec = specs_by_id.get(source_id)
            if not spec:
                continue

            evidence = hit.get("evidence")
            if not isinstance(evidence, Evidence):
                continue

            record = evidence.to_dict()
            attributes = dict(record.get("attributes") or {})
            attributes["external_source_id"] = source_id
            attributes.setdefault("connector_query", spec.get("query") or {})
            record["attributes"] = attributes
            if evidence.evidence_type == "metric" and "value" not in record:
                record["value"] = attributes.get("metric_value")

            records_by_source.setdefault(source_id, []).append(record)

        def query_succeeded(source_id: str) -> bool:
            status = status_by_id.get(source_id)
            if status is None:
                # 正常 run() 必须提供 source status；缺失即 fail closed。
                # 仅显式兼容参数允许按已入库记录推断成功。
                return allow_missing_status and bool(
                    records_by_source.get(source_id)
                )
            return status.get("available") is True

        usable_source_ids = {
            source_id
            for source_id in specs_by_id
            if query_succeeded(source_id) and records_by_source.get(source_id)
        }

        def unavailable_reason(source_ids: list[str]) -> str:
            failed = []
            no_records = []
            invalid = []

            for source_id in source_ids:
                status = status_by_id.get(source_id)
                if status is None:
                    continue
                available = status.get("available")
                if available is False:
                    failed.append(source_id)
                elif available is not True:
                    invalid.append(source_id)
                elif not records_by_source.get(source_id):
                    no_records.append(source_id)

            if failed and len(failed) == len(source_ids):
                headline = "外部数据源不可用：本轮外部数据源均查询失败"
            elif invalid and len(invalid) == len(source_ids):
                headline = (
                    "外部数据源不可用：本轮外部数据源查询状态缺失或无效"
                )
            elif no_records and len(no_records) == len(source_ids):
                headline = (
                    "外部数据源不可用：本轮外部数据源均查询成功，"
                    "但无有效入库证据"
                )
            else:
                headline = (
                    "外部数据源不可用：本轮没有查询成功且存在有效入库证据的"
                    "外部数据源"
                )

            details = []
            for source_id in source_ids:
                status = status_by_id.get(source_id)
                if status is None:
                    details.append(f"{source_id}（无查询状态）")
                    continue

                available = status.get("available")
                if available is False:
                    message = str(status.get("message") or "").strip()
                    suffix = f"：{message}" if message else ""
                    details.append(f"{source_id}（查询失败{suffix}）")
                elif "available" not in status:
                    details.append(
                        f"{source_id}（查询状态缺少 available 字段）"
                    )
                elif available is not True:
                    details.append(
                        f"{source_id}（查询状态无效：available={available!r}）"
                    )
                elif not records_by_source.get(source_id):
                    details.append(f"{source_id}（查询成功但无有效入库证据）")

            return f"{headline}（{'；'.join(details)}）"

        base_provider = getattr(
            self.diagnosis_service, "connector_provider", None
        )
        service = self

        def provider(source: dict, feedback: dict) -> list[dict]:
            connector_type = str(source.get("connector_type") or "").strip()
            query = source.get("query") or {}
            requested = source.get("external_source_id")
            if requested in (None, ""):
                requested = query.get("external_source_id")
            requested = str(requested).strip() if requested not in (None, "") else ""

            if requested:
                if requested not in specs_by_id:
                    raise ValueError(
                        "外部数据源歧义：场景指定 external_source_id="
                        f"{requested}，但本轮外部查询未包含该数据源"
                    )

                requested_spec = specs_by_id[requested]
                actual_type = str(
                    requested_spec.get("connector_type") or ""
                ).strip()

                if not connector_type:
                    raise ValueError(
                        "外部数据源配置错误：场景显式指定 "
                        f"external_source_id={requested}，"
                        "但场景数据源未配置 connector_type，无法校验来源类型"
                    )

                if actual_type != connector_type:
                    raise ValueError(
                        "外部数据源类型不匹配：场景指定 "
                        f"external_source_id={requested}，"
                        f"实际连接器类型={actual_type or '未配置'}，"
                        f"要求连接器类型={connector_type}"
                    )

                status = status_by_id.get(requested)
                if status is None:
                    # 显式来源不做 missing-status 兼容放行，必须 fail closed。
                    raise ValueError(
                        "外部数据源不可用：指定来源 "
                        f"{requested} 无查询状态"
                    )

                if "available" not in status:
                    raise ValueError(
                        "外部数据源不可用：指定来源 "
                        f"{requested} 的查询状态缺少 available 字段"
                    )

                available = status.get("available")
                if available is False:
                    message = str(status.get("message") or "").strip()
                    suffix = f"：{message}" if message else ""
                    raise ValueError(
                        "外部数据源不可用：指定来源 "
                        f"{requested} 查询失败{suffix}"
                    )

                if available is not True:
                    raise ValueError(
                        "外部数据源不可用：指定来源 "
                        f"{requested} 的查询状态无效"
                        f"（available={available!r}），仅接受布尔值 True"
                    )

                if not records_by_source.get(requested):
                    raise ValueError(
                        "外部数据源不可用：指定来源 "
                        f"{requested} 查询成功但无有效入库证据"
                    )

                return service._filter_scenario_records(
                    records_by_source.get(requested, []),
                    query,
                )

            configured_ids = source_ids_by_connector.get(connector_type, [])
            usable_ids = [
                source_id
                for source_id in configured_ids
                if source_id in usable_source_ids
            ]

            if len(usable_ids) > 1:
                raise ValueError(
                    "外部数据源歧义：连接器类型 "
                    f"{connector_type or 'unknown'} 对应多个"
                    f"查询成功且有有效记录的外部数据源"
                    f"（{', '.join(usable_ids)}），"
                    "请在场景配置中显式指定 external_source_id"
                )

            if len(usable_ids) == 1:
                return service._filter_scenario_records(
                    records_by_source.get(usable_ids[0], []),
                    query,
                )

            if configured_ids:
                raise ValueError(unavailable_reason(configured_ids))

            if base_provider is not None:
                return base_provider(source, feedback)
            return []

        return provider

    @staticmethod
    def _filter_scenario_records(
        records: list[dict],
        query: dict,
    ) -> list[dict]:
        """按场景配置中的简单过滤条件筛出可用的外部记录。"""

        filters = query.get("filters")
        if not isinstance(filters, dict):
            filters = {}

        result = []
        for record in records:
            matched = True
            for key, expected in filters.items():
                if expected in ("", None):
                    continue
                if str(record.get(key) or "") != str(expected):
                    matched = False
                    break
            if matched:
                result.append(record)
        return result

    def _external_hit_record(self, hit: dict, spec: dict) -> dict:
        """把查询 hit 转成 Evidence.from_connector_record 可接受的记录。"""

        evidence = hit.get("evidence")
        if isinstance(evidence, Evidence):
            record = evidence.to_dict()
        else:
            record = dict(hit.get("raw") or {})
            record.setdefault("evidence_type", hit.get("kind") or "log")
            record.setdefault("record_id", hit.get("record_id"))

        record = dict(record)
        record.pop("evidence_id", None)
        record.pop("source_id", None)

        attributes = dict(record.get("attributes") or {})
        attributes["external_source_id"] = str(spec.get("id") or "")
        attributes["connector_query"] = spec.get("query") or {}
        record["attributes"] = attributes

        if not record.get("raw_data"):
            record["raw_data"] = hit.get("raw") or {}

        if not record.get("connector_type"):
            record["connector_type"] = hit.get("connector_type")

        if not record.get("evidence_type"):
            record["evidence_type"] = hit.get("kind") or "log"

        return record

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
        diagnosis: Optional[dict] = None,
        context: Optional[dict] = None,
    ) -> dict:
        chain_ids = {
            item["evidence_id"] for item in chain if item.get("evidence_id")
        }
        conclusion_ids = []
        missing = []

        conclusion_items = []
        if scenario:
            conclusion_items.extend(
                (scenario.get("facts") or [])
                + (scenario.get("hypotheses") or [])
            )
        if context:
            conclusion_items.extend(
                (context.get("facts") or [])
                + (context.get("hypotheses") or [])
            )
        if diagnosis:
            conclusion_items.extend(diagnosis.get("conclusions") or [])

        for item in conclusion_items:
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
            "missing_sources": (scenario or {}).get("missing_sources") or [],
            "evidence_by_source": self._evidence_by_source(chain),
            "context_evidence_ids": [
                item.get("evidence_id")
                for item in (
                    (context or {}).get("matches") or []
                )
                if item.get("relation") == "context"
                and item.get("evidence_id")
            ],
        }

    @staticmethod
    def _evidence_by_source(chain: list[dict]) -> list[dict]:
        """按来源汇总证据链，明确本地证据与外部连接器证据。"""

        grouped: dict[tuple, dict] = {}

        for item in chain:
            if item.get("role") == "anchor":
                continue

            key = (
                item.get("source_name") or "—",
                item.get("connector_type") or "",
                item.get("type") or "",
            )
            entry = grouped.setdefault(key, {
                "source_name": item.get("source_name") or "—",
                "connector_type": item.get("connector_type") or "",
                "source_type": item.get("source_type") or "",
                "evidence_type": item.get("type") or "",
                "evidence_type_label": item.get("type_label") or "",
                "evidence_count": 0,
                "evidence_ids": [],
                "record_ids": [],
            })
            entry["evidence_count"] += 1
            if item.get("evidence_id"):
                entry["evidence_ids"].append(item["evidence_id"])
            if item.get("record_id"):
                entry["record_ids"].append(item["record_id"])

        return list(grouped.values())

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
