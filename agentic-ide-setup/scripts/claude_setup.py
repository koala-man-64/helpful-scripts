"""Capture this machine's Claude Code setup and recreate it on another machine.

export   Refresh profile/claude/CLAUDE.md from ~/.claude and write snapshot/claude/:
         portable settings, user-scope MCP servers, the Docker MCP gateway's servers
         and the git repositories the settings run code from, with the hook release
         pinned to the commit this machine runs. Agents and skills are
         repository-owned, so export only checks them for drift.
bundle   Write auto-memory (~/.claude/projects/*/memory) to a private zip outside any
         git work tree. This repository is public, so memory never goes in it.
restore  Preview by default. --apply shows the same plan, asks for confirmation, then
         recreates the setup: profile files, repositories, plugins, MCP servers, the
         settings merge, Docker MCP servers, agent-browser and memory, then runs the
         drift checks.

Standard library only, Python 3.10+. The snapshot is public and it is executable
configuration, so export refuses anything credential-shaped or machine-specific and
restore refuses any snapshot outside the shapes export writes. Credentials are never
read or written.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Iterator, Mapping

SCRIPTS_DIR = Path(__file__).resolve().parent
BUNDLE_DIR = SCRIPTS_DIR.parent
REPO_DIR = BUNDLE_DIR.parent
PROFILE_DIR = BUNDLE_DIR / "profile" / "claude"
SNAPSHOT_DIR = BUNDLE_DIR / "snapshot" / "claude"
MANIFEST_PATH = BUNDLE_DIR / "setup-manifest.json"

SETTINGS_FILE = "settings.template.json"
MCP_FILE = "mcp-servers.template.json"
REPOS_FILE = "repositories.json"
DOCKER_FILE = "docker-mcp-servers.txt"
SNAPSHOT_FILES = (SETTINGS_FILE, MCP_FILE, REPOS_FILE, DOCKER_FILE)

# Install-AgenticIdeSetup.ps1 uses the same suffix, and check_claude_drift.py skips it.
BACKUP_TAG = ".agentic-ide-setup-backup-"
BUNDLE_FORMAT = 1
MAX_BUNDLE_BYTES = 64 * 1024 * 1024

# Snapshot JSON keeps each spelling of the home directory, so every rule round-trips
# exactly: native C:\Users\x, C:/Users/x, and the /c/Users/x form Claude Code matches
# Read and Edit rules against.
HOME_MARKER = "__USERPROFILE__"
SLASH_MARKER = "__USERPROFILE_SLASH__"
POSIX_MARKER = "__USERPROFILE_POSIX__"
SNAPSHOT_MARKERS = (HOME_MARKER, SLASH_MARKER, POSIX_MARKER)
MARKER_TOKEN = re.compile(r"__[A-Z][A-Z_]*__")
HOME_TOKEN = re.compile(r"__USERPROFILE(?:_SLASH|_POSIX)?__")
# Machine-wide folders are written as %Name% and read from the restoring machine.
SYSTEM_FOLDERS = ("ProgramFiles(x86)", "ProgramFiles", "ProgramData", "SystemRoot")
SYSTEM_TOKEN = re.compile("%(" + "|".join(re.escape(name) for name in SYSTEM_FOLDERS) + ")%")
AFTER_PATH = r"(?=[\\/\"'\s)*]|$)"
MACHINE_PATHS = (
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),                      # C:\ and C:/
    re.compile(r"(?<![A-Za-z0-9])/[A-Za-z]/Users/", re.IGNORECASE),      # /c/Users/
    re.compile(r"(?<![A-Za-z0-9])/(?:mnt|cygdrive)/[A-Za-z]/", re.IGNORECASE),  # WSL and Cygwin
)
QUOTED = re.compile(r'"([^"]*)"')

# Restore clones only these: the hook release, and folders directly under ~/Projects.
HOOKS_CLONE = HOME_MARKER + "\\.claude\\hooks-release"
PROJECTS_DIR = HOME_MARKER + "\\Projects"
# Restore applies only the permission keys export captures; a bypass default never travels.
PERMISSION_KEYS = ("allow", "deny", "ask", "additionalDirectories", "defaultMode", "disableBypassPermissionsMode")
REFUSED_MODES = ("bypassPermissions",)

# Restore passes snapshot values to git and claude as arguments, so each one must look
# like what export writes: nothing that starts with "-", no ".." and no shell characters.
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
SAFE_PLUGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*")
SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
SAFE_COMMIT = re.compile(r"[0-9a-f]{40}")
SAFE_URL = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~/-]*)?"
                      r"|(?:ssh://)?git@[A-Za-z0-9.-]+[:/][A-Za-z0-9._~/-]+")
SAFE_FILTER = re.compile(r"[a-z]+(?::[A-Za-z0-9=._-]+)?")
SAFE_SPARSE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./ -]*")
GITHUB_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*")

# Credential detection. Names split on separators and camelCase, so authToken, GH_PAT
# and X-Functions-Key all match; values are checked for the shapes credentials take.
NAME_PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
CREDENTIAL_PARTS = frozenset({"token", "secret", "secrets", "password", "passwd", "pwd", "passphrase",
                              "credential", "credentials", "pat", "apikey", "key", "authorization",
                              "cookie", "bearer", "sas", "signature"})
CREDENTIAL_PHRASES = ("apikey", "accesskey", "privatekey", "clientsecret", "connectionstring", "accesstoken",
                      "authtoken", "refreshtoken", "sessiontoken", "password", "secret", "credential")
# ${NAME} is a reference that the restoring machine resolves, not a credential. The scan
# sees it as $REF, so a literal beside it is still checked and "$" values count as variables.
ENV_REFERENCE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")
REFERENCE_VALUE = re.compile(r"(?i)(?:(?:bearer|basic|token)\s+)?(?:\$\{[A-Za-z_][A-Za-z0-9_]*\})+")
KEY_ARGUMENTS = "access|account|subscription|storage|sas|signing|encryption|master|private|secret|client|api"
SECRET_SHAPES = (
    ("a URL with embedded credentials", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s\"'@]+@")),
    ("a credential in a URL query", re.compile(
        r"(?i)[?&][a-z0-9_.-]*(?:key|token|secret|password|passwd|pwd|sig|signature|auth|credential|sas)=[^&\s\"']")),
    ("a credential passed as an argument", re.compile(
        r"(?i)(?:^|[\s\"'(])--?(?:[a-z0-9]+[-_])*(?:token|apikey|(?:" + KEY_ARGUMENTS + r")[-_]?key|password|passwd|pwd"
        r"|secret|pat|auth|credential)(?:=|\s+)(?=[^\s\"'$%-])")),
    ("a user and password passed to curl",
     re.compile(r"(?i)\bcurl\b[^\n]*?\s(?:-u|--user)(?:\s+|=)[^\s:\"']+:(?![$%])[^\s\"']+")),
    ("a password passed to sshpass", re.compile(r"(?i)\bsshpass\s+-p\s*(?![$%])\S+")),
    ("a password attached to -p", re.compile(r"(?i:\b(?:mysql|mysqldump|mysqladmin|mariadb)\b)[^\n]*?\s-p(?![\s$%-])\S+")),
    ("a credential header", re.compile(
        r"(?i)\b[a-z0-9-]*(?:authorization|api-key|apikey|-key|-token|-secret|-auth)\s*:\s*"
        r"(?!(?:bearer|basic|token)\s+[$%])(?:(?:bearer|basic|token)\s+)?(?![\s$%])\S")),
    ("a known token format", re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[abeoprs]-[A-Za-z0-9-]{10,}"
        r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|glpat-[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})")),
    ("an email address", re.compile(
        r"(?<![A-Za-z0-9._%+-])(?!git@)[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
)
AUTH_SCHEME = re.compile(r"(?i)\b(?:bearer|basic)\s+([A-Za-z0-9._~+/=-]{16,})")
# NAME=value at the start of a command, the way allow rules record a one-off secret.
ASSIGNMENT = re.compile(r"(?:^|[\s(;&|])([A-Za-z_][A-Za-z0-9_]*)=([^\s\"')]+)")
TOKEN_RUN = re.compile(r"[A-Za-z0-9+=]{24,}")  # "/" ends a run, so long paths are not tokens
# Base64 keeps "/" and "+": AWS secret keys and Azure account keys. A run counts when it
# mixes letter cases and digits and its pieces are longer than folder names usually are.
BASE64_RUN = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{32,}={0,2}(?![A-Za-z0-9+/=])")
# Test-SafeProfileContent's rule from ProfileTools.psm1: a credential-named key assigned
# a long literal. Every file export writes must also pass the PowerShell bundle's check.
ASSIGNED_SECRET = re.compile(
    r"(?im)^\s*['\"]?[A-Za-z0-9_.-]*(?:api_key|token|secret|password|credential|authorization)"
    r"[A-Za-z0-9_.-]*['\"]?\s*[:=]\s*['\"][A-Za-z0-9_./+=-]{20,}['\"]")

# What happens to each top-level entry of ~/.claude. Anything not listed is reported,
# so a new kind of configuration cannot be left behind silently.
HOME_ENTRIES = {
    "CLAUDE.md": "captured: profile/claude/CLAUDE.md",
    "agents": "repository-owned: profile/claude/agents (drift-checked)",
    "skills": "repository-owned: profile/claude/skills (drift-checked; skills/synced is managed by claude.ai)",
    "settings.json": "captured: snapshot/claude/settings.template.json",
    "hooks-release": "recreated: release clone from snapshot/claude/repositories.json",
    "plugins": "recreated: marketplaces and plugins from the settings",
    "projects": "auto-memory goes in the private bundle; session transcripts stay here",
}
LEFT_BEHIND = {
    ".credentials.json": "sign-in credentials; sign in again",
    "history.jsonl": "prompt history",
    "backups": "backups", "cache": "cache", "chrome": "runtime state", "daemon": "runtime state",
    "daemon.log": "log", "downloads": "downloads", "file-history": "session state",
    "ide": "runtime state", "jobs": "runtime state", "logs": "logs", "paste-cache": "cache",
    "plans": "session state", "session-env": "session state", "session-flags": "session state",
    "sessions": "session state", "shell-snapshots": "session state", "statsig": "cache",
    "telemetry": "cache", "todos": "session state", "bridge-spawn": "runtime state",
    "worktrees": "session state", "state": "this machine's task state",
    "waits": "this machine's task state", "task-notes": "this machine's task state",
    "scheduled-tasks": "this machine's scheduled tasks",
    "gh-pr-status-cache.json": "cache", "policy-limits.json": "server policy cache",
}
LEFT_BEHIND_PREFIXES = (".last-", "daemon-auth", "policy-limits.json")


class SetupError(Exception):
    """A problem that stops the command; the message says what to fix."""


def _load_drift_module():
    spec = importlib.util.spec_from_file_location("check_claude_drift", SCRIPTS_DIR / "check_claude_drift.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drift = _load_drift_module()


# --- portable paths -------------------------------------------------------------------

def home_forms(home: Path) -> dict[str, str]:
    """Each spelling of the home directory that settings use, keyed by its marker."""
    native = str(home).rstrip("\\/")
    forms = {HOME_MARKER: native}
    if len(native) >= 2 and native[1] == ":":
        forms[SLASH_MARKER] = native.replace("\\", "/")
        forms[POSIX_MARKER] = "/" + native[0].lower() + native[2:].replace("\\", "/")
    return forms


def _replace_path(text: str, value: str, token: str) -> str:
    pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(value) + AFTER_PATH, re.IGNORECASE)
    return pattern.sub(lambda _: token, text)


def env_value(environ: Mapping[str, str], name: str) -> str:
    """Windows variable names ignore case, and dict(os.environ) upper-cases them."""
    value = environ.get(name)
    if value is None:
        value = next((item for key, item in environ.items() if key.lower() == name.lower()), None)
    return (value or "").rstrip("\\/")


def portable_text(text: str, home: Path, environ: Mapping[str, str]) -> str:
    for token, value in home_forms(home).items():
        text = _replace_path(text, value, token)
    folders = [(name, env_value(environ, name)) for name in SYSTEM_FOLDERS]
    for name, value in sorted(folders, key=lambda item: -len(item[1])):
        if len(value) > 3:  # never a bare drive root
            text = _replace_path(text, value, f"%{name}%")
    return text


def expand_text(text: str, home: Path, environ: Mapping[str, str]) -> str:
    forms = home_forms(home)

    def home_value(match: re.Match) -> str:
        if match.group(0) not in forms:
            raise SetupError(f"{match.group(0)} has no value for home {home}")
        return forms[match.group(0)]

    def system_value(match: re.Match) -> str:
        value = env_value(environ, match.group(1))
        if not value:
            raise SetupError(f"%{match.group(1)}% is not set on this machine")
        return value

    return SYSTEM_TOKEN.sub(system_value, HOME_TOKEN.sub(home_value, text))


def map_strings(value: Any, transform: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, list):
        return [map_strings(item, transform) for item in value]
    if isinstance(value, dict):
        return {key: map_strings(item, transform) for key, item in value.items()}
    return value


def iter_strings(value: Any, where: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_strings(item, f"{where}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from iter_strings(item, f"{where}.{key}")


def unportable(tree: Any, label: str) -> list[str]:
    problems = []
    for where, text in iter_strings(tree, label):
        if any(pattern.search(text) for pattern in MACHINE_PATHS):
            problems.append(f"{where}: machine-specific path")
        for token in MARKER_TOKEN.findall(text):
            if token not in SNAPSHOT_MARKERS:
                problems.append(f"{where}: unknown marker {token}")
    return problems


def user_path_problems(text: str, home: Path, label: str) -> list[str]:
    """A path that still names this user, in any spelling export did not replace."""
    pattern = re.compile(r"(?i)(?:users|home)(?:\\\\|\\|/)+" + re.escape(home.name) + AFTER_PATH)
    return [f"{label}: still contains a path with your user name"] if pattern.search(text) else []


def to_profile_text(text: str, home: Path) -> str:
    """ConvertTo-PortableText for files the PowerShell installer also reads."""
    native = str(home).rstrip("\\/")
    for form in (native.replace("\\", "\\\\"), native, native.replace("\\", "/")):
        text = re.sub(re.escape(form), lambda _: HOME_MARKER, text, flags=re.IGNORECASE)
    return text


def expand_profile_bytes(data: bytes, home: Path, json_escape: bool) -> bytes:
    """ConvertFrom-PortableText: profile markers become this home's native paths."""
    roots = {"__USERPROFILE__": home, "__APPDATA__": home / "AppData" / "Roaming",
             "__LOCALAPPDATA__": home / "AppData" / "Local"}
    if not any(marker.encode() in data for marker in roots):
        return data
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SetupError(f"has a path marker but is not UTF-8 ({error.reason})") from error
    for marker, root in roots.items():
        value = str(root).replace("\\", "\\\\") if json_escape else str(root)
        text = text.replace(marker, value)
    return text.encode("utf-8")


# --- credential checks ----------------------------------------------------------------

def credential_name(name: str) -> bool:
    """A credential word at the end of the name (GITHUB_TOKEN, X-Functions-Key, API_KEY_V2,
    BASIC_AUTH), or a credential phrase anywhere (PGPASSWORD, clientSecret). Words earlier
    in the name describe something else: SSH_AUTH_SOCK, KEY_VAULT_NAME."""
    parts = [part.lower() for part in NAME_PART.findall(name)]
    while parts and parts[-1].isdigit():
        parts.pop()
    joined = "".join(parts)
    if any(phrase in joined for phrase in CREDENTIAL_PHRASES) or CREDENTIAL_PARTS.intersection(parts[-2:]):
        return True
    return bool(parts) and parts[-1] == "auth" and joined != "oauth"


def _base64_secret(run: str) -> bool:
    pieces = [piece for piece in re.split(r"[/+]", run.rstrip("=")) if piece]
    mixed = all(re.search(pattern, run) for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]"))
    return mixed and ("/" in run or "+" in run or run.endswith("=")) and len(run) / max(len(pieces), 1) >= 10


def secret_reason(text: str) -> str | None:
    """Why a value looks like a credential, or None. The value itself is never returned."""
    bare = ENV_REFERENCE.sub("$REF", SYSTEM_TOKEN.sub(" ", HOME_TOKEN.sub(" ", text)))
    for reason, pattern in SECRET_SHAPES:
        if pattern.search(bare):
            return reason
    if any(credential_name(match.group(1)) and not match.group(2).startswith(("$", "%"))
           for match in ASSIGNMENT.finditer(bare)):
        return "a credential assigned in a command"
    if any(re.search(r"[0-9]", match.group(1)) for match in AUTH_SCHEME.finditer(bare)):
        return "an authorization header value"
    if any(re.search(r"[A-Za-z]", run) and re.search(r"[0-9]", run) for run in TOKEN_RUN.findall(bare)):
        return "a credential-shaped value"
    if any(_base64_secret(run) for run in BASE64_RUN.findall(bare)):
        return "a credential-shaped value"
    return None


def references_only(value: Any) -> bool:
    """A value that is nothing but ${NAME} references, optionally after an auth scheme
    ("Bearer ${TOKEN}"), holds no secret; a literal beside a reference still might."""
    return isinstance(value, str) and bool(REFERENCE_VALUE.fullmatch(value.strip()))


def secret_findings(tree: Any, where: str, identifier_maps: Iterable[str] = ()) -> list[str]:
    """Every credential-named key and credential-shaped string in a JSON tree. Keys of
    the maps in identifier_maps are names (plugin ids, hook events), not credentials."""
    findings, names_only = [], set(identifier_maps)

    def walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                child = f"{path}.{key}"
                in_name = secret_reason(str(key)) if path in names_only else None
                if in_name:
                    findings.append(f"{child}: {in_name} in a name")
                elif path not in names_only and credential_name(str(key)) and not references_only(item):
                    findings.append(f"{child}: credential-named entry")
                else:
                    walk(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, str):
            reason = secret_reason(value)
            if reason:
                findings.append(f"{path}: {reason}")

    walk(tree, where)
    return findings


def text_secret_findings(text: str, label: str) -> list[str]:
    findings = [f"{label}: line {number}: {reason}" for number, line in enumerate(text.splitlines(), 1)
                if (reason := secret_reason(line))]
    if ASSIGNED_SECRET.search(text):
        findings.append(f"{label}: a credential-named key holds a long literal")
    return findings


# --- snapshot shape -------------------------------------------------------------------

def repository_path_ok(path: Any) -> bool:
    if not isinstance(path, str):
        return False
    if path.lower() == HOOKS_CLONE.lower():
        return True
    prefix = PROJECTS_DIR.lower() + "\\"
    return path.lower().startswith(prefix) and bool(SAFE_NAME.fullmatch(path[len(prefix):]))


def _inside(path: str, roots: Iterable[str]) -> bool:
    lowered = path.lower().rstrip("\\")
    return any(lowered == root.lower() or lowered.startswith(root.lower() + "\\") for root in roots)


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _string_map(value: Any) -> bool:
    return isinstance(value, Mapping) and all(isinstance(item, str) for item in value.values())


def shape_problems(settings: Mapping[str, Any], servers: Mapping[str, Any]) -> list[str]:
    """The JSON types restore relies on, so a malformed snapshot is refused, not a crash."""
    problems = []
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, Mapping):
        problems.append("settings.hooks: not an object")
    else:
        for event, groups in hooks.items():
            if not isinstance(groups, list) or not all(
                    isinstance(group, Mapping) and isinstance(group.get("matcher", ""), str)
                    and isinstance(group.get("hooks", []), list)
                    and all(isinstance(hook, Mapping) and isinstance(hook.get("command", ""), str)
                            for hook in group.get("hooks", []))
                    for group in groups):
                problems.append(f"settings.hooks.{event}: not a list of hook groups")
    status = settings.get("statusLine", {})
    if not (isinstance(status, Mapping) and isinstance(status.get("command", ""), str)):
        problems.append("settings.statusLine: not an object with a command")
    plugins = settings.get("enabledPlugins", {})
    if not (isinstance(plugins, Mapping) and all(isinstance(on, bool) for on in plugins.values())):
        problems.append("settings.enabledPlugins: not a map of true or false")
    markets = settings.get("extraKnownMarketplaces", {})
    if not (isinstance(markets, Mapping) and all(
            isinstance(entry, Mapping) and _string_map(entry.get("source", {})) for entry in markets.values())):
        problems.append("settings.extraKnownMarketplaces: not a map of sources")
    if not _string_map(settings.get("env", {})):
        problems.append("settings.env: not a map of strings")
    permissions = settings.get("permissions", {})
    if not isinstance(permissions, Mapping):
        problems.append("settings.permissions: not an object")
    else:
        problems += [f"settings.permissions.{key}: not a list of strings"
                     for key in ("allow", "deny", "ask", "additionalDirectories")
                     if key in permissions and not _strings(permissions[key])]
    for name, server in servers.items():
        if not (isinstance(server, Mapping) and isinstance(server.get("command", ""), str)
                and isinstance(server.get("url", ""), str) and _strings(server.get("args", []))
                and _string_map(server.get("env", {})) and _string_map(server.get("headers", {}))):
            problems.append(f"mcpServers.{name}: not a server definition")
    return problems


def snapshot_problems(settings: Mapping[str, Any], servers: Mapping[str, Any], repos: Any,
                      docker: list[str], settings_keys: Iterable[str]) -> list[str]:
    """Everything that would make restore act outside the shapes export writes: other
    settings keys, a bypass default, clones outside the allowed folders, marketplaces
    outside the cloned repositories, or values that could pass as options."""
    problems = shape_problems(settings, servers)
    if problems:
        return problems
    for label, tree in (("settings", settings), ("mcpServers", servers), ("repositories", repos)):
        problems += unportable(tree, label)
    allowed = set(settings_keys)
    problems += [f"settings.{key}: not in claudeSnapshot.settingsKeys" for key in settings if key not in allowed]
    permissions = settings.get("permissions")
    if permissions is not None and not isinstance(permissions, Mapping):
        problems.append("settings.permissions: not an object")
    elif permissions:
        problems += [f"settings.permissions.{key}: restore does not apply it"
                     for key in permissions if key not in PERMISSION_KEYS]
        if permissions.get("defaultMode") in REFUSED_MODES:
            problems.append(f"settings.permissions.defaultMode: {permissions['defaultMode']} never travels in a snapshot")
    if not isinstance(repos, list):
        return problems + ["repositories: not a list"]
    repo_paths = []
    for index, repo in enumerate(repos):
        where = f"repositories[{index}]"
        if not isinstance(repo, Mapping):
            problems.append(f"{where}: not an object")
            continue
        if repository_path_ok(repo.get("path")):
            repo_paths.append(repo["path"])
        else:
            problems.append(f"{where}.path: must be {HOOKS_CLONE} or a folder directly under {PROJECTS_DIR}")
        if not (isinstance(repo.get("url"), str) and SAFE_URL.fullmatch(repo["url"])):
            problems.append(f"{where}.url: must be an https URL without a user name, or a git@ ssh URL")
        if ("branch" in repo) == ("commit" in repo):
            problems.append(f"{where}: needs exactly one of branch or commit")
        if "branch" in repo and not (isinstance(repo["branch"], str) and SAFE_REF.fullmatch(repo["branch"])):
            problems.append(f"{where}.branch: unsafe branch name")
        if "commit" in repo and not (isinstance(repo["commit"], str) and SAFE_COMMIT.fullmatch(repo["commit"])):
            problems.append(f"{where}.commit: must be a full commit id")
        if "filter" in repo and not (isinstance(repo["filter"], str) and SAFE_FILTER.fullmatch(repo["filter"])):
            problems.append(f"{where}.filter: unsafe clone filter")
        if not _strings(repo.get("sparse", [])):
            problems.append(f"{where}.sparse: not a list of paths")
            continue
        for path in repo.get("sparse", []):
            if not (SAFE_SPARSE.fullmatch(path) and ".." not in path.split("/")):
                problems.append(f"{where}.sparse: unsafe path {path!r}")
    problems += [f"mcpServers.{name}: unsafe server name" for name in servers if not SAFE_NAME.fullmatch(name)]
    problems += [f"settings.enabledPlugins.{plugin}: unsafe plugin id"
                 for plugin in settings.get("enabledPlugins") or {} if not SAFE_PLUGIN.fullmatch(plugin)]
    for name, entry in (settings.get("extraKnownMarketplaces") or {}).items():
        source = ((entry or {}).get("source") or {}) if isinstance(entry, Mapping) else {}
        kind, target = source.get("source"), source.get("path") or source.get("repo") or source.get("url")
        valid = isinstance(target, str) and {
            "directory": lambda: _inside(target, repo_paths),
            "github": lambda: bool(GITHUB_REPO.fullmatch(target)),
            "git": lambda: bool(SAFE_URL.fullmatch(target)),
            "url": lambda: target.startswith("https://") and bool(SAFE_URL.fullmatch(target)),
        }.get(kind, lambda: False)()
        if not SAFE_NAME.fullmatch(name) or not valid:
            problems.append(f"settings.extraKnownMarketplaces.{name}: unsafe name, or a source outside the snapshot's "
                            "repositories, GitHub or https")
    problems += [f"Docker MCP server {name!r}: unsafe name" for name in docker if not SAFE_NAME.fullmatch(name)]
    return problems


# --- file helpers ---------------------------------------------------------------------

def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise SetupError(f"cannot read {path}: {error}") from error


def dump_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def is_reparse(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def assert_no_reparse(path: Path) -> None:
    """Refuse links and junctions anywhere on a write target's path."""
    for candidate in (path, *path.parents):
        if is_reparse(candidate):
            raise SetupError(f"reparse point on a target path: {candidate}")


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + BACKUP_TAG + uuid.uuid4().hex)


def replace_file(target: Path, data: bytes, backup: bool) -> None:
    assert_no_reparse(target)
    if backup:
        shutil.copy2(target, backup_path(target))
    write_atomic(target, data)


def same_text(left: bytes, right: bytes) -> bool:
    return left.replace(b"\r\n", b"\n") == right.replace(b"\r\n", b"\n")


def git(*args: str) -> str | None:
    """Run a read-only git query; None when git fails or is missing."""
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def inside_git_worktree(directory: Path) -> bool:
    """Fails closed: a .git anywhere above the folder counts even when git cannot read
    it (missing git, another owner), and so does git saying it is a work tree."""
    probe = directory.resolve()
    if any((candidate / ".git").exists() for candidate in (probe, *probe.parents)):
        return True
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return git("-C", str(probe), "rev-parse", "--is-inside-work-tree") == "true"


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    manifest = read_json(path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("claudeSnapshot"), dict):
        raise SetupError(f"{path} has no claudeSnapshot section")
    return manifest["claudeSnapshot"]


# --- export ---------------------------------------------------------------------------

def export_settings(live: Mapping[str, Any], keys: Iterable[str], restrictions: Mapping[str, Any],
                    home: Path, environ: Mapping[str, str]) -> tuple[dict, list[str], list[str]]:
    """Return the portable settings template, the keys left out, and blocking problems."""
    wanted = set(keys)
    template = {key: copy.deepcopy(value) for key, value in live.items() if key in wanted}
    uncaptured = sorted(key for key in live if key not in wanted)
    # Reviewed restrictions from the profile template travel with every snapshot.
    for kind in ("deny", "ask"):
        reviewed = restrictions.get(kind) or []
        if not reviewed:
            continue
        permissions = template.setdefault("permissions", {})
        rules = permissions.setdefault(kind, [])
        for rule in reviewed:
            if rule not in rules:
                rules.append(rule)
    template = map_strings(template, lambda text: portable_text(text, home, environ))
    names = ("settings.enabledPlugins", "settings.extraKnownMarketplaces", "settings.hooks")
    return template, uncaptured, secret_findings(template, "settings", names) + unportable(template, "settings")


def uses_docker_gateway(server: Mapping[str, Any]) -> bool:
    command = Path(str(server.get("command", ""))).name.lower()
    return command in ("docker", "docker.exe") and [str(a) for a in server.get("args") or []][:2] == ["mcp", "gateway"]


def export_mcp(config: Mapping[str, Any], home: Path, environ: Mapping[str, str],
               exclude: Iterable[str] = ()) -> tuple[dict, list[str], list[str]]:
    """User-scope MCP servers from ~/.claude.json, made portable."""
    excluded = set(exclude)
    servers, notes, problems = {}, [], []
    for name, server in (config.get("mcpServers") or {}).items():
        if name in excluded:
            notes.append(f"MCP server {name}: excluded on request")
            continue
        portable = map_strings(copy.deepcopy(server), lambda text: portable_text(text, home, environ))
        found = secret_findings(portable, f"mcpServers.{name}") + unportable(portable, f"mcpServers.{name}")
        args = portable.get("args")
        if isinstance(args, list) and all(isinstance(arg, str) for arg in args):
            # A flag and its value arrive as separate arguments: --token hunter2.
            reason = secret_reason(" ".join(args))
            if reason and not any(item.startswith(f"mcpServers.{name}.args") for item in found):
                found.append(f"mcpServers.{name}.args: {reason}")
        if found:
            problems += found
        else:
            servers[name] = portable
    for project, entry in (config.get("projects") or {}).items():
        for name in (entry or {}).get("mcpServers") or {}:
            notes.append(f"local-scope MCP server {name} for {project} is not captured")
    missing = excluded - set((config.get("mcpServers") or {}))
    problems += [f"--exclude-mcp {name}: no such user-scope MCP server" for name in sorted(missing)]
    return {"mcpServers": servers}, notes, problems


def docker_servers(registry: Path) -> list[str] | None:
    """Server names in the Docker MCP Toolkit's registry.yaml; None when the file is
    absent or not in the shape this parser knows, so an unknown format never empties
    the snapshot's list."""
    try:
        lines = registry.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    names, inside, recognized = [], False, False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = line.rstrip() == "registry:"
            recognized = recognized or inside or line.strip() == "registry: {}"
            continue
        match = re.fullmatch(r"  ([A-Za-z0-9][A-Za-z0-9_.-]*):\s*", line)
        if inside and match:
            names.append(match.group(1))
    return names if recognized else None


def referenced_paths(settings: Mapping[str, Any], servers: Mapping[str, Any]) -> set[str]:
    """Home paths the setup runs or loads: hook and status line commands, directory
    marketplaces, and MCP server commands. Permission rules are patterns and MCP
    environment values are data locations, so neither names code to recreate."""
    launches = [{key: server.get(key) for key in ("command", "args", "cwd")} for server in servers.values()]
    sources = [settings.get("hooks"), settings.get("statusLine"), settings.get("extraKnownMarketplaces"), launches]
    found = set()
    for _, text in iter_strings(sources):
        if text.startswith(HOME_MARKER) and '"' not in text:
            found.add(text)  # a whole value is one path; spaces are allowed
            continue
        found.update(quoted for quoted in QUOTED.findall(text) if quoted.startswith(HOME_MARKER))
        for match in re.finditer(re.escape(HOME_MARKER) + r"[^\s\"']*", QUOTED.sub(" ", text)):
            found.add(match.group(0))
    return {path.rstrip("\\/") for path in found}


def describe_repository(top: Path, home: Path, environ: Mapping[str, str]) -> tuple[dict | None, list[str], list[str]]:
    portable = portable_text(str(top), home, environ)
    url = git("-C", str(top), "config", "--get", "remote.origin.url")  # as configured, before insteadOf
    if not url:
        return None, [], [f"{portable}: no origin remote, so restore cannot clone it"]
    if re.match(r"(?i)^[a-z][a-z0-9+.-]*://[^/]*@", url) and not re.match(r"(?i)^ssh://git@", url):
        return None, [], [f"{portable}: the origin URL embeds a user name or credential"]
    if any(pattern.search(url) for pattern in MACHINE_PATHS) or url.startswith(("/", "\\", ".", "file:")):
        return None, [], [f"{portable}: origin is a local path that another machine cannot clone"]
    entry: dict[str, Any] = {"path": portable, "url": url}
    notes, problems = [], []
    partial = git("-C", str(top), "config", "--get", "remote.origin.partialclonefilter")
    if partial:
        entry["filter"] = partial
    if git("-C", str(top), "config", "--get", "core.sparseCheckout") == "true":
        if git("-C", str(top), "config", "--get", "core.sparseCheckoutCone") != "true":
            problems.append(f"{portable}: non-cone sparse checkout is not supported")
        entry["sparse"] = (git("-C", str(top), "sparse-checkout", "list") or "").splitlines()
    branch = git("-C", str(top), "symbolic-ref", "-q", "--short", "HEAD")
    if branch:
        entry["branch"] = branch
        if git("-C", str(top), "rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}") is None:
            problems.append(f"{portable}: branch {branch} is not on origin")
        elif (git("-C", str(top), "rev-list", "--count", f"origin/{branch}..HEAD") or "0") != "0":
            notes.append(f"{portable}: commits not pushed to origin/{branch} stay on this machine")
    else:
        # A detached clone is a release deployment: pin the exact commit this machine runs.
        head = git("-C", str(top), "rev-parse", "HEAD")
        default = git("-C", str(top), "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
        if not head or not default:
            problems.append(f"{portable}: detached, and HEAD or origin/HEAD cannot be read (git remote set-head origin --auto)")
        elif git("-C", str(top), "merge-base", "--is-ancestor", "HEAD", default) is None:
            problems.append(f"{portable}: HEAD is not on {default.removeprefix('refs/remotes/')}; deploy a merged commit first")
        else:
            entry["commit"] = head
    if git("-C", str(top), "status", "--porcelain"):
        notes.append(f"{portable}: uncommitted changes stay on this machine")
    return entry, notes, problems


def locate_repositories(paths: Iterable[str], home: Path, environ: Mapping[str, str]) -> tuple[list[dict], list[str], list[str]]:
    repos: dict[str, dict] = {}
    notes, problems = [], []
    for portable in sorted(paths):
        local = Path(expand_text(portable, home, environ))
        if not local.exists():
            notes.append(f"{portable}: referenced by the settings but missing on this machine too")
            continue
        top = git("-C", str(local if local.is_dir() else local.parent), "rev-parse", "--show-toplevel")
        if not top and local.is_dir():
            notes.append(f"{portable}: a folder outside any git clone; it must exist on the new machine")
            continue
        if not top:
            problems.append(f"{portable}: a file outside any git clone, so restore cannot recreate it")
            continue
        key = os.path.normcase(str(Path(top)))
        if key in repos:
            continue
        entry, found_notes, found_problems = describe_repository(Path(top), home, environ)
        notes += found_notes
        problems += found_problems
        if entry:
            repos[key] = entry
    return sorted(repos.values(), key=lambda entry: entry["path"].lower()), notes, problems


def classify_home(claude_home: Path) -> tuple[list[str], list[str], list[str]]:
    """Lines for captured entries, names left behind, and names nobody classified."""
    captured, left, unknown = [], [], []
    for entry in sorted(claude_home.iterdir(), key=lambda p: p.name.lower()) if claude_home.is_dir() else []:
        name = entry.name
        if name in HOME_ENTRIES:
            captured.append(f"{name}: {HOME_ENTRIES[name]}")
        elif name in LEFT_BEHIND or "backup" in name.lower() or name.startswith(LEFT_BEHIND_PREFIXES):
            left.append(name)
        else:
            unknown.append(name)
    return captured, left, unknown


@dataclass
class ExportResult:
    written: list[str]
    notes: list[str]
    uncaptured_settings: list[str]
    captured: list[str]
    left_behind: list[str]
    unknown: list[str]


def export(home: Path, *, profile: Path = PROFILE_DIR, snapshot: Path = SNAPSHOT_DIR,
           settings_keys: Iterable[str] | None = None, environ: Mapping[str, str] = os.environ,
           exclude_mcp: Iterable[str] = (), allow_drift: bool = False) -> ExportResult:
    """Validate everything first; write only when nothing blocks the snapshot."""
    claude_home = home / ".claude"
    keys = list(settings_keys) if settings_keys is not None else load_manifest()["settingsKeys"]
    live = read_json(claude_home / "settings.json")
    if not isinstance(live, dict):
        raise SetupError(f"{claude_home / 'settings.json'} is missing or does not hold a JSON object")
    restrictions = ((read_json(profile / SETTINGS_FILE) or {}).get("permissions") or {})
    settings, uncaptured, problems = export_settings(live, keys, restrictions, home, environ)
    mcp, notes, mcp_problems = export_mcp(read_json(home / ".claude.json") or {}, home, environ, exclude_mcp)
    problems += mcp_problems
    repos, repo_notes, repo_problems = locate_repositories(referenced_paths(settings, mcp["mcpServers"]), home, environ)
    notes += repo_notes
    problems += repo_problems
    gateway = any(uses_docker_gateway(server) for server in mcp["mcpServers"].values())
    docker = docker_servers(home / ".docker" / "mcp" / "registry.yaml") if gateway else []
    if docker is None:
        notes.append("the Docker MCP Toolkit registry is missing or unrecognized; the snapshot keeps its previous list")
    claude_md = claude_home / "CLAUDE.md"
    md_text = b""
    if claude_md.is_file():
        try:
            text = to_profile_text(claude_md.read_bytes().decode("utf-8"), home)
        except UnicodeDecodeError:
            problems.append(f"{claude_md} is not UTF-8")
        else:
            md_text = text.encode("utf-8")
            problems += text_secret_findings(text, "CLAUDE.md") + user_path_problems(text, home, "CLAUDE.md")
    else:
        problems.append(f"{claude_md} does not exist")
    if not allow_drift:
        installed_md = os.path.normcase(str(claude_md))
        for finding in drift.copy_drift(profile, claude_home):
            if not os.path.normcase(finding).endswith(installed_md):
                problems.append(f"{finding} (update the profile through a PR, or pass --allow-drift)")
    outputs = {
        SETTINGS_FILE: dump_json(settings),
        MCP_FILE: dump_json(mcp),
        REPOS_FILE: dump_json(repos),
    }
    if docker:
        outputs[DOCKER_FILE] = "".join(f"{name}\n" for name in docker)
    for name, text in outputs.items():
        if ASSIGNED_SECRET.search(text):
            problems.append(f"{name}: a credential-named key holds a long literal")
        problems += user_path_problems(text, home, name)
    # The same checks restore runs, so a snapshot that exports also restores.
    problems += [problem for problem in snapshot_problems(settings, mcp["mcpServers"], repos, docker or [], keys)
                 if problem not in problems]
    if problems:
        raise SetupError("export stopped; nothing was written:\n  " + "\n  ".join(problems))

    written = []
    if not (profile / "CLAUDE.md").is_file() or (profile / "CLAUDE.md").read_bytes() != md_text:
        write_atomic(profile / "CLAUDE.md", md_text)
        written.append(str(profile / "CLAUDE.md"))
    for name in SNAPSHOT_FILES:
        target = snapshot / name
        if name in outputs:
            data = outputs[name].encode("utf-8")
            if not target.is_file() or target.read_bytes() != data:
                write_atomic(target, data)
                written.append(str(target))
        elif target.exists() and not (name == DOCKER_FILE and docker is None):
            target.unlink()
            written.append(f"{target} (removed)")
    captured, left, unknown = classify_home(claude_home)
    return ExportResult(written, notes, uncaptured, captured, left, unknown)


# --- private bundle -------------------------------------------------------------------

# Claude Code names project folders with [A-Za-z0-9-]; memory files are plain names.
PROJECT_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
FILE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]*")
RESERVED_STEMS = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$",
                            *(f"com{digit}" for digit in "0123456789"), *(f"lpt{digit}" for digit in "0123456789")})


def reserved_name(part: str) -> bool:
    """Windows device names, which Windows also matches with spaces before the dot."""
    isreserved = getattr(os.path, "isreserved", None)  # Python 3.13+ on Windows
    return part.split(".", 1)[0].rstrip(" ").lower() in RESERVED_STEMS or bool(isreserved and isreserved(part))


def safe_member(name: str) -> bool:
    """projects/<project>/memory/<file...> with names that are safe on Windows."""
    if "\\" in name or ":" in name or name.startswith("/"):
        return False
    parts = PurePosixPath(name).parts
    if len(parts) < 4 or parts[0] != "projects" or parts[2] != "memory":
        return False
    if not PROJECT_PART.fullmatch(parts[1]):
        return False
    return all(FILE_PART.fullmatch(part) and not reserved_name(part) and not part.endswith((".", " "))
               for part in parts[3:])


def collect_memory(claude_home: Path) -> tuple[list[tuple[str, Path]], list[str]]:
    """Memory files to bundle, and the ones left out with the reason."""
    files, left_out = [], []
    projects = claude_home / "projects"
    for memory in sorted(projects.glob("*/memory")) if projects.is_dir() else []:
        if not memory.is_dir() or is_reparse(memory):
            continue
        for path in sorted(memory.rglob("*")):
            if not path.is_file() or is_reparse(path):
                continue
            name = "projects/" + path.relative_to(projects).as_posix()
            if not safe_member(name):
                left_out.append(f"{name} (its name cannot be restored safely)")
            elif ASSIGNED_SECRET.search(path.read_bytes().decode("utf-8", "replace")):
                left_out.append(f"{name} (looks like it holds a credential)")
            else:
                files.append((name, path))
    return files, left_out


def write_bundle(home: Path, output: Path, *, now: datetime | None = None) -> tuple[int, list[str]]:
    output = output.resolve()
    if output.exists():
        raise SetupError(f"{output} already exists")
    if inside_git_worktree(output.parent):
        raise SetupError(f"{output.parent} is inside a git work tree; write the private bundle somewhere else")
    files, left_out = collect_memory(home / ".claude")
    manifest = {
        "format": BUNDLE_FORMAT,
        "createdAt": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "sourceHome": str(home),
        "files": [{"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for name, path in files],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=output.parent, prefix=output.name + ".", suffix=".tmp")
    os.close(handle)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("bundle.json", dump_json(manifest))
            for name, path in files:
                archive.write(path, name)
        os.replace(temporary, output)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return len(files), left_out


def read_bundle(path: Path) -> tuple[dict, dict[str, bytes]]:
    """Validate the whole bundle before any of it is used."""
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                meta = json.loads(archive.read("bundle.json"))
                expected = {entry["path"]: entry["sha256"] for entry in meta["files"]}
            except (KeyError, TypeError, ValueError) as error:
                raise SetupError("not a Claude private bundle: bundle.json is missing or malformed") from error
            if meta.get("format") != BUNDLE_FORMAT:
                raise SetupError(f"unsupported private bundle format: {meta.get('format')}")
            files, total = {}, 0
            for info in archive.infolist():
                if info.filename == "bundle.json" or info.is_dir():
                    continue
                if not safe_member(info.filename):
                    raise SetupError(f"unsafe path in private bundle: {info.filename}")
                total += info.file_size
                if total > MAX_BUNDLE_BYTES:
                    raise SetupError("private bundle is larger than the 64 MB limit")
                data = archive.read(info)
                if hashlib.sha256(data).hexdigest() != expected.get(info.filename):
                    raise SetupError(f"checksum mismatch in private bundle: {info.filename}")
                files[info.filename] = data
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise SetupError(f"cannot read private bundle {path}: {error}") from error
    if set(expected) != set(files):
        raise SetupError("private bundle contents do not match its file list")
    return meta, files


def encode_project(path: str) -> str:
    """Claude Code's ~/.claude/projects directory name for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def remap_project(name: str, source_home: str, home: Path) -> str:
    source, target = encode_project(source_home.rstrip("\\/")), encode_project(str(home).rstrip("\\/"))
    lowered = name.lower()
    if lowered != source.lower() and not lowered.startswith(source.lower() + "-"):
        return name
    if name[:1].islower():  # keep the drive-letter case the session used
        target = target[:1].lower() + target[1:]
    return target + name[len(source):]


# --- restore --------------------------------------------------------------------------

def run_command(args: list[str], env: Mapping[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, env=dict(env) if env is not None else None, cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=900)


def merge_settings(existing: Mapping[str, Any], template: Mapping[str, Any]) -> dict:
    """The template wins for values; lists gain missing entries; hooks merge by group."""
    merged = copy.deepcopy(dict(existing))
    for key, value in template.items():
        current = merged.get(key)
        if key == "hooks" and isinstance(value, Mapping) and isinstance(current, Mapping):
            merged[key] = merge_hooks(current, value)
        elif isinstance(value, Mapping) and isinstance(current, Mapping):
            merged[key] = merge_settings(current, value)
        elif isinstance(value, list) and isinstance(current, list):
            combined = copy.deepcopy(current)
            for item in value:
                if item not in combined:
                    combined.append(copy.deepcopy(item))
            merged[key] = combined
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _hook_key(group: Any) -> tuple:
    if not isinstance(group, Mapping):
        return ("", ())
    commands = sorted(str(hook.get("command", "")) for hook in group.get("hooks") or [] if isinstance(hook, Mapping))
    return (group.get("matcher", ""), tuple(commands))


def merge_hooks(existing: Mapping[str, Any], template: Mapping[str, Any]) -> dict:
    """A template group replaces the group with the same matcher and commands, so a
    rerun or a changed timeout never registers a hook twice."""
    merged = copy.deepcopy(dict(existing))
    for event, groups in template.items():
        current = merged.get(event)
        if not isinstance(current, list):
            merged[event] = copy.deepcopy(groups)
            continue
        for group in groups:
            for index, old in enumerate(current):
                if _hook_key(old) == _hook_key(group):
                    current[index] = copy.deepcopy(group)
                    break
            else:
                current.append(copy.deepcopy(group))
    return merged


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def describe_changes(before: Any, after: Any, path: str = "") -> list[str]:
    """Every settings change in full. Only long deny and ask lists, which can only
    restrict, are counted instead of itemized."""
    if isinstance(after, Mapping):
        old = before if isinstance(before, Mapping) else {}
        lines = []
        for key, value in after.items():
            if key not in old or old[key] != value:
                lines += describe_changes(old.get(key), value, f"{path}.{key}" if path else key)
        return lines
    if isinstance(after, list):
        old = before if isinstance(before, list) else []
        added = [item for item in after if item not in old]
        if len(added) > 60 and path.rsplit(".", 1)[-1] in ("deny", "ask"):
            return [f"+ {path}: {len(added)} entries (restrictions)"]
        return [f"+ {path}: {_text(item)}" for item in added]
    if before is None:
        return [f"+ {path} = {_text(after)}"]
    return [f"~ {path}: {_text(before)} -> {_text(after)}"]


def settings_gaps(live: Mapping[str, Any], template: Mapping[str, Any]) -> list[str]:
    """Template entries that the live settings do not contain."""
    return describe_changes(live, merge_settings(live, template))


def same_url(left: str | None, right: str) -> bool:
    def norm(url: str) -> str:
        return url.strip().rstrip("/").removesuffix(".git").lower()
    return left is not None and norm(left) == norm(right)


@dataclass
class Restore:
    home: Path
    apply: bool = False
    overwrite: bool = False
    staged: bool = False
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    run: Callable[..., subprocess.CompletedProcess] = run_command
    which: Callable[[str], str | None] = shutil.which
    out: Callable[[str], None] = print
    profile: Path = PROFILE_DIR
    snapshot: Path = SNAPSHOT_DIR
    repo: Path = REPO_DIR
    failures: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    planned_paths: set[str] = field(default_factory=set)

    @property
    def claude_home(self) -> Path:
        return self.home / ".claude"

    def claude_env(self) -> dict | None:
        # A staged home gets its own Claude config through CLAUDE_CONFIG_DIR, which also
        # moves the global config file to <config dir>/.claude.json.
        return {**os.environ, "CLAUDE_CONFIG_DIR": str(self.claude_home)} if self.staged else None

    def claude_config_file(self) -> Path:
        return self.claude_home / ".claude.json" if self.staged else self.home / ".claude.json"

    def will_exist(self, path: str) -> bool:
        """True when the path exists, or sits in a clone this preview plans."""
        return Path(path).exists() or _inside(os.path.normcase(path), self.planned_paths)

    def section(self, title: str) -> None:
        self.out(f"\n== {title}")

    def fail(self, message: str) -> None:
        self.failures.append(message)
        self.out(f"  FAILED   {message}")

    def command(self, args: list[str], *, env: Mapping[str, str] | None = None, cwd: Path | None = None) -> bool:
        shown = subprocess.list2cmdline(args)
        if not self.apply:
            self.out(f"  would run: {shown}")
            return True
        executable = self.which(args[0])
        if not executable:
            self.fail(f"{args[0]} is not installed: {shown}")
            return False
        if executable.lower().endswith((".cmd", ".bat")) and any(set(arg) & set('%^&|<>!') for arg in args[1:]):
            self.fail(f"{executable} is a batch shim and an argument has cmd.exe metacharacters; run it by hand: {shown}")
            return False
        try:
            result = self.run([executable, *args[1:]], env=env, cwd=cwd)
        except (OSError, subprocess.TimeoutExpired) as error:
            self.fail(f"{shown}: {error}")
            return False
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
            self.fail(f"{shown} exited {result.returncode}: {' | '.join(detail)}")
            return False
        self.out(f"  ran: {shown}")
        return True

    def claude(self, args: list[str]) -> bool:
        """A claude CLI command, run from the target home so that no project settings of
        the directory restore was started from take part."""
        return self.command(["claude", *args], env=self.claude_env(), cwd=self.home if self.home.is_dir() else None)

    def probe(self, args: list[str]) -> str | None:
        """A read-only command, run in preview too."""
        executable = self.which(args[0])
        if not executable:
            return None
        try:
            result = self.run([executable, *args[1:]])
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None


@dataclass
class Snapshot:
    settings: dict
    servers: dict
    repos: list[dict]
    docker: list[str]
    references: list[str]


def load_snapshot(ctx: Restore, settings_keys: Iterable[str]) -> Snapshot:
    settings = read_json(ctx.snapshot / SETTINGS_FILE)
    if settings is None:
        settings = read_json(ctx.profile / SETTINGS_FILE) or {}
        ctx.out(f"No snapshot at {ctx.snapshot}; using the profile's baseline settings.")
    mcp = read_json(ctx.snapshot / MCP_FILE) or {}
    repos = read_json(ctx.snapshot / REPOS_FILE) or []
    if not isinstance(settings, dict) or not isinstance(mcp, dict) or not isinstance(mcp.get("mcpServers", {}), dict):
        raise SetupError("snapshot settings and MCP files must hold JSON objects")
    servers = mcp.get("mcpServers") or {}
    docker_file = ctx.snapshot / DOCKER_FILE
    try:
        docker = [line.strip() for line in docker_file.read_text(encoding="utf-8").splitlines() if line.strip()] \
            if docker_file.is_file() else []
    except (OSError, UnicodeDecodeError) as error:
        raise SetupError(f"cannot read {docker_file}: {error}") from error
    problems = snapshot_problems(settings, servers, repos, docker, settings_keys)
    if problems:
        raise SetupError("snapshot is not portable or not safe to run; nothing was changed:\n  " + "\n  ".join(problems))
    expand = lambda tree: map_strings(tree, lambda text: expand_text(text, ctx.home, ctx.environ))  # noqa: E731
    references = sorted(expand_text(path, ctx.home, ctx.environ) for path in referenced_paths(settings, {}))
    return Snapshot(expand(settings), expand(servers), expand(repos), docker, references)


def show_trust(ctx: Restore, snap: Snapshot) -> None:
    """Everything the snapshot will run, clone or trust, in full, before any change."""
    ctx.section("What this snapshot runs and trusts (review it before --apply)")
    for event, groups in (snap.settings.get("hooks") or {}).items():
        for group in groups or []:
            matcher = f" [{group['matcher']}]" if isinstance(group, Mapping) and group.get("matcher") else ""
            for hook in (group.get("hooks") or []) if isinstance(group, Mapping) else []:
                ctx.out(f"  hook     {event}{matcher}: {hook.get('command')}")
    status = snap.settings.get("statusLine") or {}
    if isinstance(status, Mapping) and status.get("command"):
        ctx.out(f"  status   {status['command']}")
    for repo in snap.repos:
        at = f"commit {repo['commit']}" if repo.get("commit") else f"branch {repo['branch']}"
        ctx.out(f"  clone    {repo['url']} at {at} into {repo['path']}")
    for name, entry in (snap.settings.get("extraKnownMarketplaces") or {}).items():
        source = (entry or {}).get("source") or {}
        ctx.out(f"  market   {name}: {source.get('source')} {source.get('path') or source.get('repo') or source.get('url')}")
    for plugin, on in (snap.settings.get("enabledPlugins") or {}).items():
        if on is True:
            ctx.out(f"  plugin   {plugin}")
    for name, server in snap.servers.items():
        launch = " ".join([str(server.get("command") or server.get("url") or "")] + [str(a) for a in server.get("args") or []])
        ctx.out(f"  mcp      {name}: {launch}")
        for key, value in {**(server.get("env") or {}), **(server.get("headers") or {})}.items():
            ctx.out(f"             {key}={value}")
    for key, value in (snap.settings.get("env") or {}).items():
        ctx.out(f"  env      {key}={value}")
    permissions = snap.settings.get("permissions") or {}
    ctx.out(f"  grants   {len(permissions.get('allow') or [])} allow rules and "
            f"{len(permissions.get('additionalDirectories') or [])} extra directories, listed under Settings")


def step_tools(ctx: Restore, tools: list[dict]) -> None:
    ctx.section("Tools")
    for tool in tools:
        path = ctx.which(tool["command"])
        hint = f"winget install --id {tool['winget']} -e --source winget" if tool.get("winget") else tool.get("install", "")
        if path:
            ctx.out(f"  ok       {tool['name']}: {path}")
        elif tool.get("required"):
            ctx.out(f"  MISSING  {tool['name']} ({tool['purpose']}): {hint}")
            ctx.manual.append(f"Install {tool['name']}, then rerun restore: {hint}")
        else:
            ctx.out(f"  absent   {tool['name']} ({tool['purpose']}): {hint}")
    version = ctx.probe(["py", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"])
    if version and re.fullmatch(r"\d+\.\d+", version) and tuple(int(part) for part in version.split(".")) < (3, 11):
        ctx.out(f"  WARNING  py starts Python {version}; the hooks need 3.11 or newer")


def profile_files(profile: Path) -> list[tuple[str, Path]]:
    files = []
    for path in sorted(profile.rglob("*")):
        relative = path.relative_to(profile)
        if not path.is_file() or relative.parts[0] == "hooks" or relative.as_posix() == SETTINGS_FILE:
            continue  # hooks run from the release clone; settings are merged separately
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        files.append((relative.as_posix(), path))
    return files


def step_files(ctx: Restore) -> None:
    ctx.section("Profile files (CLAUDE.md, agents, skills)")
    added, replaced, kept, unchanged = [], [], [], 0
    for relative, source in profile_files(ctx.profile):
        target = ctx.claude_home / relative
        try:
            data = expand_profile_bytes(source.read_bytes(), ctx.home, json_escape=source.suffix in (".json", ".toml"))
            exists = target.is_file()
            if exists and same_text(target.read_bytes(), data):
                unchanged += 1
                continue
            if exists and not ctx.overwrite:
                kept.append(relative)
                continue
            if ctx.apply:
                replace_file(target, data, backup=exists)
        except (OSError, SetupError) as error:
            ctx.fail(f"{relative}: {error}")
            continue
        (replaced if exists else added).append(relative)
    verb = "" if ctx.apply else "would be "
    ctx.out(f"  {len(added)} {verb}added, {len(replaced)} {verb}replaced (backed up), {unchanged} unchanged")
    for relative in kept:
        ctx.out(f"  kept     {relative}: differs from the profile; --overwrite replaces it after a backup")


def clone_steps(repo: Mapping[str, Any], target: Path) -> list[list[str]]:
    clone = ["git", "clone"]
    if repo.get("filter"):
        clone.append(f"--filter={repo['filter']}")
    if repo.get("sparse") or repo.get("commit"):
        clone.append("--no-checkout")
    if repo.get("branch"):
        clone += ["--branch", repo["branch"]]
    steps = [clone + ["--", repo["url"], str(target)]]
    if repo.get("sparse"):
        steps.append(["git", "-C", str(target), "sparse-checkout", "set", "--cone", *repo["sparse"]])
    if repo.get("commit"):
        steps.append(["git", "-C", str(target), "switch", "--detach", repo["commit"]])
    return steps


def step_repositories(ctx: Restore, repos: list[dict]) -> None:
    ctx.section("Repositories")
    for repo in repos:
        path, url = Path(repo["path"]), repo["url"]
        if (path / ".git").exists():
            origin = ctx.probe(["git", "-C", str(path), "config", "--get", "remote.origin.url"])
            if not same_url(origin, url):
                ctx.fail(f"{path} exists but its origin is {origin or 'unset'}, not {url}; move it aside and rerun")
                continue
            head = ctx.probe(["git", "-C", str(path), "rev-parse", "HEAD"]) or ""
            if repo.get("commit") and head != repo["commit"]:
                ctx.out(f"  present  {path} at {head[:12] or 'unknown'}; the snapshot pins {repo['commit'][:12]} "
                        "(deploy hook changes through the release procedure)")
            else:
                ctx.out(f"  present  {path}")
            continue
        if path.exists() and any(path.iterdir()):
            ctx.fail(f"{path} exists and is not a clone of {url}; move it aside and rerun")
            continue
        if not ctx.apply:
            for args in clone_steps(repo, path):
                ctx.command(args)
            ctx.out("  (each clone is made in a temporary folder and renamed only when every step succeeds)")
            ctx.planned_paths.add(os.path.normcase(str(path)))
            continue
        # Clone beside the target and rename only on success, so a failed step can never
        # leave a half-made clone that a rerun would mistake for a finished one.
        assert_no_reparse(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f"{path.name}.restore-{uuid.uuid4().hex[:8]}")
        if all(ctx.command(args) for args in clone_steps(repo, staging)):
            try:
                if path.exists():
                    path.rmdir()  # an empty folder, checked above
                os.replace(staging, path)
            except OSError as error:  # for example antivirus or a git process holding the folder
                ctx.fail(f"could not rename {staging} to {path} ({error}); close what holds it, delete it, then rerun")
            else:
                ctx.out(f"  cloned   {path}")
        elif staging.exists():
            ctx.fail(f"the partial clone is left at {staging} for inspection; delete it, then rerun")


def step_plugins(ctx: Restore, settings: Mapping[str, Any]) -> None:
    ctx.section("Plugins")
    marketplaces = settings.get("extraKnownMarketplaces") or {}
    enabled = [plugin for plugin, on in (settings.get("enabledPlugins") or {}).items() if on is True]
    if not marketplaces and not enabled:
        ctx.out("  none in the settings")
        return
    if not ctx.which("claude"):
        ctx.out("  skipped  Claude Code is not installed; install it and rerun restore")
        return
    plugins_dir = ctx.claude_home / "plugins"
    known = read_json(plugins_dir / "known_marketplaces.json") or {}
    installed = (read_json(plugins_dir / "installed_plugins.json") or {}).get("plugins") or {}
    unavailable = set()
    for name, entry in marketplaces.items():
        if name in known:
            ctx.out(f"  present  marketplace {name}")
            continue
        source = (entry or {}).get("source") or {}
        kind = source.get("source")
        target = {"directory": source.get("path"), "github": source.get("repo"),
                  "git": source.get("url"), "url": source.get("url")}.get(kind)
        if not target:
            ctx.fail(f"marketplace {name}: unsupported source {kind!r}")
        elif kind == "directory" and not ctx.will_exist(target):
            ctx.fail(f"marketplace {name}: {target} is missing")
        elif ctx.claude(["plugin", "marketplace", "add", "--", target]):
            continue
        unavailable.add(name)
    for plugin in enabled:
        marketplace = plugin.rpartition("@")[2]
        if plugin in installed:
            ctx.out(f"  present  plugin {plugin}")
        elif marketplace in unavailable:
            ctx.out(f"  skipped  plugin {plugin}: marketplace {marketplace} was not added")
        else:
            ctx.claude(["plugin", "install", "--scope", "user", "--", plugin])


def step_mcp(ctx: Restore, servers: Mapping[str, Any]) -> None:
    ctx.section("MCP servers (user scope)")
    if not servers:
        ctx.out("  none in the snapshot")
        return
    if not ctx.which("claude"):
        ctx.out("  skipped  Claude Code is not installed; install it and rerun restore")
        return
    existing = (read_json(ctx.claude_config_file()) or {}).get("mcpServers") or {}
    for name, server in servers.items():
        if name not in existing:
            ctx.claude(["mcp", "add-json", "--scope", "user", "--", name, json.dumps(server)])
        elif existing[name] == server:
            ctx.out(f"  present  {name}")
        else:
            ctx.out(f"  differs  {name}: left as is; to take the snapshot's, run "
                    f"`claude mcp remove {name} --scope user` and rerun restore")


def step_settings(ctx: Restore, template: Mapping[str, Any], references: list[str]) -> None:
    ctx.section("Settings (~/.claude/settings.json)")
    path = ctx.claude_home / "settings.json"
    # Hooks that point at missing scripts exit 2, which Claude Code treats as a block on
    # every tool call and prompt, so settings wait until everything they run exists.
    missing = [reference for reference in references if not ctx.will_exist(reference)]
    if missing:
        ctx.fail("settings.json left unchanged, because it would run or load what is missing: "
                 + ", ".join(missing) + "; fix the repositories step and rerun")
        return
    original = path.read_bytes() if path.is_file() else None
    try:
        existing = json.loads(original.decode("utf-8-sig")) if original else {}
    except ValueError as error:
        ctx.fail(f"{path} is not valid JSON ({error}); fix it and rerun")
        return
    if not isinstance(existing, dict):
        ctx.fail(f"{path} does not hold a JSON object; fix it and rerun")
        return
    merged = merge_settings(existing, template)
    changes = describe_changes(existing, merged)
    if not changes:
        ctx.out("  unchanged")
        return
    for line in changes:
        ctx.out(f"  {line}")
    if not ctx.apply:
        return
    assert_no_reparse(path)
    if (path.read_bytes() if path.is_file() else None) != original:
        ctx.fail(f"{path} changed while restore was running; rerun restore")
        return
    if original is not None:
        write_atomic(backup_path(path), original)  # the exact bytes the merge started from
    write_atomic(path, dump_json(merged).encode("utf-8"))
    ctx.out(f"  wrote {path}" + (" (previous file backed up beside it)" if original is not None else ""))


def step_docker(ctx: Restore, names: list[str]) -> None:
    ctx.section("Docker MCP Toolkit servers")
    if not names:
        ctx.out("  none in the snapshot")
        return
    if ctx.staged:
        ctx.out("  skipped  Docker Desktop's servers are machine-wide; a staged home leaves them alone")
        return
    if not ctx.which("docker"):
        ctx.manual.append("Install Docker Desktop, turn on its MCP Toolkit, then run: docker mcp server enable " + " ".join(names))
        ctx.out("  skipped  Docker is not installed")
        return
    enabled = docker_servers(ctx.home / ".docker" / "mcp" / "registry.yaml") or []
    missing = [name for name in names if name not in enabled]
    if not missing:
        ctx.out(f"  present  {', '.join(names)}")
        return
    ctx.command(["docker", "mcp", "server", "enable", *missing])
    ctx.manual.append("Docker Desktop > MCP Toolkit: authorize the servers that need an account (for example GitHub)")


def step_agent_browser(ctx: Restore) -> None:
    ctx.section("agent-browser CLI")
    if not (ctx.profile / "skills" / "agent-browser" / "SKILL.md").is_file():
        ctx.out("  not used by the profile")
        return
    found = ctx.which("agent-browser")
    if found:
        ctx.out(f"  present  {found}")
        return
    if ctx.staged:
        ctx.out("  skipped  Python user packages are machine-wide; a staged home leaves them alone")
        return
    if not ctx.which("py"):
        ctx.out("  skipped  the Python launcher is not installed")
        return
    # PyPI only, so a machine-wide private index cannot break or redirect the install.
    env = {**os.environ, "PIP_INDEX_URL": "https://pypi.org/simple", "PIP_EXTRA_INDEX_URL": "", "PIP_NO_INPUT": "1"}
    if ctx.command(["py", "-m", "pip", "install", "--user", "-e", str(ctx.repo / "agent-browser")], env=env) and ctx.apply:
        if not ctx.which("agent-browser"):
            ctx.manual.append("Put Python's user Scripts folder on PATH so `agent-browser` resolves")
        ctx.manual.append("Run `agent-browser doctor`; it needs Edge or Chrome")


def step_memory(ctx: Restore, bundle: Path | None) -> None:
    ctx.section("Auto-memory (private bundle)")
    if bundle is None:
        ctx.out("  no --private-bundle given")
        return
    meta, files = read_bundle(bundle)
    source_home = str(meta.get("sourceHome") or "")
    added = kept = unchanged = replaced = 0
    for name, data in sorted(files.items()):
        parts = PurePosixPath(name).parts
        project = remap_project(parts[1], source_home, ctx.home) if source_home else parts[1]
        target = ctx.claude_home.joinpath("projects", project, *parts[2:])
        exists = target.is_file()
        if exists and target.read_bytes() == data:
            unchanged += 1
            continue
        if exists and not ctx.overwrite:
            kept += 1
            continue
        if ctx.apply:
            try:
                replace_file(target, data, backup=exists)
            except (OSError, SetupError) as error:
                ctx.fail(f"{name}: {error}")
                continue
        if exists:
            replaced += 1
        else:
            added += 1
    verb = "" if ctx.apply else "would be "
    ctx.out(f"  {added} {verb}added, {replaced} {verb}replaced, {unchanged} unchanged, {kept} kept (differ; --overwrite replaces)")


def step_verify(ctx: Restore, repos: list[dict], template: Mapping[str, Any]) -> None:
    ctx.section("Verification")
    if not ctx.apply:
        ctx.out("  runs after --apply")
        return
    settings_path = ctx.claude_home / "settings.json"
    clone = next((Path(repo["path"]) for repo in repos if any("profile/claude/hooks" in p for p in repo.get("sparse") or [])),
                 ctx.claude_home / "hooks-release")
    # Broken hook wiring or a settings merge that did not land is a failure; a profile
    # file kept because it differs was already reported and stays a warning.
    blocking = drift.release_drift(clone) if clone.exists() else [f"hook release clone missing: {clone}"]
    blocking += drift.wiring_drift(settings_path, clone)
    gaps = settings_gaps(read_json(settings_path) or {}, template)
    if gaps:
        blocking.append(f"settings.json lacks {len(gaps)} snapshot entr{'y' if len(gaps) == 1 else 'ies'}, "
                        f"for example {gaps[0]}")
    for finding in blocking:
        ctx.fail(f"drift: {finding}")
    warnings = drift.copy_drift(ctx.profile, ctx.claude_home)
    for finding in warnings:
        ctx.out(f"  warning  {finding}")
    if not blocking and not warnings:
        ctx.out("  no drift")


def restore(ctx: Restore, *, tools: list[dict], settings_keys: Iterable[str], bundle: Path | None = None,
            skip: Iterable[str] = ()) -> int:
    skipped = set(skip)
    snap = load_snapshot(ctx, settings_keys)
    mode = "Applying" if ctx.apply else "Preview (nothing changes)"
    ctx.out(f"{mode}: Claude Code setup into {ctx.home}" + (" (staged home)" if ctx.staged else ""))
    if bundle is not None:
        read_bundle(bundle)  # reject a bad bundle before anything is written
    show_trust(ctx, snap)
    steps = [
        ("tools", lambda: step_tools(ctx, tools)),
        ("files", lambda: step_files(ctx)),
        ("repositories", lambda: step_repositories(ctx, snap.repos)),
        ("plugins", lambda: step_plugins(ctx, snap.settings)),
        ("mcp", lambda: step_mcp(ctx, snap.servers)),
        ("settings", lambda: step_settings(ctx, snap.settings, snap.references)),
        ("docker", lambda: step_docker(ctx, snap.docker)),
        ("agent-browser", lambda: step_agent_browser(ctx)),
        ("memory", lambda: step_memory(ctx, bundle)),
        ("verify", lambda: step_verify(ctx, snap.repos, snap.settings)),
    ]
    for name, run_step in steps:
        if name in skipped:
            ctx.section(f"{name}: skipped on request")
            continue
        try:
            run_step()
        except Exception as error:  # the boundary of one step: record it, keep the summary
            ctx.fail(f"{name}: {type(error).__name__}: {error}")
    ctx.manual += [
        "Sign in: run `claude` and complete the login (credentials are never exported)",
        "Authenticate the CLIs your agents use, for example `gh auth login` and `az login`",
        "Start a new Claude Code session; settings, hooks and plugins load at startup",
    ]
    ctx.section("Manual steps")
    for number, item in enumerate(dict.fromkeys(ctx.manual), 1):
        ctx.out(f"  {number}. {item}")
    if ctx.failures:
        ctx.out(f"\n{len(ctx.failures)} failure(s); fix them and rerun (restore is idempotent).")
        return 1
    ctx.out("\nDone." if ctx.apply else "\nPreview only: nothing changed.")
    return 0


# --- command line ---------------------------------------------------------------------

def _print_export(result: ExportResult) -> None:
    print("Export complete." if result.written else "Export complete; nothing changed.")
    for path in result.written:
        print(f"  wrote {path}")
    for note in result.notes:
        print(f"  note: {note}")
    if result.uncaptured_settings:
        print("  settings keys not captured (add them to claudeSnapshot.settingsKeys to keep them): "
              + ", ".join(result.uncaptured_settings))
    print("~/.claude inventory:")
    for line in result.captured:
        print(f"  {line}")
    if result.left_behind:
        print("  left behind (credentials, history, caches, runtime state): " + ", ".join(result.left_behind))
    if result.unknown:
        print("  NOT CAPTURED, review: " + ", ".join(result.unknown))


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    export_cmd = commands.add_parser("export", help="capture this machine into the profile and snapshot")
    export_cmd.add_argument("--home", type=Path, default=Path.home())
    export_cmd.add_argument("--exclude-mcp", action="append", default=[], metavar="NAME",
                            help="leave a user-scope MCP server out of the snapshot")
    export_cmd.add_argument("--allow-drift", action="store_true",
                            help="export even when installed agents or skills differ from the profile")
    bundle_cmd = commands.add_parser("bundle", help="write auto-memory to a private zip")
    bundle_cmd.add_argument("--home", type=Path, default=Path.home())
    bundle_cmd.add_argument("--output", type=Path, help="zip path outside any git work tree (default: in your home folder)")
    restore_cmd = commands.add_parser("restore", help="recreate the setup (preview unless --apply)")
    restore_cmd.add_argument("--home", type=Path, default=Path.home(),
                             help="target home; any other folder than yours is a staged home")
    restore_cmd.add_argument("--apply", action="store_true", help="show the plan, confirm, then make the changes")
    restore_cmd.add_argument("--yes", action="store_true", help="with --apply, skip the confirmation prompt")
    restore_cmd.add_argument("--overwrite", action="store_true",
                             help="replace profile and memory files that differ, after backing them up")
    restore_cmd.add_argument("--private-bundle", type=Path, help="zip written by the bundle command")
    restore_cmd.add_argument("--skip", action="append", default=[], metavar="STEP",
                             choices=["tools", "files", "repositories", "plugins", "mcp", "settings",
                                      "docker", "agent-browser", "memory", "verify"])
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            _print_export(export(args.home.resolve(), exclude_mcp=args.exclude_mcp, allow_drift=args.allow_drift))
            return 0
        if args.command == "bundle":
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            output = args.output or args.home / f"claude-private-bundle-{stamp}.zip"
            count, left_out = write_bundle(args.home.resolve(), output)
            print(f"Wrote {count} memory file(s) to {output.resolve()}")
            for item in left_out:
                print(f"  left out: {item}")
            print("It holds personal notes in plain text: keep it out of synced folders, move it privately, "
                  "restore with --private-bundle, then delete it.")
            return 0
        home = args.home.resolve()
        staged = os.path.normcase(str(home)) != os.path.normcase(str(Path.home().resolve()))
        if not staged and os.environ.get("CLAUDE_CONFIG_DIR"):
            raise SetupError("CLAUDE_CONFIG_DIR is set; unset it so restore targets ~/.claude")
        manifest = load_manifest()

        def run(apply: bool) -> int:
            ctx = Restore(home=home, apply=apply, overwrite=args.overwrite, staged=staged)
            return restore(ctx, tools=manifest["tools"], settings_keys=manifest["settingsKeys"],
                           bundle=args.private_bundle, skip=args.skip)

        if not args.apply:
            code = run(False)
            print("Rerun with --apply to make these changes.")
            return code
        if not args.yes:
            code = run(False)
            if code:
                return code
            no_terminal = "no terminal to confirm in; review the plan above, then rerun with --apply --yes"
            if not sys.stdin.isatty():
                raise SetupError(no_terminal)
            try:
                answer = input("\nApply this plan? Type yes to continue: ")
            except EOFError:  # Windows reports NUL as a terminal
                raise SetupError(no_terminal) from None
            if answer.strip().lower() != "yes":
                print("Nothing changed.")
                return 1
        return run(True)
    except SetupError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
