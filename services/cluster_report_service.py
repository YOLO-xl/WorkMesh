"""问题簇报告：把一整个簇汇总成一份可归档、可下载的分析报告。

报告的原则：
- 只陈述簇本身能证明的事实（成员数、用户数、时间分布、现象/版本分布）。
- 根因方向一律标为「推断」，不把聚类结果说成故障结论。
- 抽样若干成员做单条诊断，用真实证据引用支撑；抽样不等于全量诊断。

簇报告目前走规则汇总（generated_by=rule），不调用 LLM：簇级结论没有单一
锚点证据，强行让模型输出会制造无引用结论。后续可在单簇内接入 LLM 做措辞润色。
"""

from __future__ import annotations

from typing import Optional

from services.cluster_service import severity_label
from services.diagnosis_service import DiagnosisService


CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}
DEFAULT_SAMPLE_SIZE = 3
DEFAULT_WINDOW_MINUTES = 60


def _clean(text: object, limit: int = 160) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit]


def _percent(count: int, total: int) -> str:
    if not total:
        return "0%"
    return f"{round(count / total * 100)}%"


def _distribution(items: list[dict], total: int, limit: int = 3) -> str:
    if not items:
        return "—"

    return "、".join(
        f"{_clean(item.get('name'))} {item.get('count', 0)} 条（{_percent(item.get('count', 0), total)}）"
        for item in items[:limit]
    )


def _latest_members(members: list[dict]) -> list[dict]:
    return sorted(
        members,
        key=lambda item: str(item.get("feedback_time") or ""),
        reverse=True,
    )


def _pick_samples(cluster: dict, sample_size: int) -> list[dict]:
    """代表反馈 + 最新 + 最早 + 描述最长的成员，去重后取前 N 条。"""

    members = cluster.get("members") or []
    if not members:
        return []

    representative = cluster.get("representative") or {}
    ordered = _latest_members(members)

    candidates = [
        representative,
        ordered[0] if ordered else {},
        ordered[-1] if ordered else {},
        max(members, key=lambda item: len(str(item.get("description") or ""))),
    ]
    # 抽样条数大于 3 时继续按时间倒序补齐，保证可选 5 / 8 条。
    candidates.extend(ordered)

    picked: list[dict] = []
    seen: set[str] = set()

    for candidate in candidates:
        member_id = str(candidate.get("id") or "")
        if not member_id or member_id in seen:
            continue
        seen.add(member_id)
        picked.append(candidate)
        if len(picked) >= sample_size:
            break

    return picked


def _aggregate_evidence(diagnoses: list[dict]) -> list[dict]:
    refs: dict[str, dict] = {}

    for diagnosis in diagnoses:
        for ref in diagnosis["result"].get("evidence_refs") or []:
            ref_id = str(ref.get("id") or "")
            if not ref_id:
                continue
            current = refs.get(ref_id)
            if current is None or float(ref.get("score") or 0) > float(
                current.get("score") or 0
            ):
                refs[ref_id] = ref

    return sorted(
        refs.values(),
        key=lambda item: float(item.get("score") or 0),
        reverse=True,
    )


def _confidence(evidence_refs: list[dict], cluster: dict) -> str:
    if len(evidence_refs) >= 2:
        return "high"
    if evidence_refs or (cluster.get("evidence") or {}).get("total", 0) > 0:
        return "medium"
    return "low"


class ClusterReportService:
    """汇总问题簇并抽样诊断，生成簇级报告。"""

    def __init__(
        self,
        db_path=None,
        diagnosis_service: Optional[DiagnosisService] = None,
    ):
        self.db_path = db_path
        self.diagnosis_service = diagnosis_service or DiagnosisService(
            db_path=db_path
        )

    def build(
        self,
        workspace_id: str,
        cluster: dict,
        sample_size: int = DEFAULT_SAMPLE_SIZE,
        window_minutes: int = DEFAULT_WINDOW_MINUTES,
    ) -> dict:
        members = cluster.get("members") or []
        sample_members = _pick_samples(cluster, max(1, int(sample_size)))
        diagnoses: list[dict] = []

        for member in sample_members:
            try:
                result = self.diagnosis_service.build(
                    workspace_id=workspace_id,
                    feedback_id=member["id"],
                    window_minutes=window_minutes,
                    use_llm=False,
                )
            except (LookupError, ValueError):
                continue

            diagnoses.append({"member": member, "result": result})

        evidence_refs = _aggregate_evidence(diagnoses)
        confidence = _confidence(evidence_refs, cluster)

        facts, inferences, next_steps = _analysis(cluster, diagnoses, evidence_refs)
        markdown = _markdown(
            cluster,
            diagnoses,
            evidence_refs,
            facts,
            inferences,
            next_steps,
            confidence,
        )

        return {
            "title": f"[簇报告] {cluster.get('title') or cluster.get('cluster_id')}",
            "severity": cluster.get("severity"),
            "confidence": confidence,
            "generated_by": "rule",
            "markdown": markdown,
            "context_counts": {
                "matched": int((cluster.get("evidence") or {}).get("total", 0)),
                "context": len(diagnoses),
            },
            "cluster": {
                key: cluster.get(key)
                for key in (
                    "cluster_id", "title", "product", "error_code", "size",
                    "unique_users", "first_seen", "last_seen", "concentration",
                    "severity", "score", "evidence", "keywords", "phenomena",
                    "versions", "platforms",
                )
            },
            "sample_diagnoses": [
                {
                    "feedback_id": item["member"].get("id"),
                    "external_id": item["member"].get("external_id"),
                    "uid": item["member"].get("uid"),
                    "severity": item["result"].get("severity"),
                    "confidence": item["result"].get("confidence"),
                    "evidence_count": len(item["result"].get("evidence_refs") or []),
                    "conclusions": [
                        {
                            "statement": conclusion.get("statement"),
                            "evidence_ids": conclusion.get("evidence_ids") or [],
                        }
                        for conclusion in (item["result"].get("conclusions") or [])[:3]
                    ],
                }
                for item in diagnoses
            ],
            "evidence_refs": evidence_refs,
            "facts": facts,
            "inferences": inferences,
            "next_steps": next_steps,
            "member_count": len(members),
            "sampled_count": len(diagnoses),
        }


def _analysis(
    cluster: dict,
    diagnoses: list[dict],
    evidence_refs: list[dict],
) -> tuple[list[str], list[str], list[str]]:
    size = int(cluster.get("size") or 0)
    users = int(cluster.get("unique_users") or 0)
    product = _clean(cluster.get("product") or "未分类产品", 40)
    error_code = _clean(cluster.get("error_code") or "", 40) or "无错误码"
    first_seen = _clean(cluster.get("first_seen") or "—", 20)
    last_seen = _clean(cluster.get("last_seen") or "—", 20)
    concentration = float(cluster.get("concentration") or 0)
    evidence = cluster.get("evidence") or {}
    versions = cluster.get("versions") or []
    platforms = cluster.get("platforms") or []
    phenomena = cluster.get("phenomena") or []

    facts = [
        f"同一产品「{product}」下，{size} 条反馈被归为同一问题簇（{error_code}），涉及 {users} 位用户。",
        f"时间范围 {first_seen} → {last_seen}；最大 1 小时箱占 {int(concentration * 100)}%。",
    ]

    if versions:
        facts.append(f"版本分布：{_distribution(versions, size)}。")
    if platforms:
        facts.append(f"平台分布：{_distribution(platforms, size)}。")
    if phenomena:
        facts.append(
            "主要表现："
            + "、".join(
                f"{_clean(item.get('label'))} {item.get('count', 0)} 条"
                for item in phenomena[:3]
            )
            + "。"
        )

    facts.append(
        f"簇范围关联证据：日志 {evidence.get('log', 0)} 条、"
        f"指标 {evidence.get('metric', 0)} 条、其他 {evidence.get('other', 0)} 条。"
    )

    representative = cluster.get("representative") or {}
    if representative:
        facts.append(
            f"代表反馈 {representative.get('external_id') or '—'}"
            f"（UID {representative.get('uid') or '—'}）："
            f"「{_clean(representative.get('description'), 80)}」。"
        )

    if diagnoses:
        facts.append(
            f"从簇内抽样 {len(diagnoses)} 条反馈做单条诊断，"
            f"合计引用 {len(evidence_refs)} 条证据。"
        )

    inferences = []

    if concentration >= 0.6 and size >= 4:
        inferences.append(
            f"时间集中度 {int(concentration * 100)}%，更像集中爆发："
            "优先对齐发布、配置变更或活动时间点。"
        )
    elif size >= 4:
        inferences.append(
            f"时间集中度 {int(concentration * 100)}%，分布较分散："
            "更像长期存在问题或环境差异，需要按版本 / 平台分层看。"
        )

    if versions and size:
        top_version = versions[0]
        share = float(top_version.get("count") or 0) / size
        if share >= 0.5:
            inferences.append(
                f"版本「{_clean(top_version.get('name'))}」占 {_percent(top_version.get('count', 0), size)}，"
                "优先怀疑该版本回归；仍需日志 / 指标确认。"
            )

    if phenomena and size:
        top = phenomena[0]
        share = float(top.get("count") or 0) / size
        if share >= 0.5:
            inferences.append(
                f"主要表现集中在「{_clean(top.get('label'))}」，"
                "可作为日志检索与回访的关键词。"
            )

    if not evidence_refs:
        inferences.append(
            "抽样诊断没有引用到日志或指标证据，"
            "当前只能停在用户反馈层面，不能据此确认根因。"
        )

    next_steps = [
        f"按产品「{product}」+ {error_code}（或现象关键词）检索服务端日志，"
        f"时间范围 {first_seen} → {last_seen}，重点找 Trace ID / Request ID。",
        "核对同期发布、配置开关与灰度记录，确认是否与版本分布一致。",
    ]

    if platforms:
        next_steps.append(
            f"按平台分层对比：{_distribution(platforms, size)}，确认是否集中在单一端。"
        )

    if diagnoses:
        next_steps.append(
            "回访抽样反馈的用户，补齐发生时间、操作路径与网络环境；"
            "用代表反馈进入单条诊断查看完整证据引用。"
        )
    else:
        next_steps.append(
            "先补齐反馈描述或接入日志，再对代表反馈做单条诊断。"
        )

    return facts, inferences, next_steps


def _markdown(
    cluster: dict,
    diagnoses: list[dict],
    evidence_refs: list[dict],
    facts: list[str],
    inferences: list[str],
    next_steps: list[str],
    confidence: str,
) -> str:
    size = int(cluster.get("size") or 0)
    users = int(cluster.get("unique_users") or 0)
    first_seen = _clean(cluster.get("first_seen") or "—", 20)
    last_seen = _clean(cluster.get("last_seen") or "—", 20)
    concentration = float(cluster.get("concentration") or 0)
    evidence = cluster.get("evidence") or {}

    lines = [
        f"# {cluster.get('title') or '问题簇报告'}",
        "",
        "> 簇表示「看起来是同一批问题」，不是根因结论；以下推断都需要日志或指标验证。",
        "",
        f"- 影响面：{size} 条反馈 / {users} 位用户",
        f"- 时间范围：{first_seen} → {last_seen}",
        f"- 时间集中度：{int(concentration * 100)}%",
        f"- 严重程度：{severity_label(cluster.get('severity'))}"
        f"｜证据充分度：{CONFIDENCE_LABELS.get(confidence, confidence)}",
        f"- 关联证据：日志 {evidence.get('log', 0)} 条 / "
        f"指标 {evidence.get('metric', 0)} 条 / 其他 {evidence.get('other', 0)} 条",
        "",
        "## 关键事实",
    ]

    lines.extend(f"- {item}" for item in facts)
    lines.append("")

    lines.append("## 推断与可能方向")
    if inferences:
        lines.extend(f"- {item}" for item in inferences)
    else:
        lines.append("- 当前没有足够的分布差异，暂不给出方向性推断。")
    lines.append("")

    phenomena = cluster.get("phenomena") or []
    if phenomena:
        lines.append("## 现象分布")
        for item in phenomena[:6]:
            samples = "；".join(
                _clean(sample, 60) for sample in item.get("samples") or []
            )
            lines.append(
                f"- {_clean(item.get('label'))}：{item.get('count', 0)} 条"
                + (f"（例：{samples}）" if samples else "")
            )
        lines.append("")

    keywords = cluster.get("keywords") or []
    if keywords:
        lines.append("## 簇关键词")
        lines.append(
            "、".join(
                f"{_clean(item.get('name'))}（{item.get('count', 0)}）"
                for item in keywords[:8]
            )
        )
        lines.append("")

    lines.append("## 抽样诊断")
    if not diagnoses:
        lines.append("- 没有可用的抽样反馈。")
    else:
        for item in diagnoses:
            member = item["member"]
            result = item["result"]
            lines.append(
                f"### {member.get('external_id') or '—'}"
                f" · UID {member.get('uid') or '—'}"
                f" · {_clean(member.get('feedback_time') or '—', 20)}"
            )
            lines.append(
                f"- 严重程度：{severity_label(result.get('severity'))}"
                f"｜证据充分度：{CONFIDENCE_LABELS.get(result.get('confidence'), result.get('confidence'))}"
                f"｜引用 {len(result.get('evidence_refs') or [])} 条证据"
            )
            for conclusion in (result.get("conclusions") or [])[:3]:
                statement = _clean(conclusion.get("statement"), 220)
                ids = conclusion.get("evidence_ids") or []
                lines.append(f"- {statement}")
                if ids:
                    lines.append(f"  - 依据：{'、'.join(str(value) for value in ids)}")
            lines.append("")

    lines.append("## 共性证据")
    if evidence_refs:
        for ref in evidence_refs[:12]:
            lines.append(
                f"- [{ref.get('id')}] {ref.get('type') or '—'} / "
                f"{ref.get('relation') or '—'} / 分数 {ref.get('score') or '—'} · "
                f"{_clean(ref.get('label'), 120)}"
            )
        if len(evidence_refs) > 12:
            lines.append(f"- 其余 {len(evidence_refs) - 12} 条见抽样诊断明细。")
    else:
        lines.append(
            "- 抽样诊断没有引用到证据。先在「证据关联」接入该产品与时间范围的日志 / 指标。"
        )
    lines.append("")

    lines.append("## 下一步建议")
    for index, step in enumerate(next_steps, start=1):
        lines.append(f"{index}. {step}")
    lines.append("")

    lines.append("## 结论边界")
    lines.append(
        f"- 本报告由规则汇总生成，抽样 {len(diagnoses)} 条反馈，不代表全部 {size} 条都做过诊断。"
    )
    lines.append("- 簇成员仍可能混入描述相似但根因不同的反馈，需人工复核。")
    lines.append("- 涉及用户与日志字段均来自本地数据，未做脱敏导出。")

    return "\n".join(lines).strip() + "\n"
