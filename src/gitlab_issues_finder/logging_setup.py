"""结构化日志配置。

提供：
  - `configure_logging()` 一次性初始化根 logger。
  - `get_logger(name)` 拿一个子 logger（推荐入口）。
  - `JsonFormatter`：把 LogRecord 序列化成 JSON（便于接入 ELK / Loki）。

设计：
  - 默认输出人类可读格式（level + 时间 + 名称 + 消息）。
  - 环境变量 `LOG_JSON=1` 切换为 JSON 格式。
  - 环境变量 `LOG_LEVEL=DEBUG|INFO|WARNING|ERROR` 控制级别（默认 INFO）。
  - 避免重复初始化（幂等）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

_configured = False

_DEFAULT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


class JsonFormatter(logging.Formatter):
    """把 LogRecord 序列化成单行 JSON。

    保留标准字段（time / level / logger / message），并把所有 extra 字段
    平铺到顶层，便于 grep / jq 查询。"""

    # LogRecord 标准属性，其它字段视为 extra
    _RESERVED = frozenset({
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName",
        "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = _jsonable(value)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _jsonable(value: Any) -> Any:
    """把非 JSON 原生类型转成可序列化形式。"""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def configure_logging() -> None:
    """根据环境变量配置根 logger。幂等。"""
    global _configured
    if _configured:
        return

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    use_json = os.environ.get("LOG_JSON", "").lower() in ("1", "true", "yes")

    handler = logging.StreamHandler(stream=sys.stderr)
    if use_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT, _DEFAULT_DATEFMT))

    root = logging.getLogger()
    # 移除已有 handler，避免重复输出（uvicorn 已加过自己的）
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)

    # 降级 uvicorn 访问日志（access 留默认，error 同步到我们的 level）
    logging.getLogger("uvicorn.access").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """拿到一个子 logger。自动触发全局配置。"""
    configure_logging()
    return logging.getLogger(name)
