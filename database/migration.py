"""WorkMesh 数据库初始化与版本化迁移。

约定：
- schema.sql 是 v1 基线结构，仅在空库时执行。
- 后续结构变更放在 database/migrations/ 下，按版本号顺序执行。
- 每一步迁移与版本记录处于同一个事务，失败即回滚，避免半迁移状态。
- 数据库版本高于代码版本时直接报错，防止旧代码写坏新结构。
"""

import sqlite3

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from database.connection import get_connection


SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

BASE_VERSION = 1


@dataclass(frozen=True)
class MigrationStep:
    version: int
    description: str
    file_name: str


MIGRATION_STEPS: tuple[MigrationStep, ...] = (
    MigrationStep(
        version=2,
        description="统一证据模型与证据关联表",
        file_name="002_evidence_model.sql",
    ),
    MigrationStep(
        version=3,
        description="诊断报告沉淀表",
        file_name="003_diagnosis_reports.sql",
    ),
)

CURRENT_VERSION = max(
    [BASE_VERSION] + [step.version for step in MIGRATION_STEPS]
)


def get_current_version(conn: sqlite3.Connection) -> int:
    """获取当前数据库结构版本。"""

    table = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'schema_migrations'
    """).fetchone()

    if table is None:
        return 0

    row = conn.execute("""
        SELECT MAX(version)
        FROM schema_migrations
    """).fetchone()

    return row[0] or 0


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _apply_script(
    conn: sqlite3.Connection,
    sql: str,
    version: int,
    description: str,
) -> None:
    """执行一段迁移 SQL 并写入版本记录。"""

    applied_at = _utc_now()

    try:
        # Schema 和迁移记录处于同一个事务中。
        conn.executescript("BEGIN IMMEDIATE;\n" + sql)

        conn.execute(
            """
            INSERT INTO schema_migrations
            (version, description, applied_at)
            VALUES (?, ?, ?)
            """,
            (version, description, applied_at),
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise


def initialize_database(
    db_path: Optional[str | Path] = None,
) -> int:
    """初始化数据库并执行必要的 Schema 迁移，返回最终版本号。"""

    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Schema 文件不存在：{SCHEMA_PATH}"
        )

    conn = get_connection(db_path)

    try:
        current_version = get_current_version(conn)

        if current_version > CURRENT_VERSION:
            raise RuntimeError(
                f"数据库版本（{current_version}）高于当前代码支持版本"
                f"（{CURRENT_VERSION}），请升级代码或恢复备份"
            )

        if current_version == 0:
            schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
            _apply_script(
                conn,
                schema_sql,
                BASE_VERSION,
                "Initial database schema",
            )
            current_version = BASE_VERSION

        for step in MIGRATION_STEPS:
            if step.version <= current_version:
                continue

            if step.version != current_version + 1:
                raise RuntimeError(
                    f"迁移版本不连续：当前 {current_version}，"
                    f"下一个版本为 {step.version}"
                )

            migration_path = MIGRATIONS_DIR / step.file_name
            if not migration_path.exists():
                raise FileNotFoundError(
                    f"迁移文件不存在：{migration_path}"
                )

            _apply_script(
                conn,
                migration_path.read_text(encoding="utf-8"),
                step.version,
                step.description,
            )
            current_version = step.version

        return current_version

    finally:
        conn.close()
