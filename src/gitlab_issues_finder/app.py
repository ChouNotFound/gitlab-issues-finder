"""FastAPI 应用入口。

路由：
  GET  /              首页 / 看板入口选择
  POST /search        查询结果（双表格）
  GET  /board         MR + Issue 看板视图

JSON API：
  GET  /api/users             活跃用户列表（自动补全用）
  GET  /api/recent-users      最近使用过的用户名
  POST /api/board/move        拖拽覆盖持久化
  POST /api/board/reset       重置拖拽覆盖
  POST /api/board/columns     新增列
  PATCH /api/board/columns/{cid}  重命名列
  DELETE /api/board/columns/{cid} 删除列
  POST /api/preferences       主题切换

异常：所有 AppError 子类统一渲染为 error.html。
"""

from __future__ import annotations

import contextlib
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import gitlab
from fastapi import Body, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from gitlab_issues_finder import storage
from gitlab_issues_finder.client import build_client
from gitlab_issues_finder.config import AppConfig
from gitlab_issues_finder.errors import AppError, AuthError, ConfigError
from gitlab_issues_finder.logging_setup import get_logger
from gitlab_issues_finder.metrics import get_metrics
from gitlab_issues_finder.middleware import RequestIDMiddleware, SecurityHeadersMiddleware
from gitlab_issues_finder.models import IssueRef
from gitlab_issues_finder.project_resolver import resolve as resolve_projects
from gitlab_issues_finder.queries import (
    EXTRA_REACTION,
    EXTRA_SUBSCRIBED,
    REACTION_EMOJI_DEFAULT,
    ItemKind,
    Relation,
    dedupe,
    fetch_items,
    fetch_items_by_user_id,
    fetch_labeled,
    fetch_reacted,
    fetch_subscribed,
    fetch_users,
    resolve_user_ids,
)
from gitlab_issues_finder.rate_limit import get_default_limiter

logger = get_logger(__name__)

_START_TIME = time.time()

# ----- 路径解析 -----
_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"


# ----- App 生命周期：初始化 SQLite -----
# FastAPI >=0.110 推荐用 lifespan context manager 替换 @app.on_event。
# 配置缺失时启动不抛错：首次访问 /search 时再让用户看到错误页。
@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # 启动时初始化 SQLite schema。配置缺失时静默忽略：首次访问 /search 时再让用户看到错误页。
    try:
        cfg = AppConfig.from_env()
        storage.init_db(cfg.db_path)
        logger.info("startup ok", extra={"db_path": cfg.db_path, "gitlab_url": cfg.url})
    except ConfigError as e:
        logger.warning("startup config incomplete: %s", e)
    yield
    logger.info("shutdown")


app = FastAPI(
    title="GitLab Status Board",
    description="Personal tool for a self-hosted GitLab: pull all issues and merge requests related to a username across the assignee / mention / author / reviewer dimensions, render as a Kanban board, export to CSV / Markdown.",
    lifespan=_lifespan,
    openapi_tags=[
        {"name": "UI", "description": "Server-rendered HTML pages."},
        {
            "name": "Board",
            "description": "Kanban board state management (drag overrides, columns).",
        },
        {"name": "Users", "description": "User listing and per-user preferences."},
        {"name": "Export", "description": "One-shot data export in CSV / Markdown."},
        {"name": "System", "description": "Version, health, and diagnostics."},
    ],
)


class _RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP token bucket. Returns 429 + Retry-After when exhausted."""

    async def dispatch(self, request, call_next):
        limiter = get_default_limiter()
        if limiter.per_minute <= 0:
            return await call_next(request)
        # Use X-Forwarded-For first hop if present, else client.host
        xff = request.headers.get("x-forwarded-for")
        ip = (
            xff.split(",")[0].strip()
            if xff
            else (request.client.host if request.client else "anon")
        )
        if not limiter.hit(ip):
            retry_after = max(1, int(60 / max(limiter.per_minute, 1)))
            return JSONResponse(
                {"detail": "rate limit exceeded", "retry_after_seconds": retry_after},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)


app.add_middleware(_RateLimitMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ----- 工具函数 -----
def _db_path() -> str:
    """从环境取 DB_PATH。

    与 AppConfig 解耦：DB_PATH 不依赖 GITLAB_URL/TOKEN，
    即使 GitLab 配置缺失也能读写本地看板状态。
    """
    return os.environ.get("DB_PATH", "data/app.db")


_SINCE_UNTIL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date(raw: str) -> str | None:
    """验证 YYYY-MM-DD 格式。返回原串或 None。"""
    if not raw:
        return None
    if not _SINCE_UNTIL_RE.match(raw):
        return None
    # 简单合法性检查（拒绝 2025-02-30 之类）
    import datetime as _dt

    try:
        _dt.date.fromisoformat(raw)
    except ValueError:
        return None
    return raw


def _filter_by_time(items, since: str | None, until: str | None):
    """按 updated_at[:10] in [since, until] 过滤（闭区间）。"""
    out = list(items)
    if since:
        out = [it for it in out if it.updated_at[:10] >= since]
    if until:
        out = [it for it in out if it.updated_at[:10] <= until]
    return out


def _validate_username(raw: str) -> str | None:
    raw = raw.strip()
    if not raw or len(raw) > 255:
        return None
    # GitLab username 限制：字母数字 + _-. + @.+_
    if not re.match(r"^[\w.\-@]+$", raw):
        return None
    return raw


# ----- 维度 / 统计常量 -----
# 综述 stat row 顺序与图标。键名 = key_to_reasons 中的 reason 字符串。
ISSUE_STAT_KEYS: list[tuple[str, str, str]] = [
    ("assignee", "指派给我", "🟦"),
    ("mention", "@我", "💬"),
    ("author", "我创建", "✏️"),
    (EXTRA_SUBSCRIBED, "我订阅", "🔔"),
    (EXTRA_REACTION, "我反应", "👍"),
]
MR_STAT_KEYS: list[tuple[str, str, str]] = [
    ("assignee", "指派给我", "🟦"),
    ("mention", "@我", "💬"),
    ("author", "我创建", "✏️"),
    ("reviewer", "审查我", "👀"),
    (EXTRA_SUBSCRIBED, "我订阅", "🔔"),
    (EXTRA_REACTION, "我反应", "👍"),
]


# ----- 数据装配 helper -----
def _load_user_items(
    gl,
    username: str,
    page_size: int,
    label_list: list[str] | None = None,
    reaction_emoji: str = REACTION_EMOJI_DEFAULT,
):
    """一站式拉取与 username 相关的所有 items 及 reason 标签。

    覆盖维度：
      1. 标准 4 类关系 (issue: 3, MR: 4) —— 按 username
      2. 多 assignee / 多 reviewer —— 按 user_id (补齐 GitLab 13+ 场景)
      3. subscribed (Token 持有者)
      4. reaction (Token 持有者，可指定 emoji)
      5. labels AND 过滤 (可选)

    返回 dict，键：
      - all_items: 去重后的全集 (包含 labels 命中的项)
      - issue_lists / mr_lists: 每个 relation 对应的列表
      - subscribed / reacted: 单独的列表
      - key_to_reasons: {(type, project_id, iid): [reason, ...]}
    """
    label_list = list(label_list or [])

    # ---- 1) username-based 拉取 ----
    issue_lists: dict[str, list[IssueRef]] = {
        rel.value: list(
            fetch_items(gl, username, rel, ItemKind.ISSUE, page_size)
        )
        for rel in (Relation.ASSIGNEE, Relation.MENTION, Relation.AUTHOR)
    }
    mr_lists: dict[str, list[IssueRef]] = {
        rel.value: list(
            fetch_items(gl, username, rel, ItemKind.MERGE_REQUEST, page_size)
        )
        for rel in (Relation.ASSIGNEE, Relation.MENTION, Relation.AUTHOR, Relation.REVIEWER)
    }

    # ---- 2) 多 assignee / reviewer (按 user_id) ----
    user_ids = resolve_user_ids(gl, username)
    if user_ids:
        issue_lists[Relation.ASSIGNEE.value] = dedupe(
            issue_lists[Relation.ASSIGNEE.value],
            fetch_items_by_user_id(
                gl, user_ids, Relation.ASSIGNEE, ItemKind.ISSUE, page_size
            ),
        )
        mr_lists[Relation.ASSIGNEE.value] = dedupe(
            mr_lists[Relation.ASSIGNEE.value],
            fetch_items_by_user_id(
                gl, user_ids, Relation.ASSIGNEE, ItemKind.MERGE_REQUEST, page_size
            ),
        )
        mr_lists[Relation.REVIEWER.value] = dedupe(
            mr_lists[Relation.REVIEWER.value],
            fetch_items_by_user_id(
                gl, user_ids, Relation.REVIEWER, ItemKind.MERGE_REQUEST, page_size
            ),
        )

    # ---- 3) subscribed ----
    subscribed: list[IssueRef] = dedupe(
        fetch_subscribed(gl, ItemKind.ISSUE, page_size),
        fetch_subscribed(gl, ItemKind.MERGE_REQUEST, page_size),
    )

    # ---- 4) reaction ----
    reacted: list[IssueRef] = dedupe(
        fetch_reacted(gl, reaction_emoji, ItemKind.ISSUE, page_size),
        fetch_reacted(gl, reaction_emoji, ItemKind.MERGE_REQUEST, page_size),
    )

    # ---- 5) labels (AND 关系) ----
    issues_labeled: list[IssueRef] = []
    mrs_labeled: list[IssueRef] = []
    if label_list:
        issues_labeled = fetch_labeled(gl, label_list, page_size)
        mrs_labeled = fetch_labeled(
            gl, label_list, page_size, kind=ItemKind.MERGE_REQUEST
        )

    # ---- 组装 key_to_reasons ----
    key_to_reasons: dict[tuple[str, int, int], list[str]] = {}
    for reason, lst in {**issue_lists, **mr_lists}.items():
        for it in lst:
            key_to_reasons.setdefault(it.key, []).append(reason)
    for it in subscribed:
        key_to_reasons.setdefault(it.key, []).append(EXTRA_SUBSCRIBED)
    for it in reacted:
        key_to_reasons.setdefault(it.key, []).append(EXTRA_REACTION)
    if label_list:
        for it in issues_labeled:
            key_to_reasons.setdefault(it.key, []).append("label")
        for it in mrs_labeled:
            key_to_reasons.setdefault(it.key, []).append("label")

    # ---- 合并并去重 all_items ----
    all_items = dedupe(
        *issue_lists.values(),
        *mr_lists.values(),
        subscribed,
        reacted,
        issues_labeled,
        mrs_labeled,
    )

    return {
        "all_items": all_items,
        "issue_lists": issue_lists,
        "mr_lists": mr_lists,
        "subscribed": subscribed,
        "reacted": reacted,
        "key_to_reasons": key_to_reasons,
    }


# ----- HTML 路由 -----
@app.get("/", response_class=HTMLResponse, tags=["UI"])
async def index(request: Request) -> HTMLResponse:
    """首页：选择用户名（带 datalist 自动补全 + 最近使用）。"""
    cfg = _try_cfg()
    recent: list[str] = []
    if cfg:
        try:
            recent = storage.list_recent_users(cfg.db_path, limit=8)
        except Exception:
            recent = []
    return templates.TemplateResponse(
        request,
        "index.html",
        {"active_tab": "home", "recent_users": recent},
    )


@app.post("/search", response_class=HTMLResponse, tags=["UI"])
async def search(
    request: Request,
    username: str = Form(...),
    labels: str = Form(""),
    since: str = Form(""),
    until: str = Form(""),
) -> HTMLResponse:
    """查询与 username 相关的所有 opened issue / MR 并按类型分两段展示。

    参与维度（任一命中即算参与）：
      - assignee_username / mention_username / author_username；
      - MR 额外覆盖 reviewer_username；
      - labels 是可选附加条件（AND 多标签）。
    """
    username = username.strip()
    if not username:
        return _render_error(request, "请输入有效的 GitLab 用户名", "输入为空")

    label_list = [s.strip() for s in labels.split(",") if s.strip()]
    # 时间范围参数先于 GitLab 调用验证（避免无效参数时仍然跑 7 个 API）
    s_date = _parse_date(since)
    u_date = _parse_date(until)
    if since and not s_date:
        return _render_error(request, "since 格式错误：应为 YYYY-MM-DD", "参数错误")
    if until and not u_date:
        return _render_error(request, "until 格式错误：应为 YYYY-MM-DD", "参数错误")
    logger.info(
        "search requested",
        extra={"username": username, "labels": label_list, "since": s_date, "until": u_date},
    )

    cfg = AppConfig.from_env()
    gl = build_client(cfg)

    loaded = _load_user_items(gl, username, cfg.page_size, label_list=label_list)
    all_items = _filter_by_time(loaded["all_items"], s_date, u_date)
    key_to_reasons = loaded["key_to_reasons"]

    # 解析项目名（用于模板显示）
    project_info: dict = {}
    try:
        dbp = _db_path()
        project_info = resolve_projects(dbp, gl, {it.project_id for it in all_items})
    except AppError:
        pass
    logger.info(
        "search result",
        extra={
            "username": username,
            "issue_count": sum(1 for it in all_items if it.type == "issue"),
            "mr_count": sum(1 for it in all_items if it.type == "merge_request"),
        },
    )
    issues = [it for it in all_items if it.type == "issue"]
    merge_requests = [it for it in all_items if it.type == "merge_request"]

    def compute_reasons(item: IssueRef) -> list[str]:
        return list(key_to_reasons.get(item.key, []))

    issues.sort(key=lambda it: it.updated_at, reverse=True)
    merge_requests.sort(key=lambda it: it.updated_at, reverse=True)
    issues_with_reasons = [(it, compute_reasons(it)) for it in issues]
    mrs_with_reasons = [(it, compute_reasons(it)) for it in merge_requests]

    # 更新最近使用
    with contextlib.suppress(Exception):
        storage.touch_user(cfg.db_path, username)

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "username": username,
            "labels": label_list,
            "issues": issues_with_reasons,
            "merge_requests": mrs_with_reasons,
            "issue_count": len(issues),
            "mr_count": len(merge_requests),
            "total_count": len(issues) + len(merge_requests),
            "active_tab": "query",
            "theme": storage.get_theme(cfg.db_path, username),
            "project_info": project_info,
        },
    )


@app.get("/board", response_class=HTMLResponse, tags=["UI"])
async def board(
    request: Request,
    username: str = Query("", alias="username"),
    q: str = Query("", description="搜索过滤"),
    view: str = Query("summary", description="视图模式: summary|all|issues|mrs|relation|project"),
    since: str = Query("", description="只看 updated_at >= 此日期 (YYYY-MM-DD)"),
    until: str = Query("", description="只看 updated_at <= 此日期 (YYYY-MM-DD)"),
) -> HTMLResponse:
    """Git 状态看板（控制台样式）。

    视图由 ``view`` 控制：
      - summary ：综述 + 维度 KPI（默认）
      - all     ：全部 issue + MR 单一表格
      - issues  ：仅 issue
      - mrs     ：仅 merge request
      - relation：按"与我关系"分桶（原 Kanban 五列），保留拖拽
      - project ：按 project_id 分组
    """
    username = username.strip()
    dbp = _db_path()
    if not username:
        return templates.TemplateResponse(
            request,
            "board.html",
            {
                "username": "",
                "columns": [],
                "items_by_col": {},
                "overrides": {},
                "all_items": [],
                "items_by_rel": {},
                "items_by_proj": {},
                "summary": _empty_summary(),
                "view": view,
                "active_tab": "board",
                "theme": "auto",
                "filter_q": "",
                "project_info": {},
                "ISSUE_STAT_KEYS": ISSUE_STAT_KEYS,
                "MR_STAT_KEYS": MR_STAT_KEYS,
            },
        )

    # 验证 view 参数
    allowed_views = {"summary", "all", "issues", "mrs", "relation", "project"}
    if view not in allowed_views:
        view = "summary"

    # 时间范围参数先验证
    s_date_b = _parse_date(since)
    u_date_b = _parse_date(until)
    if since and not s_date_b:
        return _render_error(request, "since 格式错误：应为 YYYY-MM-DD", "参数错误")
    if until and not u_date_b:
        return _render_error(request, "until 格式错误：应为 YYYY-MM-DD", "参数错误")

    cfg = AppConfig.from_env()
    gl = build_client(cfg)

    label_list = [s.strip() for s in q.split(",") if s.strip()] if q else []
    loaded = _load_user_items(gl, username, cfg.page_size, label_list=label_list)
    all_items = _filter_by_time(loaded["all_items"], s_date_b, u_date_b)
    key_to_reasons = loaded["key_to_reasons"]

    # 解析项目名（用于模板显示）
    board_project_info: dict = {}
    try:
        dbp = _db_path()
        board_project_info = resolve_projects(dbp, gl, {it.project_id for it in all_items})
    except AppError:
        pass
    logger.info(
        "board result",
        extra={
            "username": username,
            "view": view,
            "issue_count": sum(1 for it in all_items if it.type == "issue"),
            "mr_count": sum(1 for it in all_items if it.type == "merge_request"),
        },
    )

    # ---- 关键修复保证：搜索跨越多项目时不漏 ----
    # all_items 中保留的是 GitLab API 返回的实际项目 ID 集合
    # （with_membership=false 已生效），无需额外合并

    # ---- 按"关系"分桶（与原 Kanban 列对齐） ----
    # 这里用 key_to_reasons 判断 item 命中维度
    items_by_rel: dict[str, list[IssueRef]] = {
        "reviewer": [],
        "assignee": [],
        "mention": [],
        "author": [],
        "other": [],
    }
    for item in all_items:
        reasons = key_to_reasons.get(item.key, [])
        placed = False
        for rel in ("reviewer", "assignee", "mention", "author"):
            if rel in reasons:
                items_by_rel[rel].append(item)
                placed = True
                break
        if not placed:
            items_by_rel["other"].append(item)
    for lst in items_by_rel.values():
        lst.sort(key=lambda it: it.updated_at, reverse=True)

    # ---- 按项目分桶 ----
    items_by_proj: dict[int, list[IssueRef]] = {}
    for it in all_items:
        items_by_proj.setdefault(it.project_id, []).append(it)
    for lst in items_by_proj.values():
        lst.sort(key=lambda it: it.updated_at, reverse=True)

    # ---- 拖拽覆盖（按 col_id） ----
    overrides = storage.get_overrides(dbp, username)
    col_defs = storage.list_columns(dbp, username)
    items_by_col: dict[str, list[IssueRef]] = {c["id"]: [] for c in col_defs}
    for item in all_items:
        item_key = f"{item.type}-{item.project_id}-{item.iid}"
        if item_key in overrides:
            col_id = overrides[item_key]
            if col_id in items_by_col:
                items_by_col[col_id].append(item)
                continue
        # 默认分桶
        reasons = key_to_reasons.get(item.key, [])
        placed = False
        for reason in ("reviewer", "assignee", "mention", "author"):
            if reason in reasons and reason in items_by_col:
                items_by_col[reason].append(item)
                placed = True
                break
        if not placed and "other" in items_by_col:
            items_by_col["other"].append(item)
    for lst in items_by_col.values():
        lst.sort(key=lambda it: it.updated_at, reverse=True)
    total = sum(len(v) for v in items_by_col.values())

    # ---- 综述 ----
    summary = _compute_summary(all_items, key_to_reasons, items_by_proj)

    with contextlib.suppress(Exception):
        storage.touch_user(dbp, username)

    return templates.TemplateResponse(
        request,
        "board.html",
        {
            "username": username,
            "columns": col_defs,
            "items_by_col": items_by_col,
            "items_by_rel": items_by_rel,
            "items_by_proj": items_by_proj,
            "all_items": all_items,
            "overrides": overrides,
            "summary": summary,
            "total_count": total,
            "view": view,
            "active_tab": "board",
            "theme": storage.get_theme(dbp, username),
            "filter_q": q,
            "project_info": board_project_info,
            "ISSUE_STAT_KEYS": ISSUE_STAT_KEYS,
            "MR_STAT_KEYS": MR_STAT_KEYS,
        },
    )


def _empty_summary() -> dict:
    return {
        "total": 0,
        "issues": 0,
        "mrs": 0,
        "projects": 0,
        "by_relation": {"reviewer": 0, "assignee": 0, "mention": 0, "author": 0, "other": 0},
        "by_relation_counts": {
            "issues": {
                "assignee": 0, "mention": 0, "author": 0,
                EXTRA_SUBSCRIBED: 0, EXTRA_REACTION: 0,
            },
            "mrs": {
                "assignee": 0, "mention": 0, "author": 0, "reviewer": 0,
                EXTRA_SUBSCRIBED: 0, EXTRA_REACTION: 0,
            },
        },
        "by_project": [],
        "most_recent": None,
    }


def _count_by_relation(items, key_to_reasons, dimensions):
    counts = {d: 0 for d in dimensions}
    for it in items:
        reasons = set(key_to_reasons.get(it.key, []))
        for d in dimensions:
            if d in reasons:
                counts[d] += 1
    return counts


def _compute_summary(
    all_items: list[IssueRef],
    key_to_reasons: dict[tuple[str, int, int], list[str]],
    items_by_proj: dict[int, list[IssueRef]],
) -> dict:
    if not all_items:
        return _empty_summary()
    issues = [it for it in all_items if it.type == "issue"]
    mrs = [it for it in all_items if it.type == "merge_request"]
    by_rel = {rel: 0 for rel in ("reviewer", "assignee", "mention", "author", "other")}
    for it in all_items:
        rs = key_to_reasons.get(it.key, [])
        placed = False
        for rel in ("reviewer", "assignee", "mention", "author"):
            if rel in rs:
                by_rel[rel] += 1
                placed = True
                break
        if not placed:
            by_rel["other"] += 1
    by_proj = sorted(
        ({"project_id": pid, "count": len(lst)} for pid, lst in items_by_proj.items()),
        key=lambda d: d["count"],
        reverse=True,
    )[:10]
    most_recent = max(all_items, key=lambda it: it.updated_at)
    issue_dims = [k for k, _, _ in ISSUE_STAT_KEYS]
    mr_dims = [k for k, _, _ in MR_STAT_KEYS]
    by_relation_counts = {
        "issues": _count_by_relation(issues, key_to_reasons, issue_dims),
        "mrs": _count_by_relation(mrs, key_to_reasons, mr_dims),
    }
    return {
        "total": len(all_items),
        "issues": len(issues),
        "mrs": len(mrs),
        "projects": len(items_by_proj),
        "by_relation": by_rel,
        "by_relation_counts": by_relation_counts,
        "by_project": by_proj,
        "most_recent": most_recent,
    }


# ----- JSON API -----
@app.get("/api/me", tags=["Users"])
async def api_me() -> JSONResponse:
    """返回当前配置的 GitLab Token 所对应的用户信息。

    用法：部署完成后调用一次，确认 .env 里的 token 是哪个账号。
    也能用来核对 token 是否有 read_api scope。
    """
    cfg = AppConfig.from_env()
    gl = build_client(cfg)
    try:
        me: dict = gl.http_get("/user")  # type: ignore[assignment]
    except gitlab.exceptions.GitlabError as e:
        code = getattr(e, "response_code", None)
        raise AuthError(f"无法获取当前用户：{e} (HTTP {code or '?'})") from e
    return JSONResponse(
        {
            "id": me.get("id"),
            "username": me.get("username"),
            "name": me.get("name"),
            "email": me.get("email"),
            "state": me.get("state"),
            "avatar_url": me.get("avatar_url"),
            "web_url": me.get("web_url"),
        }
    )


@app.get("/api/users", tags=["Users"])
async def api_users() -> JSONResponse:
    """返回活跃用户列表（精简字段），供首页 datalist 自动补全使用。失败时静默返回空。"""
    try:
        cfg = AppConfig.from_env()
        gl = build_client(cfg)
        users = fetch_users(gl, page_size=100, max_total=200)
    except AppError:
        return JSONResponse({"users": []})
    items = [
        {"username": u.get("username", ""), "name": u.get("name", "")}
        for u in users
        if u.get("username")
    ]
    return JSONResponse({"users": items})


@app.get("/api/items", tags=["Users"])
async def api_items(
    username: str = Query(..., description="GitLab 用户名"),
    labels: str = Query("", description="逗号分隔的标签"),
    since: str = Query("", description="只看 updated_at >= 此日期 (YYYY-MM-DD)"),
    until: str = Query("", description="只看 updated_at <= 此日期 (YYYY-MM-DD)"),
    page_size: int = Query(0, ge=0, le=100, description="覆盖 PAGE_SIZE（1-100）。0 = 用配置默认"),
) -> JSONResponse:
    """JSON 版 /search：返回 username 的所有 opened item 列表。

    程序化消费者（CLI / 看板外部集成）应该用这个端点而不是
    解析 /search 的 HTML。响应结构与 /api/export.md 的列对齐：
      [{type, iid, project_id, title, state, web_url,
        labels, assignee, updated_at, reasons}, ...]
    """
    username = username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="missing username")
    s_date = _parse_date(since)
    u_date = _parse_date(until)
    if since and not s_date:
        raise HTTPException(status_code=400, detail="since format error: expected YYYY-MM-DD")
    if until and not u_date:
        raise HTTPException(status_code=400, detail="until format error: expected YYYY-MM-DD")
    label_list = [s.strip() for s in labels.split(",") if s.strip()]
    cfg = AppConfig.from_env()
    gl = build_client(cfg)
    effective_ps = page_size if page_size > 0 else cfg.page_size
    loaded = _load_user_items(gl, username, effective_ps, label_list=label_list)
    all_items = _filter_by_time(loaded["all_items"], s_date, u_date)
    key_to_reasons = loaded["key_to_reasons"]
    out = []
    for it in all_items:
        out.append(
            {
                "type": it.type,
                "iid": it.iid,
                "project_id": it.project_id,
                "title": it.title,
                "state": it.state,
                "web_url": it.web_url,
                "labels": list(it.labels),
                "assignee": it.assignee,
                "updated_at": it.updated_at,
                "reasons": list(key_to_reasons.get(it.key, [])),
            }
        )
    return JSONResponse({"count": len(out), "items": out})


@app.get("/api/recent-users", tags=["Users"])
async def api_recent_users() -> JSONResponse:
    try:
        dbp = _db_path()
        users = storage.list_recent_users(dbp, limit=8)
    except Exception:
        users = []
    return JSONResponse({"users": users})


@app.post("/api/board/move", tags=["Board"])
async def api_board_move(payload: dict = Body(...)) -> JSONResponse:
    """拖拽覆盖：{username, item_key, column_id}。"""
    username = _validate_username(payload.get("username", ""))
    item_key = str(payload.get("item_key", "")).strip()
    column_id = str(payload.get("column_id", "")).strip()
    if not username or not item_key or not column_id:
        raise HTTPException(status_code=400, detail="missing field")
    dbp = _db_path()
    cols = {c["id"] for c in storage.list_columns(dbp, username)}
    if column_id not in cols:
        raise HTTPException(status_code=400, detail="unknown column")
    storage.set_override(dbp, username, item_key, column_id)
    return JSONResponse({"ok": True})


@app.post("/api/board/reset", tags=["Board"])
async def api_board_reset(payload: dict = Body(...)) -> JSONResponse:
    username = _validate_username(payload.get("username", ""))
    if not username:
        raise HTTPException(status_code=400, detail="missing username")
    storage.clear_overrides(_db_path(), username)
    return JSONResponse({"ok": True})


@app.get("/api/board/columns", tags=["Board"])
async def api_board_columns_list(
    username: str = Query(..., description="GitLab username"),
) -> JSONResponse:
    """返回某用户的列定义列表（首次访问会自动初始化内置列）。"""
    valid = _validate_username(username)
    if not valid:
        raise HTTPException(status_code=400, detail="missing username")
    dbp = _db_path()
    columns = storage.list_columns(dbp, valid)
    return JSONResponse({"username": valid, "columns": columns})


@app.post("/api/board/columns", tags=["Board"])
async def api_board_columns_add(payload: dict = Body(...)) -> JSONResponse:
    username = _validate_username(payload.get("username", ""))
    title = str(payload.get("title", "")).strip()
    column_id = str(payload.get("column_id", "")).strip()
    if not username or not title or not column_id:
        raise HTTPException(status_code=400, detail="missing field")
    if not re.match(r"^[a-z0-9_\-]{1,32}$", column_id):
        raise HTTPException(status_code=400, detail="invalid column_id (a-z0-9_-)")
    if len(title) > 32:
        raise HTTPException(status_code=400, detail="title too long")
    dbp = _db_path()
    existing = {c["id"] for c in storage.list_columns(dbp, username)}
    if column_id in existing:
        raise HTTPException(status_code=400, detail="column_id exists")
    column = storage.add_column(dbp, username, column_id, title)
    return JSONResponse({"column": column})


@app.patch("/api/board/columns/{column_id}", tags=["Board"])
async def api_board_columns_rename(
    column_id: str,
    payload: dict = Body(...),
) -> JSONResponse:
    username = _validate_username(payload.get("username", ""))
    title = str(payload.get("title", "")).strip()
    if not username or not title:
        raise HTTPException(status_code=400, detail="missing field")
    dbp = _db_path()
    ok = storage.rename_column(dbp, username, column_id, title)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return JSONResponse({"ok": True})


@app.post("/api/board/columns/reorder", tags=["Board"])
async def api_board_columns_reorder(payload: dict = Body(...)) -> JSONResponse:
    """按传入顺序重排列。payload: ``{username, column_ids: [...]}``。"""
    username = _validate_username(payload.get("username", ""))
    if not username:
        raise HTTPException(status_code=400, detail="missing username")
    column_ids = payload.get("column_ids")
    if not isinstance(column_ids, list) or not all(isinstance(c, str) for c in column_ids):
        raise HTTPException(status_code=400, detail="column_ids must be a list of strings")
    dbp = _db_path()
    updated = storage.reorder_columns(dbp, username, column_ids)
    return JSONResponse({"ok": True, "updated": updated})


@app.delete("/api/board/columns/{column_id}", tags=["Board"])
async def api_board_columns_delete(
    column_id: str,
    payload: dict = Body(...),
) -> JSONResponse:
    username = _validate_username(payload.get("username", ""))
    if not username:
        raise HTTPException(status_code=400, detail="missing username")
    dbp = _db_path()
    ok = storage.delete_column(dbp, username, column_id)
    if not ok:
        raise HTTPException(status_code=400, detail="cannot delete (builtin or missing)")
    return JSONResponse({"ok": True})


@app.post("/api/preferences", tags=["Users"])
async def api_preferences(payload: dict = Body(...)) -> JSONResponse:
    username = _validate_username(payload.get("username", ""))
    theme = str(payload.get("theme", "auto"))
    if not username:
        raise HTTPException(status_code=400, detail="missing username")
    try:
        storage.set_theme(_db_path(), username, theme)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid theme") from e
    return JSONResponse({"ok": True, "theme": theme})


# ----- 系统端点 -----
@app.get("/api/version", tags=["System"])
async def api_version() -> JSONResponse:
    """返回应用版本与 Python / 关键依赖版本。便于部署后做版本核对。"""
    import sys

    from gitlab_issues_finder import __version__

    return JSONResponse(
        {
            "app": __version__,
            "python": sys.version.split()[0],
            "fastapi": __import__("fastapi").__version__,
        }
    )


@app.get("/api/health", tags=["System"])
async def api_health() -> JSONResponse:
    """健康检查：检查 SQLite 可达 + GitLab config 是否配齐。

    返回值：
      - status: "ok" (全部正常) / "degraded" (可降级运行) / "down" (异常)
      - checks: dict[check_name -> {ok, detail}]
    """
    checks: dict = {}

    # 1) DB 可达性
    try:
        dbp = _db_path()
        storage.list_recent_users(dbp, limit=1)
        checks["db"] = {"ok": True, "detail": dbp}
    except Exception as e:
        checks["db"] = {"ok": False, "detail": str(e)}

    # 2) GitLab 配置
    try:
        cfg = AppConfig.from_env()
        checks["config"] = {"ok": True, "detail": cfg.url}
    except ConfigError as e:
        checks["config"] = {"ok": False, "detail": str(e)}

    overall = "ok" if all(c["ok"] for c in checks.values()) else "degraded"
    return JSONResponse(
        {
            "status": overall,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": round(time.time() - _START_TIME, 2),
            "checks": checks,
        }
    )


@app.get("/metrics", tags=["System"])
async def api_metrics() -> Response:
    """Prometheus 文本格式的进程内指标。

    包含：
      - process_uptime_seconds：自启动以来的秒数
      - 业务计数器（按需注册）
      - 业务直方图（按需注册）
    """
    return Response(
        content=get_metrics().render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/api/routes", tags=["System"])
async def api_routes() -> JSONResponse:
    """返回所有已注册的 HTTP 路由（method + path + tags + name）。

    供运维 / 调试做能力发现；不依赖 OpenAPI 文档。
    """
    routes: list[dict] = []
    for r in app.routes:
        methods = getattr(r, "methods", None)
        path = getattr(r, "path", None)
        if not methods or not path or path.startswith("/openapi"):
            continue
        # 跳过 HEAD（与 GET 重复）
        ms = sorted(m for m in methods if m != "HEAD")
        if not ms:
            continue
        routes.append(
            {
                "path": path,
                "methods": ms,
                "tags": getattr(r, "tags", []) or [],
                "name": getattr(r, "name", None),
            }
        )
    routes.sort(key=lambda r: (r["path"], r["methods"]))
    return JSONResponse({"count": len(routes), "routes": routes})


@app.get("/api/stats", tags=["System"])
async def api_stats() -> JSONResponse:
    """汇总进程级统计：SQLite 库大小 + 4 张核心表的行数。"""
    import os as _os

    db_path = _db_path()
    db_bytes = 0
    with contextlib.suppress(OSError):
        db_bytes = _os.path.getsize(db_path)
    storage_stats: dict = {"db_path": db_path, "db_bytes": db_bytes}
    try:
        with _connect(db_path) as conn:
            storage_stats["recent_users"] = conn.execute(
                "SELECT COUNT(*) FROM user_prefs"
            ).fetchone()[0]
            storage_stats["overrides"] = conn.execute(
                "SELECT COUNT(*) FROM board_overrides"
            ).fetchone()[0]
            storage_stats["columns"] = conn.execute(
                "SELECT COUNT(*) FROM board_columns"
            ).fetchone()[0]
            storage_stats["cached_projects"] = conn.execute(
                "SELECT COUNT(*) FROM project_cache"
            ).fetchone()[0]
    except Exception as e:  # noqa: BLE001
        storage_stats["error"] = str(e)
    return JSONResponse({"storage": storage_stats})


def _connect(db_path):
    """连接到 SQLite 并返回行 dict。"""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ----- 分桶预览 -----
@app.post("/api/preview", tags=["Board"])
async def api_preview(payload: dict = Body(...)) -> JSONResponse:
    """给定一个 item（type/project_id/iid） + 一组命中关系，预测它会落在哪个列。

    用于前端做"快速归档"对话框：用户粘贴 issue 链接，后端告诉前端
    默认会落到哪一列（reviewer/assignee/mention/author/other），并展示
    当前所有可用列，便于用户选一个。

    payload:
        {
          "username": "alice",
          "item": {"type": "issue", "project_id": 1, "iid": 7},
          "reasons": ["assignee", "mention"]   // 命中维度
        }
    """
    username = _validate_username(payload.get("username", ""))
    if not username:
        raise HTTPException(status_code=400, detail="missing username")
    item = payload.get("item") or {}
    item_type = item.get("type")
    if item_type not in ("issue", "merge_request"):
        raise HTTPException(status_code=400, detail="item.type must be 'issue' or 'merge_request'")
    reasons = payload.get("reasons") or []
    if not isinstance(reasons, list) or not all(isinstance(r, str) for r in reasons):
        raise HTTPException(status_code=400, detail="reasons must be a list of strings")
    dbp = _db_path()
    col_defs = storage.list_columns(dbp, username)
    col_ids = {c["id"] for c in col_defs}
    # 默认分桶逻辑（与 /board 路由保持一致：reviewer/assignee/mention/author 优先，其他归 "other"）
    target = "other"
    for cand in ("reviewer", "assignee", "mention", "author"):
        if cand in reasons and cand in col_ids:
            target = cand
            break
    # 检查是否已被手动覆盖
    item_key = f"{item_type}-{item.get('project_id')}-{item.get('iid')}"
    overrides = storage.get_overrides(dbp, username)
    manual = overrides.get(item_key)
    return JSONResponse(
        {
            "item_key": item_key,
            "default_column": target,
            "current_override": manual,
            "available_columns": col_defs,
        }
    )


# ----- 数据导出 -----
def _collect_export_items(username: str, labels_raw: str) -> list[IssueRef]:
    """复用 /search 的查询逻辑，组装导出用的 items 列表。

    失败抛 AppError（ConfigError / AuthError / ...），由全局 handler 渲染。
    """
    cfg = AppConfig.from_env()
    gl = build_client(cfg)
    label_list = [s.strip() for s in labels_raw.split(",") if s.strip()]
    loaded = _load_user_items(gl, username, cfg.page_size, label_list=label_list)
    return loaded["all_items"]


@app.get("/api/export.csv", tags=["Export"])
async def api_export_csv(username: str = Query(...), labels: str = Query("")) -> Response:
    """以 CSV 格式导出与 username 相关的所有 item。

    列：type, iid, project_id, title, state, web_url, labels, assignee, updated_at。
    labels 多值以 `|` 拼接。"""
    items = _collect_export_items(username, labels)
    rows = ["type,iid,project_id,title,state,web_url,labels,assignee,updated_at"]
    for it in items:
        rows.append(
            ",".join(
                [
                    it.type,
                    str(it.iid),
                    str(it.project_id),
                    f'"{it.title.replace(chr(34), chr(34) + chr(34))}"',
                    it.state,
                    it.web_url,
                    f'"{"|".join(it.labels)}"',
                    it.assignee or "",
                    it.updated_at,
                ]
            )
        )
    body = "\n".join(rows) + "\n"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="gitlab-{username}-items.csv"'},
    )


@app.get("/api/export.md", tags=["Export"])
async def api_export_md(username: str = Query(...), labels: str = Query("")) -> Response:
    """以 Markdown 表格格式导出。便于贴进周报 / PR description。"""
    items = _collect_export_items(username, labels)
    issues = [it for it in items if it.type == "issue"]
    mrs = [it for it in items if it.type == "merge_request"]

    def render_section(title: str, lst: list[IssueRef]) -> str:
        if not lst:
            return f"## {title}\n\n_无_\n"
        lines = [
            f"## {title} ({len(lst)})\n",
            "| IID | Title | State | Labels | Updated |",
            "|---|---|---|---|---|",
        ]
        for it in sorted(lst, key=lambda x: (x.project_id, x.iid)):
            iid = f"!{it.iid}" if it.type == "merge_request" else f"#{it.iid}"
            title_cell = f"[{it.title}]({it.web_url})" if it.web_url else it.title
            labels_cell = ", ".join(f"{lb}" for lb in it.labels[:5])
            lines.append(
                f"| {iid} | {title_cell} | {it.state} | {labels_cell} | {it.updated_at[:10]} |"
            )
        return "\n".join(lines) + "\n"

    body = (
        f"# @{username} — GitLab Status Board 导出\n\n"
        f"- Issue: **{len(issues)}** 条\n"
        f"- MR: **{len(mrs)}** 条\n\n"
        + render_section("Issues", issues)
        + render_section("Merge Requests", mrs)
    )
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="gitlab-{username}-items.md"'},
    )


# ----- 错误处理 -----
def _try_cfg() -> AppConfig | None:
    try:
        return AppConfig.from_env()
    except ConfigError:
        return None


def _render_error(request: Request, message: str, title: str = "出错了") -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "error.html",
        {"title": title, "message": message, "active_tab": None},
        status_code=200,
    )


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> HTMLResponse:
    title = "出错了"
    if isinstance(exc, ConfigError):
        title = "配置错误"
    elif isinstance(exc, AuthError):
        title = "认证失败"
    return templates.TemplateResponse(
        request,
        "error.html",
        {"title": title, "message": str(exc), "active_tab": None},
        status_code=200,
    )
