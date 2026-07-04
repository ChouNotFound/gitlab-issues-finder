"""queries.py 单元测试。

使用 responses 拦截 python-gitlab 内部 HTTP 请求，回放 fixture。
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import gitlab
import pytest
import responses

from gitlab_issues_finder.queries import (
    EXTRA_REACTION,
    EXTRA_SUBSCRIBED,
    REACTION_EMOJI_DEFAULT,
    ItemKind,
    Relation,
    dedupe,
    fetch_issue_low_threshold_items,
    fetch_issue_notes,
    fetch_items,
    fetch_items_by_user_id,
    fetch_labeled,
    fetch_open_issues,
    fetch_reacted,
    fetch_subscribed,
    fetch_users,
    resolve_user_ids,
)
from tests.conftest import load_fixture

GITLAB_URL = "https://gitlab.test"
API_BASE = f"{GITLAB_URL}/api/v4"


def _add_user_endpoint(responses_mock: responses.RequestsMock) -> None:
    responses_mock.add(
        responses.GET,
        f"{API_BASE}/user",
        json={"id": 1, "username": "me"},
        status=200,
    )


def _add_paginated_endpoint(
    responses_mock: responses.RequestsMock,
    path: str,
    pages: list[list[dict]],
) -> list[str]:
    urls = []
    for _idx, page_data in enumerate(pages, start=1):
        url = f"{API_BASE}{path}"
        urls.append(url)
        responses_mock.add(
            responses.GET,
            url,
            json=page_data,
            status=200,
            match_querystring=False,
        )
    return urls


@pytest.fixture
def gl():
    return gitlab.Gitlab(url=GITLAB_URL, private_token="x", api_version="4")


def _assert_query_param(last_url: str, key: str, value: str) -> None:
    qs = parse_qs(urlparse(last_url).query)
    assert qs[key] == [value], f"expected qs[{key}]={value!r}, got {qs.get(key)}"
    assert qs["state"] == ["opened"]


class TestIssueDimensionQueries:
    """issue 三个参与维度的参数路由正确性。"""

    @responses.activate
    def test_fetch_issues_by_assignee(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/issues", [load_fixture("issues_assigned.json")])
        result = fetch_items(gl, "alice", Relation.ASSIGNEE, ItemKind.ISSUE)
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "assignee_username", "alice")

    @responses.activate
    def test_fetch_issues_by_mention(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/issues", [load_fixture("issues_assigned.json")])
        result = fetch_items(gl, "alice", Relation.MENTION, ItemKind.ISSUE)
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "mention_username", "alice")

    @responses.activate
    def test_fetch_issues_by_author(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/issues", [load_fixture("issues_assigned.json")])
        result = fetch_items(gl, "alice", Relation.AUTHOR, ItemKind.ISSUE)
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "author_username", "alice")

    @pytest.mark.parametrize(
        "relation,param",
        [
            (Relation.ASSIGNEE, "assignee_username"),
            (Relation.MENTION, "mention_username"),
            (Relation.AUTHOR, "author_username"),
        ],
    )
    @responses.activate
    def test_issue_endpoint_passes_with_membership_false(self, gl, relation, param):
        """关键回归：所有 issue 维度查询都必须传 with_membership=false。

        否则用户被指派但不是项目成员时，GitLab 会按 membership 限定范围，
        导致"项目里明明给我派了活但查不到"的 bug。
        """
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/issues", [[]])
        fetch_items(gl, "alice", relation, ItemKind.ISSUE)
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs.get("with_membership") == ["false"]
        assert last_qs.get(param) == ["alice"]


class TestMergeRequestDimensionQueries:
    """MR 四个参与维度的参数路由正确性。"""

    @responses.activate
    def test_fetch_merge_requests_by_assignee(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/merge_requests", [load_fixture("mr_mentioned.json")])
        result = fetch_items(gl, "alice", Relation.ASSIGNEE, ItemKind.MERGE_REQUEST)
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "assignee_username", "alice")

    @responses.activate
    def test_fetch_merge_requests_by_mention(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/merge_requests", [load_fixture("mr_mentioned.json")])
        result = fetch_items(gl, "alice", Relation.MENTION, ItemKind.MERGE_REQUEST)
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "mention_username", "alice")

    @responses.activate
    def test_fetch_merge_requests_by_author(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/merge_requests", [load_fixture("mr_mentioned.json")])
        result = fetch_items(gl, "alice", Relation.AUTHOR, ItemKind.MERGE_REQUEST)
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "author_username", "alice")

    @responses.activate
    def test_fetch_merge_requests_by_reviewer(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/merge_requests", [load_fixture("mr_mentioned.json")])
        result = fetch_items(gl, "alice", Relation.REVIEWER, ItemKind.MERGE_REQUEST)
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "reviewer_username", "alice")


class TestFetchLabeled:
    @responses.activate
    def test_single_label(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/issues", [load_fixture("issues_labeled.json")])
        result = fetch_labeled(gl, ["alice"])
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "labels", "alice")

    @responses.activate
    def test_multi_labels(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/issues", [[]])
        fetch_labeled(gl, ["bug", "priority::high"])
        _assert_query_param(responses.calls[-1].request.url, "labels", "bug,priority::high")


class TestIssueLowThresholdQueries:
    @responses.activate
    def test_fetch_open_issues_uses_scope_all(self, gl):
        _add_paginated_endpoint(responses, "/issues", [load_fixture("issues_opened.json")])
        result = fetch_open_issues(gl)
        assert len(result) == 2
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs["scope"] == ["all"]
        assert last_qs["state"] == ["opened"]
        assert last_qs["with_membership"] == ["false"]

    @responses.activate
    def test_fetch_issue_notes_only_comments(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/projects/201/issues/7/notes",
            json=load_fixture("issue_notes_mentioned.json"),
            status=200,
            match_querystring=False,
        )
        result = fetch_issue_notes(gl, 201, 7)
        assert len(result) == 1
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs["activity_filter"] == ["only_comments"]
        assert last_qs["sort"] == ["asc"]
        assert last_qs["order_by"] == ["updated_at"]

    @responses.activate
    def test_fetch_issue_low_threshold_items(self, gl):
        _add_paginated_endpoint(responses, "/issues", [load_fixture("issues_opened.json")])
        responses.add(
            responses.GET,
            f"{API_BASE}/projects/201/issues/7/notes",
            json=load_fixture("issue_notes_mentioned.json"),
            status=200,
            match_querystring=False,
        )
        responses.add(
            responses.GET,
            f"{API_BASE}/projects/202/issues/8/notes",
            json=load_fixture("issue_notes_replied.json"),
            status=200,
            match_querystring=False,
        )
        mentioned, replied = fetch_issue_low_threshold_items(gl, "alice")
        assert [(it.project_id, it.iid) for it in mentioned] == [(201, 7)]
        assert replied == []

    @responses.activate
    def test_low_threshold_excludes_self_authored_note(self, gl):
        """Bug #1 回归：自己评论里 @ 他人不算「@我」。

        查询 alice；alice 在某 issue 评论里 @bob。该 issue 不应在
        alice 的 mentioned 列表中（否则 bob 的「@我」会被污染，反
        之亦然）。
        """
        issues_payload = [
            {
                "project_id": 203,
                "iid": 9,
                "title": "Plain issue",
                "description": "No explicit mention here",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "https://gitlab.example.com/group/proj5/-/issues/9",
                "updated_at": "2026-07-03T11:00:00.000Z",
            }
        ]
        _add_paginated_endpoint(responses, "/issues", [issues_payload])
        responses.add(
            responses.GET,
            f"{API_BASE}/projects/203/issues/9/notes",
            json=load_fixture("issue_notes_self_mentioning.json"),
            status=200,
            match_querystring=False,
        )
        mentioned, _ = fetch_issue_low_threshold_items(gl, "alice")
        # alice 自己 @bob 不算 alice 被 @ -> 不应在结果中
        assert mentioned == []

    @responses.activate
    def test_low_threshold_is_case_sensitive(self, gl):
        """Bug #2 回归：GitLab 用户名区分大小写，@Bob 不应误命中 bob 查询。"""
        issues_payload = [
            {
                "project_id": 204,
                "iid": 10,
                "title": "Issue with case-different mention",
                "description": "Hey @Bob please look",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "https://gitlab.example.com/group/proj6/-/issues/10",
                "updated_at": "2026-07-03T12:00:00.000Z",
            }
        ]
        _add_paginated_endpoint(responses, "/issues", [issues_payload])
        # 即使 title/desc 未命中，实现仍会调用 fetch_issue_notes 注册端点
        responses.add(
            responses.GET,
            f"{API_BASE}/projects/204/issues/10/notes",
            json=[],
            status=200,
            match_querystring=False,
        )
        # 不需要走 note 路径：title/desc 触发，但移除 IGNORECASE 后应不再命中
        mentioned, _ = fetch_issue_low_threshold_items(gl, "bob")
        assert mentioned == []

    @responses.activate
    def test_low_threshold_skips_note_with_missing_author(self, gl):
        """健壮性：note 缺 author 字段时不应抛异常，应保守视为他人评论。"""
        issues_payload = [
            {
                "project_id": 205,
                "iid": 11,
                "title": "Defensive test",
                "description": "no @ here",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "https://gitlab.example.com/group/proj7/-/issues/11",
                "updated_at": "2026-07-03T13:00:00.000Z",
            }
        ]
        _add_paginated_endpoint(responses, "/issues", [issues_payload])
        responses.add(
            responses.GET,
            f"{API_BASE}/projects/205/issues/11/notes",
            json=[
                {
                    "id": 1005,
                    "body": "Note without author field, mentions @alice",
                    # 注意：没有 author 键
                    "system": False,
                    "created_at": "2026-07-03T13:30:00.000Z",
                    "updated_at": "2026-07-03T13:30:00.000Z",
                }
            ],
            status=200,
            match_querystring=False,
        )
        mentioned, _ = fetch_issue_low_threshold_items(gl, "alice")
        # author 缺失视为「非本人」，因此 note 中的 @alice 仍应命中
        assert len(mentioned) == 1
        assert (mentioned[0].project_id, mentioned[0].iid) == (205, 11)


class TestFetchMergeRequestsByLabels:
    @responses.activate
    def test_labels_passed_as_csv(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/merge_requests", [load_fixture("mr_labeled.json")])
        result = fetch_labeled(gl, ["bug", "priority::high"], kind=ItemKind.MERGE_REQUEST)
        assert all(it.type == "merge_request" for it in result)
        _assert_query_param(responses.calls[-1].request.url, "labels", "bug,priority::high")


class TestDedupe:
    def test_no_overlap(self):
        from gitlab_issues_finder.models import IssueRef

        a = IssueRef.from_api(
            {
                "project_id": 1,
                "iid": 1,
                "title": "a",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "u",
                "updated_at": "t",
            }
        )
        b = IssueRef.from_api(
            {
                "project_id": 2,
                "iid": 1,
                "title": "b",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "u",
                "updated_at": "t",
            }
        )
        result = dedupe([a], [b])
        assert len(result) == 2
        assert result[0].key == ("issue", 1, 1)
        assert result[1].key == ("issue", 2, 1)

    def test_full_overlap(self):
        from gitlab_issues_finder.models import IssueRef

        a = IssueRef.from_api(
            {
                "project_id": 1,
                "iid": 1,
                "title": "a",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "u",
                "updated_at": "t",
            }
        )
        b = IssueRef.from_api(
            {
                "project_id": 1,
                "iid": 1,
                "title": "a-dup",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "u",
                "updated_at": "t2",
            }
        )
        result = dedupe([a], [b])
        assert len(result) == 1
        assert result[0].title == "a"

    def test_partial_overlap(self):
        from gitlab_issues_finder.models import IssueRef

        a = IssueRef.from_api(
            {
                "project_id": 1,
                "iid": 1,
                "title": "1",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "u",
                "updated_at": "t",
            }
        )
        b = IssueRef.from_api(
            {
                "project_id": 1,
                "iid": 2,
                "title": "2",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "u",
                "updated_at": "t",
            }
        )
        c = IssueRef.from_api(
            {
                "project_id": 1,
                "iid": 1,
                "title": "1-dup",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "u",
                "updated_at": "t",
            }
        )
        result = dedupe([a], [b, c])
        assert len(result) == 2
        assert [it.iid for it in result] == [1, 2]

    def test_cross_type_same_iid_not_deduplicated(self):
        from gitlab_issues_finder.models import IssueRef

        issue = IssueRef.from_api(
            {
                "project_id": 1,
                "iid": 5,
                "title": "issue-5",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "https://gl/issues/5",
                "updated_at": "t",
            },
            type="issue",
        )
        mr = IssueRef.from_api(
            {
                "project_id": 1,
                "iid": 5,
                "title": "mr-5",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "https://gl/merge_requests/5",
                "updated_at": "t",
            },
            type="merge_request",
        )
        result = dedupe([issue], [mr])
        assert len(result) == 2
        assert {it.type for it in result} == {"issue", "merge_request"}

    def test_same_type_overlap_still_dedupes(self):
        from gitlab_issues_finder.models import IssueRef

        a = IssueRef.from_api(
            {
                "project_id": 7,
                "iid": 1,
                "title": "a",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "u",
                "updated_at": "t",
            },
            type="merge_request",
        )
        b = IssueRef.from_api(
            {
                "project_id": 7,
                "iid": 1,
                "title": "b",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "u",
                "updated_at": "t",
            },
            type="merge_request",
        )
        assert len(dedupe([a], [b])) == 1

    def test_empty(self):
        assert dedupe() == []
        assert dedupe([], []) == []


class TestFetchUsers:
    @responses.activate
    def test_single_page(self, gl):
        users = [
            {"id": 1, "username": "alice", "name": "Alice"},
            {"id": 2, "username": "bob", "name": "Bob"},
        ]
        responses.add(
            responses.GET,
            f"{API_BASE}/users",
            json=users,
            status=200,
        )
        result = fetch_users(gl, page_size=100, max_total=200)
        assert len(result) == 2
        assert result[0]["username"] == "alice"

        last_url = responses.calls[-1].request.url
        qs = parse_qs(urlparse(last_url).query)
        assert qs["active"] == ["true"]
        assert qs["without_project_bots"] == ["true"]

    @responses.activate
    def test_respects_max_total(self, gl):
        page1 = [{"id": i, "username": f"u{i}", "name": f"U{i}"} for i in range(1, 101)]
        page2 = [{"id": i, "username": f"u{i}", "name": f"U{i}"} for i in range(101, 201)]
        page3 = [{"id": i, "username": f"u{i}", "name": f"U{i}"} for i in range(201, 251)]
        responses.add(responses.GET, f"{API_BASE}/users", json=page1, status=200)
        responses.add(responses.GET, f"{API_BASE}/users", json=page2, status=200)
        responses.add(responses.GET, f"{API_BASE}/users", json=page3, status=200)

        result = fetch_users(gl, page_size=100, max_total=150)
        assert len(result) == 150
        users_calls = [
            c
            for c in responses.calls
            if "/users?" in c.request.url or c.request.url.endswith("/users")
        ]
        assert len(users_calls) == 2

    @responses.activate
    def test_empty(self, gl):
        responses.add(responses.GET, f"{API_BASE}/users", json=[], status=200)
        result = fetch_users(gl)
        assert result == []


class TestFetchItemsFactory:
    """验证新的 fetch_items() 工厂：参数路由、错误边界。"""

    @responses.activate
    @pytest.mark.parametrize(
        "relation,param",
        [
            (Relation.ASSIGNEE, "assignee_username"),
            (Relation.MENTION, "mention_username"),
            (Relation.AUTHOR, "author_username"),
        ],
    )
    def test_fetch_items_issue_all_relations(self, gl, relation, param):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/issues", [load_fixture("issues_assigned.json")])
        result = fetch_items(gl, "alice", relation, ItemKind.ISSUE)
        assert all(it.type == "issue" for it in result)
        _assert_query_param(responses.calls[-1].request.url, param, "alice")

    @responses.activate
    @pytest.mark.parametrize(
        "relation,param",
        [
            (Relation.ASSIGNEE, "assignee_username"),
            (Relation.MENTION, "mention_username"),
            (Relation.AUTHOR, "author_username"),
            (Relation.REVIEWER, "reviewer_username"),
        ],
    )
    def test_fetch_items_mr_all_relations(self, gl, relation, param):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/merge_requests", [load_fixture("mr_mentioned.json")])
        result = fetch_items(gl, "alice", relation, ItemKind.MERGE_REQUEST)
        assert all(it.type == "merge_request" for it in result)
        _assert_query_param(responses.calls[-1].request.url, param, "alice")

    def test_reviewer_relation_rejected_for_issue(self, gl):
        """reviewer_username 在 GitLab API 中只适用于 MR。"""
        with __import__("pytest").raises(ValueError, match="not valid for kind"):
            fetch_items(gl, "alice", Relation.REVIEWER, ItemKind.ISSUE)

    @responses.activate
    def test_fetch_items_always_passes_with_membership_false(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/issues", [[]])
        fetch_items(gl, "alice", Relation.ASSIGNEE, ItemKind.ISSUE)
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs.get("with_membership") == ["false"]
        assert last_qs.get("scope") == ["all"]

    @responses.activate
    @pytest.mark.parametrize(
        "relation,param",
        [
            (Relation.ASSIGNEE, "assignee_username"),
            (Relation.REVIEWER, "reviewer_username"),
        ],
    )
    def test_fetch_merge_requests_passes_scope_all(self, gl, relation, param):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/merge_requests", [[]])
        fetch_items(gl, "alice", relation, ItemKind.MERGE_REQUEST)
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs[param] == ["alice"]
        assert last_qs["scope"] == ["all"]
        assert last_qs["with_membership"] == ["false"]


class TestFetchLabeledKind:
    """fetch_labeled() 的 kind 参数切换 issue / MR 端点。"""

    @responses.activate
    def test_labeled_issue_default(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/issues", [load_fixture("issues_labeled.json")])
        result = fetch_labeled(gl, ["bug"], 100)
        assert all(it.type == "issue" for it in result)

    @responses.activate
    def test_labeled_merge_request(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/merge_requests", [load_fixture("mr_labeled.json")])
        result = fetch_labeled(gl, ["bug"], 100, kind=ItemKind.MERGE_REQUEST)
        assert all(it.type == "merge_request" for it in result)


class TestResolveUserIds:
    """username -> [user_id, ...] via /users?username=X."""

    @responses.activate
    def test_single_user(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/users",
            json=[{"id": 42, "username": "alice"}],
            status=200,
            match_querystring=False,
        )
        result = resolve_user_ids(gl, "alice")
        assert result == [42]
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs["username"] == ["alice"]

    @responses.activate
    def test_multiple_accounts_same_username(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/users",
            json=[
                {"id": 1, "username": "alice"},
                {"id": 2, "username": "alice"},
            ],
            status=200,
            match_querystring=False,
        )
        result = resolve_user_ids(gl, "alice")
        assert result == [1, 2]

    @responses.activate
    def test_not_found_returns_empty(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/users",
            json=[],
            status=200,
            match_querystring=False,
        )
        assert resolve_user_ids(gl, "ghost") == []

    def test_empty_username_returns_empty(self, gl):
        assert resolve_user_ids(gl, "") == []


class TestFetchItemsByUserId:
    """id-based query for the multi-assignee / multi-reviewer case."""

    @responses.activate
    def test_assignee_id_issue(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/issues",
            json=load_fixture("issues_assigned.json"),
            status=200,
            match_querystring=False,
        )
        result = fetch_items_by_user_id(gl, [42], Relation.ASSIGNEE, ItemKind.ISSUE)
        assert len(result) == 2
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs["assignee_id"] == ["42"]
        assert last_qs["state"] == ["opened"]
        assert last_qs["with_membership"] == ["false"]
        assert last_qs["scope"] == ["all"]

    @responses.activate
    def test_assignee_id_mr(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/merge_requests",
            json=load_fixture("mr_mentioned.json"),
            status=200,
            match_querystring=False,
        )
        result = fetch_items_by_user_id(
            gl, [42], Relation.ASSIGNEE, ItemKind.MERGE_REQUEST
        )
        assert len(result) == 2
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs["assignee_id"] == ["42"]
        assert last_qs["scope"] == ["all"]

    @responses.activate
    def test_reviewer_id_mr(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/merge_requests",
            json=load_fixture("mr_mentioned.json"),
            status=200,
            match_querystring=False,
        )
        result = fetch_items_by_user_id(
            gl, [42], Relation.REVIEWER, ItemKind.MERGE_REQUEST
        )
        assert len(result) == 2
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs["reviewer_id"] == ["42"]
        assert last_qs["scope"] == ["all"]

    def test_reviewer_rejected_for_issue(self, gl):
        with pytest.raises(ValueError, match="not valid for kind"):
            fetch_items_by_user_id(gl, [42], Relation.REVIEWER, ItemKind.ISSUE)

    def test_mention_not_supported(self, gl):
        with pytest.raises(ValueError, match="has no id-based query"):
            fetch_items_by_user_id(gl, [42], Relation.MENTION, ItemKind.ISSUE)

    def test_empty_user_ids_returns_empty(self, gl):
        assert fetch_items_by_user_id(gl, [], Relation.ASSIGNEE, ItemKind.ISSUE) == []

    @responses.activate
    def test_multiple_user_ids_makes_one_call_each(self, gl):
        for _ in range(2):
            responses.add(
                responses.GET,
                f"{API_BASE}/issues",
                json=load_fixture("issues_assigned.json"),
                status=200,
                match_querystring=False,
            )
        result = fetch_items_by_user_id(
            gl, [1, 2], Relation.ASSIGNEE, ItemKind.ISSUE
        )
        assert len(result) == 4
        assignee_ids = [
            parse_qs(urlparse(c.request.url).query)["assignee_id"]
            for c in responses.calls
        ]
        assert assignee_ids == [["1"], ["2"]]


class TestFetchSubscribed:
    """/issues?subscribed=true and /merge_requests?subscribed=true."""

    @responses.activate
    def test_issue_subscribed(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/issues",
            json=load_fixture("issues_assigned.json"),
            status=200,
            match_querystring=False,
        )
        result = fetch_subscribed(gl, ItemKind.ISSUE)
        assert len(result) == 2
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs["subscribed"] == ["true"]
        assert last_qs["state"] == ["opened"]
        assert last_qs["with_membership"] == ["false"]

    @responses.activate
    def test_mr_subscribed(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/merge_requests",
            json=load_fixture("mr_mentioned.json"),
            status=200,
            match_querystring=False,
        )
        result = fetch_subscribed(gl, ItemKind.MERGE_REQUEST)
        assert len(result) == 2
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs["subscribed"] == ["true"]


class TestFetchReacted:
    """/issues?my_reaction_emoji=... and /merge_requests?my_reaction_emoji=..."""

    def test_default_emoji_is_thumbsup(self):
        assert REACTION_EMOJI_DEFAULT == "thumbsup"
        assert EXTRA_REACTION == "reaction"
        assert EXTRA_SUBSCRIBED == "subscribed"

    @responses.activate
    def test_issue_default_thumbsup(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/issues",
            json=load_fixture("issues_assigned.json"),
            status=200,
            match_querystring=False,
        )
        result = fetch_reacted(gl, kind=ItemKind.ISSUE)
        assert len(result) == 2
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs["my_reaction_emoji"] == ["thumbsup"]

    @responses.activate
    def test_custom_emoji(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/issues",
            json=load_fixture("issues_assigned.json"),
            status=200,
            match_querystring=False,
        )
        result = fetch_reacted(gl, "heart", kind=ItemKind.ISSUE)
        assert len(result) == 2
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs["my_reaction_emoji"] == ["heart"]

    @responses.activate
    def test_mr_kind(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/merge_requests",
            json=load_fixture("mr_mentioned.json"),
            status=200,
            match_querystring=False,
        )
        result = fetch_reacted(gl, kind=ItemKind.MERGE_REQUEST)
        assert len(result) == 2
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs["my_reaction_emoji"] == ["thumbsup"]
        assert "/merge_requests" in responses.calls[-1].request.url
