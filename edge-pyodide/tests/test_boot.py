"""The in-browser shim (`ep._BOOT_PY`) executed under local CPython.

The module is built exactly as the runner page does it - types.ModuleType('_edgepy') +
exec(compile(src, '<edgepy>', 'exec')) - with stub `pyodide_js` / `pyodide.code` modules
standing in for the Pyodide runtime. Requests go through `dispatch()` as JSON so the
reply envelope is exercised too."""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import os
import sys
import types
import zipfile
from pathlib import Path
from typing import Any

import pytest

import edge_pyodide as ep


class StubJS:
    """Records loadPackage / loadPackagesFromImports calls; can be told to fail."""

    def __init__(self) -> None:
        self.load_calls: list[tuple[Any, dict[str, Any]]] = []
        self.import_calls: list[tuple[str, dict[str, Any]]] = []
        self.import_error: Exception | None = None
        self.load_error: Exception | None = None

    async def loadPackage(self, names: Any, **kwargs: Any) -> None:  # noqa: N802 - JS name
        self.load_calls.append((names, dict(kwargs)))
        if self.load_error is not None:
            raise self.load_error

    async def loadPackagesFromImports(self, source: str, **kwargs: Any) -> None:  # noqa: N802 - JS name
        self.import_calls.append((source, dict(kwargs)))
        if self.import_error is not None:
            raise self.import_error


class Shim:
    def __init__(self, mod: types.ModuleType, js: StubJS, main: types.ModuleType) -> None:
        self.mod = mod
        self.js = js
        self.main = main

    def dispatch(self, name: str, arg: dict[str, Any] | None = None) -> dict[str, Any]:
        reply = asyncio.run(self.mod.dispatch(json.dumps({"name": name, "arg": arg or {}})))
        assert isinstance(reply, str)
        return json.loads(reply)

    def call(self, name: str, arg: dict[str, Any] | None = None) -> dict[str, Any]:
        reply = self.dispatch(name, arg)
        assert reply.get("ok") is True, reply
        return reply["result"]

    def run(self, **arg: Any) -> dict[str, Any]:
        return self.call("run", arg)


@pytest.fixture
def shim(monkeypatch: pytest.MonkeyPatch) -> Shim:
    js = StubJS()
    pyodide_js = types.ModuleType("pyodide_js")
    pyodide_js.loadPackage = js.loadPackage  # type: ignore[attr-defined]
    pyodide_js.loadPackagesFromImports = js.loadPackagesFromImports  # type: ignore[attr-defined]
    pyodide = types.ModuleType("pyodide")
    pyodide.__path__ = []  # a package with no submodules on disk: `pyodide.ffi` etc. fail to import
    pyodide.__version__ = "0.0-stub"
    code_mod = types.ModuleType("pyodide.code")
    code_mod.run_js = lambda _src: (lambda *a, **k: None)  # type: ignore[attr-defined]
    pyodide.code = code_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyodide_js", pyodide_js)
    monkeypatch.setitem(sys.modules, "pyodide", pyodide)
    monkeypatch.setitem(sys.modules, "pyodide.code", code_mod)
    # Isolate the pieces of interpreter state the shim mutates on purpose.
    main = types.ModuleType("__main__")
    monkeypatch.setitem(sys.modules, "__main__", main)
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.chdir(os.getcwd())  # records the cwd so `run` with a cwd is undone

    mod = types.ModuleType("_edgepy")
    exec(compile(ep._BOOT_PY, "<edgepy>", "exec"), mod.__dict__)
    return Shim(mod, js, main)


# ---------------------------------------------------------------------------
# dispatch envelope


def test_dispatch_unknown_request_name(shim: Shim) -> None:
    assert shim.dispatch("bogus") == {"ok": False, "error": "unknown request 'bogus'", "traceback": None}


def test_dispatch_handler_exception_is_wrapped(shim: Shim) -> None:
    reply = shim.dispatch("eval", {"expression": 'int("x")'})
    assert reply["ok"] is False
    assert reply["error"] == "ValueError: invalid literal for int() with base 10: 'x'"
    assert reply["traceback"].startswith("Traceback (most recent call last):")
    assert "ValueError" in reply["traceback"]


def test_dispatch_missing_arg_defaults_to_empty(shim: Shim) -> None:
    reply = json.loads(asyncio.run(shim.mod.dispatch(json.dumps({"name": "load"}))))
    assert reply == {"ok": True, "result": {"loaded": []}}


# ---------------------------------------------------------------------------
# run: kind "code"


def test_run_code_prints_and_sets_argv(shim: Shim, capsys: pytest.CaptureFixture[str]) -> None:
    result = shim.run(kind="code", code="import sys\nprint('hello', sys.argv)", argv=["--flag", "value"])
    assert result == {"exit_code": 0, "traceback": None}
    out, err = capsys.readouterr()
    assert out == "hello ['-c', '--flag', 'value']\n"
    assert err == ""
    assert sys.argv == ["-c", "--flag", "value"]


def test_run_code_sets_main_dunder_file(shim: Shim) -> None:
    shim.run(kind="code", code="captured = __file__", filename="/tmp/demo.py", argv0="/tmp/demo.py")
    assert shim.main.__dict__["captured"] == "/tmp/demo.py"
    assert shim.main.__file__ == "/tmp/demo.py"
    assert sys.argv == ["/tmp/demo.py"]


def test_run_code_default_filename_is_string(shim: Shim) -> None:
    shim.run(kind="code", code="captured = __file__")
    assert shim.main.__dict__["captured"] == "<string>"


def test_run_code_state_persists_in_main_between_runs(shim: Shim, capsys: pytest.CaptureFixture[str]) -> None:
    shim.run(kind="code", code="x = 41")
    shim.run(kind="code", code="print(x + 1)")
    assert capsys.readouterr().out == "42\n"


def test_run_code_sys_exit_int(shim: Shim) -> None:
    assert shim.run(kind="code", code="import sys; sys.exit(3)")["exit_code"] == 3


def test_run_code_sys_exit_message(shim: Shim, capsys: pytest.CaptureFixture[str]) -> None:
    result = shim.run(kind="code", code="import sys; sys.exit('msg')")
    assert result["exit_code"] == 1
    assert result["traceback"] is None
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "msg\n"


def test_run_code_sys_exit_none(shim: Shim, capsys: pytest.CaptureFixture[str]) -> None:
    assert shim.run(kind="code", code="import sys; sys.exit(None)")["exit_code"] == 0
    assert shim.run(kind="code", code="raise SystemExit")["exit_code"] == 0
    assert capsys.readouterr().err == ""


def test_run_code_exception_gives_trimmed_traceback(shim: Shim, capsys: pytest.CaptureFixture[str]) -> None:
    result = shim.run(kind="code", code="def boom():\n    raise ValueError('nope')\n\nboom()\n")
    assert result["exit_code"] == 1
    tb = result["traceback"]
    assert tb.startswith("Traceback (most recent call last):")
    assert tb.rstrip().endswith("ValueError: nope")
    assert "<edgepy>" not in tb
    assert "runpy" not in tb
    assert '"<string>"' in tb
    assert "boom" in tb
    out, err = capsys.readouterr()
    assert out == ""
    assert err == tb


def test_run_code_keyboard_interrupt_is_reported_not_propagated(shim: Shim) -> None:
    result = shim.run(kind="code", code="raise KeyboardInterrupt")
    assert result["exit_code"] == 1
    assert "KeyboardInterrupt" in result["traceback"]


def test_run_flushes_partial_output(shim: Shim, capsys: pytest.CaptureFixture[str]) -> None:
    shim.run(kind="code", code="import sys; sys.stdout.write('no newline')")
    assert capsys.readouterr().out == "no newline"


# ---------------------------------------------------------------------------
# run: kind "file" / "module"


def test_run_file_sets_file_argv_and_cwd(shim: Shim, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "script.py"
    script.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'file': __file__, 'argv': sys.argv, 'cwd': os.getcwd(), 'name': __name__}))\n",
        encoding="utf-8",
    )
    before = os.getcwd()
    result = shim.run(kind="file", path=str(script), argv=["a", "b"], cwd=str(tmp_path))
    assert result == {"exit_code": 0, "traceback": None}
    seen = json.loads(capsys.readouterr().out)
    assert seen["file"] == str(script)
    assert seen["argv"] == [str(script), "a", "b"]
    assert seen["name"] == "__main__"
    assert Path(seen["cwd"]).resolve() == tmp_path.resolve()
    assert Path(os.getcwd()).resolve() == tmp_path.resolve()  # stays changed until the fixture undoes it
    assert Path(before).resolve() != tmp_path.resolve()


def test_run_file_exception_traceback_hides_runpy(shim: Shim, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "bad.py"
    script.write_text("def f():\n    raise RuntimeError('from file')\nf()\n", encoding="utf-8")
    result = shim.run(kind="file", path=str(script), argv=[], cwd=None)
    assert result["exit_code"] == 1
    tb = result["traceback"]
    assert tb.startswith("Traceback (most recent call last):")
    assert "runpy" not in tb
    assert "<edgepy>" not in tb
    assert "bad.py" in tb
    assert tb.rstrip().endswith("RuntimeError: from file")
    assert capsys.readouterr().err == tb


def test_run_file_missing_script_is_reported_like_python_does(shim: Shim, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # The open() failure happens entirely inside runpy, so every frame is trimmed and
    # only the error line remains - exit 1, no "Traceback" header, still ok:true.
    result = shim.run(kind="file", path=str(tmp_path / "absent.py"), argv=[])
    assert result["exit_code"] == 1
    assert result["traceback"].startswith("FileNotFoundError:")
    assert "absent.py" in result["traceback"]
    assert "Traceback" not in result["traceback"]
    assert capsys.readouterr().err == result["traceback"]


def test_run_module_uses_runpy_with_alter_sys(shim: Shim, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    pkg = tmp_path / "edgepytestpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "cli.py").write_text(
        "import json, sys\n"
        "if __name__ == '__main__':\n"
        "    print(json.dumps({'argv': sys.argv, 'file': __file__}))\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    try:
        result = shim.run(kind="module", module="edgepytestpkg.cli", argv=["--x", "1"], cwd=None)
    finally:
        for name in ("edgepytestpkg", "edgepytestpkg.cli"):
            sys.modules.pop(name, None)
    assert result == {"exit_code": 0, "traceback": None}
    seen = json.loads(capsys.readouterr().out)
    # alter_sys=True makes runpy put the module's file path in sys.argv[0] while it runs.
    assert Path(seen["argv"][0]).resolve() == (pkg / "cli.py").resolve()
    assert seen["argv"][1:] == ["--x", "1"]
    assert Path(seen["file"]).resolve() == (pkg / "cli.py").resolve()
    # After the run runpy restores argv[0] to what the shim set.
    assert sys.argv == ["edgepytestpkg.cli", "--x", "1"]


def test_run_module_not_found_is_reported_like_python_does(shim: Shim, capsys: pytest.CaptureFixture[str]) -> None:
    result = shim.run(kind="module", module="edgepy_no_such_module_xyz", argv=[])
    assert result["exit_code"] == 1
    assert result["traceback"] == "ImportError: No module named edgepy_no_such_module_xyz\n"
    assert capsys.readouterr().err == result["traceback"]


# ---------------------------------------------------------------------------
# run: package preload


def test_run_preloads_packages_from_source(shim: Shim) -> None:
    shim.run(kind="code", code="pass", source="import numpy\nimport yaml\n")
    assert len(shim.js.import_calls) == 1
    source, kwargs = shim.js.import_calls[0]
    assert source == "import numpy\nimport yaml\n"
    assert set(kwargs) == {"messageCallback", "errorCallback"}
    assert all(callable(v) for v in kwargs.values())


def test_run_skips_preload_without_source(shim: Shim) -> None:
    shim.run(kind="code", code="pass")
    shim.run(kind="code", code="pass", source="")
    assert shim.js.import_calls == []


def test_run_continues_when_preload_fails(shim: Shim, capsys: pytest.CaptureFixture[str]) -> None:
    shim.js.import_error = RuntimeError("no wheel for numpy")
    result = shim.run(kind="code", code="print('still ran')", source="import numpy")
    assert result["exit_code"] == 0
    out, err = capsys.readouterr()
    assert out == "still ran\n"
    assert err == "edgepy: package preload failed: no wheel for numpy\n"


def test_pkg_error_callback_writes_to_stderr(shim: Shim, capsys: pytest.CaptureFixture[str]) -> None:
    shim.run(kind="code", code="pass", source="import numpy")
    _, kwargs = shim.js.import_calls[0]
    kwargs["errorCallback"]("boom")
    assert capsys.readouterr().err == "edgepy pkg: boom\n"


# ---------------------------------------------------------------------------
# load


def test_load_forwards_names_with_callbacks(shim: Shim) -> None:
    result = shim.call("load", {"names": ["numpy", "pandas"]})
    assert result == {"loaded": ["numpy", "pandas"]}
    assert len(shim.js.load_calls) == 1
    names, kwargs = shim.js.load_calls[0]
    assert names == ["numpy", "pandas"]
    assert set(kwargs) == {"messageCallback", "errorCallback"}


def test_load_forwards_check_integrity_option(shim: Shim) -> None:
    shim.call("load", {"names": ["numpy"], "options": {"checkIntegrity": False}})
    _, kwargs = shim.js.load_calls[0]
    assert kwargs["checkIntegrity"] is False
    assert "messageCallback" in kwargs and "errorCallback" in kwargs


def test_load_with_no_names_does_not_call_pyodide(shim: Shim) -> None:
    assert shim.call("load", {"names": []}) == {"loaded": []}
    assert shim.call("load", {}) == {"loaded": []}
    assert shim.js.load_calls == []


def test_load_failure_surfaces_as_handler_error(shim: Shim) -> None:
    shim.js.load_error = ValueError("No known package with name 'nope'")
    reply = shim.dispatch("load", {"names": ["nope"]})
    assert reply["ok"] is False
    assert reply["error"] == "ValueError: No known package with name 'nope'"


# ---------------------------------------------------------------------------
# eval / info


def test_eval_json_value(shim: Shim) -> None:
    assert shim.call("eval", {"expression": "1+1"}) == {"value": 2, "repr": None}


def test_eval_non_json_value_uses_repr(shim: Shim) -> None:
    result = shim.call("eval", {"expression": "object()"})
    assert result["value"] is None
    assert result["repr"].startswith("<object object at ")


def test_eval_sees_main_namespace(shim: Shim) -> None:
    shim.run(kind="code", code="answer = [1, 2]")
    assert shim.call("eval", {"expression": "answer * 2"}) == {"value": [1, 2, 1, 2], "repr": None}


def test_info_degrades_gracefully_without_pyodide_internals(shim: Shim) -> None:
    import platform

    result = shim.call("info")
    assert result == {"pyodide": "0.0-stub", "python": platform.python_version(), "jspi": False, "loaded": []}


# ---------------------------------------------------------------------------
# _unpack_mount


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def test_unpack_mount_extracts_and_prepends_sys_path(shim: Shim, tmp_path: Path) -> None:
    dest = str(tmp_path / "mnt" / "proj")
    data = _zip_bytes({"mod.py": "VALUE = 7\n", "pkg/__init__.py": "", "pkg/data.txt": "x"})
    result = shim.mod._unpack_mount(data, dest)
    assert result == {"dest": dest, "files": 3}
    assert (Path(dest) / "mod.py").read_text(encoding="utf-8") == "VALUE = 7\n"
    assert (Path(dest) / "pkg" / "data.txt").read_text(encoding="utf-8") == "x"
    assert sys.path[0] == dest
    assert shim.mod._STATE["mounts"] == [dest]


def test_unpack_mount_twice_does_not_duplicate_sys_path(shim: Shim, tmp_path: Path) -> None:
    dest = str(tmp_path / "mnt")
    shim.mod._unpack_mount(_zip_bytes({"a.py": ""}), dest)
    shim.mod._unpack_mount(_zip_bytes({"b.py": ""}), dest)
    assert sys.path.count(dest) == 1
    assert sorted(p.name for p in Path(dest).iterdir()) == ["a.py", "b.py"]


def test_unpack_mount_makes_code_importable(shim: Shim, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dest = str(tmp_path / "mnt")
    shim.mod._unpack_mount(_zip_bytes({"edgepy_mounted_mod.py": "GREETING = 'hi from mount'\n"}), dest)
    importlib.invalidate_caches()
    try:
        shim.run(kind="code", code="import edgepy_mounted_mod; print(edgepy_mounted_mod.GREETING)")
    finally:
        sys.modules.pop("edgepy_mounted_mod", None)
    assert capsys.readouterr().out == "hi from mount\n"


# ---------------------------------------------------------------------------
# _exit_code / _format_user_traceback


@pytest.mark.parametrize(("code", "expected"), [(None, 0), (0, 0), (3, 3), (-1, -1), (True, 1)])
def test_exit_code_int_and_none(shim: Shim, code: Any, expected: int, capsys: pytest.CaptureFixture[str]) -> None:
    assert shim.mod._exit_code(SystemExit(code)) == expected
    assert capsys.readouterr().err == ""


def test_exit_code_bare_system_exit_is_zero(shim: Shim) -> None:
    assert shim.mod._exit_code(SystemExit()) == 0


def test_exit_code_message_prints_and_returns_one(shim: Shim, capsys: pytest.CaptureFixture[str]) -> None:
    assert shim.mod._exit_code(SystemExit("fatal: thing")) == 1
    assert capsys.readouterr().err == "fatal: thing\n"


def test_exit_code_non_int_object_prints_str(shim: Shim, capsys: pytest.CaptureFixture[str]) -> None:
    assert shim.mod._exit_code(SystemExit(2.5)) == 1
    assert capsys.readouterr().err == "2.5\n"


def _raise_through(filenames: list[str]) -> BaseException:
    """Raise ValueError through nested frames whose co_filename values are `filenames`
    (outermost first). The outermost frame catches it, so the traceback chain starts
    there - exactly like the shim's `_run` catching a user exception."""
    namespace: dict[str, Any] = {}
    inner_name: str | None = None
    for depth, filename in enumerate(reversed(filenames)):
        name = f"frame{depth}"
        action = "raise ValueError('deep')" if inner_name is None else f"{inner_name}()"
        if depth == len(filenames) - 1:
            src = f"def {name}():\n    try:\n        {action}\n    except ValueError as exc:\n        return exc\n"
        else:
            src = f"def {name}():\n    {action}\n"
        exec(compile(src, filename, "exec"), namespace)
        inner_name = name
    assert inner_name is not None
    exc = namespace[inner_name]()
    assert isinstance(exc, ValueError)
    return exc


def test_format_user_traceback_drops_shim_and_runpy_frames(shim: Shim) -> None:
    exc = _raise_through(["<edgepy>", "C:/Python310/lib/runpy.py", "<frozen runpy>", "/mnt/app/user.py"])
    text = shim.mod._format_user_traceback(exc)
    assert text.startswith("Traceback (most recent call last):")
    assert "<edgepy>" not in text
    assert "runpy" not in text
    assert "/mnt/app/user.py" in text
    assert text.rstrip().endswith("ValueError: deep")


def test_format_user_traceback_keeps_frames_below_the_first_user_frame(shim: Shim) -> None:
    exc = _raise_through(["<edgepy>", "/mnt/app/main.py", "<frozen importlib._bootstrap>", "/mnt/app/helper.py"])
    text = shim.mod._format_user_traceback(exc)
    # Only the leading run-machinery frames are trimmed; an importlib frame in the middle
    # of the user's own call chain is preserved as CPython would show it.
    assert "/mnt/app/main.py" in text
    assert "<frozen importlib._bootstrap>" in text
    assert "/mnt/app/helper.py" in text


def test_format_user_traceback_without_user_frames_is_just_the_exception(shim: Shim) -> None:
    exc = _raise_through(["<edgepy>"])
    text = shim.mod._format_user_traceback(exc)
    assert text == "ValueError: deep\n"


def test_format_user_traceback_untouched_when_no_machinery_frames(shim: Shim) -> None:
    exc = _raise_through(["/mnt/app/user.py"])
    text = shim.mod._format_user_traceback(exc)
    assert text.startswith("Traceback (most recent call last):")
    assert "/mnt/app/user.py" in text
