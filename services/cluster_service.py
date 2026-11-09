"""问题聚类服务。

目标：在没有 LLM、没有向量库的前提下，把散落的用户反馈自动归成
「同一个问题」的簇，给出影响面、时间分布、主要现象和关联证据量。

聚类口径（刻意保持可解释）：
1. 产品 + 错误码是强分组键；同一产品的同一错误码视为同一类问题。
2. 错误码缺失时，退化为按描述文本的字符二元组相似度做单链聚合。
3. 簇内部再按文本相似度切「现象分组」，用来回答「同一问题表现成几种样子」，
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


DEFAULT_MIN_SIZE = 2
PHENOMENON_THRESHOLD = 0.34

# 现象标签词表：用来回答「同一错误码，用户感知到的现象有几种」。
# 这是展示层的粗分类，不参与簇边界判定。
PHENOMENON_KEYWORDS = (
    "黑屏", "花屏", "卡顿", "闪退", "崩溃", "加载", "转圈",
    "超时", "登录", "退出", "初始化", "回调", "播放失败",
    "无法播放", "无声", "延迟",
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
                "stats": {
                    "total": 0,
                    "clustered": 0,
                    "cluster_count": 0,
                    "singleton_count": 0,
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

        for (product, error_code), members in buckets.items():
            groups = self._split_by_text(members) if not error_code else [members]

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

        return {
            "clusters": clusters,
            "singletons": singletons,
            "stats": {
                "total": len(records),
                "clustered": clustered,
                "cluster_count": len(clusters),
                "singleton_count": len(singletons),
                "coverage": round(clustered / len(records), 3) if records else 0.0,
            },
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _split_by_text(records: list[dict], threshold: float = PHENOMENON_THRESHOLD) -> list[list[dict]]:
        groups: list[list[dict]] = []

        for record in records:
            for group in groups:
                if text_similarity(
                    record.get("description"), group[0].get("description")
                ) >= threshold:
                    group.append(record)
                    break
            else:
                groups.append([record])

        return groups

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

        title_code = error_code or "无错误码"

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
