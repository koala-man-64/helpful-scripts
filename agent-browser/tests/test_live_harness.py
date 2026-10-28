"""Offline tests of the live suites' teardown and evidence helpers (tests/live_support.py), on fake process records."""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import live_support

EDGE = r'"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"'
MAIN = {"pid": 10, "name": "msedge.exe", "cmd": f'{EDGE} --user-data-dir="C:\\t\\abx1\\profiles\\p\\user-data" --headless'}
DRIVER = {"pid": 11, "name": "node.exe", "cmd": r'"C:\py\Lib\site-packages\playwright\driver\node.exe" cli.js run-driver'}
PROFILE_IMPORT = {"pid": 12, "name": "msedge.exe", "cmd": f"{EDGE} --type=utility --utility-sub-type=chrome.mojom.ProfileImport --lang=en-US"}
RENDERER = {"pid": 13, "name": "msedge.exe", "cmd": f"{EDGE} --type=renderer --lang=en-US"}


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _settle(alive_until: dict[int, float], procs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[int], Clock]:
    """Run settle_browsers against processes that exit at the given fake times (inf: never, until killed)."""
    clock, killed = Clock(), []
    listing = lambda root: [p for p in procs if p["pid"] not in killed and clock.now < alive_until[p["pid"]]]
    blocking = live_support.settle_browsers(Path("C:/t/abx1"), list_processes=listing, kill=killed.append, clock=clock, sleep=clock.sleep)
    return blocking, killed, clock


def test_only_chromium_children_with_a_type_flag_are_helpers() -> None:
    assert [live_support.is_helper(p) for p in (MAIN, DRIVER, PROFILE_IMPORT, RENDERER)] == [False, False, True, True]
    assert live_support.describe([MAIN, PROFILE_IMPORT]) == [
        "msedge.exe pid 10 (main)", "msedge.exe pid 12 (utility, chrome.mojom.ProfileImport)"]


@pytest.mark.parametrize("survivor", [MAIN, DRIVER], ids=["browser", "node"])
def test_a_browser_or_driver_alive_after_the_grace_still_fails_teardown(survivor: dict[str, Any]) -> None:
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        blocking, killed, clock = _settle({survivor["pid"]: float("inf"), PROFILE_IMPORT["pid"]: float("inf")}, [survivor, PROFILE_IMPORT])
    assert blocking == [survivor]
    assert sorted(killed) == sorted([survivor["pid"], PROFILE_IMPORT["pid"]])
    assert live_support.EXIT_GRACE <= clock.now < live_support.EXIT_GRACE + 1  # no helper grace while the browser is still up
    assert not [w for w in seen if issubclass(w.category, live_support.LiveTeardownWarning)]


def test_a_lingering_helper_gets_a_longer_grace_then_is_killed_and_reported() -> None:
    alive = {MAIN["pid"]: 2.0, DRIVER["pid"]: 3.0, PROFILE_IMPORT["pid"]: float("inf")}
    with pytest.warns(live_support.LiveTeardownWarning, match=r"chrome\.mojom\.ProfileImport"):
        blocking, killed, clock = _settle(alive, [MAIN, DRIVER, PROFILE_IMPORT])
    assert blocking == []
    assert killed == [PROFILE_IMPORT["pid"]]
    assert clock.now >= live_support.EXIT_GRACE + live_support.HELPER_GRACE


def test_a_helper_that_exits_within_its_grace_is_neither_killed_nor_reported() -> None:
    alive = {MAIN["pid"]: 1.0, RENDERER["pid"]: live_support.EXIT_GRACE + 5}
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        blocking, killed, _clock = _settle(alive, [MAIN, RENDERER])
    assert (blocking, killed) == ([], [])
    assert not [w for w in seen if issubclass(w.category, live_support.LiveTeardownWarning)]


def test_wait_for_pid_exit_gives_up_at_its_timeout() -> None:
    clock = Clock()
    assert live_support.wait_for_pid_exit(1, 5.0, alive=lambda pid: clock.now < 3.0, clock=clock, sleep=clock.sleep)
    clock = Clock()
    assert not live_support.wait_for_pid_exit(1, 5.0, alive=lambda pid: True, clock=clock, sleep=clock.sleep)
    assert 5.0 <= clock.now < 5.2


def test_keep_evidence_copies_capped_logs_and_recent_outputs_out_of_the_home(tmp_path: Path) -> None:
    home = tmp_path / "abx1"
    profile = home / "profiles" / "expansion_qa"
    session = profile / "sessions" / ("a" * 32)
    (profile / "user-data").mkdir(parents=True)
    session.mkdir(parents=True)
    (profile / "user-data" / "Cookies").write_bytes(b"never copied")
    (profile / "daemon.log").write_text("\n".join(f"line {n}" for n in range(1000)) + "\n", encoding="utf-8")
    (profile / "startup_error.json").write_text(json.dumps({"class": "launch", "message": "no browser"}), encoding="utf-8")
    big = "x" * (live_support.FILE_CAP * 3) + "TAIL"
    (session / "last_snapshot.txt").write_text(big, encoding="utf-8")
    for n in range(6):
        older = profile / f"last_snapshot_t{n}.txt"
        older.write_text(f"tab {n}", encoding="utf-8")
        os.utime(older, (1_000 + n, 1_000 + n))

    with pytest.warns(live_support.LiveTeardownWarning, match="kept"):
        target = live_support.keep_evidence(home, "test_live_expansion-abx1", artifacts=tmp_path / "artifacts")

    assert target is not None and target.is_relative_to(tmp_path / "artifacts")
    copied = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
    assert "profiles/expansion_qa/daemon.log" in copied and "profiles/expansion_qa/startup_error.json" in copied
    assert not any("user-data" in name for name in copied)
    recent = {name for name in copied if "last_" in name}
    assert len(recent) == live_support.RECENT_FILES
    assert f"profiles/expansion_qa/sessions/{'a' * 32}/last_snapshot.txt" in recent  # the newest file
    log = (target / "profiles/expansion_qa/daemon.log").read_text(encoding="utf-8").splitlines()
    assert len(log) == live_support.LOG_LINES and log[-1] == "line 999"
    snapshot = (target / "profiles/expansion_qa/sessions" / ("a" * 32) / "last_snapshot.txt").read_bytes()
    assert len(snapshot) == live_support.FILE_CAP and snapshot.endswith(b"TAIL")
    assert (profile / "daemon.log").is_file()  # copied, not moved: teardown deletes the home afterwards


def test_keep_evidence_with_nothing_to_keep_returns_none(tmp_path: Path) -> None:
    (tmp_path / "home" / "profiles" / "p" / "user-data").mkdir(parents=True)
    assert live_support.keep_evidence(tmp_path / "home", "label", artifacts=tmp_path / "artifacts") is None
    assert not (tmp_path / "artifacts").exists()


def test_failure_watch_counts_only_failures_after_setup() -> None:
    session = SimpleNamespace(testsfailed=2)
    watch = live_support.FailureWatch(SimpleNamespace(session=session))
    assert not watch.failed
    session.testsfailed = 3
    assert watch.failed
