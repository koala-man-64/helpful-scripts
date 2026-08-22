"""Regressions for defects found in the adversarial review (mounts, capture, fetch, CLI)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

import edge_pyodide as ep
from conftest import FakeEdgeEnv


# ---------------------------------------------------------------------------
# mount collisions


def test_two_folders_with_the_same_basename_both_reach_the_sandbox(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path
) -> None:
    a = tmp_path / "libs" / "common"
    b = tmp_path / "vendor" / "common"
    for folder in (a, b):
        folder.mkdir(parents=True)
        (folder / "m.py").write_text("", encoding="utf-8")
    with runtime_factory() as rt:
        first = rt.mount(a)
        second = rt.mount(b)
        assert first == "/mnt/common"
        assert second.startswith("/mnt/common_") and second != first
        assert [arg["dest"] for name, arg in fake_edge.page.calls if name == "mount"] == [first, second]
        assert rt.mount(b) == second, "re-mounting the same folder is a no-op"


def test_explicit_mount_name_collision_is_a_validation_error(
    runtime_factory: Callable[..., ep.EdgeRuntime], tmp_path: Path
) -> None:
    a = tmp_path / "x" / "lib"
    b = tmp_path / "y" / "lib"
    for folder in (a, b):
        folder.mkdir(parents=True)
    with runtime_factory() as rt:
        rt.mount(a, "lib")
        with pytest.raises(ValueError) as info:
            rt.mount(b, "lib")
        assert getattr(info.value, "error_class") == ep.ERR_VALIDATION
        assert "lib" in str(info.value)


def test_script_folder_is_still_mounted_when_an_explicit_mount_took_its_name(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, tmp_path: Path
) -> None:
    other = tmp_path / "other" / "examples"
    proj = tmp_path / "proj" / "examples"
    other.mkdir(parents=True)
    proj.mkdir(parents=True)
    (proj / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    with runtime_factory() as rt:
        rt.mount(other)
        rt.run_file(proj / "hello.py")
        (run_arg,) = [arg for name, arg in fake_edge.page.calls if name == "run"]
        assert run_arg["path"].startswith("/mnt/examples_") and run_arg["path"].endswith("/hello.py")
        assert run_arg["cwd"] == run_arg["path"].rsplit("/", 1)[0]


# ---------------------------------------------------------------------------
# capture


def test_truncated_is_reported_per_run_not_latched() -> None:
    sink = ep._StreamSink(None, cap=4)
    sink.feed(b"0123")
    sink.feed(b"456789")  # over the cap: dropped from the capture, flagged
    text, truncated = sink.take()
    assert text == "0123" and truncated is True
    sink.feed(b"ok")
    assert sink.take() == ("ok", False)


def test_json_timeout_envelope_keeps_partial_output(
    runtime_factory: Callable[..., ep.EdgeRuntime], fake_edge: FakeEdgeEnv, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], vendor_dir: Path,
) -> None:
    monkeypatch.setenv("EDGEPY_VENDOR_DIR", str(vendor_dir))
    page = fake_edge.page
    original = page.respond

    def respond(message: dict[str, Any]) -> list[dict[str, Any]]:
        params = message.get("params") or {}
        if message["method"] == "Runtime.evaluate" and str(params.get("expression", "")).startswith('window.edgepy.call("run"'):
            page.methods.append(message["method"])
            return [{"method": "Runtime.bindingCalled", "params": {"name": ep.BINDING_NAME,
                     "payload": json.dumps({"s": "o", "b": "cGFydGlhbAo="})}}]  # "partial\n"
        return original(message)

    page.respond = respond  # type: ignore[method-assign]
    clock = {"t": 1000.0}

    def now() -> float:
        clock["t"] += 0.001  # several pump iterations fit before the 0.01 s deadline
        return clock["t"]

    monkeypatch.setattr(ep, "_now", now)
    assert ep.main(["run", "--json", "--timeout", "0.01", "-c", "while True: pass"]) == ep.EXIT_TIMEOUT
    out, err = capsys.readouterr()
    envelope = json.loads(out)
    assert envelope["exit_code"] == ep.EXIT_TIMEOUT
    assert envelope["stdout"] == "partial\n"
    assert json.loads(err)["error"]["class"] == ep.ERR_TIMEOUT


def test_negative_timeout_is_rejected(runtime_factory: Callable[..., ep.EdgeRuntime], capsys: pytest.CaptureFixture[str]) -> None:
    assert ep.main(["run", "--timeout", "-1", "-c", "pass"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["class"] == ep.ERR_VALIDATION


# ---------------------------------------------------------------------------
# mount zip robustness


def test_unreadable_file_is_skipped_instead_of_failing_the_mount(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "ok.py").write_text("", encoding="utf-8")
    (folder / "locked.py").write_text("", encoding="utf-8")
    real_write = ep.zipfile.ZipFile.write

    def write(self: Any, filename: Any, arcname: Any = None, *args: Any, **kwargs: Any) -> None:
        if str(filename).endswith("locked.py"):
            raise PermissionError(13, "Permission denied", str(filename))
        real_write(self, filename, arcname, *args, **kwargs)

    monkeypatch.setattr(ep.zipfile.ZipFile, "write", write)
    data, count = ep.build_mount_zip(folder)
    assert count == 1
    with ep.zipfile.ZipFile(ep.io.BytesIO(data)) as zf:
        assert zf.namelist() == ["ok.py"]


# ---------------------------------------------------------------------------
# fetch: requirements files and resolver bookkeeping


def test_requirements_file_follows_includes_and_reports_other_directives(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "base.txt").write_text("six>=1.0  # comment\n", encoding="utf-8")
    (tmp_path / "req.txt").write_text(
        "-r base.txt\n--index-url https://internal/simple\n-e .\ntabulate\n\n# only a comment\n", encoding="utf-8"
    )
    unresolved: list[dict[str, str]] = []
    reqs = ep.read_requirements_file(tmp_path / "req.txt", unresolved)
    assert [r.name for r in reqs] == ["six", "tabulate"]
    assert reqs[0].specifier == ">=1.0"
    assert [u["name"] for u in unresolved] == ["--index-url https://internal/simple", "-e ."]
    assert "ignoring directive" in capsys.readouterr().err


def test_requirements_file_include_cycle_terminates(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("-r b.txt\nsix\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("-r a.txt\ntabulate\n", encoding="utf-8")
    reqs = ep.read_requirements_file(tmp_path / "a.txt", [])
    assert sorted(r.name for r in reqs) == ["six", "tabulate"]


def _index(projects: dict[str, dict[str, Any]]) -> Callable[[str], Any]:
    def fetch_json(url: str) -> Any:
        for key, value in projects.items():
            if url.endswith(f"/pypi/{key}/json"):
                return value
        raise RuntimeError(f"404 {url}")

    return fetch_json


def _project(name: str, version: str, requires: list[str]) -> dict[str, Any]:
    filename = f"{name}-{version}-py3-none-any.whl"
    file = {"packagetype": "bdist_wheel", "filename": filename, "url": f"https://files/{filename}",
            "yanked": False, "requires_python": ">=3.8", "digests": {"sha256": "ab" * 32}}
    return {"info": {"name": name, "version": version, "requires_dist": requires}, "releases": {version: [file]}}


def test_resolver_reports_conflicting_constraints_instead_of_dropping_them() -> None:
    index = _index({
        "app": _project("app", "1.0", ["lib>=2.0", "plug"]),
        "plug": _project("plug", "1.0", ["lib<2.0"]),
        "lib": _project("lib", "2.5", []),
    })
    chosen, unresolved = ep.resolve_wheels([ep.parse_requirement("app")], "3.14.2", set(), fetch_json=index)
    assert chosen["lib"].version == "2.5"
    assert any(u["name"] == "lib" and "conflicting constraint" in u["reason"] for u in unresolved)


def test_resolver_honours_extras_requested_after_the_package_was_chosen() -> None:
    index = _index({
        "pkg": _project("pkg", "1.0", ['fast ; extra == "speed"']),
        "fast": _project("fast", "1.0", []),
    })
    chosen, _ = ep.resolve_wheels([ep.parse_requirement("pkg")], "3.14.2", set(), fetch_json=index)
    assert "fast" not in chosen
    chosen, _ = ep.resolve_wheels(
        [ep.parse_requirement("pkg"), ep.parse_requirement("pkg[speed]")], "3.14.2", set(), fetch_json=index
    )
    assert "fast" in chosen
