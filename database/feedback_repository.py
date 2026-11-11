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
        """检查同一数据源是否已经完整导入过该文件。

        只有 completed 且至少成功写入一条记录的批次才算“已导入”；
        partially_failed / failed / 空 completed 批次允许重新导入，
        重试时由 filter_new_feedback_records 跳过已成功记录。
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM import_batches
                WHERE source_id = ?
                  AND file_hash = ?
                  AND status = 'completed'
                  AND success_records > 0
                LIMIT 1
                """,
                (source_id, file_hash),
            ).fetchone()

        return row is not None

    @staticmethod
    def _external_id_value(record: dict) -> Optional[str]:
        """读取用于去重的外部业务 ID，空值/NaN 视为没有 ID。"""

        value = record.get("feedback_id")
        if value is None or (
            isinstance(value, str) and not value.strip()
        ):
            value = record.get("external_id")

        value = normalize_value(value)
        if value is None:
            return None

        text = str(value).strip()
        return text or None

    @staticmethod
    def _fallback_key_values(record: dict) -> Optional[tuple]:
        """没有外部 ID 时，用时间 + 描述 + 可选 uid 构造兜底判重键。

        feedback_time 和 description 必须同时有效；uid 仅作为附加区分字段，
        允许为空。缺少时间或描述时返回 None，不能只凭 uid 判重。
        """

        uid = normalize_value(record.get("uid"))
        feedback_time = normalize_value(record.get("feedback_time"))
        description = normalize_value(record.get("description"))

        uid_text = "" if uid is None else str(uid).strip()
        time_text = "" if feedback_time is None else str(feedback_time).strip()
        description_text = (
            "" if description is None else str(description).strip()
        )

        if not time_text or not description_text:
            return None

        return (uid_text, time_text, description_text)

    def filter_new_feedback_records(
        self,
        source_id: str,
        records: Iterable[dict],
    ) -> tuple[list[dict], int]:
        """过滤已经成功写入或本批次内重复的记录，返回 (待写入记录, 跳过条数)。

        用于 partially_failed / failed 批次重试：已成功的记录不重复插入，
        失败行仍按原始行号和新批次继续写入。

        遍历过程中会把已接受记录的 external_id / 兜底业务键加入判重集合，
        因此同一批数据内部的重复也能被识别。
        """

        records = list(records)
        if not records:
            return [], 0

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT external_id, uid, feedback_time, description
                FROM feedback_records
                WHERE source_id = ?
                  AND deleted_at IS NULL
                """,
                (source_id,),
            ).fetchall()

        existing_external = {
            str(row["external_id"]).strip()
            for row in rows
            if row["external_id"] is not None
            and str(row["external_id"]).strip()
        }
        existing_fallback = set()
        for row in rows:
            key = self._fallback_key_values(dict(row))
            if key is not None:
                existing_fallback.add(key)

        seen_external = set(existing_external)
        seen_fallback = set(existing_fallback)

        new_records = []
        previously_imported = 0

        for record in records:
            external_id = self._external_id_value(record)
            fallback_key = self._fallback_key_values(record)

            duplicate = False

            if external_id is not None:
                # 有外部 ID 时保持 ID 优先判重；兜底键只作为后续无 ID 记录的线索。
                if external_id in seen_external:
                    duplicate = True
            elif fallback_key is not None and fallback_key in seen_fallback:
                duplicate = True

            if duplicate:
                previously_imported += 1
                continue

            if external_id is not None:
                seen_external.add(external_id)
            if fallback_key is not None:
                seen_fallback.add(fallback_key)

            new_records.append(record)

        return new_records, previously_imported

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
                external_id = None
                normalized = {}
                raw_payload = None
                source_row = index + 1

                if isinstance(record, dict):
                    import_row = record.get("_import_row")
                    if isinstance(import_row, int) and import_row > 0:
                        source_row = import_row

                try:
                    if not isinstance(record, dict):
                        raise TypeError("反馈记录必须是字典")

                    external_id = self._external_id_value(record)

                    feedback_time = normalize_value(
                        record.get("feedback_time")
                    )

                    raw_payload = normalize_value(
                        record.get("raw_payload") or {}
                    )
                    extra_data = normalize_value(
                        record.get("extra_data") or {}
                    )

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
                    AttributeError,
                ) as exc:
                    failed_count += 1
                    error = {
                        "row": source_row,
                        "error": str(exc),
                    }
                    if external_id is not None:
                        error["feedback_id"] = external_id
                    if normalized:
                        error["uid"] = normalized.get("uid")
                        error["description"] = normalized.get("description")
                    if raw_payload is not None:
                        error["raw_payload"] = raw_payload
                    errors.append(error)

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

    def get_batch_errors(self, batch_id: str) -> list[dict]:
        """读取批次失败行，供后续补导 / 前端展示使用。"""

        batch = self.get_batch_statistics(batch_id)
        if batch is None:
            return []

        errors = batch.get("error_summary") or []
        return errors if isinstance(errors, list) else []

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

