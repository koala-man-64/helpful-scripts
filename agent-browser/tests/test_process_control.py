"""Ending and listing processes without WMI.

`taskkill /T` and `Get-CimInstance Win32_Process` both go through WMI, and both fail as a whole while WMI is
cancelling calls: the first ends nothing, the second lists nothing. The kernel route (`_end_tree`,
`_process_table`) is what these tests pin down, with what falls back to the old programs and what a caller is
told when nothing could be read.

The first half runs on a scripted stand-in for the kernel calls. The tests marked `processes` use this machine's
real process list and short-lived child interpreters; they start no browser.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from agent_browser_core import common as core_common

WINDOWS = sys.platform == "win32"
OWN_ANCESTORS = core_common._own_ancestors  # the real one: conftest replaces it for offline tests, as it does the process table
SLEEPER = "import time; time.sleep(60)"
# A parent that starts one more sleeper, prints that one's pid, then sleeps itself.
PARENT = ("import subprocess, sys, time; "
          "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', sys.argv[1] + '-child']); "
          "print(child.pid, flush=True); time.sleep(60)")


class FakeProcesses:
    """A scripted kernel: each process has the parent a snapshot shows and the parent its own handle reports."""

    QUERY, TERMINATE = 1, 2

    def __init__(self, processes: dict[int, dict[str, Any]]) -> None:
        self.processes = {pid: {"alive": True, **facts} for pid, facts in processes.items()}
        self.handles: dict[int, int] = {}
        self.ended: list[int] = []
        self.closed: list[int] = []
        self.snapshots = 0
        self.opened: list[int] = []
        self.before_snapshot: Any = None

    def snapshot(self) -> list[tuple[int, int, str]] | None:
        self.snapshots += 1
        if self.before_snapshot is not None and self.before_snapshot(self.snapshots) == "unreadable":
            return None
        return [(pid, facts["parent"], facts.get("name", "x.exe")) for pid, facts in self.processes.items() if facts["alive"]]

    def open(self, pid: int, access: int) -> int | None:
        self.opened.append(pid)
        facts = self.processes.get(pid)
        if facts is None or not facts.get("openable", True):
            return None
        handle = 1000 + len(self.handles)
        self.handles[handle] = pid
        return handle

    def close(self, handle: int) -> None:
        self.closed.append(handle)

    def created(self, handle: int) -> int:
        return self.processes[self.handles[handle]]["created"]

    def parent(self, handle: int) -> int:
        facts = self.processes[self.handles[handle]]
        if facts.get("parent_by_handle") == "raises":
            raise OSError("the kernel call failed")
        return facts.get("parent_by_handle", facts["parent"])

    def end(self, handle: int) -> None:
        pid = self.handles[handle]
        self.ended.append(pid)
        if self.processes[pid].get("dies", True):
            self.processes[pid]["alive"] = False
            for facts in self.processes.values():  # a kill-on-close job: these go the moment `pid` does
                if facts.get("dies_with") == pid:
                    facts["alive"] = False

    def gone(self, handle: int, wait_ms: int = 0) -> bool:
        return not self.processes[self.handles[handle]]["alive"]

    def all_handles_closed(self) -> bool:
        return sorted(self.closed) == sorted(self.handles)


def _tree(**extra: dict[str, Any]) -> dict[int, dict[str, Any]]:
    processes: dict[int, dict[str, Any]] = {
        10: {"parent": 1, "created": 100, "name": "python.exe"},     # the daemon
        11: {"parent": 10, "created": 110, "name": "node.exe"},      # its Playwright driver
        12: {"parent": 11, "created": 120, "name": "msedge.exe"},    # the browser
        13: {"parent": 12, "created": 130, "name": "msedge.exe"},    # a renderer
        20: {"parent": 1, "created": 90, "name": "explorer.exe"},    # nothing to do with the tree
    }
    processes.update({int(pid): facts for pid, facts in extra.items()})
    return processes


# ---------------------------------------------------------------------------
# _end_tree: what belongs to a tree


def test_the_root_goes_first_then_every_descendant_and_nothing_else() -> None:
    api = FakeProcesses(_tree())

    assert core_common._end_tree(10, api) is True

    assert api.ended == [10, 11, 12, 13]  # the root first so it starts nothing more, then parents before children
    assert api.processes[20]["alive"] is True
    assert api.all_handles_closed()


def test_a_member_that_dies_with_the_root_does_not_hide_what_it_started() -> None:
    # A venv's python.exe is a launcher that runs the interpreter in a kill-on-close job: the interpreter is gone from
    # the process list the instant the launcher ends, while what the interpreter started lives on. The tree therefore
    # has to be opened whole before anything is ended.
    processes = _tree()
    processes[11]["dies_with"] = 10
    api = FakeProcesses(processes)

    assert core_common._end_tree(10, api) is True

    assert api.ended == [10, 11, 12, 13]
    assert not any(api.processes[pid]["alive"] for pid in (10, 11, 12, 13))


def test_a_process_older_than_its_recorded_parent_is_not_that_parents_child() -> None:
    # Pid 10 belonged to another program before the daemon got it. That program's child still names 10 as its parent.
    api = FakeProcesses(_tree(**{"30": {"parent": 10, "created": 50, "name": "notepad.exe"}}))

    assert core_common._end_tree(10, api) is True

    assert 30 not in api.ended and api.processes[30]["alive"] is True
    assert api.all_handles_closed()  # the handle opened to look at it is closed again


def test_a_pid_taken_over_since_the_snapshot_is_not_a_child() -> None:
    # The snapshot showed pid 40 as the driver's child. That process has exited and an unrelated one now has the pid:
    # the handle opened for it reports another parent.
    api = FakeProcesses(_tree(**{"40": {"parent": 11, "created": 140, "parent_by_handle": 4, "name": "notepad.exe"}}))

    assert core_common._end_tree(10, api) is True

    assert 40 not in api.ended and api.processes[40]["alive"] is True
    assert api.all_handles_closed()


@pytest.mark.parametrize("unreadable", [10, 11], ids=["the root's start time", "a child's start time"])
def test_nothing_is_taken_for_a_child_when_a_start_time_cannot_be_read(unreadable: int) -> None:
    # Without both times the "not older than its parent" test cannot be made, and a reused pid could pass for a child.
    processes = _tree()
    processes[unreadable]["created"] = 0
    api = FakeProcesses(processes)

    assert core_common._end_tree(10, api) is True

    assert api.ended == [10]  # the root is the caller's choice; what hangs below an unprovable link is left alone
    assert all(api.processes[pid]["alive"] for pid in (11, 12, 13))
    assert api.all_handles_closed()


def test_a_process_that_was_refused_is_not_opened_again_on_a_later_pass() -> None:
    api = FakeProcesses(_tree(**{"30": {"parent": 10, "created": 50, "name": "notepad.exe"}}))

    def late_child(count: int) -> None:
        if count == 2:
            api.processes[14] = {"parent": 11, "created": 135, "name": "msedge.exe", "alive": True}

    api.before_snapshot = late_child

    assert core_common._end_tree(10, api) is True

    assert api.snapshots >= 3 and api.opened.count(30) == 1 and 30 not in api.ended


def test_the_walk_stops_after_four_passes_however_much_the_tree_keeps_starting() -> None:
    api = FakeProcesses(_tree())

    def one_more(count: int) -> None:  # every snapshot shows a process the driver started since the last one
        api.processes[100 + count] = {"parent": 11, "created": 200 + count, "name": "msedge.exe", "alive": True}

    api.before_snapshot = one_more

    assert core_common._end_tree(10, api) is True  # the answer is about the root

    assert api.snapshots == 4 and [pid for pid in (101, 102, 103, 104) if pid in api.ended] == [101, 102, 103, 104]
    assert api.all_handles_closed()


def test_a_handle_is_closed_when_looking_at_a_process_fails() -> None:
    api = FakeProcesses(_tree(**{"15": {"parent": 11, "created": 125, "parent_by_handle": "raises", "name": "msedge.exe"}}))

    with pytest.raises(OSError):
        core_common._end_tree(10, api)

    assert api.all_handles_closed() and api.ended == []  # nothing was ended yet: _kill_tree goes on to taskkill


def test_a_process_started_while_the_tree_was_ending_is_found_on_the_next_pass() -> None:
    api = FakeProcesses(_tree())

    def late_child(count: int) -> None:
        if count == 2:  # the driver started one more process between the first snapshot and its own end
            api.processes[14] = {"parent": 11, "created": 135, "name": "msedge.exe", "alive": True}

    api.before_snapshot = late_child

    assert core_common._end_tree(10, api) is True

    assert 14 in api.ended and api.snapshots >= 3  # one more pass after the one that found something
    assert api.all_handles_closed()


def test_the_calling_process_and_what_it_started_are_never_ended() -> None:
    me = os.getpid()
    api = FakeProcesses(_tree(**{str(me): {"parent": 11, "created": 140, "name": "python.exe"},
                                 "99": {"parent": me, "created": 150, "name": "cmd.exe"}}))

    assert core_common._end_tree(10, api) is True

    assert me not in api.ended and 99 not in api.ended
    assert me not in api.handles.values()  # it is not even opened


def test_a_child_that_cannot_be_opened_is_left_and_the_rest_still_ends() -> None:
    api = FakeProcesses(_tree(**{"15": {"parent": 11, "created": 125, "openable": False, "name": "elevated.exe"}}))

    assert core_common._end_tree(10, api) is True

    assert api.ended == [10, 11, 12, 13] and api.all_handles_closed()


def test_a_root_that_will_not_die_is_reported_as_still_running() -> None:
    processes = _tree()
    processes[10]["dies"] = False
    api = FakeProcesses(processes)

    assert core_common._end_tree(10, api) is False

    assert api.ended[0] == 10 and api.all_handles_closed()


def test_a_root_that_cannot_be_opened_is_neither_true_nor_false() -> None:
    api = FakeProcesses(_tree())

    assert core_common._end_tree(4242, api) is None  # gone already, or not this user's: the caller looks again

    assert api.ended == [] and api.handles == {}


def test_the_root_is_still_ended_when_the_process_list_cannot_be_taken() -> None:
    api = FakeProcesses(_tree())
    api.before_snapshot = lambda count: "unreadable"

    assert core_common._end_tree(10, api) is True

    assert api.ended == [10] and api.all_handles_closed()


# ---------------------------------------------------------------------------
# _kill_tree: the kernel route first, taskkill only behind it


def _refuse_programs(cmd: Any, **kwargs: Any) -> Any:
    raise AssertionError(f"no helper program was needed, yet one was run: {cmd[:1]}")


def test_kill_tree_needs_no_helper_program_when_the_kernel_route_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_common, "_is_windows", lambda: True)
    monkeypatch.setattr(core_common, "_end_tree", lambda pid: True)
    monkeypatch.setattr(core_common.subprocess, "run", _refuse_programs)

    assert core_common._kill_tree(4242) is True


@pytest.mark.parametrize("kernel_route", ["raises", "still running"])
def test_kill_tree_falls_back_to_taskkill(monkeypatch: pytest.MonkeyPatch, fake_clock: Any, kernel_route: str) -> None:
    alive = {"now": True}
    ran: list[list[str]] = []

    def end_tree(pid: int) -> bool:
        if kernel_route == "raises":
            raise OSError("kernel32 could not be loaded")
        return False

    def taskkill(cmd: list[str], **kwargs: Any) -> Any:
        ran.append(cmd)
        alive["now"] = False
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(core_common, "_is_windows", lambda: True)
    monkeypatch.setattr(core_common, "_end_tree", end_tree)
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: alive["now"])
    monkeypatch.setattr(core_common.subprocess, "run", taskkill)

    assert core_common._kill_tree(4242) is True
    assert ran == [["taskkill", "/PID", "4242", "/T", "/F"]]


def test_kill_tree_says_so_when_nothing_ended_the_process(monkeypatch: pytest.MonkeyPatch, fake_clock: Any) -> None:
    # The failure this replaces: taskkill exits 1 with "ERROR: Call cancelled" and the process stays.
    monkeypatch.setattr(core_common, "_is_windows", lambda: True)
    monkeypatch.setattr(core_common, "_end_tree", lambda pid: False)
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(core_common.subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1))

    assert core_common._kill_tree(4242) is False


def test_kill_tree_on_a_process_that_is_already_gone_is_true_without_any_program(monkeypatch: pytest.MonkeyPatch, fake_clock: Any) -> None:
    monkeypatch.setattr(core_common, "_is_windows", lambda: True)
    monkeypatch.setattr(core_common, "_end_tree", lambda pid: None)  # it could not be opened
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(core_common.subprocess, "run", _refuse_programs)

    assert core_common._kill_tree(4242) is True


def _ancestry(**facts: dict[str, Any]) -> FakeProcesses:
    me = os.getpid()
    processes: dict[int, dict[str, Any]] = {
        me: {"parent": 71, "created": 500, "name": "python.exe"},
        71: {"parent": 72, "created": 400, "name": "python.exe"},   # a venv launcher
        72: {"parent": 73, "created": 300, "name": "bash.exe"},
        73: {"parent": 1, "created": 200, "name": "claude.exe"},
        80: {"parent": 1, "created": 100, "name": "explorer.exe"},
    }
    for pid, changed in facts.items():
        processes[int(pid)].update(changed)
    return FakeProcesses(processes)


def test_own_ancestors_follow_the_parent_chain() -> None:
    api = _ancestry()

    assert OWN_ANCESTORS(api) == {71, 72, 73}  # pid 1 is not in the list, so the chain ends there
    assert api.all_handles_closed()


def test_a_recorded_parent_that_started_after_its_child_is_a_reused_pid_and_ends_the_chain() -> None:
    api = _ancestry(**{"72": {"created": 450}})  # 71's real parent is gone; another process now has pid 72

    assert OWN_ANCESTORS(api) == {71}


@pytest.mark.parametrize("failure", ["no snapshot", "own start time unreadable", "the kernel call raises"])
def test_own_ancestors_are_empty_when_they_cannot_be_read(failure: str) -> None:
    api = _ancestry()
    if failure == "no snapshot":
        api.before_snapshot = lambda count: "unreadable"
    elif failure == "own start time unreadable":
        api.processes[os.getpid()]["created"] = 0
    else:
        api.created = lambda handle: (_ for _ in ()).throw(OSError("failed"))  # type: ignore[method-assign]

    assert OWN_ANCESTORS(api) == set()  # a guard that cannot be computed must not block a stop


def test_kill_tree_refuses_a_process_this_one_runs_under(monkeypatch: pytest.MonkeyPatch) -> None:
    # A venv launcher has its interpreter's command line, so a holder search can name the caller's own parent.
    def refuse(pid: int) -> bool:
        raise AssertionError("a process the caller runs under must not be handed to the kernel route")

    monkeypatch.setattr(core_common, "_is_windows", lambda: True)
    monkeypatch.setattr(core_common, "_own_ancestors", lambda api=None: {4242})
    monkeypatch.setattr(core_common, "_end_tree", refuse)
    monkeypatch.setattr(core_common.subprocess, "run", _refuse_programs)

    assert core_common._kill_tree(4242) is False


def test_gone_within_collects_its_own_child_where_a_dead_child_lingers(monkeypatch: pytest.MonkeyPatch, fake_clock: Any) -> None:
    # POSIX: a killed child still answers a signal until its parent collects it, so it would read as still running.
    collected: list[tuple[int, int]] = []
    monkeypatch.setattr(core_common, "_is_windows", lambda: False)
    monkeypatch.setattr(os, "WNOHANG", 1, raising=False)
    monkeypatch.setattr(os, "waitpid", lambda pid, options: collected.append((pid, options)) or (pid, 0))
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: not collected)

    assert core_common._gone_within(4242, 2.0) is True
    assert collected == [(4242, 1)]

    monkeypatch.setattr(os, "waitpid", lambda pid, options: (_ for _ in ()).throw(ChildProcessError()))  # someone else's child
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: True)
    assert core_common._gone_within(4242, 2.0) is False


def test_kill_tree_never_ends_the_process_that_calls_it(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(pid: int) -> bool:
        raise AssertionError("the calling process must not be handed to the kernel route")

    monkeypatch.setattr(core_common, "_end_tree", refuse)
    monkeypatch.setattr(core_common.subprocess, "run", _refuse_programs)

    assert core_common._kill_tree(os.getpid()) is False
    assert core_common._kill_tree(0) is True and core_common._kill_tree(-1) is True  # nothing to end


# ---------------------------------------------------------------------------
# _processes_using: a list that cannot be read is not an empty list

HOME = r"C:\Home\profiles\p\user-data"
EDGE = {"pid": 1, "parent": 9, "name": "msedge.exe", "cmd": rf'"C:\Edge\msedge.exe" --user-data-dir={HOME} about:blank'}
RENDERER = {"pid": 2, "parent": 1, "name": "msedge.exe", "cmd": rf'"C:\Edge\msedge.exe" --type=renderer --user-data-dir={HOME}'}
NOTEPAD = {"pid": 3, "parent": 9, "name": "notepad.exe", "cmd": rf"notepad {HOME}\notes.txt"}
UNREADABLE = {"pid": 4, "parent": 0, "name": "System", "cmd": ""}
OTHER = {"pid": 5, "parent": 9, "name": "node.exe", "cmd": r"node cli.js --user-data-dir=C:\Other\user-data"}
TABLE = [EDGE, RENDERER, NOTEPAD, UNREADABLE, OTHER]


def _cim_answer(stdout: str = "", returncode: int = 0) -> Any:
    return lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")


def test_processes_using_matches_the_command_line_whatever_its_case(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_common, "_is_windows", lambda: True)
    monkeypatch.setattr(core_common, "_process_table", lambda: TABLE)
    monkeypatch.setattr(core_common.subprocess, "run", _refuse_programs)  # the kernel list answered: no PowerShell

    found = core_common._processes_using(HOME.upper())

    assert [row["pid"] for row in found] == [1, 2, 3]
    assert found[0] == {"pid": 1, "name": "msedge.exe", "cmd": EDGE["cmd"]}
    assert core_common._processes_using(r"C:\Nowhere") == []  # readable, and nothing there


def test_processes_using_asks_cim_when_the_kernel_list_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_common, "_is_windows", lambda: True)
    monkeypatch.setattr(core_common, "_process_table", lambda: None)
    one = '{"ProcessId":77,"Name":"msedge.exe","CommandLine":"msedge.exe --user-data-dir=C:\\\\h"}'
    monkeypatch.setattr(core_common.subprocess, "run", _cim_answer(one))
    assert core_common._processes_using(r"C:\h") == [{"pid": 77, "name": "msedge.exe", "cmd": r"msedge.exe --user-data-dir=C:\h"}]

    monkeypatch.setattr(core_common.subprocess, "run", _cim_answer(f"[{one},{one}]"))
    assert [row["pid"] for row in core_common._processes_using(r"C:\h")] == [77, 77]

    monkeypatch.setattr(core_common.subprocess, "run", _cim_answer(""))  # exit 0 and no output: the query worked, nothing matched
    assert core_common._processes_using(r"C:\h") == []


@pytest.mark.parametrize("failure", ["exit code", "cannot run", "timed out", "not json"])
def test_a_list_that_cannot_be_read_at_all_is_none_not_empty(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    # WMI cancelling the call: PowerShell prints an error and no rows. That used to read as "no processes".
    def run(cmd: list[str], **kwargs: Any) -> Any:
        if failure == "cannot run":
            raise FileNotFoundError("powershell")
        if failure == "timed out":
            raise subprocess.TimeoutExpired(cmd, 30)
        if failure == "not json":
            return subprocess.CompletedProcess(cmd, 0, stdout="Get-CimInstance : Call cancelled", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Get-CimInstance : Call cancelled")

    monkeypatch.setattr(core_common, "_is_windows", lambda: True)
    monkeypatch.setattr(core_common, "_process_table", lambda: None)
    monkeypatch.setattr(core_common.subprocess, "run", run)

    assert core_common._processes_using(HOME) is None
    assert core_common._profile_holders(Path(HOME)) is None
    assert core_common._kill_browsers_using(Path(HOME)) == []  # nothing ended, and the caller can ask _profile_holders why


def test_the_cim_query_turns_its_own_errors_into_a_failed_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def run(cmd: list[str], **kwargs: Any) -> Any:
        seen.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(core_common.subprocess, "run", run)

    assert core_common._cim_processes_using("it's here") == []
    assert seen[0].startswith("$ErrorActionPreference = 'Stop';") and "'it''s here'" in seen[0]


def test_profile_holders_are_main_browser_driver_and_daemon_processes_only(monkeypatch: pytest.MonkeyPatch) -> None:
    mine = {"pid": os.getpid(), "name": "python.exe", "cmd": f"python agent_browser.py clean {HOME}"}
    monkeypatch.setattr(core_common, "_processes_using", lambda needle: [EDGE, RENDERER, NOTEPAD, mine])

    assert core_common._profile_holders(Path(HOME)) == [EDGE]  # not Chromium's own child, not another program, not this process


def test_kill_browsers_using_lists_only_what_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    stuck = {"pid": 8, "name": "chrome.exe", "cmd": rf"chrome.exe --user-data-dir={HOME}"}
    monkeypatch.setattr(core_common, "_processes_using", lambda needle: [EDGE, RENDERER, stuck])
    asked: list[int] = []
    monkeypatch.setattr(core_common, "_kill_tree", lambda pid: asked.append(pid) or pid != 8)

    assert core_common._kill_browsers_using(Path(HOME)) == [1]  # pid 8 would not end, so it is not reported as killed
    assert asked == [1, 8]

    asked.clear()
    assert core_common._kill_browsers_using(Path(HOME), dry_run=True) == [1, 8] and asked == []


def test_the_holders_are_read_again_before_each_one_is_ended(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ending one holder can take seconds. By then the second pid read at the start may have gone with the first
    # one's tree, and the number may belong to another program.
    second = {"pid": 8, "name": "chrome.exe", "cmd": rf"chrome.exe --user-data-dir={HOME}"}
    running = [EDGE, second]
    monkeypatch.setattr(core_common, "_processes_using", lambda needle: list(running))
    asked: list[int] = []

    def end(pid: int) -> bool:
        asked.append(pid)
        running.clear()  # the second holder went with the first one's tree
        return True

    monkeypatch.setattr(core_common, "_kill_tree", end)

    assert core_common._kill_browsers_using(Path(HOME)) == [1]
    assert asked == [1]  # pid 8 is never ended on the strength of the first reading


# ---------------------------------------------------------------------------
# The real kernel route, on real child processes


def _alive_after(pids: list[int], seconds: float) -> list[int]:
    """The pids still running once `seconds` have passed (a child ends a moment after its handle is closed)."""
    deadline = time.monotonic() + seconds
    left = [pid for pid in pids if core_common._pid_alive(pid)]
    while left and time.monotonic() < deadline:
        time.sleep(0.05)
        left = [pid for pid in left if core_common._pid_alive(pid)]
    return left


def _listed_after(token: str, seconds: float) -> list[int] | None:
    """The pids whose command line still has the token once `seconds` have passed; None if the list cannot be read."""
    deadline = time.monotonic() + seconds
    while True:
        rows = core_common._processes_using(token)
        if not rows or time.monotonic() >= deadline:
            return None if rows is None else [row["pid"] for row in rows]
        time.sleep(0.05)


@contextlib.contextmanager
def _family(token: str) -> Iterator[tuple[int, int]]:
    """A parent interpreter and the child it started. Under a venv each is a launcher with the interpreter below it."""
    parent = subprocess.Popen([sys.executable, "-c", PARENT, token], stdout=subprocess.PIPE, text=True)
    child = 0
    try:
        assert parent.stdout is not None
        child = int(parent.stdout.readline())
        yield parent.pid, child
    finally:
        _end_everything_named(token)
        parent.wait(timeout=10)
        if parent.stdout is not None:
            parent.stdout.close()


@contextlib.contextmanager
def _sleeper(token: str) -> Iterator[int]:
    process = subprocess.Popen([sys.executable, "-c", SLEEPER, token])
    try:
        yield process.pid
    finally:
        _end_everything_named(token)
        process.wait(timeout=10)


def _end_everything_named(token: str) -> None:
    """Cleanup that does not use the code under test: one TerminateProcess per process whose command line has the token."""
    for row in core_common._processes_using(token) or []:
        with contextlib.suppress(OSError):
            os.kill(row["pid"], signal.SIGTERM)


@pytest.mark.processes
@pytest.mark.skipif(not WINDOWS, reason="the kernel route is Windows only")
def test_the_process_table_shows_a_real_child_with_its_command_line_and_parent() -> None:
    token = f"abk-{uuid.uuid4().hex}"
    with _sleeper(token) as pid:
        table = core_common._process_table()
        assert table is not None
        row = next(row for row in table if row["pid"] == pid)
        assert token in row["cmd"] and row["parent"] == os.getpid() and row["name"].lower().startswith("python")

        found = core_common._processes_using(token.upper())  # whatever its case
        assert found is not None and pid in {row["pid"] for row in found}
        assert all(token in row["cmd"] for row in found)
    assert _listed_after(token, 15.0) == []  # readable, and nothing left once the ended processes are gone


class _FailingList:
    """kernel32 as far as snapshot() uses it, with a list that is empty or that breaks off."""

    def __init__(self, entries: int, last_error: int) -> None:
        self.left, self.last_error, self.closed = entries, last_error, 0

    def CreateToolhelp32Snapshot(self, flags: int, pid: int) -> int:  # noqa: N802 - the Windows name
        return 77

    def _next(self, handle: int, entry: Any) -> int:
        import ctypes

        if self.left:
            self.left -= 1
            entry._obj.th32ProcessID, entry._obj.th32ParentProcessID, entry._obj.szExeFile = 4, 0, "System"
            return 1
        ctypes.set_last_error(self.last_error)
        return 0

    Process32FirstW = Process32NextW = _next

    def CloseHandle(self, handle: int) -> int:  # noqa: N802
        self.closed += 1
        return 1


@pytest.mark.skipif(not WINDOWS, reason="the kernel route is Windows only")
@pytest.mark.parametrize("entries, last_error, readable", [(0, 18, False), (3, 5, False), (3, 18, True)],
                         ids=["an empty list", "a list that breaks off", "a list that ends normally"])
def test_a_process_list_that_is_empty_or_breaks_off_is_unreadable(entries: int, last_error: int, readable: bool) -> None:
    # The caller's own process is in every real list, and a list cut short must not read as "the rest is not running".
    api = core_common._WindowsProcesses()
    api._kernel = _FailingList(entries, last_error)

    found = api.snapshot()

    assert (found is not None) is readable and api._kernel.closed == 1
    if readable:
        assert found == [(4, 0, "System")] * 3


@pytest.mark.skipif(not WINDOWS, reason="the kernel route is Windows only")
def test_the_structures_have_the_sizes_windows_gives_them() -> None:
    import ctypes

    api = core_common._WindowsProcesses()
    sizes = (ctypes.sizeof(api._entry), ctypes.sizeof(api._basic), ctypes.sizeof(api._text))

    assert sizes == ((568, 48, 16) if ctypes.sizeof(ctypes.c_void_p) == 8 else (556, 24, 8))


@pytest.mark.processes
@pytest.mark.skipif(not WINDOWS, reason="the kernel route is Windows only")
def test_a_command_line_longer_than_the_first_buffer_is_read_whole() -> None:
    token = f"abk-{uuid.uuid4().hex}"
    padding = "x" * 5000  # the first read offers 2048 bytes; the kernel then says how many it needs
    process = subprocess.Popen([sys.executable, "-c", SLEEPER, padding, token])
    try:
        table = core_common._process_table()
        assert table is not None
        row = next(row for row in table if row["pid"] == process.pid)
        assert row["cmd"].endswith(token) and padding in row["cmd"]
    finally:
        _end_everything_named(token)
        process.wait(timeout=10)


@pytest.mark.processes
@pytest.mark.skipif(not WINDOWS, reason="the kernel route is Windows only")
def test_the_real_ancestors_of_this_test_include_its_parent_and_not_itself() -> None:
    table = core_common._process_table()
    assert table is not None
    parent = next(row["parent"] for row in table if row["pid"] == os.getpid())

    found = core_common._own_ancestors()

    assert parent in found and os.getpid() not in found


@pytest.mark.processes
@pytest.mark.skipif(not WINDOWS, reason="the kernel route is Windows only")
def test_the_kernel_list_and_the_cim_query_agree_about_a_real_process() -> None:
    token = f"abk-{uuid.uuid4().hex}"
    with _sleeper(token):
        from_cim = core_common._cim_processes_using(token)
        if from_cim is None:
            pytest.skip("WMI did not answer, which is the failure the kernel route exists for")
        from_kernel = core_common._processes_using(token)
        assert from_kernel is not None
        assert {(row["pid"], row["name"].lower()) for row in from_kernel} == {(row["pid"], row["name"].lower()) for row in from_cim}


@pytest.mark.processes
@pytest.mark.skipif(not WINDOWS, reason="the kernel route is Windows only")
def test_kill_tree_ends_a_real_process_and_what_it_started_with_no_helper_program(monkeypatch: pytest.MonkeyPatch) -> None:
    token = f"abk-{uuid.uuid4().hex}"
    with _family(token) as (parent, child), _sleeper(f"{token}-bystander") as bystander:
        everyone = [row["pid"] for row in core_common._processes_using(token) or [] if "bystander" not in row["cmd"]]
        assert parent in everyone and child in everyone
        with monkeypatch.context() as patch:
            patch.setattr(core_common.subprocess, "run", _refuse_programs)  # neither taskkill nor PowerShell
            assert core_common._kill_tree(parent) is True

        assert _alive_after(everyone, 15.0) == []
        assert core_common._pid_alive(bystander)  # a process outside the tree is untouched


@pytest.mark.processes
@pytest.mark.skipif(not WINDOWS, reason="the kernel route is Windows only")
def test_kill_tree_on_a_real_process_that_has_exited_is_true() -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=30)

    assert core_common._kill_tree(process.pid) is True
