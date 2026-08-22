"""Edge discovery, policies, argv, DevToolsActivePort, launch and teardown - all offline.

Every process/registry/socket edge is either a pure function or a seam replaced by the
`fake_edge` fixture in conftest.py. Nothing here starts Edge or reads the real registry.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import edge_pyodide as ep
from conftest import FakeEdgeEnv, FakeProcess


# ---------------------------------------------------------------------------
# Helpers


def _touch_exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")
    return path


def _policy_registry(values: dict[tuple[str, str], Any]):
    """Dict-driven stand-in for ep._registry_value keyed by (hive, value_name)."""

    def fake(key_path: str, value_name: str | None, hive: str = "HKLM") -> Any:
        if key_path != ep.EDGE_POLICY_KEY:
            return None
        return values.get((hive, value_name))

    return fake


class FakeClock:
    """Monotonic clock that jumps `step` seconds on every read (no real sleeping)."""

    def __init__(self, start: float = 1000.0, step: float = 20.0) -> None:
        self.now = start
        self.step = step
        self.reads = 0

    def __call__(self) -> float:
        self.reads += 1
        self.now += self.step
        return self.now


@pytest.fixture
def edge_lookup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    """Neutral discovery environment: no env override, no PATH hit, empty candidate folders."""
    monkeypatch.delenv("EDGEPY_EDGE_PATH", raising=False)
    monkeypatch.setattr(ep, "_is_windows", lambda: True)
    monkeypatch.setattr(ep.shutil, "which", lambda name: None)
    monkeypatch.setattr(ep, "_registry_value", lambda *a, **k: None)
    bases = {}
    for env_name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = tmp_path / env_name.replace("(", "_").replace(")", "")
        base.mkdir()
        monkeypatch.setenv(env_name, str(base))
        bases[env_name] = base
    return bases


# ---------------------------------------------------------------------------
# find_edge precedence


def test_find_edge_explicit_path_wins_over_everything(edge_lookup: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    explicit = _touch_exe(tmp_path / "explicit" / "msedge.exe")
    env_exe = _touch_exe(tmp_path / "env" / "msedge.exe")
    which_exe = _touch_exe(tmp_path / "which" / "msedge.exe")
    folder_exe = _touch_exe(edge_lookup["ProgramFiles"] / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    monkeypatch.setenv("EDGEPY_EDGE_PATH", str(env_exe))
    monkeypatch.setattr(ep.shutil, "which", lambda name: str(which_exe) if name == "msedge" else None)

    assert ep.find_edge(explicit) == explicit
    assert ep.find_edge(str(explicit)) == explicit
    assert folder_exe.is_file()  # the lower-priority candidate existed and was not chosen


def test_find_edge_env_var_beats_path_and_folders(edge_lookup: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_exe = _touch_exe(tmp_path / "env" / "msedge.exe")
    which_exe = _touch_exe(tmp_path / "which" / "msedge.exe")
    _touch_exe(edge_lookup["ProgramFiles"] / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    monkeypatch.setenv("EDGEPY_EDGE_PATH", str(env_exe))
    monkeypatch.setattr(ep.shutil, "which", lambda name: str(which_exe) if name == "msedge" else None)

    assert ep.find_edge() == env_exe


def test_find_edge_path_lookup_beats_candidate_folders(edge_lookup: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    which_exe = _touch_exe(tmp_path / "which" / "msedge.exe")
    _touch_exe(edge_lookup["ProgramFiles(x86)"] / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    monkeypatch.setattr(ep.shutil, "which", lambda name: str(which_exe) if name == "msedge" else None)

    assert ep.find_edge() == which_exe


def test_find_edge_falls_back_to_microsoft_edge_on_path(edge_lookup: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    which_exe = _touch_exe(tmp_path / "which" / "microsoft-edge")
    monkeypatch.setattr(ep.shutil, "which", lambda name: str(which_exe) if name == "microsoft-edge" else None)

    assert ep.find_edge() == which_exe


def test_find_edge_candidate_folders_follow_table_order(edge_lookup: dict[str, Path]) -> None:
    # Stable Edge under LOCALAPPDATA (3rd entry) outranks Edge Beta under ProgramFiles(x86) (4th).
    local = _touch_exe(edge_lookup["LOCALAPPDATA"] / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    _touch_exe(edge_lookup["ProgramFiles(x86)"] / "Microsoft" / "Edge Beta" / "Application" / "msedge.exe")

    assert ep.find_edge() == local


def test_find_edge_program_files_x86_beats_program_files(edge_lookup: dict[str, Path]) -> None:
    x86 = _touch_exe(edge_lookup["ProgramFiles(x86)"] / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    _touch_exe(edge_lookup["ProgramFiles"] / "Microsoft" / "Edge" / "Application" / "msedge.exe")

    assert ep.find_edge() == x86


def test_find_edge_uses_app_paths_registry_as_last_resort(edge_lookup: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    reg_exe = _touch_exe(tmp_path / "reg" / "msedge.exe")
    seen: list[tuple[str, str | None, str]] = []

    def registry(key_path: str, value_name: str | None, hive: str = "HKLM") -> Any:
        seen.append((key_path, value_name, hive))
        return str(reg_exe) if key_path == ep.EDGE_APP_PATHS_KEY else None

    monkeypatch.setattr(ep, "_registry_value", registry)

    assert ep.find_edge() == reg_exe
    assert (ep.EDGE_APP_PATHS_KEY, None, "HKLM") in seen


def test_find_edge_skips_candidates_that_are_not_files(edge_lookup: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing = tmp_path / "nowhere" / "msedge.exe"
    folder_only = tmp_path / "dir-not-file" / "msedge.exe"
    folder_only.mkdir(parents=True)
    real = _touch_exe(tmp_path / "real" / "msedge.exe")
    monkeypatch.setenv("EDGEPY_EDGE_PATH", str(folder_only))
    monkeypatch.setattr(ep.shutil, "which", lambda name: str(real) if name == "msedge" else None)

    assert ep.find_edge(missing) == real


def test_find_edge_none_found_raises_tagged_runtime_error(edge_lookup: dict[str, Path]) -> None:
    with pytest.raises(RuntimeError) as info:
        ep.find_edge()
    exc = info.value
    assert exc.error_class == ep.ERR_EDGE_NOT_FOUND
    assert "EDGEPY_EDGE_PATH" in exc.hint
    assert "--edge-path" in exc.hint
    assert "not found" in str(exc)


def test_find_edge_explicit_path_missing_and_nothing_else_raises(edge_lookup: dict[str, Path], tmp_path: Path) -> None:
    with pytest.raises(RuntimeError) as info:
        ep.find_edge(tmp_path / "ghost" / "msedge.exe")
    assert info.value.error_class == ep.ERR_EDGE_NOT_FOUND


# ---------------------------------------------------------------------------
# edge_version_from_path


def test_edge_version_from_path_picks_highest_numeric_version(tmp_path: Path) -> None:
    exe = _touch_exe(tmp_path / "Application" / "msedge.exe")
    # Lexicographic order would pick "9.0.0.0" or "...4129.99"; numeric order must win.
    for name in ("9.0.0.0", "151.0.4129.99", "151.0.4129.101", "150.0.1.1"):
        (exe.parent / name).mkdir()
    (exe.parent / "Locales").mkdir()
    (exe.parent / "152.0.0.0").write_text("a file, not a version folder", encoding="utf-8")

    assert ep.edge_version_from_path(exe) == "151.0.4129.101"


def test_edge_version_from_path_ignores_non_version_folders(tmp_path: Path) -> None:
    exe = _touch_exe(tmp_path / "Application" / "msedge.exe")
    for name in ("Locales", "1.2.3", "1.2.3.4.5", "v1.0.0.0", "Dictionaries"):
        (exe.parent / name).mkdir()

    assert ep.edge_version_from_path(exe) is None


def test_edge_version_from_path_returns_none_for_missing_parent(tmp_path: Path) -> None:
    assert ep.edge_version_from_path(tmp_path / "nope" / "msedge.exe") is None


# ---------------------------------------------------------------------------
# edge_argv


def test_edge_argv_headless_contains_required_switches(tmp_path: Path) -> None:
    exe = tmp_path / "msedge.exe"
    profile = tmp_path / "profile"
    argv = ep.edge_argv(exe, profile, headless=True, devtools=False)

    assert argv[0] == str(exe)
    assert f"--user-data-dir={profile}" in argv
    assert "--remote-debugging-port=0" in argv
    assert "--no-first-run" in argv
    assert "--no-proxy-server" in argv
    assert argv[-1] == "about:blank"
    assert "--headless" in argv
    assert not any(a.startswith("--headless=") for a in argv)
    assert "--auto-open-devtools-for-tabs" not in argv


def test_edge_argv_visible_window_has_no_headless_switch(tmp_path: Path) -> None:
    argv = ep.edge_argv(tmp_path / "msedge.exe", tmp_path / "profile", headless=False, devtools=False)

    assert not any(a.startswith("--headless") for a in argv)
    assert "--auto-open-devtools-for-tabs" not in argv
    assert argv[-1] == "about:blank"


def test_edge_argv_devtools_flag_adds_auto_open_switch(tmp_path: Path) -> None:
    argv = ep.edge_argv(tmp_path / "msedge.exe", tmp_path / "profile", headless=False, devtools=True)

    assert "--auto-open-devtools-for-tabs" in argv
    assert argv[-1] == "about:blank"


def test_edge_argv_window_size_and_url_are_passed_through(tmp_path: Path) -> None:
    argv = ep.edge_argv(tmp_path / "msedge.exe", tmp_path / "profile", headless=True, devtools=False,
                        window_size="640,480", url="http://127.0.0.1:1/t/")

    assert "--window-size=640,480" in argv
    assert argv[-1] == "http://127.0.0.1:1/t/"


def test_edge_argv_default_window_size_is_module_default(tmp_path: Path) -> None:
    argv = ep.edge_argv(tmp_path / "msedge.exe", tmp_path / "profile", headless=True, devtools=False)

    assert f"--window-size={ep.DEFAULT_WINDOW_SIZE}" in argv


# ---------------------------------------------------------------------------
# parse_active_port


def test_parse_active_port_reads_port_and_browser_path() -> None:
    assert ep.parse_active_port("9229\n/devtools/browser/uuid") == (9229, "/devtools/browser/uuid")


def test_parse_active_port_tolerates_missing_second_line() -> None:
    assert ep.parse_active_port("9229") == (9229, "")
    assert ep.parse_active_port("9229\n") == (9229, "")


def test_parse_active_port_strips_whitespace_and_crlf() -> None:
    assert ep.parse_active_port(" 4567 \r\n/devtools/browser/x\r\n") == (4567, "/devtools/browser/x")


@pytest.mark.parametrize("text", ["garbage", "", "\n/devtools/browser/x", "port=9229", "-1\n/x"])
def test_parse_active_port_garbage_raises_launch_error(text: str) -> None:
    with pytest.raises(RuntimeError) as info:
        ep.parse_active_port(text)
    assert info.value.error_class == ep.ERR_EDGE_LAUNCH
    assert "DevToolsActivePort" in str(info.value)


# ---------------------------------------------------------------------------
# read_policies / policy_blockers


def test_read_policies_remote_debugging_blocked_is_fail_and_blocker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ep, "_registry_value", _policy_registry({("HKLM", "RemoteDebuggingAllowed"): 0}))

    policies = ep.read_policies()

    assert policies["RemoteDebuggingAllowed"]["status"] == "fail"
    assert policies["RemoteDebuggingAllowed"]["hive"] == "HKLM"
    assert policies["RemoteDebuggingAllowed"]["value"] == 0
    blockers = ep.policy_blockers(policies)
    assert len(blockers) == 1
    assert blockers[0].startswith("RemoteDebuggingAllowed=0")
    assert "BLOCKED" in blockers[0]


def test_read_policies_remote_debugging_allowed_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ep, "_registry_value", _policy_registry({("HKLM", "RemoteDebuggingAllowed"): 1}))

    policies = ep.read_policies()

    assert policies["RemoteDebuggingAllowed"]["status"] == "ok"
    assert ep.policy_blockers(policies) == []


def test_read_policies_remote_debugging_string_zero_is_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ep, "_registry_value", _policy_registry({("HKLM", "RemoteDebuggingAllowed"): "0"}))

    assert ep.read_policies()["RemoteDebuggingAllowed"]["status"] == "fail"


@pytest.mark.parametrize("value, expected", [(0, "ok"), (1, "ok"), (2, "fail"), ("2", "fail"), ("weird", "warn"), (7, "warn")])
def test_read_policies_developer_tools_availability_levels(monkeypatch: pytest.MonkeyPatch, value: Any, expected: str) -> None:
    monkeypatch.setattr(ep, "_registry_value", _policy_registry({("HKLM", "DeveloperToolsAvailability"): value}))

    policies = ep.read_policies()

    assert policies["DeveloperToolsAvailability"]["status"] == expected
    assert (len(ep.policy_blockers(policies)) == 1) is (expected == "fail")


def test_read_policies_headless_disabled_is_fail_with_show_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ep, "_registry_value", _policy_registry({("HKLM", "HeadlessModeEnabled"): 0}))

    policies = ep.read_policies()

    assert policies["HeadlessModeEnabled"]["status"] == "fail"
    assert "--show" in policies["HeadlessModeEnabled"]["label"]
    assert ep.policy_blockers(policies)[0].startswith("HeadlessModeEnabled=0")


def test_read_policies_headless_enabled_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ep, "_registry_value", _policy_registry({("HKLM", "HeadlessModeEnabled"): 1}))

    assert ep.read_policies()["HeadlessModeEnabled"]["status"] == "ok"


def test_read_policies_user_data_dir_is_warn_not_blocker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ep, "_registry_value", _policy_registry({("HKLM", "UserDataDir"): r"D:\profiles\${user_name}"}))

    policies = ep.read_policies()

    assert policies["UserDataDir"]["status"] == "warn"
    assert "DevToolsActivePort" in policies["UserDataDir"]["label"]
    assert ep.policy_blockers(policies) == []


def test_read_policies_consults_hkcu_when_hklm_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str | None, str]] = []
    base = _policy_registry({("HKCU", "RemoteDebuggingAllowed"): 0})

    def registry(key_path: str, value_name: str | None, hive: str = "HKLM") -> Any:
        calls.append((value_name, hive))
        return base(key_path, value_name, hive)

    monkeypatch.setattr(ep, "_registry_value", registry)

    policies = ep.read_policies()

    assert policies["RemoteDebuggingAllowed"]["hive"] == "HKCU"
    assert policies["RemoteDebuggingAllowed"]["status"] == "fail"
    assert calls.index(("RemoteDebuggingAllowed", "HKLM")) < calls.index(("RemoteDebuggingAllowed", "HKCU"))


def test_read_policies_hklm_shadows_hkcu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ep, "_registry_value", _policy_registry({
        ("HKLM", "RemoteDebuggingAllowed"): 1,
        ("HKCU", "RemoteDebuggingAllowed"): 0,
    }))

    policies = ep.read_policies()

    assert policies["RemoteDebuggingAllowed"] == {"hive": "HKLM", "value": 1, "status": "ok", "label": "allowed"}


def test_read_policies_empty_when_nothing_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ep, "_registry_value", _policy_registry({}))

    assert ep.read_policies() == {}
    assert ep.policy_blockers({}) == []


def test_read_policies_reports_every_known_policy_with_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ep, "_registry_value", _policy_registry({
        ("HKLM", "RemoteDebuggingAllowed"): 0,
        ("HKCU", "DeveloperToolsAvailability"): 2,
        ("HKLM", "HeadlessModeEnabled"): 0,
        ("HKCU", "UserDataDir"): "X:\\p",
    }))

    policies = ep.read_policies()

    assert set(policies) == set(ep.POLICY_LABELS)
    blockers = ep.policy_blockers(policies)
    assert [b.split("=")[0] for b in blockers] == ["RemoteDebuggingAllowed", "DeveloperToolsAvailability", "HeadlessModeEnabled"]


# ---------------------------------------------------------------------------
# wait_for_active_port


def _edge_process(tmp_path: Path, proc: FakeProcess) -> ep.EdgeProcess:
    run_dir = tmp_path / "run" / "rtest"
    profile = run_dir / "profile"
    profile.mkdir(parents=True)
    return ep.EdgeProcess(exe=tmp_path / "msedge.exe", proc=proc, run_dir=run_dir, profile_dir=profile, log_path=run_dir / "edge.log")


def test_wait_for_active_port_reads_file_when_present(fake_edge: FakeEdgeEnv, tmp_path: Path) -> None:
    edge = _edge_process(tmp_path, FakeProcess())
    (edge.profile_dir / "DevToolsActivePort").write_text("9229\n/devtools/browser/abc", encoding="utf-8")

    ep.wait_for_active_port(edge, timeout=1.0)

    assert (edge.port, edge.browser_path) == (9229, "/devtools/browser/abc")
    assert fake_edge.sleeps == []


def test_wait_for_active_port_polls_until_file_has_content(fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    edge = _edge_process(tmp_path, FakeProcess())
    port_file = edge.profile_dir / "DevToolsActivePort"
    port_file.write_text("", encoding="utf-8")  # Chromium creates then fills the file

    def sleep(seconds: float) -> None:
        fake_edge.sleeps.append(seconds)
        port_file.write_text("5555\n/devtools/browser/late", encoding="utf-8")

    monkeypatch.setattr(ep, "_sleep", sleep)

    ep.wait_for_active_port(edge, timeout=30.0)

    assert (edge.port, edge.browser_path) == (5555, "/devtools/browser/late")
    assert fake_edge.sleeps == [0.05]


def test_wait_for_active_port_early_exit_reports_log_tail_as_launch_error(fake_edge: FakeEdgeEnv, tmp_path: Path) -> None:
    proc = FakeProcess()
    proc.returncode = 21
    edge = _edge_process(tmp_path, proc)
    edge.log_path.write_text("\n".join(f"line {i}" for i in range(30)) + "\nFATAL: cannot open display\n", encoding="utf-8")

    with pytest.raises(RuntimeError) as info:
        ep.wait_for_active_port(edge, timeout=30.0)

    exc = info.value
    assert "exited with code 21" in str(exc)
    assert exc.error_class == ep.ERR_EDGE_LAUNCH
    assert exc.hint.startswith("Edge log tail:")
    assert "FATAL: cannot open display" in exc.hint
    assert "line 0" not in exc.hint  # only the tail, not the whole log
    assert "Blocking policies" not in exc.hint
    assert fake_edge.sleeps == []


def test_wait_for_active_port_early_exit_with_empty_log(fake_edge: FakeEdgeEnv, tmp_path: Path) -> None:
    proc = FakeProcess()
    proc.returncode = 1
    edge = _edge_process(tmp_path, proc)  # edge.log never written

    with pytest.raises(RuntimeError) as info:
        ep.wait_for_active_port(edge, timeout=30.0)

    assert info.value.error_class == ep.ERR_EDGE_LAUNCH
    assert "(empty)" in info.value.hint


def test_wait_for_active_port_early_exit_with_blocking_policy_is_policy_error(fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ep, "_registry_value", _policy_registry({("HKLM", "RemoteDebuggingAllowed"): 0}))
    proc = FakeProcess()
    proc.returncode = 1
    edge = _edge_process(tmp_path, proc)
    edge.log_path.write_text("DevTools remote debugging is disallowed by the system admin.\n", encoding="utf-8")

    with pytest.raises(RuntimeError) as info:
        ep.wait_for_active_port(edge, timeout=30.0)

    exc = info.value
    assert exc.error_class == ep.ERR_EDGE_POLICY
    assert exc.hint.startswith("Blocking policies: RemoteDebuggingAllowed=0")
    assert "Edge log tail:" in exc.hint
    assert "disallowed by the system admin" in exc.hint


def test_wait_for_active_port_timeout_mentions_port_file_and_doctor(fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clock = FakeClock(step=20.0)
    monkeypatch.setattr(ep, "_now", clock)
    edge = _edge_process(tmp_path, FakeProcess())  # never exits, never writes the file

    with pytest.raises(RuntimeError) as info:
        ep.wait_for_active_port(edge, timeout=30.0)

    exc = info.value
    assert "DevToolsActivePort" in str(exc)
    assert "30s" in str(exc)
    assert exc.error_class == ep.ERR_EDGE_LAUNCH
    assert "edgepy doctor" in exc.hint
    assert str(edge.log_path) in exc.hint
    assert fake_edge.sleeps == [0.05]  # one poll slice before the fake clock crossed the deadline


def test_wait_for_active_port_timeout_with_blocking_policy_is_policy_error(fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ep, "_now", FakeClock(step=50.0))
    monkeypatch.setattr(ep, "_registry_value", _policy_registry({("HKLM", "HeadlessModeEnabled"): 0}))
    edge = _edge_process(tmp_path, FakeProcess())

    with pytest.raises(RuntimeError) as info:
        ep.wait_for_active_port(edge, timeout=30.0)

    assert info.value.error_class == ep.ERR_EDGE_POLICY
    assert info.value.hint.startswith("Blocking policies: HeadlessModeEnabled=0")


# ---------------------------------------------------------------------------
# launch_edge / close_edge through the fake_edge fixture


def test_launch_edge_returns_populated_edge_process(fake_edge: FakeEdgeEnv, tmp_path: Path) -> None:
    edge = ep.launch_edge()
    try:
        assert edge.port == 9229
        assert edge.browser_path == "/devtools/browser/abc-123"
        assert edge.version == "Edg/151.0.4129.101"
        assert edge.run_dir.is_dir()
        assert edge.run_dir.parent == (tmp_path / "run").resolve()
        assert edge.profile_dir == edge.run_dir / "profile"
        assert edge.log_path == edge.run_dir / "edge.log"
        assert edge.exe == tmp_path / "Edge" / "Application" / "msedge.exe"
        assert edge.proc is fake_edge.process
        assert edge.pid == 4242
        assert fake_edge.launch_argv is not None
        assert fake_edge.launch_argv[0] == str(edge.exe)
        assert "--headless" in fake_edge.launch_argv
        assert f"--user-data-dir={edge.profile_dir}" in fake_edge.launch_argv
        assert f"http://127.0.0.1:{fake_edge.port}/json/version" in fake_edge.requests
    finally:
        ep.close_edge(edge)


def test_launch_edge_honours_show_and_devtools_flags(fake_edge: FakeEdgeEnv) -> None:
    edge = ep.launch_edge(headless=False, devtools=True, window_size="800,600")
    try:
        argv = fake_edge.launch_argv or []
        assert "--headless" not in argv
        assert "--auto-open-devtools-for-tabs" in argv
        assert "--window-size=800,600" in argv
    finally:
        ep.close_edge(edge)


def test_launch_edge_accepts_explicit_exe(fake_edge: FakeEdgeEnv, tmp_path: Path) -> None:
    other = _touch_exe(tmp_path / "other" / "msedge.exe")
    edge = ep.launch_edge(other)
    try:
        assert edge.exe == other
        assert (fake_edge.launch_argv or [])[0] == str(other)
    finally:
        ep.close_edge(edge)


def test_launch_edge_version_is_none_when_json_version_fails(fake_edge: FakeEdgeEnv) -> None:
    del fake_edge.http[f"http://127.0.0.1:{fake_edge.port}/json/version"]
    edge = ep.launch_edge()
    try:
        assert edge.version is None
        assert edge.port == 9229
    finally:
        ep.close_edge(edge)


def test_close_edge_sends_browser_close_and_removes_run_dir(fake_edge: FakeEdgeEnv) -> None:
    edge = ep.launch_edge()
    run_dir = edge.run_dir

    ep.close_edge(edge)

    assert fake_edge.browser_ws is not None
    assert [m["method"] for m in fake_edge.browser_ws.sent] == ["Browser.close"]
    assert fake_edge.browser_ws.closed
    assert fake_edge.process is not None and fake_edge.process.returncode == 0
    assert fake_edge.killed == []
    assert not run_dir.exists()


def test_close_edge_kills_tree_when_process_refuses_to_exit(fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    edge = ep.launch_edge()
    assert fake_edge.process is not None
    monkeypatch.setattr(fake_edge.process, "poll", lambda: None)

    ep.close_edge(edge)

    assert fake_edge.browser_ws is not None
    assert [m["method"] for m in fake_edge.browser_ws.sent] == ["Browser.close"]
    assert fake_edge.killed == [4242]
    assert not edge.run_dir.exists()


def test_close_edge_keep_run_dir_preserves_profile_and_log(fake_edge: FakeEdgeEnv) -> None:
    edge = ep.launch_edge()

    ep.close_edge(edge, keep_run_dir=True)

    assert edge.run_dir.is_dir()
    assert edge.log_path.is_file()
    assert (edge.run_dir / "owner.json").is_file()


def test_close_edge_skips_browser_close_when_process_already_exited(fake_edge: FakeEdgeEnv) -> None:
    edge = ep.launch_edge()
    assert fake_edge.process is not None
    fake_edge.process.returncode = 0

    ep.close_edge(edge)

    assert fake_edge.browser_ws is None
    assert fake_edge.killed == []
    assert not edge.run_dir.exists()


def test_close_edge_survives_browser_close_connection_failure(fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    edge = ep.launch_edge()

    def refuse(host: str, port: int, path: str, timeout: float = 10.0) -> Any:
        raise ep._tag(RuntimeError("Cannot connect to DevTools"), ep.ERR_CDP)

    monkeypatch.setattr(ep, "_ws_connect", refuse)

    ep.close_edge(edge)

    assert fake_edge.process is not None and fake_edge.process.returncode == 0
    assert not edge.run_dir.exists()


def test_launch_edge_cleans_up_run_dir_when_edge_dies_early(fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def dying_launch(argv: list[str], cwd: Path, log_path: Path) -> FakeProcess:
        log_path.write_text("[ERROR] crashed immediately\n", encoding="utf-8")
        proc = FakeProcess()
        proc.returncode = 3
        fake_edge.process = proc
        return proc

    monkeypatch.setattr(ep, "_launch_process", dying_launch)

    with pytest.raises(RuntimeError) as info:
        ep.launch_edge()

    assert info.value.error_class == ep.ERR_EDGE_LAUNCH
    assert "crashed immediately" in info.value.hint
    assert list((tmp_path / "run").iterdir()) == []  # the per-run folder was torn down


def test_launch_edge_wraps_spawn_failure_as_launch_error(fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_launch(argv: list[str], cwd: Path, log_path: Path) -> FakeProcess:
        raise OSError("access denied")

    monkeypatch.setattr(ep, "_launch_process", failing_launch)

    with pytest.raises(RuntimeError) as info:
        ep.launch_edge()

    assert info.value.error_class == ep.ERR_EDGE_LAUNCH
    assert "Cannot start Edge" in str(info.value)
    assert "access denied" in str(info.value)


# ---------------------------------------------------------------------------
# _make_run_dir


def test_make_run_dir_creates_profile_and_owner_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EDGEPY_RUN_DIR", str(tmp_path / "run"))

    run_dir = ep._make_run_dir()

    assert run_dir.parent == (tmp_path / "run").resolve()
    assert run_dir.name.startswith("r") and len(run_dir.name) == 9
    assert (run_dir / "profile").is_dir()
    assert not (run_dir / "profile" / "edgepy-write-test").exists()
    owner = json.loads((run_dir / "owner.json").read_text(encoding="utf-8"))
    assert owner["pid"] == os.getpid()
    assert isinstance(owner["created"], float)


def test_make_run_dir_generates_unique_folders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EDGEPY_RUN_DIR", str(tmp_path / "run"))

    first = ep._make_run_dir()
    second = ep._make_run_dir()

    assert first != second
    assert first.is_dir() and second.is_dir()


def test_make_run_dir_too_long_path_raises_launch_error_naming_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Long enough to pass MAX_RUN_DIR_CHARS but still well under Windows MAX_PATH.
    filler = "d" * max(1, ep.MAX_RUN_DIR_CHARS - len(str(tmp_path)) + 5)
    monkeypatch.setenv("EDGEPY_RUN_DIR", str(tmp_path / filler))

    with pytest.raises(RuntimeError) as info:
        ep._make_run_dir()

    exc = info.value
    assert exc.error_class == ep.ERR_EDGE_LAUNCH
    assert "too long" in str(exc)
    assert "EDGEPY_RUN_DIR" in exc.hint


def test_make_run_dir_unwritable_root_raises_launch_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file in the way", encoding="utf-8")
    monkeypatch.setenv("EDGEPY_RUN_DIR", str(blocker / "run"))

    with pytest.raises(RuntimeError) as info:
        ep._make_run_dir()

    exc = info.value
    assert exc.error_class == ep.ERR_EDGE_LAUNCH
    assert "Cannot create the run directory root" in str(exc)
    assert "EDGEPY_RUN_DIR" in exc.hint
