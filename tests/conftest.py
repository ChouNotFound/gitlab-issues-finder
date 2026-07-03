"""共享 fixture。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> list[dict]:
    """加载 tests/fixtures/ 下的 JSON 文件。"""
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """设置必需的环境变量（隔离真实 .env）。"""
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.test")
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-test-token")


@pytest.fixture(autouse=True)
def _disable_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个测试前把速率限制关掉，避免 TestClient 的连续请求触发 429。"""
    monkeypatch.setenv("RATE_LIMIT_RPM", "0")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清空所有相关环境变量，模拟全新环境。"""
    for key in [
        "GITLAB_URL",
        "GITLAB_TOKEN",
        "GITLAB_SSL_VERIFY",
        "GITLAB_TIMEOUT",
        "WEB_HOST",
        "WEB_PORT",
        "PAGE_SIZE",
        "DB_PATH",
    ]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def tmp_db(monkeypatch: pytest.MonkeyPatch) -> str:
    """为每次测试创建临时 SQLite DB 文件并设置 DB_PATH。

    自动初始化 schema。测试结束后自动清理。
    """
    from gitlab_issues_finder import storage

    tmp = tempfile.mkdtemp(prefix="gif-test-")
    db_path = os.path.join(tmp, "test.db")
    monkeypatch.setenv("DB_PATH", db_path)
    storage.init_db(db_path)
    yield db_path
    shutil.rmtree(tmp, ignore_errors=True)
