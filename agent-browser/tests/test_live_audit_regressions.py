"""Real local-browser regressions for caller-relative paths and live diagnostics."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_browser_core import common, mcp_server, session
from test_live_expansion import site
import live_support


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("AGENT_BROWSER_LIVE") != "1", reason="set AGENT_BROWSER_LIVE=1 to run the live audit regressions"),
]

TOOL = Path(__file__).resolve().parents[1] / "agent_browser.py"
PROFILE = "mcp_path_audit"
SESSION = "caller-relative-audit"


def _env(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_BROWSER_")}
    env.pop("CODEX_THREAD_ID", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update({"AGENT_BROWSER_HOME": str(home), "AGENT_BROWSER_HEADLESS": "1", "PYTHONUTF8": "1"})
    return env


@pytest.fixture
def isolated_mcp_browser(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    root = Path(tempfile.mkdtemp(prefix="abm", dir=os.getenv("TEMP") or None))
    failures = live_support.FailureWatch(request)
    ready = False
    home, daemon_cwd, caller_cwd = (root / name for name in ("home", "daemon", "caller"))
    for path in (home, daemon_cwd, caller_cwd):
        path.mkdir()
    env = _env(home)
    monkeypatch.setenv("AGENT_BROWSER_HOME", str(home))
    monkeypatch.setenv("AGENT_BROWSER_HEADLESS", "1")

    start = subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL), "start", "--headless", "--profile", PROFILE],
        cwd=daemon_cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    daemon_pid = 0
    try:
        result = json.loads(start.stdout) if start.stdout.strip() else {}
        daemon_pid = int(result.get("pid") or 0)
        assert start.returncode == 0 and result.get("status") == "started", (start.stdout, start.stderr)
        ready = True
        yield {"home": home, "daemon_cwd": daemon_cwd, "caller_cwd": caller_cwd, "env": env, "pid": daemon_pid}
    finally:
        if start.returncode == 0:
            subprocess.run(
                [sys.executable, "-X", "utf8", str(TOOL), "stop", "--all", "--force", "--profile", PROFILE],
                cwd=daemon_cwd, env=env, capture_output=True, timeout=60, check=False,
            )
        daemon_left = bool(daemon_pid) and not live_support.wait_for_pid_exit(daemon_pid, live_support.EXIT_GRACE)
        if daemon_left:
            common._kill_tree(daemon_pid)
        blocking = live_support.settle_browsers(home)
        if not ready or failures.failed or blocking or daemon_left:
            live_support.keep_evidence(home, request.node.name)
        common._rmtree_retry(root)
        assert not blocking and not daemon_left, (
            f"test-owned browser processes survived teardown: {blocking}; daemon pid {daemon_pid}"
        )


@pytest.fixture
def isolated_doctor_home(request: pytest.FixtureRequest):
    home = Path(tempfile.mkdtemp(prefix="abd", dir=os.getenv("TEMP") or None))
    failures = live_support.FailureWatch(request)
    env = _env(home)
    paths = common.ProfilePaths(common.INTERNAL_DATA_PROFILE, home)
    try:
        yield home, env
    finally:
        # The live probe normally stops itself. This is a fallback scoped to its private home.
        try:
            session.stop_session(paths, force=True)
        except (RuntimeError, ValueError, OSError):
            pass
        blocking = live_support.finish_home(home, failures, request.node.name, processes_under=paths.user_data)
        assert not blocking, f"doctor probe browser processes survived teardown: {blocking}"


def test_mcp_relative_api_body_and_upload_use_caller_directory(isolated_mcp_browser, site, monkeypatch):
    browser = isolated_mcp_browser
    daemon_file = browser["daemon_cwd"] / "payload.txt"
    caller_file = browser["caller_cwd"] / "payload.txt"
    daemon_file.write_bytes(b"daemon directory bytes")
    caller_file.write_bytes(b"caller directory bytes")

    handler = site.server.RequestHandlerClass.func
    original_post = handler.do_POST
    site.server.audit_api_bodies = []

    def record_audit_post(request) -> None:
        if urlsplit(request.path).path != "/audit-api-body":
            original_post(request)
            return
        length = int(request.headers.get("Content-Length", "0"))
        body = request.rfile.read(length) if length else b""
        with request.server.save_lock:
            request.server.audit_api_bodies.append(body)
        request.send_response(204)
        request.end_headers()

    monkeypatch.setattr(handler, "do_POST", record_audit_post)
    previous_cwd = Path.cwd()
    try:
        os.chdir(browser["caller_cwd"])
        opened = mcp_server.browser_goto(f"{site.url}/home.html", SESSION, profile=PROFILE)
        assert opened.get("url", "").endswith("/home.html"), opened

        sent = mcp_server.browser_commit(
            "api", SESSION, profile=PROFILE, url=f"{site.url}/audit-api-body", method="POST", body_file="payload.txt",
        )
        assert sent.get("status") == 204, sent
        assert site.server.audit_api_bodies == [b"caller directory bytes"]

        uploaded_page = mcp_server.browser_goto(f"{site.url}/audit_upload.html", SESSION, profile=PROFILE)
        assert uploaded_page.get("url", "").endswith("/audit_upload.html"), uploaded_page
        uploaded = mcp_server.browser_commit(
            "upload", SESSION, profile=PROFILE, target={"label": "Upload payload"}, files=["payload.txt"],
        )
        assert "error" not in uploaded, uploaded
        observed = mcp_server.browser_wait(SESSION, profile=PROFILE, text="caller directory bytes", timeout=5)
        assert "text" in observed.get("satisfied", []), observed
        contents = mcp_server.browser_read("text", SESSION, profile=PROFILE)
        assert "caller directory bytes" in contents.get("text", ""), contents

        caller_file.unlink()
        missing_body = mcp_server.browser_commit(
            "api", SESSION, profile=PROFILE, url=f"{site.url}/audit-api-body", method="POST", body_file="payload.txt",
        )
        assert missing_body.get("error", {}).get("class") == common.ERR_NOT_FOUND, missing_body
        assert site.server.audit_api_bodies == [b"caller directory bytes"]

        missing_upload = mcp_server.browser_commit(
            "upload", SESSION, profile=PROFILE, target={"label": "Upload payload"}, files=["payload.txt"],
        )
        assert missing_upload.get("error", {}).get("class") == common.ERR_NOT_FOUND, missing_upload
    finally:
        os.chdir(previous_cwd)


def test_doctor_live_runs_and_tears_down_internal_probe(isolated_doctor_home):
    home, env = isolated_doctor_home
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL), "doctor", "--live"],
        cwd=home, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    report = json.loads(result.stdout) if result.stdout.strip() else {}
    checks = {check["id"]: check for check in report.get("checks", [])}
    assert result.returncode == 0, result.stderr
    for check_id in ("live_boot", "live_snapshot", "live_click", "live_teardown"):
        assert checks.get(check_id, {}).get("status") == "ok", (check_id, checks.get(check_id), report.get("failed"))
    assert checks["live_teardown"]["detail"] == "daemon and browser exited"
    assert live_support.browser_processes(home / "profiles" / common.INTERNAL_DATA_PROFILE / "user-data") == []
