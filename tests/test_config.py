"""config.py 单元测试。"""

from __future__ import annotations

import pytest

from gitlab_issues_finder.config import AppConfig, _parse_ssl
from gitlab_issues_finder.errors import ConfigError


class TestParseSsl:
    def test_true(self):
        assert _parse_ssl("true") is True
        assert _parse_ssl("TRUE") is True
        assert _parse_ssl("1") is True
        assert _parse_ssl("yes") is True

    def test_false(self):
        assert _parse_ssl("false") is False
        assert _parse_ssl("FALSE") is False
        assert _parse_ssl("0") is False
        assert _parse_ssl("no") is False

    def test_default_true(self):
        assert _parse_ssl(None) is True
        assert _parse_ssl("") is True

    def test_path(self):
        assert _parse_ssl("C:\\certs\\ca.crt") == "C:\\certs\\ca.crt"
        assert _parse_ssl("/etc/ssl/ca-bundle.pem") == "/etc/ssl/ca-bundle.pem"


class TestAppConfigFromEnv:
    @staticmethod
    def _disable_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
        from gitlab_issues_finder import config as config_module

        class _NoDotenvAppSettings(config_module.AppSettings):
            def __init__(self, **kwargs):
                kwargs.setdefault("_env_file", None)
                super().__init__(**kwargs)

        monkeypatch.setattr(config_module, "AppSettings", _NoDotenvAppSettings)

    def test_minimal_required(self, fake_env):
        cfg = AppConfig.from_env()
        assert cfg.url == "https://gitlab.test"
        assert cfg.token == "glpat-test-token"
        assert cfg.ssl_verify is True
        assert cfg.timeout == 30
        assert cfg.web_host == "127.0.0.1"
        assert cfg.web_port == 8000
        assert cfg.page_size == 100

    def test_url_trailing_slash_stripped(self, monkeypatch, fake_env):
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test/")
        cfg = AppConfig.from_env()
        assert cfg.url == "https://gitlab.test"

    def test_missing_url(self, clean_env, monkeypatch):
        self._disable_dotenv(monkeypatch)
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        with pytest.raises(ConfigError, match="GITLAB_URL"):
            AppConfig.from_env()

    def test_missing_token(self, clean_env, monkeypatch):
        self._disable_dotenv(monkeypatch)
        monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
        with pytest.raises(ConfigError, match="GITLAB_TOKEN"):
            AppConfig.from_env()

    def test_custom_values(self, monkeypatch):
        monkeypatch.setenv("GITLAB_URL", "https://gl")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        monkeypatch.setenv("GITLAB_SSL_VERIFY", "false")
        monkeypatch.setenv("GITLAB_TIMEOUT", "60")
        monkeypatch.setenv("WEB_HOST", "0.0.0.0")
        monkeypatch.setenv("WEB_PORT", "9000")
        monkeypatch.setenv("PAGE_SIZE", "50")
        cfg = AppConfig.from_env()
        assert cfg.ssl_verify is False
        assert cfg.timeout == 60
        assert cfg.web_host == "0.0.0.0"
        assert cfg.web_port == 9000
        assert cfg.page_size == 50

    def test_page_size_cap(self, monkeypatch):
        monkeypatch.setenv("GITLAB_URL", "https://gl")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        monkeypatch.setenv("PAGE_SIZE", "999")
        with pytest.raises(ConfigError, match="PAGE_SIZE"):
            AppConfig.from_env()

    def test_invalid_number(self, monkeypatch):
        monkeypatch.setenv("GITLAB_URL", "https://gl")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        monkeypatch.setenv("GITLAB_TIMEOUT", "not-a-number")
        # 错误消息：pydantic 的格式以 "1 validation error" 开头
        with pytest.raises(ConfigError, match="validation error|GITLAB_TIMEOUT|配置"):
            AppConfig.from_env()


class TestAppSettingsDirect:
    """直接对 AppSettings（pydantic）做断言：验证类型 + 字段约束。"""

    def test_required_field_raises_on_empty(self, clean_env):
        # 没有环境变量时，AppSettings 应该报 ConfigError（GITLAB_URL 是必填）
        from gitlab_issues_finder.config import AppSettings

        with pytest.raises(ConfigError, match="GITLAB_URL"):
            AppSettings(_env_file=None)

    def test_defaults_with_required_set(self, monkeypatch):
        # 设置了必填项后，其余字段应该是合理的默认值
        from gitlab_issues_finder.config import AppSettings

        monkeypatch.setenv("GITLAB_URL", "https://gl")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        s = AppSettings(_env_file=None)
        assert s.gitlab_ssl_verify == "true"
        assert s.gitlab_timeout == 30
        assert s.web_host == "127.0.0.1"
        assert s.web_port == 8000
        assert s.page_size == 100
        assert s.db_path == "data/app.db"

    def test_url_trailing_slash_stripped(self, monkeypatch):
        from gitlab_issues_finder.config import AppSettings

        monkeypatch.setenv("GITLAB_URL", "https://gl.example.com/")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        s = AppSettings(_env_file=None)
        assert s.gitlab_url == "https://gl.example.com"

    def test_page_size_out_of_range_rejected(self, monkeypatch):
        from gitlab_issues_finder.config import AppSettings

        monkeypatch.setenv("GITLAB_URL", "https://gl")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        monkeypatch.setenv("PAGE_SIZE", "999")
        with pytest.raises(ConfigError, match="validation error|PAGE_SIZE"):
            AppSettings(_env_file=None)

    def test_negative_timeout_rejected(self, monkeypatch):
        from gitlab_issues_finder.config import AppSettings

        monkeypatch.setenv("GITLAB_URL", "https://gl")
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        monkeypatch.setenv("GITLAB_TIMEOUT", "-1")
        with pytest.raises(ConfigError, match="validation error|GITLAB_TIMEOUT"):
            AppSettings(_env_file=None)

    def test_missing_required_raises_friendly(self, monkeypatch):
        from gitlab_issues_finder.config import AppSettings

        monkeypatch.delenv("GITLAB_URL", raising=False)
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        with pytest.raises(ConfigError, match="GITLAB_URL"):
            AppSettings(_env_file=None)

    def test_appconfig_from_env_uses_settings(self, monkeypatch):
        """AppConfig.from_env 走 AppSettings 路径，结果一致。"""
        from gitlab_issues_finder.config import AppConfig

        monkeypatch.setenv("GITLAB_URL", "https://gl/")
        monkeypatch.setenv("GITLAB_TOKEN", "tok")
        monkeypatch.setenv("GITLAB_SSL_VERIFY", "false")
        monkeypatch.setenv("WEB_PORT", "9999")
        monkeypatch.setenv("PAGE_SIZE", "50")
        cfg = AppConfig.from_env()
        assert cfg.url == "https://gl"
        assert cfg.token == "tok"
        assert cfg.ssl_verify is False
        assert cfg.web_port == 9999
        assert cfg.page_size == 50
