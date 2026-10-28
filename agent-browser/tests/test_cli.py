"""CLI surface: agent_browser.main([...]) argument parsing and the exit-code/error contract.

No test here starts a daemon or opens a socket. Verbs that would normally need a running
browser (wait, goto) are exercised only up to the point where they fail argument validation
before ever looking for a session; status/stop are exercised for real against an empty
AGENT_BROWSER_HOME (both exit 0 whether or not a browser runs; an invalid --profile is exit 2);
doctor runs for real with its registry/PATH/subprocess touchpoints replaced by fakes.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Callable

import pytest

import agent_browser as ab
from agent_browser_core import cli as core_cli, common as core_common, daemon as core_daemon, session as core_session
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
    assert core_cli.main(["frobnicate"]) == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["contract_version"] == 1 and payload["error"]["class"] == "usage"

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
# status / stop against an empty home: exit 0 when no browser runs

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

def test_overflow_preserves_pending_operation_and_failed_write_metadata():
    command_id = "e" * 32
    pending = json.loads(core_cli._json_line({"command_id": command_id, "session_id": "manual", "executed": True,
                                             "pending": True, "status": "pending", "body": "😀" * 15000}))
    assert pending["pending"] and pending["executed"] and pending["command_id"] == command_id
    assert pending["session_id"] == "manual" and pending["status"] == "pending"
    details = {"command_id": command_id, "executed": True, "receipt": "state --command " + command_id}
    error = json.loads(core_cli._json_line({"error": {"class": "timeout", "message": "😀" * 15000, "details": details}}))
    assert error["error"]["details"] == details


# ---------------------------------------------------------------------------
# error.untrusted: the error fields that can quote the page

@pytest.mark.parametrize("details, declared, expected", [
    ({"candidates": [{"ref": "e1", "frame": "", "description": '- button "Save"'}], "executed": False}, None, ["details"]),
    ({"options": ["2 - High"], "command_id": "a" * 32}, None, ["details"]),
    ({"dialogs": [{"type": "confirm", "message": "Leave?", "handled": "dismissed"}]}, None, ["details"]),
    ({"log": ['- waiting for get_by_role("button")']}, ["message"], ["message", "details"]),
    (None, ["message"], ["message"]),
    (["not", "a", "mapping"], None, ["details"]),
    # Receipt pointers, byte counts, condition names and empty page keys are the tool's own: nothing to mark.
    ({"command_id": "a" * 32, "executed": True, "receipt": "state --command " + "a" * 32}, None, None),
    ({"evidence_bytes": 1, "quota": 2, "session_bytes": 1, "other_sessions_bytes": 0, "journal_bytes": 0}, None, None),
    ({"pending": ["text"], "expectations": [{"kind": "url", "passed": False}], "completed_fields": [0]}, None, None),
    ({"candidates": [], "log": [], "executed": False}, None, None),
    ({"exception": "TypeError"}, None, None),
])
def test_error_untrusted_names_the_fields_that_can_quote_the_page(details: Any, declared: Any, expected: Any) -> None:
    error = {"class": "x", "http_status": None, "message": "m", "hint": None, **({"details": details} if details is not None else {})}
    assert core_common._error_untrusted(error, declared).get("untrusted") == expected
    exc = core_common._tag(ValueError("m"), "x", details=details, untrusted=declared)
    assert core_cli._error_payload(exc)["error"].get("untrusted") == expected


def test_error_untrusted_keeps_the_marker_of_details_stored_by_output_id() -> None:
    stub = {"class": "ambiguous", "details": {"executed": False, "output_id": "b" * 32, "next_offset": 0}, "untrusted": ["details", "hint", {}]}
    assert core_common._error_untrusted(stub)["untrusted"] == ["details"]


def test_cli_error_overflow_stubs_keep_the_untrusted_marker(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from pathlib import Path
    candidates = [{"ref": f"e{i}", "frame": "", "description": '- button "' + "Save " * 700 + '"'} for i in range(10)]
    exc = core_common._tag(ValueError("Semantic target matched 10 visible elements."), core_common.ERR_AMBIGUOUS,
                           details={"candidates": candidates, "executed": False})
    monkeypatch.setattr(core_cli, "_cmd_status", _raising_handler(exc))
    assert core_cli.main(["status"]) == 2
    stub = json.loads(capsys.readouterr().err)
    assert stub["truncated"] and stub["error"]["details"] == {"executed": False}
    assert stub["error"]["untrusted"] == ["details"]
    stored = json.loads(Path(stub["output_file"]).read_text(encoding="utf-8"))
    assert stored["error"]["details"]["candidates"] == candidates and stored["error"]["untrusted"] == ["details"]
    # The last-resort stub, used when even the bounded one is over budget, keeps it too.
    minimal = json.loads(core_cli._json_line(core_cli._error_payload(exc), byte_budget=200))
    assert minimal["error"] == {"class": "oversized_error", "message": "Read full error details by output ID.", "untrusted": ["details"]}


@pytest.mark.parametrize("request_fails", [False, True])
def test_action_is_sent_once_without_preflight_health_requests(monkeypatch, request_fails):
    paths = core_common._paths("default")
    paths.lock.parent.mkdir(parents=True, exist_ok=True)
    paths.lock.touch()
    record = {"port": 1, "token": "t", "protocol_version": core_common.PROTOCOL_VERSION}
    monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
    monkeypatch.setattr(core_session, "_read_session", lambda p: record)
    calls = []
    def request(session, command, arguments, timeout, **kwargs):
        calls.append(command)
        if request_fails:
            raise core_common._tag(RuntimeError("Ambiguous timeout"), core_common.ERR_TIMEOUT)
        return {"ok": True}
    monkeypatch.setattr(core_session, "_request", request)
    args = argparse.Namespace(profile="default", session="caller", timeout=15, max_bytes=None)
    if request_fails:
        with pytest.raises(RuntimeError, match="Ambiguous timeout"):
            core_cli._remote(args, "click", "click", {"target": "e1"})
    else:
        assert core_cli._remote(args, "click", "click", {"target": "e1"})["ok"]
    assert calls == ["click"]


def test_main_prints_non_ascii_result_without_raising(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(core_cli, "_cmd_status", lambda args: {"title": "caf\u00e9 \u2713 \U0001F600"})
    code = core_cli.main(["status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "caf\u00e9" in out and "\u2713" in out


def test_tab_flag_accepts_before_or_after_verb_and_validates_spec(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []

    def fake_session_for(args: Any, verb: str, autostart: bool = False) -> tuple[Any, dict[str, Any]]:
        return core_common._paths("default"), {"port": 1, "token": "t", "profile": "default"}

    def fake_request(session: dict[str, Any], cmd: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        captured.append((cmd, dict(payload)))
        return {"ok": True, "tab": 2, "tab_id": "t2"}

    monkeypatch.setattr(core_cli, "_session_for", fake_session_for)
    monkeypatch.setattr(core_session, "_request", fake_request)

    assert core_cli.main(["click", "e5", "--tab", "t2"]) == 0
    assert captured[-1][0] == "click" and captured[-1][1]["tab"] == "t2"

    assert core_cli.main(["--tab", "2", "snapshot"]) == 0
    assert captured[-1][0] == "snapshot" and captured[-1][1]["tab"] == 2

    assert core_cli.main(["tab", "t2", "--close"]) == 0
    assert captured[-1][0] == "tab" and captured[-1][1]["index"] == "t2" and captured[-1][1]["close"] is True

    assert core_cli.main(["tab", "--tab", "t3"]) == 0
    assert captured[-1][0] == "tab" and captured[-1][1]["index"] == "t3"

    assert core_cli.main(["commit", "click", "e5", "--tab", "t2"]) == 0
    assert captured[-1][0] == "commit" and captured[-1][1]["operation"] == "click" and captured[-1][1]["tab"] == "t2"
    capsys.readouterr()

    assert core_cli.main(["click", "e5", "--tab", "0"]) == 2
    err = json.loads(capsys.readouterr().err)["error"]
    assert err["class"] == core_common.ERR_VALIDATION

    assert core_cli.main(["click", "e5", "--tab", "bogus"]) == 2
    err = json.loads(capsys.readouterr().err)["error"]
    assert err["class"] == core_common.ERR_VALIDATION

    assert core_cli.main(["goto", "https://example.com", "--new-tab", "--tab", "t1"]) == 2
    err = json.loads(capsys.readouterr().err)["error"]
    assert err["class"] == core_common.ERR_USAGE

    assert core_cli.main(["tab", "1", "--tab", "t2"]) == 2
    err = json.loads(capsys.readouterr().err)["error"]
    assert err["class"] == core_common.ERR_USAGE


# ---------------------------------------------------------------------------
# One error and storage envelope (remediation PR 4)

def _msys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    monkeypatch.setenv("EXEPATH", r"C:\Program Files\Git\usr\bin")


def test_unexpected_exception_becomes_a_json_envelope_not_a_traceback(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(core_cli, "_cmd_status", _raising_handler(KeyError("C:/secret/path")))
    assert core_cli.main(["status", "--profile", "work"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "secret" not in err and "Traceback" not in err
    envelope = error_envelope(err)
    assert envelope["class"] == core_common.ERR_DAEMON
    assert envelope["details"] == {"exception": "KeyError"}
    assert envelope["hint"] == core_common._hint("inspect_receipt", "work")


def test_parse_stage_failure_that_is_not_a_usage_error_is_an_envelope(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(core_cli, "_demangle_msys", _raising_handler(TypeError("C:/secret")))
    assert core_cli.main(["status"]) == 1
    err = capsys.readouterr().err
    assert "secret" not in err and error_envelope(err)["details"] == {"exception": "TypeError"}


def test_untagged_oserror_keeps_io_class_and_exit_1(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(core_cli, "_cmd_status", _raising_handler(OSError("C:/secret")))
    assert core_cli.main(["status"]) == 1
    err = capsys.readouterr().err
    envelope = error_envelope(err)
    assert envelope["class"] == "io" and envelope["message"] == "Local file or socket access failed." and "secret" not in err


def test_tagged_and_detailed_oserrors_keep_their_class_and_receipt(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    receipt = {"command_id": "d" * 32, "executed": True, "receipt": "state --command " + "d" * 32}
    tagged = core_common._tag(OSError("gone"), core_common.ERR_NOT_RUNNING, hint="h", details=receipt)
    monkeypatch.setattr(core_cli, "_cmd_status", _raising_handler(tagged))
    assert core_cli.main(["status"]) == 1
    envelope = error_envelope(capsys.readouterr().err)
    assert (envelope["class"], envelope["message"], envelope["hint"], envelope["details"]) == ("not_running", "gone", "h", receipt)
    detailed = OSError("C:/secret")  # how client.browser_request attaches a receipt to a socket failure
    detailed.details = receipt  # type: ignore[attr-defined]
    monkeypatch.setattr(core_cli, "_cmd_status", _raising_handler(detailed))
    assert core_cli.main(["status"]) == 1
    envelope = error_envelope(capsys.readouterr().err)
    assert envelope["class"] == "io" and envelope["details"] == receipt


@pytest.mark.parametrize("exc, code, cls", [(ValueError("plain"), 2, "validation"), (RuntimeError("plain"), 1, "daemon")])
def test_exit_code_mapping_is_unchanged_for_untagged_errors(exc: Exception, code: int, cls: str, monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(core_cli, "_cmd_status", _raising_handler(exc))
    assert core_cli.main(["status"]) == code
    envelope = error_envelope(capsys.readouterr().err)
    assert envelope["class"] == cls and envelope["message"] == "plain"


def test_interrupt_hint_points_at_the_receipt(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(core_cli, "_cmd_status", _raising_handler(KeyboardInterrupt()))
    assert core_cli.main(["status", "--profile", "work"]) == 130
    assert error_envelope(capsys.readouterr().err)["hint"] == core_common._hint("inspect_receipt", "work")


def test_error_payload_shape_is_shared_by_cli_and_mcp(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    exc = core_common._tag(ValueError("stale"), core_common.ERR_STALE_REF, hint="Run: snapshot", details={"executed": False})
    monkeypatch.setattr(core_cli, "_cmd_status", _raising_handler(exc))
    assert core_cli.main(["status"]) == 2
    printed = json.loads(capsys.readouterr().err)
    assert {k: v for k, v in printed.items() if k != "contract_version"} == core_cli._error_payload(exc)


def test_hints_never_name_an_invalid_profile(capsys: pytest.CaptureFixture[str]) -> None:
    assert core_common._hint("not_running", "x; rm -rf ~") == core_common._hint("not_running", core_common.DEFAULT_PROFILE)
    assert core_common._hint("not_running", None) == core_common._hint("not_running")
    assert core_cli.main(["click", "e5", "--tab", "0", "--profile", "evil$(calc)"]) == 2
    envelope = error_envelope(capsys.readouterr().err)
    assert "evil" not in envelope["hint"] and "--profile default" in envelope["hint"]
    hint = core_cli._normalize_exception(KeyError("x"), "../evil").hint  # type: ignore[attr-defined]
    assert "evil" not in hint and "--profile default" in hint


def _storage_full(*args: Any, **kwargs: Any) -> None:
    raise core_common._tag(RuntimeError("full"), "journal_full")


def test_unstorable_result_keeps_command_id_executed_and_receipt(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    command_id = "e" * 32
    monkeypatch.setattr(core_cli, "_cmd_status", lambda args: {"command_id": command_id, "executed": True, "body": "x" * 30000})
    monkeypatch.setattr(core_cli.evidence, "require_space", _storage_full)
    assert core_cli.main(["status", "--profile", "work"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    envelope = error_envelope(err)
    assert envelope["class"] == "journal_full" and envelope["hint"] == core_common._hint("inspect_receipt", "work")
    assert envelope["details"] == {"command_id": command_id, "executed": True, "receipt": "state --command " + command_id}


def test_unstorable_result_without_command_id_points_at_the_journal(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(core_cli, "_cmd_status", lambda args: {"executed": "yes", "body": "x" * 30000})
    monkeypatch.setattr(core_cli.evidence, "require_space", _raising_handler(OSError("C:/secret")))
    assert core_cli.main(["status"]) == 1
    err = capsys.readouterr().err
    envelope = error_envelope(err)
    assert envelope["class"] == "output_storage" and "secret" not in err
    assert envelope["details"] == {"command_id": None, "executed": None, "receipt": "journal"}


def test_storage_failure_class_is_quota_or_storage_never_the_inner_class(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(core_cli, "_cmd_status", lambda args: {"body": "x" * 30000})
    monkeypatch.setattr(core_cli.evidence, "require_space", _raising_handler(core_common._tag(ValueError("bad"), core_common.ERR_VALIDATION)))
    assert core_cli.main(["status"]) == 1
    assert error_envelope(capsys.readouterr().err)["class"] == "output_storage"


def test_a_closed_stdout_is_not_reported_as_a_storage_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    class ClosedPipe:
        def write(self, text: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self) -> None:
            raise BrokenPipeError(32, "Broken pipe")

    command_id = "c" * 32
    monkeypatch.setattr(core_cli, "_cmd_status", lambda args: {"command_id": command_id, "executed": True})
    monkeypatch.setattr(core_cli.sys, "stdout", ClosedPipe())
    assert core_cli.main(["status"]) == 1
    envelope = error_envelope(capsys.readouterr().err)
    assert envelope["class"] == "io" and "stdout" in envelope["message"]
    assert envelope["details"] == {"command_id": command_id, "executed": True, "receipt": "state --command " + command_id}


def test_emit_error_fallback_keeps_details_and_full_envelope_shape(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    command_id = "a" * 32
    details = {"command_id": command_id, "executed": True, "receipt": "state --command " + command_id}
    monkeypatch.setattr(core_cli, "_json_line", _raising_handler(OSError("disk")))
    core_cli._emit_error(core_common._tag(RuntimeError("x"), core_common.ERR_TIMEOUT, details=details), "work")
    envelope = error_envelope(capsys.readouterr().err)
    assert envelope["class"] == "output_storage" and envelope["details"] == details
    assert envelope["hint"] == core_common._hint("inspect_receipt", "work")


def test_preservation_details_tolerate_any_source() -> None:
    journal = {"command_id": None, "executed": None, "receipt": "journal"}
    assert core_common._preservation_details(["not", "a", "dict"]) == journal
    assert core_common._preservation_details({"command_id": "../x", "executed": 1}) == journal
    assert core_common._preservation_details({"error": {"details": ["x"]}}) == journal
    found = core_common._preservation_details({"error": {"details": {"command_id": "f" * 32, "executed": False}}})
    assert found == {"command_id": "f" * 32, "executed": False, "receipt": "state --command " + "f" * 32}


def test_msys_note_reaches_local_verbs_and_is_not_duplicated(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _msys(monkeypatch)
    monkeypatch.setattr(core_cli, "_cmd_status", lambda args: {"ok": True})
    assert core_cli.main(["status", "--profile", "C:/Program Files/Git/p"]) == 0
    assert json.loads(capsys.readouterr().out)["note"].count("rewritten by Git Bash") == 1
    monkeypatch.setattr(core_cli, "_cmd_status", lambda args: {"note": "x"})
    assert core_cli.main(["status", "--profile", "C:/Program Files/Git/p"]) == 0
    note = json.loads(capsys.readouterr().out)["note"]
    assert note.startswith("x argument ") and note.count("rewritten by Git Bash") == 1


def test_msys_note_is_included_in_error_envelopes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _msys(monkeypatch)
    assert core_cli.main(["status", "--profile", "C:/Program Files/Git/p"]) == 2  # rewritten to '/p': not a profile name
    envelope = error_envelope(capsys.readouterr().err)
    assert envelope["class"] == core_common.ERR_VALIDATION and "rewritten by Git Bash" in envelope["note"]


def test_remote_result_note_is_not_duplicated_by_main(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _msys(monkeypatch)
    session = {"port": 1, "token": "t", "profile": "default"}
    monkeypatch.setattr(core_cli, "_session_for", lambda args, verb, autostart=False: (core_common._paths("default"), session))
    monkeypatch.setattr(core_session, "_request", lambda session, cmd, payload, timeout: {"ok": True})
    assert core_cli.main(["click", "C:/Program Files/Git/x"]) == 0
    assert json.loads(capsys.readouterr().out)["note"].count("rewritten by Git Bash") == 1


def test_msys_notes_are_capped_and_survive_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    _msys(monkeypatch)
    core_cli._demangle_msys(["--url-contains=C:/Program Files/Git/" + "a" * 2000])
    assert len(core_common._ARGV_NOTES[0]) == core_common.NOTE_CAP and core_common._ARGV_NOTES[0].endswith("...")
    bounded = json.loads(core_cli._json_line({"note": "kept", "body": "x" * 30000}))
    assert bounded["truncated"] and bounded["note"] == "kept"
    error = json.loads(core_cli._json_line({"error": {"class": "timeout", "message": "x" * 30000, "note": "kept"}}))
    assert error["error"]["note"] == "kept"



# ---------------------------------------------------------------------------
# Parser and help (remediation PR 5)

def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices


def _option(parser: argparse.ArgumentParser, flag: str) -> argparse.Action:
    return next(a for a in parser._actions if flag in a.option_strings)


def _fake_remote_transport(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any], float]]:
    sent: list[tuple[str, dict[str, Any], float]] = []
    monkeypatch.setattr(core_cli, "_session_for", lambda args, verb, autostart=False: (core_common._paths("default"), {"port": 1, "token": "t", "profile": "default"}))
    monkeypatch.setattr(core_session, "_request", lambda session, cmd, payload, timeout: sent.append((cmd, dict(payload), timeout)) or {"ok": True})
    return sent


@pytest.mark.parametrize("argv, expected", [
    (["--expect", "A", "click", "e1", "--expect", "B"], ["A", "B"]),
    (["--expect", "A", "click", "e1"], ["A"]),
    (["click", "e1", "--expect", "B"], ["B"]),
    (["--expect", "A", "--expect", "B", "click", "e1", "--expect", "C", "--expect", "D"], ["A", "B", "C", "D"]),
    (["click", "e1"], []),
])
def test_expect_groups_before_and_after_the_verb_are_merged(argv: list[str], expected: list[str]) -> None:
    parsed = core_cli._build_parser().parse_args(argv)
    assert parsed.expect == expected
    assert not hasattr(parsed, "expect_tail")


def test_expect_reaches_the_daemon_payload_for_a_plain_verb(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    sent = _fake_remote_transport(monkeypatch)
    assert core_cli.main(["--expect", "text:A", "click", "e1", "--expect", "gone:e1"]) == 0
    assert sent[-1][0] == "click" and sent[-1][1]["expect"] == ["text:A", "gone:e1"]
    assert core_cli.main(["--expect", "text:A", "api", "https://example.com/x", "--expect", "response:GET **:2xx"]) == 0
    assert sent[-1][0] == "api" and sent[-1][1]["expect"] == ["text:A", "response:GET **:2xx"]
    capsys.readouterr()


def test_commit_merges_expect_groups_from_commit_level_and_inner_verb(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    sent = _fake_remote_transport(monkeypatch)
    assert core_cli.main(["--expect", "A", "commit", "--expect", "B", "click", "e1", "--expect", "C"]) == 0
    cmd, payload, _ = sent[-1]
    assert cmd == "commit" and payload["operation"] == "click" and payload["expect"] == ["A", "B", "C"]
    assert core_cli.main(["commit", "--expect", "A", "--expect", "A", "click", "--expect", "B", "e1"]) == 0
    assert sent[-1][1]["expect"] == ["A", "A", "B"]  # ordered, never deduplicated
    capsys.readouterr()


def test_every_expect_flag_reads_check_and_has_help() -> None:
    parser = core_cli._build_parser()
    flags = [_option(parser, "--expect")] + [_option(sp, "--expect") for name, sp in _subparsers(parser).items() if name != "serve"]
    assert len(flags) > 30
    for action in flags:
        assert action.metavar == "CHECK" and "repeatable" in action.help


def test_help_epilog_does_not_tell_agents_to_blindly_repeat_after_124() -> None:
    epilog = core_cli._EPILOG.lower()
    assert "run the same command again" not in epilog
    assert "may already have run" in epilog and "only a timed-out wait" in epilog


def test_status_exits_2_for_reserved_or_invalid_profile_and_help_says_so(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (["status", "--profile", "_doctor"], ["status", "--profile", "bad name"]):
        assert core_cli.main(argv) == 2
        assert error_envelope(capsys.readouterr().err)["class"] == core_common.ERR_VALIDATION
    monkeypatch.setenv("AGENT_BROWSER_PROFILE", "_doctor")
    assert core_cli.main(["status"]) == 2
    capsys.readouterr()
    description = _subparsers(core_cli._build_parser())["status"].description
    assert "always exit 0" not in description and "invalid" in description


def test_stop_all_stops_only_the_resolved_profile(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    for name in ("a", "b"):
        core_common._paths(name).dir.mkdir(parents=True)
    stopped: list[tuple[str, bool]] = []
    monkeypatch.setattr(core_session, "stop_session", lambda paths, force=False: stopped.append((paths.profile, force)) or {"profile": paths.profile})
    assert core_cli.main(["stop", "--all", "--profile", "a"]) == 0
    assert stopped == [("a", False)]
    assert core_cli.main(["status", "--all"]) == 0
    capsys.readouterr()
    payload = core_cli._cmd_status(argparse.Namespace(all=True, profile=None))
    assert [p["profile"] for p in payload["profiles"]] == ["a", "b"]


def test_all_flags_have_distinguishing_help() -> None:
    subs = _subparsers(core_cli._build_parser())
    stop_help, status_help = _option(subs["stop"], "--all").help, _option(subs["status"], "--all").help
    assert "this profile only" in stop_help and "other profiles keep running" in stop_help
    assert "every profile" in status_help


def test_timeout_help_lists_every_default_and_only_wait_is_capped() -> None:
    parser = core_cli._build_parser()
    for action in (_option(parser, "--timeout"), _option(_subparsers(parser)["click"], "--timeout")):
        text = action.help
        assert "%" not in text  # argparse %-formats help strings
        for value in (core_common.DEFAULT_TIMEOUT, core_common.GOTO_TIMEOUT, core_common.START_TIMEOUT, core_common.STOP_GRACE,
                      core_common.DOWNLOADS_TIMEOUT, core_common.WAIT_DEFAULT, core_common.WAIT_CAP, core_common.MAX_TIMEOUT):
            assert f"{value:g}" in text
        assert "back" in text and "reload" in text and "only wait is capped" in text.lower()
        assert "max 100)" not in text
    wait = _subparsers(parser)["wait"].description
    assert f"default {core_common.WAIT_DEFAULT:g} s" in wait and f"max {core_common.WAIT_CAP:g} s" in wait


def test_only_wait_is_capped_and_the_limit_is_shared_with_the_daemon(capsys: pytest.CaptureFixture[str]) -> None:
    assert core_cli._timeout_for(argparse.Namespace(timeout=3600.0), core_common.DEFAULT_TIMEOUT) == 3600.0
    assert core_cli._timeout_for(argparse.Namespace(timeout=core_common.MAX_TIMEOUT), core_common.DEFAULT_TIMEOUT) == core_common.MAX_TIMEOUT
    for bad in (core_common.MAX_TIMEOUT + 1, float("nan"), -1.0):
        with pytest.raises(ValueError) as exc:
            core_cli._timeout_for(argparse.Namespace(timeout=bad), core_common.DEFAULT_TIMEOUT)
        assert exc.value.error_class == core_common.ERR_VALIDATION
    assert core_cli.main(["wait", "--text", "x", "--timeout", f"{core_common.WAIT_CAP + 1:g}"]) == 2
    assert error_envelope(capsys.readouterr().err)["hint"] == core_common._hint("wait_cap")


def test_downloads_uses_the_documented_default_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []
    monkeypatch.setattr(core_cli, "_remote", lambda args, verb, cmd, payload, default_timeout=core_common.DEFAULT_TIMEOUT, autostart=False: seen.append(default_timeout) or {})
    core_cli._cmd_downloads(argparse.Namespace())
    assert seen == [core_common.DOWNLOADS_TIMEOUT]


# ---------------------------------------------------------------------------
# dialog answers and stop


def _sent_to_daemon(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    captured: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(core_cli, "_session_for", lambda args, verb, autostart=False: (core_common._paths("default"), {"port": 1, "token": "t", "profile": "default"}))
    monkeypatch.setattr(core_session, "_request", lambda session, cmd, payload, timeout: captured.append((cmd, dict(payload))) or {"ok": True})
    return captured


@pytest.mark.parametrize("argv", [
    ["click", "e1"], ["fill", "e1", "x"], ["type", "e1", "x"], ["press", "Enter"], ["select", "e1", "High"], ["check", "e1"],
    ["uncheck", "e1"], ["hover", "e1"], ["scroll", "e1"], ["upload", "e1", "note.txt"], ["eval", "1"], ["fill-form", "--file", "form.json"],
    ["commit", "press", "Enter", "e1"],
])
def test_dialog_flags_reach_the_daemon_from_every_action_verb(argv: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "note.txt").write_text("note", encoding="utf-8")
    (tmp_path / "form.json").write_text('[{"target": "e1", "value": "x"}]', encoding="utf-8")
    captured = _sent_to_daemon(monkeypatch)
    assert core_cli.main([*argv, "--accept-dialog", "--dialog-text", "ok", "--discard-changes"]) == 0, capsys.readouterr().err
    cmd, payload = captured[-1]
    assert cmd == ("commit" if argv[0] == "commit" else argv[0])
    assert (payload["accept_dialog"], payload["dialog_text"], payload["discard_changes"]) == (True, "ok", True)


def test_fill_enter_with_discard_changes_reaches_the_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _sent_to_daemon(monkeypatch)
    assert core_cli.main(["fill", "e1", "draft", "--enter", "--discard-changes"]) == 0
    cmd, payload = captured[-1]
    assert cmd == "fill" and payload["enter"] is True and payload["discard_changes"] is True
    assert payload["accept_dialog"] is False and payload["dialog_text"] is None


def test_stop_sends_discard_changes_for_this_session_only(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _sent_to_daemon(monkeypatch)
    monkeypatch.setattr(core_session, "_liveness", lambda paths: ("running", {"pid": 1}))
    for argv, discard in ((["stop"], False), (["stop", "--discard-changes"], True)):
        assert core_cli.main(argv) == 0
        assert captured[-1][0] == "stop" and captured[-1][1]["discard_changes"] is discard


@pytest.mark.parametrize("argv", [
    ["fill", "e1", "x", "--dialog-text", "y"],                                   # a prompt answer without accepting would be dropped
    ["stop", "--all", "--discard-changes"], ["stop", "--force", "--discard-changes"],  # those already discard everything
    # An abbreviated flag would slip past the caller's ask rules (*--accept-dialog*, *--all*, *--clear*).
    ["click", "e1", "--accept"], ["fill", "e1", "x", "--discard"], ["goto", "https://x/", "--discard-ch"], ["stop", "--al"],
    ["stop", "--forc"], ["journal", "--cl"],
])
def test_unsafe_dialog_and_stop_flag_forms_are_usage_errors(argv: list[str], monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    captured = _sent_to_daemon(monkeypatch)
    stopped: list[bool] = []
    monkeypatch.setattr(core_session, "stop_session", lambda paths, force=False: stopped.append(force) or {})
    assert core_cli.main(argv) == 2
    assert json.loads(capsys.readouterr().err)["error"]["class"] == core_common.ERR_USAGE
    assert captured == [] and stopped == []
