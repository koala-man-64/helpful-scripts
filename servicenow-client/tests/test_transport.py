"""Transport behaviors: auth header, envelope unwrap, error translation, retries, caps."""

from __future__ import annotations

import base64
import email.message
import io
import urllib.error
import urllib.request

import pytest

import servicenow_client as snow
from conftest import BASE


def _basic(user: str = "unit", password: str = "test") -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")


def test_basic_auth_header_sent(transport):
    transport.queue_json([])
    snow._client().query("incident")
    assert transport.requests[0]["headers"]["Authorization"] == _basic()


def test_result_envelope_unwrapped(transport):
    transport.queue_json({"a": 1})
    assert snow._client().request("GET", "/x") == {"a": 1}


def test_204_returns_none(transport):
    transport.queue(204, b"")
    assert snow._client().request("GET", "/x") is None


def test_400_maps_to_valueerror_with_detail(transport):
    transport.queue_error(400, "Invalid query", "field does not exist")
    with pytest.raises(ValueError) as excinfo:
        snow._client().request("GET", "/api/now/table/incident")
    err = excinfo.value
    assert "Invalid query" in str(err) and "field does not exist" in str(err)
    assert err.error_class == "validation" and err.http_status == 400


def test_401_names_credentials(transport):
    transport.queue_error(401, "User Not Authenticated")
    with pytest.raises(RuntimeError) as excinfo:
        snow._client().request("GET", "/x")
    assert "SERVICENOW_USERNAME" in str(excinfo.value)
    assert excinfo.value.error_class == "auth"


def test_403_is_forbidden_runtime_error(transport):
    transport.queue_error(403, "Insufficient rights")
    with pytest.raises(RuntimeError) as excinfo:
        snow._client().request("GET", "/x")
    assert excinfo.value.error_class == "forbidden"
    assert "ACL" in str(excinfo.value)


def test_404_is_not_found_value_error(transport):
    transport.queue_error(404, "No Record found")
    with pytest.raises(ValueError) as excinfo:
        snow._client().request("GET", "/x")
    assert excinfo.value.error_class == "not_found"


def test_404_on_vit_table_names_plugin(transport):
    transport.queue_error(404, "Invalid table")
    with pytest.raises(ValueError) as excinfo:
        snow._client().request(
            "GET",
            "/api/now/table/sn_vul_vulnerable_item",
            table="sn_vul_vulnerable_item",
        )
    assert "Vulnerability Response" in str(excinfo.value)


def test_malformed_error_body_falls_back_to_raw_text(transport):
    transport.queue_raw(500, b"<html>gateway oops</html>")
    with pytest.raises(RuntimeError) as excinfo:
        snow._client().request("POST", "/x", json_body={})
    assert "gateway oops" in str(excinfo.value)


def test_429_retries_honoring_retry_after(transport):
    transport.queue_error(429, "Rate limit", headers={"retry-after": "7"})
    transport.queue_error(429, "Rate limit", headers={"retry-after": "3"})
    transport.queue_json({"ok": True})
    assert snow._client().request("GET", "/x") == {"ok": True}
    assert transport.sleeps == [7.0, 3.0]
    assert len(transport.requests) == 3


def test_429_gives_up_after_three_attempts(transport):
    transport.queue_error(429, "Rate limit")
    with pytest.raises(RuntimeError) as excinfo:
        snow._client().request("GET", "/x")
    assert excinfo.value.error_class == "rate_limited"
    assert len(transport.requests) == 3
    assert transport.sleeps == [1.0, 2.0]


def test_garbage_retry_after_falls_back_to_backoff(transport):
    transport.queue_error(429, "Rate limit", headers={"retry-after": "soon"})
    transport.queue_json({"ok": True})
    snow._client().request("GET", "/x")
    assert transport.sleeps == [1.0]


def test_retry_after_capped(transport):
    transport.queue_error(429, "Rate limit", headers={"retry-after": "600"})
    transport.queue_json({"ok": True})
    snow._client().request("GET", "/x")
    assert transport.sleeps == [30.0]


def test_get_503_is_retried(transport):
    transport.queue_error(503, "unavailable")
    transport.queue_json({"ok": True})
    assert snow._client().request("GET", "/x") == {"ok": True}
    assert len(transport.requests) == 2


def test_post_5xx_is_never_retried(transport):
    # A retried POST after a 5xx could create a duplicate ticket.
    transport.queue_error(503, "unavailable")
    with pytest.raises(RuntimeError):
        snow._client().request("POST", "/x", json_body={"a": 1})
    assert len(transport.requests) == 1
    assert transport.sleeps == []


def test_hibernating_instance_detected(transport):
    transport.queue_raw(200, b"<html>Instance Hibernating</html>")
    with pytest.raises(RuntimeError) as excinfo:
        snow._client().request("GET", "/x")
    assert "hibernate" in str(excinfo.value).lower()
    assert excinfo.value.error_class == "unavailable"


def test_unparseable_success_json(transport):
    transport.queue(200, b"{not json")
    with pytest.raises(RuntimeError, match="unparseable"):
        snow._client().request("GET", "/x")


# -- the real _http_send, with urlopen faked ---------------------------------


class _FakeUrlopen:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status
        self.headers = {"Content-Type": "application/json"}

    def read(self, n: int) -> bytes:
        return self._body[:n]

    def __enter__(self) -> "_FakeUrlopen":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def test_http_send_caps_oversize_body(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout: _FakeUrlopen(b"x" * 100)
    )
    with pytest.raises(RuntimeError, match="exceeded"):
        snow._http_send(urllib.request.Request(f"{BASE}/x"), 1.0, max_bytes=50)


def test_http_send_exact_cap_is_fine(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout: _FakeUrlopen(b"x" * 50)
    )
    resp = snow._http_send(urllib.request.Request(f"{BASE}/x"), 1.0, max_bytes=50)
    assert resp.status == 200 and resp.body == b"x" * 50


def test_http_send_urlerror_is_prescriptive(monkeypatch):
    def boom(req, timeout):
        raise urllib.error.URLError("dns failure")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match="Cannot reach"):
        snow._http_send(urllib.request.Request(f"{BASE}/x"), 1.0)


def test_http_send_timeout_is_prescriptive(monkeypatch):
    def boom(req, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match="timed out"):
        snow._http_send(urllib.request.Request(f"{BASE}/x"), 1.0)


def test_http_send_normalizes_httperror_into_response(monkeypatch):
    headers = email.message.Message()
    headers["Content-Type"] = "application/json"

    def boom(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url, 418, "teapot", headers, io.BytesIO(b'{"error":{}}')
        )

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    resp = snow._http_send(urllib.request.Request(f"{BASE}/x"), 1.0)
    assert resp.status == 418
    assert resp.headers.get("content-type") == "application/json"
    assert resp.body == b'{"error":{}}'


# -- config plumbing ---------------------------------------------------------


def test_instance_accepts_bare_name_and_full_url(env):
    env.setenv("SERVICENOW_INSTANCE", "dev12345")
    assert snow._base_url() == "https://dev12345.service-now.com"
    env.setenv("SERVICENOW_INSTANCE", "https://snow.example.com/")
    assert snow._base_url() == "https://snow.example.com"


def test_instance_rejects_plain_http(env):
    env.setenv("SERVICENOW_INSTANCE", "http://insecure.example.com")
    with pytest.raises(RuntimeError, match="https"):
        snow._base_url()


def test_unsupported_auth_mode(env):
    env.setenv("SERVICENOW_AUTH", "oauth")
    with pytest.raises(RuntimeError, match="only 'basic'"):
        snow._auth_headers()


def test_require_env_missing_is_prescriptive(env):
    env.delenv("SERVICENOW_PASSWORD")
    with pytest.raises(RuntimeError, match="SERVICENOW_PASSWORD"):
        snow._basic_auth_headers()


def test_timeout_env_reaches_client(env):
    env.setenv("SERVICENOW_TIMEOUT_SECONDS", "5")
    snow._client.cache_clear()
    assert snow._client().timeout == 5.0
