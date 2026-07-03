"""project_resolver unit tests."""

from __future__ import annotations

import pytest
import responses

from gitlab_issues_finder import project_resolver, storage

API_BASE = "https://gitlab.test/api/v4"


@pytest.fixture
def gl():
    import gitlab
    return gitlab.Gitlab(url="https://gitlab.test", private_token="x", api_version="4")


class TestResolve:
    @responses.activate
    def test_empty_input_returns_empty(self, gl, tmp_db):
        assert project_resolver.resolve(tmp_db, gl, []) == {}
        assert project_resolver.resolve(tmp_db, gl, set()) == {}

    @responses.activate
    def test_fetches_missing_and_caches(self, gl, tmp_db):
        responses.add(
            responses.GET,
            f"{API_BASE}/projects",
            json=[
                {"id": 1, "name": "Backend", "path_with_namespace": "team/backend"},
                {"id": 2, "name": "Frontend", "path_with_namespace": "team/frontend"},
            ],
            status=200,
            match_querystring=False,
        )
        result = project_resolver.resolve(tmp_db, gl, [1, 2])
        assert result[1]["name"] == "Backend"
        assert result[2]["path_with_namespace"] == "team/frontend"
        # Second call should hit cache (no extra HTTP request)
        result2 = project_resolver.resolve(tmp_db, gl, [1, 2])
        assert result2 == result
        assert len(responses.calls) == 1

    @responses.activate
    def test_force_refresh_when_stale(self, gl, tmp_db):
        storage.upsert_project(tmp_db, 99, "OldName", "old/path")
        responses.add(
            responses.GET,
            f"{API_BASE}/projects",
            json=[{"id": 99, "name": "NewName", "path_with_namespace": "new/path"}],
            status=200,
            match_querystring=False,
        )
        result = project_resolver.resolve(tmp_db, gl, [99], ttl_seconds=0)
        assert result[99]["name"] == "NewName"
        assert result[99]["path_with_namespace"] == "new/path"

    @responses.activate
    def test_missing_projects_silently_dropped(self, gl, tmp_db):
        responses.add(
            responses.GET,
            f"{API_BASE}/projects",
            json=[{"id": 5, "name": "Other", "path_with_namespace": "x/other"}],
            status=200,
            match_querystring=False,
        )
        result = project_resolver.resolve(tmp_db, gl, [42])
        assert 42 not in result
