"""问题聚类服务。

目标：在没有 LLM、没有向量库的前提下，把散落的用户反馈自动归成
「同一个问题」的簇，给出影响面、时间分布、主要现象和关联证据量。

聚类口径（刻意保持可解释）：
1. 产品 + 错误码是强分组键；同一产品的同一错误码视为同一类问题。
2. 错误码缺失时（普通用户反馈的常态），不再只看文本相似度，而是把描述归约成
   「现象标签 + 功能实体」，再叠加文本、版本、平台做加权打分，增量并簇。
   完全没有现象也没有实体的反馈不强行归类，单独进「未归类池」。
3. 簇内部再按现象分组，用来回答「同一问题表现成几种样子」，
   只用于展示，不改变簇的边界。

不做事：不做语义向量聚类、不猜测根因、不把簇当成故障结论。
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Optional

from core.evidence import format_timestamp, parse_timestamp
from database.evidence_repository import EvidenceRepository
from database.feedback_repository import FeedbackRepository
from services.symptom_lexicon import (
    compact_text,
    extract_features,
    extract_symptoms,
    is_suggestion,
    keywords,
)


DEFAULT_MIN_SIZE = 2
PHENOMENON_THRESHOLD = 0.34

# 无错误码反馈的并簇阈值：现象、实体、文本、版本/平台四项加权后达到该分值才合并。
MERGE_THRESHOLD = 0.45
# 单簇允许的最大时间跨度，防止「同一个现象、隔了两个月」被链成一簇。
MAX_CLUSTER_SPAN_DAYS = 21

# 现象标签词表：用来回答「同一错误码，用户感知到的现象有几种」。
# 这是展示层的粗分类，不参与簇边界判定。
PHENOMENON_KEYWORDS = (
    "黑屏", "花屏", "白屏", "卡顿", "卡死", "闪退", "崩溃", "加载", "转圈",
    "超时", "登录", "退出", "初始化", "回调", "播放失败",
    "无法播放", "无声", "延迟", "报错", "上传失败", "下载失败", "数据",
)

_SEVERITY_LABELS = {"high": "高", "medium": "中", "low": "低"}


def severity_label(severity: str) -> str:
    return _SEVERITY_LABELS.get(severity, severity)


def _clean_text(text: Optional[str]) -> str:
    """去掉数字、标点与空白，只留可用于比较的文字。"""

    if not text:
        return ""

    text = str(text).lower()
    text = re.sub(r"[0-9]+", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text


def _bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[index:index + 2] for index in range(len(text) - 1)}


def text_similarity(left: Optional[str], right: Optional[str]) -> float:
    """字符二元组 Jaccard 相似度，用来比较两句中文描述是否在讲同一件事。"""

    a = _bigrams(_clean_text(left))
    b = _bigrams(_clean_text(right))

    if not a or not b:
        return 0.0

    return len(a & b) / len(a | b)


def _ngrams(text: str, size: int = 3) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[index:index + size] for index in range(len(text) - size + 1)}


def _jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _coverage(needle: set, haystack: set) -> float:
    """needle 被 haystack 覆盖的比例，用于「单条 vs 簇质心」的比较。"""

    if not needle:
        return 0.0
    return len(needle & haystack) / len(needle)


def text_similarity_v2(left: Optional[str], right: Optional[str]) -> float:
    """字符三元组 Jaccard，比二元组更能区分「转圈打不开」和「打不开设置页」。"""

    value = _jaccard(
        _ngrams(compact_text(left)),
        _ngrams(compact_text(right)),
    )
    return value


def record_signals(record: dict) -> dict:
    text = record.get("description")
    return {
        "symptoms": set(extract_symptoms(text)),
        "features": set(extract_features(text)),
        "text": compact_text(text),
        "version": str(record.get("version") or "").strip().lower(),
        "platform": str(record.get("platform") or "").strip().lower(),
    }


def weighted_similarity(left: dict, right: dict) -> float:
    """现象 0.45 + 实体 0.25 + 文本 0.20 + 版本/平台 0.10。"""

    score = 0.0
    score += 0.45 * _jaccard(left["symptoms"], right["symptoms"])
    score += 0.25 * _jaccard(left["features"], right["features"])
    score += 0.20 * _jaccard(
        _ngrams(left["text"]),
        _ngrams(right["text"]),
    )

    if left["version"] and left["version"] == right["version"]:
        score += 0.06
    if left["platform"] and left["platform"] == right["platform"]:
        score += 0.04

    return score


def _has_signals(signals: dict) -> bool:
    return bool(signals["symptoms"] or signals["features"])


def _top_values(members: list[dict], field: str, limit: int = 3) -> list[dict]:
    counter = Counter(
        str(member.get(field)).strip()
        for member in members
        if member.get(field)
    )

    return [
        {"name": name, "count": count}
        for name, count in counter.most_common(limit)
    ]


def _time_bounds(members: list[dict]) -> tuple[Optional[object], Optional[object]]:
    stamps = [
        parse_timestamp(member.get("feedback_time"))
        for member in members
        if member.get("feedback_time")
    ]
    stamps = [stamp for stamp in stamps if stamp is not None]

    if not stamps:
        return None, None

    return min(stamps), max(stamps)


def _display_time(stamp) -> Optional[str]:
    """界面用可读时间，避免直接暴露 ISO 字符串。"""

    if stamp is None:
        return None

    return stamp.strftime("%Y-%m-%d %H:%M")


def _cluster_id(member_ids: list[str]) -> str:
    digest = hashlib.sha1(
        "|".join(sorted(member_ids)).encode("utf-8")
    ).hexdigest()[:8]
    return f"C-{digest}"


def _phenomenon_tag(description: Optional[str]) -> str:
    """展示层的粗现象标签，保持旧的短词口径，避免把「加载」拆成「加载失败」。"""

    text = str(description or "")

    for keyword in PHENOMENON_KEYWORDS:
        if keyword in text:
            return keyword

    return "其他"


def _group_by_phenomenon(members: list[dict]) -> list[dict]:
    """按现象关键词分桶，输出每种表现的条数与样例。"""

    buckets: dict[str, list[dict]] = {}

    for member in members:
        tag = _phenomenon_tag(member.get("description"))
        buckets.setdefault(tag, []).append(member)

    phenomena = [
        {
            "label": tag,
            "count": len(group),
            "samples": [
                (member.get("description") or "")[:32] for member in group[:3]
            ],
            "member_ids": [member["id"] for member in group],
        }
        for tag, group in buckets.items()
    ]

    phenomena.sort(key=lambda item: (-item["count"], item["label"]))
    return phenomena


def _cluster_keywords(member_views: list[dict], limit: int = 6) -> list[dict]:
    """簇内高频关键词，现象词权重加倍，用于卡片 chips 与词云大小。"""

    counter: Counter[str] = Counter()

    for member in member_views:
        for word in keywords(member.get("description"), limit=6):
            counter[word] += 1
        for symptom in extract_symptoms(member.get("description")):
            counter[symptom] += 1

    return [
        {"name": word, "count": count}
        for word, count in counter.most_common(limit)
    ]


def _severity(size: int, unique_users: int) -> str:
    if size >= 8 or unique_users >= 6:
        return "high"
    if size >= 4:
        return "medium"
    return "low"


def _score(size: int, unique_users: int, evidence_total: int, concentration: float) -> int:
    """影响面优先的粗排序分：先看多少人在踩，再看有没有证据支撑。"""

    value = min(size * 6, 42)
    value += min(unique_users * 4, 20)
    value += min(evidence_total * 3, 18)
    value += 12 if concentration >= 0.6 else 0
    value += 8 if size >= 8 else 0
    return min(int(value), 100)


def _concentration(members: list[dict]) -> float:
    """最大时间箱里的反馈占比，用来看是集中爆发还是长期零星。"""

    stamps = [
        parse_timestamp(member.get("feedback_time"))
        for member in members
        if member.get("feedback_time")
    ]
    stamps = [stamp for stamp in stamps if stamp is not None]

    if len(stamps) < 2:
        return 1.0 if stamps else 0.0

    buckets = Counter(stamp.strftime("%Y-%m-%d %H") for stamp in stamps)
    return max(buckets.values()) / len(stamps)


def _member_view(record: dict) -> dict:
    raw_time = record.get("feedback_time")
    display_time = str(raw_time).replace("T", " ").replace("Z", "")[:16] if raw_time else None

    return {
        "id": record.get("id"),
        "external_id": record.get("external_id"),
        "uid": record.get("uid"),
        "feedback_time": display_time,
        "product": record.get("product"),
        "version": record.get("version"),
        "platform": record.get("platform"),
        "device": record.get("device"),
        "error_code": record.get("error_code"),
        "description": record.get("description"),
        "status": record.get("status"),
    }


class ClusterService:
    """把反馈集合聚成问题簇。"""

    def __init__(
        self,
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

    def build(
        self,
        workspace_id: str,
        min_size: int = DEFAULT_MIN_SIZE,
        limit: int = 500,
        with_evidence: bool = True,
    ) -> dict:
        """聚合并返回簇列表。

        返回：
        - clusters：按影响面排序的问题簇
        - singletons：不足以成簇的孤立反馈
        - stats：总量、覆盖情况
        """

        records = self.feedback_repository.list_feedback(
            workspace_id, page=1, page_size=min(max(int(limit), 1), 500)
        )["records"]

        if not records:
            return {
                "clusters": [],
                "singletons": [],
                "unclassified": [],
                "stats": {
                    "total": 0,
                    "clustered": 0,
                    "cluster_count": 0,
                    "singleton_count": 0,
                    "unclassified_count": 0,
                    "coverage": 0.0,
                },
            }

        buckets: dict[tuple[str, str], list[dict]] = {}

        for record in records:
            product = str(record.get("product") or "未分类产品").strip()
            error_code = str(record.get("error_code") or "").strip()

            # 没有错误码时先按产品归桶，桶内再按描述相似度切分，避免一锅粥。
            buckets.setdefault((product, error_code), []).append(record)

        clusters = []
        singletons = []
        unclassified = []

        for (product, error_code), members in buckets.items():
            if error_code:
                groups, uncovered = [members], []
            else:
                groups, uncovered = self._group_without_error_code(members)
                unclassified.extend(_member_view(item) for item in uncovered)

            for group in groups:
                if len(group) < min_size:
                    singletons.extend(_member_view(item) for item in group)
                    continue

                clusters.append(
                    self._build_cluster(
                        workspace_id, product, error_code, group, with_evidence
                    )
                )

        clusters.sort(key=lambda item: (-item["score"], -item["size"]))
        singletons.sort(
            key=lambda item: item.get("feedback_time") or "", reverse=True
        )

        clustered = sum(item["size"] for item in clusters)
        unclassified.sort(
            key=lambda item: item.get("feedback_time") or "", reverse=True
        )

        return {
            "clusters": clusters,
            "singletons": singletons,
            "unclassified": unclassified,
            "stats": {
                "total": len(records),
                "clustered": clustered,
                "cluster_count": len(clusters),
                "singleton_count": len(singletons),
                "unclassified_count": len(unclassified),
                "coverage": round(clustered / len(records), 3) if records else 0.0,
            },
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _group_without_error_code(
        records: list[dict],
        threshold: float = MERGE_THRESHOLD,
    ) -> tuple[list[list[dict]], list[dict]]:
        """无错误码反馈的增量聚类。

        每条反馈与已有簇的「现象/实体集合 + 代表描述」比较，达到阈值且不超出
        时间跨度就并入；否则自成一簇。返回 (分组, 未归类反馈)。
        """

        groups: list[dict] = []
        unclassified: list[dict] = []

        for record in records:
            signals = record_signals(record)
            stamp = parse_timestamp(record.get("feedback_time"))

            if not _has_signals(signals) or is_suggestion(record.get("description")):
                # 既没现象也没实体，或是产品建议，都不强行归入故障簇
                unclassified.append(record)
                continue

            best_group = None
            best_score = 0.0

            for group in groups:
                score = 0.0
                score += 0.45 * _coverage(signals["symptoms"], group["symptoms"])
                score += 0.25 * _coverage(signals["features"], group["features"])
                score += 0.20 * max(
                    text_similarity_v2(record.get("description"), sample)
                    for sample in group["samples"]
                )
                if signals["version"] and signals["version"] == group["version"]:
                    score += 0.06
                if signals["platform"] and signals["platform"] == group["platform"]:
                    score += 0.04
                # 两边都识别出了功能、且完全不同时，多半不是同一问题，压低分数
                if (
                    signals["features"]
                    and group["features"]
                    and not signals["features"] & group["features"]
                ):
                    score -= 0.12

                if score <= best_score:
                    continue
                if (
                    stamp is not None
                    and group["first"] is not None
                    and group["last"] is not None
                    and (max(stamp, group["last"]) - min(stamp, group["first"])).days
                    > MAX_CLUSTER_SPAN_DAYS
                ):
                    continue

                best_score = score
                best_group = group

            if best_group is not None and best_score >= threshold:
                best_group["members"].append(record)
                best_group["symptoms"] |= signals["symptoms"]
                best_group["features"] |= signals["features"]
                best_group["samples"] = (best_group["samples"] + [record.get("description")])[-3:]
                if stamp is not None:
                    best_group["first"] = stamp if best_group["first"] is None else min(best_group["first"], stamp)
                    best_group["last"] = stamp if best_group["last"] is None else max(best_group["last"], stamp)
            else:
                groups.append(
                    {
                        "members": [record],
                        "symptoms": set(signals["symptoms"]),
                        "features": set(signals["features"]),
                        "samples": [record.get("description")],
                        "version": signals["version"],
                        "platform": signals["platform"],
                        "first": stamp,
                        "last": stamp,
                    }
                )

        return [group["members"] for group in groups], unclassified

    def _build_cluster(
        self,
        workspace_id: str,
        product: str,
        error_code: str,
        members: list[dict],
        with_evidence: bool,
    ) -> dict:
        member_views = [_member_view(item) for item in members]
        first_seen, last_seen = _time_bounds(member_views)

        unique_users = len(
            {item.get("uid") for item in member_views if item.get("uid")}
        )
        phenomena = _group_by_phenomenon(member_views)
        concentration = _concentration(member_views)
        severity = _severity(len(member_views), unique_users)
        evidence = self._evidence_stats(
            workspace_id, product, first_seen, last_seen, with_evidence
        )
        score = _score(len(member_views), unique_users, evidence["total"], concentration)

        dominant = phenomena[0]["label"] if phenomena else "—"
        versions = _top_values(member_views, "version")
        platforms = _top_values(member_views, "platform")

        if error_code:
            title_code = error_code
        elif dominant and dominant != "其他":
            title_code = dominant
        else:
            title_code = "疑似同类"

        return {
            "cluster_id": _cluster_id([item["id"] for item in member_views]),
            "title": f"{product} · {title_code}",
            "product": product,
            "error_code": error_code,
            "size": len(member_views),
            "unique_users": unique_users,
            "first_seen": _display_time(first_seen),
            "last_seen": _display_time(last_seen),
            "concentration": round(concentration, 3),
            "versions": versions,
            "platforms": platforms,
            "phenomena": phenomena,
            "keywords": _cluster_keywords(member_views),
            "severity": severity,
            "score": score,
            "evidence": evidence,
            "summary": (
                f"{len(member_views)} 条反馈 / {unique_users} 位用户，"
                f"主要现象「{dominant}」，版本 {versions[0]['name'] if versions else '未知'}"
            ),
            "representative": max(
                member_views,
                key=lambda item: len(item.get("description") or ""),
            ),
            "members": member_views,
        }

    def _evidence_stats(
        self,
        workspace_id: str,
        product: str,
        first_seen,
        last_seen,
        with_evidence: bool,
    ) -> dict:
        empty = {"log": 0, "metric": 0, "other": 0, "total": 0}

        if not with_evidence or first_seen is None or last_seen is None:
            return empty

        # 簇的时间跨度可能很大，直接查全量候选再按类型计数。
        anchor_filter = {
            "service": product,
            "start": format_timestamp(first_seen),
            "end": format_timestamp(last_seen),
        }

        try:
            candidates = self.evidence_repository.find_candidates(
                workspace_id=workspace_id,
                anchor_filter=anchor_filter,
                limit=500,
            )
        except Exception:
            return empty

        counters = Counter(item.evidence_type for item in candidates)

        return {
            "log": counters.get("log", 0),
            "metric": counters.get("metric", 0),
            "other": sum(
                value
                for key, value in counters.items()
                if key not in {"log", "metric"}
            ),
            "total": len(candidates),
        }
