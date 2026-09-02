"""Live suite: a real headless Edge/Chrome driven through the real CLI, one subprocess per command.

Opt-in because it needs a browser and Playwright:

    AGENT_BROWSER_LIVE=1 python -m pytest -m live

Fixtures are served over a loopback http.server so iframes are same-origin. The state root is a
short folder under %TEMP% (Chromium needs profile paths under 180 chars); a session finalizer stops
every daemon it started and checks that no browser process still references that folder.
Test order matters: one daemon serves the whole module, and a few tests restart it on purpose.
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_browser as ab  # noqa: E402

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.getenv("AGENT_BROWSER_LIVE") != "1", reason="set AGENT_BROWSER_LIVE=1 to run the live suite")]

TOOL = Path(__file__).resolve().parents[1] / "agent_browser.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROFILE = "_live"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass


def _browser_processes(home: Path) -> list[dict]:
    return [p for p in ab._processes_using(str(home)) if p["name"].lower() in ("msedge.exe", "chrome.exe", "node.exe")]


def _wait_for_no_browser(home: Path, timeout: float = 10.0) -> list[dict]:
    """Chromium helpers exit a beat after the main process; poll instead of sampling once."""
    deadline = time.time() + timeout
    leftovers = _browser_processes(home)
    while leftovers and time.time() < deadline:
        time.sleep(0.5)
        leftovers = _browser_processes(home)
    return leftovers


@pytest.fixture(scope="module")
def live_home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="abl", dir=os.getenv("TEMP") or None))
    yield home
    subprocess.run([sys.executable, "-X", "utf8", str(TOOL), "stop", "--all", "--force"], env=_env(home), capture_output=True, timeout=60, check=False)
    leftovers = _wait_for_no_browser(home)
    for proc in leftovers:
        ab._kill_tree(proc["pid"])
    ab._rmtree_retry(home)
    assert not leftovers, f"browser processes survived teardown: {leftovers}"


@pytest.fixture(scope="module")
def base_url() -> str:
    handler = functools.partial(_Quiet, directory=str(FIXTURES))
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _env(home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_BROWSER_")}
    env.update({"AGENT_BROWSER_HOME": str(home), "AGENT_BROWSER_HEADLESS": "1", "PYTHONUTF8": "1"})
    return env


class Cli:
    def __init__(self, home: Path) -> None:
        self.home = home

    def __call__(self, *args: str, timeout: float = 120) -> tuple[int, dict, dict]:
        proc = subprocess.run([sys.executable, "-X", "utf8", str(TOOL), *args, "--profile", PROFILE], capture_output=True,
                              text=True, encoding="utf-8", env=_env(self.home), timeout=timeout)
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
        err = json.loads(proc.stderr).get("error", {}) if proc.stderr.strip().startswith("{") else {"raw": proc.stderr}
        return proc.returncode, out, err

    def ok(self, *args: str, timeout: float = 120) -> dict:
        code, out, err = self(*args, timeout=timeout)
        assert code == 0, (args, err)
        return out


def ref(env: dict, pattern: str) -> str:
    for line in env.get("snapshot") or []:
        if re.search(pattern, line):
            m = re.search(r"\[ref=([^\]]+)\]", line)
            assert m, line
            return m.group(1)
    raise AssertionError(f"no line matches {pattern!r} in {env.get('snapshot')}")


@pytest.fixture(scope="module")
def cli(live_home: Path) -> Cli:
    c = Cli(live_home)
    started = c.ok("start", "--headless")
    assert started["status"] == "started"
    return c


@pytest.fixture(autouse=True)
def ensure_running(cli: Cli) -> None:
    # A few tests stop or kill the daemon on purpose; every test starts from a running browser.
    if cli.ok("status")["running"] is not True:
        assert cli.ok("start", "--headless")["status"] == "started"


# ---------------------------------------------------------------------------


def test_start_survives_the_process_that_spawned_it(cli: Cli) -> None:
    # `start` ran in a CLI process that has already exited; the daemon must still be alive.
    status = cli.ok("status")
    assert status["running"] is True and status["pid"]
    again = cli.ok("start", "--headless")
    assert again["status"] == "already_running"


def test_goto_outer_exposes_iframe_refs(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/outer.html")
    assert env["navigated"] is True
    lines = env["snapshot"]
    assert any("[ref=e" in l for l in lines)
    frame_refs = [l for l in lines if re.search(r"\[ref=f\d+e\d+\]", l)]
    assert frame_refs, lines
    assert env["frames"][0]["name"] == "gsft_main"
    prefix = env["frames"][0]["prefix"]
    assert all(f"[ref={prefix}e" in l for l in frame_refs)
    assert any(l.startswith('- iframe "gsft_main"') for l in lines)


def test_fill_and_click_inside_iframe(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/outer.html")
    caller, save = ref(env, r'textbox "Caller"'), ref(env, r'button "Save"')
    filled = cli.ok("fill", caller, "Abel")
    assert filled["value_after"] == "Abel" and filled["navigated"] is False
    assert any("Abel" in l for l in filled["changes"]["added"])
    cli.ok("click", save)
    assert "saved:Abel" in cli.ok("text")["text"]


def test_frame_navigation_makes_old_refs_stale(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/outer.html")
    save = ref(env, r'button "Save"')
    cli.ok("eval", "document.querySelector('iframe').src = 'inner.html?nav=1'")
    time.sleep(0.8)
    code, _out, err = cli("click", save)
    assert code == 2 and err["class"] == "stale_ref", err
    env2 = cli.ok("snapshot")
    cli.ok("click", ref(env2, r'button "Save"'))


def test_reload_invalidates_iframe_refs(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/outer.html")
    save = ref(env, r'button "Save"')
    reloaded = cli.ok("reload")
    new_save = ref(reloaded, r'button "Save"')
    if new_save != save:
        code, _out, err = cli("click", save)
        assert code == 2 and err["class"] == "stale_ref", err
    cli.ok("click", new_save)


def test_rows_refs_stay_bound_to_elements(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/rows.html")
    remove_refs = [re.search(r"\[ref=([^\]]+)\]", l).group(1) for l in env["snapshot"] if 'button "Remove"' in l]  # type: ignore[union-attr]
    assert len(remove_refs) == 10
    cli.ok("click", remove_refs[2])
    cli.ok("click", remove_refs[6])  # the ref from before the first click still points at row 7
    assert cli.ok("eval", "document.querySelector('[data-row=\"7\"]') === null")["value"] is True
    assert cli.ok("eval", "document.querySelectorAll('tr').length")["value"] == 8


def test_secret_fields_are_refused(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/secret.html")
    assert env["sign_in_suspected"] is True and "password_field" in env["sign_in_reasons"]
    for pattern in (r'textbox "Password" \[', r'textbox "Verification code"', r'textbox "Password \(rich\)"'):
        code, _out, err = cli("fill", ref(env, pattern), "hunter2")
        assert code == 2 and err["class"] == "guarded", (pattern, err)
    # A targetless key press goes to the focused element; focusing the password field must not open a side door.
    cli.ok("click", ref(env, r'textbox "Password" \['))
    code, _out, err = cli("press", "x")
    assert code == 2 and err["class"] == "guarded", err
    cli.ok("press", "Tab")
    code, _out, err = cli("press", "a", ref(env, r'textbox "Password"'))
    assert code == 2 and err["class"] == "guarded"
    cli.ok("press", "Tab", ref(env, r'textbox "Password"'))
    assert cli.ok("fill", ref(env, r'textbox "Username"'), "rudy")["value_after"] == "rudy"
    assert cli.ok("value", ref(env, r'textbox "Password"'))["masked"] is True


def test_wait_signed_in_ignores_the_login_page(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/login.html")
    assert env["sign_in_suspected"] is True
    waited = cli.ok("wait", "--signed-in", "127.0.0.1", "--timeout", "20")
    assert "signed_in" in waited["satisfied"] and waited["url"].endswith("home.html")


def test_wait_timeout_is_exit_124_with_a_repeat_hint(cli: Cli, base_url: str) -> None:
    cli.ok("goto", f"{base_url}/home.html")
    code, _out, err = cli("wait", "--text", "never-there", "--timeout", "2")
    assert code == 124 and err["class"] == "timeout" and "same wait" in err["hint"]
    code, _out, err = cli("wait", "--text", "x", "--timeout", "200")
    assert code == 2 and err["class"] == "validation"


def test_dialogs_are_dismissed_unless_accepted(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/dialogs.html")
    confirm, alert = ref(env, r'button "Confirm"'), ref(env, r'button "Alert"')
    code, _out, err = cli("click", confirm)
    assert code == 2 and err["class"] == "dialog", err
    accepted = cli.ok("click", confirm, "--accept-dialog")
    assert any(d["handled"] == "accepted" for d in accepted["dialogs"])
    assert "confirm:true" in cli.ok("text")["text"]
    alerted = cli.ok("click", alert)
    assert any(d["type"] == "alert" and d["handled"] == "accepted" for d in alerted["dialogs"])
    cli("click", ref(env, r'button "Timer confirm"'))
    cli.ok("wait", "--seconds", "1")
    assert "timer:false" in cli.ok("text")["text"]


def test_unsaved_changes_block_navigation_until_discarded(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/dirty.html")
    cli.ok("fill", ref(env, r'textbox "Note"'), "draft")
    code, _out, err = cli("goto", f"{base_url}/home.html")
    assert code == 2 and err["class"] == "unsaved_changes", err
    assert cli.ok("status")["url"].endswith("dirty.html")
    left = cli.ok("goto", f"{base_url}/home.html", "--discard-changes")
    assert left["url"].endswith("home.html")


def test_popup_switches_tabs_and_tab_close(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/popup.html")
    clicked = cli.ok("click", ref(env, r'link "Open popup"'))
    assert clicked["new_tab"] and clicked["new_tab"]["index"] == 2
    assert clicked["url"].endswith("home.html")
    tabs = cli.ok("tabs")
    assert len(tabs["tabs"]) == 2 and tabs["current"] == 2
    first = cli.ok("tab", "1")
    assert first["url"].endswith("popup.html")
    closed = cli.ok("tab", "2", "--close")
    assert closed["tabs"] == 1
    code, _out, err = cli("tab", "1", "--close")
    assert code == 2 and err["class"] == "validation"


def test_downloads_land_under_the_profile(cli: Cli, base_url: str, live_home: Path) -> None:
    env = cli.ok("goto", f"{base_url}/download.html")
    clicked = cli.ok("click", ref(env, r'link "Download small"'))
    assert clicked["downloads"] and clicked["downloads"][0]["file"] == "hello.txt"
    saved = cli.ok("downloads")
    path = Path(saved["downloads"][0]["path"])
    assert path.is_file() and path.parent == live_home / "profiles" / PROFILE / "downloads"
    assert path.read_text(encoding="utf-8").startswith("hello")


def test_big_page_respects_the_byte_budget(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/big.html")
    assert env["truncated"] is True and env["lines"] > len(env["snapshot"])
    assert len(json.dumps(env)) < 30000
    assert Path(env["snapshot_file"]).read_text(encoding="utf-8").count("\n") + 1 == env["lines"]
    found = cli.ok("snapshot", "--find", "Item 1999")
    assert any("Item 1999" in l for l in found["snapshot"])


def test_page_text_never_reaches_hints(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/inject.html")
    code, _out, err = cli("select", ref(env, r'combobox "Pick"'), "zzz")
    assert code == 2 and err["hint"] == ab._hint("select_not_found", PROFILE)
    assert any("agent-browser stop" in o for o in err["details"]["options"])
    assert env["untrusted"] == ab.UNTRUSTED_KEYS
    assert not any(re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", l) for l in env["snapshot"])


def test_hidden_tab_panel_needs_a_tab_click(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/tabs.html")
    missing = cli.ok("snapshot", "--find", "Work notes")
    assert missing["snapshot"] == [] and "No line matched" in missing.get("note", "")
    cli.ok("click", ref(env, r'tab "Notes"'))
    assert any("Work notes" in l for l in cli.ok("snapshot", "--find", "Work notes")["snapshot"])


def test_focus_fires_when_the_window_is_not_in_front(cli: Cli, base_url: str) -> None:
    env = cli.ok("goto", f"{base_url}/focus.html")
    cli.ok("fill", ref(env, r'textbox "Focus me"'), "x")
    assert cli.ok("eval", "window.f")["value"] == 1


def test_late_iframe_is_reached_with_wait(cli: Cli, base_url: str) -> None:
    # The iframe is created 1.5 s after load, so `goto` legitimately returns a page without it;
    # `wait --text` is the documented way to reach late-rendered frames (ServiceNow Polaris does this).
    env = cli.ok("goto", f"{base_url}/outer_late.html")
    assert env["title"] == "Outer late"
    waited = cli.ok("wait", "--text", "Caller", "--timeout", "15")
    assert any('textbox "Caller"' in l and re.search(r"\[ref=f\d+e", l) for l in waited["snapshot"])
    assert waited["frames"] and waited["frames"][0]["name"] == "gsft_main"


def test_screenshot_is_a_png_with_dimensions(cli: Cli, base_url: str) -> None:
    cli.ok("goto", f"{base_url}/home.html")
    shot = cli.ok("screenshot")
    path = Path(shot["path"])
    assert path.is_file() and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert shot["width"] > 0 and shot["height"] > 0 and "/" in shot["path"]


def test_cookie_survives_stop_and_start(cli: Cli, base_url: str) -> None:
    cli.ok("goto", f"{base_url}/home.html")
    # A cookie without max-age is a session cookie that no browser keeps across a restart.
    cli.ok("eval", "document.cookie = 'ab_live=1; path=/; max-age=3600'")
    assert cli.ok("stop")["stopped"] is True
    assert cli.ok("status")["running"] is False
    assert cli.ok("start", "--headless")["status"] == "started"
    cli.ok("goto", f"{base_url}/home.html")
    assert "ab_live=1" in cli.ok("eval", "document.cookie")["value"]


def test_killed_daemon_is_reported_and_start_recovers(cli: Cli, base_url: str) -> None:
    pid = cli.ok("status")["pid"]
    ab._kill_tree(pid)
    deadline = time.time() + 10
    while time.time() < deadline and ab._pid_alive(pid):
        time.sleep(0.2)
    status = cli.ok("status")
    assert status["running"] is False and status["reason"] in ("pid_dead", "no_session")
    assert cli.ok("start", "--headless")["status"] == "started"
    assert cli.ok("goto", f"{base_url}/home.html")["title"] == "Home"


def test_stop_leaves_nothing_behind(cli: Cli, live_home: Path) -> None:
    pid = cli.ok("status")["pid"]
    assert cli.ok("stop")["stopped"] is True
    assert not ab._pid_alive(pid)
    assert cli.ok("status")["running"] is False
    assert _wait_for_no_browser(live_home) == []
