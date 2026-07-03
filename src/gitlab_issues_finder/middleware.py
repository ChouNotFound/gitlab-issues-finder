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
            try:
                from gitlab_issues_finder.metrics import get_metrics

                m = get_metrics()
                m.inc(
                    "http_requests_total",
                    method=request.method,
                    path=request.url.path,
                )
            except Exception:  # noqa: BLE001
                pass
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
            try:
                from gitlab_issues_finder.metrics import get_metrics

                get_metrics().observe("http_request_duration_ms", duration_ms)
            except Exception:  # noqa: BLE001
                pass
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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """为所有响应附加常见的安全头。

    - X-Content-Type-Options: nosniff
        阻止浏览器做 MIME 嗅探，避免 text/plain 被当 HTML 执行。
    - X-Frame-Options: DENY
        禁止 iframe 嵌入（防御 clickjacking）。
    - Referrer-Policy: no-referrer
        防止 URL（含 token / 用户名）通过 Referer 泄漏给第三方。
    - Permissions-Policy: 关闭不用的浏览器能力（camera/mic/geolocation）。

    注意：这些头对纯 API 调用没有视觉影响，但对 Web 浏览器渲染
    /board /search 等 HTML 页面时有意义。
    """

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        return response
