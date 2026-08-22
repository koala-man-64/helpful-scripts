"""Static guarantees about edge_pyodide.py: I/O seams stay single-sited, stdlib-only imports.

These tests read the source text and AST rather than executing anything, so they stay
valid even when the fakes in conftest are bypassed.
"""

from __future__ import annotations

import ast
import inspect
import re
import sys
import time
from pathlib import Path

import pytest

import edge_pyodide as ep

SOURCE_PATH = Path(ep.__file__)
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
LINES = SOURCE.splitlines()

DEF_RE = re.compile(r"^(?:    )?(?:async )?def (\w+)\(")
CLASS_RE = re.compile(r"^class (\w+)\b")


def _enclosing_def(line_index: int) -> tuple[str | None, str | None]:
    """(class name, function name) enclosing a line; (None, None) for module-level statements.

    Walks backwards: a column-0 `def` is a module function, a 4-space `def` is a method (or a
    nested function) whose owner is the next column-0 `class`/`def`; any other column-0
    statement means the hit itself sits at module level.
    """
    func: str | None = None
    for i in range(line_index, -1, -1):
        line = LINES[i]
        if not line.strip():
            continue
        def_match = DEF_RE.match(line)
        if def_match and func is None:
            func = def_match.group(1)
            if not line.startswith("    "):
                return None, func
            continue
        if line[0] in " \t":
            continue
        class_match = CLASS_RE.match(line)
        if class_match:
            return class_match.group(1), func
        if def_match:  # a 4-space def nested inside this module-level function
            return None, def_match.group(1)
        return None, func
    return None, func


def _occurrences(needle: str) -> list[tuple[int, str | None, str | None]]:
    found = []
    for index, line in enumerate(LINES):
        code = line.split("#", 1)[0]
        if needle in code:
            cls, func = _enclosing_def(index)
            found.append((index + 1, cls, func))
    return found


@pytest.mark.parametrize(
    ("needle", "allowed"),
    [
        ("socket.create_connection(", {("WebSocket", "connect")}),
        ("subprocess.Popen(", {(None, "_launch_process")}),
        ("opener.open(", {(None, "_open_url")}),
        (".open(request", {(None, "_open_url")}),
        ("winreg.OpenKey", {(None, "_registry_value")}),
        ("subprocess.run(", {(None, "_kill_tree"), (None, "_kill_edge_using")}),
        ("time.sleep(", set()),                # callers go through the `_sleep` alias
        ("time.monotonic(", set()),            # callers go through the `_now` alias
        ("urllib.request.urlopen(", set()),    # every request goes through _open_url
        ("ctypes.windll", {(None, "_attach_job_object"), (None, "close_edge"), (None, "_pid_alive")}),
    ],
)
def test_io_touchpoints_live_only_in_their_seam(needle: str, allowed: set[tuple[str | None, str | None]]) -> None:
    hits = _occurrences(needle)
    if allowed:
        assert hits, f"{needle!r} no longer appears anywhere - the seam moved or was renamed"
    sites = {(cls, func) for _line, cls, func in hits}
    assert sites <= allowed, f"{needle!r} found outside its seam: {hits}"


def test_seam_helpers_are_module_level_and_replaceable() -> None:
    for name in ("_ws_connect", "_launch_process", "_open_url", "_sleep", "_now", "_kill_tree",
                 "_attach_job_object", "_registry_value", "_runtime_factory"):
        assert callable(getattr(ep, name)), name
    assert ep._sleep is time.sleep
    assert ep._now is time.monotonic
    assert ep._runtime_factory is ep.EdgeRuntime
    # The aliases are plain module-level assignments (so monkeypatch on the module reaches every caller).
    assert [(cls, func) for _l, cls, func in _occurrences("_sleep = time.sleep")] == [(None, None)]
    assert [(cls, func) for _l, cls, func in _occurrences("_now = time.monotonic")] == [(None, None)]


def test_seams_are_called_through_the_module_globals_not_direct_references() -> None:
    # The fakes work only because callers look the seam up on the module at call time.
    for caller, seam in (
        (ep.WebSocket.connect, "socket.create_connection"),
        (ep._launch_edge_once, "_launch_process("),
        (ep.EdgeRuntime.start, "_ws_connect("),
        (ep.close_edge, "_ws_connect("),
        (ep._urlopen, "_open_url("),
        (ep.wait_for_active_port, "_sleep("),
        (ep.close_edge, "_kill_tree("),
        (ep.read_policies, "_registry_value("),
        (ep._cmd_run, "_runtime_factory("),
    ):
        assert seam in inspect.getsource(caller), f"{caller.__name__} must call {seam}"


def test_module_imports_only_the_standard_library() -> None:
    tree = ast.parse(SOURCE, filename=str(SOURCE_PATH))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    non_stdlib = sorted(roots - set(sys.stdlib_module_names))
    assert non_stdlib == [], f"third-party imports are not allowed: {non_stdlib}"
    assert "__future__" in roots and "argparse" in roots


def test_top_level_imports_are_stdlib_and_no_dotenv() -> None:
    tree = ast.parse(SOURCE)
    top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = []
    for node in top_level:
        if isinstance(node, ast.Import):
            names.extend(alias.name.split(".")[0] for alias in node.names)
        else:
            assert node.module is not None
            names.append(node.module.split(".")[0])
    assert set(names) <= set(sys.stdlib_module_names)
    assert "dotenv" not in names
    assert "dotenv" not in SOURCE.lower().replace("python-dotenv", ""), "no .env loading anywhere"


def test_no_environment_reads_at_import_time(monkeypatch: pytest.MonkeyPatch) -> None:
    tree = ast.parse(SOURCE)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)):
            text = ast.get_source_segment(SOURCE, node) or ""
            assert "os.getenv" not in text and "os.environ" not in text, f"env read at import: {text[:80]!r}"


def test_default_pyodide_version_and_binding_name_shape() -> None:
    assert re.match(r"^\d+\.\d+\.\d+$", ep.DEFAULT_PYODIDE_VERSION)
    assert ep.BINDING_NAME.startswith("__")
    assert re.fullmatch(r"[A-Za-z_]\w*", ep.BINDING_NAME), "must be a valid JS identifier"
    assert ep.DEFAULT_PYODIDE_VERSION in ep.__doc__


def test_error_class_constants_are_unique_snake_case_strings() -> None:
    classes = {name: value for name, value in vars(ep).items() if name.startswith("ERR_")}
    assert len(classes) >= 14
    assert all(re.fullmatch(r"[a-z_]+", value) for value in classes.values()), classes
    assert len(set(classes.values())) == len(classes), "error classes must be distinct"
    assert ep.EXIT_TIMEOUT == 124 and ep.EXIT_INTERRUPT == 130


def test_content_type_table_pins_the_wasm_and_js_types() -> None:
    assert ep.CONTENT_TYPES[".wasm"] == "application/wasm"
    assert ep.CONTENT_TYPES[".js"] == ep.CONTENT_TYPES[".mjs"] == "text/javascript"
    assert "mimetypes.guess_type" not in inspect.getsource(ep.LocalServer)


def test_required_dist_files_match_the_pyodide_314_layout() -> None:
    assert set(ep.DIST_REQUIRED_FILES) == {
        "pyodide.js", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json",
    }


def test_python_310_syntax_compatibility_declared_and_honoured() -> None:
    # `from __future__ import annotations` is what lets `X | None` annotations run on 3.10.
    first_statement = next(n for n in ast.parse(SOURCE).body if not isinstance(n, ast.Expr))
    assert isinstance(first_statement, ast.ImportFrom) and first_statement.module == "__future__"
    # Best-effort parser gate for post-3.10 syntax (except*, type statements, PEP 695 generics).
    ast.parse(SOURCE, filename=str(SOURCE_PATH), feature_version=(3, 10))
