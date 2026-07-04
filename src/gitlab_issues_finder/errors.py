"""自定义异常层级。

Web 层通过注册 AppError 处理器统一转为友好错误页。
"""

from __future__ import annotations


class AppError(Exception):
    """所有应用层异常的基类。"""


class ConfigError(AppError):
    """配置缺失或非法 (如缺少 GITLAB_URL / GITLAB_TOKEN)。

    可选 ``hint`` 属性: 触发该错误的修复建议, 启动器会优先展示。
    """
    hint: str | None = None

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


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
