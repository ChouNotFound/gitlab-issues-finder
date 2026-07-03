"""GitLab Issue / Merge Request 查询与去重。

查询维度（任一命中即视为「参与的」）：
  - assignee_username：被指派给该用户
  - mention_username：被该用户 @ 提及（评论/描述/标题中含 @username）
  - author_username：由该用户创建

每个维度同时覆盖 /issues 和 /merge_requests 两个端点；MR 额外支持 reviewer。
查询结果合入 dedupe() 按 (type, project_id, iid) 去重。
"""

from __future__ import annotations

from typing import Iterable, Iterator, Sequence

import gitlab

from gitlab_issues_finder.client import safe_http_get
from gitlab_issues_finder.models import IssueRef


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


def _fetch_by_param(
    gl: gitlab.Gitlab,
    username: str,
    page_size: int,
    param: str,
    type: str,
    path: str,
) -> list[IssueRef]:
    """通用维度查询：根据 param（assignee/mention/author/reviewer）拉开 item。

    关键修复：传 ``with_membership=false`` 给 GitLab API，让搜索
    跨整个实例（不限用户 membership），从而发现"我被分派但我不是
    该项目成员"的 issue。否则 GitLab 默认按用户 membership 范围搜索，
    导致"项目里明明给我派了活，但看板查不到"的体验。
    """
    params = {param: username, "state": "opened", "with_membership": "false"}
    return [
        IssueRef.from_api(p, type=type)  # type: ignore[arg-type]
        for p in _iter_pages(gl, params, page_size, path=path)
    ]


def fetch_issues_by_assignee(gl, username, page_size=100):
    return _fetch_by_param(gl, username, page_size, "assignee_username", "issue", "/issues")


def fetch_issues_by_mention(gl, username, page_size=100):
    return _fetch_by_param(gl, username, page_size, "mention_username", "issue", "/issues")


def fetch_issues_by_author(gl, username, page_size=100):
    return _fetch_by_param(gl, username, page_size, "author_username", "issue", "/issues")


def fetch_merge_requests_by_assignee(gl, username, page_size=100):
    return _fetch_by_param(gl, username, page_size, "assignee_username", "merge_request", "/merge_requests")


def fetch_merge_requests_by_mention(gl, username, page_size=100):
    return _fetch_by_param(gl, username, page_size, "mention_username", "merge_request", "/merge_requests")


def fetch_merge_requests_by_author(gl, username, page_size=100):
    return _fetch_by_param(gl, username, page_size, "author_username", "merge_request", "/merge_requests")


def fetch_merge_requests_by_reviewer(gl, username, page_size=100):
    """reviewer_username 仅对 merge request 有效。"""
    params = {"reviewer_username": username, "state": "opened", "with_membership": "false"}
    return [
        IssueRef.from_api(p, type="merge_request")
        for p in _iter_pages(gl, params, page_size, path="/merge_requests")
    ]


def fetch_labeled(
    gl: gitlab.Gitlab,
    labels: Sequence[str],
    page_size: int = 100,
) -> list[IssueRef]:
    """拉取同时包含 labels 中所有标签（AND） 且 state=opened 的 issues。

    调用方需自行保证 labels 非空；空列表请跳过此函数而非依赖内部判定。
    """
    params = {"labels": ",".join(labels), "state": "opened", "with_membership": "false"}
    return [
        IssueRef.from_api(p, type="issue")
        for p in _iter_pages(gl, params, page_size)
    ]


def fetch_merge_requests_by_labels(
    gl: gitlab.Gitlab,
    labels: Sequence[str],
    page_size: int = 100,
) -> list[IssueRef]:
    """拉取同时包含 labels 中所有标签（AND）且 state=opened 的 merge requests。

    调用方需自行保证 labels 非空。
    """
    params = {"labels": ",".join(labels), "state": "opened", "with_membership": "false"}
    return [
        IssueRef.from_api(p, type="merge_request")
        for p in _iter_pages(gl, params, page_size, path="/merge_requests")
    ]


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
