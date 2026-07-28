"""Policy loading and canonical Git repository identity resolution."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from .models import DeliveryState, RepoContext
from .utils import canonical_origin, canonical_path, load_json, run_git


PROTECTED_DEFAULTS = ("main", "master", "trunk", "develop", "staging", "production")


def package_root() -> Path:
    override = os.environ.get("CODEX_WORKFLOW_HOOKS_ROOT")
    if override:
        return canonical_path(Path(override))
    return Path(__file__).resolve().parents[2]


def default_data_dir() -> Path:
    override = os.environ.get("CODEX_WORKFLOW_HOOKS_DATA")
    if override:
        return canonical_path(Path(override))
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return canonical_path(Path(base) / "CodexWorkflowHooks" / "data")
    return canonical_path(Path.home() / ".local" / "share" / "codex-workflow-hooks")


def load_global_policy(root: Path | None = None) -> dict[str, Any]:
    policy_path = (root or package_root()) / "policies" / "global.json"
    value = load_json(policy_path)
    if not isinstance(value, dict):
        raise ValueError(f"Invalid or missing global policy: {policy_path}")
    return cast(dict[str, Any], value)


def load_repository_policies(root: Path | None = None) -> dict[str, dict[str, Any]]:
    policies: dict[str, dict[str, Any]] = {}
    repo_dir = (root or package_root()) / "policies" / "repos"
    if not repo_dir.exists():
        return policies
    for path in sorted(repo_dir.glob("*.json")):
        value = load_json(path)
        if not isinstance(value, dict):
            raise ValueError(f"Repository policy must be an object: {path}")
        repository_id = value.get("repository_id")
        if not isinstance(repository_id, str) or not repository_id:
            raise ValueError(f"Repository policy has no repository_id: {path}")
        validate_overlay(value)
        policies[repository_id.lower()] = value
    return policies


def validate_overlay(overlay: dict[str, Any]) -> None:
    """Reject overlay fields that could weaken the global safety core."""
    forbidden = {
        "allow_self_approval",
        "allow_direct_completion",
        "allow_force_push",
        "allow_production_approval",
        "remove_denies",
        "replace_protected_branches",
        "authority",
        "safety",
    }
    present = forbidden.intersection(overlay)
    if present:
        raise ValueError(f"Repository overlay attempts to weaken global policy: {sorted(present)}")
    allowed = {
        "$schema",
        "schema_version",
        "repository_id",
        "name",
        "canonical_origin",
        "azure",
        "protected_branches",
        "validation_profiles",
        "required_validation",
        "delivery_verification",
    }
    unknown = set(overlay).difference(allowed)
    if unknown:
        raise ValueError(f"Repository overlay has unsupported fields: {sorted(unknown)}")
    required = allowed.difference({"$schema", "delivery_verification"})
    missing = required.difference(overlay)
    if missing:
        raise ValueError(f"Repository overlay is missing fields: {sorted(missing)}")
    if overlay.get("schema_version") != 1:
        raise ValueError("Repository overlay schema_version must be 1.")
    for key in ("repository_id", "name", "canonical_origin"):
        if not isinstance(overlay.get(key), str) or not overlay[key]:
            raise ValueError(f"Repository overlay {key} must be a non-empty string.")
    if not str(overlay["canonical_origin"]).startswith("azure://"):
        raise ValueError("Repository overlay canonical_origin must be canonical Azure identity.")
    _validate_string_list(overlay, "protected_branches", require_nonempty=True)
    required_profiles = _validate_string_list(
        overlay,
        "required_validation",
        require_nonempty=True,
    )
    azure = overlay.get("azure")
    if not isinstance(azure, dict) or set(azure) != {
        "organization",
        "project",
        "repository_id",
        "remote_url",
    }:
        raise ValueError("Repository overlay azure identity is invalid.")
    if not all(isinstance(value, str) and value for value in azure.values()):
        raise ValueError("Repository overlay azure values must be non-empty strings.")
    profiles = overlay.get("validation_profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("Repository overlay validation_profiles must be a non-empty array.")
    profile_ids: set[str] = set()
    allowed_profile = {
        "id",
        "description",
        "working_directory",
        "commands",
        "path_globs",
        "evidence_state",
    }
    for profile in profiles:
        if not isinstance(profile, dict) or not set(profile).issubset(allowed_profile):
            raise ValueError("Repository validation profile has unsupported fields.")
        if not {"id", "description", "working_directory", "commands", "evidence_state"}.issubset(
            profile
        ):
            raise ValueError("Repository validation profile is incomplete.")
        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or not profile_id or profile_id in profile_ids:
            raise ValueError("Repository validation profile IDs must be unique strings.")
        profile_ids.add(profile_id)
        _validate_string_list(profile, "commands", require_nonempty=True)
        if "path_globs" in profile:
            _validate_string_list(profile, "path_globs", require_nonempty=True)
        if profile.get("evidence_state") != "validated":
            raise ValueError("Repository validation profile evidence_state must be validated.")
    if not set(required_profiles).issubset(profile_ids):
        raise ValueError("required_validation references an unknown validation profile.")
    if "delivery_verification" in overlay:
        _validate_delivery_verification(overlay["delivery_verification"])


def load_registrations(data_dir: Path | None = None) -> list[dict[str, Any]]:
    value = load_json((data_dir or default_data_dir()) / "registrations.json", [])
    if not isinstance(value, list):
        raise ValueError("registrations.json must contain an array.")
    return [item for item in value if isinstance(item, dict)]


def resolve_repo_context(cwd: Path, data_dir: Path | None = None) -> RepoContext:
    location = canonical_path(cwd)
    identity = _filesystem_git_identity(location)
    if identity is None:
        return RepoContext(
            cwd=location, repo_root=None, git_common_dir=None, origin="", branch="", head=""
        )
    repo_root, common_dir, origin, branch, head, identity_error = identity
    verify_code, verified_head, _ = run_git(repo_root, "rev-parse", "--verify", "HEAD")
    if verify_code != 0 or not verified_head:
        identity_error = "git_identity_unreadable"
    elif not head:
        head = verified_head

    policies = load_repository_policies()
    matching_policy = next(
        (
            value
            for value in policies.values()
            if canonical_origin(str(value.get("canonical_origin", ""))) == origin and origin
        ),
        None,
    )
    expected_managed = matching_policy is not None
    identity_unreadable = bool(identity_error or common_dir is None or not origin or not head)
    registrations = load_registrations(data_dir)
    normalized_common = os.path.normcase(str(common_dir)) if common_dir else ""
    registration = next(
        (
            item
            for item in registrations
            if canonical_origin(str(item.get("origin", ""))) == origin
            and os.path.normcase(str(canonical_path(Path(str(item.get("git_common_dir", ""))))))
            == normalized_common
        ),
        None,
    )
    repository_id = str(
        (registration or {}).get("repository_id")
        or (matching_policy or {}).get("repository_id")
        or ""
    )
    policy_error = ""
    managed = False
    rollout_mode = "global"
    if identity_unreadable:
        expected_managed = True
        policy_error = "git_identity_unreadable"
    elif registration:
        registered_policy = policies.get(repository_id.lower())
        if registered_policy is None:
            policy_error = "registration_policy_missing"
        elif canonical_origin(str(registered_policy.get("canonical_origin", ""))) != origin:
            policy_error = "registration_origin_mismatch"
        else:
            managed = True
            rollout_mode = str(registration.get("rollout_mode", "shadow"))
    elif expected_managed:
        policy_error = "expected_repository_not_registered"

    return RepoContext(
        cwd=location,
        repo_root=repo_root,
        git_common_dir=common_dir,
        origin=origin,
        branch=branch,
        head=head,
        repository_id=repository_id,
        managed=managed,
        expected_managed=expected_managed,
        rollout_mode=rollout_mode,
        policy_error=policy_error,
    )


def effective_policy(context: RepoContext) -> dict[str, Any]:
    base = cast(dict[str, Any], json.loads(json.dumps(load_global_policy())))
    overlays = load_repository_policies()
    overlay = overlays.get(context.repository_id.lower()) if context.repository_id else None
    if not overlay:
        return base
    validate_overlay(overlay)
    base["repository"] = overlay
    protected = set(base.get("protected_branches", PROTECTED_DEFAULTS))
    protected.update(overlay.get("protected_branches", []))
    base["protected_branches"] = sorted(protected)
    return base


def context_as_safe_dict(context: RepoContext) -> dict[str, Any]:
    value = asdict(context)
    for key in ("cwd", "repo_root", "git_common_dir"):
        if value[key] is not None:
            value[key] = str(value[key])
    return value


def _nearest_git_marker(path: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return canonical_path(candidate)
    return None


def _filesystem_git_identity(
    location: Path,
) -> tuple[Path, Path | None, str, str, str, str] | None:
    root = _nearest_git_marker(location)
    if root is None:
        return None
    marker = root / ".git"
    git_dir: Path | None = None
    if marker.is_dir():
        git_dir = canonical_path(marker)
    elif marker.is_file():
        value = _read_small_text(marker)
        match = re.match(r"^\s*gitdir:\s*(.+?)\s*$", value, re.I)
        if match:
            raw = Path(match.group(1))
            git_dir = canonical_path(raw if raw.is_absolute() else root / raw)
    if git_dir is None or not git_dir.is_dir():
        return (root, None, "", "", "", "git_identity_unreadable")
    common_dir = git_dir
    commondir_file = git_dir / "commondir"
    if commondir_file.is_file():
        raw_common = Path(_read_small_text(commondir_file).strip())
        if raw_common:
            common_dir = canonical_path(
                raw_common if raw_common.is_absolute() else git_dir / raw_common
            )
    head_text = _read_small_text(git_dir / "HEAD").strip()
    branch = ""
    head = ""
    if head_text.startswith("ref: "):
        reference = head_text.removeprefix("ref: ").strip()
        branch = reference.removeprefix("refs/heads/")
        head = _read_reference(common_dir, reference)
    elif re.fullmatch(r"[0-9a-fA-F]{40,64}", head_text):
        head = head_text.lower()
    config = _read_small_text(common_dir / "config")
    origin = canonical_origin(_origin_from_config(config))
    error = "" if head_text and common_dir.is_dir() else "git_identity_unreadable"
    return (root, common_dir, origin, branch, head, error)


def _read_reference(common_dir: Path, reference: str) -> str:
    loose = common_dir / Path(reference)
    value = _read_small_text(loose).strip()
    if re.fullmatch(r"[0-9a-fA-F]{40,64}", value):
        return value.lower()
    packed = _read_small_text(common_dir / "packed-refs")
    for line in packed.splitlines():
        if line.startswith(("#", "^")):
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1].strip() == reference:
            return parts[0].strip().lower()
    return ""


def _origin_from_config(config: str) -> str:
    match = re.search(
        r'(?ms)^\s*\[remote\s+"origin"\]\s*$\s*(.*?)(?=^\s*\[|\Z)',
        config,
    )
    if not match:
        return ""
    url = re.search(r"(?m)^\s*url\s*=\s*(.+?)\s*$", match.group(1))
    return url.group(1) if url else ""


def _read_small_text(path: Path, limit: int = 1_000_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def _validate_string_list(
    value: dict[str, Any],
    key: str,
    *,
    require_nonempty: bool,
) -> list[str]:
    items = value.get(key)
    if not isinstance(items, list) or (require_nonempty and not items):
        raise ValueError(f"{key} must be a non-empty array.")
    if not all(isinstance(item, str) and item for item in items):
        raise ValueError(f"{key} values must be non-empty strings.")
    if len(items) != len(set(items)):
        raise ValueError(f"{key} values must be unique.")
    return cast(list[str], items)


def _validate_delivery_verification(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {"https_targets"}:
        raise ValueError("delivery_verification must contain only https_targets.")
    targets = value.get("https_targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("delivery_verification https_targets must be non-empty.")
    identifiers: set[str] = set()
    allowed = {
        "id",
        "url",
        "states",
        "expected_status",
        "commit_header",
        "content_type_prefix",
        "body_sha256",
    }
    required = {
        "id",
        "url",
        "states",
        "expected_status",
        "commit_header",
    }
    for target in targets:
        if (
            not isinstance(target, dict)
            or not required.issubset(target)
            or not set(target).issubset(allowed)
        ):
            raise ValueError("delivery_verification target is invalid.")
        identifier = target.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError("delivery_verification target IDs must be unique strings.")
        identifiers.add(identifier)
        url = target.get("url")
        if not isinstance(url, str) or not url.startswith("https://") or "@" in url or "#" in url:
            raise ValueError("delivery_verification target URL must be credential-free HTTPS.")
        states = _validate_string_list(target, "states", require_nonempty=True)
        if not set(states).issubset(
            {
                DeliveryState.RUNTIME_HEALTHY.value,
                DeliveryState.USER_PATH_VERIFIED.value,
            }
        ):
            raise ValueError("delivery_verification target state is unsupported.")
        if DeliveryState.USER_PATH_VERIFIED.value in states and not re.fullmatch(
            r"[0-9a-fA-F]{64}", str(target.get("body_sha256", ""))
        ):
            raise ValueError("user-path verification requires body_sha256.")
        status = target.get("expected_status")
        if not isinstance(status, int) or not 100 <= status <= 599:
            raise ValueError("delivery_verification expected_status is invalid.")
        header = target.get("commit_header")
        if not isinstance(header, str) or not re.fullmatch(r"[A-Za-z0-9-]+", header):
            raise ValueError("delivery_verification commit_header is invalid.")
