"""自定义异常层级。

Web 层通过注册 AppError 处理器统一转为友好错误页。
"""

from __future__ import annotations


class AppError(Exception):
    """所有应用层异常的基类。"""


class ConfigError(AppError):
    """配置缺失或非法（如缺少 GITLAB_URL / GITLAB_TOKEN）。"""


class AuthError(AppError):
    """Token 无效、过期或权限不足（401 / 403）。"""


class GitlabUnavailableError(AppError):
    """GitLab 实例不可达或返回 5xx。"""


class GitlabTimeoutError(AppError):
    """请求 GitLab 超时。"""

class RateLimitError(GitlabUnavailableError):
    """GitLab 实例触发 rate limit (HTTP 429)。"""



class NotFoundError(AppError):
    """资源不存在 (HTTP 404)，如 project_id 不存在或被删除。"""
