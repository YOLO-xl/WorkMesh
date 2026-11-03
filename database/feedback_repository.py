import json
import math
import sqlite3
import uuid

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from database.connection import get_connection


def generate_id() -> str:
    return str(uuid.uuid4())


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def normalize_value(value: Any) -> Any:
    """将 Pandas/Numpy 等类型转换为可存储的 Python 基础类型。"""

    if value is None:
        return None

    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()

    if hasattr(value, "item") and callable(value.item):
        try:
            value = value.item()
        except (ValueError, TypeError):
            pass

    if isinstance(value, float) and math.isnan(value):
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

    if isinstance(value, Path):
        return str(value)

    return value


def json_dumps(data: Any) -> str:
    """生成兼容 SQLite TEXT 字段的 JSON 字符串。"""

    return json.dumps(
        data,
        ensure_ascii=False,
        default=str,
        allow_nan=False,
    )


class FeedbackRepository:
    """WorkMesh 反馈数据 Repository。"""

    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    def create_workspace(
        self,
        name: str,
        description: Optional[str] = None,
    ) -> str:
        workspace_id = generate_id()
        now = utc_now()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workspaces
                (id, name, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (workspace_id, name, description, now, now),
            )

        return workspace_id

    def get_default_workspace(self) -> str:
        """获取默认工作空间；不存在时自动创建。"""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM workspaces
                WHERE name = ?
                ORDER BY created_at
                LIMIT 1
                """,
                ("Default Workspace",),
            ).fetchone()

            if row:
                return row["id"]

            workspace_id = generate_id()
            now = utc_now()

            conn.execute(
                """
                INSERT INTO workspaces
                (id, name, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    "Default Workspace",
                    "WorkMesh 默认工作空间",
                    now,
                    now,
                ),
            )

            return workspace_id

    def create_data_source(
        self,
        workspace_id: str,
        name: str,
        source_type: str = "csv",
        connector_type: Optional[str] = None,
        config: Optional[dict] = None,
    ) -> str:
        source_id = generate_id()
        now = utc_now()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO data_sources
                (
                    id, workspace_id, name, source_type,
                    connector_type, config_json,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    source_id,
                    workspace_id,
                    name,
                    source_type,
                    connector_type,
                    json_dumps(config or {}),
                    now,
                    now,
                ),
            )

        return source_id

    def get_or_create_file_source(
        self,
        workspace_id: str,
        source_type: str,
        name: str = "本地文件导入",
    ) -> str:
        """复用同一工作空间下的文件导入数据源。"""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM data_sources
                WHERE workspace_id = ?
                  AND source_type = ?
                  AND name = ?
                  AND status = 'active'
                LIMIT 1
                """,
                (workspace_id, source_type, name),
            ).fetchone()

            if row:
                return row["id"]

        return self.create_data_source(
            workspace_id=workspace_id,
            name=name,
            source_type=source_type,
            connector_type="file",
        )

    def create_import_batch(
        self,
        workspace_id: str,
        source_id: str,
        file_name: Optional[str] = None,
        file_size: Optional[int] = None,
        file_hash: Optional[str] = None,
        total_records: int = 0,
    ) -> str:
        batch_id = generate_id()
        now = utc_now()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO import_batches
                (
                    id, workspace_id, source_id,
                    file_name, file_size, file_hash,
                    status, total_records, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    batch_id,
                    workspace_id,
                    source_id,
                    file_name,
                    file_size,
                    file_hash,
                    total_records,
                    now,
                ),
            )

        return batch_id

    def bulk_insert_feedback(
        self,
        batch_id: str,
        records: Iterable[dict],
    ) -> dict:
        """
        批量写入反馈记录。

        每条记录独立处理：
        - 成功记录正常保存。
        - 单条失败记录计入失败数量。
        - 不因单条异常导致整批数据回滚。
        """

        records = list(records)

        conn = self._connect()

        try:
            batch = conn.execute(
                """
                SELECT workspace_id, source_id
                FROM import_batches
                WHERE id = ?
                """,
                (batch_id,),
            ).fetchone()

            if batch is None:
                raise ValueError(f"导入批次不存在：{batch_id}")

            workspace_id = batch["workspace_id"]
            source_id = batch["source_id"]

            success_count = 0
            failed_count = 0
            errors = []

            for index, record in enumerate(records):
                try:
                    external_id = normalize_value(
                        record.get("feedback_id")
                        or record.get("external_id")
                    )

                    feedback_time = normalize_value(
                        record.get("feedback_time")
                    )

                    raw_payload = record.get("raw_payload") or {}
                    extra_data = record.get("extra_data") or {}

                    normalized = {
                        "uid": normalize_value(record.get("uid")),
                        "feedback_time": feedback_time,
                        "source_type": normalize_value(
                            record.get("source_type")
                            or record.get("source")
                        ),
                        "product": normalize_value(record.get("product")),
                        "version": normalize_value(record.get("version")),
                        "platform": normalize_value(record.get("platform")),
                        "device": normalize_value(record.get("device")),
                        "error_code": normalize_value(
                            record.get("error_code")
                        ),
                        "description": normalize_value(
                            record.get("description")
                        ),
                        "status": normalize_value(
                            record.get("status")
                        ) or "new",
                    }

                    if not isinstance(raw_payload, dict):
                        raise ValueError("raw_payload 必须是字典")

                    if not isinstance(extra_data, dict):
                        raise ValueError("extra_data 必须是字典")

                    now = utc_now()

                    conn.execute(
                        """
                        INSERT INTO feedback_records
                        (
                            id, workspace_id, source_id, batch_id,
                            external_id, uid, feedback_time,
                            source_type, product, version,
                            platform, device, error_code,
                            description, status,
                            raw_payload, extra_data,
                            created_at, updated_at
                        )
                        VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        (
                            generate_id(),
                            workspace_id,
                            source_id,
                            batch_id,
                            external_id,
                            normalized["uid"],
                            normalized["feedback_time"],
                            normalized["source_type"],
                            normalized["product"],
                            normalized["version"],
                            normalized["platform"],
                            normalized["device"],
                            normalized["error_code"],
                            normalized["description"],
                            normalized["status"],
                            json_dumps(raw_payload),
                            json_dumps(extra_data),
                            now,
                            now,
                        ),
                    )

                    success_count += 1

                except (
                    sqlite3.IntegrityError,
                    ValueError,
                    TypeError,
                    OverflowError,
                ) as exc:
                    failed_count += 1
                    errors.append({
                        "row": index + 1,
                        "error": str(exc),
                    })

            completed_at = utc_now()

            if failed_count == 0:
                batch_status = "completed"
            elif success_count == 0:
                batch_status = "failed"
            else:
                batch_status = "partially_failed"

            conn.execute(
                """
                UPDATE import_batches
                SET status = ?,
                    processed_records = ?,
                    success_records = ?,
                    failed_records = ?,
                    error_summary = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    batch_status,
                    success_count + failed_count,
                    success_count,
                    failed_count,
                    json_dumps(errors) if errors else None,
                    completed_at,
                    batch_id,
                ),
            )

            conn.commit()

            return {
                "batch_id": batch_id,
                "total_records": len(records),
                "success_records": success_count,
                "failed_records": failed_count,
                "status": batch_status,
                "errors": errors,
            }

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

    def list_feedback(
        self,
        workspace_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """按工作空间分页查询未软删除的反馈。"""

        if page < 1:
            raise ValueError("page 必须大于等于 1")

        if page_size < 1 or page_size > 500:
            raise ValueError("page_size 必须在 1 到 500 之间")

        offset = (page - 1) * page_size

        with self._connect() as conn:
            total = conn.execute(
                """
                SELECT COUNT(*)
                FROM feedback_records
                WHERE workspace_id = ?
                  AND deleted_at IS NULL
                """,
                (workspace_id,),
            ).fetchone()[0]

            rows = conn.execute(
                """
                SELECT *
                FROM feedback_records
                WHERE workspace_id = ?
                  AND deleted_at IS NULL
                ORDER BY feedback_time DESC, created_at DESC
                LIMIT ? OFFSET ?
                """,
                (workspace_id, page_size, offset),
            ).fetchall()

            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "records": [dict(row) for row in rows],
            }

    def get_batch_statistics(self, batch_id: str) -> Optional[dict]:
        """查询导入批次统计信息。"""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    workspace_id,
                    source_id,
                    file_name,
                    status,
                    total_records,
                    processed_records,
                    success_records,
                    failed_records,
                    error_summary,
                    created_at,
                    completed_at
                FROM import_batches
                WHERE id = ?
                """,
                (batch_id,),
            ).fetchone()

            if row is None:
                return None

            result = dict(row)

            if result["error_summary"]:
                result["error_summary"] = json.loads(
                    result["error_summary"]
                )

            return result
