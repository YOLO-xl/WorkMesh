"""报告处理记录：待处理 / 已处理 / 已忽略、负责人与备注。

单条诊断和簇报告分属两张表，处理状态用独立表按 scope 关联，
避免两套报告表各加一遍字段，也方便以后加更多报告类型。
"""

import sqlite3

from pathlib import Path
from typing import Any, Optional

from database.connection import get_connection
from database.utils import utc_now


REPORT_SCOPES = ("feedback", "cluster")
TRACKING_STATUSES = ("pending", "handled", "ignored")
DEFAULT_STATUS = "pending"
NOTE_LIMIT = 500


def _default_entry(report_id: str, scope: str) -> dict:
    return {
        "report_id": report_id,
        "scope": scope,
        "status": DEFAULT_STATUS,
        "owner": None,
        "note": None,
        "updated_at": None,
    }


def _row_to_entry(row: sqlite3.Row) -> dict:
    return {
        "report_id": row["report_id"],
        "scope": row["report_scope"],
        "status": row["status"],
        "owner": row["owner"],
        "note": row["note"],
        "updated_at": row["updated_at"],
    }


class ReportTrackingRepository:
    """报告处理状态的读取与保存。"""

    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    def get(self, workspace_id: str, scope: str, report_id: str) -> dict:
        self._validate_scope(scope)

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM report_tracking
                WHERE workspace_id = ?
                  AND report_scope = ?
                  AND report_id = ?
                """,
                (workspace_id, scope, report_id),
            ).fetchone()

        return _row_to_entry(row) if row else _default_entry(report_id, scope)

    def get_many(
        self,
        workspace_id: str,
        scope: str,
        report_ids: list[str],
    ) -> dict[str, dict]:
        """批量读取，未建过记录的按默认「待处理」返回。"""

        self._validate_scope(scope)
        result = {
            report_id: _default_entry(report_id, scope)
            for report_id in report_ids
        }

        if not report_ids:
            return result

        placeholders = ",".join("?" for _ in report_ids)
        params: list[Any] = [workspace_id, scope, *report_ids]

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM report_tracking
                WHERE workspace_id = ?
                  AND report_scope = ?
                  AND report_id IN ({placeholders})
                """,
                params,
            ).fetchall()

        for row in rows:
            result[row["report_id"]] = _row_to_entry(row)

        return result

    def upsert(
        self,
        workspace_id: str,
        scope: str,
        report_id: str,
        status: str = DEFAULT_STATUS,
        owner: Optional[str] = None,
        note: Optional[str] = None,
    ) -> dict:
        self._validate_scope(scope)
        self._validate_status(status)

        cleaned_owner = " ".join(str(owner or "").split())[:80] or None
        cleaned_note = " ".join(str(note or "").split())[:NOTE_LIMIT] or None
        updated_at = utc_now()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO report_tracking
                (
                    workspace_id, report_scope, report_id,
                    status, owner, note, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (workspace_id, report_scope, report_id)
                DO UPDATE SET
                    status = excluded.status,
                    owner = excluded.owner,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    workspace_id,
                    scope,
                    report_id,
                    status,
                    cleaned_owner,
                    cleaned_note,
                    updated_at,
                ),
            )

        return self.get(workspace_id, scope, report_id)

    def bulk_update(
        self,
        workspace_id: str,
        scope: str,
        report_ids: list[str],
        status: str = DEFAULT_STATUS,
        owner: Optional[str] = None,
        note: Optional[str] = None,
    ) -> int:
        """批量更新处理状态。

        负责人 / 备注留空时保留各报告原值，避免一次批量操作清空已有备注。
        """

        self._validate_scope(scope)
        self._validate_status(status)

        if not report_ids:
            return 0

        entries = self.get_many(workspace_id, scope, report_ids)
        cleaned_owner = " ".join(str(owner or "").split())[:80] or None
        cleaned_note = " ".join(str(note or "").split())[:NOTE_LIMIT] or None
        updated_at = utc_now()

        rows = [
            (
                workspace_id,
                scope,
                report_id,
                status,
                cleaned_owner
                if cleaned_owner
                else entries[report_id].get("owner"),
                cleaned_note if cleaned_note else entries[report_id].get("note"),
                updated_at,
            )
            for report_id in report_ids
        ]

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO report_tracking
                (
                    workspace_id, report_scope, report_id,
                    status, owner, note, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (workspace_id, report_scope, report_id)
                DO UPDATE SET
                    status = excluded.status,
                    owner = excluded.owner,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                rows,
            )

        return len(rows)

    def delete(self, workspace_id: str, scope: str, report_id: str) -> bool:
        self._validate_scope(scope)

        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM report_tracking
                WHERE workspace_id = ?
                  AND report_scope = ?
                  AND report_id = ?
                """,
                (workspace_id, scope, report_id),
            )

        return cursor.rowcount > 0

    def stats(self, workspace_id: str) -> dict[str, int]:
        counts = {status: 0 for status in TRACKING_STATUSES}

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*)
                FROM report_tracking
                WHERE workspace_id = ?
                GROUP BY status
                """,
                (workspace_id,),
            ).fetchall()

        for status, count in rows:
            if status in counts:
                counts[status] = count

        return counts

    @staticmethod
    def _validate_scope(scope: str) -> None:
        if scope not in REPORT_SCOPES:
            raise ValueError(f"不支持的报告类型：{scope}")

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in TRACKING_STATUSES:
            raise ValueError(f"不支持的处理状态：{status}")
