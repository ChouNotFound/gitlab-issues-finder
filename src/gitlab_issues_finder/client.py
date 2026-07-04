"""GitLab 客户端构造与异常映射。

将 python-gitlab / requests 抛出的各类异常归一化为应用层 AppError 子类。
"""

from __future__ import annotations

import gitlab
import gitlab.exceptions
import requests.exceptions

from gitlab_issues_finder.config import AppConfig
from gitlab_issues_finder.errors import (
    AuthError,
    GitlabTimeoutError,
    GitlabUnavailableError,
    NotFoundError,
    RateLimitError,
)


def build_client(cfg: AppConfig) -> gitlab.Gitlab:
    """构造 python-gitlab 客户端。

    注：不在此处调用 gl.auth() 触发 /user。
    - 优点：避免 python-gitlab 4.13 在 Python 3.14 上的 RESTObject 初始化 bug；
      首页不会因 token 错误而无法访问，UX 更友好。
    - 缺点：token 错误要等到第一次实际查询才暴露，但 safe_http_get() 会把它
      映射为 AuthError 并在错误页中显示。
    """
    return gitlab.Gitlab(
        url=cfg.url,
        private_token=cfg.token,
        ssl_verify=cfg.ssl_verify,
        timeout=cfg.timeout,
        api_version="4",
    )


def safe_http_get(gl: gitlab.Gitlab, path: str, **query_data):
    """调用 gl.http_get 并将网络层异常映射为应用层异常。

    用于 queries.py 中所有主动发起的 HTTP 请求（如分页拉取 /issues）。

    映射规则:
      - 401 / 403  -> AuthError
      - 404        -> NotFoundError (资源不存在)
      - 429        -> RateLimitError
      - 5xx        -> GitlabUnavailableError
      - SSL / 连接 / 超时 -> 对应专项异常

    注: ``obey_rate_limit=False`` + ``retry_transient_errors=False`` 让 429 / 5xx
    立刻浮出, 而不是被 python-gitlab 默认的 max_retries=10 sleep+retry 吞掉。
    """
    try:
        # ``obey_rate_limit=False`` 阻止 python-gitlab 自带的 429 sleep+retry 循环
        # (默认 max_retries=10, 一次 rate limit 可能阻塞数十秒), 让 429 立刻
        # 浮上来交由 RateLimitError 处理。
        # ``retry_transient_errors=False`` 同样避免 5xx 反复重试吞噬时间。
        return gl.http_get(
            path,
            query_data=query_data,
            obey_rate_limit=False,
            retry_transient_errors=False,
        )
    except gitlab.exceptions.GitlabAuthenticationError as e:
        raise AuthError(f"Token 认证失败：{e}") from e
    except gitlab.exceptions.GitlabError as e:
        code = getattr(e, "response_code", None)
        if code == 403:
            raise AuthError("Token 没有访问权限。请确认 scope 包含 read_api。") from e
        if code == 404:
            raise NotFoundError(f"资源不存在 (HTTP 404): {path}") from e
        if code == 429:
            # 注: python-gitlab 的 GitlabError 不暴露 response headers,
            # 因此 Retry-After 无法透传。调用方可以再次刷新尝试 (受
            # 应用层 rate_limit.py 控制频率)。
            raise RateLimitError(
                "GitLab 触发限流 (HTTP 429)。稍等片刻再试或调高 RATE_LIMIT_RPM。"
            ) from e
        raise GitlabUnavailableError(f"GitLab 返回 HTTP {code or '?'}：{e}") from e
    except requests.exceptions.SSLError as e:
        raise GitlabUnavailableError(
            f"SSL 证书校验失败：{e}（可在 .env 设置 GITLAB_SSL_VERIFY=false）"
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise GitlabUnavailableError(f"无法连接 GitLab：{e}") from e
    except requests.exceptions.Timeout as e:
        raise GitlabTimeoutError(f"请求 GitLab 超时：{e}") from e
