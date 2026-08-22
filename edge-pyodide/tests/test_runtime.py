"""EdgeRuntime over the fake Edge: boot, runs, packages, mounts, timeouts, dialogs, teardown.

Every test here drives the real EdgeRuntime code path; only the process/websocket/HTTP
seams are replaced by the `fake_edge` fixture from conftest.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any, Callable

import pytest

import edge_pyodide as ep
from conftest import FakeEdgeEnv, FakePage, binding_event_bytes, make_vendor

# ---------------------------------------------------------------------------
# Local helpers


class FakeClock:
    """Monotonic clock stand-in: every read advances by `step`, so deadlines pass without sleeping."""

    def __init__(self, start: float = 1000.0, step: float = 0.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


class FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _sent_params(ws: Any, method: str) -> list[dict[str, Any]]:
    return [m.get("params") or {} for m in ws.sent if m["method"] == method]


def _calls(page: FakePage, name: str) -> list[dict[str, Any]]:
    return [arg for called, arg in page.calls if called == name]


def _hang_sandbox_call(page: FakePage, name: str) -> None:
    """Make the page swallow `window.edgepy.call(name, ...)` so the reply never arrives."""
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
def piped_stdin(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    stream = io.StringIO("")
    monkeypatch.setattr("sys.stdin", stream)
    return stream


# ---------------------------------------------------------------------------
# Boot sequence


def test_boot_sends_cdp_commands_in_order_and_navigates_to_the_local_server(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    with runtime_factory() as rt:
        assert rt.server is not None
        base_url = rt.server.base_url
        assert fake_edge.page.methods[:4] == ["Runtime.enable", "Page.enable", "Runtime.addBinding", "Page.navigate"]
        assert fake_edge.page.methods[4] == "Runtime.evaluate"
        assert fake_edge.page_ws is not None
        assert _sent_params(fake_edge.page_ws, "Runtime.addBinding") == [{"name": ep.BINDING_NAME}]
        assert _sent_params(fake_edge.page_ws, "Page.navigate") == [{"url": base_url + "/"}]
        state_polls = [
            m for m in fake_edge.page_ws.sent
            if m["method"] == "Runtime.evaluate" and "window.edgepy.state" in m["params"]["expression"]
        ]
        assert state_polls, "boot must poll window.edgepy.state"
        assert base_url.startswith("http://127.0.0.1:")


def test_boot_reports_versions_and_jspi_from_the_info_call(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    with runtime_factory() as rt:
        assert rt.pyodide_version == "314.0.5"
        assert rt.python_version == "3.14.2"
        assert rt.jspi is True
        assert rt.edge_version == "Edg/151.0.4129.101"
        assert rt.boot_seconds is not None and rt.boot_seconds >= 0
        assert _calls(fake_edge.page, "info") == [{}]


def test_stdin_auto_resolves_to_prompt_when_stdin_is_not_a_tty(
    runtime_factory: Callable[..., ep.EdgeRuntime], piped_stdin: io.StringIO
) -> None:
    # A pipe is read one line per input() through the dialog bridge; pre-reading the whole
    # pipe (lines mode) would block forever when the writer keeps it open.
    with runtime_factory(stdin="auto") as rt:
        assert rt.stdin_mode == "prompt"


def test_stdin_auto_resolves_to_prompt_on_a_tty(
    runtime_factory: Callable[..., ep.EdgeRuntime], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", FakeTty())
    with runtime_factory(stdin="auto") as rt:
        assert rt.stdin_mode == "prompt"


def test_stdin_none_is_honoured(runtime_factory: Callable[..., ep.EdgeRuntime], piped_stdin: io.StringIO) -> None:
    with runtime_factory(stdin="none") as rt:
        assert rt.stdin_mode == "none"


def test_devtools_forces_lines_mode_even_on_a_tty(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", FakeTty())
    with runtime_factory(stdin="auto", devtools=True, headless=False) as rt:
        assert rt.stdin_mode == "lines"
    assert fake_edge.launch_argv is not None
    assert "--auto-open-devtools-for-tabs" in fake_edge.launch_argv
    assert "--headless" not in fake_edge.launch_argv


def test_invalid_stdin_choice_is_rejected_at_construction(vendor_dir: Path) -> None:
    with pytest.raises(ValueError) as info:
        ep.EdgeRuntime(vendor_dir=vendor_dir, stdin="keyboard", on_stdout=None, on_stderr=None)
    assert info.value.error_class == ep.ERR_VALIDATION  # type: ignore[attr-defined]


def test_runner_page_is_served_with_the_resolved_stdin_mode(
    runtime_factory: Callable[..., ep.EdgeRuntime], piped_stdin: io.StringIO
) -> None:
    with runtime_factory(stdin="none") as rt:
        assert rt.server is not None
        body, ctype = rt.server.routes["index.html"]
        assert ctype == ep.CONTENT_TYPES[".html"]
        assert b'const stdinMode = "none";' in body
        assert ep.BINDING_NAME.encode() in body
        assert "simple/" in rt.server.routes
        assert "simple/tabulate/" in rt.server.routes


# ---------------------------------------------------------------------------
# Boot failures


def test_failed_page_state_raises_boot_error_with_the_page_error(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    fake_edge.page.state = "failed"
    fake_edge.page.error = "TypeError: WebAssembly.instantiate exploded"
    rt = runtime_factory()
    with pytest.raises(RuntimeError) as info:
        rt.start()
    assert info.value.error_class == ep.ERR_BOOT  # type: ignore[attr-defined]
    assert "WebAssembly.instantiate exploded" in str(info.value)
    # start() tears everything down on failure
    assert rt.cdp is None and rt.edge is None and rt.server is None
    assert fake_edge.browser_ws is not None
    assert any(m["method"] == "Browser.close" for m in fake_edge.browser_ws.sent)


def test_page_stuck_in_booting_hits_boot_timeout_with_antivirus_hint(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch,
    piped_stdin: io.StringIO,
) -> None:
    fake_edge.page.state = "booting"
    monkeypatch.setattr(ep, "_now", FakeClock(step=5.0))
    with pytest.raises(RuntimeError) as info:
        runtime_factory().start()
    assert info.value.error_class == ep.ERR_BOOT  # type: ignore[attr-defined]
    assert f"{ep.BOOT_TIMEOUT:g}s" in str(info.value)
    assert "antivirus" in info.value.hint  # type: ignore[attr-defined]
    assert fake_edge.sleeps == [], "boot polling must never call time.sleep directly"


def test_wasm_instantiation_console_error_during_boot_gives_content_type_hint(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    page = fake_edge.page
    page.state = "booting"
    original = page.respond
    fired = False

    def respond(message: dict[str, Any]) -> list[dict[str, Any]]:
        nonlocal fired
        replies = original(message)
        params = message.get("params") or {}
        if (not fired and message["method"] == "Runtime.evaluate"
                and "window.edgepy.state" in params.get("expression", "")):
            fired = True
            console = {"method": "Runtime.consoleAPICalled",
                       "params": {"type": "error", "args": [{"type": "string", "value": "wasm instantiation failed!"}]}}
            return [console, *replies]
        return replies

    page.respond = respond  # type: ignore[method-assign]
    with pytest.raises(RuntimeError) as info:
        runtime_factory().start()
    assert info.value.error_class == ep.ERR_BOOT  # type: ignore[attr-defined]
    assert "wasm instantiation failed!" in str(info.value)
    assert "Content-Type" in info.value.hint  # type: ignore[attr-defined]


def test_page_exception_during_boot_is_reported_as_boot_error(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    page = fake_edge.page
    page.state = "booting"
    original = page.respond
    fired = False

    def respond(message: dict[str, Any]) -> list[dict[str, Any]]:
        nonlocal fired
        replies = original(message)
        if not fired and message["method"] == "Runtime.evaluate":
            fired = True
            thrown = {"method": "Runtime.exceptionThrown",
                      "params": {"exceptionDetails": {"exception": {"description": "ReferenceError: loadPyodide is not defined"}}}}
            return [thrown, *replies]
        return replies

    page.respond = respond  # type: ignore[method-assign]
    with pytest.raises(RuntimeError) as info:
        runtime_factory().start()
    assert info.value.error_class == ep.ERR_BOOT  # type: ignore[attr-defined]
    assert "loadPyodide is not defined" in str(info.value)


# ---------------------------------------------------------------------------
# run_code


def test_run_code_captures_streams_and_exit_code(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    page = fake_edge.page

    def run(arg: dict[str, Any]) -> dict[str, Any]:
        page.emit("o", "hello\n")
        page.emit("e", "warning\n")
        page.emit("o", "bye\n")
        return {"exit_code": 3, "traceback": None}

    page.sandbox["run"] = run
    with runtime_factory() as rt:
        result = rt.run_code("print('hello')")
    assert result.exit_code == 3
    assert result.stdout == "hello\nbye\n"
    assert result.stderr == "warning\n"
    assert result.traceback is None
    assert result.truncated is False
    assert result.duration_s >= 0
    (arg,) = _calls(page, "run")
    assert arg["kind"] == "code"
    assert arg["code"] == "print('hello')"
    assert arg["filename"] == "<string>"
    assert arg["argv"] == []
    assert arg["source"] == "print('hello')"


def test_run_code_reassembles_a_utf8_character_split_across_binding_events(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    page = fake_edge.page
    first, second = "é".encode("utf-8")[:1], "é".encode("utf-8")[1:]

    def run(arg: dict[str, Any]) -> dict[str, Any]:
        page.emit("o", "caf")
        page.pending_events.append(binding_event_bytes("o", first))
        page.pending_events.append(binding_event_bytes("o", second))
        page.emit("o", "\n")
        return {"exit_code": 0, "traceback": None}

    page.sandbox["run"] = run
    chunks: list[str] = []
    with runtime_factory(on_stdout=chunks.append) as rt:
        result = rt.run_code("print('café')")
    assert result.stdout == "café\n"
    assert chunks == ["caf", "é", "\n"]


def test_run_code_forwards_stderr_live_and_passes_argv(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    page = fake_edge.page

    def run(arg: dict[str, Any]) -> dict[str, Any]:
        page.emit("e", "Traceback\n")
        return {"exit_code": 1, "traceback": "Traceback\n"}

    page.sandbox["run"] = run
    err_chunks: list[str] = []
    with runtime_factory(on_stderr=err_chunks.append) as rt:
        result = rt.run_code("raise SystemExit(1)", argv=["--x", "1"], filename="snippet.py")
    assert err_chunks == ["Traceback\n"]
    assert result.stderr == "Traceback\n"
    assert result.traceback == "Traceback\n"
    (arg,) = _calls(page, "run")
    assert arg["argv"] == ["--x", "1"]
    assert arg["filename"] == "snippet.py"


def test_run_code_reports_packages_loaded_earlier(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    with runtime_factory() as rt:
        before = rt.run_code("pass")
        rt.load_packages(["numpy"])
        rt.install(["tabulate"])
        after = rt.run_code("pass")
        assert rt.loaded_packages == ["numpy", "tabulate"]
    assert before.packages_loaded == []
    assert after.packages_loaded == ["numpy", "tabulate"]


def test_run_code_output_is_reset_between_runs(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    page = fake_edge.page
    page.sandbox["run"] = lambda arg: (page.emit("o", arg["code"]), {"exit_code": 0, "traceback": None})[1]
    with runtime_factory() as rt:
        assert rt.run_code("one").stdout == "one"
        assert rt.run_code("two").stdout == "two"


def test_sandbox_error_envelope_becomes_a_tagged_sandbox_error(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    def run(arg: dict[str, Any]) -> dict[str, Any]:
        raise KeyError("kind")

    fake_edge.page.sandbox["run"] = run
    with runtime_factory() as rt:
        with pytest.raises(RuntimeError) as info:
            rt.run_code("x")
    assert info.value.error_class == ep.ERR_SANDBOX  # type: ignore[attr-defined]
    assert "KeyError" in str(info.value)
    assert info.value.hint == "tb"  # type: ignore[attr-defined]


def test_javascript_exception_from_evaluate_is_a_cdp_error(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    page = fake_edge.page
    original = page.respond

    def respond(message: dict[str, Any]) -> list[dict[str, Any]]:
        params = message.get("params") or {}
        if message["method"] == "Runtime.evaluate" and params.get("expression", "").startswith('window.edgepy.call("run"'):
            page.methods.append(message["method"])
            return [{"id": message["id"], "result": {"exceptionDetails": {"exception": {"description": "TypeError: window.edgepy.call is not a function"}}}}]
        return original(message)

    page.respond = respond  # type: ignore[method-assign]
    with runtime_factory() as rt:
        with pytest.raises(RuntimeError) as info:
            rt.run_code("x")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    assert "window.edgepy.call is not a function" in str(info.value)


# ---------------------------------------------------------------------------
# run_file / run_module


def test_run_file_mounts_the_parent_folder_and_runs_from_it(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path, piped_stdin: io.StringIO
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    script = folder / "script.py"
    script.write_text("print('hi')\n", encoding="utf-8")
    with runtime_factory() as rt:
        assert rt.server is not None
        base_url = rt.server.base_url
        result = rt.run_file(script, argv=["a", "b"])
        assert result.exit_code == 0
        (mount_arg,) = _calls(fake_edge.page, "mount")
        assert mount_arg["dest"] == "/mnt/proj"
        assert mount_arg["url"].startswith(base_url + "/mount/")
        assert mount_arg["url"].endswith(".zip")
        zip_route = mount_arg["url"][len(base_url) + 1:]
        assert zip_route in rt.server.routes
        assert rt.server.routes[zip_route][1] == ep.CONTENT_TYPES[".zip"]
    (run_arg,) = _calls(fake_edge.page, "run")
    assert run_arg["kind"] == "file"
    assert run_arg["path"] == "/mnt/proj/script.py"
    assert run_arg["cwd"] == "/mnt/proj"
    assert run_arg["argv"] == ["a", "b"]
    assert run_arg["source"] == "print('hi')\n"


def test_run_file_without_mount_executes_source_under_tmp(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path, piped_stdin: io.StringIO
) -> None:
    script = tmp_path / "solo.py"
    script.write_text("import sys\n", encoding="utf-8")
    with runtime_factory() as rt:
        rt.run_file(script, mount_parent=False)
    assert _calls(fake_edge.page, "mount") == []
    (run_arg,) = _calls(fake_edge.page, "run")
    assert run_arg["kind"] == "code"
    assert run_arg["filename"] == "/tmp/solo.py"
    assert run_arg["argv0"] == "/tmp/solo.py"
    assert run_arg["code"] == "import sys\n"
    assert run_arg["cwd"] is None


def test_run_file_missing_script_is_a_validation_error_without_touching_the_page(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path, piped_stdin: io.StringIO
) -> None:
    with runtime_factory() as rt:
        with pytest.raises(ValueError) as info:
            rt.run_file(tmp_path / "nope.py")
    assert info.value.error_class == ep.ERR_VALIDATION  # type: ignore[attr-defined]
    assert "nope.py" in str(info.value)
    assert _calls(fake_edge.page, "run") == []
    assert _calls(fake_edge.page, "mount") == []


def test_second_run_file_from_the_same_folder_does_not_mount_again(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path, piped_stdin: io.StringIO
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "a.py").write_text("a = 1\n", encoding="utf-8")
    (folder / "b.py").write_text("b = 2\n", encoding="utf-8")
    with runtime_factory() as rt:
        rt.run_file(folder / "a.py")
        rt.run_file(folder / "b.py")
    assert len(_calls(fake_edge.page, "mount")) == 1
    paths = [arg["path"] for arg in _calls(fake_edge.page, "run")]
    assert paths == ["/mnt/proj/a.py", "/mnt/proj/b.py"]


def test_mount_sanitizes_the_mount_name_and_dedupes_by_dest(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path, piped_stdin: io.StringIO
) -> None:
    folder = tmp_path / "my project (v2)"
    folder.mkdir()
    (folder / "x.py").write_text("", encoding="utf-8")
    with runtime_factory() as rt:
        assert rt.mount(folder) == "/mnt/my_project_v2_"
        assert rt.mount(folder, name="lib") == "/mnt/lib"
        assert rt.mount(tmp_path / "my project (v2)", name="lib") == "/mnt/lib"
    assert [arg["dest"] for arg in _calls(fake_edge.page, "mount")] == ["/mnt/my_project_v2_", "/mnt/lib"]


def test_run_module_rejects_invalid_module_names(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    with runtime_factory() as rt:
        for bad in ("bad name", "1abc", "pkg/mod", "", "pkg.mod;import os"):
            with pytest.raises(ValueError) as info:
                rt.run_module(bad)
            assert info.value.error_class == ep.ERR_VALIDATION  # type: ignore[attr-defined]
    assert _calls(fake_edge.page, "run") == []


def test_run_module_sends_kind_module_and_preloads_the_top_level_package(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    with runtime_factory() as rt:
        rt.run_module("pkg.sub.mod", argv=["-v"])
    (run_arg,) = _calls(fake_edge.page, "run")
    assert run_arg == {"kind": "module", "module": "pkg.sub.mod", "argv": ["-v"], "cwd": None, "source": "import pkg"}
    assert _calls(fake_edge.page, "mount") == [], "run_module leaves mounting to the caller"


# ---------------------------------------------------------------------------
# Packages


def test_load_packages_sends_bundled_names_with_empty_options(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    with runtime_factory() as rt:
        assert rt.load_packages(["numpy"]) == ["numpy"]
        assert rt.load_packages([]) == []
        assert rt.load_packages(["  "]) == []
    assert _calls(fake_edge.page, "load") == [{"names": ["numpy"], "options": {}}]


def test_load_packages_disables_integrity_when_the_manifest_says_so(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, vendor_dir: Path, piped_stdin: io.StringIO
) -> None:
    manifest_path = vendor_dir / ep.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["integrity"] = False
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with runtime_factory() as rt:
        rt.load_packages(["numpy", "pandas"])
        rt.install(["tabulate"])
    assert _calls(fake_edge.page, "load") == [{"names": ["numpy", "pandas"], "options": {"checkIntegrity": False}}]
    (install_arg,) = _calls(fake_edge.page, "install")
    assert install_arg["check_integrity"] is False


def test_load_packages_rejects_unknown_and_missing_packages(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    with runtime_factory() as rt:
        with pytest.raises(RuntimeError) as info:
            rt.load_packages(["definitely-not-a-package"])
        assert info.value.error_class == ep.ERR_PACKAGE_MISSING  # type: ignore[attr-defined]
    assert _calls(fake_edge.page, "load") == []


def test_install_routes_wheels_through_the_local_simple_index(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    with runtime_factory() as rt:
        assert rt.server is not None
        base_url = rt.server.base_url
        assert rt.install(["tabulate"]) == ["tabulate"]
        assert rt.install([]) == []
    assert _calls(fake_edge.page, "install") == [
        {"names": ["tabulate"], "index_url": base_url + "/simple", "check_integrity": True},
    ]


def test_install_on_core_vendor_without_micropip_raises_package_missing(
    fake_edge: FakeEdgeEnv, tmp_path: Path, piped_stdin: io.StringIO
) -> None:
    vendor = make_vendor(tmp_path / "vendor-core", flavor="core")
    (vendor / "pyodide" / "micropip-0.11.1-py3-none-any.whl").unlink()
    with ep.EdgeRuntime(vendor_dir=vendor, on_stdout=None, on_stderr=None) as rt:
        assert rt.vendor is not None and rt.vendor.flavor == "core"
        with pytest.raises(RuntimeError) as info:
            rt.install(["tabulate"])
    assert info.value.error_class == ep.ERR_PACKAGE_MISSING  # type: ignore[attr-defined]
    assert "micropip" in str(info.value)
    assert _calls(fake_edge.page, "install") == []


def test_install_on_core_vendor_with_micropip_present_works(
    fake_edge: FakeEdgeEnv, core_vendor_dir: Path, piped_stdin: io.StringIO
) -> None:
    with ep.EdgeRuntime(vendor_dir=core_vendor_dir, on_stdout=None, on_stderr=None) as rt:
        assert rt.install(["six"]) == ["six"]
        with pytest.raises(RuntimeError) as info:
            rt.load_packages(["numpy"])  # bundled in the lockfile but the wheel is absent in core
    assert info.value.error_class == ep.ERR_PACKAGE_MISSING  # type: ignore[attr-defined]
    assert "core" in str(info.value)


# ---------------------------------------------------------------------------
# Mount cap


def test_mount_larger_than_the_cap_is_rejected_before_any_sandbox_call(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path, piped_stdin: io.StringIO
) -> None:
    folder = tmp_path / "big"
    folder.mkdir()
    # Extend without writing: NTFS/ext4 make this instant, and only st_size is consulted
    # before the cap check fires (the file is never read).
    with open(folder / "blob.bin", "wb") as fh:
        fh.truncate(ep.MOUNT_CAP_BYTES + 1)
    with runtime_factory() as rt:
        with pytest.raises(ValueError) as info:
            rt.mount(folder)
    assert info.value.error_class == ep.ERR_MOUNT_TOO_LARGE  # type: ignore[attr-defined]
    assert "64 MB" in str(info.value)
    assert _calls(fake_edge.page, "mount") == []


def test_mount_cap_honours_the_module_constant_at_call_time(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, piped_stdin: io.StringIO,
) -> None:
    folder = tmp_path / "small"
    folder.mkdir()
    (folder / "data.txt").write_bytes(b"x" * 200)
    monkeypatch.setattr(ep, "MOUNT_CAP_BYTES", 100)
    with runtime_factory() as rt:
        with pytest.raises(ValueError) as info:
            rt.mount(folder)
    assert info.value.error_class == ep.ERR_MOUNT_TOO_LARGE  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Timeout and poisoning


def test_run_timeout_raises_tagged_error_then_close_terminates_before_browser_close(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch,
    piped_stdin: io.StringIO,
) -> None:
    _hang_sandbox_call(fake_edge.page, "run")
    monkeypatch.setattr(ep, "_now", FakeClock(step=0.005))
    rt = runtime_factory()
    with rt:
        with pytest.raises(RuntimeError) as info:
            rt.run_code("while True: pass", timeout=0.01)
        assert info.value.error_class == ep.ERR_TIMEOUT  # type: ignore[attr-defined]
        assert "0.01s" in str(info.value)
        assert "terminated" in info.value.hint  # type: ignore[attr-defined]
        assert rt._poisoned is True
    methods = fake_edge.page.methods
    assert "Runtime.terminateExecution" in methods
    assert methods.index("Runtime.terminateExecution") < methods.index("Browser.close")
    assert fake_edge.page_ws is not None and fake_edge.page_ws.closed


def test_instance_timeout_applies_when_the_call_gives_none(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch,
    piped_stdin: io.StringIO,
) -> None:
    _hang_sandbox_call(fake_edge.page, "eval")
    monkeypatch.setattr(ep, "_now", FakeClock(step=0.01))
    with runtime_factory(timeout=0.02) as rt:
        with pytest.raises(RuntimeError) as info:
            rt.eval("1 + 1")
    assert info.value.error_class == ep.ERR_TIMEOUT  # type: ignore[attr-defined]
    assert "0.02s" in str(info.value)


def test_poisoned_runtime_refuses_further_calls_with_cdp_error(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch,
    piped_stdin: io.StringIO,
) -> None:
    _hang_sandbox_call(fake_edge.page, "run")
    monkeypatch.setattr(ep, "_now", FakeClock(step=0.005))
    with runtime_factory() as rt:
        with pytest.raises(RuntimeError):
            rt.run_code("while True: pass", timeout=0.01)
        with pytest.raises(RuntimeError) as info:
            rt.run_code("print(1)")
        assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
        assert "restart" in info.value.hint  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError) as info2:
            rt.eval("1")
        assert info2.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    # only the hung run reached the page; the refused calls never did
    assert len(_calls(fake_edge.page, "run")) == 0
    assert _calls(fake_edge.page, "eval") == []


def test_refused_call_after_timeout_still_terminates_execution_on_close(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch,
    piped_stdin: io.StringIO,
) -> None:
    _hang_sandbox_call(fake_edge.page, "run")
    monkeypatch.setattr(ep, "_now", FakeClock(step=0.005))
    with runtime_factory() as rt:
        with pytest.raises(RuntimeError):
            rt.run_code("while True: pass", timeout=0.01)
        with pytest.raises(RuntimeError):
            rt.run_code("print(1)")
    assert "Runtime.terminateExecution" in fake_edge.page.methods


def test_unstarted_runtime_raises_cdp_error(vendor_dir: Path) -> None:
    rt = ep.EdgeRuntime(vendor_dir=vendor_dir, on_stdout=None, on_stderr=None)
    with pytest.raises(RuntimeError) as info:
        rt.eval("1")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    rt.close()  # nothing to tear down, must not raise


def test_detached_inspector_surfaces_as_cdp_error(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    page = fake_edge.page
    original = page.respond

    def respond(message: dict[str, Any]) -> list[dict[str, Any]]:
        params = message.get("params") or {}
        if message["method"] == "Runtime.evaluate" and params.get("expression", "").startswith('window.edgepy.call("run"'):
            page.methods.append(message["method"])
            return [{"method": "Inspector.detached", "params": {"reason": "target_closed"}}]
        return original(message)

    page.respond = respond  # type: ignore[method-assign]
    with runtime_factory() as rt:
        with pytest.raises(RuntimeError) as info:
            rt.run_code("x")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    assert "target_closed" in str(info.value)


# ---------------------------------------------------------------------------
# Dialog bridge (input() via window.prompt)


def _dialog(dtype: str) -> dict[str, Any]:
    return {"method": "Page.javascriptDialogOpening", "params": {"type": dtype, "message": "", "url": "http://127.0.0.1/"}}


def test_prompt_dialog_is_answered_from_the_stdin_reader(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    fake_edge.page.pending_events.append(_dialog("prompt"))
    with runtime_factory(stdin="prompt", stdin_reader=lambda: "typed") as rt:
        result = rt.run_code("input()")
    assert result.exit_code == 0
    assert fake_edge.page_ws is not None
    assert _sent_params(fake_edge.page_ws, "Page.handleJavaScriptDialog") == [{"accept": True, "promptText": "typed"}]


def test_prompt_dialog_is_cancelled_when_the_reader_hits_eof(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    fake_edge.page.pending_events.append(_dialog("prompt"))
    with runtime_factory(stdin="prompt", stdin_reader=lambda: None) as rt:
        rt.run_code("input()")
    assert fake_edge.page_ws is not None
    assert _sent_params(fake_edge.page_ws, "Page.handleJavaScriptDialog") == [{"accept": False}]


def test_prompt_dialog_is_cancelled_outside_prompt_mode_without_reading_stdin(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    fake_edge.page.pending_events.append(_dialog("prompt"))
    reads: list[int] = []

    def reader() -> str:
        reads.append(1)
        return "never"

    with runtime_factory(stdin="lines", stdin_reader=reader) as rt:
        rt.run_code("input()")
    assert reads == []
    assert fake_edge.page_ws is not None
    assert _sent_params(fake_edge.page_ws, "Page.handleJavaScriptDialog") == [{"accept": False}]


@pytest.mark.parametrize("dtype", ["alert", "beforeunload"])
def test_alert_style_dialogs_are_accepted_regardless_of_stdin_mode(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, dtype: str, piped_stdin: io.StringIO
) -> None:
    fake_edge.page.pending_events.append(_dialog(dtype))
    with runtime_factory(stdin="none") as rt:
        rt.run_code("pass")
    assert fake_edge.page_ws is not None
    assert _sent_params(fake_edge.page_ws, "Page.handleJavaScriptDialog") == [{"accept": True}]


def test_confirm_dialog_is_dismissed(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    fake_edge.page.pending_events.append(_dialog("confirm"))
    with runtime_factory(stdin="prompt", stdin_reader=lambda: "yes") as rt:
        rt.run_code("pass")
    assert fake_edge.page_ws is not None
    assert _sent_params(fake_edge.page_ws, "Page.handleJavaScriptDialog") == [{"accept": False}]


def test_no_dialog_is_showing_error_does_not_propagate(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    page = fake_edge.page
    original = page.respond

    def respond(message: dict[str, Any]) -> list[dict[str, Any]]:
        if message["method"] == "Page.handleJavaScriptDialog":
            page.methods.append(message["method"])
            return [{"id": message["id"], "error": {"code": -32602, "message": "No dialog is showing"}}]
        return original(message)

    page.respond = respond  # type: ignore[method-assign]
    page.pending_events.append(_dialog("prompt"))
    with runtime_factory(stdin="prompt", stdin_reader=lambda: "typed") as rt:
        result = rt.run_code("input()")
    assert result.exit_code == 0
    assert "Page.handleJavaScriptDialog" in page.methods


# ---------------------------------------------------------------------------
# stdin lines, eval


def test_set_stdin_lines_forwards_the_lines_to_the_page(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    with runtime_factory(stdin="lines") as rt:
        rt.set_stdin_lines(["a\n", "b\n", "tail"])
        assert fake_edge.page.stdin_lines == ["a\n", "b\n", "tail"]
        rt.set_stdin_lines(())
        assert fake_edge.page.stdin_lines == []


def test_eval_returns_json_value_or_repr(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    with runtime_factory() as rt:
        assert rt.eval("1 + 1") == 2
        fake_edge.page.sandbox["eval"] = lambda arg: {"value": None, "repr": "<object object at 0x1>"}
        assert rt.eval("object()") == "<object object at 0x1>"
        fake_edge.page.sandbox["eval"] = lambda arg: {"value": None, "repr": None}
        assert rt.eval("None") is None
    assert [arg["expression"] for arg in _calls(fake_edge.page, "eval")] == ["1 + 1", "object()", "None"]


def test_repl_push_passes_the_vendor_flavor(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    with runtime_factory() as rt:
        assert rt.repl_push("x = 1\n") == {"status": "complete", "error": None, "exit_code": None}
    assert _calls(fake_edge.page, "repl_push") == [{"line": "x = 1\n", "full": True}]


# ---------------------------------------------------------------------------
# Teardown and restart


def test_exit_closes_browser_sockets_and_removes_the_run_dir(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path, piped_stdin: io.StringIO
) -> None:
    run_root = Path(os.environ["EDGEPY_RUN_DIR"])
    with runtime_factory() as rt:
        assert rt.edge is not None
        run_dir = rt.edge.run_dir
        assert run_dir.parent == run_root.resolve()
        assert (run_dir / "owner.json").is_file()
        assert json.loads((run_dir / "owner.json").read_text(encoding="utf-8"))["pid"] == os.getpid()
        assert rt.server is not None and rt.server.port > 0
    assert fake_edge.browser_ws is not None
    assert [m["method"] for m in fake_edge.browser_ws.sent] == ["Browser.close"]
    assert fake_edge.browser_ws.closed
    assert fake_edge.page_ws is not None and fake_edge.page_ws.closed
    assert not run_dir.exists()
    assert fake_edge.process is not None and fake_edge.process.returncode == 0
    assert fake_edge.killed == [], "a cooperative Browser.close must not fall through to taskkill"
    assert rt.cdp is None and rt.edge is None and rt.server is None


def test_close_is_idempotent(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    rt = runtime_factory()
    rt.start()
    rt.start()  # already started: no second boot
    assert fake_edge.page.methods.count("Page.navigate") == 1
    rt.close()
    rt.close()
    assert fake_edge.browser_ws is not None
    assert [m["method"] for m in fake_edge.browser_ws.sent] == ["Browser.close"]


def test_keep_run_dir_leaves_the_profile_on_disk(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    with runtime_factory(keep_run_dir=True) as rt:
        assert rt.edge is not None
        run_dir = rt.edge.run_dir
    assert run_dir.is_dir()


def test_stuck_edge_process_is_killed_after_browser_close(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    with runtime_factory() as rt:
        assert fake_edge.process is not None
        fake_edge.process.wait = lambda timeout=None: None  # type: ignore[method-assign]
    assert fake_edge.killed == [fake_edge.process.pid]


def test_restart_terminates_reloads_and_clears_poison(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path, piped_stdin: io.StringIO
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "s.py").write_text("", encoding="utf-8")
    with runtime_factory() as rt:
        rt.run_file(folder / "s.py")
        rt.load_packages(["numpy"])
        rt._poisoned = True
        rt.restart()
        assert rt._poisoned is False
        # Session setup is replayed into the fresh interpreter: the mount and the package.
        assert rt.loaded_packages == ["numpy"]
        assert [name for name, _ in fake_edge.page.calls].count("mount") == 2
        assert [name for name, _ in fake_edge.page.calls].count("load") == 2
        methods = fake_edge.page.methods
        assert methods.count("Page.navigate") == 2
        assert "Runtime.terminateExecution" in methods
        assert methods.index("Runtime.terminateExecution") < len(methods) - 1 - methods[::-1].index("Page.navigate")
        assert rt.run_code("print(1)").exit_code == 0
        rt.run_file(folder / "s.py")
    assert len(_calls(fake_edge.page, "mount")) == 2, "restart forgets mounts so the folder is mounted again"
    assert len(_calls(fake_edge.page, "info")) == 2


def test_restart_survives_a_failing_terminate_execution(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, piped_stdin: io.StringIO
) -> None:
    page = fake_edge.page
    original = page.respond

    def respond(message: dict[str, Any]) -> list[dict[str, Any]]:
        if message["method"] == "Runtime.terminateExecution":
            page.methods.append(message["method"])
            return [{"id": message["id"], "error": {"code": -32000, "message": "Nothing to terminate"}}]
        return original(message)

    page.respond = respond  # type: ignore[method-assign]
    with runtime_factory() as rt:
        rt.restart()
        assert rt.run_code("pass").exit_code == 0
    assert page.methods.count("Page.navigate") == 2
