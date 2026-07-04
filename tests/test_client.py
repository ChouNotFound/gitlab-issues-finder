"""client.py 单元测试。

client.py 现在用 ``requests`` + 自研极简 ``GitlabClient`` 替代 python-gitlab。
所有测试通过 ``responses`` 拦截底层 ``requests`` 调用。
"""

from __future__ import annotations

import pytest
import requests
import responses

from gitlab_issues_finder.client import GitlabClient, build_client, safe_http_get
from gitlab_issues_finder.config import AppConfig
from gitlab_issues_finder.errors import (
    AuthError,
    GitlabTimeoutError,
    GitlabUnavailableError,
    NotFoundError,
    RateLimitError,
)

API_BASE = "https://gitlab.test/api/v4"


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig(
        url="https://gitlab.test",
        token="glpat-test",
        ssl_verify=True,
        timeout=30,
        web_host="127.0.0.1",
        web_port=8000,
        page_size=100,
    )


@pytest.fixture
def client() -> GitlabClient:
    return GitlabClient(url="https://gitlab.test", token="x")


class TestBuildClient:
    def test_returns_client_without_making_requests(self, cfg):
        """build_client 不应主动发起任何 HTTP 请求 (token 错误延迟暴露)。"""
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rmock:
            gl = build_client(cfg)
            assert isinstance(gl, GitlabClient)
            assert len(rmock.calls) == 0

    def test_url_stored(self, cfg):
        gl = build_client(cfg)
        assert gl.url == cfg.url

    def test_token_stored_in_session_headers(self, cfg):
        gl = build_client(cfg)
        assert gl.session.headers["PRIVATE-TOKEN"] == cfg.token

    def test_url_trailing_slash_stripped(self, cfg):
        cfg2 = AppConfig(
            url="https://gitlab.test/",  # 末尾有 /
            token="x", ssl_verify=True, timeout=30,
            web_host="127.0.0.1", web_port=8000, page_size=100,
        )
        gl = build_client(cfg2)
        assert gl.url == "https://gitlab.test"


class TestSafeHttpGetErrors:
    """http_get 把 HTTP 状态码映射为应用层异常。"""

    @responses.activate
    def test_401_raises_auth_error(self, client):
        responses.add(
            responses.GET, f"{API_BASE}/user",
            json={"message": "401 Unauthorized"}, status=401,
        )
        with pytest.raises(AuthError, match="401"):
            client.http_get("/user")

    @responses.activate
    def test_403_raises_auth_error(self, client):
        responses.add(
            responses.GET, f"{API_BASE}/user",
            json={"message": "403 Forbidden"}, status=403,
        )
        with pytest.raises(AuthError, match="访问权限"):
            client.http_get("/user")

    @responses.activate
    def test_404_raises_not_found(self, client):
        responses.add(
            responses.GET, f"{API_BASE}/projects/9999",
            json={"message": "404 Project Not Found"}, status=404,
        )
        with pytest.raises(NotFoundError, match="404"):
            client.http_get("/projects/9999")

    @responses.activate
    def test_429_raises_rate_limit(self, client):
        responses.add(
            responses.GET, f"{API_BASE}/issues",
            json={"message": "429"}, status=429,
        )
        with pytest.raises(RateLimitError, match="限流"):
            client.http_get("/issues")

    @responses.activate
    def test_500_raises_unavailable(self, client):
        responses.add(
            responses.GET, f"{API_BASE}/user",
            json={"message": "ISE"}, status=500,
        )
        with pytest.raises(GitlabUnavailableError):
            client.http_get("/user")

    @responses.activate
    def test_502_raises_unavailable(self, client):
        responses.add(
            responses.GET, f"{API_BASE}/user",
            json={"message": "Bad Gateway"}, status=502,
        )
        with pytest.raises(GitlabUnavailableError):
            client.http_get("/user")

    @responses.activate
    def test_timeout_raises_timeout(self, client):
        def timeout_cb(request):
            raise requests.exceptions.Timeout("simulated")

        responses.add_callback(
            responses.GET, f"{API_BASE}/user", callback=timeout_cb,
        )
        with pytest.raises(GitlabTimeoutError):
            client.http_get("/user")

    @responses.activate
    def test_ssl_error_raises_unavailable(self, client):
        def ssl_cb(request):
            raise requests.exceptions.SSLError("cert verify failed")

        responses.add_callback(
            responses.GET, f"{API_BASE}/user", callback=ssl_cb,
        )
        with pytest.raises(GitlabUnavailableError, match="SSL"):
            client.http_get("/user")

    @responses.activate
    def test_connection_error_raises_unavailable(self, client):
        def cb(request):
            raise requests.exceptions.ConnectionError("refused")

        responses.add_callback(
            responses.GET, f"{API_BASE}/user", callback=cb,
        )
        with pytest.raises(GitlabUnavailableError, match="无法连接"):
            client.http_get("/user")

    @responses.activate
    def test_success_returns_json(self, client):
        responses.add(
            responses.GET, f"{API_BASE}/issues",
            json=[{"id": 1}], status=200,
        )
        result = client.http_get("/issues", page=1, per_page=20)
        assert result == [{"id": 1}]

    @responses.activate
    def test_non_json_response_raises_unavailable(self, client):
        responses.add(
            responses.GET, f"{API_BASE}/user",
            body="<html>not json</html>", status=200,
        )
        with pytest.raises(GitlabUnavailableError, match="非 JSON"):
            client.http_get("/user")

    @responses.activate
    def test_query_params_passed_through(self, client):
        """回归: query_data 必须正确拼到 URL 上。"""
        responses.add(
            responses.GET, f"{API_BASE}/issues",
            json=[], status=200, match_querystring=False,
        )
        client.http_get("/issues", assignee_username="alice", state="opened")
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(responses.calls[0].request.url).query)
        assert qs["assignee_username"] == ["alice"]
        assert qs["state"] == ["opened"]


class TestSafeHttpGetAlias:
    """safe_http_get 保留为 GitlabClient.http_get 的别名, 不破坏旧调用方。"""

    @responses.activate
    def test_safe_http_get_delegates_to_client(self, client):
        responses.add(
            responses.GET, f"{API_BASE}/issues",
            json=[{"id": 1}], status=200,
        )
        result = safe_http_get(client, "/issues")
        assert result == [{"id": 1}]


class TestSslVerify:
    def test_ssl_verify_bool(self, cfg):
        cfg2 = AppConfig(
            url="https://gl", token="x", ssl_verify=False, timeout=30,
            web_host="127.0.0.1", web_port=8000, page_size=100,
        )
        gl = build_client(cfg2)
        assert gl.session.verify is False

    def test_ssl_verify_path(self, cfg):
        cfg2 = AppConfig(
            url="https://gl", token="x", ssl_verify="/path/to/ca.crt", timeout=30,
            web_host="127.0.0.1", web_port=8000, page_size=100,
        )
        gl = build_client(cfg2)
        assert gl.session.verify == "/path/to/ca.crt"
