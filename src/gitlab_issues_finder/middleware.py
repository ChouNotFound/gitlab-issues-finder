"""Starlette middleware: 注入 / 透传 X-Request-ID，并把请求元数据写到日志。

工作方式：
  - 收到请求：尝试从 ``X-Request-ID`` header 复用；否则生成 16 字节
    的 URL-safe 随机串。
  - 把 ``request_id`` 绑到 ``request.state``，方便业务代码在日志里引用。
  - 响应时把同一个 ``X-Request-ID`` 写到 header。
  - 整个请求处理过程会输出一条 ``request start`` 和 ``request end``
    日志（含 method / path / status / duration_ms / request_id）。
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger("gif.request")

_HEADER = "X-Request-ID"
_MAX_INCOMING_LEN = 128


def _new_request_id() -> str:
    return secrets.token_urlsafe(16)


def _sanitize_incoming(raw: str) -> str | None:
    """如果传入的 header 看起来合理就复用；否则丢弃。"""
    raw = raw.strip()
    if not raw or len(raw) > _MAX_INCOMING_LEN:
        return None
    # 仅允许 URL-safe / 字母数字 / 短横线，避免日志注入。
    for ch in raw:
        if not (ch.isalnum() or ch in "-_."):
            return None
    return raw


class RequestIDMiddleware(BaseHTTPMiddleware):
    """为每个请求分配一个 ID，附加到 request.state 与响应 header，并打日志。"""

    def __init__(self, app: ASGIApp, *, header_name: str = _HEADER) -> None:
        super().__init__(app)
        self.header_name = header_name

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get(self.header_name)
        request_id = _sanitize_incoming(incoming) if incoming else None
        if not request_id:
            request_id = _new_request_id()
        request.state.request_id = request_id

        start = time.perf_counter()
        # /static/* 噪音较大，跳过日志。
        is_static = request.url.path.startswith("/static/")
        if not is_static:
            logger.info(
                "request start",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "request failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise exc

        duration_ms = (time.perf_counter() - start) * 1000
        response.headers[self.header_name] = request_id
        if not is_static:
            logger.info(
                "request end",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
        return response
