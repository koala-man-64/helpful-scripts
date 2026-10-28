"""Teardown and evidence helpers shared by the live suites (tests/test_live*.py); tests/test_live_harness.py tests them offline.

Two rules. A live test fails on what the tool does, not on how fast a Windows runner's Edge winds down: the browser's
main process and Playwright's node.exe must exit within EXIT_GRACE, but a Chromium helper child that lingers after
them is killed and reported, not failed. And a failed live run keeps its evidence: the temp home is deleted at
teardown, so its daemon log and last outputs are copied to ARTIFACTS first (CI uploads that folder).
"""

from __future__ import annotations

import os
import re
import tempfile
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agent_browser_core import common as core_common

BROWSER_NAMES = frozenset({"msedge.exe", "chrome.exe", "node.exe"})
EXIT_GRACE = 10.0
# Edge's ProfileImport utility has outlived the main process by more than 10 s on CI runners (PR 6, PR 12, PR 13 runs).
HELPER_GRACE = 30.0
# Read at import: the autouse _isolated_env fixture strips AGENT_BROWSER_* from the environment while a test runs.
ARTIFACTS = Path(os.getenv("AGENT_BROWSER_LIVE_ARTIFACTS") or Path(tempfile.gettempdir()) / "agent-browser-live-artifacts")
RUN = f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}"
LOG_LINES = 300
FILE_CAP = 32 * 1024
RECENT_FILES = 4


class LiveTeardownWarning(UserWarning):
    """Something the live suite tolerated and reports instead of failing: a lingering helper, a slow stop, kept logs."""


def browser_processes(root: Path) -> list[dict[str, Any]]:
    return [proc for proc in core_common._processes_using(str(root)) if proc["name"].lower() in BROWSER_NAMES]


def is_helper(proc: dict[str, Any]) -> bool:
    """A Chromium child (renderer, GPU, a utility such as ProfileImport): its command line carries --type=.
    The browser's main process and Playwright's node.exe driver carry none."""
    return proc["name"].lower() in {"msedge.exe", "chrome.exe"} and "--type=" in proc.get("cmd", "")


def describe(procs: list[dict[str, Any]]) -> list[str]:
    out = []
    for proc in procs:
        kinds = re.findall(r"--(?:type|utility-sub-type)=(\S+)", proc.get("cmd", ""))
        out.append(f"{proc['name']} pid {proc['pid']} ({', '.join(kinds) or 'main'})")
    return out


def settle_browsers(root: Path, *, grace: float = EXIT_GRACE, helper_grace: float = HELPER_GRACE, poll: float = 0.25,
                    list_processes: Callable[[Path], list[dict[str, Any]]] | None = None, kill: Callable[[int], None] | None = None,
                    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> list[dict[str, Any]]:
    """Wait for the browser processes under `root` to exit and kill whatever is left. Returns the ones that must fail
    the caller: a main browser or node.exe still alive after `grace`. Helpers alone get `helper_grace` more."""
    list_processes = list_processes or browser_processes
    kill = kill or core_common._kill_tree
    deadline = clock() + grace
    left = list_processes(root)
    while left and clock() < deadline:
        sleep(poll)
        left = list_processes(root)
    if left and all(is_helper(proc) for proc in left):
        deadline = clock() + helper_grace
        while left and all(is_helper(proc) for proc in left) and clock() < deadline:
            sleep(poll)
            left = list_processes(root)
    for proc in left:
        kill(proc["pid"])
    blocking = [proc for proc in left if not is_helper(proc)]
    if left and not blocking:
        warnings.warn(LiveTeardownWarning(f"killed Chromium helpers still running {grace + helper_grace:g}s after stop: {describe(left)}"),
                      stacklevel=2)
    return blocking


def wait_for_pid_exit(pid: int, timeout: float, *, alive: Callable[[int], bool] | None = None,
                      clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> bool:
    alive = alive or core_common._pid_alive
    deadline = clock() + timeout
    while alive(pid):
        if clock() >= deadline:
            return False
        sleep(0.1)
    return True


def _tail_bytes(path: Path, cap: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - cap))
        return handle.read()


def keep_evidence(home: Path, label: str, *, artifacts: Path | None = None) -> Path | None:
    """Copy each profile's daemon.log tail, startup_error.json and its most recent snapshot/output files out of `home`
    before teardown deletes it. Every file is capped. Returns the folder, or None when there was nothing to keep."""
    target = (artifacts or ARTIFACTS) / RUN / label
    kept = 0
    for profile in sorted(p for p in (home / "profiles").glob("*") if p.is_dir()):
        recent = [*profile.glob("last_snapshot*.txt"), *profile.glob("last_output.json"),
                  *profile.glob("sessions/*/last_snapshot*.txt"), *profile.glob("sessions/*/last_output.json")]
        recent = sorted(recent, key=lambda p: p.stat().st_mtime, reverse=True)[:RECENT_FILES]
        for source in [profile / "daemon.log", profile / "startup_error.json", *recent]:
            if not source.is_file():
                continue
            dest = target / source.relative_to(home)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source.name == "daemon.log":
                dest.write_text(core_common._tail(source, lines=LOG_LINES) + "\n", encoding="utf-8")
            else:
                dest.write_bytes(_tail_bytes(source, FILE_CAP))
            kept += 1
    if not kept:
        return None
    warnings.warn(LiveTeardownWarning(f"kept {kept} log/output files from a failed live run in {target}"), stacklevel=2)
    return target


def finish_home(home: Path, failures: "FailureWatch", label: str, *, processes_under: Path | None = None) -> list[dict[str, Any]]:
    """After the caller's stop: settle the browsers, keep the evidence if anything failed, delete `home`.
    Returns the processes that must fail the caller (see settle_browsers)."""
    blocking = settle_browsers(processes_under or home)
    if failures.failed or blocking:
        keep_evidence(home, label)
    core_common._rmtree_retry(home)
    return blocking


class FailureWatch:
    """Did a test fail while this fixture was set up? Compares the session's failure count at setup and teardown,
    so a module-scoped home also sees the failures of every test that used it."""

    def __init__(self, request: Any) -> None:
        self.session = request.session
        self.before = self.session.testsfailed

    @property
    def failed(self) -> bool:
        return self.session.testsfailed > self.before
