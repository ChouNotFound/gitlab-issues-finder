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
    """
    try:
        return gl.http_get(path, query_data=query_data)
    except gitlab.exceptions.GitlabAuthenticationError as e:
        raise AuthError(f"Token 认证失败：{e}") from e
    except gitlab.exceptions.GitlabError as e:
        code = getattr(e, "response_code", None)
        if code == 403:
            raise AuthError("Token 没有访问权限。请确认 scope 包含 read_api。") from e
        raise GitlabUnavailableError(f"GitLab 返回 HTTP {code or '?'}：{e}") from e
    except requests.exceptions.SSLError as e:
        raise GitlabUnavailableError(
            f"SSL 证书校验失败：{e}（可在 .env 设置 GITLAB_SSL_VERIFY=false）"
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise GitlabUnavailableError(f"无法连接 GitLab：{e}") from e
    except requests.exceptions.Timeout as e:
        raise GitlabTimeoutError(f"请求 GitLab 超时：{e}") from e