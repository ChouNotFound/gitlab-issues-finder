"""rate_limit unit and integration tests."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from gitlab_issues_finder.rate_limit import (
    RateLimiter,
    get_default_limiter,
    reset_default_limiter,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset singleton + bucket state before each test."""
    reset_default_limiter()
    yield
    reset_default_limiter()


class TestRateLimiter:
    def test_first_hits_pass(self):
        rl = RateLimiter(per_minute=60, burst=5)
        for _ in range(5):
            assert rl.hit("k") is True

    def test_sixth_hit_in_same_window_fails(self):
        rl = RateLimiter(per_minute=60, burst=5)
        for _ in range(5):
            assert rl.hit("k") is True
        assert rl.hit("k") is False

    def test_different_keys_have_separate_buckets(self):
        rl = RateLimiter(per_minute=60, burst=2)
        assert rl.hit("a") is True
        assert rl.hit("a") is True
        assert rl.hit("a") is False
        assert rl.hit("b") is True
        assert rl.hit("b") is True
        assert rl.hit("b") is False

    def test_per_minute_zero_disables(self):
        rl = RateLimiter(per_minute=0)
        for _ in range(100):
            assert rl.hit("k") is True

    def test_tokens_refill_over_time(self):
        rl = RateLimiter(per_minute=6000, burst=2)
        for _ in range(2):
            assert rl.hit("k") is True
        assert rl.hit("k") is False
        time.sleep(0.05)
        assert rl.hit("k") is True

    def test_reset_clears_state(self):
        rl = RateLimiter(per_minute=60, burst=1)
        assert rl.hit("k") is True
        assert rl.hit("k") is False
        rl.reset()
        assert rl.hit("k") is True


def _build_test_app(per_minute: int, burst: int) -> FastAPI:
    """Minimal app with a rate-limit middleware pre-configured."""
    app = FastAPI()
    limiter = RateLimiter(per_minute=per_minute, burst=burst)

    class _Limiter(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            ip = request.client.host if request.client else "anon"
            if not limiter.hit(ip):
                return JSONResponse(
                    {"detail": "rate limit exceeded", "retry_after_seconds": 1},
                    status_code=429,
                    headers={"Retry-After": "1"},
                )
            return await call_next(request)

    app.add_middleware(_Limiter)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


class TestRateLimitMiddleware:
    def test_returns_429_when_exhausted(self):
        app = _build_test_app(per_minute=60, burst=2)
        c = TestClient(app)
        assert c.get("/ping").status_code == 200
        assert c.get("/ping").status_code == 200
        r = c.get("/ping")
        assert r.status_code == 429
        assert r.json()["detail"] == "rate limit exceeded"

    def test_429_includes_retry_after(self):
        app = _build_test_app(per_minute=60, burst=1)
        c = TestClient(app)
        c.get("/ping")
        r = c.get("/ping")
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        assert int(r.headers["Retry-After"]) > 0


def test_get_default_limiter_singleton(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_RPM", "120")
    monkeypatch.setenv("RATE_LIMIT_BURST", "10")
    a = get_default_limiter()
    b = get_default_limiter()
    assert a is b
    assert a.per_minute == 120
    assert a.burst == 10
