"""ServiceNow's optional agent-browser transport stays offline under fakes."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

import servicenow_client as snow
from conftest import BASE


def _browser_auth(env) -> None:
    env.setenv("SERVICENOW_AUTH", "browser")
    env.delenv("SERVICENOW_USERNAME", raising=False)
    env.delenv("SERVICENOW_PASSWORD", raising=False)


def _install_bridge(monkeypatch, *responses: dict):
    calls: list[dict] = []
    queued = list(responses)

    def browser_request(**kwargs):
        calls.append(kwargs)
        if not queued:
            raise AssertionError("fake browser bridge has no queued response")
        return queued.pop(0)

    package = ModuleType("agent_browser_core")
    package.__path__ = []  # type: ignore[attr-defined]
    client = ModuleType("agent_browser_core.client")
    client.browser_request = browser_request  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_browser_core", package)
    monkeypatch.setitem(sys.modules, "agent_browser_core.client", client)
    return calls


def _load_repo_browser_bridge(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(repo_root / "agent-browser"))
    monkeypatch.delitem(sys.modules, "agent_browser_core.client", raising=False)
    monkeypatch.delitem(sys.modules, "agent_browser_core", raising=False)
    return importlib.import_module("agent_browser_core.client")


def test_browser_auth_needs_no_basic_credentials(env):
    _browser_auth(env)
    assert snow._auth_headers() == {}


def test_browser_transport_maps_binary_io_and_keeps_secrets_inside_bridge(
    env, monkeypatch
):
    _browser_auth(env)
    env.setenv("SERVICENOW_BROWSER_PROFILE", "work")
    calls = _install_bridge(
        monkeypatch,
        {
            "status": 201,
            "headers": {
                "Content-Type": "application/octet-stream",
                "Set-Cookie": "session=secret",
                "X-UserToken": "csrf-secret",
                "X_g_ck": "gck-secret",
                "X-Auth-Token": "auth-secret",
                "X-Session-Id": "session-secret",
            },
            "body": b"\x00\x80\xffattachment",
        },
    )
    client = snow.ServiceNowClient(
        BASE,
        auth_provider=lambda: {
            "Authorization": "Bearer must-not-leave",
            "Cookie": "session=must-not-leave",
            "X-UserToken": "csrf-must-not-leave",
            "X-Auth-Token": "token-must-not-leave",
            "X-Session-Id": "session-must-not-leave",
        },
        transport=snow._browser_http_send,
    )

    body, response = client._request(
        "POST",
        "/api/now/attachment/file",
        raw_body=b"\x00\x80\xffupload",
        content_type="application/octet-stream",
        max_bytes=64,
        parse=False,
    )

    assert body == b"\x00\x80\xffattachment"
    assert response is not None
    assert response.status == 201
    assert response.headers == {"content-type": "application/octet-stream"}
    assert calls == [
        {
            "method": "POST",
            "url": BASE + "/api/now/attachment/file",
            "headers": {
                "Accept": "application/json",
                "Content-type": "application/octet-stream",
            },
            "body": b"\x00\x80\xffupload",
            "timeout": snow.DEFAULT_TIMEOUT,
            "max_bytes": 64,
            "profile": "work",
            "session_id": None,
            "servicenow": True,
        }
    ]


def test_browser_http_errors_use_existing_service_now_translation(env, monkeypatch):
    _browser_auth(env)
    _install_bridge(
        monkeypatch,
        {
            "status": 403,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {"error": {"message": "Access denied", "detail": "ACL"}}
            ).encode(),
        },
    )

    with pytest.raises(RuntimeError, match="Access denied") as excinfo:
        snow._client().request("GET", "/api/now/table/incident")
    assert excinfo.value.error_class == "forbidden"
    assert excinfo.value.http_status == 403


def test_browser_write_429_is_not_retried_and_requires_receipt_inspection(
    env, monkeypatch, capsys
):
    _browser_auth(env)
    calls = _install_bridge(
        monkeypatch,
        {
            "status": 429,
            "headers": {"Retry-After": "1", "Content-Type": "application/json"},
            "body": b'{"error":{"message":"Rate limited"}}',
            "command_id": "cmd-rate-limited-write",
            "receipt": "state --command cmd-rate-limited-write",
            "executed": True,
            "write_outcome": "unknown",
        },
    )

    with pytest.raises(RuntimeError) as excinfo:
        snow._client().request(
            "PATCH", "/api/now/table/incident/" + "a" * 32,
            json_body={"state": "2"},
        )

    error = excinfo.value
    assert len(calls) == 1
    assert error.error_class == "unknown_write_outcome"
    assert error.http_status == 429
    assert error.command_id == "cmd-rate-limited-write"
    assert error.receipt == "state --command cmd-rate-limited-write"
    assert error.write_outcome == "unknown"
    assert "verify the ServiceNow record before any manual retry" in error.hint
    assert "retry later" not in str(error).lower()

    snow._emit_error(error)
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["command_id"] == "cmd-rate-limited-write"
    assert payload["error"]["receipt"] == "state --command cmd-rate-limited-write"
    assert payload["error"]["write_outcome"] == "unknown"


@pytest.mark.parametrize("status", [302, 400, 403])
def test_browser_non2xx_explicit_unknown_outcome_is_preserved(
    env, monkeypatch, status
):
    _browser_auth(env)
    calls = _install_bridge(
        monkeypatch,
        {
            "status": status,
            "headers": {"Content-Type": "application/json"},
            "body": b'{"error":{"message":"request was not accepted"}}',
            "command_id": f"cmd-http-{status}",
            "receipt": f"state --command cmd-http-{status}",
            "executed": True,
            "write_outcome": "unknown",
        },
    )

    with pytest.raises(RuntimeError) as excinfo:
        snow._client().request(
            "PATCH", "/api/now/table/incident/" + "a" * 32,
            json_body={"state": "2"},
        )

    error = excinfo.value
    assert len(calls) == 1
    assert error.error_class == "unknown_write_outcome"
    assert error.http_status == status
    assert error.command_id == f"cmd-http-{status}"
    assert error.receipt == f"state --command cmd-http-{status}"
    assert error.executed is True
    assert error.write_outcome == "unknown"


def test_browser_get_429_keeps_safe_retry(env, monkeypatch):
    _browser_auth(env)
    monkeypatch.setattr(snow, "_sleep", lambda _seconds: None)
    calls = _install_bridge(
        monkeypatch,
        {"status": 429, "headers": {}, "body": b"busy"},
        {
            "status": 200,
            "headers": {"Content-Type": "application/json"},
            "body": b'{"result":{"ok":true}}',
        },
    )

    assert snow._client().request("GET", "/api/now/table/incident") == {"ok": True}
    assert len(calls) == 2


def test_actual_bridge_preserves_uncertain_daemon_write_receipt(
    env, monkeypatch, tmp_path, capsys
):
    _browser_auth(env)
    env.setenv("SERVICENOW_BROWSER_PROFILE", "servicenowtest")
    env.setenv("AGENT_BROWSER_HOME", str(tmp_path / "browser-home"))
    env.setenv("CODEX_THREAD_ID", "offline-bridge-error-test")
    bridge = _load_repo_browser_bridge(monkeypatch)
    daemon_error = RuntimeError("daemon connection ended after command dispatch")
    daemon_error.error_class = "daemon"  # type: ignore[attr-defined]
    daemon_error.details = {  # type: ignore[attr-defined]
        "command_id": "cmd-uncertain-write",
        "receipt": "state --command cmd-uncertain-write",
        "executed": True,
    }

    def fake_invoke(*_args, **_kwargs):
        raise daemon_error

    monkeypatch.setattr(bridge, "invoke", fake_invoke)
    with pytest.raises(RuntimeError) as excinfo:
        snow._client().request(
            "POST", "/api/now/table/incident", json_body={"short_description": "x"}
        )

    error = excinfo.value
    assert error.error_class == "unknown_write_outcome"
    assert error.command_id == "cmd-uncertain-write"
    assert error.receipt == "state --command cmd-uncertain-write"
    snow._emit_error(error)
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["executed"] is True
    assert payload["error"]["receipt"] == "state --command cmd-uncertain-write"


def test_browser_transport_enforces_response_byte_cap(env, monkeypatch):
    _browser_auth(env)
    calls = _install_bridge(
        monkeypatch,
        {
            "status": 200,
            "headers": {"Content-Type": "application/octet-stream"},
            "body": b"12345",
        },
    )
    request = snow.urllib.request.Request(BASE + "/api/now/attachment/file")

    with pytest.raises(RuntimeError, match="exceeded") as excinfo:
        snow._browser_http_send(request, 1.0, max_bytes=4)
    assert excinfo.value.error_class == "validation"
    assert calls[0]["max_bytes"] == 4


def test_browser_transport_uses_repo_bridge_and_guarded_binary_artifacts(
    env, monkeypatch, tmp_path
):
    _browser_auth(env)
    env.setenv("SERVICENOW_BROWSER_PROFILE", "servicenowtest")
    env.setenv("AGENT_BROWSER_HOME", str(tmp_path / "browser-home"))
    env.setenv("CODEX_THREAD_ID", "offline-bridge-test")
    bridge = _load_repo_browser_bridge(monkeypatch)
    request_body = b"\x00request\x80\xff"
    response_body = b"\x00response\x80\xff"
    rpc_calls: list[dict] = []
    request_artifacts: list[Path] = []
    escape_response_artifact = False
    response_status = 201
    write_outcome = None

    def fake_invoke(
        command,
        arguments,
        *,
        profile=None,
        session_id=None,
        timeout=15,
        committing=False,
    ):
        assert command == "api"
        assert profile == "servicenowtest"
        assert session_id == "offline-bridge-test"
        assert timeout == 4.5
        assert committing is True
        paths = bridge.common.ProfilePaths(
            profile,
            bridge.common._home(),
            bridge.common.session_identity(session_id),
        )
        request_path = Path(arguments["body_file"]).resolve()
        request_artifacts.append(request_path)
        assert request_path.parent == paths.downloads.resolve()
        assert request_path.suffix == ".body"
        assert request_path.read_bytes() == request_body
        assert arguments["method"] == "POST"
        assert arguments["url"] == BASE + "/api/now/attachment/file"
        assert arguments["headers"] == {
            "Accept": "application/json",
            "Content-type": "application/octet-stream",
        }
        assert arguments["max_response_bytes"] == 512
        assert arguments["save_response"] is True
        assert arguments["servicenow"] is True

        paths.outputs.mkdir(parents=True, exist_ok=True)
        response_path = (
            tmp_path / "escaped-response.body"
            if escape_response_artifact
            else paths.outputs / "offline-response.body"
        )
        response_path.write_bytes(response_body)
        rpc_calls.append({"arguments": arguments, "response_path": response_path})
        return {
            "status": response_status,
            "headers": {
                "Content-Type": "application/octet-stream",
                "Set-Cookie": "browser-session=secret",
                "X-UserToken": "csrf-secret",
                "X_g_ck": "gck-secret",
            },
            "body_file": str(response_path),
            "command_id": "cmd-binary-upload",
            "receipt": "state --command cmd-binary-upload",
            "executed": True,
            "write_outcome": write_outcome,
        }

    monkeypatch.setattr(bridge, "invoke", fake_invoke)
    client = snow.ServiceNowClient(
        BASE,
        auth_provider=lambda: {
            "Authorization": "Bearer must-not-leave",
            "Cookie": "browser-session=must-not-leave",
            "X-UserToken": "csrf-must-not-leave",
        },
        timeout=4.5,
        transport=snow._browser_http_send,
    )

    body, response = client._request(
        "POST",
        "/api/now/attachment/file",
        raw_body=request_body,
        content_type="application/octet-stream",
        max_bytes=512,
        parse=False,
    )

    assert body == response_body
    assert response is not None
    assert response.status == 201
    assert response.headers == {"content-type": "application/octet-stream"}
    assert response.command_id == "cmd-binary-upload"
    assert response.receipt == "state --command cmd-binary-upload"
    assert response.executed is True
    assert response.write_outcome is None
    assert len(rpc_calls) == 1
    assert len(request_artifacts) == 1
    assert not request_artifacts[0].exists()
    assert rpc_calls[0]["response_path"].read_bytes() == response_body

    response_status = 403
    write_outcome = "unknown"
    bridge_response = snow._browser_http_send(
        snow.urllib.request.Request(
            BASE + "/api/now/attachment/file",
            data=request_body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/octet-stream",
            },
            method="POST",
        ),
        4.5,
        max_bytes=512,
    )
    assert bridge_response.status == 403
    assert bridge_response.command_id == "cmd-binary-upload"
    assert bridge_response.receipt == "state --command cmd-binary-upload"
    assert bridge_response.executed is True
    assert bridge_response.write_outcome == "unknown"
    assert len(request_artifacts) == 2
    assert all(not path.exists() for path in request_artifacts)

    response_status = 201
    write_outcome = None
    escape_response_artifact = True
    with pytest.raises(RuntimeError, match="invalid response artifact") as excinfo:
        client._request(
            "POST",
            "/api/now/attachment/file",
            raw_body=request_body,
            content_type="application/octet-stream",
            max_bytes=512,
            parse=False,
        )
    assert excinfo.value.error_class == "unknown_write_outcome"
    assert "before any manual retry" in excinfo.value.hint
    assert excinfo.value.command_id == "cmd-binary-upload"
    assert excinfo.value.receipt == "state --command cmd-binary-upload"
    assert excinfo.value.executed is True
    assert len(request_artifacts) == 3
    assert all(not path.exists() for path in request_artifacts)


def test_browser_auth_guards_run_before_the_bridge(env, monkeypatch):
    _browser_auth(env)
    calls = _install_bridge(
        monkeypatch,
        {
            "status": 200,
            "headers": {"Content-Type": "application/json"},
            "body": b'{"result":{}}',
        },
    )
    client = snow._client()

    env.setenv("SERVICENOW_READ_ONLY", "true")
    with pytest.raises(RuntimeError, match="SERVICENOW_READ_ONLY"):
        client.request("POST", "/api/now/table/incident", json_body={})
    env.setenv("SERVICENOW_READ_ONLY", "false")

    preview = client.request(
        "POST",
        "/api/now/table/incident",
        json_body={"short_description": "x"},
        dry_run=True,
    )
    assert preview["dry_run"] is True

    with pytest.raises(ValueError, match="platform-configuration"):
        client.request(
            "POST",
            "/api/now/table/sys_properties",
            json_body={"name": "x"},
            table="sys_properties",
        )

    with pytest.raises(ValueError, match="--force"):
        snow.delete_record("INC0010023")
    with pytest.raises(RuntimeError, match="SERVICENOW_ALLOW_DELETE"):
        snow.delete_record("INC0010023", force=True)
    env.setenv("SERVICENOW_ALLOW_DELETE", "true")
    env.setenv("SERVICENOW_READ_ONLY", "true")
    with pytest.raises(RuntimeError, match="SERVICENOW_READ_ONLY"):
        snow.delete_record("INC0010023", force=True)

    assert calls == []


def test_browser_attachment_upload_limit_stays_before_transport(env, monkeypatch, tmp_path):
    _browser_auth(env)
    calls = _install_bridge(monkeypatch)
    env.setenv("SERVICENOW_MAX_ATTACHMENT_MB", "0")
    path = tmp_path / "too-large.bin"
    path.write_bytes(b"x")

    with pytest.raises(ValueError, match="SERVICENOW_MAX_ATTACHMENT_MB"):
        snow._client().upload_attachment("incident", "a" * 32, path)
    assert calls == []


def test_browser_download_attachment_preserves_bytes_and_cap(env, monkeypatch, tmp_path):
    _browser_auth(env)
    env.setenv("SERVICENOW_MAX_ATTACHMENT_MB", "1")
    calls = _install_bridge(
        monkeypatch,
        {
            "status": 200,
            "headers": {"Content-Type": "application/json"},
            "body": b'{"result":{"file_name":"capture.bin"}}',
        },
        {
            "status": 200,
            "headers": {"Content-Type": "application/octet-stream"},
            "body": b"\x00\x80\xffdownload",
        },
    )

    saved = snow._client().download_attachment("b" * 32, tmp_path)

    assert saved.read_bytes() == b"\x00\x80\xffdownload"
    assert calls[1]["max_bytes"] == 1024 * 1024


def test_missing_browser_bridge_is_a_config_error(env, monkeypatch):
    _browser_auth(env)
    original_import = __import__

    def import_without_bridge(name, *args, **kwargs):
        if name == "agent_browser_core.client":
            raise ModuleNotFoundError("missing agent_browser_core")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", import_without_bridge)
    request = snow.urllib.request.Request(BASE + "/api/now/table/incident")

    with pytest.raises(RuntimeError, match="agent-browser transport") as excinfo:
        snow._browser_http_send(request, 1.0)
    assert excinfo.value.error_class == "config"
