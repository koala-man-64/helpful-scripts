"""CLI surface: agent_browser.main([...]) argument parsing and the exit-code/error contract.

No test here starts a daemon or opens a socket. Verbs that would normally need a running
browser (wait, goto) are exercised only up to the point where they fail argument validation
before ever looking for a session; status/stop are exercised for real against an empty
AGENT_BROWSER_HOME (both are designed to always succeed, running or not); doctor runs for
real with its registry/PATH/subprocess touchpoints replaced by fakes.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Callable

import pytest

import agent_browser as ab
from agent_browser_core import cli as core_cli, common as core_common, daemon as core_daemon
from conftest import error_envelope

def _raising_handler(exc: Exception) -> Callable[[argparse.Namespace], Any]:
    def handler(args: argparse.Namespace) -> Any:
        raise exc

    return handler

# ---------------------------------------------------------------------------
# argparse basics

def test_version_prints_tool_and_version_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        core_cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"{core_common.TOOL_NAME} {core_common.TOOL_VERSION}"

def test_unknown_verb_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        core_cli.main(["frobnicate"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err

# ---------------------------------------------------------------------------
# The error envelope contract (repo-wide: ValueError=2, RuntimeError=1, timeout class=124)

@pytest.mark.parametrize(
    "make_exc, expected_code",
    [
        (lambda: core_common._tag(ValueError("bad thing"), core_common.ERR_VALIDATION, hint="do X"), 2),
        (lambda: core_common._tag(RuntimeError("boom"), core_common.ERR_DAEMON), 1),
        (lambda: core_common._tag(RuntimeError("timed out"), core_common.ERR_TIMEOUT), 124),
    ],
)
def test_tagged_exception_exit_codes_and_stderr_envelope(
    make_exc: Callable[[], Exception], expected_code: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exc = make_exc()
    monkeypatch.setattr(core_cli, "_cmd_status", _raising_handler(exc))
    code = core_cli.main(["status"])
    assert code == expected_code
    out, err = capsys.readouterr()
    assert out == ""  # nothing is printed on the success path when the handler raises
    envelope = error_envelope(err)
    assert envelope["class"] == exc.error_class
    assert envelope["message"] == str(exc)
    assert envelope["hint"] == getattr(exc, "hint", None)

def test_keyboard_interrupt_exits_130(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(core_cli, "_cmd_status", _raising_handler(KeyboardInterrupt()))
    code = core_cli.main(["status"])
    assert code == 130
    assert "interrupted" in capsys.readouterr().err

# ---------------------------------------------------------------------------
# --profile: accepted before or after the verb, last occurrence wins

def test_profile_flag_before_after_and_both(capture_args: Callable[[str], list[Any]]) -> None:
    calls = capture_args("status")
    core_cli.main(["--profile", "before1", "status"])
    core_cli.main(["status", "--profile", "after1"])
    core_cli.main(["--profile", "before2", "status", "--profile", "after2"])
    assert [c.profile for c in calls] == ["before1", "after1", "after2"]

# ---------------------------------------------------------------------------
# wait: validation happens before any session lookup

def test_wait_validation_errors_need_no_session(capsys: pytest.CaptureFixture[str]) -> None:
    code = core_cli.main(["wait", "--timeout", "200", "--text", "hi"])
    assert code == 2
    envelope = error_envelope(capsys.readouterr().err)
    assert envelope["class"] == core_common.ERR_VALIDATION and envelope["hint"] == core_common._hint("wait_cap")

    code = core_cli.main(["wait"])
    assert code == 2
    envelope = error_envelope(capsys.readouterr().err)
    assert envelope["class"] == core_common.ERR_USAGE and envelope["hint"] == core_common._hint("no_wait_condition")

# ---------------------------------------------------------------------------
# goto: the scheme guard fires before any daemon/session lookup

def test_goto_guarded_scheme_fires_before_any_session_lookup(capsys: pytest.CaptureFixture[str]) -> None:
    code = core_cli.main(["goto", "edge://settings"])
    assert code == 2
    envelope = error_envelope(capsys.readouterr().err)
    assert envelope["class"] == core_common.ERR_GUARDED
    assert envelope["hint"] == core_common._hint("guarded_scheme")
    assert not (core_common._home() / "profiles").exists()  # no profile dir touched: nothing tried to reach a daemon

# ---------------------------------------------------------------------------
# status / stop against an empty home: always exit 0

def test_status_and_stop_on_an_empty_home_exit_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert core_cli.main(["status"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["running"] is False and status_payload["reason"] == "no_session"

    assert core_cli.main(["stop"]) == 0
    stop_payload = json.loads(capsys.readouterr().out)
    assert stop_payload["stopped"] is False

# ---------------------------------------------------------------------------
# doctor: never touches the registry, PATH, or a real subprocess

def test_doctor_exits_zero_and_routes_every_touchpoint_through_the_patched_seam(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: dict[str, int] = {"subprocess": 0, "registry": 0, "job_info": 0, "which": 0}

    class _FakeCompleted:
        stdout = ""

    def counted(key: str, value: Any) -> Callable[..., Any]:
        def fn(*a: Any, **k: Any) -> Any:
            calls[key] += 1
            return value

        return fn

    monkeypatch.setattr(core_daemon, "_playwright_version", lambda: "1.61.0")
    monkeypatch.setattr(core_common.shutil, "which", counted("which", None))
    monkeypatch.setattr(core_common.subprocess, "run", counted("subprocess", _FakeCompleted()))
    monkeypatch.setattr(core_common, "_registry_value", counted("registry", None))
    monkeypatch.setattr(core_common, "_job_info", counted("job_info", {"in_job": False, "kill_on_job_close": False}))
    monkeypatch.setattr(core_common, "_processes_using", lambda needle: [])

    code = core_cli.main(["doctor"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) >= {"ok", "failed", "checks"}
    assert isinstance(payload["checks"], list) and payload["checks"]
    assert isinstance(payload["ok"], bool) and isinstance(payload["failed"], list)
    # recording fakes (not just static returns) prove doctor() actually calls each seam
    assert calls["subprocess"] >= 1  # the where.exe lookup
    assert calls["registry"] >= 1  # policy + app-path + long-paths-enabled lookups
    assert calls["job_info"] == 1
    assert calls["which"] >= 1

# ---------------------------------------------------------------------------
# MSYS (Git Bash) argv de-mangling

def test_demangle_msys_only_rewrites_under_msystem(monkeypatch: pytest.MonkeyPatch) -> None:
    argv = ["--url-contains=C:/Program Files/Git/incident.do"]
    assert core_cli._demangle_msys(argv) == argv  # no MSYSTEM: unchanged

    monkeypatch.setenv("MSYSTEM", "MINGW64")
    monkeypatch.setenv("EXEPATH", r"C:\Program Files\Git\usr\bin")
    out = core_cli._demangle_msys(argv)
    assert out == ["--url-contains=/incident.do"]
    assert any("rewritten by Git Bash" in note for note in core_common._ARGV_NOTES)

def test_demangle_msys_through_main_reaches_the_handler(monkeypatch: pytest.MonkeyPatch, capture_args: Callable[[str], list[Any]]) -> None:
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    monkeypatch.setenv("EXEPATH", r"C:\Program Files\Git\usr\bin")
    calls = capture_args("wait")
    core_cli.main(["wait", "--url-contains=C:/Program Files/Git/incident.do"])
    assert calls[0].url_contains == "/incident.do"

# ---------------------------------------------------------------------------
# UTF-8 stdout when stdout is a pipe (not a tty)

def test_main_prints_non_ascii_result_without_raising(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(core_cli, "_cmd_status", lambda args: {"title": "caf\u00e9 \u2713 \U0001F600"})
    code = core_cli.main(["status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "caf\u00e9" in out and "\u2713" in out
