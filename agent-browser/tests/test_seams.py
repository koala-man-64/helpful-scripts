"""Static guarantees about agent_browser.py: I/O seams stay single-sited, Playwright stays lazy, hints stay static.

These read the source with `ast` rather than executing anything, so they hold even when the fakes are bypassed.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_browser as ab  # noqa: E402

SOURCE = Path(ab.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _enclosing_functions() -> dict[int, str]:
    """Map every line number to the innermost function that contains it.

    ast.walk is breadth-first, so an inner function is visited after its enclosing one and its
    lines overwrite the outer owner; that is the innermost-wins rule this map relies on.
    """
    owner: dict[int, str] = {}
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for line in range(node.lineno, node.end_lineno + 1):
                owner[line] = node.name
    return owner


OWNER = _enclosing_functions()


def _import_sites(module: str) -> set[str | None]:
    sites: set[str | None] = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Import) and any(a.name == module or a.name.startswith(module + ".") for a in node.names):
            sites.add(OWNER.get(node.lineno))
        if isinstance(node, ast.ImportFrom) and node.module and (node.module == module or node.module.startswith(module + ".")):
            sites.add(OWNER.get(node.lineno))
    return sites


def _call_sites(qualified: str) -> set[str | None]:
    sites: set[str | None] = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if name == qualified:
                sites.add(OWNER.get(node.lineno))
    return sites


def test_playwright_is_imported_only_inside_lazy_seams() -> None:
    assert _import_sites("playwright") == {"_playwright_factory", "_playwright_errors"}


def test_process_and_registry_edges_are_single_sited() -> None:
    assert _call_sites("subprocess.Popen") == {"_spawn_daemon"}
    assert _import_sites("winreg") == {"_registry_value"}
    assert _import_sites("msvcrt") == {"_try_lock", "_release_lock"}
    assert _import_sites("fcntl") == {"_try_lock"}


def test_daemon_server_is_never_threaded() -> None:
    assert "ThreadingMixIn" not in SOURCE
    assert "ThreadingTCPServer" not in SOURCE


def test_hints_are_static_templates() -> None:
    for node in ast.walk(TREE):
        if isinstance(node, ast.keyword) and node.arg == "hint":
            assert not isinstance(node.value, ast.JoinedStr), f"line {node.value.lineno}: hint built from an f-string"
    assert 'hint=f"' not in SOURCE


def test_no_environment_is_read_at_import_time() -> None:
    for node in TREE.body:
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and ast.unparse(child.func) in ("os.getenv", "os.environ.get") and OWNER.get(child.lineno) is None:
                raise AssertionError(f"line {child.lineno}: environment read at import time")


def test_module_docstring_lists_every_env_var_the_code_reads() -> None:
    names = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and ast.unparse(node.func) in ("os.getenv", "_env_flag", "_env_float") and node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str) and value.startswith("AGENT_BROWSER_"):
                names.add(value)
    doc = ast.get_docstring(TREE) or ""
    missing = sorted(n for n in names if n not in doc)
    assert not missing, f"document these in the module docstring: {missing}"


def test_source_is_python_310_syntax() -> None:
    compile(SOURCE, ab.__file__, "exec", flags=0, dont_inherit=True, optimize=0)
    # `match` statements are 3.10+, which is the floor; anything newer (PEP 695 generics, `except*`) is not.
    for node in ast.walk(TREE):
        assert not isinstance(node, (ast.TryStar, ast.TypeAlias)), f"line {node.lineno}: syntax newer than Python 3.10"
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            assert not getattr(node, "type_params", None), f"line {node.lineno}: PEP 695 type parameters need Python 3.12"
