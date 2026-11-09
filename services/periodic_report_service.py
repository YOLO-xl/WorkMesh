"""周期性分析报告：按日期区间汇总反馈、问题与报告处理情况。"""

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Optional

from database.connection import get_connection
from database.report_tracking_repository import (
    ReportTrackingRepository,
    TRACKING_STATUSES,
)
from services.symptom_lexicon import extract_symptoms


STATUS_LABELS = {
    "pending": "待处理",
    "handled": "已处理",
    "ignored": "已忽略",
}


def _parse_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _pct(part: int, total: int) -> str:
    if not total:
        return "—"
    return f"{part / total * 100:.0f}%"


def _delta(current: int, previous: int) -> str:
    diff = current - previous
    if previous == 0 and current == 0:
        return "0"
    if previous == 0:
        return f"+{current}（上期无数据）"
    return f"{diff:+d}（{_pct(diff, previous)}）"


class PeriodicReportService:
    """生成可归档的周期报告，不调用 LLM。"""

    def __init__(self, db_path=None):
        self.db_path = db_path
        self.tracking_repository = ReportTrackingRepository(db_path)

    def _connect(self):
        return get_connection(self.db_path)

    def available_products(self, workspace_id: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT product
                FROM feedback_records
                WHERE workspace_id = ?
                  AND deleted_at IS NULL
                  AND product IS NOT NULL
                  AND TRIM(product) != ''
                ORDER BY product
                """,
                (workspace_id,),
            ).fetchall()
        return [row["product"] for row in rows]

    def _feedback_rows(
        self,
        workspace_id: str,
        start: date,
        end: date,
        product: Optional[str],
    ) -> list[dict]:
        conditions = [
            "workspace_id = ?",
            "deleted_at IS NULL",
            "date(feedback_time) >= date(?)",
            "date(feedback_time) <= date(?)",
        ]
        params: list[Any] = [
            workspace_id,
            start.isoformat(),
            end.isoformat(),
        ]

        if product:
            conditions.append("product = ?")
            params.append(product)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM feedback_records
                WHERE {" AND ".join(conditions)}
                ORDER BY feedback_time ASC
                """,
                params,
            ).fetchall()

        return [dict(row) for row in rows]

    def _report_rows(
        self,
        table: str,
        workspace_id: str,
        start: date,
        end: date,
        product: Optional[str],
    ) -> list[dict]:
        conditions = [
            "workspace_id = ?",
            "substr(created_at, 1, 10) >= ?",
            "substr(created_at, 1, 10) <= ?",
        ]
        params: list[Any] = [workspace_id, start.isoformat(), end.isoformat()]

        if product:
            conditions.append("product = ?")
            params.append(product)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM {table}
                WHERE {" AND ".join(conditions)}
                ORDER BY created_at DESC
                """,
                params,
            ).fetchall()

        return [dict(row) for row in rows]

    def build(
        self,
        workspace_id: str,
        period_start: date | str,
        period_end: date | str,
        product: Optional[str] = None,
    ) -> dict:
        start = _parse_date(period_start)
        end = _parse_date(period_end)

        if end < start:
            raise ValueError("结束日期不能早于开始日期")
        if (end - start).days > 366:
            raise ValueError("单次周期报告最长 366 天")

        span = (end - start).days + 1
        previous_end = start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=span - 1)

        current = self._feedback_rows(workspace_id, start, end, product)
        previous = self._feedback_rows(
            workspace_id, previous_start, previous_end, product
        )

        total = len(current)
        users = len({row.get("uid") for row in current if row.get("uid")})
        previous_total = len(previous)
        previous_users = len(
            {row.get("uid") for row in previous if row.get("uid")}
        )

        daily_counter = Counter(
            str(row.get("feedback_time") or "")[:10]
            for row in current
            if row.get("feedback_time")
        )

        issue_counter = Counter()
        issue_users: dict[str, set] = {}
        for row in current:
            if not row.get("error_code"):
                continue
            key = f"{row.get('product') or '未知产品'} · {row['error_code']}"
            issue_counter[key] += 1
            if row.get("uid"):
                issue_users.setdefault(key, set()).add(row["uid"])

        previous_issues = Counter()
        for row in previous:
            if row.get("error_code"):
                key = f"{row.get('product') or '未知产品'} · {row['error_code']}"
                previous_issues[key] += 1

        top_issues = []
        for key, count in issue_counter.most_common(8):
            top_issues.append({
                "key": key,
                "count": count,
                "users": len(issue_users.get(key, set())),
                "share": _pct(count, total),
                "delta": _delta(count, previous_issues.get(key, 0)),
            })

        product_counter = Counter(
            row.get("product") or "未知产品" for row in current
        )
        platform_counter = Counter(
            row.get("platform") or "未知平台" for row in current
        )

        no_code_symptoms = Counter()
        for row in current:
            if row.get("error_code"):
                continue
            for symptom in extract_symptoms(row.get("description") or ""):
                no_code_symptoms[symptom] += 1

        diagnosis_reports = self._report_rows(
            "diagnosis_reports", workspace_id, start, end, product
        )
        cluster_reports = self._report_rows(
            "cluster_reports", workspace_id, start, end, product
        )

        report_ids = [row["id"] for row in diagnosis_reports]
        cluster_ids = [row["id"] for row in cluster_reports]
        diagnosis_tracking = self.tracking_repository.get_many(
            workspace_id, "feedback", report_ids
        )
        cluster_tracking = self.tracking_repository.get_many(
            workspace_id, "cluster", cluster_ids
        )

        tracking_counts = {status: 0 for status in TRACKING_STATUSES}
        for entry in list(diagnosis_tracking.values()) + list(
            cluster_tracking.values()
        ):
            tracking_counts[entry["status"]] += 1

        report_total = len(report_ids) + len(cluster_ids)
        tracked_closed = (
            tracking_counts["handled"] + tracking_counts["ignored"]
        )
        pending_reports = max(0, report_total - tracked_closed)

        stats = {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "product": product,
            "feedback_total": total,
            "feedback_previous": previous_total,
            "unique_users": users,
            "unique_users_previous": previous_users,
            "daily": dict(sorted(daily_counter.items())),
            "top_issues": top_issues,
            "products": product_counter.most_common(8),
            "platforms": platform_counter.most_common(8),
            "no_code_symptoms": no_code_symptoms.most_common(8),
            "reports": {
                "feedback": len(report_ids),
                "cluster": len(cluster_ids),
                "pending": pending_reports,
                "handled": tracking_counts["handled"],
                "ignored": tracking_counts["ignored"],
            },
        }

        title = (
            f"[周期报告] {start.isoformat()} ~ {end.isoformat()}"
            f" · {product or '全产品'}"
        )
        markdown = self._markdown(title, stats, start, end, product, previous_start)

        return {
            "title": title,
            "markdown": markdown,
            "stats": stats,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
        }

    def _markdown(
        self,
        title: str,
        stats: dict,
        start: date,
        end: date,
        product: Optional[str],
        previous_start: date,
    ) -> str:
        total = stats["feedback_total"]
        previous_total = stats["feedback_previous"]
        users = stats["unique_users"]
        reports = stats["reports"]

        lines = [
            f"# {title}",
            "",
            "> 统计口径：按反馈时间落在区间内的未删除反馈；"
            "报告沉淀按报告创建时间统计。周期报告只做汇总，不输出根因结论。",
            "",
            "## 一、总览",
            f"- 区间：{start.isoformat()} ~ {end.isoformat()}"
            f"（对比上一周期 {previous_start.isoformat()} 起）",
            f"- 产品范围：{product or '全部产品'}",
            f"- 反馈：{total} 条，较上期 {_delta(total, previous_total)}",
            f"- 涉及用户：{users} 位，较上期 {_delta(users, stats['unique_users_previous'])}",
            f"- 报告沉淀：诊断 {reports['feedback']} 份 / 簇 {reports['cluster']} 份；"
            f"待处理 {reports['pending']} 份，已处理 {reports['handled']} 份，"
            f"已忽略 {reports['ignored']} 份。",
            "",
            "## 二、每日趋势",
        ]

        if stats["daily"]:
            lines.extend(["| 日期 | 反馈数 |", "| --- | --- |"])
            for day, count in stats["daily"].items():
                lines.append(f"| {day} | {count} |")
        else:
            lines.append("区间内没有反馈记录。")

        lines.extend(["", "## 三、主要问题（按产品 + 错误码）"])

        if stats["top_issues"]:
            lines.extend([
                "| 问题 | 反馈 | 用户 | 占比 | 较上期 |",
                "| --- | --- | --- | --- | --- |",
            ])
            for item in stats["top_issues"]:
                lines.append(
                    f"| {item['key']} | {item['count']} | {item['users']} | "
                    f"{item['share']} | {item['delta']} |"
                )
        else:
            lines.append("区间内没有带错误码的反馈；可参考下一节的无码现象。")

        lines.extend(["", "## 四、无错误码现象"])
        if stats["no_code_symptoms"]:
            lines.append(
                "| 现象 | 提及次数 |"
                "\n| --- | --- |"
            )
            for symptom, count in stats["no_code_symptoms"]:
                lines.append(f"| {symptom} | {count} |")
            lines.append("")
            lines.append(
                "> 现象词按规则词典抽取，只代表用户描述中的高频词，"
                "不能直接等同于根因。"
            )
        else:
            lines.append("区间内没有可识别的无码现象词。")

        lines.extend(["", "## 五、分布"])
        if stats["products"]:
            lines.append("**产品**：" + "、".join(
                f"{name} {count} 条" for name, count in stats["products"]
            ))
        if stats["platforms"]:
            lines.append("")
            lines.append("**平台**：" + "、".join(
                f"{name} {count} 条" for name, count in stats["platforms"]
            ))

        lines.extend([
            "",
            "## 六、结论边界",
            "- 本报告只汇总区间内的反馈、问题分布和报告处理状态，"
            "不判断故障根因，也不把簇当作根因结论。",
            "- 主要问题按「产品 + 错误码」聚合；无错误码反馈按现象词单独统计，"
            "两者不要直接相加。",
            "- 报告处理状态来自报告中心的人工标记；未标记的报告按待处理计算。",
            "- 周期对比只反映数量变化，不代表问题严重程度同比例变化。",
            "",
        ])

        return "\n".join(lines)
