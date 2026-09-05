"""Fail-closed semantic checks for the three localized benchmark fixtures."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import sys
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping


TASKS = frozenset({"localized-failed-test", "localized-long-output", "localized-missing-dependency"})
TEST_COMMAND = ("python", "-B", "-m", "unittest", "discover", "-s", "tests")
LOG_COMMAND = ("python", "-B", "generate_log.py")
TIMEOUT_SECONDS = 10


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.relative_to(root).as_posix() != "logs/build.log"
    }


def _changed_files(baseline: Path, workspace: Path) -> set[str]:
    before, after = _file_hashes(baseline), _file_hashes(workspace)
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    """Execute only a fixed evaluator program in an isolated fixture copy."""
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _unittest_result(workspace: Path) -> subprocess.CompletedProcess[str] | None:
    return _run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"], workspace)


def _ran_tests(result: subprocess.CompletedProcess[str] | None) -> bool:
    """Require executed tests with no skipped or expected-failure coverage."""
    if result is None:
        return False
    output = result.stdout + result.stderr
    return bool(re.search(r"Ran [1-9][0-9]* tests?", output)) and not any(
        marker in output for marker in ("skipped=", "expected failures=", "unexpected successes=")
    )


def _child_value(workspace: Path, program: str) -> str | None:
    result = _run([sys.executable, "-B", "-c", program], workspace)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip()


def _test_methods(path: Path) -> dict[str, str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {}
    methods = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for method in node.body:
                if isinstance(method, ast.FunctionDef) and method.name.startswith("test_"):
                    methods[f"{path.stem}.{node.name}.{method.name}"] = ast.dump(method, include_attributes=False)
    return methods


def _coverage_preserved(before: Path, after: Path) -> bool:
    original, produced = _test_methods(before), _test_methods(after)
    if not original or not all(produced.get(name) == body for name, body in original.items()):
        return False
    try:
        baseline = ast.parse(before.read_text(encoding="utf-8"))
        candidate = ast.parse(after.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    original_classes = {node.name for node in baseline.body if isinstance(node, ast.ClassDef)}
    retained = []
    for node in candidate.body:
        if isinstance(node, ast.ClassDef):
            if node.name not in original_classes:
                # Extra test classes can add cases, but cannot replace setup,
                # alter discovery, or mutate the original tests at import time.
                if (node.decorator_list or node.keywords
                        or len(node.bases) != 1 or ast.unparse(node.bases[0]) != "unittest.TestCase"
                        or not node.body or not all(isinstance(method, ast.FunctionDef)
                            and method.name.startswith("test_") and not method.decorator_list
                            for method in node.body)):
                    return False
                continue
            node.body = [method for method in node.body if not (
                isinstance(method, ast.FunctionDef) and method.name.startswith("test_")
                and f"{after.stem}.{node.name}.{method.name}" not in original
            )]
        retained.append(node)
    candidate.body = retained
    # Compare the entire original module, including class bases, decorators,
    # setup/teardown and discovery helpers, after removing only added cases.
    return ast.dump(baseline, include_attributes=False) == ast.dump(candidate, include_attributes=False)


def _new_cases_detect_bug(baseline: Path, workspace: Path, test: str) -> bool:
    original, produced = _test_methods(baseline / test), _test_methods(workspace / test)
    added = sorted(set(produced) - set(original))
    if not _coverage_preserved(baseline / test, workspace / test) or not added:
        return False
    # Execute only the added cases, individually. Existing failing tests cannot
    # lend their failure to a newly added tautology or unrelated assertion.
    with tempfile.TemporaryDirectory(prefix="benchmark-regression-") as temporary:
        old = Path(temporary) / "original-source"
        shutil.copytree(workspace, old, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        for path in (baseline / "src").rglob("*.py"):
            destination = old / path.relative_to(baseline)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
        for name in added:
            program = (
                "import sys,unittest; sys.path.insert(0,'tests'); "
                f"suite=unittest.defaultTestLoader.loadTestsFromName({name!r}); "
                "result=unittest.TextTestRunner().run(suite); "
                "raise SystemExit(not result.wasSuccessful())"
            )
            bad = _run([sys.executable, "-B", "-c", program], old)
            good = _run([sys.executable, "-B", "-c", program], workspace)
            if (bad is None or good is None or bad.returncode != 1 or good.returncode != 0
                    or "FAIL:" not in bad.stderr or "ERROR:" in bad.stderr
                    or not _ran_tests(bad) or not _ran_tests(good)
                    or "skipped=" in good.stderr):
                return False
    return True


def _ast_changed(before: Path, after: Path) -> bool:
    try:
        return ast.dump(ast.parse(before.read_text(encoding="utf-8")), include_attributes=False) != ast.dump(
            ast.parse(after.read_text(encoding="utf-8")), include_attributes=False
        )
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False


def _allowed_read_only(argv: tuple[str, ...], fixture_files: set[str]) -> bool:
    if argv in {("git", "status"), ("git", "diff", "--check")}:
        return True
    if len(argv) != 2 or argv[0] not in {"cat", "Get-Content", "Get-FileHash"}:
        return False
    target = argv[1].replace("\\", "/")
    return target in fixture_files


def _command_evidence(rows: list[dict[str, Any]], fixture_files: set[str], *, need_log: bool) -> tuple[bool, bool]:
    """Accept only normalized records and a fixed shell-free command allowlist."""
    saw_passing_test = False
    saw_log = not need_log
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"type", "argv", "exit_code", "status", "output"}:
            return False, False
        argv, exit_code = row["argv"], row["exit_code"]
        if (
            row["type"] != "command_execution"
            or row["status"] != "completed"
            or not isinstance(argv, list)
            or not all(isinstance(item, str) for item in argv)
            or not isinstance(exit_code, int)
            or isinstance(exit_code, bool)
            or not isinstance(row["output"], str)
        ):
            return False, False
        command = tuple(argv)
        if command == TEST_COMMAND:
            # A recorded reproduction may fail before the fixed run, but only a
            # completed success establishes validation evidence.
            if exit_code not in {0, 1}:
                return False, False
            saw_passing_test |= exit_code == 0
        elif command == LOG_COMMAND:
            if exit_code != 0:
                return False, False
            saw_log = True
        elif _allowed_read_only(command, fixture_files):
            if exit_code != 0:
                return False, False
        else:
            return False, False
    return True, saw_passing_test and saw_log


def _raw_log_is_pinned(baseline: Path, raw_refs: Mapping[str, Any]) -> bool:
    generated = _run([sys.executable, "-B", "generate_log.py"], baseline)
    log = baseline / "logs" / "build.log"
    reference = raw_refs.get("build_log") if isinstance(raw_refs, Mapping) else None
    if generated is None or generated.returncode != 0 or not log.is_file() or not isinstance(reference, Mapping):
        return False
    raw_path = Path(str(reference.get("path", "")))
    if not raw_path.is_file() or reference.get("digest") != _sha256(raw_path):
        return False
    raw = raw_path.read_bytes()
    marker = b"ERROR BUILD417 src/route.py:1"
    return _sha256(raw_path) == _sha256(log) and raw.find(marker) >= 0 and raw.find(b"BUILD417") == raw.find(marker) + 6


def _failed_test_checks(
    baseline: Path, workspace: Path, commands: list[dict[str, Any]]
) -> dict[str, bool]:
    source, test = "src/allocation.py", "tests/test_allocation.py"
    changed = _changed_files(baseline, workspace)
    baseline_run, fixed_run = _unittest_result(baseline), _unittest_result(workspace)
    command_valid, test_evidence = _command_evidence(commands, {source, test}, need_log=False)
    source_changed = _ast_changed(baseline / source, workspace / source)
    genuine_regression = _new_cases_detect_bug(baseline, workspace, test)
    behavior = _child_value(
        workspace,
        "from src.allocation import allocate; assert allocate(11, [1, 1, 1]) == [4, 4, 3]; assert allocate(10, [1, 1]) == [5, 5]",
    ) == ""
    return {
        "narrow_change": changed <= {source, test} and source in changed,
        "regression_test": source_changed and genuine_regression,
        "offline_test_evidence": command_valid and test_evidence and (workspace / test).is_file() and _ran_tests(baseline_run) and baseline_run.returncode == 1 and _ran_tests(fixed_run) and fixed_run.returncode == 0 and behavior,
        "no_unrelated_changes": changed <= {source, test},
    }


def _long_output_checks(
    baseline: Path, workspace: Path, final_text: str, commands: list[dict[str, Any]], raw_refs: Mapping[str, Any]
) -> dict[str, bool]:
    source, test, generator = "src/route.py", "tests/test_routes.py", "generate_log.py"
    changed = _changed_files(baseline, workspace)
    command_valid, test_evidence = _command_evidence(commands, {source, test, generator, "logs/build.log"}, need_log=True)
    fixed_run = _unittest_result(workspace)
    route_value = _child_value(workspace, "from src.route import ROUTES; print(ROUTES.get('ready', ''))")
    raw_ok = _raw_log_is_pinned(baseline, raw_refs)
    return {
        "raw_output_preserved": raw_ok,
        "first_failure_identified": raw_ok and "BUILD417" in final_text,
        "narrow_change": changed <= {source, test} and source in changed,
        "validation_evidence": command_valid and test_evidence and _coverage_preserved(baseline / test, workspace / test) and _ran_tests(fixed_run) and fixed_run.returncode == 0 and route_value == "/ready",
    }


def _missing_dependency_checks(
    baseline: Path, workspace: Path, final_text: str, commands: list[dict[str, Any]]
) -> dict[str, bool]:
    lock, source, test, request = "requirements.txt", "src/supported.py", "tests/test_supported.py", "request.json"
    command_valid, test_evidence = _command_evidence(commands, {lock, source, test, request}, need_log=False)
    fixed_run = _unittest_result(workspace)
    value = _child_value(workspace, "from src.supported import total; print(total('count\\n2\\n3\\n'))")
    lock_unchanged = (baseline / lock).is_file() and (workspace / lock).is_file() and _sha256(baseline / lock) == _sha256(workspace / lock)
    return {
        "no_install": command_valid,
        "lock_unchanged": lock_unchanged,
        "supported_path_used": command_valid and test_evidence and _coverage_preserved(baseline / test, workspace / test) and _ran_tests(fixed_run) and fixed_run.returncode == 0 and value == "5",
        "blocker_or_validation_evidence": command_valid and test_evidence and "supported" in final_text.casefold(),
    }


def evaluate_local(
    task_id: str,
    *,
    baseline: Path,
    workspace: Path,
    final_text: str,
    commands: list[dict[str, Any]],
    raw_refs: Mapping[str, Any],
) -> dict[str, bool]:
    """Evaluate fixed localized fixtures without importing their produced modules."""
    if task_id not in TASKS or not baseline.is_dir() or not workspace.is_dir() or not isinstance(final_text, str):
        return {}
    if task_id == "localized-failed-test":
        return _failed_test_checks(baseline, workspace, commands)
    if task_id == "localized-long-output":
        return _long_output_checks(baseline, workspace, final_text, commands, raw_refs)
    return _missing_dependency_checks(baseline, workspace, final_text, commands)
