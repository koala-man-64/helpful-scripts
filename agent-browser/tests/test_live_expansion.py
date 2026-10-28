"""Independent headless-browser journeys against loopback expansion fixtures."""

from __future__ import annotations

import functools
import html
import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_browser_core import common as core_common


pytestmark = [pytest.mark.live, pytest.mark.skipif(os.getenv("AGENT_BROWSER_LIVE") != "1", reason="set AGENT_BROWSER_LIVE=1 to run live expansion journeys")]

TOOL = Path(__file__).resolve().parents[1] / "agent_browser.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROFILE = "expansion_qa"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    # Loopback hops that send the browser outside loopback; .invalid hosts never resolve, so nothing can be reached.
    REDIRECTS = {"/redirect-chain": "/redirect-external", "/redirect-external": "http://login.example.invalid/authorize",
                 "/redirect-image": "http://hop.example.invalid/pixel.png"}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path in self.REDIRECTS:
            self.send_response(302)
            self.send_header("Location", self.REDIRECTS[parsed.path])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parsed.path != "/record":
            super().do_GET()
            return
        query = urllib.parse.parse_qs(parsed.query)
        sys_id = query.get("sys_id", [""])[0]
        number = query.get("number", ["INC0010023"])[0]
        server = self.server
        with server.save_lock:
            server.record_gets += 1
            note = server.record_notes.get(sys_id, "")
        document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Local record form</title></head>
<body>
  <label for="record_number">Number</label><input id="record_number" value="{html.escape(number, quote=True)}" readonly>
  <label for="work_notes">Work notes</label><textarea id="work_notes"></textarea>
  <button id="sysverb_update" type="button">Update</button>
  <div id="activity">{html.escape(note)}</div>
  <script>
    document.querySelector('#sysverb_update').addEventListener('click', () => fetch(location.href, {{
      method: 'POST', headers: {{'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'}},
      body: new URLSearchParams({{work_notes: document.querySelector('#work_notes').value}})
    }}));
  </script>
</body></html>""".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(document)))
        self.end_headers()
        self.wfile.write(document)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/record":
            query = urllib.parse.parse_qs(parsed.query)
            sys_id = query.get("sys_id", [""])[0]
            note = urllib.parse.parse_qs(body).get("work_notes", [""])[0]
            with self.server.save_lock:
                self.server.record_posts += 1
                if query.get("persist", ["1"])[0] != "0":
                    self.server.record_notes[sys_id] = note
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if parsed.path != "/save":
            self.send_error(404)
            return
        server = self.server
        with server.save_lock:
            server.save_calls += 1
        self.send_response(204)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()


class _FixtureServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.save_calls = 0
        self.record_gets = 0
        self.record_posts = 0
        self.record_notes: dict[str, str] = {}
        self.save_lock = threading.Lock()


class _Site:
    def __init__(self, server: _FixtureServer) -> None:
        self.server = server
        self.url = f"http://127.0.0.1:{server.server_address[1]}"


class Cli:
    def __init__(self, home: Path) -> None:
        self.home = home

    def argv(self, *args: str) -> list[str]:
        return [sys.executable, "-X", "utf8", str(TOOL), *args, "--profile", PROFILE]

    def __call__(self, *args: str, timeout: float = 120) -> tuple[int, dict, dict]:
        proc = subprocess.run(self.argv(*args), capture_output=True, text=True, encoding="utf-8",
                              env=_env(self.home), timeout=timeout)
        output = json.loads(proc.stdout) if proc.stdout.strip() else {}
        error = json.loads(proc.stderr).get("error", {}) if proc.stderr.strip().startswith("{") else {"raw": proc.stderr}
        return proc.returncode, output, error

    def ok(self, *args: str, timeout: float = 120) -> dict:
        code, output, error = self(*args, timeout=timeout)
        if code != 0:
            # Preserve the daemon's exception class before module teardown removes
            # this temporary profile. Its log excludes argument values and bodies.
            log = self.home / "profiles" / PROFILE / "daemon.log"
            tail = core_common._tail(log, lines=12)
            pytest.fail(repr((args, error, {"daemon_log_tail": tail})))
        return output


def _env(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_BROWSER_")}
    env.update({"AGENT_BROWSER_HOME": str(home), "AGENT_BROWSER_HEADLESS": "1", "PYTHONUTF8": "1"})
    return env


def _browser_processes(home: Path) -> list[dict]:
    return [proc for proc in core_common._processes_using(str(home))
            if proc["name"].lower() in {"msedge.exe", "chrome.exe", "node.exe"}]


def _stop_and_cleanup(home: Path) -> None:
    subprocess.run([sys.executable, "-X", "utf8", str(TOOL), "stop", "--all", "--force", "--profile", PROFILE],
                   env=_env(home), capture_output=True, timeout=60, check=False)
    deadline = time.time() + 10
    leftovers = _browser_processes(home)
    while leftovers and time.time() < deadline:
        time.sleep(0.25)
        leftovers = _browser_processes(home)
    for proc in leftovers:
        core_common._kill_tree(proc["pid"])
    core_common._rmtree_retry(home)
    assert not leftovers, f"browser processes survived expansion test teardown: {leftovers}"


@pytest.fixture(scope="module")
def site() -> _Site:
    handler = functools.partial(_QuietHandler, directory=str(FIXTURES))
    server = _FixtureServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield _Site(server)
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def live_home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="abx", dir=os.getenv("TEMP") or None))
    yield home
    _stop_and_cleanup(home)


@pytest.fixture(scope="module")
def cli(live_home: Path) -> Cli:
    client = Cli(live_home)
    assert client.ok("start", "--headless")["status"] == "started"
    return client


@pytest.fixture(autouse=True)
def ensure_running(cli: Cli) -> None:
    if cli.ok("status")["running"] is not True:
        assert cli.ok("start", "--headless")["status"] in {"started", "already_running"}


def _ref(env: dict, pattern: str) -> str:
    for line in env.get("snapshot") or []:
        if re.search(pattern, line):
            match = re.search(r"\[ref=([^\]]+)\]", line)
            assert match, line
            return match.group(1)
    raise AssertionError(f"no line matches {pattern!r} in {env.get('snapshot')}")


def test_semantic_ambiguity_spans_main_page_and_iframe(cli: Cli, site: _Site) -> None:
    session = "semantic"
    cli.ok("goto", f"{site.url}/expansion_ambiguous.html", "--session", session)

    code, _output, error = cli("click", "--role", "button", "--name", "Save", "--session", session)

    assert code != 0 and error["class"] == "ambiguous", error
    candidates = error["details"]["candidates"]
    assert len(candidates) == 2
    assert {candidate["frame"] for candidate in candidates} == {"", "dialog_frame"}
    state = cli.ok("text", "--session", session)["text"]
    assert "main:0" in state and "frame:0" in state


def test_form_preflight_leaves_earlier_fields_untouched(cli: Cli, site: _Site) -> None:
    session = "form"
    cli.ok("goto", f"{site.url}/expansion_form.html", "--session", session)
    form_dir = core_common.ProfilePaths(PROFILE, cli.home, session).downloads
    form_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", prefix="qa-form-",
                                     dir=form_dir, delete=False) as form_handle:
        json.dump([
            {"target": {"label": "Display name"}, "operation": "fill", "value": "after"},
            {"target": {"label": "Password"}, "operation": "fill", "value": "synthetic-placeholder"},
        ], form_handle)
        form_file = Path(form_handle.name)
    try:
        code, _output, error = cli("fill-form", "--file", str(form_file), "--session", session)

        assert code != 0 and error["class"] == "guarded", error
        assert "credential field" in error["message"], error
        values = cli.ok("eval", "JSON.stringify([...document.querySelectorAll('input')].map(el => el.value))",
                        "--session", session)["value"]
        assert json.loads(values) == ["before", ""]
    finally:
        form_file.unlink(missing_ok=True)


def test_expectations_report_success_and_failure_without_replaying_click(cli: Cli, site: _Site) -> None:
    session = "expectation"
    cli.ok("goto", f"{site.url}/expansion_save.html", "--session", session)

    success = cli.ok("click", "--role", "button", "--name", "Save", "--expect", "response:POST **/save:2xx", "--timeout", "5",
                     "--session", session)
    assert success["expectations"] == [{"kind": "response", "passed": True}]
    assert success["refs_valid"] is True
    assert site.server.save_calls == 1

    code, _output, error = cli("click", "--role", "button", "--name", "Save", "--expect", "text:never appears", "--timeout", "0.5",
                                "--session", session)
    assert code != 0 and error["class"] == "timeout", error
    assert error["details"]["executed"] is True
    assert error["details"]["command_id"]
    assert site.server.save_calls == 2
    assert "2" in cli.ok("text", "--session", session)["text"]

    # "saved" shows before this click and after it: text already on the page is no evidence of the action.
    code, _output, error = cli("click", "--role", "button", "--name", "Save", "--expect", "text:saved", "--timeout", "1",
                                "--session", session)
    assert code != 0 and error["class"] == "timeout", error
    assert error["details"]["executed"] is True
    assert site.server.save_calls == 3


def test_wait_in_one_session_does_not_block_another_session_read(cli: Cli, site: _Site) -> None:
    waiter, reader = "slow-reader", "fast-reader"
    cli.ok("goto", f"{site.url}/expansion_wait.html", "--session", waiter)
    cli.ok("goto", f"{site.url}/home.html", "--session", reader)
    wait_proc = subprocess.Popen(
        cli.argv("wait", "--seconds", "100", "--timeout", "100", "--session", waiter),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=_env(cli.home),
    )
    try:
        time.sleep(0.6)
        started = time.perf_counter()
        read = cli.ok("text", "--session", reader)
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"second session read took {elapsed:.2f}s while the first session waited"
        assert "Home" in read["text"]

        cli.ok("stop", "--session", waiter)
        stdout, stderr = wait_proc.communicate(timeout=12)
        assert wait_proc.returncode != 0, (stdout, stderr)
        assert "Home" in cli.ok("text", "--session", reader)["text"]
    finally:
        if wait_proc.poll() is None:
            cli("stop", "--session", waiter)
            wait_proc.kill()
            wait_proc.communicate(timeout=3)


def test_popup_tabs_and_stop_are_scoped_to_their_session(cli: Cli, site: _Site) -> None:
    owner, other = "popup-owner", "popup-other"
    cli.ok("goto", f"{site.url}/expansion_popup.html", "--session", owner)
    cli.ok("click", "--role", "button", "--name", "Open popup", "--session", owner)
    owner_tabs = cli.ok("tabs", "--session", owner)["tabs"]
    assert len(owner_tabs) == 2
    assert any("expansion_popup_child.html" in tab.get("url", "") for tab in owner_tabs)

    cli.ok("goto", f"{site.url}/home.html", "--session", other)
    assert len(cli.ok("tabs", "--session", other)["tabs"]) == 1
    cli.ok("stop", "--session", owner)

    assert cli.ok("status")["running"] is True
    assert "Home" in cli.ok("text", "--session", other)["text"]


def test_table_returns_clickable_iframe_row_refs(cli: Cli, site: _Site) -> None:
    session = "table"
    cli.ok("goto", f"{site.url}/expansion_table.html", "--session", session)

    result = cli.ok("table", "--session", session)

    assert result["columns"] == ["Item", "Owner"]
    assert result["rows"][0]["cells"] == ["Review", "Rudy"]
    row_ref = result["rows"][0]["ref"]
    assert re.fullmatch(r"f\d+e\d+", row_ref), row_ref
    current_snapshot = cli.ok("snapshot", "--full", "--session", session)
    row_lines = [line for line in current_snapshot.get("snapshot", [])
                 if re.search(r"^\s*- row(?: |\[|:)", line) and f"[ref={row_ref}]" in line]
    assert row_lines, current_snapshot.get("snapshot")
    current_row_ref = re.search(r"\[ref=([^\]]+)\]", row_lines[0]).group(1)
    assert current_row_ref == row_ref, {"table_ref": row_ref, "snapshot_row": row_lines[0]}
    ref_probe = cli.ok("eval", "el => JSON.stringify({tag:el.tagName,text:el.innerText,rect:el.getBoundingClientRect().toJSON(),display:getComputedStyle(el).display,visibility:getComputedStyle(el).visibility,html:el.outerHTML.slice(0,500)})",
                       "--ref", row_ref, "--session", session)
    frame_probe = cli.ok("eval", "(() => { const f=document.querySelector('iframe'); const fr=f.getBoundingClientRect(); const r=f.contentDocument.querySelector('tbody tr'); const rr=r.getBoundingClientRect(); const x=rr.left+rr.width/2, y=rr.top+rr.height/2; return JSON.stringify({frame:fr.toJSON(),row:rr.toJSON(),pagePoint:{x:fr.left+x,y:fr.top+y},hit:f.contentDocument.elementFromPoint(x,y)?.outerHTML.slice(0,250),scroll:{x:scrollX,y:scrollY}}); })()",
                         "--session", session)
    code, _output, error = cli("click", row_ref, "--session", session, "--timeout", "5")
    assert code == 0, json.dumps({"row_ref": row_ref, "snapshot_row": row_lines[0], "ref_probe": ref_probe, "frame_probe": frame_probe, "table": result, "error": error})
    assert "row selected" in cli.ok("text", "--session", session)["text"]


def test_table_reads_a_grid_only_page(cli: Cli, site: _Site) -> None:
    session = "grid"
    cli.ok("goto", f"{site.url}/expansion_grid.html", "--session", session)

    result = cli.ok("table", "--session", session)

    assert result["columns"] == ["Item", "Owner"]
    assert [row["cells"] for row in result["rows"]] == [["Review", "Rudy"]]


def test_recipe_preview_without_a_record_form_opens_no_handoff(cli: Cli, site: _Site) -> None:
    session = "recipe-blocked"
    cli.ok("goto", f"{site.url}/home.html", "--session", session)

    preview = cli.ok("run", "sn/add-work-note", "--number", "INC0010023", "--text", "note", "--session", session)

    assert preview["blocked"] is True and preview["requires_commit"] is False
    assert "gsft_main" in preview["blocked_reason"] and "handoff_id" not in preview
    assert cli.ok("state", "--session", session)["handoffs"] == []


def test_recipe_preview_and_commit_use_one_local_post_then_readback(cli: Cli, site: _Site) -> None:
    session, number = "recipe", "INC0010023"
    note = "synthetic loopback work note"
    cli.ok("goto", f"{site.url}/expansion_recipe.html", "--session", session)
    initial_gets = site.server.record_gets
    initial_posts = site.server.record_posts

    preview = cli.ok("run", "sn/add-work-note", "--number", number, "--text", note, "--session", session)

    assert preview["requires_commit"] is True
    assert preview["preview"] == {"number": number, "work_note": note}
    assert site.server.record_gets == initial_gets and site.server.record_posts == initial_posts

    committed = cli.ok("commit", "run", "sn/add-work-note", "--number", number, "--text", note,
                       "--session", session, "--timeout", "5")

    assert committed["executed"] is True and committed["persisted"] is True
    assert committed["number"] == number
    assert site.server.record_posts == initial_posts + 1
    assert site.server.record_gets == initial_gets + 1
    assert site.server.record_notes["0123456789abcdef0123456789abcdef"] == note


def test_raw_request_cannot_forge_the_commit_flag_to_submit_a_recipe(cli: Cli, site: _Site) -> None:
    from agent_browser_core import session as core_session
    session, number = "recipe-forged", "INC0010023"
    cli.ok("goto", f"{site.url}/expansion_recipe.html", "--session", session)
    initial_posts = site.server.record_posts
    state = core_session._read_session(core_common.ProfilePaths(PROFILE, cli.home))

    result = core_session._request(state, "run", {"recipe": "sn/add-work-note", "number": number, "text": "forged note",
                                                  "_commit": True, "_session_id": session}, 10)

    assert result["requires_commit"] is True and "screenshots" not in result
    assert site.server.record_posts == initial_posts
    receipt = cli.ok("state", "--command", result["command_id"], "--session", session)["commands"][0]
    assert receipt["verb"] == "run"


def test_recipe_identity_mismatch_blocks_commit_before_post(cli: Cli, site: _Site) -> None:
    session, requested = "recipe-mismatch", "INC0010023"
    cli.ok("goto", f"{site.url}/expansion_recipe_mismatch.html", "--session", session)
    initial_posts = site.server.record_posts

    code, _output, error = cli("commit", "run", "sn/add-work-note", "--number", requested,
                               "--text", "synthetic note", "--session", session, "--timeout", "3")

    assert code != 0 and error["class"] == "guarded", error
    assert "identity" in error["message"].lower()
    assert site.server.record_posts == initial_posts
    values = json.loads(cli.ok("eval", "JSON.stringify(document.querySelector('#work_notes').value)",
                               "--frame", "gsft_main", "--session", session)["value"])
    assert values == ""


def test_recipe_missing_readback_returns_unknown_without_replaying_post(cli: Cli, site: _Site) -> None:
    session, number = "recipe-unknown", "INC0010023"
    cli.ok("goto", f"{site.url}/expansion_recipe_missing.html", "--session", session)
    initial_gets, initial_posts = site.server.record_gets, site.server.record_posts

    code, _output, error = cli("commit", "run", "sn/add-work-note", "--number", number,
                               "--text", "synthetic note not persisted", "--session", session, "--timeout", "5")

    assert code != 0 and error["class"] == "timeout", error
    assert error["details"]["executed"] is True and error["details"]["command_id"]
    assert "do not replay" in error["message"].lower()
    assert site.server.record_posts == initial_posts + 1
    assert site.server.record_gets == initial_gets + 1
    assert "2123456789abcdef0123456789abcdef" not in site.server.record_notes


def test_annotated_screenshot_cleans_up_and_places_iframe_labels(cli: Cli, site: _Site) -> None:
    session = "geometry"
    page = cli.ok("goto", f"{site.url}/expansion_geometry.html", "--session", session)
    inside_ref = _ref(page, r'button "Inside frame"')
    cli.ok("eval", "window.scrollTo(0, 60); 1", "--session", session)
    cli.ok("eval", "window.__abAppend = Element.prototype.append; Element.prototype.append = function(...nodes) { if (this.id === '__agent_browser_refs__') window.__abLabels = nodes.map(node => ({ref: node.textContent, left: node.style.left, top: node.style.top})); return window.__abAppend.apply(this, nodes); }; 1",
           "--session", session)

    capture = cli.ok("screenshot", "--annotate", "--session", session)

    assert capture["annotated"] > 0
    labels = json.loads(cli.ok("eval", "JSON.stringify(window.__abLabels || [])", "--session", session)["value"])
    inside = next(label for label in labels if label["ref"] == inside_ref)
    expected = json.loads(cli.ok("eval", "(() => { const frame=document.querySelector('iframe'); const a=frame.getBoundingClientRect(); const b=frame.contentDocument.querySelector('#inside').getBoundingClientRect(); return JSON.stringify({x:a.x+b.x+scrollX,y:a.y+b.y+scrollY}); })()",
                                 "--session", session)["value"])
    assert abs(float(inside["left"][:-2]) - expected["x"]) <= 2
    assert abs(float(inside["top"][:-2]) - expected["y"]) <= 2
    assert cli.ok("eval", "document.getElementById('__agent_browser_refs__') === null", "--session", session)["value"] is True


def test_local_diagnostics_keep_the_working_tab(cli: Cli, site: _Site) -> None:
    session = "diagnostics"
    cli.ok("goto", f"{site.url}/home.html", "--session", session)

    result = cli.ok("check", f"{site.url}/expansion_diagnostic.html", "--session", session)

    assert result["status"] == 200
    assert any("synthetic fixture diagnostic" in line for line in result["console_errors"])
    assert any(item["status"] == 404 for item in result["http_errors"])
    assert result["blocked_external"] == [] and result["popups_closed"] == 0
    assert "Home" in cli.ok("text", "--session", session)["text"]


def test_local_diagnostics_refuse_a_redirect_chain_off_loopback(cli: Cli, site: _Site) -> None:
    session = "diagnostics-redirect"
    cli.ok("goto", f"{site.url}/home.html", "--session", session)

    code, _output, error = cli("check", f"{site.url}/redirect-chain", "--session", session)

    # Page routes let redirect hops through, so this is the DevTools Fetch guard refusing the second hop.
    assert code != 0 and error["class"] == "guarded", error
    assert error["details"]["blocked_external"] == ["login.example.invalid"]
    assert len(cli.ok("tabs", "--session", session)["tabs"]) == 1
    assert "Home" in cli.ok("text", "--session", session)["text"]


def test_local_diagnostics_block_outside_requests_sockets_and_popups(cli: Cli, site: _Site) -> None:
    session = "diagnostics-outside"
    cli.ok("goto", f"{site.url}/home.html", "--session", session)

    result = cli.ok("check", f"{site.url}/expansion_diagnostic_external.html", "--session", session)

    assert result["status"] == 200 and "blocked_external" in result["untrusted"]
    assert {"img.example.invalid", "hop.example.invalid", "ws.example.invalid"} <= set(result["blocked_external"]), result
    assert result["popups_closed"] == 1
    assert len(cli.ok("tabs", "--session", session)["tabs"]) == 1
    assert "Home" in cli.ok("text", "--session", session)["text"]
