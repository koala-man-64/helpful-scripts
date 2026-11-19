"""Live checks of stopping, on a real daemon, driver and browser.

`taskkill /T` and `Get-CimInstance Win32_Process` both go through WMI, which fails in bursts ("Call cancelled") on
a busy machine. A forced stop then ended nothing, found nothing, and still reported `stopped: true`. These tests
take both programs away and expect the stop to work, and compare the kernel's process list with the CIM answer.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_browser_core import common
import live_support


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("AGENT_BROWSER_LIVE") != "1", reason="set AGENT_BROWSER_LIVE=1 to run the live stop checks"),
]

TOOL = Path(__file__).resolve().parents[1] / "agent_browser.py"
PROFILE = "stop_live"
TREE_NAMES = {"python.exe", "pythonw.exe", "node.exe", "msedge.exe", "chrome.exe", "conhost.exe"}


def _env(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_BROWSER_")}
    env.pop("CODEX_THREAD_ID", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update({"AGENT_BROWSER_HOME": str(home), "AGENT_BROWSER_HEADLESS": "1", "PYTHONUTF8": "1"})
    return env


def _cli(home: Path, *args: str, env: dict[str, str] | None = None, timeout: float = 120) -> tuple[int, dict[str, Any], str]:
    proc = subprocess.run([sys.executable, "-X", "utf8", str(TOOL), *args, "--profile", PROFILE], capture_output=True,
                          text=True, encoding="utf-8", env=env or _env(home), timeout=timeout)
    return proc.returncode, (json.loads(proc.stdout) if proc.stdout.strip() else {}), proc.stderr


@pytest.fixture
def stop_home(request: pytest.FixtureRequest):
    home = Path(tempfile.mkdtemp(prefix="abs", dir=os.getenv("TEMP") or None))
    failures = live_support.FailureWatch(request)
    try:
        yield home
    finally:
        subprocess.run([sys.executable, "-X", "utf8", str(TOOL), "stop", "--all", "--force", "--profile", PROFILE],
                       env=_env(home), capture_output=True, timeout=60, check=False)
        blocking = live_support.finish_home(home, failures, request.node.name)
        assert not blocking, f"browser processes survived the stop test teardown: {blocking}"


def _tree(root: int) -> list[dict[str, Any]]:
    """`root` and what hangs below it in the kernel's list, kept to the programs a daemon's tree is made of."""
    table = common._process_table()
    assert table is not None
    members = {root}
    grew = True
    while grew:
        grew = False
        for row in table:
            if row["parent"] in members and row["pid"] not in members and row["name"].lower() in TREE_NAMES:
                members.add(row["pid"])
                grew = True
    return [row for row in table if row["pid"] in members]


def _still_alive(pids: list[int], seconds: float) -> list[int]:
    deadline = time.monotonic() + seconds
    left = [pid for pid in pids if common._pid_alive(pid)]
    while left and time.monotonic() < deadline:
        time.sleep(0.1)
        left = [pid for pid in left if common._pid_alive(pid)]
    return left


def test_a_forced_stop_ends_the_whole_tree_with_no_helper_program_reachable(stop_home: Path, tmp_path: Path) -> None:
    code, started, err = _cli(stop_home, "start", "--headless")
    assert code == 0 and started.get("status") == "started", err
    daemon = int(started["pid"])
    tree = _tree(daemon)
    names = {row["name"].lower() for row in tree}
    assert "node.exe" in names and names & {"msedge.exe", "chrome.exe"}, names  # the driver and the browser hang below the daemon

    bare = tmp_path / "no-programs"
    bare.mkdir()
    env = {**_env(stop_home), "PATH": str(bare)}
    assert shutil.which("taskkill", path=env["PATH"]) is None and shutil.which("powershell", path=env["PATH"]) is None

    code, stopped, err = _cli(stop_home, "stop", "--all", "--force", env=env)

    assert code == 0, err
    assert stopped["stopped"] is True and daemon in stopped["killed_pids"], stopped
    assert "note" not in stopped  # the leftover-browser check ran, without PowerShell
    left = _still_alive([row["pid"] for row in tree], 10.0)
    assert left == [], [(row["pid"], row["name"]) for row in tree if row["pid"] in left]
    assert live_support.settle_browsers(stop_home) == []
    code, status, err = _cli(stop_home, "status")
    assert code == 0 and status["running"] is False, err


def test_the_kernel_list_and_the_cim_query_agree_about_a_running_browser(stop_home: Path) -> None:
    code, started, err = _cli(stop_home, "start", "--headless")
    assert code == 0 and started.get("status") == "started", err

    def mains(rows: list[dict[str, Any]]) -> set[tuple[int, str]]:
        # The browser itself: its helpers (--type=) come and go between the two readings.
        return {(row["pid"], row["name"].lower()) for row in rows
                if row["name"].lower() in ("msedge.exe", "chrome.exe") and "--type=" not in row["cmd"]}

    from_cim = common._cim_processes_using(str(stop_home))
    if from_cim is None:
        pytest.skip("WMI did not answer, which is the failure the kernel route exists for")
    from_kernel = common._processes_using(str(stop_home))

    assert from_kernel is not None and mains(from_kernel) == mains(from_cim) and mains(from_kernel)
