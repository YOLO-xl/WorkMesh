import sqlite3

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from database.connection import get_connection


SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

CURRENT_VERSION = 1


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


def initialize_database(
    db_path: Optional[str | Path] = None,
) -> int:
    """初始化数据库并执行必要的 Schema 迁移。"""

    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Schema 文件不存在：{SCHEMA_PATH}"
        )

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    conn = get_connection(db_path)

    try:
        current_version = get_current_version(conn)

        if current_version >= CURRENT_VERSION:
            return current_version

        if current_version != 0:
            raise RuntimeError(
                f"不支持的数据库迁移版本：{current_version}"
            )

        applied_at = (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )

        try:
            # Schema 和迁移记录处于同一个事务中。
            conn.executescript(
                "BEGIN IMMEDIATE;\n" + schema_sql
            )

            conn.execute("""
                INSERT INTO schema_migrations
                (version, description, applied_at)
                VALUES (?, ?, ?)
            """, (
                CURRENT_VERSION,
                "Initial database schema",
                applied_at,
            ))

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        return CURRENT_VERSION

    finally:
        conn.close()
