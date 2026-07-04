"""GitLab 客户端构造与异常映射。

使用 ``requests`` 直接调用 GitLab REST API, 不再依赖 ``python-gitlab``。
理由参见 ``docs/TECH_STACK.md``。
"""

from __future__ import annotations

import requests

from gitlab_issues_finder.config import AppConfig
from gitlab_issues_finder.errors import (
    AuthError,
    GitlabTimeoutError,
    GitlabUnavailableError,
    NotFoundError,
    RateLimitError,
)


class GitlabClient:
    """极简 GitLab REST 客户端。

    仅提供 ``http_get`` (后续可加 http_post 等), 内部使用 ``requests.Session``
    复用连接 + 自动应用 ``PRIVATE-TOKEN`` 头。
    """

    def __init__(self, url: str, token: str, *, ssl_verify: bool | str = True,
                 timeout: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["PRIVATE-TOKEN"] = token
        self.session.verify = ssl_verify
        self.timeout = timeout

    def http_get(self, path: str, **query_data):
        """GET ``{url}/api/v4{path}?{query_data}`` 并返回 JSON 解析结果。"""
        # 路径: 接受 ``/issues`` 或 ``projects/X/issues`` 形式, 统一补 /api/v4 前缀
        full = f"{self.url}/api/v4{path}" if path.startswith("/") else f"{self.url}/api/v4/{path}"
        try:
            resp = self.session.get(
                full,
                params=query_data or None,
                timeout=self.timeout,
            )
        except requests.exceptions.SSLError as e:
            raise GitlabUnavailableError(
                f"SSL 证书校验失败：{e}（可在 .env 设置 GITLAB_SSL_VERIFY=false）"
            ) from e
        except requests.exceptions.ConnectionError as e:
            raise GitlabUnavailableError(f"无法连接 GitLab：{e}") from e
        except requests.exceptions.Timeout as e:
            raise GitlabTimeoutError(f"请求 GitLab 超时：{e}") from e

        # 状态码映射
        if resp.status_code == 401:
            raise AuthError("Token 认证失败：401 Unauthorized")
        if resp.status_code == 403:
            raise AuthError("Token 没有访问权限。请确认 scope 包含 read_api。")
        if resp.status_code == 404:
            raise NotFoundError(f"资源不存在 (HTTP 404): {path}")
        if resp.status_code == 429:
            raise RateLimitError(
                "GitLab 触发限流 (HTTP 429)。稍等片刻再试或调高 RATE_LIMIT_RPM。"
            )
        if 500 <= resp.status_code < 600:
            raise GitlabUnavailableError(
                f"GitLab 返回 HTTP {resp.status_code}：{resp.text[:200]}"
            )

        # 2xx: 让 requests 自己抛非 2xx (不会到这里) 或返回 JSON
        try:
            return resp.json()
        except ValueError as e:
            raise GitlabUnavailableError(
                f"GitLab 返回非 JSON (HTTP {resp.status_code}): {resp.text[:200]}"
            ) from e


def build_client(cfg: AppConfig) -> GitlabClient:
    """构造 GitLab REST 客户端。

    不主动触发 /user, 认证错误延迟到第一次实际请求时由 ``http_get`` 抛出。
    """
    return GitlabClient(
        url=cfg.url,
        token=cfg.token,
        ssl_verify=cfg.ssl_verify,
        timeout=cfg.timeout,
    )


def safe_http_get(client: GitlabClient, path: str, **query_data):
    """``GitlabClient.http_get`` 的语义化别名, 保留旧导入路径。

    旧版 python-gitlab 在这里做的异常映射已内联到 ``GitlabClient.http_get``。
    """
    return client.http_get(path, **query_data)
