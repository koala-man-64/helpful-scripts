"""Cooperative command permissions; these do not sandbox a hostile local process."""
from __future__ import annotations

import re
from typing import Any
from . import common

READ = frozenset({"ping", "state", "journal", "output", "snapshot", "text", "value", "screenshot", "tabs", "tab", "wait", "poll", "needs-human", "goto", "back", "reload", "stop", "api", "run", "table", "diagnose"})
CONTROL = frozenset({"ping", "state", "journal", "output", "poll", "needs-human", "stop"})
PERMISSIONS = ("read", "act", "commit")
# A key under `hosts`: a bare host name or IPv4 address. No scheme, port, path or wildcard: like an allowed_hosts
# entry, a rule also covers the host's subdomains.
HOST_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?)*\Z")


def _config_error(message: str) -> Exception:
    return common._tag(RuntimeError(message), common.ERR_CONFIG)


def _check_capabilities(rule: dict[str, Any], where: str = "") -> None:
    if rule.get("permission", "commit") not in PERMISSIONS:
        raise _config_error(f"{where}permission must be read, act or commit.")
    for capability in ("eval", "uploads"):
        if capability in rule and not isinstance(rule[capability], bool):
            raise _config_error(f"{where}{capability} must be a boolean.")


def _host_rules(value: Any, where: str) -> dict[str, dict[str, Any]]:
    """Validated `hosts`: {host: {permission?, eval?, uploads?}}, host names in lower case."""
    if not isinstance(value, dict):
        raise _config_error(f"{where} must map host names to rule objects.")
    rules: dict[str, dict[str, Any]] = {}
    for raw, rule in value.items():
        host = raw.strip().lower() if isinstance(raw, str) else ""
        shown = repr(common.sanitize_text(str(raw), 60, single_line=True))
        if not HOST_NAME_RE.match(host):
            raise _config_error(f"{where}: {shown} is not a bare host name. Write it without a scheme, port, path or wildcard; "
                                "a rule also covers the host's subdomains.")
        if host in rules:
            raise _config_error(f"{where}: {shown} is listed twice.")
        if not isinstance(rule, dict):
            raise _config_error(f"{where}.{host} must be an object that sets permission, eval or uploads.")
        common._check_policy_keys(rule, common.HOST_RULE_KEYS, f"{where}.{host}")
        _check_capabilities(rule, f"{where}.{host}: ")
        rules[host] = dict(rule)
    return rules


def profile_config(config: dict[str, Any], profile: str) -> dict[str, Any]:
    result = {k: v for k, v in config.items() if k != "profiles"}
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict) or any(not common._is_profile_name(k) or not isinstance(v, dict) for k, v in profiles.items()):
        raise common._tag(RuntimeError("profiles must map valid profile names to policy objects."), common.ERR_CONFIG)
    # Every profile is checked, not only the one in use: a typo in any of them is a restriction that is not applied.
    common._check_policy_keys(result, common.PROFILE_POLICY_KEYS, "The policy")
    if "hosts" in result:
        _host_rules(result["hosts"], "hosts")
    for name, overrides in profiles.items():
        common._check_policy_keys(overrides, common.PROFILE_POLICY_KEYS, f"profiles.{name}")
        if "hosts" in overrides:
            _host_rules(overrides["hosts"], f"profiles.{name}.hosts")
    result.update(profiles.get(profile, {}))
    _check_capabilities(result)
    if "hosts" in result:
        result["hosts"] = _host_rules(result["hosts"], "hosts")
    for key in ("allowed_hosts", "upload_roots"):
        if key in result and (not isinstance(result[key], list) or any(not isinstance(v, str) for v in result[key])):
            raise common._tag(RuntimeError(f"{key} must be a list of strings."), common.ERR_CONFIG)
    if "allowed_hosts" in result:
        result["allowed_hosts"] = common._normalize_allowed_hosts(result["allowed_hosts"])
    quota = result.get("evidence_max_bytes", 1024 ** 3)
    if type(quota) is not int or quota < 65536:
        raise common._tag(RuntimeError("evidence_max_bytes must be an integer >= 65536."), common.ERR_CONFIG)
    retention = result.get("evidence_retention_days")
    if retention is not None and (type(retention) is not int or retention < 1):
        raise _config_error("evidence_retention_days must be an integer >= 1; omit it to keep evidence until it is cleared.")
    if "max_chars" in result and (type(result["max_chars"]) is not int or result["max_chars"] < 0):
        raise common._tag(RuntimeError("max_chars must be an integer >= 0."), common.ERR_CONFIG)
    return result


def for_host(config: dict[str, Any], host: str) -> dict[str, Any]:
    """The policy for a page on `host`: the profile's, with the most specific matching `hosts` rule laid over it.
    `_host_rule` names the rule that applied. A host with no rule gets the profile's policy unchanged."""
    rules = config.get("hosts") or {}
    host = (host or "").lower().rstrip(".")
    matching = [name for name in rules if host == name or host.endswith("." + name)]
    if not matching:
        return config
    best = max(matching, key=len)
    return {**config, **rules[best], "_host_rule": best}


def _guarded(config: dict[str, Any], message: str, hint: str | None = None) -> Exception:
    """A refusal that says when it comes from a host rule rather than from the profile's own level."""
    if config.get("_host_rule"):
        message += f" The hosts rule for {config['_host_rule']} in config.json applies to this page."
    return common._tag(ValueError(message), common.ERR_GUARDED, hint=hint)


def authorize(config: dict[str, Any], command: str, args: dict[str, Any]) -> bool:
    level = config.get("permission", "commit")
    resolving = command == "state" and bool(args.get("resolve"))
    if resolving and not args.get("_commit"):
        raise common._tag(ValueError("Human completion requires commit state --handoff ID --resolve."), common.ERR_GUARDED)
    if command == "journal" and args.get("clear") and not args.get("_commit"):
        raise common._tag(ValueError("Clearing evidence requires commit journal --clear."), common.ERR_GUARDED, hint=common._hint("commit_clear"))
    committing = bool(args.get("_commit")) or command == "eval" or bool(args.get("enter")) or (command == "journal" and args.get("clear"))
    if committing and level != "commit":
        raise _guarded(config, "This operation requires a commit profile.")
    paginating = command == "table" and bool(args.get("next"))
    if level == "read" and (command not in READ or paginating):
        raise _guarded(config, "This profile permits observation only.")
    if command == "eval" and config.get("eval") is False:
        raise _guarded(config, "eval is disabled by profile policy.", common._hint("eval_disabled"))
    if config.get("uploads") is False and (command == "upload" or command == "api" and args.get("body_file")):
        raise _guarded(config, "File uploads and file-backed API bodies are disabled by profile policy.")
    if command == "api" and str(args.get("method", "GET")).upper() not in ("GET", "HEAD") and not args.get("_commit"):
        raise common._tag(ValueError("Write API calls require commit api."), common.ERR_GUARDED)
    return command not in READ or committing or paginating
