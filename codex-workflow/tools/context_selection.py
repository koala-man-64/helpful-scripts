"""Read-only compact selection of existing Git, task, dependency and evidence rows.

Selection is a view, not an evidence ledger or a mutation authorization. Revalidate
mutable state immediately before a mutation; central ownership checks still apply.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

STAGES = {"source", "ci", "release", "deployment", "runtime", "user_path"}
KINDS = {"task", "ownership", "dependency", "pending_operation", "constraint", "acceptance", "evidence"}
DEPENDENCY_FILES = ("uv.lock", "poetry.lock", "requirements.txt", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "packages.lock.json", "Directory.Packages.props", "global.json")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(repository), *arguments], capture_output=True, check=False)
    if result.returncode:
        raise ValueError(f"Git state unavailable: {' '.join(arguments)} (exit {result.returncode})")
    return result.stdout.decode("utf-8", errors="surrogateescape").strip()


def _file_state(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {"path": str(path.resolve()), "bytes": len(content), "sha256": "sha256:" + hashlib.sha256(content).hexdigest()}


def capture_git_state(repository: Path) -> dict[str, Any]:
    repository = repository.resolve(strict=True)
    root = Path(_git(repository, "rev-parse", "--show-toplevel")).resolve()
    common = Path(_git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    git_dir = Path(_git(repository, "rev-parse", "--absolute-git-dir")).resolve()
    dependencies = []
    # Only known dependency lock/config names already tracked by Git. No broad
    # filesystem walk and no changes to business dependencies or worktrees.
    tracked = _git(root, "ls-files", "-z").split("\0")
    for name in tracked:
        if name and Path(name).name in DEPENDENCY_FILES:
            path = root / name
            dependencies.append(_file_state(path) if path.is_file() else {"path": str(path), "missing": True})
    lock_paths = set(git_dir.glob("*.lock")) | set(common.glob("*.lock"))
    refs = common / "refs"
    if refs.is_dir():
        lock_paths.update(refs.rglob("*.lock"))
    operations = []
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "BISECT_LOG"):
        if (git_dir / name).exists():
            operations.append(name)
    state = {
        "repository": str(root), "requested_directory": str(repository),
        "worktree": str(root), "git_directory": str(git_dir),
        "branch": _git(root, "branch", "--show-current") or None,
        "head": _git(root, "rev-parse", "HEAD"),
        "status": _git(root, "status", "--porcelain=v1", "-z"),
        "worktrees": _git(root, "worktree", "list", "--porcelain"),
        "git_locks": [_file_state(path) for path in sorted(lock_paths) if path.is_file()],
        "dependencies": dependencies, "pending_git_operations": operations,
        "ownership": None,
    }
    state["state_digest"] = digest(state)
    return state


def revalidate_git_state(previous: dict[str, Any]) -> list[str]:
    current = capture_git_state(Path(previous["requested_directory"]))
    if previous.get("compact"):
        return [] if previous.get("state_digest") == current["state_digest"] else ["Git/worktree/locks/dependencies changed; expand and revalidate current state"]
    return [f"{key} changed; expand current state and repeat ownership/policy checks" for key in current if key != "state_digest" and current[key] != previous.get(key)]


def load_records(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = path.read_bytes()
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("existing records must be a JSON array; no ledger is created")
    seen = set()
    for row in parsed:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or row.get("kind") not in KINDS:
            raise ValueError("each record needs a stable id and a supported kind")
        if row["id"] in seen:
            raise ValueError("duplicate record IDs cannot provide unambiguous expansion")
        seen.add(row["id"])
        if row["kind"] == "evidence" and row.get("stage") not in STAGES:
            raise ValueError("evidence must name one explicit source/ci/release/deployment/runtime/user_path stage")
    return parsed, {"path": str(path.resolve()), "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}


def select_context(records: list[dict[str, Any]], query: str = "", limit: int = 15) -> dict[str, Any]:
    if not 10 <= limit <= 20:
        raise ValueError("compact row limit must be between 10 and 20")
    terms = set(query.casefold().split())
    required = {index for index, row in enumerate(records) if row.get("kind") in {"ownership", "pending_operation", "constraint", "acceptance"} or row.get("blocking") is True}
    ordered = sorted(range(len(records)), key=lambda index: (-len(terms & set(json.dumps(records[index], ensure_ascii=False).casefold().split())), index))
    selected = set(required)
    for index in ordered:
        if len(selected) >= limit:
            break
        selected.add(index)
    # Retain full grammar and ordering. Never summarize away an urgent negation,
    # acceptance condition, human gate, or continuation dependency to fit a cap.
    return {"rows": [records[index] for index in sorted(selected)], "available_rows": len(records), "omitted_ids": [row["id"] for index, row in enumerate(records) if index not in selected], "limit": limit, "required_rows_exceed_limit": len(required) > limit, "expansion": "Use --expand ID with the same records file; verify its digest before reuse."}


def build_context(repository: Path, records_path: Path | None = None, *, query: str = "", limit: int = 15) -> dict[str, Any]:
    state = capture_git_state(repository)
    records, source = load_records(records_path) if records_path else ([], None)
    selected = select_context(records, query, limit)
    receipts = {stage: [row["id"] for row in selected["rows"] if row.get("kind") == "evidence" and row.get("stage") == stage] for stage in sorted(STAGES)}
    compact = {key: state[key] for key in ("repository", "requested_directory", "worktree", "branch", "head", "state_digest", "ownership", "pending_git_operations")}
    compact.update({"compact": True, "status_digest": digest(state["status"]), "dirty_entries": len([x for x in state["status"].split("\0") if x]), "git_lock_count": len(state["git_locks"]), "git_locks": [row["path"] for row in state["git_locks"]], "dependencies": state["dependencies"][:10], "dependency_count": len(state["dependencies"]), "dependencies_digest": digest(state["dependencies"]), "worktree_count": sum(line.startswith("worktree ") for line in state["worktrees"].splitlines()), "worktrees_digest": digest(state["worktrees"]), "expansion": "Use --expand-git to inspect all current status, worktrees, locks and dependency hashes."})
    return {"schema_version": "compact-context/v1", "git": compact, "records_source": source, "selection": selected, "stage_receipts": receipts, "mutation_ready": False, "before_mutation": "Re-read this Git snapshot, dependency files and ownership/pending records immediately before each mutation; a cached snapshot does not authorize writes."}


def revalidate_context(context: dict[str, Any], *, verify_ownership: Callable[[list[dict[str, Any]]], bool] | None = None) -> list[str]:
    errors = revalidate_git_state(context["git"])
    source = context.get("records_source")
    if source:
        _, current = load_records(Path(source["path"]))
        if current != source:
            errors.append("task/ownership/dependency/evidence records changed; reselect context")
    ownership = [row for row in context["selection"]["rows"] if row.get("kind") == "ownership"]
    if not ownership or any(row.get("status") != "held" or row.get("authority") not in {"central_hooks", "agentcoord"} or not row.get("owner") for row in ownership):
        errors.append("ownership remains unknown or unresolved; obtain current authoritative ownership before mutation")
    elif verify_ownership is None:
        errors.append("held ownership records require a fresh authoritative ownership check before mutation")
    else:
        try:
            if verify_ownership(ownership) is not True:
                errors.append("current authoritative ownership check did not confirm ownership")
        except Exception:
            errors.append("current authoritative ownership check failed")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--records", type=Path)
    parser.add_argument("--query", default="")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--expand", action="append", default=[])
    parser.add_argument("--expand-git", action="store_true")
    parser.add_argument("--revalidate", type=Path)
    args = parser.parse_args(argv)
    if args.revalidate:
        errors = revalidate_context(json.loads(args.revalidate.read_text(encoding="utf-8")))
        print(json.dumps({"errors": errors, "mutation_authorization": False}))
        return 1 if errors else 0
    if args.expand_git:
        result = capture_git_state(args.repository)
    elif args.expand:
        if not args.records:
            parser.error("--expand requires --records")
        records, source = load_records(args.records)
        if set(args.expand) - {row["id"] for row in records}:
            parser.error("unknown expansion ID")
        result = {"records_source": source, "rows": [row for row in records if row["id"] in args.expand]}
    else:
        result = build_context(args.repository, args.records, query=args.query, limit=args.limit)
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
