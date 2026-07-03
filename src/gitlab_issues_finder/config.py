"""配置加载：从 .env 文件或环境变量构建 AppConfig。

优先级：环境变量 > .env 文件 > 代码内置默认值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from gitlab_issues_finder.errors import ConfigError

# 加载项目根目录下的 .env（若不存在则静默忽略）
load_dotenv()

SslVerify = bool | str


def _parse_ssl(raw: str | None) -> SslVerify:
    """解析 GITLAB_SSL_VERIFY。

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


@dataclass(frozen=True)
class AppConfig:
    """应用配置（不可变）。"""

    url: str
    token: str
    ssl_verify: SslVerify
    timeout: int
    web_host: str
    web_port: int
    page_size: int = field(default=100)
    db_path: str = field(default="data/app.db")

    @classmethod
    def from_env(cls) -> AppConfig:
        url = os.environ.get("GITLAB_URL", "").rstrip("/")
        token = os.environ.get("GITLAB_TOKEN", "")

        if not url:
            raise ConfigError(
                "GITLAB_URL 未设置。请在 .env 文件或环境变量中配置。参考 .env.example。"
            )
        if not token:
            raise ConfigError(
                "GITLAB_TOKEN 未设置。请在 .env 文件或环境变量中配置。Token 需要 read_api scope。"
            )

        try:
            timeout = int(os.environ.get("GITLAB_TIMEOUT", "30"))
            web_port = int(os.environ.get("WEB_PORT", "8000"))
            page_size = int(os.environ.get("PAGE_SIZE", "100"))
        except ValueError as e:
            raise ConfigError(f"数值型配置解析失败：{e}") from e

        if page_size < 1 or page_size > 100:
            raise ConfigError("PAGE_SIZE 必须在 1-100 之间（GitLab API 上限）。")

        return cls(
            url=url,
            token=token,
            ssl_verify=_parse_ssl(os.environ.get("GITLAB_SSL_VERIFY", "true")),
            timeout=timeout,
            web_host=os.environ.get("WEB_HOST", "127.0.0.1"),
            web_port=web_port,
            page_size=page_size,
            db_path=os.environ.get("DB_PATH", "data/app.db"),
        )
