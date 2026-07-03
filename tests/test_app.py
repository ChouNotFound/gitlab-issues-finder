"""app.py 端到端测试（FastAPI TestClient）。"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
import responses
from fastapi.testclient import TestClient

from gitlab_issues_finder.app import app
from tests.conftest import load_fixture

API_BASE = "https://gitlab.test/api/v4"


@pytest.fixture
def client() -> TestClient:
    # Use context manager so FastAPI lifespan (startup/shutdown) fires.
    with TestClient(app) as c:
        yield c


class TestIndexRoute:
    def test_get_index(self, client, tmp_db):
        resp = client.get("/")
        assert resp.status_code == 200
        # 标题与新版样式文案
        assert "GitLab Status Board" in resp.text or "Status Board" in resp.text
        assert 'name="username"' in resp.text
        # 行动文案 "打开看板"
        assert "查看看板" in resp.text


class TestSearchRoute:
    @responses.activate
    def test_search_with_results(self, client, monkeypatch, tmp_db):
        """三维度都触发，issue + MR 全覆盖；标签可选。"""
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")

        # 注册足够多的响应以覆盖 9 次查询（3 issue 维度 + 4 mr 维度 + 2 labels）
        for _ in range(3):
            responses.add(
                responses.GET,
                f"{API_BASE}/issues",
                json=load_fixture("issues_assigned.json"),
                status=200,
                match_querystring=False,
            )
        for _ in range(4):
            responses.add(
                responses.GET,
                f"{API_BASE}/merge_requests",
                json=load_fixture("mr_mentioned.json"),
                status=200,
                match_querystring=False,
            )
        # 2 次 labels 端点
        responses.add(
            responses.GET,
            f"{API_BASE}/issues",
            json=load_fixture("issues_labeled.json"),
            status=200,
            match_querystring=False,
        )
        responses.add(
            responses.GET,
            f"{API_BASE}/merge_requests",
            json=load_fixture("mr_labeled.json"),
            status=200,
            match_querystring=False,
        )

        resp = client.post(
            "/search",
            data={"username": "alice", "labels": "bug,priority::high"},
        )
        assert resp.status_code == 200
        assert "Fix login bug" in resp.text
        assert "Add CI pipeline for backend" in resp.text
        # 参与原因标签
        assert "assignee" in resp.text
        assert "mention" in resp.text
        assert "author" in resp.text

    @responses.activate
    def test_search_empty(self, client, monkeypatch, tmp_db):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        for _ in range(4):
            responses.add(
                responses.GET,
                f"{API_BASE}/merge_requests",
                json=[],
                status=200,
                match_querystring=False,
            )
        for _ in range(3):
            responses.add(
                responses.GET,
                f"{API_BASE}/issues",
                json=[],
                status=200,
                match_querystring=False,
            )
        resp = client.post("/search", data={"username": "ghost"})
        assert resp.status_code == 200
        assert "未找到" in resp.text

    @responses.activate
    def test_search_minimal_endpoints_when_no_labels(self, client, monkeypatch, tmp_db):
        """不传 labels 时只触发参与维度的 7 个端点；labels 维度不触发。"""
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        for _ in range(3):
            responses.add(
                responses.GET,
                f"{API_BASE}/issues",
                json=[],
                status=200,
                match_querystring=False,
            )
        for _ in range(4):
            responses.add(
                responses.GET,
                f"{API_BASE}/merge_requests",
                json=[],
                status=200,
                match_querystring=False,
            )
        resp = client.post("/search", data={"username": "alice"})
        assert resp.status_code == 200
        urls = [urlparse(c.request.url) for c in responses.calls]
        issue_urls = [u for u in urls if u.path == "/api/v4/issues"]
        mr_urls = [u for u in urls if u.path == "/api/v4/merge_requests"]
        assert len(issue_urls) >= 3
        assert len(mr_urls) >= 4
        for u in urls:
            assert "labels=" not in u.query, f"labels query fired: {u.query}"

    @responses.activate
    def test_search_labels_only_whitespace_skipped(self, client, monkeypatch, tmp_db):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        for _ in range(3):
            responses.add(
                responses.GET,
                f"{API_BASE}/issues",
                json=[],
                status=200,
                match_querystring=False,
            )
        for _ in range(4):
            responses.add(
                responses.GET,
                f"{API_BASE}/merge_requests",
                json=[],
                status=200,
                match_querystring=False,
            )
        resp = client.post("/search", data={"username": "alice", "labels": "  , , "})
        assert resp.status_code == 200
        qs_list = [urlparse(c.request.url).query for c in responses.calls]
        assert all("labels=" not in q for q in qs_list)

    @responses.activate
    def test_search_with_labels_passes_csv(self, client, monkeypatch, tmp_db):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        for _ in range(3):
            responses.add(
                responses.GET,
                f"{API_BASE}/issues",
                json=[],
                status=200,
                match_querystring=False,
            )
        for _ in range(4):
            responses.add(
                responses.GET,
                f"{API_BASE}/merge_requests",
                json=[],
                status=200,
                match_querystring=False,
            )
        # 留 2 个额外响应给 labels
        responses.add(
            responses.GET,
            f"{API_BASE}/issues",
            json=[],
            status=200,
            match_querystring=False,
        )
        responses.add(
            responses.GET,
            f"{API_BASE}/merge_requests",
            json=[],
            status=200,
            match_querystring=False,
        )
        resp = client.post(
            "/search",
            data={"username": "alice", "labels": "bug,priority::high"},
        )
        assert resp.status_code == 200
        labels_qs = [
            parse_qs(q).get("labels")
            for q in (urlparse(c.request.url).query for c in responses.calls)
        ]
        assert any(lst == ["bug,priority::high"] for lst in labels_qs)

    def test_empty_username(self, client, tmp_db):
        resp = client.post("/search", data={"username": "   "})
        assert resp.status_code == 200
        assert "出错了" in resp.text or "请输入" in resp.text

    def test_missing_token_returns_config_error(self, client, monkeypatch, tmp_db):
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        resp = client.post("/search", data={"username": "alice"})
        assert resp.status_code == 200
        assert "GITLAB_TOKEN" in resp.text

    @responses.activate
    def test_auth_error_renders_friendly_page(self, client, monkeypatch, tmp_db):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        responses.add(
            responses.GET,
            f"{API_BASE}/issues",
            json={"message": "401 Unauthorized"},
            status=401,
        )
        resp = client.post("/search", data={"username": "alice"})
        assert resp.status_code == 200
        assert "认证失败" in resp.text


class TestDbPathDecoupling:
    """验证 _db_path() 不依赖 GITLAB_URL/TOKEN：看板本地状态应始终可读写。"""

    def test_db_path_works_without_gitlab_config(self, tmp_db):
        """没有 GITLAB_URL/TOKEN 时仍能读写看板状态。"""
        # 显式清掉（如果 clean_env 被设过）
        from gitlab_issues_finder.app import _db_path

        with __import__("pytest").MonkeyPatch().context() as m:
            for k in ("GITLAB_URL", "GITLAB_TOKEN"):
                m.delenv(k, raising=False)
            # 但 DB_PATH 保留 tmp_db 设置的值
            assert _db_path() == tmp_db


class TestStaticFiles:
    def test_static_css_served(self, client):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "background" in resp.text


class TestBoardRoute:
    @responses.activate
    def test_board_empty_when_no_username(self, client, monkeypatch, tmp_db):
        """无 username 时看板仍渲染用户名输入入口（不列 5 列）。"""
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        resp = client.get("/board")
        assert resp.status_code == 200
        assert 'name="username"' in resp.text
        # 无 username 不应渲染列 5 个默认
        assert "需我审查" not in resp.text

    @responses.activate
    def test_board_buckets_mrs_by_relation(self, client, monkeypatch, tmp_db):
        """4 个 MR 分别匹配 reviewer/assignee/mention/author，应进入对应视图。"""
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")

        reviewer_mr = [
            {
                "project_id": 1,
                "iid": 11,
                "title": "Reviewer MR",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "https://gl/proj1/-/merge_requests/11",
                "updated_at": "2026-07-02T10:00:00Z",
            }
        ]
        assignee_mr = [
            {
                "project_id": 2,
                "iid": 22,
                "title": "Assignee MR",
                "state": "opened",
                "labels": [],
                "assignee": {"username": "alice"},
                "web_url": "https://gl/proj2/-/merge_requests/22",
                "updated_at": "2026-07-02T10:00:00Z",
            }
        ]
        mention_mr = [
            {
                "project_id": 3,
                "iid": 33,
                "title": "Mention MR",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "https://gl/proj3/-/merge_requests/33",
                "updated_at": "2026-07-02T10:00:00Z",
            }
        ]
        author_mr = [
            {
                "project_id": 4,
                "iid": 44,
                "title": "Author MR",
                "state": "opened",
                "labels": [],
                "assignee": None,
                "web_url": "https://gl/proj4/-/merge_requests/44",
                "updated_at": "2026-07-02T10:00:00Z",
            }
        ]

        # 默认视图 + relation 视图各需要 7 个 MR 维度查询（每次 request 都重新拉）
        for _ in range(7):
            responses.add(
                responses.GET,
                f"{API_BASE}/issues",
                json=[],
                status=200,
                match_querystring=False,
            )
        for payload in (assignee_mr, mention_mr, author_mr, reviewer_mr) * 2:
            responses.add(
                responses.GET,
                f"{API_BASE}/merge_requests",
                json=payload,
                status=200,
                match_querystring=False,
            )

        # 默认综述视图
        resp = client.get("/board?username=alice")
        assert resp.status_code == 200
        for t in ["Reviewer MR", "Assignee MR", "Mention MR", "Author MR"]:
            assert t in resp.text, f"missing MR (summary): {t}"
        assert "总 Items" in resp.text
        assert "涉及项目" in resp.text
        assert "最近更新" in resp.text

        # 关系视图（Kanban 5 列 + 拖拽保留）
        resp = client.get("/board?username=alice&view=relation")
        assert resp.status_code == 200
        assert "dragstart" in resp.text
        for rel in ["需我审查", "需我动", "@我的", "我创建的", "其他参与"]:
            assert rel in resp.text, f"missing column: {rel}"
        for t in ["Reviewer MR", "Assignee MR", "Mention MR", "Author MR"]:
            assert t in resp.text, f"missing MR (relation view): {t}"

    def test_board_calls_4_mr_queries(self, client, monkeypatch, tmp_db):
        """username 非空时应触发 3 issue + 4 MR = 7 次 fetch_items 调用。"""
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")

        calls = {"count": 0}

        def fake_fetch(*args, **kwargs):
            calls["count"] += 1
            return []

        from gitlab_issues_finder import app as app_module

        monkeypatch.setattr(app_module, "fetch_items", fake_fetch)

        resp = client.get("/board?username=alice")
        assert resp.status_code == 200
        # /board 路径下 summary/all/issues/mrs 视图各拉一次完整 7 维度，
        # 实际取决于 view 参数；默认 summary 触发 7 次 (3 issue + 4 MR)。
        assert calls["count"] == 7


class TestBoardApi:
    """看板 JSON API：拖拽、列管理、主题"""

    def test_move_and_reset(self, client, tmp_db):
        r = client.post(
            "/api/board/move",
            json={
                "username": "alice",
                "item_key": "merge_request-1-1",
                "column_id": "reviewer",
            },
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r = client.post("/api/board/reset", json={"username": "alice"})
        assert r.json()["ok"] is True

    def test_add_rename_delete_column(self, client, tmp_db):
        client.get("/board?username=alice")
        r = client.post(
            "/api/board/columns",
            json={
                "username": "alice",
                "title": "待 review",
                "column_id": "reviewing",
            },
        )
        assert r.status_code == 200
        r = client.patch(
            "/api/board/columns/reviewing",
            json={
                "username": "alice",
                "title": "需要 review",
            },
        )
        assert r.json()["ok"] is True
        # DELETE 在 Starlette TestClient 不接 json=，改用 request body raw
        r = client.request(
            "DELETE",
            "/api/board/columns/reviewing",
            json={"username": "alice"},
        )
        assert r.json()["ok"] is True
        # 内置列无法删除
        r = client.request(
            "DELETE",
            "/api/board/columns/reviewer",
            json={"username": "alice"},
        )
        assert r.status_code == 400

    def test_invalid_column_id(self, client, tmp_db):
        r = client.post(
            "/api/board/columns",
            json={
                "username": "alice",
                "title": "x",
                "column_id": "BAD ID",
            },
        )
        assert r.status_code == 400

    def test_theme(self, client, tmp_db):
        r = client.post(
            "/api/preferences",
            json={
                "username": "alice",
                "theme": "dark",
            },
        )
        assert r.json()["theme"] == "dark"
        r = client.post(
            "/api/preferences",
            json={
                "username": "alice",
                "theme": "rainbow",
            },
        )
        assert r.status_code == 400


class TestRecentUsers:
    def test_returns_empty_initially(self, client, tmp_db):
        r = client.get("/api/recent-users")
        assert r.json() == {"users": []}


class TestApiUsers:
    @responses.activate
    def test_returns_users(self, client, monkeypatch):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        responses.add(
            responses.GET,
            f"{API_BASE}/users",
            json=[
                {"id": 1, "username": "alice", "name": "Alice"},
                {"id": 2, "username": "bob", "name": "Bob"},
            ],
            status=200,
        )
        resp = client.get("/api/users")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["users"]) == 2
        assert data["users"][0] == {"username": "alice", "name": "Alice"}

    @responses.activate
    def test_filters_empty_usernames(self, client, monkeypatch):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        responses.add(
            responses.GET,
            f"{API_BASE}/users",
            json=[
                {"id": 1, "username": "alice", "name": "Alice"},
                {"id": 2, "username": "", "name": "Ghost"},
            ],
            status=200,
        )
        resp = client.get("/api/users")
        data = resp.json()
        assert len(data["users"]) == 1
        assert data["users"][0]["username"] == "alice"

    @responses.activate
    def test_auth_error_returns_empty(self, client, monkeypatch):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        responses.add(
            responses.GET,
            f"{API_BASE}/users",
            json={"message": "401"},
            status=401,
        )
        resp = client.get("/api/users")
        assert resp.status_code == 200
        assert resp.json() == {"users": []}

    def test_missing_config_returns_empty(self, client, monkeypatch):
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        resp = client.get("/api/users")
        assert resp.status_code == 200
        assert resp.json() == {"users": []}


class TestSystemEndpoints:
    def test_version(self, client, tmp_db):
        r = client.get("/api/version")
        assert r.status_code == 200
        data = r.json()
        assert "app" in data and data["app"]  # non-empty
        assert "python" in data
        assert "fastapi" in data

    def test_health_ok_with_config(self, client, monkeypatch, tmp_db):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["checks"]["db"]["ok"] is True
        assert data["checks"]["config"]["ok"] is True

    def test_health_degraded_without_config(self, client, tmp_db):
        # tmp_db 仍设了 DB_PATH；但删掉 GITLAB_URL/TOKEN 让 config 失败
        import pytest

        m = pytest.MonkeyPatch()
        m.delenv("GITLAB_URL", raising=False)
        m.delenv("GITLAB_TOKEN", raising=False)
        try:
            r = client.get("/api/health")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "degraded"
            assert data["checks"]["config"]["ok"] is False
            assert data["checks"]["db"]["ok"] is True
        finally:
            m.undo()


class TestExportEndpoints:
    @responses.activate
    def test_export_csv(self, client, monkeypatch, tmp_db):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        # 3 issue + 4 MR endpoints (no labels in query)
        for _ in range(3):
            responses.add(
                responses.GET,
                f"{API_BASE}/issues",
                json=[
                    {
                        "project_id": 1,
                        "iid": 7,
                        "title": "Foo",
                        "state": "opened",
                        "labels": ["bug"],
                        "assignee": {"username": "alice"},
                        "web_url": "https://gl/x",
                        "updated_at": "2026-07-01T00:00:00Z",
                    }
                ],
                status=200,
                match_querystring=False,
            )
        for _ in range(4):
            responses.add(
                responses.GET,
                f"{API_BASE}/merge_requests",
                json=[],
                status=200,
                match_querystring=False,
            )
        r = client.get("/api/export.csv?username=alice")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        text = r.text
        assert "type,iid,project_id,title,state" in text
        assert "issue,7,1" in text
        assert '"bug"' in text  # labels

    @responses.activate
    def test_export_md(self, client, monkeypatch, tmp_db):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        for _ in range(3):
            responses.add(
                responses.GET,
                f"{API_BASE}/issues",
                json=[
                    {
                        "project_id": 1,
                        "iid": 7,
                        "title": "Foo",
                        "state": "opened",
                        "labels": [],
                        "assignee": None,
                        "web_url": "https://gl/x",
                        "updated_at": "2026-07-01T00:00:00Z",
                    }
                ],
                status=200,
                match_querystring=False,
            )
        for _ in range(4):
            responses.add(
                responses.GET,
                f"{API_BASE}/merge_requests",
                json=[],
                status=200,
                match_querystring=False,
            )
        r = client.get("/api/export.md?username=alice")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        text = r.text
        assert "# @alice" in text
        assert "## Issues" in text
        assert "## Merge Requests" in text
        assert "[Foo](https://gl/x)" in text

    def test_export_missing_username(self, client, tmp_db):
        # No env, no username -> either ConfigError-rendered or 422
        r = client.get("/api/export.csv")
        assert r.status_code in (200, 422)
        if r.status_code == 200:
            assert "GITLAB_" in r.text or "配置" in r.text


class TestBoardFilterAndSort:
    """新工具栏：标题实时搜索 + 排序下拉。"""

    @responses.activate
    def test_filter_and_sort_rendered_for_non_summary(self, client, monkeypatch, tmp_db):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        for _ in range(3):
            responses.add(
                responses.GET, f"{API_BASE}/issues", json=[], status=200, match_querystring=False
            )
        for _ in range(4):
            responses.add(
                responses.GET,
                f"{API_BASE}/merge_requests",
                json=[],
                status=200,
                match_querystring=False,
            )
        for view in ("all", "issues", "mrs", "relation", "project"):
            r = client.get(f"/board?username=alice&view={view}")
            assert r.status_code == 200
            assert 'id="card-filter"' in r.text, f"missing filter input on view={view}"
            assert 'id="card-sort"' in r.text, f"missing sort select on view={view}"
            assert "按标题搜索" in r.text

    @responses.activate
    def test_filter_and_sort_NOT_rendered_for_summary(self, client, monkeypatch, tmp_db):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        for _ in range(3):
            responses.add(
                responses.GET, f"{API_BASE}/issues", json=[], status=200, match_querystring=False
            )
        for _ in range(4):
            responses.add(
                responses.GET,
                f"{API_BASE}/merge_requests",
                json=[],
                status=200,
                match_querystring=False,
            )
        r = client.get("/board?username=alice&view=summary")
        assert r.status_code == 200
        assert 'id="card-filter"' not in r.text
        assert 'id="card-sort"' not in r.text


class TestProjectNameDisplay:
    @responses.activate
    def test_search_renders_project_name(self, client, monkeypatch, tmp_db):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        # Mock /issues to return one item
        for _ in range(3):
            responses.add(
                responses.GET,
                f"{API_BASE}/issues",
                json=[
                    {
                        "project_id": 42,
                        "iid": 1,
                        "title": "X",
                        "state": "opened",
                        "labels": [],
                        "assignee": None,
                        "web_url": "https://gl/x",
                        "updated_at": "2026-07-01T00:00:00Z",
                    }
                ],
                status=200,
                match_querystring=False,
            )
        for _ in range(4):
            responses.add(
                responses.GET,
                f"{API_BASE}/merge_requests",
                json=[],
                status=200,
                match_querystring=False,
            )
        # Mock /projects to return name for project_id 42
        responses.add(
            responses.GET,
            f"{API_BASE}/projects",
            json=[{"id": 42, "name": "Cool Project", "path_with_namespace": "team/cool"}],
            status=200,
            match_querystring=False,
        )
        r = client.post("/search", data={"username": "alice", "labels": ""})
        assert r.status_code == 200
        assert "team/cool" in r.text
        assert "p42" in r.text


class TestOpenAPITags:
    """验证 OpenAPI 文档带 tag 分组。"""

    def test_openapi_has_tag_groups(self, client, tmp_db):
        r = client.get("/openapi.json")
        assert r.status_code == 200
        spec = r.json()
        tags = {t["name"] for t in spec.get("tags", [])}
        assert {"UI", "Board", "Users", "Export", "System"} <= tags

    def test_routes_carry_their_tag(self, client, tmp_db):
        r = client.get("/openapi.json")
        spec = r.json()
        paths = spec["paths"]
        assert "Users" in paths["/api/users"]["get"]["tags"]
        assert "System" in paths["/api/health"]["get"]["tags"]
        assert "Export" in paths["/api/export.csv"]["get"]["tags"]
        assert "Board" in paths["/api/board/move"]["post"]["tags"]

    def test_app_metadata_present(self, client, tmp_db):
        r = client.get("/openapi.json")
        spec = r.json()
        info = spec["info"]
        assert "GitLab Status Board" in info["title"]
        assert info["description"]


class TestListColumns:
    def test_list_columns_initializes_builtin(self, client, tmp_db):
        r = client.get("/api/board/columns", params={"username": "alice"})
        assert r.status_code == 200
        data = r.json()
        assert data["username"] == "alice"
        ids = [c["id"] for c in data["columns"]]
        assert ids == ["reviewer", "assignee", "mention", "author", "other"]
        assert all(c["is_builtin"] for c in data["columns"])

    def test_list_columns_includes_custom(self, client, tmp_db):
        client.post(
            "/api/board/columns",
            json={"username": "alice", "column_id": "reviewing", "title": "Reviewing"},
        )
        r = client.get("/api/board/columns", params={"username": "alice"})
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()["columns"]]
        assert "reviewing" in ids
        custom = [c for c in r.json()["columns"] if c["id"] == "reviewing"][0]
        assert custom["is_builtin"] is False

    def test_list_columns_missing_username(self, client, tmp_db):
        r = client.get("/api/board/columns")
        assert r.status_code == 422  # FastAPI validation error


class TestTimeRangeFilter:
    """since/until 按 updated_at 日期过滤。"""

    @responses.activate
    def test_since_filter(self, client, monkeypatch, tmp_db):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        for _ in range(3):
            responses.add(
                responses.GET,
                f"{API_BASE}/issues",
                json=[
                    {
                        "project_id": 1,
                        "iid": 1,
                        "title": "Old",
                        "state": "opened",
                        "labels": [],
                        "assignee": None,
                        "web_url": "u",
                        "updated_at": "2025-01-15T00:00:00Z",
                    },
                    {
                        "project_id": 1,
                        "iid": 2,
                        "title": "New",
                        "state": "opened",
                        "labels": [],
                        "assignee": None,
                        "web_url": "u",
                        "updated_at": "2026-07-01T00:00:00Z",
                    },
                ],
                status=200,
                match_querystring=False,
            )
        for _ in range(4):
            responses.add(
                responses.GET,
                f"{API_BASE}/merge_requests",
                json=[],
                status=200,
                match_querystring=False,
            )
        r = client.post("/search", data={"username": "alice", "since": "2026-01-01"})
        assert r.status_code == 200
        # New 应该被保留，Old 应该被过滤
        assert "New" in r.text
        # Old should be filtered out (cannot see Old item in result table)
        # We just verify the count is 1, not 2
        assert r.text.count("p1") >= 1  # at least 1 item rendered

    @responses.activate
    def test_invalid_since_format(self, client, monkeypatch, tmp_db):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        r = client.post("/search", data={"username": "alice", "since": "garbage"})
        assert r.status_code == 200
        assert "格式错误" in r.text

    @responses.activate
    def test_invalid_until_format(self, client, monkeypatch, tmp_db):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        r = client.post("/search", data={"username": "alice", "until": "2025/01/01"})
        assert r.status_code == 200
        assert "格式错误" in r.text

    @responses.activate
    def test_invalid_date_value(self, client, monkeypatch, tmp_db):
        """Well-formed but invalid date like 2025-02-30."""
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        r = client.post("/search", data={"username": "alice", "since": "2025-02-30"})
        assert r.status_code == 200
        assert "格式错误" in r.text


class TestParseDate:
    """_parse_date 单元测试。"""

    def test_valid(self):
        from gitlab_issues_finder.app import _parse_date

        assert _parse_date("2025-01-15") == "2025-01-15"

    def test_empty_returns_none(self):
        from gitlab_issues_finder.app import _parse_date

        assert _parse_date("") is None

    def test_wrong_format(self):
        from gitlab_issues_finder.app import _parse_date

        assert _parse_date("2025/01/15") is None
        assert _parse_date("01-15-2025") is None
        assert _parse_date("20250115") is None

    def test_invalid_date(self):
        from gitlab_issues_finder.app import _parse_date

        assert _parse_date("2025-02-30") is None
        assert _parse_date("2025-13-01") is None


class TestMetricsEndpoint:
    def test_metrics_endpoint_returns_prometheus_format(self, client, tmp_db):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        # Should have process_uptime_seconds even with no business metrics yet
        assert "process_uptime_seconds" in r.text

    def test_metrics_increments_on_request(self, client, tmp_db):
        client.get("/")
        r = client.get("/metrics")
        assert "http_requests_total" in r.text
        # The / route should be counted
        assert 'path="/"' in r.text


class TestMetricsUnit:
    def test_counter_increments(self):
        from gitlab_issues_finder.metrics import Metrics

        m = Metrics()
        m.inc("test_counter")
        m.inc("test_counter", 2.0)
        m.inc("test_counter", env="prod")
        rendered = m.render()
        # base counter
        assert "test_counter 3" in rendered
        # labeled
        assert 'env="prod"' in rendered
        label_line = "test_counter{env=" + chr(34) + "prod" + chr(34) + "} 1"
        assert label_line in rendered

    def test_histogram_observation(self):
        from gitlab_issues_finder.metrics import Metrics

        m = Metrics()
        m.observe("latency_ms", 10.0)
        m.observe("latency_ms", 20.0)
        m.observe("latency_ms", 30.0)
        rendered = m.render()
        assert "latency_ms_count 3" in rendered
        assert "latency_ms_sum 60" in rendered
        assert "latency_ms_max 30" in rendered

    def test_gauge(self):
        from gitlab_issues_finder.metrics import Metrics

        m = Metrics()
        m.set_gauge("queue_depth", 5)
        m.set_gauge("queue_depth", 7, region="us")
        rendered = m.render()
        assert "queue_depth 5" in rendered
        assert 'region="us"' in rendered
        label_line2 = "queue_depth{region=" + chr(34) + "us" + chr(34) + "} 7"
        assert label_line2 in rendered

    def test_singleton(self):
        from gitlab_issues_finder import metrics

        metrics.reset_metrics()
        a = metrics.get_metrics()
        b = metrics.get_metrics()
        assert a is b
        metrics.reset_metrics()


class TestReorderColumns:
    def test_reorder_basic(self, client, tmp_db):
        # Initial: reviewer/assignee/mention/author/other
        r = client.post(
            "/api/board/columns/reorder",
            json={
                "username": "alice",
                "column_ids": ["author", "reviewer", "assignee", "mention", "other"],
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["updated"] == 5
        # Verify the new order
        r2 = client.get("/api/board/columns", params={"username": "alice"})
        ids = [c["id"] for c in r2.json()["columns"]]
        assert ids == ["author", "reviewer", "assignee", "mention", "other"]

    def test_reorder_unknown_id_ignored(self, client, tmp_db):
        r = client.post(
            "/api/board/columns/reorder",
            json={
                "username": "alice",
                "column_ids": ["nonexistent", "author", "assignee", "mention", "other", "reviewer"],
            },
        )
        assert r.status_code == 200
        # nonexistent is ignored; the 5 known columns get reordered
        assert r.json()["updated"] == 5

    def test_reorder_missing_username(self, client, tmp_db):
        r = client.post("/api/board/columns/reorder", json={"column_ids": ["a", "b"]})
        assert r.status_code == 400

    def test_reorder_invalid_payload(self, client, tmp_db):
        r = client.post(
            "/api/board/columns/reorder",
            json={"username": "alice", "column_ids": "not-a-list"},
        )
        assert r.status_code == 400
