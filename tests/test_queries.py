"""queries.py 单元测试。

使用 responses 拦截 python-gitlab 内部 HTTP 请求，回放 fixture。
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import gitlab
import pytest
import responses

from gitlab_issues_finder.queries import (
    dedupe,
    fetch_issues_by_assignee,
    fetch_issues_by_author,
    fetch_issues_by_mention,
    fetch_labeled,
    fetch_merge_requests_by_assignee,
    fetch_merge_requests_by_author,
    fetch_merge_requests_by_labels,
    fetch_merge_requests_by_mention,
    fetch_merge_requests_by_reviewer,
    fetch_users,
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
        result = fetch_issues_by_assignee(gl, "alice")
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "assignee_username", "alice")

    @responses.activate
    def test_fetch_issues_by_mention(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/issues", [load_fixture("issues_assigned.json")])
        result = fetch_issues_by_mention(gl, "alice")
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "mention_username", "alice")

    @responses.activate
    def test_fetch_issues_by_author(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/issues", [load_fixture("issues_assigned.json")])
        result = fetch_issues_by_author(gl, "alice")
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "author_username", "alice")

    @pytest.mark.parametrize("func_name,param", [
        ("fetch_issues_by_assignee", "assignee_username"),
        ("fetch_issues_by_mention", "mention_username"),
        ("fetch_issues_by_author", "author_username"),
    ])
    @responses.activate
    def test_issue_endpoint_passes_with_membership_false(self, gl, func_name, param):
        """关键回归：所有 issue 维度查询都必须传 with_membership=false。

        否则用户被指派但不是项目成员时，GitLab 会按 membership 限定范围，
        导致"项目里明明给我派了活但查不到"的 bug。
        """
        from gitlab_issues_finder import queries as q
        func = getattr(q, func_name)
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/issues", [[]])
        func(gl, "alice")
        last_qs = parse_qs(urlparse(responses.calls[-1].request.url).query)
        assert last_qs.get("with_membership") == ["false"]
        assert last_qs.get(param) == ["alice"]


class TestMergeRequestDimensionQueries:
    """MR 四个参与维度的参数路由正确性。"""

    @responses.activate
    def test_fetch_merge_requests_by_assignee(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/merge_requests", [load_fixture("mr_mentioned.json")])
        result = fetch_merge_requests_by_assignee(gl, "alice")
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "assignee_username", "alice")

    @responses.activate
    def test_fetch_merge_requests_by_mention(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/merge_requests", [load_fixture("mr_mentioned.json")])
        result = fetch_merge_requests_by_mention(gl, "alice")
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "mention_username", "alice")

    @responses.activate
    def test_fetch_merge_requests_by_author(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/merge_requests", [load_fixture("mr_mentioned.json")])
        result = fetch_merge_requests_by_author(gl, "alice")
        assert len(result) == 2
        _assert_query_param(responses.calls[-1].request.url, "author_username", "alice")

    @responses.activate
    def test_fetch_merge_requests_by_reviewer(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/merge_requests", [load_fixture("mr_mentioned.json")])
        result = fetch_merge_requests_by_reviewer(gl, "alice")
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


class TestFetchMergeRequestsByLabels:
    @responses.activate
    def test_labels_passed_as_csv(self, gl):
        _add_user_endpoint(responses)
        _add_paginated_endpoint(responses, "/merge_requests", [load_fixture("mr_labeled.json")])
        result = fetch_merge_requests_by_labels(gl, ["bug", "priority::high"])
        assert all(it.type == "merge_request" for it in result)
        _assert_query_param(responses.calls[-1].request.url, "labels", "bug,priority::high")


class TestDedupe:
    def test_no_overlap(self):
        from gitlab_issues_finder.models import IssueRef
        a = IssueRef.from_api({"project_id": 1, "iid": 1, "title": "a",
                                "state": "opened", "labels": [], "assignee": None,
                                "web_url": "u", "updated_at": "t"})
        b = IssueRef.from_api({"project_id": 2, "iid": 1, "title": "b",
                                "state": "opened", "labels": [], "assignee": None,
                                "web_url": "u", "updated_at": "t"})
        result = dedupe([a], [b])
        assert len(result) == 2
        assert result[0].key == ("issue", 1, 1)
        assert result[1].key == ("issue", 2, 1)

    def test_full_overlap(self):
        from gitlab_issues_finder.models import IssueRef
        a = IssueRef.from_api({"project_id": 1, "iid": 1, "title": "a",
                                "state": "opened", "labels": [], "assignee": None,
                                "web_url": "u", "updated_at": "t"})
        b = IssueRef.from_api({"project_id": 1, "iid": 1, "title": "a-dup",
                                "state": "opened", "labels": [], "assignee": None,
                                "web_url": "u", "updated_at": "t2"})
        result = dedupe([a], [b])
        assert len(result) == 1
        assert result[0].title == "a"

    def test_partial_overlap(self):
        from gitlab_issues_finder.models import IssueRef
        a = IssueRef.from_api({"project_id": 1, "iid": 1, "title": "1",
                                "state": "opened", "labels": [], "assignee": None,
                                "web_url": "u", "updated_at": "t"})
        b = IssueRef.from_api({"project_id": 1, "iid": 2, "title": "2",
                                "state": "opened", "labels": [], "assignee": None,
                                "web_url": "u", "updated_at": "t"})
        c = IssueRef.from_api({"project_id": 1, "iid": 1, "title": "1-dup",
                                "state": "opened", "labels": [], "assignee": None,
                                "web_url": "u", "updated_at": "t"})
        result = dedupe([a], [b, c])
        assert len(result) == 2
        assert [it.iid for it in result] == [1, 2]

    def test_cross_type_same_iid_not_deduplicated(self):
        from gitlab_issues_finder.models import IssueRef
        issue = IssueRef.from_api(
            {"project_id": 1, "iid": 5, "title": "issue-5",
             "state": "opened", "labels": [], "assignee": None,
             "web_url": "https://gl/issues/5", "updated_at": "t"},
            type="issue",
        )
        mr = IssueRef.from_api(
            {"project_id": 1, "iid": 5, "title": "mr-5",
             "state": "opened", "labels": [], "assignee": None,
             "web_url": "https://gl/merge_requests/5", "updated_at": "t"},
            type="merge_request",
        )
        result = dedupe([issue], [mr])
        assert len(result) == 2
        assert {it.type for it in result} == {"issue", "merge_request"}

    def test_same_type_overlap_still_dedupes(self):
        from gitlab_issues_finder.models import IssueRef
        a = IssueRef.from_api(
            {"project_id": 7, "iid": 1, "title": "a",
             "state": "opened", "labels": [], "assignee": None,
             "web_url": "u", "updated_at": "t"},
            type="merge_request",
        )
        b = IssueRef.from_api(
            {"project_id": 7, "iid": 1, "title": "b",
             "state": "opened", "labels": [], "assignee": None,
             "web_url": "u", "updated_at": "t"},
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
        users_calls = [c for c in responses.calls if "/users?" in c.request.url or c.request.url.endswith("/users")]
        assert len(users_calls) == 2

    @responses.activate
    def test_empty(self, gl):
        responses.add(responses.GET, f"{API_BASE}/users", json=[], status=200)
        result = fetch_users(gl)
        assert result == []
