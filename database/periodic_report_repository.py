"""周期性分析报告的保存、检索与删除。"""

import hashlib
import sqlite3

from pathlib import Path
from typing import Optional

from database.connection import get_connection
from database.utils import generate_id, json_dumps, json_loads, utc_now


def _content_hash(markdown: str, stats: dict) -> str:
    payload = json_dumps(stats)
    return hashlib.sha256(f"{markdown}\n{payload}".encode("utf-8")).hexdigest()


def _row_to_report(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "product": row["product"] or None,
        "title": row["title"],
        "markdown": row["markdown"],
        "stats": json_loads(row["stats"]) or {},
        "created_at": row["created_at"],
    }


class PeriodicReportRepository:
    """周期报告归档。同一周期、同一产品、同一内容不会重复入库。"""

    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    def save(
        self,
        workspace_id: str,
        period_start: str,
        period_end: str,
        product: Optional[str],
        result: dict,
    ) -> tuple[str, bool]:
        markdown = result["markdown"]
        stats = result.get("stats") or {}
        content_hash = _content_hash(markdown, stats)
        product_key = product or ""

        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT id
                FROM periodic_reports
                WHERE workspace_id = ?
                  AND period_start = ?
                  AND period_end = ?
                  AND product = ?
                  AND content_hash = ?
                """,
                (workspace_id, period_start, period_end, product_key, content_hash),
            ).fetchone()

            if existing:
                return existing["id"], False

            report_id = generate_id()
            now = utc_now()
            conn.execute(
                """
                INSERT INTO periodic_reports
                (
                    id, workspace_id, period_start, period_end, product,
                    title, markdown, stats, content_hash, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    workspace_id,
                    period_start,
                    period_end,
                    product_key,
                    result["title"],
                    markdown,
                    json_dumps(stats),
                    content_hash,
                    now,
                ),
            )

        return report_id, True

    def list_reports(
        self,
        workspace_id: str,
        keyword: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        params: list = [workspace_id]
        where = "workspace_id = ?"

        if keyword:
            like = f"%{keyword}%"
            where += """
                AND (
                    title LIKE ? OR product LIKE ? OR markdown LIKE ?
                )
            """
            params.extend([like, like, like])

        params.extend([int(limit), int(offset)])

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM periodic_reports
                WHERE {where}
                ORDER BY period_end DESC, created_at DESC, rowid DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()

        return [_row_to_report(row) for row in rows]

    def get(self, report_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM periodic_reports WHERE id = ?",
                (report_id,),
            ).fetchone()

        return _row_to_report(row) if row else None

    def delete(self, report_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM periodic_reports WHERE id = ?",
                (report_id,),
            )
        return cursor.rowcount > 0

    def stats(self, workspace_id: str) -> dict:
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM periodic_reports WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()[0]

            recent = conn.execute(
                """
                SELECT COUNT(*)
                FROM periodic_reports
                WHERE workspace_id = ?
                  AND created_at >= datetime('now', '-7 days')
                """,
                (workspace_id,),
            ).fetchone()[0]

        return {"total": total, "recent_total": recent}
