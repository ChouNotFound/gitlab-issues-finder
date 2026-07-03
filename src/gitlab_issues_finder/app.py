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

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gitlab_issues_finder import storage
from gitlab_issues_finder.client import build_client
from gitlab_issues_finder.config import AppConfig
from gitlab_issues_finder.errors import AppError, AuthError, ConfigError
from gitlab_issues_finder.models import IssueRef
from gitlab_issues_finder.queries import (
    ItemKind,
    Relation,
    dedupe,
    fetch_items,
    fetch_labeled,
    fetch_users,
)

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
    except ConfigError:
        pass
    yield
    # shutdown: 暂无需清理（SQLite 连接随用随关）


app = FastAPI(title="GitLab Status Board", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ----- 工具函数 -----
def _db_path() -> str:
    """从环境取 DB_PATH。

    与 AppConfig 解耦：DB_PATH 不依赖 GITLAB_URL/TOKEN，
    即使 GitLab 配置缺失也能读写本地看板状态。
    """
    return os.environ.get("DB_PATH", "data/app.db")


def _validate_username(raw: str) -> str | None:
    raw = raw.strip()
    if not raw or len(raw) > 255:
        return None
    # GitLab username 限制：字母数字 + _-. + @.+_
    if not re.match(r"^[\w.\-@]+$", raw):
        return None
    return raw


# ----- HTML 路由 -----
@app.get("/", response_class=HTMLResponse)
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


@app.post("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    username: str = Form(...),
    labels: str = Form(""),
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

    cfg = AppConfig.from_env()
    gl = build_client(cfg)

    issue_relations = (Relation.ASSIGNEE, Relation.MENTION, Relation.AUTHOR)
    issue_lists: dict[str, list[IssueRef]] = {
        rel.value: fetch_items(gl, username, rel, ItemKind.ISSUE, cfg.page_size)
        for rel in issue_relations
    }
    mr_relations = (Relation.ASSIGNEE, Relation.MENTION, Relation.AUTHOR, Relation.REVIEWER)
    mr_lists: dict[str, list[IssueRef]] = {
        rel.value: fetch_items(gl, username, rel, ItemKind.MERGE_REQUEST, cfg.page_size)
        for rel in mr_relations
    }

    issues_labeled: list[IssueRef] = []
    mrs_labeled: list[IssueRef] = []
    if label_list:
        issues_labeled = fetch_labeled(gl, label_list, cfg.page_size)
        mrs_labeled = fetch_labeled(gl, label_list, cfg.page_size, kind=ItemKind.MERGE_REQUEST)

    all_items = dedupe(
        *issue_lists.values(), *mr_lists.values(),
        issues_labeled, mrs_labeled,
    )
    issues = [it for it in all_items if it.type == "issue"]
    merge_requests = [it for it in all_items if it.type == "merge_request"]

    labeled_issue_keys = {it.key for it in issues_labeled}
    labeled_mr_keys = {it.key for it in mrs_labeled}

    def compute_reasons(item: IssueRef) -> list[str]:
        reasons: list[str] = []
        bucket = issue_lists if item.type == "issue" else mr_lists
        for reason, lst in bucket.items():
            if any(it.key == item.key for it in lst):
                reasons.append(reason)
        if label_list:
            labeled = labeled_issue_keys if item.type == "issue" else labeled_mr_keys
            if item.key in labeled:
                reasons.append("label")
        return reasons

    issues.sort(key=lambda it: it.updated_at, reverse=True)
    merge_requests.sort(key=lambda it: it.updated_at, reverse=True)
    issues_with_reasons = [(it, compute_reasons(it)) for it in issues]
    mrs_with_reasons = [(it, compute_reasons(it)) for it in merge_requests]

    # 更新最近使用
    try:
        storage.touch_user(cfg.db_path, username)
    except Exception:
        pass

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
        },
    )


@app.get("/board", response_class=HTMLResponse)
async def board(
    request: Request,
    username: str = Query("", alias="username"),
    q: str = Query("", description="搜索过滤"),
    view: str = Query("summary", description="视图模式: summary|all|issues|mrs|relation|project"),
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
            },
        )

    # 验证 view 参数
    allowed_views = {"summary", "all", "issues", "mrs", "relation", "project"}
    if view not in allowed_views:
        view = "summary"

    cfg = AppConfig.from_env()
    gl = build_client(cfg)

    issue_relations = (Relation.ASSIGNEE, Relation.MENTION, Relation.AUTHOR)
    issue_lists: dict[str, list[IssueRef]] = {
        rel.value: fetch_items(gl, username, rel, ItemKind.ISSUE, cfg.page_size)
        for rel in issue_relations
    }
    mr_relations = (Relation.ASSIGNEE, Relation.MENTION, Relation.AUTHOR, Relation.REVIEWER)
    mr_lists: dict[str, list[IssueRef]] = {
        rel.value: fetch_items(gl, username, rel, ItemKind.MERGE_REQUEST, cfg.page_size)
        for rel in mr_relations
    }

    issues_labeled: list[IssueRef] = []
    mrs_labeled: list[IssueRef] = []
    label_list = [s.strip() for s in q.split(",") if s.strip()] if q else []
    if label_list:
        issues_labeled = fetch_labeled(gl, label_list, cfg.page_size)
        mrs_labeled = fetch_labeled(gl, label_list, cfg.page_size, kind=ItemKind.MERGE_REQUEST)

    key_to_reasons: dict[tuple[str, int, int], list[str]] = {}
    for reason, lst in {**issue_lists, **mr_lists}.items():
        for it in lst:
            key_to_reasons.setdefault(it.key, []).append(reason)

    all_items = dedupe(
        *issue_lists.values(), *mr_lists.values(),
        issues_labeled, mrs_labeled,
    )

    # ---- 关键修复保证：搜索跨越多项目时不漏 ----
    # all_items 中保留的是 GitLab API 返回的实际项目 ID 集合
    # （with_membership=false 已生效），无需额外合并

    # ---- 按"关系"分桶（与原 Kanban 列对齐） ----
    # 这里用 key_to_reasons 判断 item 命中维度
    items_by_rel: dict[str, list[IssueRef]] = {
        "reviewer": [], "assignee": [], "mention": [], "author": [], "other": [],
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
        if not placed:
            if "other" in items_by_col:
                items_by_col["other"].append(item)
    for lst in items_by_col.values():
        lst.sort(key=lambda it: it.updated_at, reverse=True)
    total = sum(len(v) for v in items_by_col.values())

    # ---- 综述 ----
    summary = _compute_summary(all_items, key_to_reasons, items_by_proj)

    try:
        storage.touch_user(dbp, username)
    except Exception:
        pass

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
        },
    )


def _empty_summary() -> dict:
    return {
        "total": 0,
        "issues": 0,
        "mrs": 0,
        "projects": 0,
        "by_relation": {"reviewer": 0, "assignee": 0, "mention": 0, "author": 0, "other": 0},
        "by_project": [],
        "most_recent": None,
    }


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
        (
            {"project_id": pid, "count": len(lst)}
            for pid, lst in items_by_proj.items()
        ),
        key=lambda d: d["count"],
        reverse=True,
    )[:10]
    most_recent = max(all_items, key=lambda it: it.updated_at)
    return {
        "total": len(all_items),
        "issues": len(issues),
        "mrs": len(mrs),
        "projects": len(items_by_proj),
        "by_relation": by_rel,
        "by_project": by_proj,
        "most_recent": most_recent,
    }


# ----- JSON API -----
@app.get("/api/users")
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


@app.get("/api/recent-users")
async def api_recent_users() -> JSONResponse:
    try:
        dbp = _db_path()
        users = storage.list_recent_users(dbp, limit=8)
    except Exception:
        users = []
    return JSONResponse({"users": users})


@app.post("/api/board/move")
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


@app.post("/api/board/reset")
async def api_board_reset(payload: dict = Body(...)) -> JSONResponse:
    username = _validate_username(payload.get("username", ""))
    if not username:
        raise HTTPException(status_code=400, detail="missing username")
    storage.clear_overrides(_db_path(), username)
    return JSONResponse({"ok": True})


@app.post("/api/board/columns")
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


@app.patch("/api/board/columns/{column_id}")
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


@app.delete("/api/board/columns/{column_id}")
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


@app.post("/api/preferences")
async def api_preferences(payload: dict = Body(...)) -> JSONResponse:
    username = _validate_username(payload.get("username", ""))
    theme = str(payload.get("theme", "auto"))
    if not username:
        raise HTTPException(status_code=400, detail="missing username")
    try:
        storage.set_theme(_db_path(), username, theme)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid theme")
    return JSONResponse({"ok": True, "theme": theme})


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
