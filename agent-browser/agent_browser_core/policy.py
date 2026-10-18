"""Cooperative command permissions; these do not sandbox a hostile local process."""
from __future__ import annotations

from typing import Any
from . import common

READ = frozenset({"ping", "state", "journal", "output", "snapshot", "text", "value", "screenshot", "tabs", "tab", "wait", "poll", "needs-human", "goto", "back", "reload", "stop", "api", "run", "table", "diagnose"})
CONTROL = frozenset({"ping", "state", "journal", "output", "poll", "needs-human", "stop"})


def profile_config(config: dict[str, Any], profile: str) -> dict[str, Any]:
    result = {k: v for k, v in config.items() if k != "profiles"}
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict) or any(not common._is_profile_name(k) or not isinstance(v, dict) for k, v in profiles.items()):
        raise common._tag(RuntimeError("profiles must map valid profile names to policy objects."), common.ERR_CONFIG)
    result.update(profiles.get(profile, {}))
    if result.get("permission", "commit") not in ("read", "act", "commit"):
        raise common._tag(RuntimeError("permission must be read, act or commit."), common.ERR_CONFIG)
    for capability in ("eval", "uploads"):
        if capability in result and not isinstance(result[capability], bool):
            raise common._tag(RuntimeError(f"{capability} must be a boolean."), common.ERR_CONFIG)
    for key in ("allowed_hosts", "upload_roots"):
        if key in result and (not isinstance(result[key], list) or any(not isinstance(v, str) for v in result[key])):
            raise common._tag(RuntimeError(f"{key} must be a list of strings."), common.ERR_CONFIG)
    quota = result.get("evidence_max_bytes", 1024 ** 3)
    if type(quota) is not int or quota < 65536:
        raise common._tag(RuntimeError("evidence_max_bytes must be an integer >= 65536."), common.ERR_CONFIG)
    return result


def authorize(config: dict[str, Any], command: str, args: dict[str, Any]) -> bool:
    level = config.get("permission", "commit")
    resolving = command == "state" and bool(args.get("resolve"))
    if resolving and not args.get("_commit"):
        raise common._tag(ValueError("Human completion requires commit state --handoff ID --resolve."), common.ERR_GUARDED)
    committing = bool(args.get("_commit")) or command == "eval" or bool(args.get("enter")) or (command == "journal" and args.get("clear"))
    if committing and level != "commit":
        raise common._tag(ValueError("This operation requires a commit profile."), common.ERR_GUARDED)
    if level == "read" and command not in READ:
        raise common._tag(ValueError("This profile permits observation only."), common.ERR_GUARDED)
    if command == "eval" and config.get("eval") is False:
        raise common._tag(ValueError("eval is disabled by profile policy."), common.ERR_GUARDED, hint=common._hint("eval_disabled"))
    if config.get("uploads") is False and (command == "upload" or command == "api" and args.get("body_file")):
        raise common._tag(ValueError("File uploads and file-backed API bodies are disabled by profile policy."), common.ERR_GUARDED)
    if command == "api" and str(args.get("method", "GET")).upper() not in ("GET", "HEAD") and not args.get("_commit"):
        raise common._tag(ValueError("Write API calls require commit api."), common.ERR_GUARDED)
    return command not in READ or committing
