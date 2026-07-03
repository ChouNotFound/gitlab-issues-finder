"""logging_setup 单元测试。"""

from __future__ import annotations

import io
import json
import logging
import os

import pytest

from gitlab_issues_finder import logging_setup


@pytest.fixture(autouse=True)
def reset_logging():
    """每个测试前后重置 _configured，避免 import app.py 时已配置。"""
    logging_setup._configured = False
    yield
    logging_setup._configured = False


def _capture_root_log(level: str = "INFO", json_mode: bool = False) -> io.StringIO:
    """重置根 logger，把输出捕获到 StringIO。"""
    logging_setup._configured = False
    os.environ["LOG_LEVEL"] = level
    if json_mode:
        os.environ["LOG_JSON"] = "1"
    elif "LOG_JSON" in os.environ:
        del os.environ["LOG_JSON"]
    logging_setup.configure_logging()
    buf = io.StringIO()
    logging.getLogger().handlers[0].stream = buf
    return buf


class TestConfigureLogging:
    def test_idempotent(self):
        logging_setup.configure_logging()
        h1 = list(logging.getLogger().handlers)
        logging_setup.configure_logging()
        h2 = list(logging.getLogger().handlers)
        assert len(h1) == len(h2) == 1

    def test_human_format_default(self):
        buf = _capture_root_log()
        logging.getLogger("gif.test").info("hello")
        out = buf.getvalue()
        assert "hello" in out
        assert "[gif.test]" in out
        assert "INFO" in out

    def test_json_format(self):
        buf = _capture_root_log(json_mode=True)
        logging.getLogger("gif.test").info("hi", extra={"user": "alice", "n": 3})
        rec = json.loads(buf.getvalue().strip().splitlines()[-1])
        assert rec["message"] == "hi"
        assert rec["level"] == "INFO"
        assert rec["logger"] == "gif.test"
        assert rec["user"] == "alice"
        assert rec["n"] == 3
        assert "time" in rec

    def test_level_from_env(self):
        _capture_root_log(level="WARNING")
        assert logging.getLogger().level == logging.WARNING

    def test_invalid_level_falls_back_to_info(self):
        _capture_root_log(level="BANANA")
        assert logging.getLogger().level == logging.INFO


class TestGetLogger:
    def test_returns_named_logger(self):
        log = logging_setup.get_logger("gif.sub")
        assert log.name == "gif.sub"
        assert isinstance(log, logging.Logger)

    def test_triggers_global_config(self):
        assert logging_setup._configured is False
        logging_setup.get_logger("x")
        assert logging_setup._configured is True
