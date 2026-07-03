"""Typed application configuration via pydantic-settings.

设计要点：
  - 所有配置走 ``AppSettings(BaseSettings)``：从环境变量 / .env 自动加载。
  - 必填项 ``GITLAB_URL`` / ``GITLAB_TOKEN`` 通过 model_validator 检查；
    缺失时抛 ``ConfigError``，与历史行为兼容。
  - SSL 接受 ``true/false/1/0/yes/no`` 解析为 bool；其他字符串视作 CA bundle 路径。
  - 数值类型（PAGE_SIZE / WEB_PORT / GITLAB_TIMEOUT）有范围约束。
  - ``AppConfig`` 保持为对外数据类，构造时由 ``AppSettings.model_validate()``
    转换而来，保证调用方零迁移成本。

优先级：环境变量 > .env 文件 > 字段默认值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from gitlab_issues_finder.errors import ConfigError

# 加载项目根目录下的 .env（若不存在则静默忽略）
load_dotenv()

SslVerify = bool | str


def _parse_ssl(raw: str | None) -> SslVerify:
    """解析 ``GITLAB_SSL_VERIFY``。

    接受：true/false/1/0/yes/no（大小写不敏感）→ bool；
    其他任意字符串视作 CA bundle 文件路径。
    """
    if raw is None or raw == "":
        return True
    lowered = raw.strip().lower()
    if lowered in ("true", "1", "yes"):
        return True
    if lowered in ("false", "0", "no"):
        return False
    return raw  # 当作 CA bundle 路径


class AppSettings(BaseSettings):
    """应用配置（pydantic-settings）。从 ``GITLAB_*`` / ``WEB_*`` / ``PAGE_SIZE`` / ``DB_PATH`` 自动加载。

    ``__init__`` 会把 pydantic 抛出的 ``ValidationError``（字段约束、必填项）
    统一映射为 ``ConfigError``，让上层调用方只需捕获一个异常类型。
    """

    def __init__(self, **kwargs: Any) -> None:
        try:
            super().__init__(**kwargs)
        except ValidationError as e:
            # 第一个错误的字段名 + 简短信息
            errs = e.errors()
            field = ".".join(str(p) for p in errs[0]["loc"]) if errs else "config"
            msg = errs[0]["msg"] if errs else str(e)
            raise ConfigError(f"配置错误 [{field}]: {msg}") from e

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- GitLab ----
    gitlab_url: str = Field(default="", alias="GITLAB_URL")
    gitlab_token: str = Field(default="", alias="GITLAB_TOKEN")
    gitlab_ssl_verify: str = Field(default="true", alias="GITLAB_SSL_VERIFY")
    gitlab_timeout: int = Field(default=30, alias="GITLAB_TIMEOUT", ge=1)

    # ---- Web ----
    web_host: str = Field(default="127.0.0.1", alias="WEB_HOST")
    web_port: int = Field(default=8000, alias="WEB_PORT", ge=1, le=65535)

    # ---- App ----
    page_size: int = Field(default=100, alias="PAGE_SIZE", ge=1, le=100)
    db_path: str = Field(default="data/app.db", alias="DB_PATH")

    @field_validator("gitlab_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("gitlab_ssl_verify", mode="before")
    @classmethod
    def _coerce_ssl(cls, v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    @model_validator(mode="after")
    def _check_required(self) -> AppSettings:
        if not self.gitlab_url:
            raise ConfigError(
                "GITLAB_URL 未设置。请在 .env 文件或环境变量中配置。参考 .env.example。"
            )
        if not self.gitlab_token:
            raise ConfigError(
                "GITLAB_TOKEN 未设置。请在 .env 文件或环境变量中配置。Token 需要 read_api scope。"
            )
        return self


@dataclass(frozen=True)
class AppConfig:
    """应用配置（不可变，兼容旧 API）。"""

    url: str
    token: str
    ssl_verify: SslVerify
    timeout: int
    web_host: str
    web_port: int
    page_size: int = 100
    db_path: str = "data/app.db"

    @classmethod
    def from_env(cls) -> AppConfig:
        """从环境构建 AppConfig。配置错误抛 ConfigError。"""
        try:
            settings = AppSettings()
        except Exception as e:
            # pydantic 抛 ValidationError；映射为 ConfigError
            if isinstance(e, ConfigError):
                raise
            raise ConfigError(f"配置解析失败：{e}") from e
        return cls(
            url=settings.gitlab_url,
            token=settings.gitlab_token,
            ssl_verify=_parse_ssl(settings.gitlab_ssl_verify),
            timeout=settings.gitlab_timeout,
            web_host=settings.web_host,
            web_port=settings.web_port,
            page_size=settings.page_size,
            db_path=settings.db_path,
        )
