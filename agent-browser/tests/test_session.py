"""Session lifecycle: profile validation, the session store, liveness, and start/stop/status/clean.

Every OS edge (`_spawn_daemon`, `_try_lock`/`_lock_held`, `_ping`, `_pid_alive`, `_kill_tree`,
`_kill_browsers_using`) is monkeypatched; nothing here starts a real daemon, browser, or socket.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import agent_browser as ab
from agent_browser_core import common as core_common, session as core_session

# ---------------------------------------------------------------------------
# Profile names and the 180-char profile-path guard


@pytest.mark.parametrize("name", ["default", "a", "A1_-", "x" * 32, "_doctor", "_live"])
def test_valid_profile_names(name: str) -> None:
    assert core_common._is_profile_name(name) is True
    assert core_common._validate_profile(name) == name


@pytest.mark.parametrize("name", ["", "-leading-dash", "has space", "x" * 33, "has/slash", "..", "_other_reserved"])
def test_invalid_profile_names_raise_validation(name: str) -> None:
    assert core_common._is_profile_name(name) is False
    with pytest.raises(ValueError) as exc:
        core_common._validate_profile(name)
    assert exc.value.error_class == core_common.ERR_VALIDATION


def test_unknown_remote_error_class_maps_to_runtime_error() -> None:
    # ambiguous_profile was never raised and is gone; a daemon that still sends it, like any class outside EXIT_2_CLASSES, exits 1.
    with pytest.raises(RuntimeError) as exc:
        core_session._raise_remote({"class": "ambiguous_profile", "message": "m", "hint": "h"}, "default")
    assert exc.value.error_class == "ambiguous_profile" and not isinstance(exc.value, ValueError)


def test_paths_rejects_a_profile_path_over_the_max_path_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_common, "_home", lambda: Path("C:/" + "a" * 200))
    with pytest.raises(RuntimeError) as exc:
        core_common._paths("default")
    assert exc.value.error_class == core_common.ERR_CONFIG
    assert exc.value.hint == core_common._hint("profile_path")


def test_paths_accepts_a_short_profile_path(tmp_path: Path) -> None:
    paths = core_common._paths("default")
    assert paths.profile == "default"
    assert str(paths.user_data).startswith(str(core_common._home()))


# ---------------------------------------------------------------------------
# Session store: _write_json_atomic / _read_session


def test_write_and_read_session_round_trip(tmp_path: Path) -> None:
    paths = core_common._paths("default")
    core_session._write_json_atomic(paths.session, {"port": 5555, "token": "abc", "pid": 42})
    data = core_session._read_session(paths)
    assert data == {"port": 5555, "token": "abc", "pid": 42}


@pytest.mark.parametrize("payload", [{}, {"port": 5555}, {"token": "abc"}, {"port": 0, "token": "abc"}, {"port": 5555, "token": ""}])
def test_read_session_rejects_files_missing_port_or_token(tmp_path: Path, payload: dict[str, Any]) -> None:
    paths = core_common._paths("default")
    core_session._write_json_atomic(paths.session, payload)
    assert core_session._read_session(paths) is None


def test_read_session_returns_none_when_absent_or_malformed(tmp_path: Path) -> None:
    paths = core_common._paths("default")
    assert core_session._read_session(paths) is None
    paths.session.parent.mkdir(parents=True, exist_ok=True)
    paths.session.write_text("not json", encoding="utf-8")
    assert core_session._read_session(paths) is None


# ---------------------------------------------------------------------------
# _liveness matrix


def _touch_lock(paths: core_common.ProfilePaths) -> None:
    paths.lock.parent.mkdir(parents=True, exist_ok=True)
    paths.lock.touch()


def test_liveness_running_when_lock_held_and_ping_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_read_session", lambda p: {"port": 1, "token": "t", "protocol_version": core_common.PROTOCOL_VERSION})
    monkeypatch.setattr(core_session, "_ping", lambda session, timeout=core_common.PING_TIMEOUT: {"pong": True})
    state, session = core_session._liveness(paths)
    assert state == "running" and session["port"] == 1


def test_liveness_unresponsive_when_lock_held_and_ping_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_read_session", lambda p: {"port": 1, "token": "t", "protocol_version": core_common.PROTOCOL_VERSION})
    monkeypatch.setattr(core_session, "_ping", lambda session, timeout=core_common.PING_TIMEOUT: None)
    state, session = core_session._liveness(paths)
    assert state == "unresponsive" and session["port"] == 1


def test_liveness_starting_when_lock_held_and_no_session_yet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_read_session", lambda p: None)
    state, session = core_session._liveness(paths)
    assert state == "starting" and session is None


def test_liveness_stale_when_lock_free_but_session_file_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")  # lock file does not exist -> held is False without calling _lock_held
    monkeypatch.setattr(core_session, "_read_session", lambda p: {"port": 1, "token": "t"})
    state, session = core_session._liveness(paths)
    assert state == "stale" and session == {"port": 1, "token": "t"}


def test_liveness_none_when_nothing_present(tmp_path: Path) -> None:
    paths = core_common._paths("default")
    state, session = core_session._liveness(paths)
    assert state == "none" and session is None


# ---------------------------------------------------------------------------
# Lock probes and the daemon's lock acquisition


def _probe_sequence(monkeypatch: pytest.MonkeyPatch, answers: list[bool]) -> list[Path]:
    """`_lock_held` answers in order (the last repeats): True where another client's probe held the lock."""
    calls: list[Path] = []

    def probe(path: Path) -> bool:
        calls.append(path)
        return answers[min(len(calls), len(answers)) - 1]

    monkeypatch.setattr(core_common, "_lock_held", probe)
    return calls


@pytest.mark.parametrize("record, expected", [(None, "none"), ({"port": 1, "token": "t", "pid": 4242}, "stale")])
def test_a_colliding_probe_is_not_mistaken_for_a_lock_owner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock,
                                                            record: dict[str, Any] | None, expected: str) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    if record:
        core_session._write_json_atomic(paths.session, record)
    calls = _probe_sequence(monkeypatch, [True, False])
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: False)
    assert core_session._transport_state(paths)[0] == expected
    assert len(calls) == 2


def test_a_live_recorded_owner_is_believed_on_the_first_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    core_session._write_json_atomic(paths.session, {"port": 1, "token": "t", "pid": 4242, "protocol_version": core_common.PROTOCOL_VERSION})
    calls = _probe_sequence(monkeypatch, [True, False])
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: pid == 4242)
    assert core_session._transport_state(paths)[0] == "running"
    assert len(calls) == 1  # a running daemon's hot path stays one non-blocking attempt


def test_running_profiles_skips_a_profile_whose_lock_was_only_another_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    _make_running_profile("alpha")
    _probe_sequence(monkeypatch, [True, False])
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: False)
    assert core_session._running_profiles() == []


def test_acquire_lock_retries_through_a_transient_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    sentinel = object()
    answers = [None, None, sentinel]
    monkeypatch.setattr(core_common, "_try_lock", lambda path: answers.pop(0))
    assert core_common._acquire_lock(tmp_path / "x.lock", wait=1.0) is sentinel

    monkeypatch.setattr(core_common, "_try_lock", lambda path: None)
    began = fake_clock.value
    assert core_common._acquire_lock(tmp_path / "x.lock", wait=1.0) is None
    assert fake_clock.value - began >= 1.0


def test_acquire_lock_waits_for_a_real_short_lived_holder(tmp_path: Path) -> None:
    import threading

    path = tmp_path / "x.lock"
    holder = core_common._try_lock(path)
    assert holder is not None
    threading.Timer(0.1, core_common._release_lock, [holder]).start()
    handle = core_common._acquire_lock(path, wait=2.0)
    try:
        assert handle is not None
        began = time.monotonic()
        assert core_common._acquire_lock(path, wait=0.3) is None  # a lock another daemon keeps still fails
        assert time.monotonic() - began < 1.5
    finally:
        core_common._release_lock(handle)


@pytest.mark.parametrize("until, expected", [
    (60, "busy"),                      # inside the published request's deadline: another command holds the daemon
    (-60, "unresponsive"),             # still in a request past its caller's deadline: hung
    ("not a time", "unresponsive"),
    (None, "unresponsive"),            # a pre-upgrade daemon publishes no deadline
])
def test_liveness_tells_a_daemon_busy_with_another_command_from_a_hung_one(
    monkeypatch: pytest.MonkeyPatch, until: Any, expected: str
) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    busy: dict[str, Any] = {"cmd": "goto", "since": core_common._utc_now()}
    if until is not None:
        busy["until"] = core_common._utc_after(until) if isinstance(until, int) else until
    record = {"port": 1, "token": "t", "protocol_version": core_common.PROTOCOL_VERSION, "busy": busy}
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_read_session", lambda p: record)
    monkeypatch.setattr(core_session, "_ping", lambda session, timeout=core_common.PING_TIMEOUT: None)
    state, session = core_session._liveness(paths)
    assert state == expected and session["port"] == 1


def test_liveness_rereads_the_busy_record_after_a_missed_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    idle = {"port": 1, "token": "t", "protocol_version": core_common.PROTOCOL_VERSION, "busy": None}
    held = {**idle, "busy": {"cmd": "click", "since": core_common._utc_now(), "until": core_common._utc_after(30)}}
    reads = iter([idle, held])  # the command started between the first read and the ping
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_read_session", lambda p: next(reads))
    monkeypatch.setattr(core_session, "_ping", lambda session, timeout=core_common.PING_TIMEOUT: None)
    state, session = core_session._liveness(paths)
    assert state == "busy" and session["busy"]["cmd"] == "click"


def test_liveness_stays_busy_when_the_holding_command_ends_during_the_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    held = {"port": 1, "token": "t", "protocol_version": core_common.PROTOCOL_VERSION,
            "busy": {"cmd": "goto", "since": core_common._utc_now(), "until": core_common._utc_after(30)}}
    reads = iter([held, {**held, "busy": None}])  # the ping queued behind it, then it finished
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_read_session", lambda p: next(reads))
    monkeypatch.setattr(core_session, "_ping", lambda session, timeout=core_common.PING_TIMEOUT: None)
    state, session = core_session._liveness(paths)
    assert state == "busy" and session["busy"]["cmd"] == "goto"


def test_command_connection_does_not_depend_on_a_short_health_probe(monkeypatch):
    paths = core_common._paths("default")
    _touch_lock(paths)
    record = {"port": 1, "token": "t", "protocol_version": core_common.PROTOCOL_VERSION}
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_read_session", lambda p: record)
    def refuse_probe(*args, **kwargs):
        raise AssertionError("An action must use its own request, not a health probe")
    monkeypatch.setattr(core_session, "_ping", refuse_probe)
    assert core_session._require_running(paths, "click") is record


@pytest.mark.parametrize("protocol", [None, 1, core_common.PROTOCOL_VERSION + 1])
def test_incompatible_record_is_rejected_before_action_dispatch(monkeypatch, protocol):
    paths = core_common._paths("default")
    _touch_lock(paths)
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_read_session", lambda p: {"port": 1, "token": "t", "protocol_version": protocol})
    with pytest.raises(RuntimeError) as exc:
        core_session._require_running(paths, "click")
    assert exc.value.error_class == "protocol_mismatch"


def test_reused_start_health_check_uses_the_start_deadline(monkeypatch):
    paths = core_common._paths("default")
    _touch_lock(paths)
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_read_session", lambda p: {"port": 1, "token": "t", "protocol_version": core_common.PROTOCOL_VERSION})
    deadlines = []
    monkeypatch.setattr(core_session, "_ping", lambda record, timeout: deadlines.append(timeout) or {"pong": True})
    assert core_session.start_session(paths, _start_args(), timeout=17)["status"] == "already_running"
    assert deadlines == [17]


# ---------------------------------------------------------------------------
# _running_profiles / _resolve_profile


def _make_running_profile(name: str) -> None:
    paths = core_common._paths(name)
    _touch_lock(paths)
    core_session._write_json_atomic(paths.session, {"port": 1, "token": "t"})


def test_running_profiles_lists_only_profiles_with_a_held_lock_and_a_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _make_running_profile("alpha")
    core_common._paths("beta")  # exists on disk but never started: no lock, no session
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    assert core_session._running_profiles() == ["alpha"]


def test_running_profiles_empty_when_no_profiles_dir(tmp_path: Path) -> None:
    assert core_session._running_profiles() == []


def test_resolve_profile_sticky_rules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(core_session, "_running_profiles", lambda: [])
    assert core_session._resolve_profile(None) == core_common.DEFAULT_PROFILE

    monkeypatch.setattr(core_session, "_running_profiles", lambda: ["work"])
    assert core_session._resolve_profile(None) == core_common.DEFAULT_PROFILE

    monkeypatch.setattr(core_session, "_running_profiles", lambda: ["alpha", "beta"])
    assert core_session._resolve_profile(None) == core_common.DEFAULT_PROFILE

    # explicit --profile wins even with several running
    assert core_session._resolve_profile("chosen") == "chosen"

    # AGENT_BROWSER_PROFILE wins over the sticky running-profile lookup
    monkeypatch.setenv("AGENT_BROWSER_PROFILE", "env-pick")
    assert core_session._resolve_profile(None) == "env-pick"

    # sticky=False skips the running-profile scan entirely
    monkeypatch.delenv("AGENT_BROWSER_PROFILE")
    assert core_session._resolve_profile(None, sticky=False) == core_common.DEFAULT_PROFILE


# ---------------------------------------------------------------------------
# start_session


def _start_args() -> argparse.Namespace:
    return argparse.Namespace(browser=None, exe=None, headless=True, window_size=None)


def _valid_session(paths: core_common.ProfilePaths, **overrides: Any) -> dict[str, Any]:
    data = {"schema": 1, "protocol_version": core_common.PROTOCOL_VERSION, "profile": paths.profile, "pid": 9999, "port": 4444, "token": "tok",
            "browser": "msedge", "browser_version": "119.0", "headless": True, "code_hash": core_common._code_hash()}
    data.update(overrides)
    return data


def test_start_session_returns_already_running_without_spawning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    session = _valid_session(paths)
    core_session._write_json_atomic(paths.session, session)
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_ping", lambda s, timeout=core_common.PING_TIMEOUT: {"pong": True, "current_tab": 1, "url": "about:blank"})

    def refuse_spawn(*a: Any, **k: Any) -> Any:
        raise AssertionError("_spawn_daemon must not be called when already running")

    monkeypatch.setattr(core_common, "_spawn_daemon", refuse_spawn)
    result = core_session.start_session(paths, _start_args())
    assert result["status"] == "already_running"
    assert result["pid"] == 9999
    assert result["url"] == "about:blank" and result["untrusted"] == core_common.UNTRUSTED_KEYS


def _install_success_recorder(monkeypatch: pytest.MonkeyPatch, paths: core_common.ProfilePaths) -> None:
    def fake_spawn(argv: list[str], log_path: Path, cwd: Path) -> tuple[int, str]:
        assert argv[0] == core_common.sys.executable and argv[1:3] == ["-X", "utf8"]
        token = json.loads(paths.launch.read_text(encoding="utf-8"))["token"]  # a real daemon serves the token it was launched with
        session = _valid_session(paths, pid=1234, port=5555, token=token)
        core_session._write_json_atomic(paths.session, session)
        return 1234, "popen"

    monkeypatch.setattr(core_common, "_spawn_daemon", fake_spawn)
    monkeypatch.setattr(core_session, "_ping", lambda s, timeout=core_common.PING_TIMEOUT: {"pong": True})
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: True)


def test_start_session_spawns_and_writes_launch_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = core_common._paths("default")
    _install_success_recorder(monkeypatch, paths)

    result = core_session.start_session(paths, _start_args())

    assert result["status"] == "started"
    assert result["pid"] == 1234 and result["port"] == 5555
    launch = json.loads(paths.launch.read_text(encoding="utf-8"))
    assert launch["token"] and launch["browser"] == "msedge" and launch["headless"] is True
    assert launch["config"] == {} and "code_hash" in launch
    argv = [core_common.sys.executable, "-X", "utf8", str(Path(ab.__file__).resolve()), "serve", "--launch-file", str(paths.launch)]
    assert argv[0] == core_common.sys.executable


def test_launch_record_carries_an_absolute_home_for_the_daemon(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The daemon runs with cwd = the profile folder and rebuilds every path from launch["home"].
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_BROWSER_HOME", "rel-home")
    assert core_common._home() == tmp_path / "rel-home"
    for paths in (core_common._paths("default"), core_common.ProfilePaths("default", Path("rel-home"))):
        home = Path(core_session._launch_record(paths, _start_args(), "tok")["home"])
        assert home.is_absolute() and home == tmp_path / "rel-home"


def test_start_session_refuses_overlong_profile_path_before_spawning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common.ProfilePaths("default", tmp_path / ("a" * 120))  # built directly, bypassing _paths()

    def refuse_spawn(*a: Any, **k: Any) -> Any:
        raise AssertionError("_spawn_daemon must not be called for an overlong profile path")

    monkeypatch.setattr(core_common, "_spawn_daemon", refuse_spawn)
    with pytest.raises(RuntimeError) as exc:
        core_session.start_session(paths, _start_args())
    assert exc.value.error_class == core_common.ERR_CONFIG
    assert exc.value.hint == core_common._hint("profile_path")
    assert not paths.launch.exists()


def test_start_session_on_a_stale_session_removes_it_and_kills_leftovers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = core_common._paths("default")
    core_session._write_json_atomic(paths.session, _valid_session(paths))  # stale: no lock held
    killed = []
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: (killed.append(user_data) or [777]))
    _install_success_recorder(monkeypatch, paths)

    result = core_session.start_session(paths, _start_args())

    assert result["status"] == "started"
    assert killed == [paths.user_data]


@pytest.mark.parametrize(
    "log_text, error_class",
    [
        ("Playwright is not installed for this interpreter", core_common.ERR_PLAYWRIGHT_MISSING),
        ("launch_persistent_context: already in use", core_common.ERR_PROFILE_IN_USE),
    ],
)
def test_start_session_daemon_exit_early_maps_log_text_to_error_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock, log_text: str, error_class: str
) -> None:
    paths = core_common._paths("default")
    paths.dir.mkdir(parents=True, exist_ok=True)
    paths.log.write_text(log_text, encoding="utf-8")
    monkeypatch.setattr(core_common, "_spawn_daemon", lambda argv, log_path, cwd: (4321, "popen"))
    monkeypatch.setattr(core_session, "_ping", lambda s, timeout=core_common.PING_TIMEOUT: None)
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(core_common, "policy_blockers", lambda policies: [])

    with pytest.raises(RuntimeError) as exc:
        core_session.start_session(paths, _start_args())
    assert exc.value.error_class == error_class
    assert log_text in exc.value.details["log"]


def test_start_session_daemon_exit_early_with_policy_blocker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = core_common._paths("default")
    paths.dir.mkdir(parents=True, exist_ok=True)
    paths.log.write_text("browser exited unexpectedly", encoding="utf-8")
    monkeypatch.setattr(core_common, "_spawn_daemon", lambda argv, log_path, cwd: (4321, "popen"))
    monkeypatch.setattr(core_session, "_ping", lambda s, timeout=core_common.PING_TIMEOUT: None)
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(core_common, "read_policies", lambda kind: {"RemoteDebuggingAllowed": {"status": "fail", "value": 0, "label": "blocked"}})

    with pytest.raises(RuntimeError) as exc:
        core_session.start_session(paths, _start_args())
    assert exc.value.error_class == core_common.ERR_POLICY


def test_start_session_daemon_exit_early_generic_launch_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = core_common._paths("default")
    paths.dir.mkdir(parents=True, exist_ok=True)
    paths.log.write_text("segfault, no idea why", encoding="utf-8")
    monkeypatch.setattr(core_common, "_spawn_daemon", lambda argv, log_path, cwd: (4321, "popen"))
    monkeypatch.setattr(core_session, "_ping", lambda s, timeout=core_common.PING_TIMEOUT: None)
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(core_common, "policy_blockers", lambda policies: [])

    with pytest.raises(RuntimeError) as exc:
        core_session.start_session(paths, _start_args())
    assert exc.value.error_class == core_common.ERR_LAUNCH


@pytest.mark.parametrize("config, word", [
    ({"profiles": {"default": {"permission": "bogus"}}}, "permission"),
    ({"profiles": {"default": "act"}}, "profiles"),
    ({"evidence_max_bytes": 1}, "evidence_max_bytes"),
])
def test_start_session_rejects_a_malformed_profile_policy_before_spawning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                                         config: dict[str, Any], word: str) -> None:
    paths = core_common._paths("default")
    core_common._home().mkdir(parents=True, exist_ok=True)
    (core_common._home() / "config.json").write_text(json.dumps(config), encoding="utf-8")

    def refuse_spawn(*a: Any, **k: Any) -> Any:
        raise AssertionError("_spawn_daemon must not be called for a malformed profile policy")

    monkeypatch.setattr(core_common, "_spawn_daemon", refuse_spawn)
    with pytest.raises(RuntimeError) as exc:
        core_session.start_session(paths, _start_args())
    assert exc.value.error_class == core_common.ERR_CONFIG and word in str(exc.value)
    assert not paths.launch.exists()


@pytest.mark.parametrize("alive", [False, True], ids=["daemon exited", "start timed out"])
def test_start_session_removes_its_launch_record_when_no_daemon_comes_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock, alive: bool) -> None:
    paths = core_common._paths("default")
    monkeypatch.setattr(core_common, "_spawn_daemon", lambda argv, log_path, cwd: (4321, "popen"))
    monkeypatch.setattr(core_session, "_ping", lambda s, timeout=core_common.PING_TIMEOUT: None)
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: alive)
    monkeypatch.setattr(core_common, "_kill_tree", lambda pid: None)
    monkeypatch.setattr(core_common, "policy_blockers", lambda policies: [])

    with pytest.raises(RuntimeError) as exc:
        core_session.start_session(paths, _start_args(), timeout=2.0)
    assert exc.value.error_class == core_common.ERR_LAUNCH
    assert not paths.launch.exists()  # the token it carries outlived no failed start


def test_discard_launch_deletes_only_the_record_it_wrote(tmp_path: Path) -> None:
    paths = core_common._paths("default")
    core_session._write_json_atomic(paths.launch, {"token": "other-start"})
    core_session._discard_launch(paths.launch, "this-start")
    assert paths.launch.exists()  # a concurrent start's daemon still has to read it
    core_session._discard_launch(paths.launch, "other-start")
    assert not paths.launch.exists()
    core_session._discard_launch(paths.launch, "other-start")  # already gone: no error
    paths.launch.write_text("not json", encoding="utf-8")
    core_session._discard_launch(paths.launch, "other-start")
    assert paths.launch.exists()


def _install_lost_race(monkeypatch: pytest.MonkeyPatch, paths: core_common.ProfilePaths, winner_after: int | None) -> list[int]:
    """This start's daemon exits at once while another start's daemon holds the lock (and, after `winner_after` probes, serves)."""
    killed: list[int] = []
    probes = {"n": 0}

    def held_by_winner(path: Path) -> bool:
        probes["n"] += 1
        if winner_after is not None and probes["n"] == winner_after:
            core_session._write_json_atomic(paths.session, _valid_session(paths, pid=7777, port=6666, launcher="wmi"))
        return True

    monkeypatch.setattr(core_common, "_spawn_daemon", lambda argv, log_path, cwd: (4321, "popen"))
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(core_common, "_lock_held", held_by_winner)
    monkeypatch.setattr(core_common, "_kill_tree", killed.append)
    monkeypatch.setattr(core_common, "policy_blockers", lambda policies: [])
    monkeypatch.setattr(core_session, "_ping", lambda s, timeout=core_common.PING_TIMEOUT: {"pong": True})
    return killed


def test_start_session_waits_for_a_concurrent_starts_daemon_instead_of_failing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = core_common._paths("default")
    killed = _install_lost_race(monkeypatch, paths, winner_after=3)

    result = core_session.start_session(paths, _start_args())

    assert result["status"] == "already_running" and result["pid"] == 7777 and result["port"] == 6666
    assert result["launcher"] == "wmi" and core_session._read_session(paths)["launcher"] == "wmi"  # the winner's, not this start's
    assert killed == []
    assert not paths.launch.exists()  # this start's record, which no daemon will consume


def test_start_session_timeout_after_a_lost_race_does_not_kill_the_exited_pid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = core_common._paths("default")
    killed = _install_lost_race(monkeypatch, paths, winner_after=None)
    seen = {"n": 0}

    def recycled(pid: int) -> bool:  # exited, then the OS hands the pid to an unrelated process
        seen["n"] += 1
        return seen["n"] > 1

    monkeypatch.setattr(core_common, "_pid_alive", recycled)

    with pytest.raises(RuntimeError) as exc:
        core_session.start_session(paths, _start_args(), timeout=2.0)

    assert exc.value.error_class == core_common.ERR_LAUNCH and "did not become ready" in str(exc.value)
    assert killed == []


def test_start_session_reports_another_starts_daemon_while_its_own_still_waits_for_the_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock
) -> None:
    paths = core_common._paths("default")
    killed = _install_lost_race(monkeypatch, paths, winner_after=None)
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: True)  # this start's daemon is still retrying the lock

    def winner_serves(argv: list[str], log_path: Path, cwd: Path) -> tuple[int, str]:
        core_session._write_json_atomic(paths.session, _valid_session(paths, pid=7777, port=6666, launcher="wmi"))
        return 4321, "popen"

    monkeypatch.setattr(core_common, "_spawn_daemon", winner_serves)

    result = core_session.start_session(paths, _start_args())

    assert result["status"] == "already_running" and result["pid"] == 7777 and result["launcher"] == "wmi"
    assert core_session._read_session(paths)["launcher"] == "wmi"  # the winner's session.json is not this start's to edit
    assert killed == [] and not paths.launch.exists()


def test_start_session_checks_the_profile_policy_before_cleaning_a_stale_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")
    core_session._write_json_atomic(paths.session, _valid_session(paths))  # stale: no lock held
    (core_common._home() / "config.json").write_text(json.dumps({"permission": "bogus"}), encoding="utf-8")
    killed: list[Path] = []
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: killed.append(user_data) or [])

    with pytest.raises(RuntimeError) as exc:
        core_session.start_session(paths, _start_args())

    assert exc.value.error_class == core_common.ERR_CONFIG
    assert killed == [] and paths.session.exists()


# ---------------------------------------------------------------------------
# stop_session


def test_stop_session_not_running_returns_false_without_side_effects(tmp_path: Path) -> None:
    paths = core_common._paths("default")
    result = core_session.stop_session(paths)
    assert result == {"profile": "default", "stopped": False, "killed_pids": []}


def test_stop_session_requests_stop_then_waits_and_skips_kill_tree_if_pid_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock
) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    session = _valid_session(paths, pid=555)
    core_session._write_json_atomic(paths.session, session)
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_request", lambda s, cmd, args, timeout, **kw: {"stopping": True})
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: False)  # exited during the grace wait
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: [])
    kill_tree_calls = []
    monkeypatch.setattr(core_common, "_kill_tree", lambda pid: kill_tree_calls.append(pid))

    result = core_session.stop_session(paths)

    assert result["stopped"] is True
    assert kill_tree_calls == []  # pid was already gone: no forced kill needed


def test_stop_session_kill_tree_only_if_pid_still_alive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    core_session._write_json_atomic(paths.session, _valid_session(paths, pid=555))
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_request", lambda s, cmd, args, timeout, **kw: {"stopping": True})
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: True)  # never exits
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: [])
    kill_tree_calls = []
    monkeypatch.setattr(core_common, "_kill_tree", lambda pid: kill_tree_calls.append(pid))

    result = core_session.stop_session(paths)

    assert result["stopped"] is True
    assert kill_tree_calls == [555]
    assert 555 in result["killed_pids"]


def test_stop_all_on_a_busy_daemon_asks_it_to_stop_before_killing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    session = _valid_session(paths, pid=555)
    core_session._write_json_atomic(paths.session, session)
    monkeypatch.setattr(core_session, "_liveness", lambda p: ("busy", session))
    requests: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(core_session, "_request", lambda s, cmd, args, timeout, **kw: requests.append((cmd, args)) or {"stopping": True})
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: [])
    kill_tree_calls: list[int] = []
    monkeypatch.setattr(core_common, "_kill_tree", kill_tree_calls.append)

    result = core_session.stop_session(paths)

    assert requests == [("stop", {"all": True})]
    assert kill_tree_calls == [] and result["stopped"] is True


def test_stop_session_also_kills_browsers_using_the_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    core_session._write_json_atomic(paths.session, _valid_session(paths, pid=555))
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_request", lambda s, cmd, args, timeout, **kw: {})
    monkeypatch.setattr(core_common, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(core_common, "_kill_tree", lambda pid: None)
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: [111, 222])

    result = core_session.stop_session(paths)
    assert result["killed_pids"] == [111, 222]


# ---------------------------------------------------------------------------
# status_session


@pytest.mark.parametrize(
    "state, expected_reason",
    [("none", "no_session"), ("stale", "pid_dead"), ("unresponsive", "unresponsive"), ("starting", "starting")],
)
def test_status_session_not_running_reasons(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: str, expected_reason: str) -> None:
    paths = core_common._paths("default")
    monkeypatch.setattr(core_session, "_liveness", lambda p: (state, None))
    result = core_session.status_session(paths)
    assert result["running"] is False
    assert result["reason"] == expected_reason


def test_status_session_reports_busy_as_running_without_force_stop_advice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")
    busy = {"cmd": "goto", "since": core_common._utc_now(), "until": core_common._utc_after(60)}
    session = _valid_session(paths, started=core_common._utc_now(), busy=busy, code_hash="old-hash")
    monkeypatch.setattr(core_session, "_liveness", lambda p: ("busy", session))

    def no_second_probe(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("a busy daemon cannot answer a second probe either")

    monkeypatch.setattr(core_session, "_ping", no_second_probe)
    result = core_session.status_session(paths)
    assert result["running"] is True and result["busy"] == busy
    assert result["note"] == core_common._hint("busy_status", "default")  # outranks the stale-code restart note
    assert "--force" not in json.dumps(result)
    assert not {"current_tab", "url", "title", "tabs"} & set(result)


def test_status_session_running_reports_stale_code_when_hash_differs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")
    session = _valid_session(paths, code_hash="old-hash", started=core_common._utc_now())
    monkeypatch.setattr(core_session, "_liveness", lambda p: ("running", session))
    monkeypatch.setattr(core_session, "_ping", lambda s, timeout=1.0: {"current_tab": 1, "url": "http://x", "title": "X", "tabs": []})
    monkeypatch.setattr(core_common, "_code_hash", lambda: "new-hash")

    result = core_session.status_session(paths)
    assert result["running"] is True
    assert result["stale_code"] is True
    assert result["note"] == core_common._hint("stale_code", "default")


def test_status_session_notes_a_session_open_more_than_8_hours(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")
    started = (datetime.now(timezone.utc) - timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%SZ")
    session = _valid_session(paths, started=started)
    monkeypatch.setattr(core_session, "_liveness", lambda p: ("running", session))
    monkeypatch.setattr(core_session, "_ping", lambda s, timeout=1.0: {})

    result = core_session.status_session(paths)
    assert result["uptime_s"] > 8 * 3600
    assert "hours" in result["note"]


# ---------------------------------------------------------------------------
# clean


def test_clean_removes_stale_sessions_and_prunes_old_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")
    core_session._write_json_atomic(paths.session, _valid_session(paths))  # stale: no lock held
    paths.shots.mkdir(parents=True, exist_ok=True)
    old_file = paths.shots / "old.png"
    old_file.write_bytes(b"x")
    os.utime(old_file, (time.time() - 8 * 86400, time.time() - 8 * 86400))
    new_file = paths.shots / "new.png"
    new_file.write_bytes(b"x")
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: [])

    result = core_session.clean()

    assert result["stale_sessions_removed"] == ["default"]
    assert not paths.session.exists()
    assert result["artifacts_removed"] == 0
    assert old_file.exists() and new_file.exists()


def test_clean_dry_run_touches_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")
    core_session._write_json_atomic(paths.session, _valid_session(paths))
    paths.shots.mkdir(parents=True, exist_ok=True)
    old_file = paths.shots / "old.png"
    old_file.write_bytes(b"x")
    os.utime(old_file, (time.time() - 8 * 86400, time.time() - 8 * 86400))
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: [])

    result = core_session.clean(dry_run=True)

    assert result["stale_sessions_removed"] == ["default"]  # reported...
    assert paths.session.exists()  # ...but not actually removed
    assert result["artifacts_removed"] == 0
    assert old_file.exists()


def test_clean_purge_refused_while_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")
    _touch_lock(paths)
    core_session._write_json_atomic(paths.session, _valid_session(paths))
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_ping", lambda s, timeout=core_common.PING_TIMEOUT: {"pong": True})
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: [])

    with pytest.raises(ValueError) as exc:
        core_session.clean(purge="default")
    assert exc.value.error_class == core_common.ERR_VALIDATION
    assert exc.value.hint == core_common._hint("purge_running", "default")


def test_clean_purge_refused_while_busy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    core_common._paths("default").dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core_session, "_liveness", lambda p: ("busy", {}))
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: [])

    with pytest.raises(ValueError) as exc:
        core_session.clean(purge="default")
    assert exc.value.hint == core_common._hint("purge_running", "default")


def test_clean_purge_removes_a_stopped_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")
    paths.dir.mkdir(parents=True, exist_ok=True)
    (paths.dir / "marker.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: [])

    result = core_session.clean(purge="default")
    assert result["profiles_purged"] == ["default"]
    assert not paths.dir.exists()


def test_clean_purge_failure_is_reported_once_and_later_profiles_are_still_cleaned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    held = core_common._paths("aaa")
    held.dir.mkdir(parents=True, exist_ok=True)
    (held.dir / "marker.txt").write_text("x", encoding="utf-8")
    later = core_common._paths("zzz")  # sorts after the purge target
    core_session._write_json_atomic(later.session, _valid_session(later))  # stale: no lock held
    later.downloads.mkdir(parents=True, exist_ok=True)
    old_file = later.downloads / "old.bin"
    old_file.write_bytes(b"x")
    os.utime(old_file, (time.time() - 8 * 86400, time.time() - 8 * 86400))
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: [])
    removals: list[Path] = []
    monkeypatch.setattr(core_common, "_rmtree_retry", lambda path, attempts=6: removals.append(path) or False)

    with pytest.raises(RuntimeError) as exc:
        core_session.clean(purge="aaa")

    assert not isinstance(exc.value, ValueError)  # exit 1, not a usage error
    assert exc.value.error_class == core_common.ERR_PROFILE_IN_USE
    assert exc.value.hint == core_common._hint("purge_failed", "aaa")
    assert exc.value.details["profiles_purged"] == []
    assert exc.value.details["profiles_purge_failed"] == ["aaa"]
    assert exc.value.details["stale_sessions_removed"] == ["zzz"]
    assert exc.value.details["artifacts_removed"] == 1
    assert removals == [held.dir]
    assert held.dir.exists()
    assert not later.session.exists() and not old_file.exists()  # the later profile was still cleaned


def test_clean_purge_dry_run_reports_the_profile_without_removing_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = core_common._paths("default")
    paths.dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: [])
    monkeypatch.setattr(core_common, "_rmtree_retry", lambda path, attempts=6: pytest.fail("a dry run removed files"))

    result = core_session.clean(purge="default", dry_run=True)
    assert result["profiles_purged"] == ["default"]
    assert paths.dir.exists()


def test_clean_unknown_purge_is_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    core_common._paths("other").dir.mkdir(parents=True, exist_ok=True)  # profiles dir must exist to reach the purge check
    monkeypatch.setattr(core_common, "_kill_browsers_using", lambda user_data, dry_run=False: [])
    with pytest.raises(ValueError) as exc:
        core_session.clean(purge="no-such-profile")
    assert exc.value.error_class == core_common.ERR_NOT_FOUND


# ---------------------------------------------------------------------------
# _spawn_daemon: Windows popen breakaway retry, and the wmi launcher


def test_spawn_daemon_retries_without_breakaway_on_winerror_5(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(core_common, "_is_windows", lambda: True)
    calls: list[int] = []

    def fake_popen(argv: list[str], cwd: str | None = None, stdin: Any = None, stdout: Any = None,
                   stderr: Any = None, close_fds: bool | None = None, creationflags: int = 0, **kw: Any) -> Any:
        calls.append(creationflags)
        if creationflags & core_common.subprocess.CREATE_BREAKAWAY_FROM_JOB:
            err = OSError("Access is denied")
            err.winerror = 5
            raise err
        return type("Proc", (), {"pid": 4242})()

    monkeypatch.setattr(core_common.subprocess, "Popen", fake_popen)
    pid, launcher = core_common._spawn_daemon(["prog", "arg"], tmp_path / "log.txt", tmp_path)

    assert pid == 4242 and launcher == "popen"
    assert len(calls) == 2
    assert calls[0] & core_common.subprocess.CREATE_BREAKAWAY_FROM_JOB
    assert not (calls[1] & core_common.subprocess.CREATE_BREAKAWAY_FROM_JOB)


def test_spawn_daemon_wmi_launcher_parses_the_pid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(core_common, "_is_windows", lambda: True)
    monkeypatch.setenv("AGENT_BROWSER_LAUNCHER", "wmi")
    seen_cmd: dict[str, Any] = {}

    def fake_run(cmd: list[str], capture_output: bool = True, text: bool = True, timeout: float = 60, check: bool = False) -> Any:
        seen_cmd["cmd"] = cmd
        return type("Result", (), {"stdout": "9876\n", "stderr": ""})()

    monkeypatch.setattr(core_common.subprocess, "run", fake_run)
    pid, launcher = core_common._spawn_daemon(["prog", "arg"], tmp_path / "log.txt", tmp_path)

    assert pid == 9876 and launcher == "wmi"
    assert seen_cmd["cmd"][0] == "powershell"
    assert "Invoke-CimMethod" in seen_cmd["cmd"][-1]


# ---------------------------------------------------------------------------
# Client socket timeout wording (remediation PR 5)


class _SilentSocket:
    """A connected socket whose daemon never answers."""

    def __enter__(self) -> "_SilentSocket":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def sendall(self, data: bytes) -> None:
        self.sent = data

    def makefile(self, mode: str) -> Any:
        sock = self

        class Reader:
            def __enter__(self) -> "Reader":
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

            def readline(self) -> bytes:
                raise core_session.socket.timeout(f"timed out after {sock.timeout}")

        return Reader()


def test_client_socket_timeout_hint_points_at_receipts(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(core_session.socket, "create_connection", lambda address, timeout=None: _SilentSocket())
    session = {"port": 1, "token": "t", "profile": "work"}
    with pytest.raises(RuntimeError) as exc:
        core_session._request(session, "click", {"target": "e1"}, timeout=1.0)
    assert exc.value.error_class == core_common.ERR_TIMEOUT
    assert "may have run" in str(exc.value)
    assert exc.value.hint == core_common._hint("timeout_client_action", "work") and "state --profile work" in exc.value.hint

    # A ping is a health probe, not a command: nothing ran, so it keeps the plain wording.
    with pytest.raises(RuntimeError) as exc:
        core_session._request(session, "ping", {}, timeout=0.0, grace=0.0, queue_wait=1.0)
    assert exc.value.error_class == core_common.ERR_TIMEOUT
    assert "may have run" not in str(exc.value) and exc.value.hint == core_common._hint("timeout_client", "work")

    from agent_browser_core import cli as core_cli
    monkeypatch.setattr(core_cli, "_session_for", lambda args, verb, autostart=False: (core_common._paths("work"), session))
    assert core_cli.main(["click", "e1", "--profile", "work"]) == core_common.EXIT_TIMEOUT
    error = json.loads(capsys.readouterr().err)["error"]
    assert error["class"] == core_common.ERR_TIMEOUT and error["hint"] == core_common._hint("timeout_client_action", "work")
