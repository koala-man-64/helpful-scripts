"""Authenticated transport boundaries, receipts, and retained artifact contracts."""
import json
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_browser_core import api, cli, common, evidence, mcp_server


class Response:
    def __init__(self, status=200, headers=None, body=b'{"result":[]}'):
        self.status, self.headers, self.data = status, headers or {}, body
        self.disposed = False

    def body(self):
        return self.data

    def dispose(self):
        self.disposed = True


def request_context(ctx, response):
    calls = []

    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        if isinstance(response, Exception):
            raise response
        return response

    ctx.request = SimpleNamespace(fetch=fetch)
    return calls


@pytest.mark.parametrize("url", ["https://other.example/path", "//other.example/path", "http://x:8081/path", "https://x/path", "http://user:pass@x/path"])
def test_api_rejects_nonmatching_origin_before_dispatch(make_daemon, url):
    daemon, ctx, page = make_daemon()
    page.url = "http://x/app"
    calls = request_context(ctx, Response())
    with pytest.raises(ValueError):
        daemon.dispatch("api", {"url": url}, 1)
    assert calls == []


def test_api_uses_cookie_context_without_redirects_or_retries_and_disposes(make_daemon):
    daemon, ctx, page = make_daemon()
    page.url = "http://x/app"
    response = Response(headers={"Content-Type": "application/json", "Set-Cookie": "hidden"})
    calls = request_context(ctx, response)
    result = daemon.dispatch("api", {"url": "/api/items"}, 1)
    assert calls[0][0] == "http://x/api/items"
    assert calls[0][1]["max_redirects"] == calls[0][1]["max_retries"] == 0
    assert result["body"] == {"result": []} and response.disposed
    assert "Set-Cookie" not in result["headers"]


def test_redirect_auth_headers_and_body_do_not_escape(make_daemon):
    daemon, ctx, page = make_daemon()
    page.url = "http://x/app"
    response = Response(302, {"Location": "/login?code=private-code", "Refresh": "0;url=/login?state=private-state",
                              "X-UserToken": "private-key", "WWW-Authenticate": "Negotiate private-challenge",
                              "Proxy-Authenticate": "Negotiate private-proxy"}, b"private-auth-page")
    request_context(ctx, response)
    result = daemon.dispatch("api", {"url": "/api/items"}, 1)
    assert result["needs_human"] and result["handoff_id"]
    assert "private-" not in json.dumps(result)
    assert response.disposed


def test_api_write_requires_commit_and_uncertain_write_is_never_replayed(make_daemon):
    daemon, ctx, page = make_daemon()
    page.url = "http://x/app"
    calls = request_context(ctx, Exception("transport included X-UserToken=private-key"))
    with pytest.raises(ValueError):
        daemon.dispatch("api", {"url": "/api/items", "method": "POST"}, 1)
    assert calls == []
    with pytest.raises(RuntimeError) as err:
        daemon.dispatch("commit", {"operation": "api", "url": "/api/items", "method": "POST"}, 1)
    assert len(calls) == 1 and "private-key" not in str(err.value)
    assert err.value.__suppress_context__
    receipt = err.value.details["command_id"]
    row = daemon.journal.rows("manual", receipt)[0]
    assert row["status"] == "unknown" and json.loads(row["outcome"])["executed"]


def test_api_non_successful_write_receipt_preserves_http_status(make_daemon):
    daemon, ctx, page = make_daemon()
    page.url = "http://x/app"
    calls = request_context(ctx, Response(429))
    result = daemon.dispatch("commit", {"operation": "api", "url": "/api/items", "method": "PATCH"}, 1)
    assert len(calls) == 1 and result["write_outcome"] == "unknown"
    assert result["observed"]["http_errors"] == 1
    row = daemon.journal.rows("manual", result["command_id"])[0]
    assert row["status"] == "unknown" and json.loads(row["outcome"])["status"] == 429


def test_servicenow_auth_stays_inside_daemon_and_absent_token_stops_dispatch(make_daemon, monkeypatch):
    daemon, ctx, page = make_daemon()
    page.url = "https://dev.service-now.com/app"
    page.main_frame.url = page.url
    monkeypatch.setattr(page.main_frame, "evaluate", lambda js: None)
    calls = request_context(ctx, Response())
    with pytest.raises(RuntimeError) as err:
        daemon.dispatch("api", {"url": "/api/now/table/incident", "servicenow": True}, 1)
    assert err.value.error_class == "unsupported_browser_auth" and calls == []
    monkeypatch.setattr(page.main_frame, "evaluate", lambda js: "private-session-key")
    result = daemon.dispatch("api", {"url": "/api/now/table/incident", "servicenow": True}, 1)
    assert calls[0][1]["headers"]["X-UserToken"] == "private-session-key"
    assert "private-session-key" not in json.dumps(result)
    assert "private-session-key" not in json.dumps(daemon.journal.rows("manual"))


def test_api_artifacts_are_session_owned_retained_and_explicitly_cleared(make_daemon):
    daemon, ctx, page = make_daemon()
    page.url = "http://x/app"
    request_context(ctx, Response(body=b"binary\x00payload"))
    result = daemon.dispatch("api", {"url": "/download", "save_response": True}, 1)
    from pathlib import Path
    artifact = Path(result["body_file"])
    assert artifact.read_bytes() == b"binary\x00payload"
    row = daemon.journal.rows("manual", result["command_id"])[0]
    assert json.loads(row["outcome"])["body_file"] == str(artifact)
    daemon.dispatch("commit", {"operation": "journal", "clear": True}, 1)
    assert not artifact.exists()


def test_quota_rejects_complete_output_without_deleting_prior_evidence(tmp_path, monkeypatch):
    root = common._paths("default").dir
    root.mkdir(parents=True)
    keep = root / "receipt.txt"
    keep.write_text("preserve")
    monkeypatch.setattr(common, "_load_config", lambda: {"evidence_max_bytes": 65536})
    with pytest.raises(RuntimeError) as err:
        cli._json_line({"message": "😀" * 20000})
    assert err.value.error_class == "journal_full" and keep.read_text() == "preserve"
    assert not list(root.rglob("*.json"))


def test_concurrent_overflow_paging_is_immutable():
    values = [str(i) + "😀\\\"" * 9000 for i in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        receipts = list(pool.map(lambda value: json.loads(cli._json_line({"value": value})), values))
    from pathlib import Path
    for receipt, expected in zip(receipts, values):
        assert json.loads(Path(receipt["output_file"]).read_text(encoding="utf-8"))["value"] == expected
    assert len({r["output_id"] for r in receipts}) == 6


def test_frame_context_masks_numeric_sign_in_control(make_daemon, monkeypatch):
    daemon, ctx, page = make_daemon()
    page.url = "https://app.example/dashboard"
    monkeypatch.setattr(daemon, "_secret_info", lambda locator, timeout_ms=3000: {"inputmode": "numeric", "maxlength": 6, "text": "Code",
                                                               "context_url": "https://login.example/mfa", "context_title": "Sign in"})
    assert daemon._is_secret(page, object())


def test_mcp_uses_the_same_dispatch_permission_gate(make_daemon, monkeypatch):
    daemon, ctx, page = make_daemon(config={"permission": "act"})

    def invoke(command, arguments, *, committing=False, **kwargs):
        return daemon.dispatch("commit" if committing else command,
                               {**arguments, **({"operation": command} if committing else {})}, 1)

    monkeypatch.setattr(mcp_server.client, "invoke", invoke)
    assert mcp_server.browser_eval("arbitrary()", "manual")["error"]["class"] == "guarded"
    assert mcp_server.browser_commit("click", "manual", target="e1")["error"]["class"] == "guarded"


def test_mcp_upload_and_body_file_cannot_read_profile_token_files(make_daemon, tmp_path, monkeypatch):
    daemon, ctx, page = make_daemon(snapshot_text='- button "Upload" [ref=e1]\n')
    page.elements["e1"].is_file_input = True
    for target in (daemon.paths.session, daemon.paths.launch):
        target.write_text('{"token": "daemon-token"}', encoding="utf-8")
    calls = request_context(ctx, Response())
    monkeypatch.chdir(daemon.paths.dir)  # the daemon's own cwd

    def invoke(command, arguments, *, committing=False, **kwargs):
        # client.invoke sends the MCP server's cwd. An IDE often starts it in the user's home,
        # an ancestor of the default state folder, so that cwd root covers the store.
        arguments = {**arguments, "_cwd": str(tmp_path)}
        return daemon.dispatch("commit" if committing else command,
                               {**arguments, **({"operation": command} if committing else {})}, 1)

    monkeypatch.setattr(mcp_server.client, "invoke", invoke)
    results = [
        mcp_server.browser_action("upload", "manual", target="e1", files=[str(daemon.paths.launch)]),
        mcp_server.browser_commit("upload", "manual", target="e1", files=[str(daemon.paths.session)]),
        mcp_server.browser_commit("api", "manual", url="/items", method="POST", body_file=str(daemon.paths.session)),
    ]
    for result in results:
        assert result["error"]["class"] == "guarded", result
        assert "agent-browser state folder" in result["error"]["message"], result
    assert not calls and not page.elements["e1"].calls


def test_screenshot_refuses_to_replace_existing_file(make_daemon, tmp_path):
    daemon, ctx, page = make_daemon()
    target = tmp_path / "existing.png"
    target.write_bytes(b"original")
    with pytest.raises(ValueError):
        daemon.dispatch("screenshot", {"path": str(target), "_cwd": str(tmp_path)}, 1)
    assert target.read_bytes() == b"original"


@pytest.mark.parametrize("target", [None, "e1"])
def test_page_and_element_screenshot_pass_credential_masks(make_daemon, monkeypatch, target):
    from conftest import FakeLocator, FakePage
    daemon, ctx, page = make_daemon(snapshot_text='- textbox "Name" [ref=e1]\n')
    masks = [object()]
    monkeypatch.setattr(daemon, "_secret_masks", lambda p: masks)
    kind = FakePage if target is None else FakeLocator
    original = kind.screenshot
    calls = []
    def capture(self, **kwargs):
        calls.append(kwargs)
        return original(self, **kwargs)
    monkeypatch.setattr(kind, "screenshot", capture)
    daemon.dispatch("screenshot", {"target": target}, 1)
    assert calls and calls[0]["mask"] is masks


def test_form_invalid_select_preflights_before_filling_any_field(make_daemon, monkeypatch):
    daemon, ctx, page = make_daemon()
    class Field:
        def __init__(self, tag): self.tag = tag
        def evaluate(self, js):
            return {"tag": self.tag, "type": "text", "options": [{"label": "Yes", "value": "yes", "disabled": False}]}
    controls = {"name": Field("input"), "answer": Field("select")}
    monkeypatch.setattr(daemon, "resolve", lambda p, target: controls[target])
    monkeypatch.setattr(daemon, "_secret_check", lambda *a, **k: "clear")
    calls = []
    monkeypatch.setattr(daemon, "h_fill", lambda *a: calls.append(a))
    fields = [{"target": "name", "value": "Rudy"}, {"target": "answer", "operation": "select", "value": "Missing"}]
    with pytest.raises(ValueError, match="nothing was filled"):
        daemon.dispatch("fill-form", {"fields": fields}, 1)
    assert not calls


def test_partial_form_failure_reports_receipt_without_rollback_or_remaining_edits(make_daemon, monkeypatch):
    daemon, ctx, page = make_daemon()
    field = SimpleNamespace(evaluate=lambda js: {"tag": "input", "type": "text"})
    monkeypatch.setattr(daemon, "resolve", lambda *a: field)
    monkeypatch.setattr(daemon, "_secret_check", lambda *a, **k: "clear")
    attempts = []
    def fill(options, timeout):
        attempts.append(options["target"])
        daemon.action_attempted = True
        if options["target"] == "second": raise RuntimeError("field changed")
    monkeypatch.setattr(daemon, "h_fill", fill)
    with pytest.raises(RuntimeError) as err:
        daemon.dispatch("fill-form", {"fields": [{"target": t, "value": "private note"} for t in ("first", "second", "third")]}, 1)
    assert attempts == ["first", "second"]
    assert err.value.details["completed_fields"] == [0] and err.value.details["executed"]
    row = daemon.journal.rows("manual", err.value.details["command_id"])[0]
    assert json.loads(row["outcome"])["completed_fields"] == [0] and row["status"] == "unknown"
    assert "private note" not in json.dumps(row)


def test_api_write_receipt_names_endpoint_path_without_query_or_body(make_daemon):
    daemon, ctx, page = make_daemon()
    page.url = "http://x/app"
    request_context(ctx, Response(204, body=b""))
    result = daemon.dispatch("commit", {"operation": "api", "url": "http://x/api/items/42?sysparm_query=private-filter", "method": "PATCH",
                                        "headers": {"Content-Type": "application/json"}}, 1)
    row = daemon.journal.rows("manual", result["command_id"])[0]
    assert json.loads(row["arguments"])["path"] == "/api/items/42"
    assert "private-filter" not in json.dumps(row)


def test_api_response_satisfies_its_own_response_expectation(make_daemon):
    daemon, ctx, page = make_daemon()
    page.url = "http://x/app"
    request_context(ctx, Response())
    result = daemon.dispatch("api", {"url": "/api/items", "expect": ["response:GET **/api/items:2xx"]}, 5)
    assert result["pending"] is False and result["expectations"] == [{"kind": "response", "passed": True}]
