"""问题簇报告数据访问层。

簇报告以 cluster_key 为去重键：同一问题簇、同一报告内容只保留一份；
成员变化会生成新的 cluster_key，因此可以保留问题簇的演进快照。
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
        "cluster_key": row["cluster_key"],
        "title": row["title"],
        "product": row["product"],
        "error_code": row["error_code"],
        "severity": row["severity"],
        "confidence": row["confidence"],
        "size": row["size"],
        "unique_users": row["unique_users"],
        "evidence_total": row["evidence_total"],
        "representative_feedback_id": row["representative_feedback_id"],
        "generated_by": row["generated_by"],
        "model": row["model"],
        "markdown": row["markdown"],
        "created_at": row["created_at"],
    }

    if with_payload:
        report["payload"] = json_loads(row["payload_json"], {}) or {}

    return report


def _content_hash(cluster_key: str, markdown: str) -> str:
    payload = f"{cluster_key}\n{markdown}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class ClusterReportRepository:
    """问题簇报告的保存、检索与删除。"""

    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    def save(
        self,
        workspace_id: str,
        cluster: dict,
        result: dict,
        model: Optional[str] = None,
    ) -> tuple[str, bool]:
        """保存一份簇报告；同一簇、同一内容则复用已有记录。"""

        cluster_key = str(cluster.get("cluster_id") or "")
        markdown = result.get("markdown") or ""

        if not cluster_key:
            raise ValueError("问题簇缺少 cluster_id，无法归档报告")

        content_hash = _content_hash(cluster_key, markdown)

        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT id
                FROM cluster_reports
                WHERE workspace_id = ?
                  AND cluster_key = ?
                  AND content_hash = ?
                """,
                (workspace_id, cluster_key, content_hash),
            ).fetchone()

            if existing:
                return existing["id"], False

            report_id = generate_id()
            now = utc_now()

            conn.execute(
                """
                INSERT INTO cluster_reports
                (
                    id, workspace_id, cluster_key, title, product, error_code,
                    severity, confidence, size, unique_users, evidence_total,
                    representative_feedback_id, generated_by, model,
                    markdown, payload_json, content_hash, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    workspace_id,
                    cluster_key,
                    result.get("title") or "未命名簇报告",
                    cluster.get("product"),
                    cluster.get("error_code"),
                    result.get("severity") or cluster.get("severity"),
                    result.get("confidence"),
                    int(cluster.get("size") or 0),
                    int(cluster.get("unique_users") or 0),
                    int((cluster.get("evidence") or {}).get("total", 0)),
                    (cluster.get("representative") or {}).get("id"),
                    "llm" if result.get("generated_by") == "llm" else "rule",
                    model,
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
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        conditions = ["workspace_id = ?"]
        params: list[Any] = [workspace_id]

        if keyword:
            pattern = f"%{keyword.strip()}%"
            conditions.append(
                """
                (
                    title LIKE ?
                    OR product LIKE ?
                    OR error_code LIKE ?
                    OR markdown LIKE ?
                )
                """
            )
            params.extend([pattern] * 4)

        sql = f"""
            SELECT *
            FROM cluster_reports
            WHERE {' AND '.join(conditions)}
            ORDER BY created_at DESC, rowid DESC
            LIMIT ? OFFSET ?
        """
        params.extend([int(limit), int(offset)])

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [_row_to_report(row) for row in rows]

    def issue_history(
        self,
        workspace_id: str,
        product: Optional[str],
        error_code: Optional[str],
        title: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """同一问题的历史快照，按时间升序。

        有错误码时按产品 + 错误码归并；无错误码时用报告标题兜底，
        避免把同一产品下现象不同的簇当成同一个问题。
        """

        if error_code:
            conditions = "product IS ? AND error_code IS ?"
            params: list[Any] = [workspace_id, product, error_code]
        else:
            conditions = "product IS ? AND title = ?"
            params = [workspace_id, product, title or ""]

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM cluster_reports
                WHERE workspace_id = ?
                  AND {conditions}
                ORDER BY created_at ASC, rowid ASC
                LIMIT ?
                """,
                (*params, int(limit)),
            ).fetchall()

        return [_row_to_report(row) for row in rows]

    def get(self, report_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cluster_reports WHERE id = ?",
                (report_id,),
            ).fetchone()

        return _row_to_report(row, with_payload=True) if row else None

    def delete(self, report_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM cluster_reports WHERE id = ?",
                (report_id,),
            )

        return cursor.rowcount > 0

    def stats(self, workspace_id: str) -> dict:
        """簇报告总量、近 7 天新增、覆盖的不同簇。"""

        recent_threshold = (
            (datetime.now(timezone.utc) - timedelta(days=7))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM cluster_reports WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()[0]
            recent = conn.execute(
                """
                SELECT COUNT(*)
                FROM cluster_reports
                WHERE workspace_id = ? AND created_at >= ?
                """,
                (workspace_id, recent_threshold),
            ).fetchone()[0]
            clusters = conn.execute(
                """
                SELECT COUNT(DISTINCT cluster_key)
                FROM cluster_reports
                WHERE workspace_id = ?
                """,
                (workspace_id,),
            ).fetchone()[0]

        return {
            "total": total,
            "recent_total": recent,
            "covered_clusters": clusters,
        }
