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

import re
from collections.abc import Iterable, Iterator, Sequence
from enum import Enum

from gitlab_issues_finder.client import GitlabClient, safe_http_get
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

# 新增的两个「类关系」常量。
# 注意：与 Relation 不同，它们不以 username 为查询参数：
#   - subscribed：取当前 Token 持有者订阅的 items
#   - reaction：取当前 Token 持有者用某 emoji 反应过的 items
# 不进 Relation 枚举是为了保持 Relation ↔ username query param 的对应不变量。
EXTRA_SUBSCRIBED = "subscribed"
EXTRA_REACTION = "reaction"

# reaction 维度默认 emoji；调用方可在 fetch_reacted 里覆盖。
REACTION_EMOJI_DEFAULT = "thumbsup"

# Relation → {assignee_id|reviewer_id} 的对应关系（用于多 assignee/reviewer 拉取）。
# 注意：mention/author 的「多用户」语义 GitLab API 不直接支持（按单一值索引），
# 因此这两类关系不进入 _ID_RELATION_MAP。
_ID_RELATION_MAP: dict[Relation, str] = {
    Relation.ASSIGNEE: "assignee_id",
    Relation.REVIEWER: "reviewer_id",
}


# 防御性上限：单次分页拉取的页数。100 页 * 100 条 = 10k 条结果，
# 远超个人看板的合理规模。触发后立即停止并信任调用方处理 (通常是缓存截断)。
_MAX_PAGES = 100


def _iter_pages(
    gl: GitlabClient,
    params: dict,
    page_size: int,
    path: str = "/issues",
) -> Iterator[dict]:
    """通用分页迭代器。

    GitLab 默认 per_page=20，最多 100。每次拉满 page_size，少于 page_size 即终止。
    path 默认 "/issues"，传 "/merge_requests" 等即可复用同一分页逻辑。

    防御: 当 GitLab 不正确地返回等于 page_size 的最后一页 (罕见的客户端 / 服务端
    bug 或 504 重试场景) 时, 硬上限 ``_MAX_PAGES`` 防止无限循环 + 内存爆炸。
    """
    page = 1
    while page <= _MAX_PAGES:
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
    """所有 fetch_* 共用前缀：scope=all + state=opened + 跨实例搜索。

    scope=all 避免 GitLab 全局列表接口默认只返回当前 token 相关的子集；
    with_membership=false 让搜索跨整个实例（不限用户 membership），从而发现
    "我被分派但我不是该项目成员"的 item。
    """
    return {"scope": "all", **query, "state": "opened", "with_membership": "false"}


def fetch_items(
    gl: GitlabClient,
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


def resolve_user_ids(
    gl: GitlabClient,
    username: str,
    page_size: int = 100,
    max_total: int = 100,
) -> list[int]:
    """把 username 解析为对应的用户 ID 列表。

    通过 `GET /users?username=X` 拉取。GitLab 中同一个 username 可能对应多个
    账号（外部用户 / bot / 重名），所以这里返回 list 而非单值。

    找不到（空响应）时返回 []，由调用方决定如何处理。
    """
    if not username:
        return []
    out: list[int] = []
    page = 1
    while len(out) < max_total:
        chunk = safe_http_get(
            gl,
            "/users",
            **{
                "username": username,
                "page": page,
                "per_page": page_size,
            },
        )
        if not chunk:
            break
        for u in chunk:
            uid = u.get("id")
            if uid is not None:
                out.append(int(uid))
        if len(chunk) < page_size:
            break
        page += 1
        if len(out) >= max_total:
            out = out[:max_total]
            break
    return out


def fetch_items_by_user_id(
    gl: GitlabClient,
    user_ids: Sequence[int],
    relation: Relation,
    kind: ItemKind,
    page_size: int = 100,
) -> list[IssueRef]:
    """按 user_id + relation + kind 拉取 item 列表。

    用于补齐多 assignee / 多 reviewer 场景：
    GitLab API 中 `assignee_username=X` / `reviewer_username=X` 只命中「主」
    指派人/审查人；当一个 issue/MR 存在多个 assignee/reviewer 时，只有走
    `assignee_id={id}` / `reviewer_id={id}` 才能命中「X 是众多 assignee/reviewer
    之一」的情况。

    支持的 relation：ASSIGNEE / REVIEWER（其余关系无 ID 维度等价物）。
    """
    if relation not in _ID_RELATION_MAP:
        raise ValueError(
            f"relation {relation.value!r} has no id-based query; "
            f"allowed: {sorted(r.value for r in _ID_RELATION_MAP)}"
        )
    if kind not in _RELATION_ITEM_KIND_COMPAT[relation]:
        allowed = sorted(k.type_name for k in _RELATION_ITEM_KIND_COMPAT[relation])
        raise ValueError(
            f"relation {relation.value!r} is not valid for kind {kind.type_name!r}; "
            f"allowed kinds: {allowed}"
        )
    if not user_ids:
        return []
    id_param = _ID_RELATION_MAP[relation]
    out: list[IssueRef] = []
    # GitLab API 限制：单次请求 id 类参数只接受一个值（不支持 OR 数组）。
    # 因此对每个 ID 各发一次拉取，调用方（app.py）负责去重合并。
    for uid in user_ids:
        params = _make_params({id_param: uid})
        out.extend(
            IssueRef.from_api(p, type=kind.type_name)
            for p in _iter_pages(gl, params, page_size, path=kind.path)
        )
    return out


def fetch_subscribed(
    gl: GitlabClient,
    kind: ItemKind,
    page_size: int = 100,
) -> list[IssueRef]:
    """拉取当前 Token 持有者订阅的 items。

    走 `?subscribed=true` —— GitLab 该参数只识别 Token 持有者本人，**与查询
    username 无关**。如需查别人，请用对方的 Token。
    """
    params = _make_params({"subscribed": "true"})
    return [
        IssueRef.from_api(p, type=kind.type_name)
        for p in _iter_pages(gl, params, page_size, path=kind.path)
    ]


def fetch_reacted(
    gl: GitlabClient,
    emoji: str = REACTION_EMOJI_DEFAULT,
    kind: ItemKind = ItemKind.ISSUE,
    page_size: int = 100,
) -> list[IssueRef]:
    """拉取当前 Token 持有者用给定 emoji 反应过的 items。

    走 `?my_reaction_emoji={emoji}`。该参数只识别 Token 持有者本人。
    `emoji` 默认 thumbsup，可由调用方覆盖。
    """
    params = _make_params({"my_reaction_emoji": emoji})
    return [
        IssueRef.from_api(p, type=kind.type_name)
        for p in _iter_pages(gl, params, page_size, path=kind.path)
    ]


def fetch_labeled(
    gl: GitlabClient,
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


def fetch_open_issues(
    gl: GitlabClient,
    page_size: int = 100,
) -> list[dict]:
    """拉取当前用户可访问范围内的全部 opened issues 原始 payload。

    这里显式带 `scope=all`，避免 GitLab 默认只返回 `created_by_me`。
    返回原始 dict 以便调用方同时检查 title / description / notes。
    """
    params = _make_params({"scope": "all"})
    return list(_iter_pages(gl, params, page_size, path=ItemKind.ISSUE.path))


def fetch_issue_notes(
    gl: GitlabClient,
    project_id: int,
    issue_iid: int,
    page_size: int = 100,
) -> list[dict]:
    """拉取指定 issue 的人工评论 notes。"""
    path = f"/projects/{project_id}/issues/{issue_iid}/notes"
    params = {
        "activity_filter": "only_comments",
        "sort": "asc",
        "order_by": "updated_at",
    }
    return list(_iter_pages(gl, params, page_size, path=path))


def _mention_regex(username: str) -> re.Pattern[str]:
    """构造匹配 ``@<username>`` 的正则。

    GitLab 用户名区分大小写，因此不使用 ``re.IGNORECASE``：
    使用 ``IGNORECASE`` 会把 ``@Bob`` 也匹配 ``bob`` 的查询，从而在不同
    用户名仅大小写差异时产生交叉误统计。
    """
    escaped = re.escape(username)
    return re.compile(rf"(?<![\w@])@{escaped}(?![\w.\-])")


def _is_self_authored_note(note: dict, username: str) -> bool:
    """判断 note 是否由 ``username`` 本人发出。

    GitLab ``activity_filter=only_comments`` 已过滤系统事件，但为了
    防御性兜底（缺 ``author`` 字段、``author`` 缺 ``username`` 字段、
    ``username`` 为 ``None``），一律保守视为「非本人」——宁可多走一次
    匹配也不能误排除他人评论。
    """
    author = note.get("author") or {}
    author_username = author.get("username") if isinstance(author, dict) else None
    if not isinstance(author_username, str):
        return False
    return author_username == username


def fetch_issue_low_threshold_items(
    gl: GitlabClient,
    username: str,
    page_size: int = 100,
) -> tuple[list[IssueRef], list[IssueRef]]:
    """补齐 Issue 的显式 mention 定义。

    返回 `(mentioned_items, [])`：

    - mentioned_items：在 title / description / **他人**评论中显式
      ``@`` 到 ``username`` 的 issue 列表。

    注意：

    1. 评论扫描时排除本人发出的 note——避免「我在自己评论里
       ``@bob``」被错误地算作「``@我``」（这是「@我」维度大量误统
       计的根因）。
    2. 第二个返回值仅为兼容旧调用方保留；本人回复不再属于默认
       参与度口径。
    3. title / description 不按作者过滤：若用户自己创建了一个
       issue 并在 description 中 ``@他人``，GitLab API 层无法
       区分「自创引用」与「他人 ``@`` 我」，目前保留现状。如有
       强烈需求可在 product 上明确说明。
    """
    if not username:
        return [], []

    mention_re = _mention_regex(username)
    mentioned: list[IssueRef] = []
    for payload in fetch_open_issues(gl, page_size):
        ref = IssueRef.from_api(payload, type=ItemKind.ISSUE.type_name)
        title = str(payload.get("title", ""))
        description = str(payload.get("description", ""))
        matched_mention = bool(mention_re.search(title) or mention_re.search(description))

        for note in fetch_issue_notes(gl, ref.project_id, ref.iid, page_size):
            if _is_self_authored_note(note, username):
                # 自己发出的评论不算「@我」——这是「@我」维度大量误统计的根因。
                continue
            body = str(note.get("body", ""))
            if mention_re.search(body):
                matched_mention = True

        if matched_mention:
            mentioned.append(ref)

    return mentioned, []


def fetch_users(gl: GitlabClient, page_size: int = 100, max_total: int = 200) -> list[dict]:
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


def resolve_projects(
    gl: GitlabClient,
    project_ids,
    page_size: int = 100,
) -> dict:
    """按 project_id 批量拉取 {name, path_with_namespace}。

    GitLab /projects/:id 单点拉取太慢，所以走 /projects?membership=false
    + 客户端过滤。返回 {project_id: {name, path_with_namespace}}。
    """
    wanted = set(project_ids)
    out: dict = {}
    if not wanted:
        return out
    page = 1
    while True:
        chunk = safe_http_get(
            gl,
            "/projects",
            **{"page": page, "per_page": page_size, "membership": "false"},
        )
        if not chunk:
            break
        for p in chunk:
            pid = p.get("id")
            if pid in wanted:
                out[pid] = {
                    "name": p.get("name", ""),
                    "path_with_namespace": p.get("path_with_namespace", ""),
                }
        if len(out) >= len(wanted) or len(chunk) < page_size:
            break
        page += 1
    return out
