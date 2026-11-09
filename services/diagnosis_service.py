"""基于证据的诊断服务。

设计原则：
1. 先算证据，再下结论。诊断的每个结论都必须挂到具体证据 ID 上。
2. 事实与推断严格分开：字段一致才能写成「已验证事实」，其余一律标推断。
3. 没有 LLM 也能工作：规则诊断始终可用；配置 LLM 只是把推断写得更自然。
4. LLM 的输出要过引用校验，引用不存在的证据 ID 一律打回，退回规则诊断。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Optional

from core.evidence import Evidence
from services.evidence_service import TYPE_LABELS, EvidenceService
from services.fault_scenario_service import (
    FaultScenarioRegistry,
    ScenarioRunner,
    match_scenario,
)
from services.llm_config import resolve_settings


CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
}

RELATION_LABELS = {
    "correlates": "关联",
    "context": "上下文",
    "supports": "佐证",
    "contradicts": "反证",
}

SYSTEM_PROMPT = (
    "你是企业技术支持诊断助手。只能使用给定证据作答，"
    "不允许编造证据 ID、日志内容或指标数值。"
    "输出必须是 JSON 对象，不要输出解释性文字或 Markdown 代码块。"
)


class DiagnosisService:
    """把证据上下文变成可追溯的诊断结论。"""

    def __init__(
        self,
        evidence_service: Optional[EvidenceService] = None,
        llm_client: Optional[Callable[[str], str]] = None,
        db_path=None,
        scenario_registry: Optional[FaultScenarioRegistry] = None,
    ):
        self.evidence_service = evidence_service or EvidenceService(db_path=db_path)
        self._llm_client = llm_client
        self.scenario_registry = scenario_registry
        self._scenario_registry_loaded = scenario_registry is not None

    def _get_scenario_registry(self) -> FaultScenarioRegistry:
        if not self._scenario_registry_loaded:
            self.scenario_registry = FaultScenarioRegistry()
            self._scenario_registry_loaded = True
        return self.scenario_registry

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    @property
    def llm_available(self) -> bool:
        if self._llm_client is not None:
            return True
        return resolve_settings().available

    @property
    def llm_model(self) -> str:
        return resolve_settings().model

    @property
    def llm_source(self) -> str:
        if self._llm_client is not None:
            return "injected"
        return resolve_settings().source

    def _call_llm(self, prompt: str) -> str:
        if self._llm_client is not None:
            return self._llm_client(prompt)

        from openai import OpenAI  # 延迟导入，未配置时不增加启动成本

        settings = resolve_settings()

        if not settings.available:
            raise RuntimeError("未配置 LLM API Key")

        client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url or None,
            timeout=settings.timeout,
        )

        response = client.chat.completions.create(
            model=self.llm_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )

        return response.choices[0].message.content or ""

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def build(
        self,
        workspace_id: str,
        feedback_id: str,
        window_minutes: int = 30,
        min_score: float = 30.0,
        use_llm: bool = False,
        manual_answers: Optional[dict[str, Any]] = None,
    ) -> dict:
        context = self.evidence_service.build_context(
            workspace_id=workspace_id,
            feedback_id=feedback_id,
            window_minutes=window_minutes,
            min_score=min_score,
        )

        refs = _collect_refs(context)
        result = self._rule_diagnosis(context, refs)
        result["generated_by"] = "rule"
        result["llm_error"] = None

        scenario_run = self._run_scenario(
            workspace_id=workspace_id,
            context=context,
            manual_answers=manual_answers or {},
        )
        result["scenario"] = scenario_run

        if use_llm:
            if not self.llm_available:
                result["llm_error"] = (
                    "未配置 LLM：可在「报告中心 → LLM 配置」中填写，"
                    "或设置环境变量 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL。"
                )
            else:
                try:
                    payload = self._parse_llm_json(
                        self._call_llm(self._build_prompt(context, refs, result))
                    )
                    result = self._merge_llm_result(result, payload, refs)
                    result["generated_by"] = "llm"
                except Exception as exc:  # 引用校验失败或调用失败都退回规则诊断
                    result["llm_error"] = f"LLM 诊断未采用：{exc}"

        if scenario_run and scenario_run.get("next_actions"):
            result["next_steps"] = self._merge_scenario_actions(
                result.get("next_steps") or [], scenario_run
            )

        result["markdown"] = render_markdown(result)
        result["markdown_body"] = render_markdown(result, include_scenario=False)
        result["context_counts"] = context["counts"]
        result["context"] = context
        return result

    def _run_scenario(
        self,
        workspace_id: str,
        context: dict,
        manual_answers: dict[str, Any],
    ) -> Optional[dict]:
        """匹配并执行故障场景；配置不可用时返回 None，不影响规则诊断。"""

        try:
            registry = self._get_scenario_registry()
        except Exception:
            return None

        if not registry.scenarios:
            return None

        feedback = context.get("anchor_record") or {}
        scenario, score, reasons = match_scenario(feedback, registry.enabled())

        if not scenario:
            return None

        runner = ScenarioRunner(
            feedback_repository=self.evidence_service.feedback_repository,
            evidence_repository=self.evidence_service.evidence_repository,
            manual_answers=manual_answers,
        )
        run = runner.run(
            workspace_id=workspace_id,
            feedback=feedback,
            scenario=scenario,
            anchor=context.get("anchor"),
        )
        run["match"] = {"score": score, "reasons": reasons}
        return run

    @staticmethod
    def _merge_scenario_actions(
        next_steps: list[dict],
        scenario_run: dict,
    ) -> list[dict]:
        existing = {
            " ".join(str(item.get("action") or "").split())
            for item in next_steps
        }
        merged = list(next_steps)

        for action in scenario_run.get("next_actions") or []:
            text = " ".join(str(action).split())
            if not text or text in existing:
                continue
            existing.add(text)
            merged.append({
                "action": text,
                "rationale": (
                    f"故障场景「{scenario_run.get('name') or scenario_run['scenario_id']}」"
                    " 的排查动作"
                ),
                "evidence_ids": [],
            })

        return merged

    # ------------------------------------------------------------------
    # 规则诊断
    # ------------------------------------------------------------------
    def _rule_diagnosis(self, context: dict, refs: dict[str, dict]) -> dict:
        anchor: Evidence = context["anchor"]
        matches = context["matches"]
        counts = context["counts"]

        strong = [item for item in matches if item["strength"] == "strong"]
        context_items = [item for item in matches if item["relation"] == "context"]

        conclusions: list[dict] = []

        for fact in context["facts"]:
            conclusions.append({
                "kind": "fact",
                "statement": fact["statement"],
                "evidence_ids": fact["evidence_ids"],
                "confidence": "high",
            })

        if not context["facts"]:
            conclusions.append({
                "kind": "inference",
                "statement": "窗口内没有标识完全一致的证据，无法确认真实故障面。",
                "evidence_ids": [],
                "confidence": "low",
            })

        error_logs = [
            item for item in strong
            if item["evidence"].evidence_type == "log"
            and item["evidence"].error_code
            and item["evidence"].error_code == anchor.error_code
        ]

        if error_logs:
            conclusions.append({
                "kind": "inference",
                "statement": (
                    f"窗口内 {len(error_logs)} 条强关联日志都带错误码"
                    f"「{anchor.error_code}」，故障方向与反馈描述一致。"
                ),
                "evidence_ids": [_ref_id(item["evidence"]) for item in error_logs],
                "confidence": "high",
            })

        if context_items:
            metric_names = []
            for item in context_items:
                title = item["evidence"].title or item["evidence"].record_id
                value = (item["evidence"].attributes or {}).get("metric_value")
                metric_names.append(
                    f"{title}={value}" if value is not None else str(title)
                )

            conclusions.append({
                "kind": "inference",
                "statement": (
                    "指标侧存在同服务、同窗口的异常读数："
                    + "、".join(metric_names[:4])
                    + "。指标只作方向佐证，不能单独定位代码缺陷。"
                ),
                "evidence_ids": [_ref_id(item["evidence"]) for item in context_items],
                "confidence": "medium",
            })

        if counts["related_feedback"] > 0:
            conclusions.append({
                "kind": "inference",
                "statement": (
                    f"同一用户/错误码在窗口内还有 {counts['related_feedback']} 条反馈，"
                    "需判断是个体环境问题还是面上故障扩散。"
                ),
                "evidence_ids": [],
                "confidence": "medium",
            })

        next_steps = self._next_steps(anchor, context, strong, context_items)

        return {
            "title": _title(anchor),
            "severity": _severity(strong, counts),
            "confidence": _confidence(strong),
            "conclusions": conclusions,
            "next_steps": next_steps,
            "evidence_refs": list(refs.values()),
            "anchor": {
                "uid": anchor.uid,
                "error_code": anchor.error_code,
                "service": anchor.service,
                "message": anchor.message,
            },
        }

    def _next_steps(
        self,
        anchor: Evidence,
        context: dict,
        strong: list[dict],
        context_items: list[dict],
    ) -> list[dict]:
        steps = []

        if not anchor.trace_id:
            steps.append({
                "action": "补齐 Trace ID / Request ID",
                "rationale": "当前锚点缺少链路标识，只能按用户与时间窗口关联，定位精度有限。",
                "evidence_ids": [],
            })

        if any(
            "rtmp" in (item["evidence"].message or "").lower()
            or "cdn" in (item["evidence"].title or "").lower()
            for item in strong + context_items
        ):
            steps.append({
                "action": "核对推流与 CDN 侧状态",
                "rationale": "日志与指标同时指向播放链路，优先排查推流地址、CDN 回源与网络质量。",
                "evidence_ids": [
                    _ref_id(item["evidence"])
                    for item in (strong + context_items)[:3]
                ],
            })

        if context["counts"]["related_feedback"] > 0:
            steps.append({
                "action": "回访同 UID 的其他反馈并确认版本分布",
                "rationale": "同一用户连续反馈时，需区分个体环境与版本级问题。",
                "evidence_ids": [],
            })

        if not steps:
            steps.append({
                "action": "扩大时间窗口并补充更多证据源",
                "rationale": "当前证据量不足以下结论。",
                "evidence_ids": [],
            })

        return steps

    # ------------------------------------------------------------------
    # LLM 接入与校验
    # ------------------------------------------------------------------
    def _build_prompt(
        self,
        context: dict,
        refs: dict[str, dict],
        rule_result: dict,
    ) -> str:
        evidence_lines = []
        for ref_id, ref in refs.items():
            evidence_lines.append(
                f"- [{ref_id}] 类型={ref['type']} 关系={ref['relation']} "
                f"分数={ref['score']} 摘要={ref['label']}"
            )

        anchor = context["anchor"]
        facts = [
            f"- {fact['statement']}"
            for fact in context["facts"]
        ] or ["- 无标识一致的事实"]

        return "\n".join([
            "为下面这条用户反馈生成诊断，只能引用证据清单中的 ID。",
            "",
            "## 锚点反馈",
            f"UID={anchor.uid or '—'} 错误码={anchor.error_code or '—'} "
            f"产品={anchor.service or '—'} 描述={anchor.message or '—'}",
            "",
            "## 已验证事实",
            *facts,
            "",
            "## 证据清单",
            *evidence_lines,
            "",
            "## 规则诊断初稿",
            json.dumps(
                {
                    "conclusions": rule_result["conclusions"],
                    "next_steps": rule_result["next_steps"],
                },
                ensure_ascii=False,
            ),
            "",
            "输出 JSON：",
            '{"severity":"high|medium|low","conclusions":'
            '[{"statement":"...","evidence_ids":["E:..."],"confidence":"high|medium|low"}],'
            '"next_steps":[{"action":"...","rationale":"...","evidence_ids":["E:..."]}]}',
            "要求：3-5 条结论，至少 2 条引入证据 ID；不得出现清单外的 ID。",
        ])

    def _parse_llm_json(self, text: str) -> dict:
        cleaned = text.strip()

        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]

        payload = json.loads(cleaned)

        if not isinstance(payload, dict):
            raise ValueError("LLM 返回结构不是对象")

        return payload

    def _merge_llm_result(
        self,
        rule_result: dict,
        payload: dict,
        refs: dict[str, dict],
    ) -> dict:
        conclusions = payload.get("conclusions") or []
        next_steps = payload.get("next_steps") or []

        if not isinstance(conclusions, list) or not conclusions:
            raise ValueError("LLM 未给出结论")

        valid_conclusions = []
        cited_total = 0

        for item in conclusions:
            if not isinstance(item, dict) or not item.get("statement"):
                raise ValueError("结论缺少 statement")

            evidence_ids = [
                str(value) for value in (item.get("evidence_ids") or [])
            ]
            unknown = [value for value in evidence_ids if value not in refs]

            if unknown:
                raise ValueError(f"LLM 引用了不存在的证据：{unknown[0]}")

            cited_total += len(evidence_ids)
            valid_conclusions.append({
                "kind": "inference",
                "statement": str(item["statement"]),
                "evidence_ids": evidence_ids,
                "confidence": item.get("confidence") or "medium",
            })

        if cited_total == 0:
            raise ValueError("LLM 结论没有任何证据引用")

        merged_steps = []

        for item in next_steps:
            if not isinstance(item, dict) or not item.get("action"):
                continue

            evidence_ids = [
                str(value) for value in (item.get("evidence_ids") or [])
            ]
            unknown = [value for value in evidence_ids if value not in refs]
            if unknown:
                raise ValueError(f"LLM 建议引用了不存在的证据：{unknown[0]}")

            merged_steps.append({
                "action": str(item["action"]),
                "rationale": str(item.get("rationale") or ""),
                "evidence_ids": evidence_ids,
            })

        # 事实仍以规则层为准，LLM 只负责补充推断。
        facts = [
            item for item in rule_result["conclusions"] if item["kind"] == "fact"
        ]

        return {
            **rule_result,
            "severity": payload.get("severity") or rule_result["severity"],
            "conclusions": facts + valid_conclusions,
            "next_steps": merged_steps or rule_result["next_steps"],
        }


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------
def _ref_id(evidence: Evidence) -> str:
    """证据引用一律用库内主键，保证与事实区、关联表可对得上。"""

    return str(evidence.evidence_id or evidence.record_id or "unknown")


def _collect_refs(context: dict) -> dict[str, dict]:
    refs = {}

    for item in context["matches"]:
        evidence: Evidence = item["evidence"]
        ref_id = _ref_id(evidence)
        refs[ref_id] = {
            "id": ref_id,
            "short": evidence.record_id or ref_id[:8],
            "type": TYPE_LABELS.get(evidence.evidence_type, evidence.evidence_type),
            "relation": RELATION_LABELS.get(item.get("relation", "correlates"), "关联"),
            "score": item["score"],
            "label": evidence.display_label(),
        }

    return refs


def _title(anchor: Evidence) -> str:
    head = anchor.error_code or anchor.service or "问题诊断"
    return f"[{head}] {anchor.message or '用户反馈'}"


def _severity(strong: list[dict], counts: dict) -> str:
    if strong and counts["related_feedback"] > 0:
        return "high"
    if strong:
        return "medium"
    return "low"


def _confidence(strong: list[dict]) -> str:
    if len(strong) >= 2:
        return "high"
    if strong:
        return "medium"
    return "low"


def render_markdown(result: dict, include_scenario: bool = True) -> str:
    """把诊断结果渲染成可直接下载的 Markdown 报告。"""

    short_ids = {
        ref["id"]: ref.get("short") or ref["id"][:8]
        for ref in result.get("evidence_refs") or []
    }

    severity_label = {
        "high": "高",
        "medium": "中",
        "low": "低",
    }.get(result.get("severity"), result.get("severity"))

    lines = [
        f"# {result['title']}",
        "",
        f"- 严重程度：{severity_label}",
        f"- 证据充分度：{CONFIDENCE_LABELS.get(result.get('confidence'), result.get('confidence'))}",
        f"- 生成方式：{'规则诊断' if result.get('generated_by') == 'rule' else '规则 + LLM'}",
    ]

    if result.get("llm_error"):
        lines.append(f"- 提示：{result['llm_error']}")

    lines.extend(["", "## 结论"])

    for item in result["conclusions"]:
        tag = "已验证事实" if item["kind"] == "fact" else "推断"
        refs = "、".join(
            short_ids.get(evidence_id, evidence_id)
            for evidence_id in item["evidence_ids"]
        ) or "无证据引用"
        confidence = CONFIDENCE_LABELS.get(item["confidence"], item["confidence"])
        lines.append(f"- （{tag}，置信度 {confidence}）{item['statement']}")
        lines.append(f"  - 依据：{refs}")

    lines.extend(["", "## 下一步建议"])

    for step in result["next_steps"]:
        refs = "、".join(
            short_ids.get(evidence_id, evidence_id)
            for evidence_id in step["evidence_ids"]
        ) or "无证据引用"
        lines.append(f"- **{step['action']}**：{step['rationale']}")
        lines.append(f"  - 依据：{refs}")

    if include_scenario:
        lines.extend(["", "## 故障场景"])

        scenario = result.get("scenario")
        if scenario:
            status_labels = {
                "verified": "已验证",
                "partial": "部分验证",
                "blocked": "关键数据缺失",
            }
            lines.append(
                f"- 命中场景：{scenario.get('name')}（{scenario.get('scenario_id')} · "
                f"{scenario.get('version')}）"
            )
            lines.append(
                f"- 场景状态：{status_labels.get(scenario.get('status'), scenario.get('status'))}"
                f"｜结论置信度：{CONFIDENCE_LABELS.get(scenario.get('confidence'), scenario.get('confidence'))}"
            )

            if scenario.get("match", {}).get("reasons"):
                lines.append(
                    "- 命中理由："
                    + "；".join(scenario["match"]["reasons"])
                )

            lines.append("")
            lines.append("| 排查步骤 | 状态 | 观测值 | 说明 |")
            lines.append("|---|---|---|---|")

            step_labels = {
                "pass": "通过",
                "fail": "未通过",
                "unknown": "未验证",
                "skipped": "跳过",
            }

            for step in scenario.get("steps") or []:
                lines.append(
                    f"| {step['title']} | {step_labels.get(step['status'], step['status'])} "
                    f"| {step.get('observation') or '—'} | {step.get('reason') or '—'} |"
                )

            if scenario.get("hypotheses"):
                lines.append("")
                lines.append("### 场景假设")
                for item in scenario["hypotheses"]:
                    confidence = CONFIDENCE_LABELS.get(item.get("confidence"), item.get("confidence"))
                    refs = "、".join(item.get("evidence_refs") or []) or "无信号引用"
                    lines.append(f"- （假设，置信度 {confidence}）{item['statement']}")
                    lines.append(f"  - 依据：{refs}")

            if scenario.get("missing_sources"):
                lines.append("")
                lines.append(
                    "- 缺失数据源："
                    + "、".join(item["id"] for item in scenario["missing_sources"])
                )

            if scenario.get("limits"):
                lines.append("")
                lines.append("### 结论边界")
                for limit in scenario["limits"]:
                    lines.append(f"- {limit}")
        else:
            lines.append("- 未命中已配置的故障场景，按通用规则诊断处理。")

    lines.extend(["", "## 证据清单"])

    for ref in result["evidence_refs"]:
        lines.append(
            f"- `{ref.get('short') or ref['id'][:8]}`"
            f"（{ref['type']}｜{ref['relation']}｜{ref['score']} 分）"
            f"{ref['label']}　证据 ID：`{ref['id']}`"
        )

    return "\n".join(lines)
