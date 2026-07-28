#!/usr/bin/env python3
"""Repository launcher and pre-import integrity bootstrap."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any


sys.dont_write_bytecode = True

ROOT = Path(__file__).absolute().parent
_ALLOWED_RELEASE_ROOTS = {"src", "policies", "schemas"}
_EXPECTED_OWNER = "codex-workflow-hooks"
_MAX_EVENT_BYTES = 2_000_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_release_path(value: str) -> bool:
    if not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    if value == "hookctl.py":
        return True
    return bool(path.parts and path.parts[0] in _ALLOWED_RELEASE_ROOTS)


def _verify_before_import(expected_manifest_digest: str) -> bool:
    manifest_path = ROOT / "manifest.json"
    if (
        not re.fullmatch(r"[0-9a-fA-F]{64}", expected_manifest_digest)
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
        or _sha256(manifest_path).casefold() != expected_manifest_digest.casefold()
    ):
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        return False
    expected_hashes: dict[str, str] = {}
    for item in manifest["files"]:
        if not isinstance(item, dict):
            return False
        relative = str(item.get("path", ""))
        expected_hash = str(item.get("sha256", ""))
        if (
            not _valid_release_path(relative)
            or relative in expected_hashes
            or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        ):
            return False
        expected_hashes[relative] = expected_hash
    expected_directories = {
        parent.as_posix()
        for relative in expected_hashes
        for parent in PurePosixPath(relative).parents
        if parent.as_posix() != "."
    }
    seen_files: set[str] = set()
    for target in ROOT.rglob("*"):
        relative = target.relative_to(ROOT).as_posix()
        if target.is_symlink():
            return False
        if target.is_file():
            if relative == "manifest.json":
                continue
            expected_hash = expected_hashes.get(relative)
            if expected_hash is None or _sha256(target) != expected_hash:
                return False
            seen_files.add(relative)
        elif target.is_dir():
            if relative not in expected_directories:
                return False
        else:
            return False
    return seen_files == set(expected_hashes)


def _option_value(arguments: list[str], option: str) -> str:
    for index, value in enumerate(arguments):
        if value == option and index + 1 < len(arguments):
            return arguments[index + 1]
        if value.startswith(option + "="):
            return value.split("=", 1)[1]
    return ""


def _event_name(arguments: list[str]) -> str:
    try:
        index = arguments.index("event")
    except ValueError:
        return ""
    return arguments[index + 1] if index + 1 < len(arguments) else ""


def _stop_hook_active(event_name: str, raw_event: str) -> bool:
    if event_name != "Stop":
        return False
    try:
        payload = json.loads(raw_event)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("stop_hook_active") is True


def _emit_integrity_failure(event_name: str, *, raw_event: str = "") -> int:
    print("hookctl: pre-import release integrity verification failed", file=sys.stderr)
    result: dict[str, Any] | None = None
    if event_name == "PreToolUse":
        result = {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "RELEASE_INTEGRITY_FAILURE: installed hook runtime failed verification."
                ),
            }
        }
    elif event_name == "Stop" and not _stop_hook_active(event_name, raw_event):
        result = {
            "decision": "block",
            "reason": ("RELEASE_INTEGRITY_FAILURE: central evidence gate is unavailable."),
        }
    if result is not None:
        json.dump(result, sys.stdout, ensure_ascii=True, separators=(",", ":"))
    return 0


def _emit_runtime_failure(event_name: str, code: str, *, raw_event: str = "") -> int:
    result: dict[str, Any] | None = None
    if event_name == "PreToolUse":
        result = {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"{code}: central hook verification failed; mutation safety is unavailable."
                ),
            }
        }
    elif event_name == "Stop" and not _stop_hook_active(event_name, raw_event):
        result = {
            "decision": "block",
            "reason": f"{code}: central evidence gate could not be evaluated.",
        }
    if result is not None:
        json.dump(result, sys.stdout, ensure_ascii=True, separators=(",", ":"))
    return 0


def _run_event(arguments: list[str], event_name: str) -> int:
    owner = _option_value(arguments, "--owner")
    if owner and owner != _EXPECTED_OWNER:
        return _emit_runtime_failure(event_name, "HOOK_OWNER_MISMATCH")
    raw = sys.stdin.read(_MAX_EVENT_BYTES + 1)
    if len(raw) > _MAX_EVENT_BYTES:
        return _emit_runtime_failure(event_name, "HOOK_INPUT_TOO_LARGE", raw_event=raw)
    try:
        import sqlite3

        from codex_workflow_hooks.hooks import handle_event
        from codex_workflow_hooks.policy import default_data_dir

        payload = json.loads(raw)
        configured_data_dir = _option_value(arguments, "--data-dir")
        result = handle_event(
            event_name,
            payload,
            data_dir=Path(configured_data_dir) if configured_data_dir else default_data_dir(),
        )
    except (json.JSONDecodeError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"hookctl: {exc.__class__.__name__}", file=sys.stderr)
        return _emit_runtime_failure(event_name, "HOOK_RUNTIME_FAILURE", raw_event=raw)
    if result:
        json.dump(result, sys.stdout, ensure_ascii=True, separators=(",", ":"))
    return 0


def _bootstrap() -> int:
    arguments = sys.argv[1:]
    expected_digest = _option_value(arguments, "--manifest-digest")
    event_name = _event_name(arguments)
    if expected_digest and not _verify_before_import(expected_digest):
        raw_event = sys.stdin.read(_MAX_EVENT_BYTES + 1) if event_name == "Stop" else ""
        return _emit_integrity_failure(event_name, raw_event=raw_event)
    source = ROOT / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    if event_name:
        return _run_event(arguments, event_name)
    from codex_workflow_hooks.cli import main

    return main(arguments)


if __name__ == "__main__":
    raise SystemExit(_bootstrap())
