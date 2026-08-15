"""Shared fixtures for the servicenow-client offline test suite.

The test seam is the module-level ``_http_send`` transport function: a
FakeTransport records every request and serves canned HttpResponses, so no
network or credentials are ever touched and the whole suite runs offline.
``_sleep`` is redirected into the fake so retry tests record delays instead of
sleeping, and the ``env`` fixture pins a known environment and clears the
cached client between tests (mirrors mcp-chatbot/tests/conftest.py).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import servicenow_client as snow  # noqa: E402

BASE = "https://unit.test.service-now.com"
SYS_ID = "a" * 32


class FakeTransport:
    """Recording fake for snow._http_send: queue responses, record requests."""

    def __init__(self) -> None:
        self.responses: list[snow.HttpResponse] = []
        self.requests: list[dict[str, Any]] = []
        self.sleeps: list[float] = []

    def __call__(self, request, timeout, max_bytes=snow.MAX_RESPONSE_BYTES):
        self.requests.append(
            {
                "method": request.get_method(),
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "body": request.data,
                "timeout": timeout,
                "max_bytes": max_bytes,
            }
        )
        if not self.responses:
            raise AssertionError(
                f"FakeTransport has no queued response for "
                f"{request.get_method()} {request.full_url}"
            )
        # FIFO; the last queued response repeats so single-queue tests can make
        # several identical calls (e.g. retry loops).
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    def queue(self, status: int, body: bytes = b"", headers: dict | None = None) -> None:
        merged = {"content-type": "application/json"}
        merged.update(headers or {})
        self.responses.append(
            snow.HttpResponse(status=status, headers=merged, body=body)
        )

    def queue_json(self, result: Any, status: int = 200, headers: dict | None = None) -> None:
        self.queue(status, json.dumps({"result": result}).encode("utf-8"), headers)

    def queue_error(
        self, status: int, message: str, detail: str = "", headers: dict | None = None
    ) -> None:
        body = json.dumps(
            {"error": {"message": message, "detail": detail}, "status": "failure"}
        ).encode("utf-8")
        self.queue(status, body, headers)

    def queue_raw(
        self,
        status: int,
        body: bytes,
        content_type: str = "text/html",
        headers: dict | None = None,
    ) -> None:
        merged = {"content-type": content_type}
        merged.update(headers or {})
        self.responses.append(
            snow.HttpResponse(status=status, headers=merged, body=body)
        )

    def urls(self) -> list[str]:
        return [entry["url"] for entry in self.requests]


def record(**overrides: Any) -> dict[str, Any]:
    """A display=both-shaped incident record, the wire format the client normalizes."""
    base: dict[str, Any] = {
        "sys_id": {"value": SYS_ID, "display_value": SYS_ID},
        "number": {"value": "INC0010023", "display_value": "INC0010023"},
        "short_description": {"value": "VPN down", "display_value": "VPN down"},
        "state": {"value": "2", "display_value": "In Progress"},
        "opened_at": {
            "value": "2026-08-01 10:00:00",
            "display_value": "2026-08-01 03:00:00",
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SERVICENOW_INSTANCE", BASE)
    monkeypatch.setenv("SERVICENOW_USERNAME", "unit")
    monkeypatch.setenv("SERVICENOW_PASSWORD", "test")
    for name in (
        "SERVICENOW_READ_ONLY",
        "SERVICENOW_ALLOW_DELETE",
        "SERVICENOW_AUTH",
        "SERVICENOW_TIMEOUT_SECONDS",
        "SERVICENOW_MAX_ATTACHMENT_MB",
    ):
        monkeypatch.delenv(name, raising=False)
    snow._client.cache_clear()
    yield monkeypatch
    snow._client.cache_clear()


@pytest.fixture
def transport(monkeypatch, env):
    fake = FakeTransport()
    monkeypatch.setattr(snow, "_http_send", fake)
    monkeypatch.setattr(snow, "_sleep", lambda seconds: fake.sleeps.append(seconds))
    return fake
