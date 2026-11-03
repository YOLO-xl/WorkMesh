import sqlite3
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "workmesh.db"


def get_connection(
    db_path: Optional[str | Path] = None,
) -> sqlite3.Connection:
    """
    创建 WorkMesh SQLite 数据库连接。

    - 自动创建数据库目录
    - 启用外键约束
    - 设置连接超时
    - 使用 Row 作为查询结果
    """

    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(path),
        timeout=5.0,
    )

    conn.row_factory = sqlite3.Row

    # 必须在每个连接上单独启用
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")

    # WAL 是数据库级配置，不需要每次连接都重新设置
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")

    return conn


def check_connection(
    conn: sqlite3.Connection,
) -> bool:
    """检查数据库连接是否正常。"""

    result = conn.execute("SELECT 1").fetchone()
    return result is not None and result[0] == 1
