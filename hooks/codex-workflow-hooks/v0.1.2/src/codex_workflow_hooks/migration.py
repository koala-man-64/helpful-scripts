"""Read-only fleet inventory and explicit legacy-hook retirement helpers."""

from __future__ import annotations

import os
import stat
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable

from .policy import PROTECTED_DEFAULTS, load_global_policy
from .utils import (
    canonical_origin,
    canonical_path,
    is_within,
    run_git,
    sha256_text,
    stable_json,
)


@dataclass
class FleetEntry:
    root: str
    origin: str
    git_common_dir: str
    branch: str
    head: str
    status_readable: bool
    dirty: bool
    ahead: int
    behind: int
    working_hook_files: int
    head_hook_files: int
    origin_main_hook_files: int
    origin_main_head: str
    working_hooks_absent_from_head: bool
    collision: bool = False
    quarantine_reasons: tuple[str, ...] = ()


def audit_fleet(search_roots: Iterable[Path]) -> dict[str, object]:
    normalized_search_roots = sorted(
        {str(canonical_path(root)) for root in search_roots},
        key=os.path.normcase,
    )
    candidates = _discover_hook_roots(Path(root) for root in normalized_search_roots)
    entries = []
    invalid_candidates = 0
    for root in candidates:
        code, discovered, _ = run_git(root, "rev-parse", "--show-toplevel")
        if code != 0 or canonical_path(Path(discovered)) != root:
            invalid_candidates += 1
            continue
        entries.append(_audit_root(root))
    groups: dict[tuple[str, str], list[FleetEntry]] = {}
    for entry in entries:
        if entry.origin and entry.branch:
            groups.setdefault((entry.origin, entry.branch.lower()), []).append(entry)
    for group in groups.values():
        common_dirs = {os.path.normcase(entry.git_common_dir) for entry in group}
        if len(group) > 1 and len(common_dirs) > 1:
            for entry in group:
                entry.collision = True
    _apply_quarantine_reasons(entries)
    payload = []
    for entry in entries:
        value = asdict(entry)
        value["quarantine_reasons"] = list(entry.quarantine_reasons)
        payload.append(value)
    result: dict[str, object] = {
        "schema_version": 1,
        "search_roots": normalized_search_roots,
        "roots": payload,
        "summary": {
            "candidate_roots": len(entries),
            "invalid_or_nested_candidates": invalid_candidates,
            "quarantined": sum(bool(entry.quarantine_reasons) for entry in entries),
            "dirty": sum(entry.dirty for entry in entries),
            "detached_or_unknown": sum(not entry.branch for entry in entries),
            "status_unreadable": sum(not entry.status_readable for entry in entries),
            "collisions": sum(entry.collision for entry in entries),
            "working_hooks_absent_from_head": sum(
                entry.working_hooks_absent_from_head for entry in entries
            ),
            "origin_main_unavailable": sum(not entry.origin_main_head for entry in entries),
            "head_not_origin_main": sum(
                bool(entry.origin_main_head and entry.head != entry.origin_main_head)
                for entry in entries
            ),
            "distinct_git_common_dirs": len(
                {entry.git_common_dir for entry in entries if entry.git_common_dir}
            ),
            "distinct_origins": len({entry.origin for entry in entries if entry.origin}),
        },
    }
    result["receipt_digest"] = _audit_receipt_digest(result)
    return result


def retire_legacy_hooks(
    path: Path,
    *,
    apply: bool = False,
    audit_receipt: dict[str, object] | None = None,
) -> dict[str, object]:
    root = canonical_path(path)
    code, discovered, error = run_git(root, "rev-parse", "--show-toplevel")
    if code != 0 or canonical_path(Path(discovered)) != root:
        raise ValueError(f"Path must be an exact Git root: {error or path}")
    current = _audit_root(root)
    _apply_quarantine_reasons([current])
    if current.quarantine_reasons:
        raise ValueError(
            "Legacy retirement blocked by audit quarantine: "
            + ", ".join(current.quarantine_reasons)
        )
    if _is_protected_branch(current.branch):
        raise ValueError("Legacy retirement requires a task branch; protected branch matched.")

    receipt_verified = False
    if audit_receipt is not None:
        _verify_audit_receipt(audit_receipt, current)
        receipt_verified = True
    if apply and not receipt_verified:
        raise ValueError("Legacy retirement apply requires a fresh fleet audit receipt.")

    _, tracked, _ = run_git(
        root,
        "ls-files",
        "--",
        ".codex/hooks.json",
        ".codex/hooks",
    )
    relative_paths = [
        value.strip()
        for value in tracked.splitlines()
        if value.strip() == ".codex/hooks.json" or value.strip().startswith(".codex/hooks/")
    ]
    targets = [_legacy_hook_target(root, value) for value in relative_paths]
    for target in targets:
        _assert_safe_delete_target(root, target)

    deleted = 0
    if apply:
        for target in targets:
            _assert_safe_delete_target(root, target)
            target.unlink()
            deleted += 1
        hooks_dir = root / ".codex" / "hooks"
        if (
            hooks_dir.is_dir()
            and not _is_link_or_reparse(hooks_dir)
            and not any(hooks_dir.iterdir())
        ):
            hooks_dir.rmdir()
    return {
        "root": str(root),
        "branch": current.branch,
        "origin": current.origin,
        "apply": apply,
        "targets": relative_paths,
        "deleted": deleted,
        "audit_receipt_verified": receipt_verified,
        "audit_receipt_required_for_apply": not receipt_verified,
    }


def _discover_hook_roots(search_roots: Iterable[Path]) -> list[Path]:
    roots: set[Path] = set()
    excluded = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"}
    for search_root in search_roots:
        start = canonical_path(search_root)
        if not start.is_dir():
            continue
        for current, directories, files in os.walk(start, topdown=True, onerror=lambda _: None):
            directories[:] = [name for name in directories if name not in excluded]
            current_path = Path(current)
            if current_path.name == ".codex" and ("hooks.json" in files or "hooks" in directories):
                roots.add(canonical_path(current_path.parent))
                directories[:] = []
    return sorted(roots, key=lambda path: os.path.normcase(str(path)))


def _audit_root(root: Path) -> FleetEntry:
    status_code, status, _ = run_git(root, "status", "--porcelain=v2", "--branch", timeout=8)
    root_code, discovered, _ = run_git(root, "rev-parse", "--show-toplevel")
    exact_root = root_code == 0 and canonical_path(Path(discovered)) == root
    status_readable = status_code == 0 and exact_root
    branch = ""
    head = ""
    ahead = 0
    behind = 0
    dirty = False
    if status_readable:
        for line in status.splitlines():
            if line.startswith("# branch.head "):
                value = line.removeprefix("# branch.head ").strip()
                branch = "" if value == "(detached)" else value
            elif line.startswith("# branch.oid "):
                head = line.removeprefix("# branch.oid ").strip()
            elif line.startswith("# branch.ab "):
                parts = line.split()
                ahead = int(parts[2].lstrip("+"))
                behind = int(parts[3].lstrip("-"))
            elif not line.startswith("#"):
                dirty = True
    common = ""
    if exact_root:
        common_code, raw_common, _ = run_git(root, "rev-parse", "--git-common-dir")
        if common_code == 0 and raw_common:
            common_path = Path(raw_common)
            common = str(
                canonical_path(common_path if common_path.is_absolute() else root / common_path)
            )
    working = _working_hook_files(root)
    head_hooks = _tree_hook_count(root, "HEAD") if exact_root else 0
    origin_main = _tree_hook_count(root, "refs/remotes/origin/main") if exact_root else 0
    origin_main_head = ""
    if exact_root:
        origin_code, raw_origin_main, _ = run_git(
            root,
            "rev-parse",
            "--verify",
            "refs/remotes/origin/main",
        )
        if origin_code == 0:
            origin_main_head = raw_origin_main
    absent = False
    if exact_root:
        for target in working:
            relative = target.relative_to(root).as_posix()
            code, _, _ = run_git(root, "cat-file", "-e", f"HEAD:{relative}")
            if code != 0:
                absent = True
                break
    return FleetEntry(
        root=str(root),
        origin=_origin(root) if exact_root else "",
        git_common_dir=common,
        branch=branch,
        head=head,
        status_readable=status_readable,
        dirty=dirty,
        ahead=ahead,
        behind=behind,
        working_hook_files=len(working),
        head_hook_files=head_hooks,
        origin_main_hook_files=origin_main,
        origin_main_head=origin_main_head,
        working_hooks_absent_from_head=absent,
    )


def _apply_quarantine_reasons(entries: Iterable[FleetEntry]) -> None:
    for entry in entries:
        reasons: list[str] = []
        if not entry.status_readable:
            reasons.append("status_unreadable")
        if not entry.branch:
            reasons.append("detached_or_unknown")
        if entry.dirty:
            reasons.append("dirty")
        if entry.ahead:
            reasons.append("ahead")
        if entry.behind:
            reasons.append("behind")
        if not entry.origin_main_head:
            reasons.append("origin_main_unavailable")
        elif entry.head != entry.origin_main_head:
            reasons.append("head_not_origin_main")
        if entry.working_hooks_absent_from_head:
            reasons.append("working_hooks_absent_from_head")
        if entry.collision:
            reasons.append("remote_branch_collision")
        entry.quarantine_reasons = tuple(reasons)


def _working_hook_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    config = root / ".codex" / "hooks.json"
    if config.is_file():
        paths.append(config)
    hooks_dir = root / ".codex" / "hooks"
    if hooks_dir.is_dir():
        paths.extend(
            path
            for path in hooks_dir.rglob("*.py")
            if path.is_file() and "__pycache__" not in path.parts
        )
    return paths


def _tree_hook_count(root: Path, treeish: str) -> int:
    code, output, _ = run_git(
        root,
        "ls-tree",
        "-r",
        "--name-only",
        treeish,
        "--",
        ".codex/hooks.json",
        ".codex/hooks",
    )
    return len(output.splitlines()) if code == 0 and output else 0


def _origin(root: Path) -> str:
    _, value, _ = run_git(root, "config", "--get", "remote.origin.url")
    return canonical_origin(value)


def _is_protected_branch(branch: str) -> bool:
    try:
        policy = load_global_policy()
    except (OSError, ValueError) as exc:
        raise ValueError("Protected branch policy is unavailable.") from exc
    patterns = {value.casefold() for value in PROTECTED_DEFAULTS}
    configured = policy.get("protected_branches", [])
    if not isinstance(configured, list) or not all(isinstance(value, str) for value in configured):
        raise ValueError("Protected branch policy is invalid.")
    patterns.update(value.casefold() for value in configured)
    value = branch.casefold().removeprefix("refs/heads/")
    return any(fnmatchcase(value, pattern) for pattern in patterns)


def _audit_receipt_digest(receipt: dict[str, object]) -> str:
    payload = {
        "schema_version": receipt.get("schema_version"),
        "search_roots": receipt.get("search_roots"),
        "roots": receipt.get("roots"),
        "summary": receipt.get("summary"),
    }
    return sha256_text(stable_json(payload))


def _verify_audit_receipt(receipt: dict[str, object], current: FleetEntry) -> None:
    if receipt.get("schema_version") != 1:
        raise ValueError("Fleet audit receipt schema is invalid.")
    expected_digest = receipt.get("receipt_digest")
    if not isinstance(expected_digest, str) or expected_digest != _audit_receipt_digest(receipt):
        raise ValueError("Fleet audit receipt digest is invalid.")
    search_roots = receipt.get("search_roots")
    if not isinstance(search_roots, list) or not search_roots:
        raise ValueError("Fleet audit receipt has no search roots.")
    if not any(
        isinstance(value, str) and is_within(Path(current.root), canonical_path(Path(value)))
        for value in search_roots
    ):
        raise ValueError("Fleet audit receipt does not cover the retirement root.")
    roots = receipt.get("roots")
    if not isinstance(roots, list):
        raise ValueError("Fleet audit receipt roots are invalid.")
    matching = [
        value
        for value in roots
        if isinstance(value, dict)
        and isinstance(value.get("root"), str)
        and canonical_path(Path(str(value["root"]))) == Path(current.root)
    ]
    if len(matching) != 1:
        raise ValueError("Fleet audit receipt does not contain one exact retirement root.")
    recorded = matching[0]
    reasons = recorded.get("quarantine_reasons")
    if recorded.get("collision") is not False or reasons != []:
        detail = ", ".join(str(value) for value in reasons) if isinstance(reasons, list) else ""
        raise ValueError(f"Fleet audit receipt quarantines this root: {detail or 'collision'}")
    current_value: dict[str, Any] = asdict(current)
    current_value["quarantine_reasons"] = list(current.quarantine_reasons)
    snapshot_fields = (
        "root",
        "origin",
        "git_common_dir",
        "branch",
        "head",
        "status_readable",
        "dirty",
        "ahead",
        "behind",
        "working_hook_files",
        "head_hook_files",
        "origin_main_hook_files",
        "origin_main_head",
        "working_hooks_absent_from_head",
        "collision",
        "quarantine_reasons",
    )
    stale = [field for field in snapshot_fields if recorded.get(field) != current_value[field]]
    if stale:
        raise ValueError("Fleet audit receipt is stale for: " + ", ".join(stale))


def _legacy_hook_target(root: Path, relative: str) -> Path:
    value = PurePosixPath(relative)
    allowed = value == PurePosixPath(".codex/hooks.json") or (
        len(value.parts) > 2 and value.parts[:2] == (".codex", "hooks")
    )
    if value.is_absolute() or ".." in value.parts or not allowed:
        raise ValueError(f"Legacy hook target is outside the allowed paths: {relative}")
    target = root.joinpath(*value.parts)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Legacy hook target escaped the repository: {relative}") from exc
    return target


def _assert_safe_delete_target(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Legacy hook target escaped the repository: {target}") from exc
    current = root
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            details = os.lstat(current)
        except OSError as exc:
            raise ValueError(f"Legacy hook target is unreadable: {current}") from exc
        if stat.S_ISLNK(details.st_mode) or _has_reparse_attribute(details):
            raise ValueError(f"Legacy hook target contains a symlink or reparse point: {current}")
        is_target = index == len(relative.parts) - 1
        if is_target and not stat.S_ISREG(details.st_mode):
            raise ValueError(f"Legacy hook target is not a regular file: {current}")
        if not is_target and not stat.S_ISDIR(details.st_mode):
            raise ValueError(f"Legacy hook target has a non-directory parent: {current}")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        details = os.lstat(path)
    except OSError:
        return True
    return stat.S_ISLNK(details.st_mode) or _has_reparse_attribute(details)


def _has_reparse_attribute(details: os.stat_result) -> bool:
    attributes = int(getattr(details, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return bool(reparse and attributes & reparse)
