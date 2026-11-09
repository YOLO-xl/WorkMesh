"""故障场景模型：配置加载、匹配、信号计算与规则执行。

第一阶段只实现本地可闭环的数据源：

- ``feedback``：本地反馈查询
- ``evidence``：本地已入库证据查询
- ``manual``：人工确认项

``connector`` / ``report_history`` / ``knowledge`` 保留接口，
当前返回“不可用”，不会伪造结果。真实 ES / Prometheus 适配器放到第二阶段。
"""

from __future__ import annotations

import json
import re

from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from core.evidence import Evidence, format_timestamp, parse_timestamp
from database.evidence_repository import EvidenceRepository
from database.feedback_repository import FeedbackRepository
from services.symptom_lexicon import (
    extract_features,
    extract_symptoms,
    normalize_text,
)


SCENARIO_SCHEMA_VERSION = 1
DEFAULT_CONFIG_DIR = (
    Path(__file__).resolve().parent.parent / "configs" / "fault_scenarios"
)

SOURCE_KINDS = (
    "feedback",
    "evidence",
    "connector",
    "report_history",
    "manual",
    "knowledge",
)

SIGNAL_KINDS = (
    "count",
    "exists",
    "value",
    "distinct_count",
    "max",
    "min",
    "avg",
    "sum",
    "latest",
    "ratio",
    "threshold_flag",
)

CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
CONFIDENCE_BY_RANK = {value: key for key, value in CONFIDENCE_RANK.items()}

BANNED_KEYS = (
    "api_key",
    "apikey",
    "token",
    "password",
    "secret",
    "authorization",
    "cookie",
)

ALLOWED_PLACEHOLDERS = {
    "{{anchor.uid}}",
    "{{anchor.error_code}}",
    "{{anchor.service}}",
    "{{anchor.product}}",
    "{{window.start}}",
    "{{window.end}}",
}

ROOT_CAUSE_WORDS = ("根因", "确定是", "必然", "一定是")


def _record_get(record: Any, field: Optional[str], default: Any = None) -> Any:
    """从 dict / Evidence / 嵌套 attributes 中读取字段。

    场景配置里的 ``attributes.metric_value`` 这类点路径需要逐层解析，
    否则 Evidence.to_dict() 之后只能取到整个 attributes 对象。
    """

    path = str(field or "value")
    current: Any = record

    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return default

    return current


# ----------------------------------------------------------------------
# 校验
# ----------------------------------------------------------------------
def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _collect_placeholders(value: Any) -> set[str]:
    found = set()
    for item in _walk(value):
        if not isinstance(item, str):
            continue
        found.update(re.findall(r"\{\{[^{}]+\}\}", item))
    return found


def _condition_signals(condition: Any) -> set[str]:
    signals = set()
    for item in _walk(condition):
        if isinstance(item, dict) and "signal" in item:
            signals.add(str(item["signal"]))
    return signals


def validate_scenario(scenario: dict) -> list[str]:
    """校验场景配置；返回错误列表，空列表表示通过。"""

    errors: list[str] = []

    if not isinstance(scenario, dict):
        return ["场景配置必须是 JSON 对象"]

    if scenario.get("schema_version") != SCENARIO_SCHEMA_VERSION:
        errors.append(f"schema_version 必须为 {SCENARIO_SCHEMA_VERSION}")

    scenario_id = str(scenario.get("id") or "")
    if not re.match(r"^[a-z][a-z0-9_.-]{2,64}$", scenario_id):
        errors.append("id 必须匹配 ^[a-z][a-z0-9_.-]{2,64}$")

    for key in ("name", "version", "owner", "effective_from"):
        if not str(scenario.get(key) or "").strip():
            errors.append(f"缺少必填字段：{key}")

    if not isinstance(scenario.get("enabled", True), bool):
        errors.append("enabled 必须是布尔值")

    match = scenario.get("match")
    if not isinstance(match, dict) or not (
        match.get("always") is True
        or any(match.get(key) for key in ("products", "error_codes", "keywords_any"))
    ):
        errors.append(
            "match 至少需要 always=true 或 products / error_codes / keywords_any 之一"
        )

    sources = scenario.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("sources 不能为空")
        sources = []

    source_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            errors.append("sources 的每一项必须是对象")
            continue
        source_id = str(source.get("id") or "")
        if not source_id:
            errors.append("source 缺少 id")
        elif source_id in source_ids:
            errors.append(f"source id 重复：{source_id}")
        source_ids.add(source_id)

        if source.get("kind") not in SOURCE_KINDS:
            errors.append(f"source {source_id} 的 kind 不支持：{source.get('kind')}")
        if not isinstance(source.get("required", False), bool):
            errors.append(f"source {source_id} 的 required 必须是布尔值")

    signals = scenario.get("signals")
    if not isinstance(signals, list) or not signals:
        errors.append("signals 不能为空")
        signals = []

    signal_ids: set[str] = set()
    for signal in signals:
        if not isinstance(signal, dict):
            errors.append("signals 的每一项必须是对象")
            continue
        signal_id = str(signal.get("id") or "")
        if not signal_id:
            errors.append("signal 缺少 id")
        elif signal_id in signal_ids:
            errors.append(f"signal id 重复：{signal_id}")
        signal_ids.add(signal_id)

        if signal.get("kind") not in SIGNAL_KINDS:
            errors.append(f"signal {signal_id} 的 kind 不支持：{signal.get('kind')}")

    for signal in signals:
        if not isinstance(signal, dict):
            continue
        signal_id = str(signal.get("id") or "")
        kind = signal.get("kind")
        source_id = signal.get("source")

        if source_id and source_id not in source_ids:
            errors.append(f"signal {signal_id} 引用了不存在的 source：{source_id}")
        if kind in ("count", "exists", "value", "distinct_count", "max", "min", "avg", "sum", "latest"):
            if not source_id:
                errors.append(f"signal {signal_id} 缺少 source")
        if kind == "ratio":
            for key in ("numerator", "denominator"):
                ref = signal.get(key)
                if ref not in signal_ids:
                    errors.append(
                        f"signal {signal_id} 的 {key} 引用了不存在的 signal：{ref}"
                    )
        if kind == "threshold_flag":
            ref = signal.get("signal")
            if ref not in signal_ids:
                errors.append(
                    f"signal {signal_id} 引用了不存在的 signal：{ref}"
                )

    steps = scenario.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps 不能为空")
        steps = []

    step_ids: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            errors.append("steps 的每一项必须是对象")
            continue

        step_id = str(step.get("id") or "")
        if not step_id:
            errors.append("step 缺少 id")
        elif step_id in step_ids:
            errors.append(f"step id 重复：{step_id}")
        step_ids.add(step_id)

        if not str(step.get("title") or "").strip():
            errors.append(f"step {step_id} 缺少 title")

        for source_id in step.get("uses") or []:
            if source_id not in source_ids:
                errors.append(f"step {step_id} 引用了不存在的 source：{source_id}")

        if step.get("observe") not in signal_ids:
            errors.append(
                f"step {step_id} 的 observe 引用了不存在的 signal：{step.get('observe')}"
            )

        if step.get("when") and (
            _condition_signals(step["when"]) - signal_ids
        ):
            errors.append(
                f"step {step_id} 的 when 引用了不存在的 signal："
                f"{sorted(_condition_signals(step['when']) - signal_ids)[0]}"
            )

        if step.get("on_missing") not in ("skip", "mark_unverified", "block"):
            errors.append(f"step {step_id} 的 on_missing 不合法")

        criteria = step.get("criteria")
        if not isinstance(criteria, dict) or not (criteria.get("pass") or criteria.get("fail")):
            errors.append(f"step {step_id} 至少需要 pass 或 fail 判定")
        else:
            for key in ("pass", "fail"):
                condition = criteria.get(key)
                if condition:
                    unknown = _condition_signals(condition) - signal_ids
                    if unknown:
                        errors.append(
                            f"step {step_id} 的 {key} 引用了不存在的 signal："
                            f"{sorted(unknown)[0]}"
                        )

    rules = scenario.get("rules")
    if not isinstance(rules, list) or not rules:
        errors.append("rules 不能为空")
        rules = []

    rule_ids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            errors.append("rules 的每一项必须是对象")
            continue

        rule_id = str(rule.get("id") or "")
        if not rule_id:
            errors.append("rule 缺少 id")
        elif rule_id in rule_ids:
            errors.append(f"rule id 重复：{rule_id}")
        rule_ids.add(rule_id)

        unknown = _condition_signals(rule.get("when")) - signal_ids
        if unknown:
            errors.append(
                f"rule {rule_id} 的 when 引用了不存在的 signal：{sorted(unknown)[0]}"
            )

        for source_id in rule.get("requires") or []:
            if source_id not in source_ids:
                errors.append(f"rule {rule_id} 引用了不存在的 source：{source_id}")

        then = rule.get("then")
        if not isinstance(then, dict) or then.get("type") not in ("hypothesis", "action"):
            errors.append(f"rule {rule_id} 的 then.type 必须是 hypothesis/action")
        if not isinstance(then, dict) or not str(then.get("statement") or "").strip():
            errors.append(f"rule {rule_id} 的 then 缺少 statement")

        if isinstance(then, dict):
            for ref in then.get("evidence_refs") or []:
                ref = str(ref)
                if ref.startswith("S:"):
                    parts = ref[2:].split(".", 1)
                    if len(parts) != 2 or parts[0] not in source_ids or parts[1] not in signal_ids:
                        errors.append(f"rule {rule_id} 的 evidence_ref 不存在：{ref}")

    policy = scenario.get("output_policy")
    if not isinstance(policy, dict):
        errors.append("output_policy 必须是对象")
        policy = {}

    if policy.get("max_confidence_without_trace") not in CONFIDENCE_RANK:
        errors.append("output_policy.max_confidence_without_trace 不合法")

    if not isinstance(policy.get("forbid_root_cause", True), bool):
        errors.append("output_policy.forbid_root_cause 必须是布尔值")

    for item in _walk(scenario):
        if not isinstance(item, dict):
            continue
        for key in item:
            if str(key).lower() in BANNED_KEYS:
                errors.append(f"配置中不允许出现敏感字段：{key}")

    for placeholder in _collect_placeholders(scenario):
        if placeholder not in ALLOWED_PLACEHOLDERS:
            errors.append(f"不支持的占位符：{placeholder}")

    if policy.get("forbid_root_cause", True):
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            statement = str((rule.get("then") or {}).get("statement") or "")
            if any(word in statement for word in ROOT_CAUSE_WORDS):
                errors.append(
                    f"rule {rule.get('id')} 在禁止根因的配置下使用了禁用词"
                )

    return errors


# ----------------------------------------------------------------------
# 注册中心
# ----------------------------------------------------------------------
class FaultScenarioRegistry:
    """加载并校验 ``configs/fault_scenarios/*.json``。"""

    def __init__(self, directory: Optional[str | Path] = None):
        self.directory = Path(directory) if directory else DEFAULT_CONFIG_DIR
        self.scenarios: dict[str, dict] = {}
        self.errors: list[dict] = []
        self.load()

    def load(self) -> None:
        self.scenarios = {}
        self.errors = []

        if not self.directory.exists():
            self.errors.append({
                "file": str(self.directory),
                "errors": ["场景配置目录不存在"],
            })
            return

        for path in sorted(self.directory.glob("*.json")):
            try:
                scenario = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.errors.append({
                    "file": path.name,
                    "errors": [f"JSON 解析失败：{exc}"],
                })
                continue

            errors = validate_scenario(scenario)
            if errors:
                self.errors.append({"file": path.name, "errors": errors})
                continue

            scenario_id = scenario["id"]
            if scenario_id in self.scenarios:
                self.errors.append({
                    "file": path.name,
                    "errors": [f"场景 ID 重复：{scenario_id}"],
                })
                continue

            if not scenario.get("enabled", True):
                continue

            self.scenarios[scenario_id] = scenario

    def enabled(self) -> list[dict]:
        return sorted(
            self.scenarios.values(),
            key=lambda item: (-int(item.get("priority", 0)), item["id"]),
        )

    def get(self, scenario_id: str) -> Optional[dict]:
        return self.scenarios.get(scenario_id)


# ----------------------------------------------------------------------
# 匹配
# ----------------------------------------------------------------------
def _resolve_placeholders(
    value: Any,
    anchor: Evidence,
    start: Optional[str] = None,
    end: Optional[str] = None,
    product: Optional[str] = None,
) -> Any:
    """替换配置中的受控占位符；未识别的占位符原样保留。"""

    mapping = {
        "{{anchor.uid}}": anchor.uid or "",
        "{{anchor.error_code}}": anchor.error_code or "",
        "{{anchor.service}}": anchor.service or "",
        "{{anchor.product}}": product or "",
        "{{window.start}}": start or "",
        "{{window.end}}": end or "",
    }

    if isinstance(value, str):
        result = value
        for placeholder, replacement in mapping.items():
            result = result.replace(placeholder, replacement)
        return result
    if isinstance(value, list):
        return [
            _resolve_placeholders(item, anchor, start, end, product)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _resolve_placeholders(item, anchor, start, end, product)
            for key, item in value.items()
        }
    return value


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _text_of(feedback: dict) -> str:
    return normalize_text(
        " ".join(
            str(feedback.get(key) or "")
            for key in ("description", "title", "error_code")
        )
    )


def _hit_count(values: Iterable[str], text: str) -> int:
    return sum(
        1
        for value in values
        if str(value).strip() and normalize_text(str(value)) in text
    )


def match_scenario(
    feedback: dict,
    scenarios: Iterable[dict],
) -> tuple[Optional[dict], int, list[str]]:
    """返回 (最佳场景, 分数, 命中理由)。"""

    product = str(feedback.get("product") or "").strip()
    error_code = str(feedback.get("error_code") or "").strip()
    platform = str(feedback.get("platform") or "").strip()
    text = _text_of(feedback)
    symptoms = extract_symptoms(feedback.get("description"))
    features = extract_features(feedback.get("description"))

    best: Optional[dict] = None
    best_score = 0
    best_reasons: list[str] = []

    for scenario in scenarios:
        match = scenario.get("match") or {}
        score = 0
        reasons: list[str] = []

        if match.get("always") is True:
            score += 40
            reasons.append("通用兜底场景")

        products = [str(item) for item in (match.get("products") or [])]
        if products:
            if product not in products:
                continue
            score += 40
            reasons.append(f"产品命中：{product}")

        error_codes = [str(item) for item in (match.get("error_codes") or [])]
        if error_codes:
            if error_code and error_code not in error_codes:
                continue
            if error_code in error_codes:
                score += 30
                reasons.append(f"错误码命中：{error_code}")

        keywords = [str(item) for item in (match.get("keywords_any") or [])]
        keyword_hits = _hit_count(keywords, text)
        if keyword_hits:
            score += min(keyword_hits * 10, 30)
            reasons.append(f"关键词命中：{keyword_hits} 个")

        symptom_values = [str(item) for item in (match.get("symptoms_any") or [])]
        symptom_hits = _hit_count(symptom_values, text) if symptom_values else 0
        if symptom_hits:
            score += min(symptom_hits * 10, 20)
            reasons.append(f"现象命中：{symptom_hits} 个")

        feature_values = [str(item) for item in (match.get("features_any") or [])]
        feature_hits = _hit_count(feature_values, text) if feature_values else 0
        if feature_hits:
            score += min(feature_hits * 10, 20)
            reasons.append(f"功能命中：{feature_hits} 个")

        platforms = [str(item) for item in (match.get("platforms") or [])]
        if platforms and platform in platforms:
            score += 5
            reasons.append(f"平台命中：{platform}")

        excludes = [str(item) for item in (match.get("exclude_keywords") or [])]
        if _hit_count(excludes, text):
            score -= 20
            reasons.append("命中排除词，降分")

        # 记录提取出的现象/功能，便于后续解释；不参与打分避免重复。
        if symptoms and not symptom_values:
            reasons.append("已提取现象词：" + "、".join(symptoms[:3]))
        if features and not feature_values:
            reasons.append("已提取功能词：" + "、".join(features[:3]))

        if score < 40:
            continue

        if (
            best is None
            or score > best_score
            or (
                score == best_score
                and int(scenario.get("priority", 0)) > int(best.get("priority", 0))
            )
        ):
            best = scenario
            best_score = score
            best_reasons = reasons

    return best, best_score, best_reasons


# ----------------------------------------------------------------------
# 规则执行
# ----------------------------------------------------------------------
def _compare(left: Any, op: str, right: Any = None) -> bool:
    if op == "is_null":
        return left is None
    if op == "exists":
        return left is not None
    if left is None:
        return False

    try:
        if op == "eq":
            return left == right
        if op == "ne":
            return left != right
        if op == "gt":
            return float(left) > float(right)
        if op == "gte":
            return float(left) >= float(right)
        if op == "lt":
            return float(left) < float(right)
        if op == "lte":
            return float(left) <= float(right)
        if op == "in":
            return left in (right or [])
    except (TypeError, ValueError):
        return False

    return False


def evaluate_condition(condition: Any, values: dict[str, Any]) -> bool:
    """执行受控条件树，不 eval 任意表达式。"""

    if condition is None:
        return True
    if not isinstance(condition, dict):
        return False

    if "all" in condition:
        return all(evaluate_condition(item, values) for item in condition["all"])
    if "any" in condition:
        return any(evaluate_condition(item, values) for item in condition["any"])
    if "not" in condition:
        return not evaluate_condition(condition["not"], values)

    signal_id = condition.get("signal")
    if signal_id is None:
        return False

    return _compare(values.get(signal_id), condition.get("op", "eq"), condition.get("value"))


# ----------------------------------------------------------------------
# 场景执行
# ----------------------------------------------------------------------
class ScenarioRunner:
    """执行一个场景，产出可解释的 ScenarioRun。"""

    def __init__(
        self,
        feedback_repository: Optional[FeedbackRepository] = None,
        evidence_repository: Optional[EvidenceRepository] = None,
        db_path=None,
        manual_answers: Optional[dict[str, Any]] = None,
        connector_provider: Optional[Callable[[dict, dict], list[dict]]] = None,
    ):
        self.feedback_repository = feedback_repository or FeedbackRepository(db_path)
        self.evidence_repository = evidence_repository or EvidenceRepository(db_path)
        self.manual_answers = manual_answers or {}
        self.connector_provider = connector_provider

    def run(
        self,
        workspace_id: str,
        feedback: dict,
        scenario: dict,
        anchor: Optional[Evidence] = None,
    ) -> dict:
        anchor = anchor or Evidence.from_feedback_record(feedback)
        source_results = self._load_sources(workspace_id, feedback, scenario, anchor)
        signal_values = self._compute_signals(scenario, source_results)
        steps = self._run_steps(scenario, source_results, signal_values)
        facts, hypotheses, actions = self._run_rules(
            scenario, source_results, signal_values, steps
        )

        missing_sources = [
            {
                "id": source_id,
                "description": result.get("description") or source_id,
                "reason": result.get("reason") or "不可用",
            }
            for source_id, result in source_results.items()
            if not result.get("available")
        ]

        required_missing = [
            item for item in missing_sources
            if any(
                source.get("id") == item["id"] and source.get("required")
                for source in scenario.get("sources") or []
            )
        ]

        blocked_steps = [
            step for step in steps
            if step["status"] == "unknown" and step.get("on_missing") == "block"
        ]
        unknown_steps = [step for step in steps if step["status"] == "unknown"]

        if required_missing or blocked_steps:
            status = "blocked"
        elif missing_sources or unknown_steps:
            status = "partial"
        else:
            status = "verified"

        confidence = self._confidence(scenario, hypotheses, status, anchor)
        limits = self._limits(scenario, status, missing_sources, anchor)
        next_actions = self._next_actions(
            scenario, steps, missing_sources, actions
        )

        evidence_refs = []
        evidence_ids = []
        for item in facts + hypotheses:
            for ref in item.get("evidence_refs") or []:
                if ref not in evidence_refs:
                    evidence_refs.append(ref)
            for evidence_id in item.get("evidence_ids") or []:
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)

        return {
            "scenario_id": scenario["id"],
            "name": scenario.get("name") or scenario["id"],
            "version": scenario.get("version"),
            "status": status,
            "match": {
                "score": 0,
                "reasons": [],
            },
            "facts": facts,
            "hypotheses": hypotheses,
            "steps": steps,
            "sources": {
                source_id: {
                    "available": result.get("available"),
                    "count": len(result.get("records") or []),
                    "reason": result.get("reason"),
                }
                for source_id, result in source_results.items()
            },
            "signals": signal_values,
            "missing_sources": missing_sources,
            "next_actions": next_actions,
            "confidence": confidence,
            "limits": limits,
            "evidence_refs": evidence_refs,
            "evidence_ids": evidence_ids,
        }

    # ------------------------------------------------------------------
    # 数据源
    # ------------------------------------------------------------------
    def _load_sources(
        self,
        workspace_id: str,
        feedback: dict,
        scenario: dict,
        anchor: Evidence,
    ) -> dict[str, dict]:
        results = {}

        for source in scenario.get("sources") or []:
            source_id = source["id"]
            kind = source["kind"]
            description = source.get("description") or source_id

            if kind == "feedback":
                results[source_id] = self._load_feedback_source(
                    workspace_id, source, feedback, anchor
                )
            elif kind == "evidence":
                results[source_id] = self._load_evidence_source(
                    workspace_id, source, feedback, anchor
                )
            elif kind == "manual":
                results[source_id] = self._load_manual_source(source_id)
            elif kind == "connector":
                results[source_id] = self._load_connector_source(
                    source, feedback, anchor
                )
            else:
                results[source_id] = {
                    "available": False,
                    "records": [],
                    "description": description,
                    "reason": f"{kind} 数据源尚未接入",
                }

            results[source_id].setdefault("description", description)

        return results

    def _window(self, source: dict, anchor: Evidence) -> tuple[Optional[str], Optional[str]]:
        if anchor.timestamp is None:
            return None, None

        minutes = int(source.get("window_minutes") or 30)
        start = anchor.timestamp - timedelta(minutes=max(minutes, 1))
        end = anchor.timestamp + timedelta(minutes=max(minutes, 1))
        return format_timestamp(start), format_timestamp(end)

    def _load_feedback_source(
        self,
        workspace_id: str,
        source: dict,
        feedback: dict,
        anchor: Evidence,
    ) -> dict:
        query = source.get("query") or {}
        start, end = self._window(source, anchor)
        query = _resolve_placeholders(
            query, anchor, start, end, feedback.get("product")
        )

        records = self.feedback_repository.query_feedback(
            workspace_id,
            products=_as_list(query.get("products")),
            error_codes=_as_list(query.get("error_codes")),
            uid=query.get("uid"),
            start=start,
            end=end,
            limit=source.get("limit") or query.get("limit") or 200,
        )

        # 锚点本身也是反馈证据的一部分，避免过滤条件把它排除后 step 看不到。
        if feedback.get("id") and all(
            str(item.get("id")) != str(feedback.get("id")) for item in records
        ):
            records.append(feedback)

        return {
            "available": True,
            "records": records,
            "description": source.get("description") or source["id"],
            "reason": None,
        }

    def _load_evidence_source(
        self,
        workspace_id: str,
        source: dict,
        feedback: dict,
        anchor: Evidence,
    ) -> dict:
        query = source.get("query") or {}
        start, end = self._window(source, anchor)
        query = _resolve_placeholders(
            query, anchor, start, end, feedback.get("product")
        )

        records = self.evidence_repository.query_evidence(
            workspace_id,
            evidence_types=_as_list(query.get("evidence_types")),
            service=query.get("service"),
            uid=query.get("uid"),
            error_code=query.get("error_code"),
            trace_id=query.get("trace_id"),
            request_id=query.get("request_id"),
            titles=_as_list(query.get("titles")),
            keyword=query.get("keyword"),
            start=start,
            end=end,
            limit=source.get("limit") or query.get("limit") or 200,
        )

        record_dicts = [item.to_dict() for item in records]

        return {
            "available": True,
            "records": record_dicts,
            "evidence_ids": [
                item["evidence_id"]
                for item in record_dicts
                if item.get("evidence_id")
            ],
            "description": source.get("description") or source["id"],
            "reason": None,
        }

    def _load_manual_source(self, source_id: str) -> dict:
        if source_id not in self.manual_answers:
            return {
                "available": False,
                "records": [],
                "description": source_id,
                "reason": "等待人工确认",
            }

        answer = self.manual_answers[source_id]

        if isinstance(answer, str):
            answer = normalize_text(answer) in {
                "是",
                "已有",
                "有",
                "yes",
                "true",
                "1",
                "已有 trace",
            }

        return {
            "available": True,
            "records": [{"value": answer}],
            "description": source_id,
            "reason": None,
        }

    def _load_connector_source(
        self,
        source: dict,
        feedback: dict,
        anchor: Evidence,
    ) -> dict:
        if self.connector_provider is None:
            return {
                "available": False,
                "records": [],
                "description": source.get("description") or source["id"],
                "reason": "第一阶段未接入实时连接器",
            }

        start, end = self._window(source, anchor)
        resolved = _resolve_placeholders(
            source, anchor, start, end, feedback.get("product")
        )

        try:
            records = self.connector_provider(resolved, feedback) or []
        except Exception as exc:
            return {
                "available": False,
                "records": [],
                "description": source.get("description") or source["id"],
                "reason": f"连接器查询失败：{exc}",
            }

        return {
            "available": True,
            "records": records,
            "description": source.get("description") or source["id"],
            "reason": None,
        }

    # ------------------------------------------------------------------
    # 信号
    # ------------------------------------------------------------------
    def _compute_signals(
        self,
        scenario: dict,
        source_results: dict[str, dict],
    ) -> dict[str, Any]:
        values: dict[str, Any] = {}
        signals = scenario.get("signals") or []

        # 允许多轮，处理 ratio / threshold_flag 依赖其他信号的情况。
        for _ in range(len(signals) + 1):
            changed = False

            for signal in signals:
                signal_id = signal["id"]
                if signal_id in values:
                    continue

                try:
                    value = self._compute_one_signal(signal, source_results, values)
                except Exception:
                    value = None

                if value is not None or signal.get("source") in source_results:
                    values[signal_id] = value
                    changed = True

            if not changed:
                break

        for signal in signals:
            values.setdefault(signal["id"], None)

        return values

    def _compute_one_signal(
        self,
        signal: dict,
        source_results: dict[str, dict],
        values: dict[str, Any],
    ) -> Any:
        kind = signal.get("kind")
        source_id = signal.get("source")

        if kind == "ratio":
            numerator = values.get(signal.get("numerator"))
            denominator = values.get(signal.get("denominator"))
            if numerator is None or denominator in (None, 0):
                return None
            return float(numerator) / float(denominator)

        if kind == "threshold_flag":
            reference = values.get(signal.get("signal"))
            if reference is None:
                return None
            return _compare(
                reference,
                signal.get("op", "eq"),
                signal.get("value"),
            )

        if source_id not in source_results:
            return None

        result = source_results[source_id]
        if not result.get("available"):
            return None

        records = result.get("records") or []
        field = signal.get("field")

        if kind == "count":
            return len(records)
        if kind == "exists":
            return bool(records)
        if kind == "value":
            if not records:
                return None
            return _record_get(records[0], field)
        if kind == "distinct_count":
            return len({
                _record_get(record, field)
                for record in records
                if _record_get(record, field) not in (None, "")
            })

        numeric = []
        for record in records:
            raw = _record_get(record, field)
            try:
                if raw is not None and raw != "":
                    numeric.append(float(raw))
            except (TypeError, ValueError):
                continue

        if kind == "max":
            return max(numeric) if numeric else None
        if kind == "min":
            return min(numeric) if numeric else None
        if kind == "sum":
            return sum(numeric) if numeric else None
        if kind == "avg":
            return sum(numeric) / len(numeric) if numeric else None
        if kind == "latest":
            if not records:
                return None
            record = max(
                records,
                key=lambda item: str(_record_get(item, "timestamp") or ""),
            )
            return _record_get(record, field)

        return None

    # ------------------------------------------------------------------
    # 步骤与规则
    # ------------------------------------------------------------------
    def _run_steps(
        self,
        scenario: dict,
        source_results: dict[str, dict],
        values: dict[str, Any],
    ) -> list[dict]:
        steps = []

        for step in scenario.get("steps") or []:
            uses = step.get("uses") or []
            missing = [
                source_id
                for source_id in uses
                if not source_results.get(source_id, {}).get("available")
            ]

            if step.get("when") and not evaluate_condition(step["when"], values):
                steps.append(self._step_result(step, "skipped", None, "条件不满足"))
                continue

            if missing:
                reason = "数据源不可用：" + "、".join(missing)
                status = (
                    "skipped"
                    if step.get("on_missing") == "skip"
                    else "unknown"
                )
                steps.append(self._step_result(step, status, None, reason))
                continue

            value = values.get(step.get("observe"))
            if value is None:
                reason = "信号无数据：" + str(step.get("observe"))
                status = (
                    "skipped"
                    if step.get("on_missing") == "skip"
                    else "unknown"
                )
                steps.append(self._step_result(step, status, None, reason))
                continue

            criteria = step.get("criteria") or {}
            if criteria.get("pass") and evaluate_condition(criteria["pass"], values):
                status = "pass"
                reason = ""
            elif criteria.get("fail") and evaluate_condition(criteria["fail"], values):
                status = "fail"
                reason = ""
            else:
                status = "unknown"
                reason = "判定条件未命中"

            steps.append(self._step_result(step, status, value, reason))

        return steps

    @staticmethod
    def _step_result(
        step: dict,
        status: str,
        value: Any,
        reason: str,
    ) -> dict:
        return {
            "id": step["id"],
            "title": step.get("title") or step["id"],
            "status": status,
            "observation": _display_value(value),
            "value": value,
            "reason": reason,
            "on_missing": step.get("on_missing", "mark_unverified"),
            "guidance": step.get("guidance") or "",
            "uses": step.get("uses") or [],
            "observe": step.get("observe"),
        }

    def _run_rules(
        self,
        scenario: dict,
        source_results: dict[str, dict],
        values: dict[str, Any],
        steps: list[dict],
    ) -> tuple[list[dict], list[dict], list[str]]:
        facts = []
        hypotheses = []
        actions = []

        source_by_signal = {
            signal["id"]: signal.get("source")
            for signal in scenario.get("signals") or []
        }

        for step in steps:
            if step["status"] not in ("pass", "fail"):
                continue

            signal_id = step.get("observe")
            source_id = source_by_signal.get(signal_id)
            refs = [f"S:{source_id}.{signal_id}"] if source_id else []

            facts.append({
                "statement": f"{step['title']}：{step['observation']}",
                "evidence_refs": refs,
                "evidence_ids": self._refs_to_evidence_ids(
                    refs, source_results
                ),
                "step_id": step["id"],
                "status": step["status"],
            })

        for rule in sorted(
            scenario.get("rules") or [],
            key=lambda item: -int(item.get("priority", 0)),
        ):
            if any(
                not source_results.get(source_id, {}).get("available")
                for source_id in rule.get("requires") or []
            ):
                continue

            if not evaluate_condition(rule.get("when"), values):
                continue

            then = rule.get("then") or {}
            refs = list(then.get("evidence_refs") or [])
            item = {
                "statement": str(then.get("statement") or ""),
                "confidence": then.get("confidence") or "low",
                "evidence_refs": refs,
                "evidence_ids": self._refs_to_evidence_ids(
                    refs, source_results
                ),
                "rule_id": rule.get("id"),
            }

            if then.get("type") == "hypothesis":
                min_refs = int(
                    (scenario.get("output_policy") or {}).get(
                        "min_evidence_refs_for_hypothesis", 0
                    )
                    or 0
                )
                if (
                    item["statement"]
                    and len(item["evidence_refs"]) >= min_refs
                    and not any(
                        existing["statement"] == item["statement"]
                        for existing in hypotheses
                    )
                ):
                    hypotheses.append(item)
            elif item["statement"]:
                actions.append(item["statement"])

            actions.extend(str(action) for action in rule.get("actions") or [])

        return facts, hypotheses, actions

    @staticmethod
    def _refs_to_evidence_ids(
        refs: list[str],
        source_results: dict[str, dict],
    ) -> list[str]:
        """把 S:<source>.<signal> 引用展开为可追溯的证据 ID。"""

        evidence_ids: list[str] = []

        for ref in refs:
            if not str(ref).startswith("S:"):
                continue

            source_id = str(ref)[2:].split(".", 1)[0]
            for evidence_id in (
                source_results.get(source_id, {}).get("evidence_ids") or []
            ):
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)

        return evidence_ids

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------
    def _confidence(
        self,
        scenario: dict,
        hypotheses: list[dict],
        status: str,
        anchor: Evidence,
    ) -> str:
        policy = scenario.get("output_policy") or {}
        max_without_trace = policy.get("max_confidence_without_trace") or "medium"

        if not hypotheses:
            rank = CONFIDENCE_RANK["low"]
        else:
            rank = max(
                CONFIDENCE_RANK.get(item.get("confidence"), 1)
                for item in hypotheses
            )

        if status == "blocked":
            rank = min(rank, CONFIDENCE_RANK["low"])
        elif status == "partial":
            rank = min(rank, CONFIDENCE_RANK["medium"])

        has_trace = bool(anchor.trace_id or anchor.request_id)
        if not has_trace:
            rank = min(rank, CONFIDENCE_RANK.get(max_without_trace, 2))

        return CONFIDENCE_BY_RANK[rank]

    @staticmethod
    def _limits(
        scenario: dict,
        status: str,
        missing_sources: list[dict],
        anchor: Evidence,
    ) -> list[str]:
        policy = scenario.get("output_policy") or {}
        limits = []

        disclaimer = policy.get("disclaimer")
        if disclaimer:
            limits.append(str(disclaimer))

        if missing_sources:
            limits.append(
                "缺失数据源："
                + "、".join(item["id"] for item in missing_sources)
                + "；相关步骤未验证。"
            )

        if not (anchor.trace_id or anchor.request_id):
            limits.append("无请求级 Trace / Request ID，不能定位到具体代码行。")
        if status == "blocked":
            limits.append("关键数据源不可用，本次只输出缺失清单，不输出方向性假设。")

        return limits

    @staticmethod
    def _next_actions(
        scenario: dict,
        steps: list[dict],
        missing_sources: list[dict],
        rule_actions: list[str],
    ) -> list[str]:
        actions = []

        for source in missing_sources:
            actions.append(
                f"补充数据源：{source.get('description') or source['id']}"
            )

        for step in steps:
            if step["status"] == "unknown" and step.get("guidance"):
                actions.append(f"{step['title']}：{step['guidance']}")

        actions.extend(rule_actions)

        deduped = []
        for action in actions:
            text = " ".join(str(action).split())
            if text and text not in deduped:
                deduped.append(text)

        return deduped


def _display_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)
