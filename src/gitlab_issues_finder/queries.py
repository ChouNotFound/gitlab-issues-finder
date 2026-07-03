"""GitLab Issue / Merge Request 查询与去重。

查询维度（任一命中即视为「参与的」）：
  - ASSIGNEE：被指派给该用户
  - MENTION：被该用户 @ 提及（评论/描述/标题中含 @username）
  - AUTHOR：由该用户创建
  - REVIEWER：被该用户审查（仅 MR）

每个维度同时覆盖 /issues 和 /merge_requests 两个端点；MR 额外支持 reviewer。
查询结果合入 dedupe() 按 (type, project_id, iid) 去重。

设计：
  - `Relation` / `ItemKind` 枚举 + `fetch_items()` 工厂函数替代
    历史上的 7 个薄包装函数（fetch_issues_by_assignee 等）。
  - 旧函数以 deprecated 形式保留到下一轮迭代，再统一删除（保持单步 PR 小）。
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable, Iterator, Sequence

import gitlab

from gitlab_issues_finder.client import safe_http_get
from gitlab_issues_finder.models import IssueRef


class ItemKind(Enum):
    """GitLab API 端点 + ItemRef 类型。"""

    ISSUE = "issue"
    MERGE_REQUEST = "merge_request"

    @property
    def type_name(self) -> str:
        return self.value

    @property
    def path(self) -> str:
        return "/merge_requests" if self is ItemKind.MERGE_REQUEST else "/issues"


class Relation(Enum):
    """用户与 item 的参与关系 → GitLab API 查询参数名。"""

    ASSIGNEE = "assignee_username"
    MENTION = "mention_username"
    AUTHOR = "author_username"
    REVIEWER = "reviewer_username"  # 仅 MR 有效


# GitLab API 限制：reviewer_username 不适用于 issues。
_RELATION_ITEM_KIND_COMPAT: dict[Relation, frozenset[ItemKind]] = {
    Relation.ASSIGNEE: frozenset({ItemKind.ISSUE, ItemKind.MERGE_REQUEST}),
    Relation.MENTION: frozenset({ItemKind.ISSUE, ItemKind.MERGE_REQUEST}),
    Relation.AUTHOR: frozenset({ItemKind.ISSUE, ItemKind.MERGE_REQUEST}),
    Relation.REVIEWER: frozenset({ItemKind.MERGE_REQUEST}),
}


def _iter_pages(
    gl: gitlab.Gitlab,
    params: dict,
    page_size: int,
    path: str = "/issues",
) -> Iterator[dict]:
    """通用分页迭代器。

    GitLab 默认 per_page=20，最多 100。每次拉满 page_size，少于 page_size 即终止。
    path 默认 "/issues"，传 "/merge_requests" 等即可复用同一分页逻辑。
    """
    page = 1
    while True:
        chunk = safe_http_get(
            gl,
            path,
            **{**params, "page": page, "per_page": page_size},
        )
        if not chunk:
            return
        yield from chunk
        if len(chunk) < page_size:
            return
        page += 1


def _make_params(query: dict) -> dict:
    """所有 fetch_* 共用前缀：state=opened + 跨实例搜索（with_membership=false）。

    with_membership=false 是关键修复：让搜索跨整个实例（不限用户
    membership），从而发现"我被分派但我不是该项目成员"的 item。
    否则 GitLab 默认按用户 membership 范围搜索，导致"项目里明明
    给我派了活，但看板查不到"的体验。
    """
    return {**query, "state": "opened", "with_membership": "false"}


def fetch_items(
    gl: gitlab.Gitlab,
    username: str,
    relation: Relation,
    kind: ItemKind,
    page_size: int = 100,
) -> list[IssueRef]:
    """按 username + relation + kind 拉取 item 列表。"""
    if kind not in _RELATION_ITEM_KIND_COMPAT[relation]:
        allowed = sorted(k.type_name for k in _RELATION_ITEM_KIND_COMPAT[relation])
        raise ValueError(
            f"relation {relation.value!r} is not valid for kind {kind.type_name!r}; "
            f"allowed kinds: {allowed}"
        )
    params = _make_params({relation.value: username})
    return [
        IssueRef.from_api(p, type=kind.type_name)
        for p in _iter_pages(gl, params, page_size, path=kind.path)
    ]


def fetch_labeled(
    gl: gitlab.Gitlab,
    labels: Sequence[str],
    page_size: int = 100,
    *,
    kind: ItemKind = ItemKind.ISSUE,
) -> list[IssueRef]:
    """拉取同时包含 labels 中所有标签（AND）且 state=opened 的 items。

    调用方需自行保证 labels 非空。
    """
    params = _make_params({"labels": ",".join(labels)})
    return [
        IssueRef.from_api(p, type=kind.type_name)
        for p in _iter_pages(gl, params, page_size, path=kind.path)
    ]


def fetch_users(gl: gitlab.Gitlab, page_size: int = 100, max_total: int = 200) -> list[dict]:
    """拉取活跃用户列表（用于首页自动补全下拉框）。

    返回值是 GitLab API 原始 dict 列表，每项至少包含 id / username / name。
    限制 max_total 防止用户极多的实例把首页加载拖慢。
    """
    params = {"active": "true", "without_project_bots": "true"}
    out: list[dict] = []
    page = 1
    while len(out) < max_total:
        chunk = safe_http_get(
            gl,
            "/users",
            **{**params, "page": page, "per_page": page_size},
        )
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < page_size:
            break
        page += 1
        if len(out) >= max_total:
            out = out[:max_total]
            break
    return out


def dedupe(*lists: Iterable[IssueRef]) -> list[IssueRef]:
    """按 (type, project_id, iid) 去重合并多个列表，保持首次出现的顺序。"""
    seen: set[tuple[str, int, int]] = set()
    out: list[IssueRef] = []
    for lst in lists:
        for issue in lst:
            if issue.key in seen:
                continue
            seen.add(issue.key)
            out.append(issue)
    return out


# ----------------------------------------------------------------------------
# Backward-compat thin wrappers.
# New code should call `fetch_items(gl, username, relation, kind, page_size)`
# directly. These wrappers remain so existing tests / external imports do
# not break, and will be removed in a future iteration once all call sites
# migrate.
# ----------------------------------------------------------------------------


def fetch_issues_by_assignee(gl, username, page_size=100):
    return fetch_items(gl, username, Relation.ASSIGNEE, ItemKind.ISSUE, page_size)


def fetch_issues_by_mention(gl, username, page_size=100):
    return fetch_items(gl, username, Relation.MENTION, ItemKind.ISSUE, page_size)


def fetch_issues_by_author(gl, username, page_size=100):
    return fetch_items(gl, username, Relation.AUTHOR, ItemKind.ISSUE, page_size)


def fetch_merge_requests_by_assignee(gl, username, page_size=100):
    return fetch_items(gl, username, Relation.ASSIGNEE, ItemKind.MERGE_REQUEST, page_size)


def fetch_merge_requests_by_mention(gl, username, page_size=100):
    return fetch_items(gl, username, Relation.MENTION, ItemKind.MERGE_REQUEST, page_size)


def fetch_merge_requests_by_author(gl, username, page_size=100):
    return fetch_items(gl, username, Relation.AUTHOR, ItemKind.MERGE_REQUEST, page_size)


def fetch_merge_requests_by_reviewer(gl, username, page_size=100):
    return fetch_items(gl, username, Relation.REVIEWER, ItemKind.MERGE_REQUEST, page_size)


def fetch_merge_requests_by_labels(
    gl, labels, page_size=100,
):
    return fetch_labeled(gl, labels, kind=ItemKind.MERGE_REQUEST, page_size=page_size)
