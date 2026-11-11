"""WorkMesh 证据数据访问层。

负责两件事：
1. 把标准化后的外部证据写入 evidence_records（重复接入自动复用已有记录）。
2. 保存「锚点 ↔ 证据」的可解释关联（分数、命中键、关联理由）。

数据访问统一走 Repository，不把 SQL 泄漏到 Streamlit 或分析逻辑中。
"""

import json
import sqlite3

from pathlib import Path
from typing import Any, Iterable, Optional

from core.evidence import Evidence, format_timestamp, parse_timestamp
from database.connection import get_connection
from database.utils import generate_id, json_dumps, utc_now


ANCHOR_TYPES = ("feedback", "evidence", "analysis")
RELATIONS = ("correlates", "supports", "contradicts", "context")


def _row_to_evidence(row: sqlite3.Row) -> Evidence:
    return Evidence(
        evidence_type=row["evidence_type"],
        record_id=row["record_id"],
        timestamp=parse_timestamp(row["timestamp"]),
        uid=row["uid"],
        request_id=row["request_id"],
        trace_id=row["trace_id"],
        service=row["service"],
        level=row["level"],
        error_code=row["error_code"],
        title=row["title"],
        message=row["message"],
        attributes=row["attributes_json"],
        raw_data=row["raw_data"],
        connector_type=row["connector_type"],
        source_id=row["source_id"],
        evidence_id=row["id"],
    )


class EvidenceRepository:
    """证据记录与证据关联的 Repository。"""

    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    # ------------------------------------------------------------------
    # 数据源
    # ------------------------------------------------------------------
    def get_or_create_source(
        self,
        workspace_id: str,
        name: str,
        source_type: str = "log",
        connector_type: Optional[str] = None,
    ) -> str:
        """复用同一工作空间下同名的证据数据源。"""

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

            source_id = generate_id()
            now = utc_now()

            conn.execute(
                """
                INSERT INTO data_sources
                (
                    id, workspace_id, name, source_type,
                    connector_type, config_json,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, '{}', 'active', ?, ?)
                """,
                (
                    source_id,
                    workspace_id,
                    name,
                    source_type,
                    connector_type,
                    now,
                    now,
                ),
            )

            return source_id

    # ------------------------------------------------------------------
    # 证据写入
    # ------------------------------------------------------------------
    def _find_existing(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        source_id: str,
        evidence: Evidence,
    ) -> Optional[str]:
        """查找已存在的等价证据，避免重复接入。

        优先用「数据源 + 外部记录 ID」，其次用内容指纹。
        """

        if evidence.record_id:
            row = conn.execute(
                """
                SELECT id
                FROM evidence_records
                WHERE workspace_id = ?
                  AND source_id = ?
                  AND record_id = ?
                LIMIT 1
                """,
                (workspace_id, source_id, evidence.record_id),
            ).fetchone()

            if row:
                return row["id"]

        row = conn.execute(
            """
            SELECT id
            FROM evidence_records
            WHERE workspace_id = ?
              AND source_id = ?
              AND content_hash = ?
            LIMIT 1
            """,
            (workspace_id, source_id, evidence.content_hash()),
        ).fetchone()

        return row["id"] if row else None

    def insert_evidence(
        self,
        workspace_id: str,
        source_id: str,
        evidence: Evidence,
        conn: Optional[sqlite3.Connection] = None,
    ) -> dict:
        """写入单条证据，返回 {evidence_id, inserted}。"""

        own_connection = conn is None
        if own_connection:
            conn = self._connect()

        try:
            existing_id = self._find_existing(
                conn, workspace_id, source_id, evidence
            )

            if existing_id:
                evidence.evidence_id = existing_id
                if own_connection:
                    conn.commit()
                return {"evidence_id": existing_id, "inserted": False}

            evidence_id = generate_id()
            now = utc_now()

            conn.execute(
                """
                INSERT INTO evidence_records
                (
                    id, workspace_id, source_id, evidence_type,
                    connector_type, record_id, timestamp,
                    uid, request_id, trace_id, service, level,
                    error_code, title, message,
                    attributes_json, raw_data, content_hash,
                    created_at, updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    evidence_id,
                    workspace_id,
                    source_id,
                    evidence.evidence_type,
                    evidence.connector_type,
                    evidence.record_id,
                    format_timestamp(evidence.timestamp),
                    evidence.uid,
                    evidence.request_id,
                    evidence.trace_id,
                    evidence.service,
                    evidence.level,
                    evidence.error_code,
                    evidence.title,
                    evidence.message,
                    json_dumps(evidence.attributes),
                    json_dumps(evidence.raw_data),
                    evidence.content_hash(),
                    now,
                    now,
                ),
            )

            if own_connection:
                conn.commit()

            evidence.evidence_id = evidence_id

            return {"evidence_id": evidence_id, "inserted": True}

        except Exception:
            if own_connection:
                conn.rollback()
            raise

        finally:
            if own_connection:
                conn.close()

    def bulk_insert_evidence(
        self,
        workspace_id: str,
        source_id: str,
        evidences: Iterable[Evidence],
    ) -> dict:
        """批量写入证据。

        单条失败不影响整批；重复记录计入 skipped，便于核对接入结果。
        """

        evidences = list(evidences)

        conn = self._connect()
        inserted = 0
        skipped = 0
        errors = []
        records = []

        try:
            for index, evidence in enumerate(evidences):
                try:
                    result = self.insert_evidence(
                        workspace_id,
                        source_id,
                        evidence,
                        conn=conn,
                    )
                    if result["inserted"]:
                        inserted += 1
                    else:
                        skipped += 1

                    # 兼容旧调用方：保留计数，同时按输入顺序返回每条证据的 ID，
                    # 供查询层把内存 hit 映射回统一证据记录。
                    evidence.evidence_id = result["evidence_id"]
                    records.append({
                        "index": index,
                        "record_id": evidence.record_id,
                        "evidence_id": result["evidence_id"],
                        "inserted": bool(result["inserted"]),
                    })
                except (
                    sqlite3.IntegrityError,
                    ValueError,
                    TypeError,
                ) as exc:
                    errors.append({
                        "row": index + 1,
                        "error": str(exc),
                    })
                    records.append({
                        "index": index,
                        "record_id": evidence.record_id,
                        "evidence_id": None,
                        "inserted": False,
                        "error": str(exc),
                    })

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

        return {
            "total": len(evidences),
            "inserted": inserted,
            "skipped": skipped,
            "failed": len(errors),
            "errors": errors,
            "records": records,
        }

    # ------------------------------------------------------------------
    # 证据查询
    # ------------------------------------------------------------------
    def get_evidence(
        self,
        workspace_id: str,
        evidence_id: str,
    ) -> Optional[Evidence]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM evidence_records
                WHERE workspace_id = ?
                  AND id = ?
                """,
                (workspace_id, evidence_id),
            ).fetchone()

        return _row_to_evidence(row) if row else None

    def find_candidates(
        self,
        workspace_id: str,
        anchor_filter: dict,
        limit: int = 200,
        exclude_evidence_id: Optional[str] = None,
    ) -> list[Evidence]:
        """按关联键做粗筛。

        anchor_filter 来自 core.correlation.candidate_filter：
        包含 trace_id / request_id / uid / error_code / service / start / end。

        粗筛分两类条件：
        - 强标识（Trace / Request / UID）可以独立命中，不受时间窗口限制。
        - 错误码与服务名必须落在时间窗口内，避免把整个服务的日志全量捞出来。
        """

        conditions = ["workspace_id = ?"]
        parameters: list[Any] = [workspace_id]

        identity_conditions = []

        for key in ("trace_id", "request_id", "uid"):
            value = anchor_filter.get(key)
            if value:
                identity_conditions.append(f"{key} = ?")
                parameters.append(value)

        start = anchor_filter.get("start")
        end = anchor_filter.get("end")
        error_code = anchor_filter.get("error_code")
        service = anchor_filter.get("service")

        window_conditions = []

        if start and end and (error_code or service):
            weak_keys = []
            weak_params = [start, end]

            if error_code:
                weak_keys.append("error_code = ?")
                weak_params.append(error_code)

            if service:
                weak_keys.append("service = ?")
                weak_params.append(service)

            window_conditions.append(
                "(timestamp BETWEEN ? AND ?) AND ("
                + " OR ".join(weak_keys)
                + ")"
            )
            parameters.extend(weak_params)

        all_conditions = identity_conditions + window_conditions

        if not all_conditions:
            return []

        conditions.append("(" + " OR ".join(all_conditions) + ")")

        if exclude_evidence_id:
            conditions.append("id != ?")
            parameters.append(exclude_evidence_id)

        limit = min(max(int(limit), 1), 500)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM evidence_records
                WHERE {' AND '.join(conditions)}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (*parameters, limit),
            ).fetchall()

        return [_row_to_evidence(row) for row in rows]

    def list_evidence(
        self,
        workspace_id: str,
        evidence_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Evidence]:
        conditions = ["workspace_id = ?"]
        parameters: list[Any] = [workspace_id]

        if evidence_type:
            conditions.append("evidence_type = ?")
            parameters.append(evidence_type)

        limit = min(max(int(limit), 1), 500)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM evidence_records
                WHERE {' AND '.join(conditions)}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
                """,
                (*parameters, limit, offset),
            ).fetchall()

        return [_row_to_evidence(row) for row in rows]

    def query_evidence(
        self,
        workspace_id: str,
        *,
        evidence_types: Optional[Iterable[str]] = None,
        service: Optional[str] = None,
        uid: Optional[str] = None,
        error_code: Optional[str] = None,
        trace_id: Optional[str] = None,
        request_id: Optional[str] = None,
        titles: Optional[Iterable[str]] = None,
        keyword: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 200,
    ) -> list[Evidence]:
        """按场景条件查询证据，供故障场景模型和多系统检索使用。"""

        conditions = ["workspace_id = ?"]
        parameters: list[Any] = [workspace_id]

        type_values = [
            str(item).strip()
            for item in (evidence_types or [])
            if str(item).strip()
        ]
        if type_values:
            conditions.append(
                "evidence_type IN (" + ",".join("?" for _ in type_values) + ")"
            )
            parameters.extend(type_values)

        for name, value in (
            ("service", service),
            ("uid", uid),
            ("error_code", error_code),
            ("trace_id", trace_id),
            ("request_id", request_id),
        ):
            if value:
                conditions.append(f"{name} = ?")
                parameters.append(str(value))

        title_values = [
            str(item).strip() for item in (titles or []) if str(item).strip()
        ]
        if title_values:
            conditions.append(
                "title IN (" + ",".join("?" for _ in title_values) + ")"
            )
            parameters.extend(title_values)

        if keyword:
            keyword_like = f"%{str(keyword).strip()}%"
            conditions.append(
                "(message LIKE ? OR title LIKE ? OR error_code LIKE ? OR service LIKE ?)"
            )
            parameters.extend([
                keyword_like, keyword_like, keyword_like, keyword_like
            ])

        if start:
            conditions.append("timestamp >= ?")
            parameters.append(str(start))

        if end:
            conditions.append("timestamp <= ?")
            parameters.append(str(end))

        limit = min(max(int(limit), 1), 500)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM evidence_records
                WHERE {' AND '.join(conditions)}
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (*parameters, limit),
            ).fetchall()

        return [_row_to_evidence(row) for row in rows]

    def get_source(
        self,
        workspace_id: str,
        source_id: str,
    ) -> Optional[dict]:
        """读取证据数据源信息，供原始证据追溯展示来源。"""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, name, source_type, connector_type, status
                FROM data_sources
                WHERE workspace_id = ?
                  AND id = ?
                LIMIT 1
                """,
                (workspace_id, source_id),
            ).fetchone()

        return dict(row) if row else None

    def count_evidence(
        self,
        workspace_id: str,
        evidence_type: Optional[str] = None,
    ) -> int:
        conditions = ["workspace_id = ?"]
        parameters: list[Any] = [workspace_id]

        if evidence_type:
            conditions.append("evidence_type = ?")
            parameters.append(evidence_type)

        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM evidence_records
                WHERE {' AND '.join(conditions)}
                """,
                parameters,
            ).fetchone()

        return row[0] if row else 0

    # ------------------------------------------------------------------
    # 证据关联
    # ------------------------------------------------------------------
    def replace_links(
        self,
        workspace_id: str,
        anchor_type: str,
        anchor_id: str,
        links: Iterable[dict],
        relation: Optional[str] = "correlates",
    ) -> int:
        """重建锚点的证据关联，返回写入条数。

        links 中每项包含：
        evidence_id、score、strength、matched_keys、reasons，
        可选 relation（缺省用参数 relation）。
        relation 传 None 时重建该锚点的全部关联（含 context 等混合关系）。
        """

        if anchor_type not in ANCHOR_TYPES:
            raise ValueError(f"不支持的锚点类型：{anchor_type}")

        if relation is not None and relation not in RELATIONS:
            raise ValueError(f"不支持的关联类型：{relation}")

        links = list(links)

        conn = self._connect()

        try:
            if relation is None:
                conn.execute(
                    """
                    DELETE FROM evidence_links
                    WHERE workspace_id = ?
                      AND anchor_type = ?
                      AND anchor_id = ?
                    """,
                    (workspace_id, anchor_type, anchor_id),
                )
            else:
                conn.execute(
                    """
                    DELETE FROM evidence_links
                    WHERE workspace_id = ?
                      AND anchor_type = ?
                      AND anchor_id = ?
                      AND relation = ?
                    """,
                    (workspace_id, anchor_type, anchor_id, relation),
                )

            now = utc_now()
            written = 0

            for link in links:
                evidence_id = link.get("evidence_id")
                if not evidence_id:
                    continue

                link_relation = link.get("relation") or relation or "correlates"
                if link_relation not in RELATIONS:
                    raise ValueError(f"不支持的关联类型：{link_relation}")

                conn.execute(
                    """
                    INSERT INTO evidence_links
                    (
                        id, workspace_id, anchor_type, anchor_id,
                        evidence_id, relation, score, strength,
                        matched_keys_json, reasons_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generate_id(),
                        workspace_id,
                        anchor_type,
                        anchor_id,
                        evidence_id,
                        link_relation,
                        float(link.get("score", 0)),
                        link.get("strength", "weak"),
                        json_dumps(link.get("matched_keys", [])),
                        json_dumps(link.get("reasons", [])),
                        now,
                        now,
                    ),
                )
                written += 1

            conn.commit()

            return written

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

    def merge_links(
        self,
        workspace_id: str,
        anchor_type: str,
        anchor_id: str,
        links: Iterable[dict],
        relation: Optional[str] = "correlates",
    ) -> int:
        """增量写入证据关联，不删除该锚点已有的历史关联。

        与 replace_links 的区别：
        - 已存在的同 (evidence_id, relation) 关联只更新分数、强度与理由；
        - 未出现在本次结果中的历史关联保留，避免重复诊断静默覆盖历史。
        """

        if anchor_type not in ANCHOR_TYPES:
            raise ValueError(f"不支持的锚点类型：{anchor_type}")

        if relation is not None and relation not in RELATIONS:
            raise ValueError(f"不支持的关联类型：{relation}")

        links = list(links)
        conn = self._connect()

        try:
            now = utc_now()
            written = 0

            for link in links:
                evidence_id = link.get("evidence_id")
                if not evidence_id:
                    continue

                link_relation = link.get("relation") or relation or "correlates"
                if link_relation not in RELATIONS:
                    raise ValueError(f"不支持的关联类型：{link_relation}")

                conn.execute(
                    """
                    INSERT INTO evidence_links
                    (
                        id, workspace_id, anchor_type, anchor_id,
                        evidence_id, relation, score, strength,
                        matched_keys_json, reasons_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (
                        workspace_id, anchor_type, anchor_id,
                        evidence_id, relation
                    )
                    DO UPDATE SET
                        score = excluded.score,
                        strength = excluded.strength,
                        matched_keys_json = excluded.matched_keys_json,
                        reasons_json = excluded.reasons_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        generate_id(),
                        workspace_id,
                        anchor_type,
                        anchor_id,
                        evidence_id,
                        link_relation,
                        float(link.get("score", 0)),
                        link.get("strength", "weak"),
                        json_dumps(link.get("matched_keys", [])),
                        json_dumps(link.get("reasons", [])),
                        now,
                        now,
                    ),
                )
                written += 1

            conn.commit()

            return written

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

    def list_links(
        self,
        workspace_id: str,
        anchor_type: str,
        anchor_id: str,
    ) -> list[dict]:
        """查询锚点上的全部关联，按分数倒序。"""


        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM evidence_links
                WHERE workspace_id = ?
                  AND anchor_type = ?
                  AND anchor_id = ?
                ORDER BY score DESC
                """,
                (workspace_id, anchor_type, anchor_id),
            ).fetchall()

        results = []
        for row in rows:
            item = dict(row)
            item["matched_keys"] = json.loads(
                item.pop("matched_keys_json") or "[]"
            )
            item["reasons"] = json.loads(
                item.pop("reasons_json") or "[]"
            )
            results.append(item)

        return results

    def list_linked_evidence(
        self,
        workspace_id: str,
        anchor_type: str,
        anchor_id: str,
    ) -> list[dict]:
        """关联明细：证据字段 + 关联分数/理由。"""


        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    e.*,
                    l.score AS link_score,
                    l.strength AS link_strength,
                    l.relation AS link_relation,
                    l.matched_keys_json AS link_matched_keys,
                    l.reasons_json AS link_reasons
                FROM evidence_links AS l
                JOIN evidence_records AS e
                  ON e.id = l.evidence_id
                 AND e.workspace_id = l.workspace_id
                WHERE l.workspace_id = ?
                  AND l.anchor_type = ?
                  AND l.anchor_id = ?
                ORDER BY l.score DESC, e.timestamp DESC
                """,
                (workspace_id, anchor_type, anchor_id),
            ).fetchall()

        results = []

        for row in rows:
            item = dict(row)
            item["matched_keys"] = json.loads(
                item.pop("link_matched_keys") or "[]"
            )
            item["reasons"] = json.loads(
                item.pop("link_reasons") or "[]"
            )
            item["evidence_id"] = item.pop("id")
            item["attributes"] = json.loads(
                item.pop("attributes_json") or "{}"
            )
            item["raw_data"] = json.loads(
                item.pop("raw_data") or "{}"
            )
            item.pop("content_hash", None)
            results.append(item)

        return results
