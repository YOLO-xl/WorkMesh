"""诊断报告数据访问层。

报告是诊断结果的持久化形态：Markdown 用于展示与下载，payload_json
保留完整结果（证据引用、结论、上下文计数），方便以后回放和二次分析。
"""

import hashlib
import sqlite3

from datetime import datetime, timedelta, timezone

from pathlib import Path
from typing import Any, Optional

from database.connection import get_connection
from database.utils import generate_id, json_dumps, json_loads, utc_now


def _row_to_report(row: sqlite3.Row, with_payload: bool = False) -> dict:
    report = {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "feedback_id": row["feedback_id"],
        "title": row["title"],
        "product": row["product"],
        "error_code": row["error_code"],
        "feedback_summary": row["feedback_summary"],
        "severity": row["severity"],
        "confidence": row["confidence"],
        "generated_by": row["generated_by"],
        "model": row["model"],
        "window_minutes": row["window_minutes"],
        "min_score": row["min_score"],
        "matched_count": row["matched_count"],
        "context_count": row["context_count"],
        "markdown": row["markdown"],
        "created_at": row["created_at"],
    }

    if with_payload:
        report["payload"] = json_loads(row["payload_json"], {}) or {}

    return report


def _content_hash(feedback_id: str, markdown: str) -> str:
    payload = f"{feedback_id}\n{markdown}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ReportRepository:
    """诊断报告的保存、检索与删除。"""

    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    def save(
        self,
        workspace_id: str,
        feedback: dict,
        result: dict,
        model: Optional[str] = None,
        window_minutes: int = 30,
        min_score: float = 30.0,
    ) -> tuple[str, bool]:
        """保存一份诊断报告；内容相同则复用已有记录。"""

        markdown = result.get("markdown") or ""
        feedback_id = feedback.get("id") or ""

        if not feedback_id:
            raise ValueError("反馈缺少 id，无法归档报告")

        content_hash = _content_hash(feedback_id, markdown)

        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT id
                FROM diagnosis_reports
                WHERE workspace_id = ?
                  AND feedback_id = ?
                  AND content_hash = ?
                """,
                (workspace_id, feedback_id, content_hash),
            ).fetchone()

            if existing:
                return existing["id"], False

            report_id = generate_id()
            now = utc_now()

            conn.execute(
                """
                INSERT INTO diagnosis_reports
                (
                    id, workspace_id, feedback_id, title, product, error_code,
                    feedback_summary, severity, confidence, generated_by,
                    model, window_minutes, min_score, matched_count,
                    context_count, markdown, payload_json, content_hash,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    workspace_id,
                    feedback_id,
                    result.get("title") or "未命名诊断",
                    feedback.get("product"),
                    feedback.get("error_code"),
                    (feedback.get("description") or "")[:120],
                    result.get("severity"),
                    result.get("confidence"),
                    "llm" if result.get("generated_by") == "llm" else "rule",
                    model,
                    int(window_minutes),
                    float(min_score),
                    int((result.get("context_counts") or {}).get("matched", 0)),
                    int((result.get("context_counts") or {}).get("context", 0)),
                    markdown,
                    json_dumps(result),
                    content_hash,
                    now,
                ),
            )

        return report_id, True

    def list_reports(
        self,
        workspace_id: str,
        keyword: Optional[str] = None,
        generated_by: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        conditions = ["workspace_id = ?"]
        params: list[Any] = [workspace_id]

        if generated_by in ("rule", "llm"):
            conditions.append("generated_by = ?")
            params.append(generated_by)

        if keyword:
            pattern = f"%{keyword.strip()}%"
            conditions.append(
                """
                (
                    title LIKE ?
                    OR product LIKE ?
                    OR error_code LIKE ?
                    OR feedback_summary LIKE ?
                    OR markdown LIKE ?
                )
                """
            )
            params.extend([pattern] * 5)

        sql = f"""
            SELECT *
            FROM diagnosis_reports
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC, rowid DESC
            LIMIT ? OFFSET ?
        """
        params.extend([int(limit), int(offset)])

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [_row_to_report(row) for row in rows]

    def get(self, report_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM diagnosis_reports
                WHERE id = ?
                """,
                (report_id,),
            ).fetchone()

        return _row_to_report(row, with_payload=True) if row else None

    def delete(self, report_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM diagnosis_reports WHERE id = ?",
                (report_id,),
            )

        return cursor.rowcount > 0

    def stats(self, workspace_id: str) -> dict:
        threshold = (
            datetime.now(timezone.utc) - timedelta(days=7)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(DISTINCT feedback_id) AS covered_feedback,
                    SUM(CASE WHEN generated_by = 'llm' THEN 1 ELSE 0 END) AS llm_total,
                    SUM(
                        CASE WHEN created_at >= ?
                        THEN 1 ELSE 0 END
                    ) AS recent_total
                FROM diagnosis_reports
                WHERE workspace_id = ?
                """,
                (threshold, workspace_id),
            ).fetchone()

        return {
            "total": row["total"] or 0,
            "covered_feedback": row["covered_feedback"] or 0,
            "llm_total": row["llm_total"] or 0,
            "recent_total": row["recent_total"] or 0,
        }
