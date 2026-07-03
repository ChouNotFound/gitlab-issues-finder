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
        monkeypatch.setenv("GITLAB_TOKEN", "x")
        with pytest.raises(ConfigError, match="GITLAB_URL"):
            AppConfig.from_env()

    def test_missing_token(self, clean_env, monkeypatch):
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
        with pytest.raises(ConfigError, match="数值型配置"):
            AppConfig.from_env()
