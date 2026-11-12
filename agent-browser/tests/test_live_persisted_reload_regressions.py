"""Live native-form cases for the persisted expectation's reload safety guard."""

from __future__ import annotations

import html
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

from test_live_expansion import cli, live_home, pytestmark


pytestmark = [*pytestmark, pytest.mark.skipif(os.getenv("AGENT_BROWSER_LIVE") != "1", reason="set AGENT_BROWSER_LIVE=1 to run live persisted-reload journeys")]


class _PostSite(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.lock = threading.Lock()
        self.direct_posts = 0
        self.redirect_posts = 0
        self.success_gets = 0
        self.post_values: list[str] = []


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        pass

    def _html(self, body: str, *, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/form-direct":
            self._html('<!doctype html><title>Direct form</title><form method="post" action="/direct"><label for="memo">Memo</label><input id="memo" name="memo" value="draft"><button id="submit">Submit direct</button></form>')
            return
        if self.path == "/form-redirect":
            self._html('<!doctype html><title>Redirect form</title><form method="post" action="/redirect"><label for="memo">Memo</label><input id="memo" name="memo" value="draft"><button id="submit">Submit redirect</button></form>')
            return
        if self.path == "/success":
            with self.server.lock:
                self.server.success_gets += 1
            self._html('<!doctype html><title>Redirect success</title><label for="memo">Memo</label><input id="memo" value="redirected-value"><div>Saved after redirect</div>')
            return
        self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        value = parse_qs(body).get("memo", [""])[0]
        with self.server.lock:
            self.server.post_values.append(value)
            if self.path == "/direct":
                self.server.direct_posts += 1
            elif self.path == "/redirect":
                self.server.redirect_posts += 1
        if self.path == "/direct":
            escaped = html.escape("direct-value", quote=True)
            self._html(f'<!doctype html><title>Direct POST success</title><label for="memo">Memo</label><input id="memo" value="{escaped}"><div>Saved direct</div>')
            return
        if self.path == "/redirect":
            self.send_response(303)
            self.send_header("Location", "/success")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404)


@pytest.fixture(scope="module")
def post_site():
    server = _PostSite(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_native_post_is_not_replayed_and_post_redirect_get_remains_verifiable(cli, live_home, post_site) -> None:
    assert live_home.exists()
    server, base = post_site

    cli.ok("goto", f"{base}/form-direct")
    code, _output, error = cli(
        "click", "#submit",
        "--expect", "response:POST **/direct:2xx",
        "--expect", "text:Saved direct",
        "--expect", "persisted:#memo=direct-value",
        timeout=60,
    )
    assert code != 0 and error.get("class") == "verification_unavailable", error
    details = error.get("details", {})
    assert details.get("executed") is True, error
    command_id = details.get("command_id")
    assert command_id, error
    receipt = cli.ok("state", "--command", command_id)
    assert any(command.get("id") == command_id and command.get("status") == "unknown" for command in receipt["commands"])
    with server.lock:
        assert server.direct_posts == 1
        assert server.post_values == ["draft"]

    cli.ok("goto", f"{base}/form-redirect")
    saved = cli.ok(
        "click", "#submit",
        "--expect", "response:GET **/success:2xx",
        "--expect", "text:Saved after redirect",
        "--expect", "persisted:#memo=redirected-value",
        timeout=60,
    )
    assert saved["pending"] is False
    assert saved["executed"] is True
    assert saved["navigated"] is True and saved["refs_valid"] is True
    assert all(item["passed"] for item in saved["expectations"])
    with server.lock:
        assert server.redirect_posts == 1
        assert server.success_gets == 2  # redirect destination plus the one safe persistence reload
        assert server.post_values == ["draft", "draft"]
