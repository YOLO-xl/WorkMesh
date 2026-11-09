"""故障场景运营统计。

把场景配置从“静态 JSON”变成可观察的运营对象：
列出场景、评估最近反馈的场景覆盖，并统计已验证 / 部分完成 / 阻塞比例。
"""

from __future__ import annotations

from typing import Any, Optional

from database.feedback_repository import FeedbackRepository
from services.fault_scenario_service import (
    FaultScenarioRegistry,
    ScenarioRunner,
    match_scenario,
)


STATUS_LABELS = {
    "verified": "已验证",
    "partial": "部分完成",
    "blocked": "阻塞",
    "none": "未命中",
}


def _feedback_text(record: dict) -> str:
    return str(record.get("description") or "")


class ScenarioOpsService:
    """场景库与覆盖评估。"""

    def __init__(
        self,
        db_path=None,
        feedback_repository: Optional[FeedbackRepository] = None,
        registry: Optional[FaultScenarioRegistry] = None,
        runner_factory=None,
        connector_provider=None,
    ):
        self.db_path = db_path
        self.feedback_repository = feedback_repository or FeedbackRepository(db_path)
        self.registry = registry or FaultScenarioRegistry()
        self.runner_factory = runner_factory
        self.connector_provider = connector_provider

    def overview(self) -> dict:
        """返回场景库清单与配置健康度。"""

        scenarios = []
        for scenario in self.registry.enabled():
            sources = scenario.get("sources") or []
            connector_sources = [
                item for item in sources
                if item.get("kind") == "connector"
            ]
            scenarios.append({
                "id": scenario["id"],
                "name": scenario.get("name") or scenario["id"],
                "version": scenario.get("version"),
                "owner": scenario.get("owner"),
                "priority": int(scenario.get("priority", 0)),
                "tags": scenario.get("tags") or [],
                "source_count": len(sources),
                "connector_sources": [
                    {
                        "id": item.get("id"),
                        "connector_type": item.get("connector_type"),
                        "required": bool(item.get("required")),
                    }
                    for item in connector_sources
                ],
                "required_sources": [
                    item.get("id")
                    for item in sources
                    if item.get("required")
                ],
                "step_count": len(scenario.get("steps") or []),
                "rule_count": len(scenario.get("rules") or []),
                "disclaimer": (scenario.get("output_policy") or {}).get(
                    "disclaimer"
                ),
            })

        return {
            "scenarios": scenarios,
            "errors": self.registry.errors,
            "enabled_count": len(scenarios),
            "config_error_count": len(self.registry.errors),
        }

    def evaluate(self, workspace_id: str, limit: int = 100) -> dict:
        """对最近 N 条反馈逐个匹配并执行场景，产出覆盖率与状态分布。"""

        limit = min(max(int(limit), 1), 500)
        records = self.feedback_repository.list_feedback(
            workspace_id,
            page=1,
            page_size=limit,
        )["records"]

        if self.runner_factory:
            runner = self.runner_factory()
        else:
            runner = ScenarioRunner(
                feedback_repository=self.feedback_repository,
                db_path=self.db_path,
                connector_provider=self.connector_provider,
            )

        rows = []
        scenario_stats: dict[str, dict[str, Any]] = {}

        for record in records:
            scenario, score, reasons = match_scenario(
                record, self.registry.enabled()
            )
            if not scenario:
                rows.append({
                    "feedback_id": record.get("id"),
                    "external_id": record.get("external_id"),
                    "uid": record.get("uid"),
                    "product": record.get("product"),
                    "error_code": record.get("error_code"),
                    "description": _feedback_text(record),
                    "feedback_time": record.get("feedback_time"),
                    "scenario_id": "unmatched",
                    "scenario_name": "未命中",
                    "score": 0,
                    "reasons": [],
                    "status": "none",
                    "confidence": None,
                    "missing_sources": [],
                })
                continue

            run = runner.run(
                workspace_id=workspace_id,
                feedback=record,
                scenario=scenario,
                anchor=None,
            )
            row = {
                "feedback_id": record.get("id"),
                "external_id": record.get("external_id"),
                "uid": record.get("uid"),
                "product": record.get("product"),
                "error_code": record.get("error_code"),
                "description": _feedback_text(record),
                "feedback_time": record.get("feedback_time"),
                "scenario_id": run["scenario_id"],
                "scenario_name": run.get("name"),
                "score": score,
                "reasons": reasons,
                "status": run.get("status") or "partial",
                "confidence": run.get("confidence"),
                "missing_sources": run.get("missing_sources") or [],
                "limits": run.get("limits") or [],
                "next_actions": run.get("next_actions") or [],
                "facts": run.get("facts") or [],
                "hypotheses": run.get("hypotheses") or [],
                "steps": run.get("steps") or [],
            }
            rows.append(row)

            stats = scenario_stats.setdefault(run["scenario_id"], {
                "id": run["scenario_id"],
                "name": run.get("name"),
                "matched": 0,
                "verified": 0,
                "partial": 0,
                "blocked": 0,
            })
            stats["matched"] += 1
            status = row["status"]
            if status in ("verified", "partial", "blocked"):
                stats[status] += 1

        return {
            "workspace_id": workspace_id,
            "evaluated": len(records),
            "rows": rows,
            "scenario_stats": sorted(
                scenario_stats.values(),
                key=lambda item: (-item["matched"], item["id"]),
            ),
            "summary": self._summary(rows, scenario_stats, len(records)),
        }

    @staticmethod
    def _summary(
        rows: list[dict],
        scenario_stats: dict[str, dict],
        evaluated: int,
    ) -> dict:
        specific = [
            row for row in rows
            if row["scenario_id"] not in ("generic.triage", "unmatched")
        ]
        fallback = [
            row for row in rows if row["scenario_id"] == "generic.triage"
        ]
        unmatched = [
            row for row in rows if row["scenario_id"] == "unmatched"
        ]

        def count_status(status: str) -> int:
            return sum(1 for row in rows if row["status"] == status)

        coverage = len(specific) / evaluated if evaluated else 0.0
        verified_rate = (
            sum(1 for row in specific if row["status"] == "verified")
            / len(specific)
            if specific else 0.0
        )

        return {
            "evaluated": evaluated,
            "specific_matched": len(specific),
            "fallback": len(fallback),
            "unmatched": len(unmatched),
            "coverage_rate": coverage,
            "verified": count_status("verified"),
            "partial": count_status("partial"),
            "blocked": count_status("blocked"),
            "verified_rate": verified_rate,
            "scenario_count": len(scenario_stats),
        }

    @staticmethod
    def status_label(status: str) -> str:
        return STATUS_LABELS.get(status, status or "未知")
