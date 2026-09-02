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

# ---------------------------------------------------------------------------
# Profile names and the 180-char profile-path guard


@pytest.mark.parametrize("name", ["default", "a", "A1_-", "x" * 32, "_doctor", "_live"])
def test_valid_profile_names(name: str) -> None:
    assert ab._is_profile_name(name) is True
    assert ab._validate_profile(name) == name


@pytest.mark.parametrize("name", ["", "-leading-dash", "has space", "x" * 33, "has/slash", "..", "_other_reserved"])
def test_invalid_profile_names_raise_validation(name: str) -> None:
    assert ab._is_profile_name(name) is False
    with pytest.raises(ValueError) as exc:
        ab._validate_profile(name)
    assert exc.value.error_class == ab.ERR_VALIDATION


def test_paths_rejects_a_profile_path_over_the_max_path_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ab, "_home", lambda: Path("C:/" + "a" * 200))
    with pytest.raises(RuntimeError) as exc:
        ab._paths("default")
    assert exc.value.error_class == ab.ERR_CONFIG
    assert exc.value.hint == ab._hint("profile_path")


def test_paths_accepts_a_short_profile_path(tmp_path: Path) -> None:
    paths = ab._paths("default")
    assert paths.profile == "default"
    assert str(paths.user_data).startswith(str(ab._home()))


# ---------------------------------------------------------------------------
# Session store: _write_json_atomic / _read_session


def test_write_and_read_session_round_trip(tmp_path: Path) -> None:
    paths = ab._paths("default")
    ab._write_json_atomic(paths.session, {"port": 5555, "token": "abc", "pid": 42})
    data = ab._read_session(paths)
    assert data == {"port": 5555, "token": "abc", "pid": 42}


@pytest.mark.parametrize("payload", [{}, {"port": 5555}, {"token": "abc"}, {"port": 0, "token": "abc"}, {"port": 5555, "token": ""}])
def test_read_session_rejects_files_missing_port_or_token(tmp_path: Path, payload: dict[str, Any]) -> None:
    paths = ab._paths("default")
    ab._write_json_atomic(paths.session, payload)
    assert ab._read_session(paths) is None


def test_read_session_returns_none_when_absent_or_malformed(tmp_path: Path) -> None:
    paths = ab._paths("default")
    assert ab._read_session(paths) is None
    paths.session.parent.mkdir(parents=True, exist_ok=True)
    paths.session.write_text("not json", encoding="utf-8")
    assert ab._read_session(paths) is None


# ---------------------------------------------------------------------------
# _liveness matrix


def _touch_lock(paths: ab.ProfilePaths) -> None:
    paths.lock.parent.mkdir(parents=True, exist_ok=True)
    paths.lock.touch()


def test_liveness_running_when_lock_held_and_ping_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = ab._paths("default")
    _touch_lock(paths)
    monkeypatch.setattr(ab, "_lock_held", lambda p: True)
    monkeypatch.setattr(ab, "_read_session", lambda p: {"port": 1, "token": "t"})
    monkeypatch.setattr(ab, "_ping", lambda session, timeout=ab.PING_TIMEOUT: {"pong": True})
    state, session = ab._liveness(paths)
    assert state == "running" and session == {"port": 1, "token": "t"}


def test_liveness_unresponsive_when_lock_held_and_ping_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = ab._paths("default")
    _touch_lock(paths)
    monkeypatch.setattr(ab, "_lock_held", lambda p: True)
    monkeypatch.setattr(ab, "_read_session", lambda p: {"port": 1, "token": "t"})
    monkeypatch.setattr(ab, "_ping", lambda session, timeout=ab.PING_TIMEOUT: None)
    state, session = ab._liveness(paths)
    assert state == "unresponsive" and session == {"port": 1, "token": "t"}


def test_liveness_starting_when_lock_held_and_no_session_yet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = ab._paths("default")
    _touch_lock(paths)
    monkeypatch.setattr(ab, "_lock_held", lambda p: True)
    monkeypatch.setattr(ab, "_read_session", lambda p: None)
    state, session = ab._liveness(paths)
    assert state == "starting" and session is None


def test_liveness_stale_when_lock_free_but_session_file_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = ab._paths("default")  # lock file does not exist -> held is False without calling _lock_held
    monkeypatch.setattr(ab, "_read_session", lambda p: {"port": 1, "token": "t"})
    state, session = ab._liveness(paths)
    assert state == "stale" and session == {"port": 1, "token": "t"}


def test_liveness_none_when_nothing_present(tmp_path: Path) -> None:
    paths = ab._paths("default")
    state, session = ab._liveness(paths)
    assert state == "none" and session is None


# ---------------------------------------------------------------------------
# _running_profiles / _resolve_profile


def _make_running_profile(name: str) -> None:
    paths = ab._paths(name)
    _touch_lock(paths)
    ab._write_json_atomic(paths.session, {"port": 1, "token": "t"})


def test_running_profiles_lists_only_profiles_with_a_held_lock_and_a_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _make_running_profile("alpha")
    ab._paths("beta")  # exists on disk but never started: no lock, no session
    monkeypatch.setattr(ab, "_lock_held", lambda p: True)
    assert ab._running_profiles() == ["alpha"]


def test_running_profiles_empty_when_no_profiles_dir(tmp_path: Path) -> None:
    assert ab._running_profiles() == []


def test_resolve_profile_sticky_rules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ab, "_running_profiles", lambda: [])
    assert ab._resolve_profile(None) == ab.DEFAULT_PROFILE

    monkeypatch.setattr(ab, "_running_profiles", lambda: ["work"])
    assert ab._resolve_profile(None) == "work"

    monkeypatch.setattr(ab, "_running_profiles", lambda: ["alpha", "beta"])
    with pytest.raises(ValueError) as exc:
        ab._resolve_profile(None)
    assert exc.value.error_class == ab.ERR_AMBIGUOUS_PROFILE
    assert exc.value.details == {"profiles": ["alpha", "beta"]}

    # explicit --profile wins even with several running
    assert ab._resolve_profile("chosen") == "chosen"

    # AGENT_BROWSER_PROFILE wins over the sticky running-profile lookup
    monkeypatch.setenv("AGENT_BROWSER_PROFILE", "env-pick")
    assert ab._resolve_profile(None) == "env-pick"

    # sticky=False skips the running-profile scan entirely
    monkeypatch.delenv("AGENT_BROWSER_PROFILE")
    assert ab._resolve_profile(None, sticky=False) == ab.DEFAULT_PROFILE


# ---------------------------------------------------------------------------
# start_session


def _start_args() -> argparse.Namespace:
    return argparse.Namespace(browser=None, exe=None, headless=True, window_size=None)


def _valid_session(paths: ab.ProfilePaths, **overrides: Any) -> dict[str, Any]:
    data = {"schema": 1, "profile": paths.profile, "pid": 9999, "port": 4444, "token": "tok",
            "browser": "msedge", "browser_version": "119.0", "headless": True, "code_hash": ab._code_hash()}
    data.update(overrides)
    return data


def test_start_session_returns_already_running_without_spawning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = ab._paths("default")
    _touch_lock(paths)
    session = _valid_session(paths)
    ab._write_json_atomic(paths.session, session)
    monkeypatch.setattr(ab, "_lock_held", lambda p: True)
    monkeypatch.setattr(ab, "_ping", lambda s, timeout=ab.PING_TIMEOUT: {"pong": True, "current_tab": 1, "url": "about:blank"})

    def refuse_spawn(*a: Any, **k: Any) -> Any:
        raise AssertionError("_spawn_daemon must not be called when already running")

    monkeypatch.setattr(ab, "_spawn_daemon", refuse_spawn)
    result = ab.start_session(paths, _start_args())
    assert result["status"] == "already_running"
    assert result["pid"] == 9999


def _install_success_recorder(monkeypatch: pytest.MonkeyPatch, paths: ab.ProfilePaths) -> None:
    def fake_spawn(argv: list[str], log_path: Path, cwd: Path) -> tuple[int, str]:
        assert argv[0] == ab.sys.executable and argv[1:3] == ["-X", "utf8"]
        session = _valid_session(paths, pid=1234, port=5555)
        ab._write_json_atomic(paths.session, session)
        return 1234, "popen"

    monkeypatch.setattr(ab, "_spawn_daemon", fake_spawn)
    monkeypatch.setattr(ab, "_ping", lambda s, timeout=ab.PING_TIMEOUT: {"pong": True})
    monkeypatch.setattr(ab, "_pid_alive", lambda pid: True)


def test_start_session_spawns_and_writes_launch_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = ab._paths("default")
    _install_success_recorder(monkeypatch, paths)

    result = ab.start_session(paths, _start_args())

    assert result["status"] == "started"
    assert result["pid"] == 1234 and result["port"] == 5555
    launch = json.loads(paths.launch.read_text(encoding="utf-8"))
    assert launch["token"] and launch["browser"] == "msedge" and launch["headless"] is True
    assert launch["config"] == {} and "code_hash" in launch
    argv = [ab.sys.executable, "-X", "utf8", str(Path(ab.__file__).resolve()), "serve", "--launch-file", str(paths.launch)]
    assert argv[0] == ab.sys.executable


def test_start_session_on_a_stale_session_removes_it_and_kills_leftovers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = ab._paths("default")
    ab._write_json_atomic(paths.session, _valid_session(paths))  # stale: no lock held
    killed = []
    monkeypatch.setattr(ab, "_kill_browsers_using", lambda user_data, dry_run=False: (killed.append(user_data) or [777]))
    _install_success_recorder(monkeypatch, paths)

    result = ab.start_session(paths, _start_args())

    assert result["status"] == "started"
    assert killed == [paths.user_data]


@pytest.mark.parametrize(
    "log_text, error_class",
    [
        ("Playwright is not installed for this interpreter", ab.ERR_PLAYWRIGHT_MISSING),
        ("launch_persistent_context: already in use", ab.ERR_PROFILE_IN_USE),
    ],
)
def test_start_session_daemon_exit_early_maps_log_text_to_error_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock, log_text: str, error_class: str
) -> None:
    paths = ab._paths("default")
    paths.dir.mkdir(parents=True, exist_ok=True)
    paths.log.write_text(log_text, encoding="utf-8")
    monkeypatch.setattr(ab, "_spawn_daemon", lambda argv, log_path, cwd: (4321, "popen"))
    monkeypatch.setattr(ab, "_ping", lambda s, timeout=ab.PING_TIMEOUT: None)
    monkeypatch.setattr(ab, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(ab, "policy_blockers", lambda policies: [])

    with pytest.raises(RuntimeError) as exc:
        ab.start_session(paths, _start_args())
    assert exc.value.error_class == error_class
    assert log_text in exc.value.details["log"]


def test_start_session_daemon_exit_early_with_policy_blocker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = ab._paths("default")
    paths.dir.mkdir(parents=True, exist_ok=True)
    paths.log.write_text("browser exited unexpectedly", encoding="utf-8")
    monkeypatch.setattr(ab, "_spawn_daemon", lambda argv, log_path, cwd: (4321, "popen"))
    monkeypatch.setattr(ab, "_ping", lambda s, timeout=ab.PING_TIMEOUT: None)
    monkeypatch.setattr(ab, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(ab, "read_policies", lambda kind: {"RemoteDebuggingAllowed": {"status": "fail", "value": 0, "label": "blocked"}})

    with pytest.raises(RuntimeError) as exc:
        ab.start_session(paths, _start_args())
    assert exc.value.error_class == ab.ERR_POLICY


def test_start_session_daemon_exit_early_generic_launch_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = ab._paths("default")
    paths.dir.mkdir(parents=True, exist_ok=True)
    paths.log.write_text("segfault, no idea why", encoding="utf-8")
    monkeypatch.setattr(ab, "_spawn_daemon", lambda argv, log_path, cwd: (4321, "popen"))
    monkeypatch.setattr(ab, "_ping", lambda s, timeout=ab.PING_TIMEOUT: None)
    monkeypatch.setattr(ab, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(ab, "policy_blockers", lambda policies: [])

    with pytest.raises(RuntimeError) as exc:
        ab.start_session(paths, _start_args())
    assert exc.value.error_class == ab.ERR_LAUNCH


# ---------------------------------------------------------------------------
# stop_session


def test_stop_session_not_running_returns_false_without_side_effects(tmp_path: Path) -> None:
    paths = ab._paths("default")
    result = ab.stop_session(paths)
    assert result == {"profile": "default", "stopped": False, "killed_pids": []}


def test_stop_session_requests_stop_then_waits_and_skips_kill_tree_if_pid_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock
) -> None:
    paths = ab._paths("default")
    _touch_lock(paths)
    session = _valid_session(paths, pid=555)
    ab._write_json_atomic(paths.session, session)
    monkeypatch.setattr(ab, "_lock_held", lambda p: True)
    monkeypatch.setattr(ab, "_request", lambda s, cmd, args, timeout, grace=ab.CLIENT_GRACE: {"stopping": True})
    monkeypatch.setattr(ab, "_pid_alive", lambda pid: False)  # exited during the grace wait
    monkeypatch.setattr(ab, "_kill_browsers_using", lambda user_data, dry_run=False: [])
    kill_tree_calls = []
    monkeypatch.setattr(ab, "_kill_tree", lambda pid: kill_tree_calls.append(pid))

    result = ab.stop_session(paths)

    assert result["stopped"] is True
    assert kill_tree_calls == []  # pid was already gone: no forced kill needed


def test_stop_session_kill_tree_only_if_pid_still_alive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = ab._paths("default")
    _touch_lock(paths)
    ab._write_json_atomic(paths.session, _valid_session(paths, pid=555))
    monkeypatch.setattr(ab, "_lock_held", lambda p: True)
    monkeypatch.setattr(ab, "_request", lambda s, cmd, args, timeout, grace=ab.CLIENT_GRACE: {"stopping": True})
    monkeypatch.setattr(ab, "_pid_alive", lambda pid: True)  # never exits
    monkeypatch.setattr(ab, "_kill_browsers_using", lambda user_data, dry_run=False: [])
    kill_tree_calls = []
    monkeypatch.setattr(ab, "_kill_tree", lambda pid: kill_tree_calls.append(pid))

    result = ab.stop_session(paths)

    assert result["stopped"] is True
    assert kill_tree_calls == [555]
    assert 555 in result["killed_pids"]


def test_stop_session_also_kills_browsers_using_the_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock) -> None:
    paths = ab._paths("default")
    _touch_lock(paths)
    ab._write_json_atomic(paths.session, _valid_session(paths, pid=555))
    monkeypatch.setattr(ab, "_lock_held", lambda p: True)
    monkeypatch.setattr(ab, "_request", lambda s, cmd, args, timeout, grace=ab.CLIENT_GRACE: {})
    monkeypatch.setattr(ab, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(ab, "_kill_tree", lambda pid: None)
    monkeypatch.setattr(ab, "_kill_browsers_using", lambda user_data, dry_run=False: [111, 222])

    result = ab.stop_session(paths)
    assert result["killed_pids"] == [111, 222]


# ---------------------------------------------------------------------------
# status_session


@pytest.mark.parametrize(
    "state, expected_reason",
    [("none", "no_session"), ("stale", "pid_dead"), ("unresponsive", "unresponsive"), ("starting", "starting")],
)
def test_status_session_not_running_reasons(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: str, expected_reason: str) -> None:
    paths = ab._paths("default")
    monkeypatch.setattr(ab, "_liveness", lambda p: (state, None))
    result = ab.status_session(paths)
    assert result["running"] is False
    assert result["reason"] == expected_reason


def test_status_session_running_reports_stale_code_when_hash_differs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = ab._paths("default")
    session = _valid_session(paths, code_hash="old-hash", started=ab._utc_now())
    monkeypatch.setattr(ab, "_liveness", lambda p: ("running", session))
    monkeypatch.setattr(ab, "_ping", lambda s, timeout=1.0: {"current_tab": 1, "url": "http://x", "title": "X", "tabs": []})
    monkeypatch.setattr(ab, "_code_hash", lambda: "new-hash")

    result = ab.status_session(paths)
    assert result["running"] is True
    assert result["stale_code"] is True
    assert result["note"] == ab._hint("stale_code", "default")


def test_status_session_notes_a_session_open_more_than_8_hours(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = ab._paths("default")
    started = (datetime.now(timezone.utc) - timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%SZ")
    session = _valid_session(paths, started=started)
    monkeypatch.setattr(ab, "_liveness", lambda p: ("running", session))
    monkeypatch.setattr(ab, "_ping", lambda s, timeout=1.0: {})

    result = ab.status_session(paths)
    assert result["uptime_s"] > 8 * 3600
    assert "hours" in result["note"]


# ---------------------------------------------------------------------------
# clean


def test_clean_removes_stale_sessions_and_prunes_old_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = ab._paths("default")
    ab._write_json_atomic(paths.session, _valid_session(paths))  # stale: no lock held
    paths.shots.mkdir(parents=True, exist_ok=True)
    old_file = paths.shots / "old.png"
    old_file.write_bytes(b"x")
    os.utime(old_file, (time.time() - 8 * 86400, time.time() - 8 * 86400))
    new_file = paths.shots / "new.png"
    new_file.write_bytes(b"x")
    monkeypatch.setattr(ab, "_kill_browsers_using", lambda user_data, dry_run=False: [])

    result = ab.clean()

    assert result["stale_sessions_removed"] == ["default"]
    assert not paths.session.exists()
    assert result["artifacts_removed"] == 1
    assert not old_file.exists() and new_file.exists()


def test_clean_dry_run_touches_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = ab._paths("default")
    ab._write_json_atomic(paths.session, _valid_session(paths))
    paths.shots.mkdir(parents=True, exist_ok=True)
    old_file = paths.shots / "old.png"
    old_file.write_bytes(b"x")
    os.utime(old_file, (time.time() - 8 * 86400, time.time() - 8 * 86400))
    monkeypatch.setattr(ab, "_kill_browsers_using", lambda user_data, dry_run=False: [])

    result = ab.clean(dry_run=True)

    assert result["stale_sessions_removed"] == ["default"]  # reported...
    assert paths.session.exists()  # ...but not actually removed
    assert result["artifacts_removed"] == 1
    assert old_file.exists()


def test_clean_purge_refused_while_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = ab._paths("default")
    _touch_lock(paths)
    ab._write_json_atomic(paths.session, _valid_session(paths))
    monkeypatch.setattr(ab, "_lock_held", lambda p: True)
    monkeypatch.setattr(ab, "_ping", lambda s, timeout=ab.PING_TIMEOUT: {"pong": True})
    monkeypatch.setattr(ab, "_kill_browsers_using", lambda user_data, dry_run=False: [])

    with pytest.raises(ValueError) as exc:
        ab.clean(purge="default")
    assert exc.value.error_class == ab.ERR_VALIDATION
    assert exc.value.hint == ab._hint("purge_running", "default")


def test_clean_purge_removes_a_stopped_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    paths = ab._paths("default")
    paths.dir.mkdir(parents=True, exist_ok=True)
    (paths.dir / "marker.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(ab, "_kill_browsers_using", lambda user_data, dry_run=False: [])

    result = ab.clean(purge="default")
    assert result["profiles_purged"] == ["default"]
    assert not paths.dir.exists()


def test_clean_unknown_purge_is_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ab._paths("other").dir.mkdir(parents=True, exist_ok=True)  # profiles dir must exist to reach the purge check
    monkeypatch.setattr(ab, "_kill_browsers_using", lambda user_data, dry_run=False: [])
    with pytest.raises(ValueError) as exc:
        ab.clean(purge="no-such-profile")
    assert exc.value.error_class == ab.ERR_NOT_FOUND


# ---------------------------------------------------------------------------
# _spawn_daemon: Windows popen breakaway retry, and the wmi launcher


def test_spawn_daemon_retries_without_breakaway_on_winerror_5(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ab, "_is_windows", lambda: True)
    calls: list[int] = []

    def fake_popen(argv: list[str], cwd: str | None = None, stdin: Any = None, stdout: Any = None,
                   stderr: Any = None, close_fds: bool | None = None, creationflags: int = 0, **kw: Any) -> Any:
        calls.append(creationflags)
        if creationflags & ab.subprocess.CREATE_BREAKAWAY_FROM_JOB:
            err = OSError("Access is denied")
            err.winerror = 5
            raise err
        return type("Proc", (), {"pid": 4242})()

    monkeypatch.setattr(ab.subprocess, "Popen", fake_popen)
    pid, launcher = ab._spawn_daemon(["prog", "arg"], tmp_path / "log.txt", tmp_path)

    assert pid == 4242 and launcher == "popen"
    assert len(calls) == 2
    assert calls[0] & ab.subprocess.CREATE_BREAKAWAY_FROM_JOB
    assert not (calls[1] & ab.subprocess.CREATE_BREAKAWAY_FROM_JOB)


def test_spawn_daemon_wmi_launcher_parses_the_pid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ab, "_is_windows", lambda: True)
    monkeypatch.setenv("AGENT_BROWSER_LAUNCHER", "wmi")
    seen_cmd: dict[str, Any] = {}

    def fake_run(cmd: list[str], capture_output: bool = True, text: bool = True, timeout: float = 60, check: bool = False) -> Any:
        seen_cmd["cmd"] = cmd
        return type("Result", (), {"stdout": "9876\n", "stderr": ""})()

    monkeypatch.setattr(ab.subprocess, "run", fake_run)
    pid, launcher = ab._spawn_daemon(["prog", "arg"], tmp_path / "log.txt", tmp_path)

    assert pid == 9876 and launcher == "wmi"
    assert seen_cmd["cmd"][0] == "powershell"
    assert "Invoke-CimMethod" in seen_cmd["cmd"][-1]
