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
