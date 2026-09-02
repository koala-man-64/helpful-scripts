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
from conftest import error_envelope

def _raising_handler(exc: Exception) -> Callable[[argparse.Namespace], Any]:
    def handler(args: argparse.Namespace) -> Any:
        raise exc

    return handler

# ---------------------------------------------------------------------------
# argparse basics

def test_version_prints_tool_and_version_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        ab.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"{ab.TOOL_NAME} {ab.TOOL_VERSION}"

def test_unknown_verb_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        ab.main(["frobnicate"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err

# ---------------------------------------------------------------------------
# The error envelope contract (repo-wide: ValueError=2, RuntimeError=1, timeout class=124)

@pytest.mark.parametrize(
    "make_exc, expected_code",
    [
        (lambda: ab._tag(ValueError("bad thing"), ab.ERR_VALIDATION, hint="do X"), 2),
        (lambda: ab._tag(RuntimeError("boom"), ab.ERR_DAEMON), 1),
        (lambda: ab._tag(RuntimeError("timed out"), ab.ERR_TIMEOUT), 124),
    ],
)
def test_tagged_exception_exit_codes_and_stderr_envelope(
    make_exc: Callable[[], Exception], expected_code: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exc = make_exc()
    monkeypatch.setattr(ab, "_cmd_status", _raising_handler(exc))
    code = ab.main(["status"])
    assert code == expected_code
    out, err = capsys.readouterr()
    assert out == ""  # nothing is printed on the success path when the handler raises
    envelope = error_envelope(err)
    assert envelope["class"] == exc.error_class
    assert envelope["message"] == str(exc)
    assert envelope["hint"] == getattr(exc, "hint", None)

def test_keyboard_interrupt_exits_130(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(ab, "_cmd_status", _raising_handler(KeyboardInterrupt()))
    code = ab.main(["status"])
    assert code == 130
    assert "interrupted" in capsys.readouterr().err

# ---------------------------------------------------------------------------
# --profile: accepted before or after the verb, last occurrence wins

def test_profile_flag_before_after_and_both(capture_args: Callable[[str], list[Any]]) -> None:
    calls = capture_args("status")
    ab.main(["--profile", "before1", "status"])
    ab.main(["status", "--profile", "after1"])
    ab.main(["--profile", "before2", "status", "--profile", "after2"])
    assert [c.profile for c in calls] == ["before1", "after1", "after2"]

# ---------------------------------------------------------------------------
# wait: validation happens before any session lookup

def test_wait_validation_errors_need_no_session(capsys: pytest.CaptureFixture[str]) -> None:
    code = ab.main(["wait", "--timeout", "200", "--text", "hi"])
    assert code == 2
    envelope = error_envelope(capsys.readouterr().err)
    assert envelope["class"] == ab.ERR_VALIDATION and envelope["hint"] == ab._hint("wait_cap")

    code = ab.main(["wait"])
    assert code == 2
    envelope = error_envelope(capsys.readouterr().err)
    assert envelope["class"] == ab.ERR_USAGE and envelope["hint"] == ab._hint("no_wait_condition")

# ---------------------------------------------------------------------------
# goto: the scheme guard fires before any daemon/session lookup

def test_goto_guarded_scheme_fires_before_any_session_lookup(capsys: pytest.CaptureFixture[str]) -> None:
    code = ab.main(["goto", "edge://settings"])
    assert code == 2
    envelope = error_envelope(capsys.readouterr().err)
    assert envelope["class"] == ab.ERR_GUARDED
    assert envelope["hint"] == ab._hint("guarded_scheme")
    assert not (ab._home() / "profiles").exists()  # no profile dir touched: nothing tried to reach a daemon

# ---------------------------------------------------------------------------
# status / stop against an empty home: always exit 0

def test_status_and_stop_on_an_empty_home_exit_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert ab.main(["status"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["running"] is False and status_payload["reason"] == "no_session"

    assert ab.main(["stop"]) == 0
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

    monkeypatch.setattr(ab, "_playwright_version", lambda: "1.61.0")
    monkeypatch.setattr(ab.shutil, "which", counted("which", None))
    monkeypatch.setattr(ab.subprocess, "run", counted("subprocess", _FakeCompleted()))
    monkeypatch.setattr(ab, "_registry_value", counted("registry", None))
    monkeypatch.setattr(ab, "_job_info", counted("job_info", {"in_job": False, "kill_on_job_close": False}))
    monkeypatch.setattr(ab, "_processes_using", lambda needle: [])

    code = ab.main(["doctor"])
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
    assert ab._demangle_msys(argv) == argv  # no MSYSTEM: unchanged

    monkeypatch.setenv("MSYSTEM", "MINGW64")
    monkeypatch.setenv("EXEPATH", r"C:\Program Files\Git\usr\bin")
    out = ab._demangle_msys(argv)
    assert out == ["--url-contains=/incident.do"]
    assert any("rewritten by Git Bash" in note for note in ab._ARGV_NOTES)

def test_demangle_msys_through_main_reaches_the_handler(monkeypatch: pytest.MonkeyPatch, capture_args: Callable[[str], list[Any]]) -> None:
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    monkeypatch.setenv("EXEPATH", r"C:\Program Files\Git\usr\bin")
    calls = capture_args("wait")
    ab.main(["wait", "--url-contains=C:/Program Files/Git/incident.do"])
    assert calls[0].url_contains == "/incident.do"

# ---------------------------------------------------------------------------
# UTF-8 stdout when stdout is a pipe (not a tty)

def test_main_prints_non_ascii_result_without_raising(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(ab, "_cmd_status", lambda args: {"title": "caf\u00e9 \u2713 \U0001F600"})
    code = ab.main(["status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "caf\u00e9" in out and "\u2713" in out
