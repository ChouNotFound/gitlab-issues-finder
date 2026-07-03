"""RequestIDMiddleware tests."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitlab_issues_finder.middleware import RequestIDMiddleware, _sanitize_incoming

# A trivial app so we can test the middleware in isolation.
_app = FastAPI()
_app.add_middleware(RequestIDMiddleware)


@_app.get("/echo")
def echo():

    # we cannot easily reach request.state from inside the handler in this minimal app
    return {"ok": True}


client = TestClient(_app)


class TestSanitizeIncoming:
    def test_keeps_alnum_dash_underscore(self):
        assert _sanitize_incoming("abc-123_XYZ") == "abc-123_XYZ"

    def test_rejects_empty(self):
        assert _sanitize_incoming("") is None

    def test_rejects_too_long(self):
        assert _sanitize_incoming("a" * 200) is None

    def test_rejects_injection_chars(self):
        assert _sanitize_incoming("abc\ndef") is None
        assert _sanitize_incoming("abc def") is None
        assert _sanitize_incoming("abc;rm") is None


class TestRequestIDMiddleware:
    def test_response_has_request_id_header(self):
        r = client.get("/echo")
        assert r.status_code == 200
        assert "x-request-id" in r.headers
        rid = r.headers["x-request-id"]
        # 16 bytes -> 22 chars (urlsafe base64, no padding)
        assert len(rid) >= 16

    def test_incoming_request_id_is_echoed(self):
        r = client.get("/echo", headers={"X-Request-ID": "trace-abc-123"})
        assert r.headers["x-request-id"] == "trace-abc-123"

    def test_incoming_request_id_is_validated(self):
        r = client.get("/echo", headers={"X-Request-ID": "evil\nlog"})
        # invalid incoming id is replaced with a generated one
        assert "\n" not in r.headers["x-request-id"]
