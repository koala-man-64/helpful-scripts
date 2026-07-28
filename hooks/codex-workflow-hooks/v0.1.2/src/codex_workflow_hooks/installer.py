"""Immutable release installation, hook config merge, and repository registration."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

from . import __version__
from .evidence import EvidenceLedger
from .models import HookEvent
from .policy import (
    default_data_dir,
    load_repository_policies,
    package_root,
    resolve_repo_context,
)
from .utils import (
    atomic_write_json,
    canonical_origin,
    canonical_path,
    load_json,
    sha256_file,
    sha256_text,
    stable_json,
)


OWNER = "codex-workflow-hooks"
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){2}(?:[-+][A-Za-z0-9.-]+)?$")
HOOK_TOPOLOGY: tuple[tuple[HookEvent, str | None], ...] = (
    (HookEvent.SESSION_START, "startup|resume|clear|compact"),
    (HookEvent.SUBAGENT_START, None),
    (HookEvent.PRE_TOOL_USE, "Bash|apply_patch"),
    (HookEvent.POST_TOOL_USE, "Bash|apply_patch"),
    (HookEvent.STOP, None),
    (HookEvent.SESSION_END, "other"),
)


def install_release(
    *,
    version: str = __version__,
    codex_home: Path | None = None,
    install_root: Path | None = None,
    default_mode: str = "shadow",
) -> dict[str, Any]:
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"Unsafe release version: {version}")
    if default_mode not in {"shadow", "enforce"}:
        raise ValueError("default_mode must be shadow or enforce.")
    source = package_root()
    target_root = canonical_path(install_root or _default_install_root())
    release = target_root / "releases" / version
    data_dir = target_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    source_entries = _source_manifest(source)
    source_digest = sha256_text(stable_json(source_entries))

    if release.exists():
        manifest = load_json(release / "manifest.json", {})
        if not isinstance(manifest, dict) or manifest.get("source_digest") != source_digest:
            raise FileExistsError(
                f"Immutable release path already exists with different content: {release}"
            )
        existing_verification = verify_release(release)
        if not existing_verification.get("valid"):
            raise FileExistsError(f"Immutable release path failed integrity checks: {release}")
    else:
        staging = release.with_name(f".{release.name}.staging-{os.getpid()}")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            _copy_release_files(source, staging)
            manifest = {
                "schema_version": 1,
                "owner": OWNER,
                "version": version,
                "created_at": datetime.now(UTC).isoformat(),
                "source_digest": source_digest,
                "files": _source_manifest(staging),
            }
            atomic_write_json(staging / "manifest.json", manifest)
            release.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, release)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    manifest_digest = sha256_file(release / "manifest.json")
    release_verification = verify_release(release, manifest_digest)
    if not release_verification.get("valid"):
        raise RuntimeError(f"New release failed integrity checks: {release}")
    home = canonical_path(codex_home or Path.home() / ".codex")
    hook_path = home / "hooks.json"
    interpreter = canonical_path(Path(sys.executable))
    config = load_json(hook_path, {"hooks": {}})
    if not isinstance(config, dict):
        raise ValueError(f"Hook config must be a JSON object: {hook_path}")
    merged = merge_hook_config(
        config,
        release=release,
        interpreter=interpreter,
        manifest_digest=manifest_digest,
        data_dir=data_dir,
    )
    backup = _backup_file(hook_path, data_dir / "backups")
    atomic_write_json(hook_path, merged)
    install_state = {
        "schema_version": 1,
        "owner": OWNER,
        "version": version,
        "release_path": str(release),
        "manifest_digest": manifest_digest,
        "hook_config": str(hook_path),
        "interpreter": str(interpreter),
        "data_dir": str(data_dir),
        "default_mode": default_mode,
        "installed_at": datetime.now(UTC).isoformat(),
        "trust_managed_by_installer": False,
    }
    atomic_write_json(data_dir / "install.json", install_state)
    return {
        "version": version,
        "release_path": str(release),
        "manifest_digest": manifest_digest,
        "hook_config": str(hook_path),
        "backup": str(backup) if backup else "",
        "default_mode": default_mode,
        "trust_required": True,
    }


def merge_hook_config(
    config: dict[str, Any],
    *,
    release: Path,
    interpreter: Path,
    manifest_digest: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    result = cast(dict[str, Any], json.loads(json.dumps(config)))
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks.json field 'hooks' must be an object.")
    for event, matcher in HOOK_TOPOLOGY:
        groups = hooks.setdefault(event.value, [])
        if not isinstance(groups, list):
            raise ValueError(f"hooks.{event.value} must be an array.")
        retained = [group for group in groups if not _owned_group(group)]
        retained.append(
            _hook_group(
                event=event,
                matcher=matcher,
                release=release,
                interpreter=interpreter,
                manifest_digest=manifest_digest,
                data_dir=data_dir or default_data_dir(),
            )
        )
        hooks[event.value] = retained
    return result


def uninstall_hooks(
    *,
    codex_home: Path | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    home = canonical_path(codex_home or Path.home() / ".codex")
    hook_path = home / "hooks.json"
    config = load_json(hook_path, {})
    if not isinstance(config, dict):
        raise ValueError(f"Hook config must be a JSON object: {hook_path}")
    hooks = config.get("hooks", {})
    removed = 0
    if isinstance(hooks, dict):
        for event in list(hooks):
            groups = hooks[event]
            if not isinstance(groups, list):
                continue
            retained = []
            for group in groups:
                if _owned_group(group):
                    removed += 1
                else:
                    retained.append(group)
            if retained:
                hooks[event] = retained
            else:
                del hooks[event]
    storage = data_dir or default_data_dir()
    backup = _backup_file(hook_path, storage / "backups")
    atomic_write_json(hook_path, config)
    return {
        "removed_groups": removed,
        "hook_config": str(hook_path),
        "backup": str(backup) if backup else "",
        "releases_preserved": True,
    }


def register_repository(
    path: Path,
    repository_id: str,
    *,
    rollout_mode: str = "shadow",
    data_dir: Path | None = None,
) -> dict[str, Any]:
    if rollout_mode not in {"shadow", "canary", "enforce"}:
        raise ValueError("rollout_mode must be shadow, canary, or enforce.")
    storage = data_dir or default_data_dir()
    context = resolve_repo_context(path, storage)
    if context.repo_root is None or context.git_common_dir is None:
        raise ValueError(f"Not a Git repository: {path}")
    policies = load_repository_policies()
    overlay = policies.get(repository_id.lower())
    if overlay is None:
        raise ValueError(f"No immutable repository policy for {repository_id}.")
    expected_origin = canonical_origin(str(overlay.get("canonical_origin", "")))
    if context.origin != expected_origin:
        raise ValueError(
            f"Origin mismatch for {repository_id}: expected {expected_origin}, got {context.origin}"
        )
    registrations_path = storage / "registrations.json"
    registrations = load_json(registrations_path, [])
    if not isinstance(registrations, list):
        raise ValueError("registrations.json must contain an array.")
    common = str(canonical_path(context.git_common_dir))
    retained = [
        value
        for value in registrations
        if not (
            isinstance(value, dict)
            and os.path.normcase(str(value.get("git_common_dir", ""))) == os.path.normcase(common)
        )
    ]
    entry = {
        "repository_id": repository_id,
        "origin": expected_origin,
        "git_common_dir": common,
        "rollout_mode": rollout_mode,
        "registered_at": datetime.now(UTC).isoformat(),
    }
    retained.append(entry)
    atomic_write_json(registrations_path, sorted(retained, key=_registration_sort_key))
    EvidenceLedger(storage).record_registration(
        repository_id,
        expected_origin,
        context.git_common_dir,
        rollout_mode,
    )
    return entry


def verify_release(release: Path, expected_digest: str = "") -> dict[str, Any]:
    if not release.is_dir() or release.is_symlink():
        return {"valid": False, "error": "release_path_invalid"}
    manifest_path = release / "manifest.json"
    if not manifest_path.exists():
        return {"valid": False, "error": "manifest_missing"}
    manifest_digest = sha256_file(manifest_path)
    if expected_digest and manifest_digest.lower() != expected_digest.lower():
        return {"valid": False, "error": "manifest_digest_mismatch"}
    manifest = load_json(manifest_path, {})
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        return {"valid": False, "error": "manifest_invalid"}
    failures: list[str] = []
    invalid_entries: list[str] = []
    expected_files: set[str] = set()
    for item in manifest["files"]:
        if not isinstance(item, dict):
            invalid_entries.append("<invalid-entry>")
            continue
        relative = str(item.get("path", ""))
        expected = str(item.get("sha256", ""))
        if not _valid_manifest_path(relative) or relative in expected_files:
            invalid_entries.append(relative or "<empty-path>")
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            invalid_entries.append(relative)
            continue
        expected_files.add(relative)
        path = release.joinpath(*PurePosixPath(relative).parts)
        if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
            failures.append(relative)
    allowed_paths = {"manifest.json", *expected_files}
    expected_directories = {
        parent.as_posix()
        for relative in expected_files
        for parent in PurePosixPath(relative).parents
        if parent.as_posix() != "."
    }
    unexpected_paths: list[str] = []
    for path in release.rglob("*"):
        relative = path.relative_to(release).as_posix()
        if path.is_symlink():
            unexpected_paths.append(relative)
        elif path.is_file() and relative not in allowed_paths:
            unexpected_paths.append(relative)
        elif path.is_dir() and relative not in expected_directories:
            unexpected_paths.append(relative + "/")
    return {
        "valid": not failures and not invalid_entries and not unexpected_paths,
        "manifest_digest": manifest_digest,
        "version": manifest.get("version", ""),
        "failures": failures,
        "invalid_manifest_entries": sorted(set(invalid_entries)),
        "unexpected_paths": sorted(set(unexpected_paths)),
    }


def verify_hook_config(
    config: Any,
    *,
    release: Path,
    interpreter: Path,
    manifest_digest: str,
    data_dir: Path,
) -> dict[str, Any]:
    """Verify the exact owned topology while preserving unrelated hook groups."""
    failures: list[str] = []
    owned_events: list[str] = []
    if not isinstance(config, dict):
        return {
            "valid": False,
            "owned_events": owned_events,
            "failures": ["hook_config_not_object"],
        }
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return {
            "valid": False,
            "owned_events": owned_events,
            "failures": ["hooks_not_object"],
        }

    expected_events = {event.value for event, _ in HOOK_TOPOLOGY}
    for event, matcher in HOOK_TOPOLOGY:
        groups = hooks.get(event.value)
        if not isinstance(groups, list):
            failures.append(f"{event.value}:groups_not_array")
            continue
        owned = [group for group in groups if _owned_group(group)]
        if owned:
            owned_events.append(event.value)
        expected = _hook_group(
            event=event,
            matcher=matcher,
            release=release,
            interpreter=interpreter,
            manifest_digest=manifest_digest,
            data_dir=data_dir,
        )
        if len(owned) != 1:
            failures.append(f"{event.value}:owned_group_count_{len(owned)}")
        elif owned[0] != expected:
            failures.append(f"{event.value}:owned_group_mismatch")

    for event_name, groups in hooks.items():
        if event_name in expected_events or not isinstance(groups, list):
            continue
        if any(_owned_group(group) for group in groups):
            failures.append(f"{event_name}:unexpected_owned_event")

    return {
        "valid": not failures,
        "owned_events": owned_events,
        "failures": failures,
    }


def _hook_group(
    *,
    event: HookEvent,
    matcher: str | None,
    release: Path,
    interpreter: Path,
    manifest_digest: str,
    data_dir: Path,
) -> dict[str, Any]:
    launcher = release / "hookctl.py"
    arguments = (
        f'"{interpreter}" -I -S -B "{launcher}" --owner {OWNER} '
        f'--manifest-digest {manifest_digest} event {event.value} --data-dir "{data_dir}"'
    )
    handler = {
        "type": "command",
        "command": arguments,
        "commandWindows": arguments,
        "timeout": 10,
        "statusMessage": f"Codex workflow: {event.value}",
    }
    group: dict[str, Any] = {"hooks": [handler]}
    if matcher is not None:
        group["matcher"] = matcher
    return group


def _owned_group(group: Any) -> bool:
    if not isinstance(group, dict):
        return False
    handlers = group.get("hooks", [])
    if not isinstance(handlers, list):
        return False
    for handler in handlers:
        if not isinstance(handler, dict):
            continue
        commands = (
            str(handler.get("command", "")),
            str(handler.get("commandWindows", "")),
        )
        if any(_command_has_owner_marker(command) for command in commands):
            return True
    return False


def _command_has_owner_marker(command: str) -> bool:
    try:
        tokens = [
            token[1:-1]
            if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\""
            else token
            for token in shlex.split(command, posix=False)
        ]
    except ValueError:
        return False
    return any(
        token == "--owner" and index + 1 < len(tokens) and tokens[index + 1] == OWNER
        for index, token in enumerate(tokens)
    )


def _valid_manifest_path(value: str) -> bool:
    if not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    if value == "hookctl.py":
        return True
    return bool(path.parts and path.parts[0] in {"src", "policies", "schemas"})


def _copy_release_files(source: Path, target: Path) -> None:
    shutil.copy2(source / "hookctl.py", target / "hookctl.py")
    for directory in ("src", "policies", "schemas"):
        source_dir = source / directory
        if source_dir.exists():
            shutil.copytree(
                source_dir,
                target / directory,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )


def _source_manifest(root: Path) -> list[dict[str, str]]:
    included = ("hookctl.py", "src", "policies", "schemas")
    paths: list[Path] = []
    for name in included:
        path = root / name
        if path.is_file():
            paths.append(path)
        elif path.is_dir():
            paths.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and "__pycache__" not in candidate.parts
                and candidate.suffix != ".pyc"
            )
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
        }
        for path in sorted(paths)
    ]


def _backup_file(path: Path, backup_dir: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = backup_dir / f"{path.name}.{stamp}.bak"
    shutil.copy2(path, backup)
    return backup


def _registration_sort_key(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        return ("~", stable_json(value))
    return (str(value.get("repository_id", "")), str(value.get("git_common_dir", "")))


def _default_install_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "CodexWorkflowHooks"
    return Path.home() / ".local" / "share" / "codex-workflow-hooks"
