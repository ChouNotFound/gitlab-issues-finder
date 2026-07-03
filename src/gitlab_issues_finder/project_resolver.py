"""Project name resolver: wraps storage cache + GitLab API.

公开 ``resolve(db_path, gl, project_ids)`` 接口：
  - 已缓存且未超过 TTL（默认 7 天）→ 直接返回
  - 未缓存或已过期 → 调 GitLab /projects 拉取，写缓存，返回
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from collections.abc import Iterable

import gitlab

from gitlab_issues_finder import queries, storage

# 缓存项视为过期的秒数：默认 7 天
DEFAULT_TTL_SECONDS = 86400 * 7


def _row_age_seconds(fetched_at: str) -> float:
    """行 fetched_at 到当前的秒数。解析失败返回 +inf。"""
    try:
        when = _dt.datetime.fromisoformat(fetched_at)
        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        return (_dt.datetime.now(_dt.timezone.utc) - when).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def _fetch_with_freshness(db_path: str) -> dict[int, tuple[dict, float]]:
    """从 SQLite 拿全部缓存 + 年龄。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT project_id, name, path_with_namespace, fetched_at "
            "FROM project_cache"
        ).fetchall()
    finally:
        conn.close()
    out: dict[int, tuple[dict, float]] = {}
    for r in rows:
        info = {"name": r["name"], "path_with_namespace": r["path_with_namespace"]}
        out[r["project_id"]] = (info, _row_age_seconds(r["fetched_at"]))
    return out


def resolve(
    db_path: str,
    gl: gitlab.Gitlab,
    project_ids: Iterable[int],
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[int, dict]:
    """返回 {project_id: {name, path_with_namespace}}。

    实现要点：
      - 一次性从 SQLite 读出全部缓存行（含 fetched_at）。
      - 已缓存且年龄 < TTL → 直接返回。
      - 未缓存或过期的 ID → 调 GitLab /projects 拉取，写回缓存，返回。
    """
    wanted = {int(pid) for pid in project_ids}
    if not wanted:
        return {}

    cache = _fetch_with_freshness(db_path)
    out: dict[int, dict] = {}
    missing: set[int] = set()
    for pid in wanted:
        entry = cache.get(pid)
        if entry is not None and entry[1] < ttl_seconds:
            out[pid] = entry[0]
        else:
            missing.add(pid)

    if missing:
        fetched = queries.resolve_projects(gl, missing)
        for pid, info in fetched.items():
            storage.upsert_project(db_path, pid, info["name"], info["path_with_namespace"])
            out[pid] = info
        # 没拉到的（例如私有项目 / API 失败）静默忽略，调用方按 ID 渲染

    return out
