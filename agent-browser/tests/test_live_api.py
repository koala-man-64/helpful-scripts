"""Real cookie-sharing request context, redirects and binary bridge against loopback only."""
import json
import os

import pytest
from agent_browser_core import client
from test_live_expansion import cli, site, live_home, ensure_running

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.getenv("AGENT_BROWSER_LIVE") != "1", reason="opt-in local Edge")]


@pytest.fixture
def api_site(site, monkeypatch):
    handler = site.server.RequestHandlerClass.func
    original = handler.do_GET
    seen = {"redirect_target": 0, "csrf": False, "cookie": False}
    def get(request):
        if request.path == "/api-cookie":
            seen["cookie"] = "qa_session=shared" in request.headers.get("Cookie", "")
            seen["csrf"] = request.headers.get("X-UserToken") == "qa-only-session-key"
            request.send_response(200)
            request.send_header("Content-Type", "application/json")
            request.end_headers()
            request.wfile.write(json.dumps({"authenticated": seen["cookie"], "csrf_present": seen["csrf"]}).encode())
        elif request.path == "/api-redirect":
            request.send_response(302)
            request.send_header("Location", "/redirect-target?code=synthetic-private")
            request.end_headers()
        elif request.path.startswith("/redirect-target"):
            seen["redirect_target"] += 1
            request.send_response(200)
            request.end_headers()
        elif request.path == "/api-binary":
            request.send_response(200)
            request.send_header("Content-Type", "application/octet-stream")
            request.end_headers()
            request.wfile.write(b"binary\x00\xffbody")
        else:
            original(request)
    monkeypatch.setattr(handler, "do_GET", get)
    return site, seen


def test_cookie_sharing_across_sessions_and_internal_csrf(cli, api_site):
    site, seen = api_site
    cli.ok("goto", f"{site.url}/home.html", "--session", "auth-owner")
    cli.ok("eval", "document.cookie='qa_session=shared;path=/'", "--session", "auth-owner")
    cli.ok("goto", f"{site.url}/home.html", "--session", "auth-reader")
    cli.ok("eval", "window.g_ck='qa-only-session-key'", "--session", "auth-reader")
    result = cli.ok("api", f"{site.url}/api-cookie", "--servicenow", "--session", "auth-reader")
    assert result["body"] == {"authenticated": True, "csrf_present": True}
    assert "qa-only-session-key" not in json.dumps(result)
    assert seen["cookie"] and seen["csrf"]


def test_redirect_is_not_followed_and_write_requires_declared_commit(cli, api_site):
    site, seen = api_site
    cli.ok("goto", f"{site.url}/home.html", "--session", "api")
    result = cli.ok("api", f"{site.url}/api-redirect", "--session", "api")
    assert result["status"] == 302 and result["needs_human"]
    assert seen["redirect_target"] == 0 and "synthetic-private" not in json.dumps(result)
    before = site.server.save_calls
    code, _, error = cli("api", f"{site.url}/save", "--method", "POST", "--session", "api")
    assert code == 2 and error["class"] == "guarded" and site.server.save_calls == before
    result = cli.ok("commit", "api", f"{site.url}/save", "--method", "POST", "--session", "api")
    assert result["status"] == 204 and result["executed"]
    assert site.server.save_calls == before + 1
    code, _, error = cli("api", "http://127.0.0.1:1/api-cookie", "--session", "api")
    assert code == 2 and error["class"] == "guarded"


def test_binary_bridge_uses_owned_artifact_without_exposing_browser_state(cli, api_site, monkeypatch):
    site, _ = api_site
    cli.ok("goto", f"{site.url}/home.html", "--session", "binary")
    monkeypatch.setenv("AGENT_BROWSER_HOME", str(cli.home))
    result = client.browser_request(method="GET", url=f"{site.url}/api-binary", headers={}, body=None,
                                    timeout=5, max_bytes=1024, profile="expansion_qa", session_id="binary", servicenow=False)
    assert result["body"] == b"binary\x00\xffbody" and result["status"] == 200
    assert result["command_id"] and result["receipt"]
    state = cli.ok("state", "--command", result["command_id"], "--session", "binary")
    assert json.loads(state["commands"][0]["outcome"])["body_file"]
