"""client.py 单元测试。

注：build_client() 现在不主动调用 auth()，认证错误由 safe_http_get() 在
第一次实际请求时捕获并映射。AuthError / GitlabUnavailableError / Timeout
等异常映射的测试覆盖在 safe_http_get 路径上，并通过 test_app.py 端到端验证。
"""

from __future__ import annotations

import gitlab
import pytest
import requests
import responses

from gitlab_issues_finder.client import build_client, safe_http_get
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


class TestBuildClient:
    def test_returns_client_without_making_requests(self, cfg):
        """build_client 不应主动发起任何 HTTP 请求（token 错误延迟暴露）。"""
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rmock:
            gl = build_client(cfg)
            assert gl is not None
            assert len(rmock.calls) == 0

    def test_url_stored(self, cfg):
        gl = build_client(cfg)
        assert cfg.url in gl._url  # type: ignore[attr-defined]

    def test_token_stored(self, cfg):
        gl = build_client(cfg)
        assert gl.private_token == cfg.token


class TestSafeHttpGetErrors:
    """safe_http_get 把底层异常映射为应用层异常。"""

    @pytest.fixture
    def gl(self) -> gitlab.Gitlab:
        return gitlab.Gitlab(url="https://gitlab.test", private_token="x")

    @responses.activate
    def test_401_raises_auth_error(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/user",
            json={"message": "401 Unauthorized"},
            status=401,
        )
        with pytest.raises(AuthError, match="Token 认证失败"):
            safe_http_get(gl, "/user")

    @responses.activate
    def test_403_raises_auth_error(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/user",
            json={"message": "403 Forbidden"},
            status=403,
        )
        with pytest.raises(AuthError, match="访问权限"):
            safe_http_get(gl, "/user")

    @responses.activate
    def test_500_raises_unavailable(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/user",
            json={"message": "Internal Server Error"},
            status=500,
        )
        with pytest.raises(GitlabUnavailableError):
            safe_http_get(gl, "/user")

    @responses.activate
    def test_404_raises_not_found(self, gl):
        """404 单独映射为 NotFoundError, 区别于通用 GitlabUnavailableError。"""
        responses.add(
            responses.GET,
            f"{API_BASE}/projects/9999",
            json={"message": "404 Project Not Found"},
            status=404,
        )
        with pytest.raises(NotFoundError, match="404"):
            safe_http_get(gl, "/projects/9999")

    @responses.activate
    def test_429_raises_rate_limit(self, gl):
        """429 映射为 RateLimitError, 不被 python-gitlab 内部 sleep+retry 吞掉。

        这里特意用 ``assert_all_requests_are_fired=False`` 因为我们只注册了
        一次响应, 期望 429 立刻抛出 (而非 10 次重试)。
        """
        responses.add(
            responses.GET,
            f"{API_BASE}/issues",
            json={"message": "429 Too Many Requests"},
            status=429,
        )
        with pytest.raises(RateLimitError, match="限流"):
            safe_http_get(gl, "/issues")
        # 验证没有触发重试: 一次响应就抛错
        assert len(responses.calls) == 1

    @responses.activate
    def test_timeout_raises_timeout(self, gl):
        def timeout_callback(request):
            raise requests.exceptions.Timeout("simulated timeout")

        responses.add_callback(
            responses.GET,
            f"{API_BASE}/user",
            callback=timeout_callback,
        )
        with pytest.raises(GitlabTimeoutError):
            safe_http_get(gl, "/user")

    @responses.activate
    def test_success(self, gl):
        responses.add(
            responses.GET,
            f"{API_BASE}/issues",
            json=[{"id": 1}],
            status=200,
        )
        result = safe_http_get(gl, "/issues", page=1, per_page=20)
        assert result == [{"id": 1}]


class TestSslVerify:
    def test_ssl_verify_bool(self):
        cfg = AppConfig(
            url="https://gl",
            token="x",
            ssl_verify=False,
            timeout=30,
            web_host="127.0.0.1",
            web_port=8000,
            page_size=100,
        )
        gl = build_client(cfg)
        # python-gitlab 4.x 把 ssl_verify 存到自身属性
        assert gl.ssl_verify is False

    def test_ssl_verify_path(self):
        cfg = AppConfig(
            url="https://gl",
            token="x",
            ssl_verify="/path/to/ca.crt",
            timeout=30,
            web_host="127.0.0.1",
            web_port=8000,
            page_size=100,
        )
        gl = build_client(cfg)
        assert gl.ssl_verify == "/path/to/ca.crt"
