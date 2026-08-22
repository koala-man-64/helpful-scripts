"""CLI surface: ep.main([...]) over the fake Edge plus the REPL loop and the error envelope."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any, Callable

import pytest

import edge_pyodide as ep
from conftest import FakeEdgeEnv, FakePage


class FakeClock:
    def __init__(self, start: float = 1000.0, step: float = 0.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def _calls(page: FakePage, name: str) -> list[dict[str, Any]]:
    return [arg for called, arg in page.calls if called == name]


def _error_envelope(err: str) -> dict[str, Any]:
    payload = json.loads(err)
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"class", "http_status", "message", "hint"}
    return payload["error"]


def _hang_sandbox_call(page: FakePage, name: str) -> None:
    original = page.respond
    prefix = f"window.edgepy.call({json.dumps(name)}"

    def respond(message: dict[str, Any]) -> list[dict[str, Any]]:
        params = message.get("params") or {}
        if message["method"] == "Runtime.evaluate" and str(params.get("expression", "")).startswith(prefix):
            page.methods.append(message["method"])
            return []
        return original(message)

    page.respond = respond  # type: ignore[method-assign]


@pytest.fixture
def cli(runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, vendor_dir: Path,
        monkeypatch: pytest.MonkeyPatch) -> list[ep.EdgeRuntime]:
    """CLI harness: fake Edge, synthetic vendor dir via env, empty piped stdin, runtimes recorded."""
    # _cmd_run passes vendor_dir=None explicitly, so the env var (not the factory default) must
    # steer the runtime away from any real <script dir>/vendor folder.
    monkeypatch.setenv("EDGEPY_VENDOR_DIR", str(vendor_dir))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    created: list[ep.EdgeRuntime] = []

    def recording_factory(**kwargs: Any) -> ep.EdgeRuntime:
        rt = runtime_factory(**kwargs)
        created.append(rt)
        return rt

    monkeypatch.setattr(ep, "_runtime_factory", recording_factory)
    return created


@pytest.fixture
def script(tmp_path: Path) -> Path:
    folder = tmp_path / "proj"
    folder.mkdir()
    path = folder / "s.py"
    path.write_text("print('script')\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# run: exit codes and validation


def test_run_code_returns_zero_and_passes_output_through(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    page = fake_edge.page
    page.sandbox["run"] = lambda arg: (page.emit("o", "1\n"), {"exit_code": 0, "traceback": None})[1]
    assert ep.main(["run", "-c", "print(1)"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "1\n"
    assert captured.err == ""
    (arg,) = _calls(page, "run")
    assert arg["kind"] == "code" and arg["code"] == "print(1)" and arg["filename"] == "<string>"
    assert cli[0].stdin_mode == "prompt"  # auto: on-demand line reads, never a blocking pre-read


def test_run_propagates_the_script_exit_code(cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv) -> None:
    fake_edge.page.sandbox["run"] = lambda arg: {"exit_code": 5, "traceback": None}
    assert ep.main(["run", "-c", "import sys; sys.exit(5)"]) == 5


def test_run_script_stderr_passes_through_untouched(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    page = fake_edge.page
    page.sandbox["run"] = lambda arg: (page.emit("e", "Traceback: boom\n"), {"exit_code": 1, "traceback": "Traceback: boom\n"})[1]
    assert ep.main(["run", "-c", "raise ValueError"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "Traceback: boom\n"
    assert not captured.err.startswith('{"error"')


def test_run_without_a_target_is_a_validation_error(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ep.main(["run"]) == 2
    err = _error_envelope(capsys.readouterr().err)
    assert err["class"] == ep.ERR_VALIDATION
    assert "Nothing to run" in err["message"]
    assert cli == [], "validation happens before Edge is launched"
    assert fake_edge.process is None


def test_run_module_and_code_together_is_rejected(
    cli: list[ep.EdgeRuntime], capsys: pytest.CaptureFixture[str]
) -> None:
    assert ep.main(["run", "-m", "x", "-c", "y"]) == 2
    err = _error_envelope(capsys.readouterr().err)
    assert err["class"] == ep.ERR_VALIDATION
    assert "-m" in err["message"] and "-c" in err["message"]
    assert cli == []


def test_run_missing_script_path_is_rejected(
    cli: list[ep.EdgeRuntime], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.py"
    assert ep.main(["run", str(missing)]) == 2
    err = _error_envelope(capsys.readouterr().err)
    assert err["class"] == ep.ERR_VALIDATION
    assert "missing.py" in err["message"]
    assert cli == []


def test_run_unknown_package_is_an_environment_error(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ep.main(["run", "--pkg", "nosuchpkg", "-c", "pass"]) == 1
    err = _error_envelope(capsys.readouterr().err)
    assert err["class"] == ep.ERR_PACKAGE_MISSING
    assert "nosuchpkg" in err["message"]
    assert "fetch --pkg nosuchpkg" in err["hint"]
    assert _calls(fake_edge.page, "run") == []
    assert fake_edge.browser_ws is not None and any(m["method"] == "Browser.close" for m in fake_edge.browser_ws.sent)


def test_runtime_errors_from_the_sandbox_exit_one_with_their_class(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    def run(arg: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("dispatch exploded")

    fake_edge.page.sandbox["run"] = run
    assert ep.main(["run", "-c", "pass"]) == 1
    err = _error_envelope(capsys.readouterr().err)
    assert err["class"] == ep.ERR_SANDBOX
    assert "dispatch exploded" in err["message"]


# ---------------------------------------------------------------------------
# run: argv, mounts, stdin


def test_positional_args_after_the_script_become_argv(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, script: Path
) -> None:
    assert ep.main(["run", str(script), "a", "b"]) == 0
    (arg,) = _calls(fake_edge.page, "run")
    assert arg["kind"] == "file"
    assert arg["path"] == "/mnt/proj/s.py"
    assert arg["cwd"] == "/mnt/proj"
    assert arg["argv"] == ["a", "b"]
    assert [m["dest"] for m in _calls(fake_edge.page, "mount")] == ["/mnt/proj"]


def test_double_dash_lets_option_like_args_reach_the_script(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, script: Path
) -> None:
    assert ep.main(["run", str(script), "--", "--flag", "value"]) == 0
    (arg,) = _calls(fake_edge.page, "run")
    assert arg["argv"] == ["--flag", "value"]


def test_code_target_args_become_argv(cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv) -> None:
    assert ep.main(["run", "-c", "import sys", "x", "y"]) == 0
    (arg,) = _calls(fake_edge.page, "run")
    assert arg["argv"] == ["x", "y"]


def test_no_mount_runs_the_script_source_from_tmp(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, script: Path
) -> None:
    assert ep.main(["run", "--no-mount", str(script)]) == 0
    assert _calls(fake_edge.page, "mount") == []
    (arg,) = _calls(fake_edge.page, "run")
    assert arg["kind"] == "code" and arg["filename"] == "/tmp/s.py"


def test_module_run_mounts_cwd_by_default(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    (cwd / "pkg").mkdir()
    (cwd / "pkg" / "mod.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(cwd)
    assert ep.main(["run", "-m", "pkg.mod", "--", "-v"]) == 0
    assert [m["dest"] for m in _calls(fake_edge.page, "mount")] == ["/mnt/workdir"]
    (arg,) = _calls(fake_edge.page, "run")
    assert arg == {"kind": "module", "module": "pkg.mod", "argv": ["-v"], "cwd": None, "source": "import pkg"}


def test_module_run_with_no_mount_skips_cwd(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert ep.main(["run", "--no-mount", "-m", "json.tool"]) == 0
    assert _calls(fake_edge.page, "mount") == []
    (arg,) = _calls(fake_edge.page, "run")
    assert arg["module"] == "json.tool"


def test_invalid_module_name_is_a_validation_error(
    cli: list[ep.EdgeRuntime], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert ep.main(["run", "--no-mount", "-m", "bad name"]) == 2
    assert _error_envelope(capsys.readouterr().err)["class"] == ep.ERR_VALIDATION


def test_explicit_mount_with_name(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path
) -> None:
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "__init__.py").write_text("", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    assert ep.main(["run", "--mount", f"{lib}=mylib", "--mount", str(data), "-c", "import mylib"]) == 0
    assert [m["dest"] for m in _calls(fake_edge.page, "mount")] == ["/mnt/mylib", "/mnt/data"]


def test_bad_mount_directory_is_rejected_before_launch(
    cli: list[ep.EdgeRuntime], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ep.main(["run", "--mount", str(tmp_path / "absent"), "-c", "pass"]) == 2
    err = _error_envelope(capsys.readouterr().err)
    assert err["class"] == ep.ERR_VALIDATION
    assert "absent" in err["message"]
    assert cli == []


def test_dash_target_reads_code_from_stdin_with_stdin_mode_none(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("print(7)\n"))
    assert ep.main(["run", "-", "argA"]) == 0
    (arg,) = _calls(fake_edge.page, "run")
    assert arg["kind"] == "code"
    assert arg["code"] == "print(7)\n"
    assert arg["filename"] == "<stdin>"
    assert arg["argv"] == ["argA"]
    assert cli[0].stdin_mode == "none"
    assert fake_edge.page.stdin_lines == [], "stdin was consumed for code, never fed to input()"


def test_stdin_lines_mode_pre_reads_the_pipe(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("a\nb\n"))
    assert ep.main(["run", "--stdin", "lines", "-c", "print(input())"]) == 0
    assert cli[0].stdin_mode == "lines"
    assert fake_edge.page.stdin_lines == ["a\n", "b\n"]
    assert "window.edgepy.setStdinLines" in "".join(
        m["params"]["expression"] for m in (fake_edge.page_ws.sent if fake_edge.page_ws else []) if m["method"] == "Runtime.evaluate"
    )


def test_stdin_none_flag_skips_the_stdin_bridge(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("unused\n"))
    assert ep.main(["run", "--stdin", "none", "-c", "pass"]) == 0
    assert cli[0].stdin_mode == "none"
    assert fake_edge.page.stdin_lines == []
    assert fake_edge.page_ws is not None
    assert not any("setStdinLines" in (m.get("params") or {}).get("expression", "") for m in fake_edge.page_ws.sent)


# ---------------------------------------------------------------------------
# run: --json, --timeout, --pkg, --show


def test_json_flag_prints_one_envelope_and_suppresses_passthrough(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    page = fake_edge.page

    def run(arg: dict[str, Any]) -> dict[str, Any]:
        page.emit("o", "hello\n")
        page.emit("e", "warn\n")
        return {"exit_code": 4, "traceback": None}

    page.sandbox["run"] = run
    assert ep.main(["run", "--json", "--pkg", "numpy", "-c", "print('hello')"]) == 4
    captured = capsys.readouterr()
    assert captured.err == ""
    envelope = json.loads(captured.out)  # a single JSON object, nothing else on stdout
    assert set(envelope) == {"exit_code", "stdout", "stderr", "truncated", "duration_s", "packages_loaded",
                             "pyodide_version", "edge_version"}
    assert envelope["exit_code"] == 4
    assert envelope["stdout"] == "hello\n"
    assert envelope["stderr"] == "warn\n"
    assert envelope["truncated"] is False
    assert envelope["packages_loaded"] == ["numpy"]
    assert envelope["pyodide_version"] == "314.0.5"
    assert envelope["edge_version"] == "Edg/151.0.4129.101"
    assert isinstance(envelope["duration_s"], float)


def test_timeout_flag_exits_124_with_timeout_class(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _hang_sandbox_call(fake_edge.page, "run")
    monkeypatch.setattr(ep, "_now", FakeClock(step=0.005))
    assert ep.main(["run", "--timeout", "0.01", "-c", "while True: pass"]) == ep.EXIT_TIMEOUT
    err = _error_envelope(capsys.readouterr().err)
    assert err["class"] == ep.ERR_TIMEOUT
    assert "0.01s" in err["message"]
    assert cli[0].timeout == 0.01
    methods = fake_edge.page.methods
    assert methods.index("Runtime.terminateExecution") < methods.index("Browser.close")


def test_timeout_env_var_is_the_default(
    cli: list[ep.EdgeRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGEPY_TIMEOUT_SECONDS", "2.5")
    assert ep.main(["run", "-c", "pass"]) == 0
    assert cli[0].timeout == 2.5


def test_invalid_timeout_env_var_is_a_config_error(
    cli: list[ep.EdgeRuntime], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EDGEPY_TIMEOUT_SECONDS", "soon")
    assert ep.main(["run", "-c", "pass"]) == 1
    assert _error_envelope(capsys.readouterr().err)["class"] == ep.ERR_CONFIG
    assert cli == []


def test_pkg_flags_load_bundled_then_install_wheels(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv
) -> None:
    assert ep.main(["run", "--pkg", "numpy", "--pkg", "tabulate", "-c", "pass"]) == 0
    names = [name for name, _ in fake_edge.page.calls if name in ("load", "install")]
    assert names == ["load", "install"]
    assert _calls(fake_edge.page, "load") == [{"names": ["numpy"], "options": {}}]
    (install_arg,) = _calls(fake_edge.page, "install")
    assert install_arg["names"] == ["tabulate"]


def test_show_and_devtools_flags_reach_edge_argv(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv
) -> None:
    assert ep.main(["run", "--show", "--window-size", "640,480", "-c", "pass"]) == 0
    assert fake_edge.launch_argv is not None
    assert "--headless" not in fake_edge.launch_argv
    assert "--window-size=640,480" in fake_edge.launch_argv
    assert cli[0].headless is False


def test_edgepy_show_env_disables_headless(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDGEPY_SHOW", "true")
    assert ep.main(["run", "-c", "pass"]) == 0
    assert fake_edge.launch_argv is not None and "--headless" not in fake_edge.launch_argv


def test_headless_is_the_default(cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv) -> None:
    assert ep.main(["run", "-c", "pass"]) == 0
    assert fake_edge.launch_argv is not None and "--headless" in fake_edge.launch_argv


def test_keyboard_interrupt_from_the_runtime_exits_130(
    cli: list[ep.EdgeRuntime], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class InterruptedRuntime:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def __enter__(self) -> "InterruptedRuntime":
            raise KeyboardInterrupt

        def __exit__(self, *exc: Any) -> None:
            return None

    monkeypatch.setattr(ep, "_runtime_factory", InterruptedRuntime)
    assert ep.main(["run", "-c", "pass"]) == ep.EXIT_INTERRUPT
    assert "interrupted" in capsys.readouterr().err


def test_keyboard_interrupt_during_the_run_exits_130_and_still_closes_edge(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    def run(arg: dict[str, Any]) -> dict[str, Any]:
        raise KeyboardInterrupt

    # FakePage.respond only catches Exception, so KeyboardInterrupt escapes the page like a real Ctrl-C.
    fake_edge.page.sandbox["run"] = run
    assert ep.main(["run", "-c", "pass"]) == ep.EXIT_INTERRUPT
    assert fake_edge.browser_ws is not None
    assert any(m["method"] == "Browser.close" for m in fake_edge.browser_ws.sent)
    assert "interrupted" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# repl


def _scripted_reader(lines: list[Any], prompts: list[str]) -> Callable[[str], str]:
    queue = list(lines)

    def read_line(prompt: str) -> str:
        prompts.append(prompt)
        if not queue:
            raise EOFError
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return str(item)

    return read_line


def test_repl_loop_prompts_and_reports_errors(
    cli: list[ep.EdgeRuntime], runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv,
    capsys: pytest.CaptureFixture[str],
) -> None:
    replies = iter([
        {"status": "incomplete", "error": None, "exit_code": None},
        {"status": "incomplete", "error": None, "exit_code": None},
        {"status": "complete", "error": None, "exit_code": None},
        {"status": "syntax-error", "error": "SyntaxError: invalid syntax", "exit_code": None},
        {"status": "complete", "error": "ZeroDivisionError: division by zero\n", "exit_code": None},
    ])
    fake_edge.page.sandbox["repl_push"] = lambda arg: next(replies)
    prompts: list[str] = []
    reader = _scripted_reader(["if True:\n", "    x = 1\n", "\n", "bad(\n", "1/0\n"], prompts)
    with runtime_factory() as rt:
        assert ep.repl_loop(rt, read_line=reader) == 0
    assert prompts == [">>> ", "... ", "... ", ">>> ", ">>> ", ">>> "]
    assert [arg["line"] for arg in _calls(fake_edge.page, "repl_push")] == ["if True:", "    x = 1", "", "bad(", "1/0"]
    captured = capsys.readouterr()
    assert "SyntaxError: invalid syntax\n" in captured.err
    assert "ZeroDivisionError: division by zero\n" in captured.err
    assert captured.err.count("ZeroDivisionError") == 1
    assert "edgepy REPL" in captured.err
    assert captured.out.endswith("\n"), "EOF prints a newline before returning"


def test_repl_loop_exit_code_from_the_sandbox_ends_the_loop(
    cli: list[ep.EdgeRuntime], runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv
) -> None:
    fake_edge.page.sandbox["repl_push"] = lambda arg: {"status": "complete", "error": None, "exit_code": 7}
    prompts: list[str] = []
    reader = _scripted_reader(["exit(7)\n", "never reached\n"], prompts)
    with runtime_factory() as rt:
        assert ep.repl_loop(rt, read_line=reader) == 7
    assert prompts == [">>> "]
    assert len(_calls(fake_edge.page, "repl_push")) == 1


def test_repl_loop_keyboard_interrupt_at_the_prompt_continues(
    cli: list[ep.EdgeRuntime], runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv,
    capsys: pytest.CaptureFixture[str],
) -> None:
    replies = iter([
        {"status": "incomplete", "error": None, "exit_code": None},
        {"status": "complete", "error": None, "exit_code": None},
    ])
    fake_edge.page.sandbox["repl_push"] = lambda arg: next(replies)
    prompts: list[str] = []
    reader = _scripted_reader(["for i in x:\n", KeyboardInterrupt(), "y = 2\n"], prompts)
    with runtime_factory() as rt:
        assert ep.repl_loop(rt, read_line=reader) == 0
    assert prompts == [">>> ", "... ", ">>> ", ">>> "], "Ctrl-C resets a continuation prompt to '>>> '"
    assert "KeyboardInterrupt" in capsys.readouterr().out
    assert [arg["line"] for arg in _calls(fake_edge.page, "repl_push")] == ["for i in x:", "y = 2"]


def test_repl_loop_keyboard_interrupt_while_running_restarts_the_sandbox(
    cli: list[ep.EdgeRuntime], runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = True

    def push(arg: dict[str, Any]) -> dict[str, Any]:
        nonlocal first
        if first:
            first = False
            raise KeyboardInterrupt
        return {"status": "complete", "error": None, "exit_code": None}

    fake_edge.page.sandbox["repl_push"] = push
    prompts: list[str] = []
    reader = _scripted_reader(["while True: pass\n", "x = 1\n"], prompts)
    with runtime_factory() as rt:
        assert ep.repl_loop(rt, read_line=reader) == 0
    assert fake_edge.page.methods.count("Page.navigate") == 2
    assert "Runtime.terminateExecution" in fake_edge.page.methods
    assert "restarting the sandbox" in capsys.readouterr().err
    assert prompts == [">>> ", ">>> ", ">>> "]


def test_repl_command_mounts_cwd_and_runs_the_loop(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cwd = tmp_path / "replcwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    # sys.stdin is an empty StringIO (cli fixture): the first input() hits EOF and ends the loop.
    assert ep.main(["repl", "--pkg", "tabulate"]) == 0
    assert [m["dest"] for m in _calls(fake_edge.page, "mount")] == ["/mnt/replcwd"]
    assert len(_calls(fake_edge.page, "install")) == 1
    assert _calls(fake_edge.page, "repl_push") == []
    assert cli[0].timeout is None


# ---------------------------------------------------------------------------
# doctor / packages / clean


def test_doctor_exits_zero_and_reports_a_missing_vendor_dir(
    fake_edge: FakeEdgeEnv, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty-vendor"
    empty.mkdir()
    assert ep.main(["doctor", "--vendor-dir", str(empty)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"ok", "failed", "checks"}
    by_id = {c["id"]: c for c in report["checks"]}
    assert report["ok"] is False
    assert report["failed"] == ["vendor_present"]
    assert by_id["vendor_present"]["status"] == "fail"
    assert "fetch --flavor full" in by_id["vendor_present"]["hint"]
    assert by_id["edge_found"]["status"] == "ok"
    assert by_id["edge_version"]["detail"] == "151.0.4129.101"
    assert by_id["policies"]["detail"] == "no Edge policies configured"
    assert by_id["run_dir_writable"]["status"] == "ok"
    assert by_id["port_bind"]["status"] == "ok"
    assert "live_boot" not in by_id
    assert fake_edge.process is None, "doctor without --live never launches Edge"


def test_doctor_reports_a_healthy_vendor_dir(
    fake_edge: FakeEdgeEnv, vendor_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ep.main(["doctor", "--vendor-dir", str(vendor_dir)]) == 0
    report = json.loads(capsys.readouterr().out)
    by_id = {c["id"]: c for c in report["checks"]}
    assert report["ok"] is True and report["failed"] == []
    assert by_id["vendor_flavor"]["status"] == "ok"
    assert by_id["vendor_wheels"]["detail"].startswith("2 pure-Python wheels")
    assert by_id["vendor_manifest"]["status"] == "ok"


def test_doctor_live_boots_the_fake_sandbox(
    cli: list[ep.EdgeRuntime], fake_edge: FakeEdgeEnv, vendor_dir: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    page = fake_edge.page

    def run(arg: dict[str, Any]) -> dict[str, Any]:
        if "input()" in arg["code"]:
            page.emit("o", "probe\n")
            return {"exit_code": 0, "traceback": None}
        return {"exit_code": 7, "traceback": None}

    page.sandbox["run"] = run
    monkeypatch.setattr(ep, "_pid_alive", lambda pid: False)
    assert ep.main(["doctor", "--live", "--vendor-dir", str(vendor_dir)]) == 0
    by_id = {c["id"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert by_id["live_boot"]["status"] == "ok"
    assert by_id["live_jspi"]["status"] == "ok"
    assert by_id["live_stdin_prompt"]["status"] == "ok"
    assert by_id["live_exit_code"]["status"] == "ok"
    assert by_id["live_teardown"]["status"] == "ok"


def test_packages_lists_bundled_and_wheels(
    fake_edge: FakeEdgeEnv, vendor_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ep.main(["packages", "--vendor-dir", str(vendor_dir)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["vendor_dir"] == str(vendor_dir)
    assert report["pyodide_version"] == "314.0.5"
    assert report["python"] == "3.14.2"
    assert report["flavor"] == "full"
    assert report["bundled"] == ["micropip", "numpy", "pandas", "pyyaml"]
    assert report["bundled_missing"] == []
    assert [(w["name"], w["version"]) for w in report["wheels"]] == [("six", "1.16.0"), ("tabulate", "0.9.0")]


def test_packages_with_a_missing_vendor_dir_exits_one(
    fake_edge: FakeEdgeEnv, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ep.main(["packages", "--vendor-dir", str(tmp_path / "nope")]) == 1
    assert _error_envelope(capsys.readouterr().err)["class"] == ep.ERR_VENDOR_MISSING


def test_clean_reports_run_root_removed_and_kept(
    fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path(os.environ["EDGEPY_RUN_DIR"])
    stale = root / "rdead0001"
    stale.mkdir(parents=True)
    (stale / "owner.json").write_text(json.dumps({"pid": 0}), encoding="utf-8")
    live = root / "rlive0002"
    live.mkdir()
    (live / "owner.json").write_text(json.dumps({"pid": 99999}), encoding="utf-8")
    (root / "stray.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(ep, "_pid_alive", lambda pid: pid == 99999)
    monkeypatch.setattr(ep, "_kill_edge_using", lambda run_dir: [4242] if run_dir.name == "rdead0001" else [])
    assert ep.main(["clean"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"run_root", "removed", "kept", "killed_pids"}
    assert Path(report["run_root"]) == root.resolve()
    assert report["removed"] == [str(stale)]
    assert report["kept"] == [str(live)]
    assert report["killed_pids"] == [4242]
    assert not stale.exists() and live.is_dir() and (root / "stray.txt").is_file()


def test_clean_with_no_run_root_is_empty(
    fake_edge: FakeEdgeEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    assert ep.main(["clean"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["removed"] == [] and report["kept"] == [] and report["killed_pids"] == []


# ---------------------------------------------------------------------------
# argparse surface and the error envelope


def test_missing_subcommand_is_an_argparse_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        ep.main([])
    assert info.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_unknown_stdin_choice_is_an_argparse_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        ep.main(["run", "--stdin", "keyboard", "-c", "pass"])
    assert info.value.code == 2


def test_verbose_flag_enables_diagnostics(cli: list[ep.EdgeRuntime], capsys: pytest.CaptureFixture[str]) -> None:
    assert ep.main(["run", "--verbose", "-c", "pass"]) == 0
    assert "edgepy: " in capsys.readouterr().err
    assert ep.VERBOSE is True


def test_emit_error_envelope_for_a_tagged_runtime_error(capsys: pytest.CaptureFixture[str]) -> None:
    exc = ep._tag(RuntimeError("Pyodide failed to boot"), ep.ERR_BOOT, http_status=503, hint="check the vendor folder")
    ep._emit_error(exc)
    assert _error_envelope(capsys.readouterr().err) == {
        "class": ep.ERR_BOOT, "http_status": 503, "message": "Pyodide failed to boot", "hint": "check the vendor folder",
    }


def test_emit_error_envelope_for_an_untagged_value_error(capsys: pytest.CaptureFixture[str]) -> None:
    ep._emit_error(ValueError("bad input"))
    assert _error_envelope(capsys.readouterr().err) == {
        "class": ep.ERR_VALIDATION, "http_status": None, "message": "bad input", "hint": None,
    }


def test_emit_error_envelope_defaults_untagged_runtime_errors_to_cdp(capsys: pytest.CaptureFixture[str]) -> None:
    ep._emit_error(RuntimeError("socket went away"))
    assert _error_envelope(capsys.readouterr().err)["class"] == ep.ERR_CDP


def test_emit_error_envelope_keeps_non_ascii_messages(capsys: pytest.CaptureFixture[str]) -> None:
    ep._emit_error(ValueError("café"))
    assert "café" in capsys.readouterr().err
