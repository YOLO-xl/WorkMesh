import json
import sqlite3

from pathlib import Path
from typing import Any, Iterable, Optional

from database.connection import get_connection
from database.utils import (
    generate_id,
    json_dumps,
    normalize_value,
    utc_now,
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

    def has_imported_file(
        self,
        source_id: str,
        file_hash: str,
    ) -> bool:
        """检查同一数据源是否已经导入过该文件。"""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM import_batches
                WHERE source_id = ?
                  AND file_hash = ?
                  AND status IN ('completed', 'partially_failed')
                LIMIT 1
                """,
                (source_id, file_hash),
            ).fetchone()

        return row is not None

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

        except Exception as exc:
            conn.rollback()

            status_conn = None
            try:
                status_conn = self._connect()
                status_conn.execute(
                    """
                    UPDATE import_batches
                    SET status = 'failed',
                        error_summary = ?,
                        completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        json_dumps([{"error": str(exc)}]),
                        utc_now(),
                        batch_id,
                    ),
                )
                status_conn.commit()
            except Exception:
                if status_conn is not None:
                    status_conn.rollback()
            finally:
                if status_conn is not None:
                    status_conn.close()

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

    def query_feedback(
        self,
        workspace_id: str,
        *,
        products: Optional[Iterable[str]] = None,
        error_codes: Optional[Iterable[str]] = None,
        uid: Optional[str] = None,
        keyword: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        """按场景条件查询反馈，供故障场景模型使用。

        与分页列表不同，这里按产品、错误码、UID 和时间窗口过滤，
        返回按反馈时间倒序的记录；上限仍为 500，避免单次查询失控。
        """

        conditions = ["workspace_id = ?", "deleted_at IS NULL"]
        parameters: list[Any] = [workspace_id]

        product_values = [
            str(item).strip() for item in (products or []) if str(item).strip()
        ]
        if product_values:
            conditions.append(
                "product IN (" + ",".join("?" for _ in product_values) + ")"
            )
            parameters.extend(product_values)

        code_values = [
            str(item).strip() for item in (error_codes or []) if str(item).strip()
        ]
        if code_values:
            conditions.append(
                "error_code IN (" + ",".join("?" for _ in code_values) + ")"
            )
            parameters.extend(code_values)

        if uid:
            conditions.append("uid = ?")
            parameters.append(str(uid))

        if keyword:
            keyword_like = f"%{str(keyword).strip()}%"
            conditions.append(
                "(description LIKE ? OR error_code LIKE ? OR external_id LIKE ?)"
            )
            parameters.extend([keyword_like, keyword_like, keyword_like])

        if start:
            conditions.append("feedback_time >= ?")
            parameters.append(str(start))

        if end:
            conditions.append("feedback_time <= ?")
            parameters.append(str(end))

        limit = min(max(int(limit), 1), 500)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM feedback_records
                WHERE {' AND '.join(conditions)}
                ORDER BY feedback_time DESC, created_at DESC
                LIMIT ?
                """,
                (*parameters, limit),
            ).fetchall()

        return [dict(row) for row in rows]

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

    def get_feedback_analytics(self, workspace_id: str) -> dict:
        """获取工作空间的反馈分析统计数据。"""
        with self._connect() as conn:
            overview = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_feedback,
                    COUNT(DISTINCT uid) AS unique_users,
                    COUNT(DISTINCT product) AS unique_products
                FROM feedback_records
                WHERE workspace_id = ?
                  AND deleted_at IS NULL
                """,
                (workspace_id,),
            ).fetchone()

            daily_trend = conn.execute(
                """
                SELECT
                    date(feedback_time) AS feedback_date,
                    COUNT(*) AS feedback_count
                FROM feedback_records
                WHERE workspace_id = ?
                  AND deleted_at IS NULL
                  AND feedback_time IS NOT NULL
                GROUP BY date(feedback_time)
                ORDER BY feedback_date ASC
                """,
                (workspace_id,),
            ).fetchall()

            distribution_queries = {
                "version_distribution": "version",
                "platform_distribution": "platform",
                "source_distribution": "source_type",
                "error_code_distribution": "error_code",
            }

            distributions = {}

            for key, field in distribution_queries.items():
                rows = conn.execute(
                    f"""
                    SELECT
                        {field} AS name,
                        COUNT(*) AS count
                    FROM feedback_records
                    WHERE workspace_id = ?
                      AND deleted_at IS NULL
                      AND {field} IS NOT NULL
                      AND TRIM({field}) != ''
                    GROUP BY {field}
                    ORDER BY count DESC, name ASC
                    LIMIT 10
                    """,
                    (workspace_id,),
                ).fetchall()

                distributions[key] = [
                    dict(row) for row in rows
                ]

            return {
                "overview": dict(overview),
                "daily_trend": [
                    dict(row) for row in daily_trend
                ],
                **distributions,
            }

    def get_feedback_record(
        self,
        workspace_id: str,
        feedback_id: str,
    ) -> Optional[dict]:
        """按 ID 查询单条反馈记录（用于证据关联的锚点）。"""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM feedback_records
                WHERE workspace_id = ?
                  AND id = ?
                  AND deleted_at IS NULL
                """,
                (workspace_id, feedback_id),
            ).fetchone()

        return dict(row) if row else None

    def find_related_feedback(
        self,
        workspace_id: str,
        anchor_id: str,
        uid: Optional[str] = None,
        error_code: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """查找其他可能相关的反馈记录。

        关联条件：相同 UID 或相同错误码，且落在同一时间窗口内。
        该查询用于「同类问题是否集中爆发」的判断，不涉及语义相似度。
        """

        conditions = ["workspace_id = ?", "deleted_at IS NULL", "id != ?"]
        parameters: list[Any] = [workspace_id, anchor_id]

        key_conditions = []
        if uid:
            key_conditions.append("uid = ?")
            parameters.append(uid)
        if error_code:
            key_conditions.append("error_code = ?")
            parameters.append(error_code)

        if not key_conditions:
            return []

        conditions.append("(" + " OR ".join(key_conditions) + ")")

        if start_time:
            conditions.append("feedback_time >= ?")
            parameters.append(start_time)
        if end_time:
            conditions.append("feedback_time <= ?")
            parameters.append(end_time)

        limit = min(max(int(limit), 1), 200)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM feedback_records
                WHERE {' AND '.join(conditions)}
                ORDER BY feedback_time DESC
                LIMIT ?
                """,
                (*parameters, limit),
            ).fetchall()

        return [dict(row) for row in rows]

