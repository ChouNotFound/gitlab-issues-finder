"""SQLite 持久化层。

存储看板拖拽覆盖、列配置、用户偏好。零外部依赖，path 由 .env 配置项
DB_PATH 指定，默认 ``./data/app.db``。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# ----- Schema -----
SCHEMA = """
CREATE TABLE IF NOT EXISTS board_overrides (
    username    TEXT NOT NULL,
    item_key    TEXT NOT NULL,  -- "{type}-{project_id}-{iid}"
    column_id   TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (username, item_key)
);

CREATE TABLE IF NOT EXISTS board_columns (
    username    TEXT NOT NULL,
    column_id   TEXT NOT NULL,
    title       TEXT NOT NULL,
    sort_index  INTEGER NOT NULL,
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (username, column_id)
);

CREATE TABLE IF NOT EXISTS user_prefs (
    username    TEXT PRIMARY KEY,
    theme       TEXT NOT NULL DEFAULT 'auto',  -- auto|light|dark
    last_seen   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_cache (
    project_id          INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    path_with_namespace TEXT NOT NULL,
    fetched_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> None:
    """初始化数据库（创建表）。幂等。"""
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def get_conn(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ----- Board override (拖拽覆盖) -----
def set_override(db_path: str | Path, username: str, item_key: str, column_id: str) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO board_overrides(username, item_key, column_id)
               VALUES (?, ?, ?)
               ON CONFLICT(username, item_key) DO UPDATE SET
                   column_id = excluded.column_id,
                   updated_at = CURRENT_TIMESTAMP""",
            (username, item_key, column_id),
        )


def get_overrides(db_path: str | Path, username: str) -> dict[str, str]:
    """返回 {item_key: column_id}。"""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT item_key, column_id FROM board_overrides WHERE username = ?",
            (username,),
        ).fetchall()
    return {r["item_key"]: r["column_id"] for r in rows}


def clear_overrides(db_path: str | Path, username: str) -> None:
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM board_overrides WHERE username = ?", (username,))


# ----- Board columns -----
# 内置默认列：搜索/看板视图首次打开某用户时按此初始化
DEFAULT_BUILTIN_COLUMNS: list[tuple[str, str]] = [
    ("reviewer", "需我审查"),
    ("assignee", "需我动"),
    ("mention", "@我的"),
    ("author", "我创建的"),
    ("other", "其他参与"),
]


def _ensure_builtin_columns(db_path: str | Path, username: str) -> None:
    """首次访问某用户时建立默认列。"""
    with get_conn(db_path) as conn:
        existing = conn.execute(
            "SELECT 1 FROM board_columns WHERE username = ? LIMIT 1",
            (username,),
        ).fetchone()
        if existing:
            return
        for idx, (cid, title) in enumerate(DEFAULT_BUILTIN_COLUMNS):
            conn.execute(
                "INSERT INTO board_columns(username, column_id, title, sort_index, is_builtin) "
                "VALUES (?, ?, ?, ?, 1)",
                (username, cid, title, idx),
            )


def list_columns(db_path: str | Path, username: str) -> list[dict]:
    _ensure_builtin_columns(db_path, username)
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT column_id, title, sort_index, is_builtin FROM board_columns "
            "WHERE username = ? ORDER BY sort_index",
            (username,),
        ).fetchall()
    return [
        {
            "id": r["column_id"],
            "title": r["title"],
            "sort_index": r["sort_index"],
            "is_builtin": bool(r["is_builtin"]),
        }
        for r in rows
    ]


def add_column(db_path: str | Path, username: str, column_id: str, title: str) -> dict:
    """追加自定义列。column_id 由调用方保证唯一。"""
    with get_conn(db_path) as conn:
        max_idx = conn.execute(
            "SELECT COALESCE(MAX(sort_index), -1) FROM board_columns WHERE username = ?",
            (username,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO board_columns(username, column_id, title, sort_index, is_builtin) "
            "VALUES (?, ?, ?, ?, 0)",
            (username, column_id, title, max_idx + 1),
        )
    return {"id": column_id, "title": title, "sort_index": max_idx + 1, "is_builtin": False}


def rename_column(db_path: str | Path, username: str, column_id: str, title: str) -> bool:
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE board_columns SET title = ? WHERE username = ? AND column_id = ?",
            (title, username, column_id),
        )
    return cur.rowcount > 0


def delete_column(db_path: str | Path, username: str, column_id: str) -> bool:
    """删除自定义列（不允许删除内置列）。

    同时清理属于该列的 overrides。
    """
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT is_builtin FROM board_columns WHERE username = ? AND column_id = ?",
            (username, column_id),
        ).fetchone()
        if not row or row["is_builtin"]:
            return False
        conn.execute(
            "DELETE FROM board_columns WHERE username = ? AND column_id = ?", (username, column_id)
        )
        conn.execute(
            "DELETE FROM board_overrides WHERE username = ? AND column_id = ?",
            (username, column_id),
        )
    return True


# ----- User preferences -----
def get_theme(db_path: str | Path, username: str) -> str:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT theme FROM user_prefs WHERE username = ?", (username,)
        ).fetchone()
    return row["theme"] if row else "auto"


def set_theme(db_path: str | Path, username: str, theme: str) -> None:
    if theme not in ("auto", "light", "dark"):
        raise ValueError(f"invalid theme: {theme}")
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO user_prefs(username, theme, last_seen)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(username) DO UPDATE SET
                   theme = excluded.theme,
                   last_seen = CURRENT_TIMESTAMP""",
            (username, theme),
        )


def touch_user(db_path: str | Path, username: str) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO user_prefs(username, theme, last_seen)
               VALUES (?, 'auto', CURRENT_TIMESTAMP)
               ON CONFLICT(username) DO UPDATE SET last_seen = CURRENT_TIMESTAMP""",
            (username,),
        )


def list_recent_users(db_path: str | Path, limit: int = 10) -> list[str]:
    """按 last_seen 倒序返回最近用户列表。

    主排序键为 last_seen，rowid 作为稳定 tiebreaker（保证同秒内顺序稳定）。
    """
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT username FROM user_prefs ORDER BY last_seen DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [r["username"] for r in rows]



# ----- Project name cache -----
def get_project(db_path: str | Path, project_id: int) -> dict | None:
    """返回 {name, path_with_namespace} 或 None（未缓存）。"""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT name, path_with_namespace, fetched_at FROM project_cache WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    if not row:
        return None
    return {"name": row["name"], "path_with_namespace": row["path_with_namespace"]}


def upsert_project(
    db_path: str | Path,
    project_id: int,
    name: str,
    path_with_namespace: str,
) -> None:
    """写入或更新项目缓存。"""
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO project_cache(project_id, name, path_with_namespace)
               VALUES (?, ?, ?)
               ON CONFLICT(project_id) DO UPDATE SET
                   name = excluded.name,
                   path_with_namespace = excluded.path_with_namespace,
                   fetched_at = CURRENT_TIMESTAMP""",
            (project_id, name, path_with_namespace),
        )


def stale_projects(db_path: str | Path, max_age_seconds: int = 86400 * 7) -> list[int]:
    """返回超过 max_age_seconds 未刷新的 project_id 列表（默认 7 天）。

    用于后台清理 / 按需重新拉取。
    """
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT project_id FROM project_cache WHERE "
            "(julianday('now') - julianday(fetched_at)) * 86400 > ?",
            (max_age_seconds,),
        ).fetchall()
    return [r["project_id"] for r in rows]
