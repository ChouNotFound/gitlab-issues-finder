"""Per-IP token bucket rate limiter (in-memory).

Simple, no external dependencies, sufficient for a personal tool
running on localhost. Multi-process deployments would need a
shared backend (Redis), but that is out of scope.

Env knobs:
  - RATE_LIMIT_RPM: requests per minute per IP (default 60). 0
    disables the limiter entirely.
  - RATE_LIMIT_BURST: maximum burst size (default = rpm).
"""

from __future__ import annotations

import os
import threading
import time


class RateLimiter:
    """Per-key token bucket (one key = one IP). Thread-safe."""

    def __init__(self, *, per_minute: int, burst: int | None = None) -> None:
        self.per_minute = per_minute
        self.burst = burst if burst is not None else per_minute
        self._rate = per_minute / 60.0
        self._buckets: dict[str, "_Bucket"] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Try to consume 1 token for ``key``. Returns True if allowed."""
        if self.per_minute <= 0:
            return True
        with self._lock:
            now = time.monotonic()
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(self.burst)
                self._buckets[key] = bucket
            elapsed = now - bucket.last_refill
            if elapsed > 0:
                bucket.tokens = min(self.burst, bucket.tokens + elapsed * self._rate)
                bucket.last_refill = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            return False

    def reset(self) -> None:
        """Clear all bucket state."""
        with self._lock:
            self._buckets.clear()


class _Bucket:
    __slots__ = ("tokens", "last_refill")

    def __init__(self, burst: int) -> None:
        self.tokens: float = float(burst)
        self.last_refill: float = time.monotonic()


_DEFAULT_LOCK = threading.Lock()
_DEFAULT_LIMITER: RateLimiter | None = None


def get_default_limiter() -> RateLimiter:
    """Lazy singleton. Reads RATE_LIMIT_RPM at first call."""
    global _DEFAULT_LIMITER
    with _DEFAULT_LOCK:
        if _DEFAULT_LIMITER is None:
            rpm = int(os.environ.get("RATE_LIMIT_RPM", "60"))
            burst = int(os.environ.get("RATE_LIMIT_BURST", str(rpm)))
            _DEFAULT_LIMITER = RateLimiter(per_minute=rpm, burst=burst)
    return _DEFAULT_LIMITER


def reset_default_limiter() -> None:
    """Reset singleton + its bucket state. Test helper."""
    global _DEFAULT_LIMITER
    with _DEFAULT_LOCK:
        if _DEFAULT_LIMITER is not None:
            _DEFAULT_LIMITER.reset()
        _DEFAULT_LIMITER = None
