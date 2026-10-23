"""Capture this machine's Claude Code setup and recreate it on another machine.

export   Refresh profile/claude/CLAUDE.md from ~/.claude and write snapshot/claude/:
         portable settings, user-scope MCP servers, the Docker MCP gateway's servers
         and the git repositories the settings run code from. Agents and skills are
         repository-owned, so export only checks them for drift.
bundle   Write auto-memory (~/.claude/projects/*/memory) to a private zip outside any
         git work tree. This repository is public, so memory never goes in it.
restore  Preview by default; --apply recreates the setup: profile files, repositories,
         plugins, MCP servers, the settings merge, Docker MCP servers, agent-browser
         and memory, then runs the drift checks.

Standard library only, Python 3.11+. Export fails closed on credential-shaped values
and on machine paths it cannot make portable. Credentials are never read or written.
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
DRIVE_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
POSIX_USER_PATH = re.compile(r"(?<![A-Za-z0-9])/[A-Za-z]/Users/", re.IGNORECASE)
QUOTED = re.compile(r'"([^"]*)"')

RESTRICTED_TERMS = ("api_key", "api-key", "apikey", "token", "secret", "secrets", "password",
                    "passwd", "credential", "credentials", "authorization", "cookie",
                    "private_key", "access_key")
RESTRICTED_NAME = re.compile(
    r"(?i)(?:^|[._-])(?:" + "|".join(map(re.escape, RESTRICTED_TERMS)) + r")(?:[._-]|$)")
# Test-SafeProfileContent's rule from ProfileTools.psm1: a credential-named key assigned
# a long literal. Every file export writes must also pass the PowerShell bundle's check.
ASSIGNED_SECRET = re.compile(
    r"(?im)^\s*['\"]?[A-Za-z0-9_.-]*(?:api_key|token|secret|password|credential|authorization)"
    r"[A-Za-z0-9_.-]*['\"]?\s*[:=]\s*['\"][A-Za-z0-9_./+=-]{20,}['\"]")
TOKEN_RUN = re.compile(r"[A-Za-z0-9+/=_]{20,}")

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


def is_portable_reference(text: str) -> bool:
    return bool(HOME_TOKEN.search(text) or SYSTEM_TOKEN.search(text))


def unportable(tree: Any, label: str) -> list[str]:
    problems = []
    for where, text in iter_strings(tree, label):
        if DRIVE_PATH.search(text) or POSIX_USER_PATH.search(text):
            problems.append(f"{where}: machine-specific path")
        for token in MARKER_TOKEN.findall(text):
            if token not in SNAPSHOT_MARKERS:
                problems.append(f"{where}: unknown marker {token}")
    return problems


# Restore passes snapshot values to git and claude as arguments, so each one must look
# like what export writes: nothing that starts with "-", no ".." and no shell characters.
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
SAFE_PLUGIN = re.compile(SAFE_NAME.pattern + "@" + SAFE_NAME.pattern)
SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
SAFE_URL = re.compile(r"(?:https://|ssh://|git@)[^\s\"'<>|^%`]+")
SAFE_FILTER = re.compile(r"[a-z]+(?::[A-Za-z0-9=._-]+)?")
SAFE_SPARSE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./ -]*")
GITHUB_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def _home_folder(path: Any) -> bool:
    """A portable path inside the home directory, without parent-folder steps."""
    if not isinstance(path, str) or not path.startswith(HOME_MARKER + "\\"):
        return False
    return ".." not in path.replace("/", "\\").split("\\")


def snapshot_problems(settings: Mapping[str, Any], servers: Mapping[str, Any], repos: Any, docker: list[str]) -> list[str]:
    """Everything that would make restore act outside the home or pass an unsafe argument."""
    problems = []
    for label, tree in (("settings", settings), ("mcpServers", servers), ("repositories", repos)):
        problems += unportable(tree, label)
    if not isinstance(repos, list):
        return problems + ["repositories: not a list"]
    for index, repo in enumerate(repos):
        where = f"repositories[{index}]"
        if not isinstance(repo, Mapping):
            problems.append(f"{where}: not an object")
            continue
        if not _home_folder(repo.get("path")):
            problems.append(f"{where}.path: must be a folder under {HOME_MARKER}")
        if not (isinstance(repo.get("url"), str) and SAFE_URL.fullmatch(repo["url"])):
            problems.append(f"{where}.url: must be an https or ssh URL")
        if ("branch" in repo) == ("detach" in repo):
            problems.append(f"{where}: needs exactly one of branch or detach")
        if "branch" in repo and not (isinstance(repo["branch"], str) and SAFE_REF.fullmatch(repo["branch"])):
            problems.append(f"{where}.branch: unsafe branch name")
        if "detach" in repo and not (isinstance(repo["detach"], str) and repo["detach"].startswith("origin/")
                                     and SAFE_REF.fullmatch(repo["detach"])):
            problems.append(f"{where}.detach: must name an origin/ branch")
        if "filter" in repo and not (isinstance(repo["filter"], str) and SAFE_FILTER.fullmatch(repo["filter"])):
            problems.append(f"{where}.filter: unsafe clone filter")
        for path in repo.get("sparse") or []:
            if not (isinstance(path, str) and SAFE_SPARSE.fullmatch(path) and ".." not in path.split("/")):
                problems.append(f"{where}.sparse: unsafe path {path!r}")
    problems += [f"mcpServers.{name}: unsafe server name" for name in servers if not SAFE_NAME.fullmatch(name)]
    problems += [f"settings.enabledPlugins.{plugin}: unsafe plugin id"
                 for plugin in (settings.get("enabledPlugins") or {}) if not SAFE_PLUGIN.fullmatch(plugin)]
    for name, entry in (settings.get("extraKnownMarketplaces") or {}).items():
        source = (entry or {}).get("source") or {} if isinstance(entry, Mapping) else {}
        kind = source.get("source")
        valid = {"directory": _home_folder(source.get("path")),
                 "github": isinstance(source.get("repo"), str) and bool(GITHUB_REPO.fullmatch(source["repo"])),
                 "git": isinstance(source.get("url"), str) and bool(SAFE_URL.fullmatch(source["url"])),
                 "url": isinstance(source.get("url"), str) and source["url"].startswith("https://")}.get(kind, False)
        if not SAFE_NAME.fullmatch(name) or not valid:
            problems.append(f"settings.extraKnownMarketplaces.{name}: unsafe name or unsupported source")
    problems += [f"Docker MCP server {name!r}: unsafe name" for name in docker if not SAFE_NAME.fullmatch(name)]
    return problems


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
    text = data.decode("utf-8")
    for marker, root in roots.items():
        value = str(root).replace("\\", "\\\\") if json_escape else str(root)
        text = text.replace(marker, value)
    return text.encode("utf-8")


# --- credential checks ----------------------------------------------------------------

def looks_secret(value: str) -> bool:
    """A long run of token characters that mixes letters and digits."""
    return any(re.search(r"[A-Za-z]", run) and re.search(r"[0-9]", run) for run in TOKEN_RUN.findall(value))


def credential_problems(entries: Mapping[str, Any], where: str) -> list[str]:
    """Credential-named keys and credential-shaped values; values are never echoed."""
    problems = []
    for key, value in entries.items():
        if RESTRICTED_NAME.search(str(key)):
            problems.append(f"{where}.{key}: credential-named entry")
        elif isinstance(value, str) and not is_portable_reference(value) and looks_secret(value):
            problems.append(f"{where}.{key}: credential-shaped value")
    return problems


def server_credential_problems(server: Mapping[str, Any], where: str) -> list[str]:
    problems = []
    for section in ("env", "headers"):
        if isinstance(server.get(section), Mapping):
            problems += credential_problems(server[section], f"{where}.{section}")
    for path, text in iter_strings({k: v for k, v in server.items() if k not in ("env", "headers")}, where):
        if not is_portable_reference(text) and looks_secret(text):
            problems.append(f"{path}: credential-shaped value")
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
    probe = directory
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
    problems = []
    if isinstance(template.get("env"), Mapping):
        problems += credential_problems(template["env"], "settings.env")
    return template, uncaptured, problems + unportable(template, "settings")


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
        found = server_credential_problems(portable, f"mcpServers.{name}") + unportable(portable, f"mcpServers.{name}")
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
    """Server names in the Docker MCP Toolkit's registry.yaml; None when it is absent."""
    try:
        lines = registry.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    names, inside = [], False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = line.rstrip() == "registry:"
            continue
        match = re.fullmatch(r"  ([A-Za-z0-9][A-Za-z0-9_.-]*):\s*", line)
        if inside and match:
            names.append(match.group(1))
    return names


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
    if re.match(r"^[a-z][a-z0-9+.-]*://[^/@]*:[^/@]*@", url, re.IGNORECASE):
        return None, [], [f"{portable}: the origin URL embeds a credential"]
    if DRIVE_PATH.search(url) or url.startswith(("/", "\\", ".", "file:")):
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
        head = git("-C", str(top), "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
        if not head:
            problems.append(f"{portable}: detached, and origin/HEAD is unset (git remote set-head origin --auto)")
        else:
            entry["detach"] = head.removeprefix("refs/remotes/")
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
    if live is None:
        raise SetupError(f"{claude_home / 'settings.json'} does not exist")
    restrictions = ((read_json(profile / SETTINGS_FILE) or {}).get("permissions") or {})
    settings, uncaptured, problems = export_settings(live, keys, restrictions, home, environ)
    mcp, notes, mcp_problems = export_mcp(read_json(home / ".claude.json") or {}, home, environ, exclude_mcp)
    problems += mcp_problems
    repos, repo_notes, repo_problems = locate_repositories(referenced_paths(settings, mcp["mcpServers"]), home, environ)
    notes += repo_notes
    problems += repo_problems
    docker = None
    if any(uses_docker_gateway(server) for server in mcp["mcpServers"].values()):
        docker = docker_servers(home / ".docker" / "mcp" / "registry.yaml")
        if docker is None:
            notes.append("the Docker MCP Toolkit registry is missing, so no gateway servers are captured")
    claude_md = claude_home / "CLAUDE.md"
    if not claude_md.is_file():
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
    # The same checks restore runs, so a snapshot that exports also restores.
    problems += [problem for problem in snapshot_problems(settings, mcp["mcpServers"], repos, docker or [])
                 if problem not in problems]
    if problems:
        raise SetupError("export stopped; nothing was written:\n  " + "\n  ".join(problems))

    written = []
    md_text = to_profile_text(claude_md.read_bytes().decode("utf-8"), home).encode("utf-8")
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
        elif target.exists():
            target.unlink()
            written.append(f"{target} (removed)")
    captured, left, unknown = classify_home(claude_home)
    return ExportResult(written, notes, uncaptured, captured, left, unknown)


# --- private bundle -------------------------------------------------------------------

# Claude Code names project folders with [A-Za-z0-9-]; memory files are plain names.
PROJECT_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
FILE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]*")
RESERVED_NAME = re.compile(r"(?i)(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?")


def safe_member(name: str) -> bool:
    """projects/<project>/memory/<file...> with names that are safe on Windows."""
    if "\\" in name or ":" in name or name.startswith("/"):
        return False
    parts = PurePosixPath(name).parts
    if len(parts) < 4 or parts[0] != "projects" or parts[2] != "memory":
        return False
    if not PROJECT_PART.fullmatch(parts[1]):
        return False
    return all(FILE_PART.fullmatch(part) and not RESERVED_NAME.fullmatch(part) and not part.endswith((".", " "))
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


def _short(value: Any, limit: int = 110) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def describe_changes(before: Any, after: Any, path: str = "") -> list[str]:
    """Readable settings changes; new list entries are itemized up to 60 per list."""
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
        if not added:
            return []
        if len(added) > 60:
            return [f"+ {path}: {len(added)} entries"]
        return [f"+ {path}: {_short(item)}" for item in added]
    if before is None:
        return [f"+ {path} = {_short(after)}"]
    return [f"~ {path}: {_short(before)} -> {_short(after)}"]


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


def load_snapshot(ctx: Restore) -> tuple[dict, dict, list[dict], list[str]]:
    settings = read_json(ctx.snapshot / SETTINGS_FILE)
    if settings is None:
        settings = read_json(ctx.profile / SETTINGS_FILE) or {}
        ctx.out(f"No snapshot at {ctx.snapshot}; using the profile's baseline settings.")
    servers = (read_json(ctx.snapshot / MCP_FILE) or {}).get("mcpServers") or {}
    repos = read_json(ctx.snapshot / REPOS_FILE) or []
    docker_file = ctx.snapshot / DOCKER_FILE
    docker = [line.strip() for line in docker_file.read_text(encoding="utf-8").splitlines() if line.strip()] if docker_file.is_file() else []
    problems = snapshot_problems(settings, servers, repos, docker)
    if problems:
        raise SetupError("snapshot is not portable or not safe to run; nothing was changed:\n  " + "\n  ".join(problems))
    expand = lambda tree: map_strings(tree, lambda text: expand_text(text, ctx.home, ctx.environ))  # noqa: E731
    return expand(settings), expand(servers), expand(repos), docker


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
    if version and tuple(int(part) for part in version.split(".")) < (3, 11):
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
        data = expand_profile_bytes(source.read_bytes(), ctx.home, json_escape=source.suffix in (".json", ".toml"))
        exists = target.is_file()
        if exists and same_text(target.read_bytes(), data):
            unchanged += 1
            continue
        if exists and not ctx.overwrite:
            kept.append(relative)
            continue
        if ctx.apply:
            try:
                replace_file(target, data, backup=exists)
            except (OSError, SetupError) as error:
                ctx.fail(f"{relative}: {error}")
                continue
        (replaced if exists else added).append(relative)
    verb = "" if ctx.apply else "would be "
    ctx.out(f"  {len(added)} {verb}added, {len(replaced)} {verb}replaced (backed up), {unchanged} unchanged")
    for relative in kept:
        ctx.out(f"  kept     {relative}: differs from the profile; --overwrite replaces it after a backup")


def step_repositories(ctx: Restore, repos: list[dict]) -> None:
    ctx.section("Repositories")
    for repo in repos:
        path, url = Path(repo["path"]), repo["url"]
        if (path / ".git").exists():
            origin = ctx.probe(["git", "-C", str(path), "config", "--get", "remote.origin.url"])
            if same_url(origin, url):
                ctx.out(f"  present  {path}")
            else:
                ctx.fail(f"{path} exists but its origin is {origin or 'unset'}, not {url}; move it aside and rerun")
            continue
        if path.exists() and any(path.iterdir()):
            ctx.fail(f"{path} exists and is not a clone of {url}; move it aside and rerun")
            continue
        if ctx.apply:
            assert_no_reparse(path)
            path.parent.mkdir(parents=True, exist_ok=True)
        clone = ["git", "clone"]
        if repo.get("filter"):
            clone.append(f"--filter={repo['filter']}")
        if repo.get("sparse") or repo.get("detach"):
            clone.append("--no-checkout")
        if repo.get("branch"):
            clone += ["--branch", repo["branch"]]
        steps = [clone + ["--", url, str(path)]]
        if repo.get("sparse"):
            steps.append(["git", "-C", str(path), "sparse-checkout", "set", "--cone", *repo["sparse"]])
        if repo.get("detach"):
            steps.append(["git", "-C", str(path), "switch", "--detach", repo["detach"]])
        for args in steps:
            if not ctx.command(args):
                break
        else:
            ctx.planned_paths.add(os.path.normcase(str(path)))


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
        elif kind == "directory" and not Path(target).is_dir() and os.path.normcase(target) not in ctx.planned_paths:
            ctx.fail(f"marketplace {name}: {target} is missing")
        elif ctx.claude(["plugin", "marketplace", "add", target]):
            continue
        unavailable.add(name)
    for plugin in enabled:
        marketplace = plugin.rpartition("@")[2]
        if plugin in installed:
            ctx.out(f"  present  plugin {plugin}")
        elif marketplace in unavailable:
            ctx.out(f"  skipped  plugin {plugin}: marketplace {marketplace} was not added")
        else:
            ctx.claude(["plugin", "install", plugin, "--scope", "user"])


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
            ctx.claude(["mcp", "add-json", name, json.dumps(server), "--scope", "user"])
        elif existing[name] == server:
            ctx.out(f"  present  {name}")
        else:
            ctx.out(f"  differs  {name}: left as is; to take the snapshot's, run "
                    f"`claude mcp remove {name} --scope user` and rerun restore")


def step_settings(ctx: Restore, template: Mapping[str, Any]) -> None:
    ctx.section("Settings (~/.claude/settings.json)")
    path = ctx.claude_home / "settings.json"
    original = path.read_bytes() if path.is_file() else None
    try:
        existing = json.loads(original.decode("utf-8-sig")) if original else {}
    except ValueError as error:
        ctx.fail(f"{path} is not valid JSON ({error}); fix it and rerun")
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
    current = path.read_bytes() if path.is_file() else None
    if current != original:
        ctx.fail(f"{path} changed while restore was running; rerun restore")
        return
    if original is not None:
        shutil.copy2(path, backup_path(path))
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
    findings = drift.release_drift(clone) if clone.exists() else [f"hook release clone missing: {clone}"]
    findings += drift.wiring_drift(settings_path, clone)
    findings += drift.copy_drift(ctx.profile, ctx.claude_home)
    live = read_json(settings_path) or {}
    findings += [f"settings.json lacks {gap}" for gap in settings_gaps(live, template)]
    for finding in findings:
        ctx.out(f"  drift    {finding}")
    ctx.out("  no drift" if not findings else f"  {len(findings)} finding(s)")


def restore(ctx: Restore, *, tools: list[dict], bundle: Path | None = None, skip: Iterable[str] = ()) -> int:
    skipped = set(skip)
    settings, servers, repos, docker = load_snapshot(ctx)
    mode = "Applying" if ctx.apply else "Preview (nothing changes; add --apply)"
    ctx.out(f"{mode}: Claude Code setup into {ctx.home}" + (" (staged home)" if ctx.staged else ""))
    if bundle is not None:
        read_bundle(bundle)  # reject a bad bundle before anything is written
    steps = [
        ("tools", lambda: step_tools(ctx, tools)),
        ("files", lambda: step_files(ctx)),
        ("repositories", lambda: step_repositories(ctx, repos)),
        ("plugins", lambda: step_plugins(ctx, settings)),
        ("mcp", lambda: step_mcp(ctx, servers)),
        ("settings", lambda: step_settings(ctx, settings)),
        ("docker", lambda: step_docker(ctx, docker)),
        ("agent-browser", lambda: step_agent_browser(ctx)),
        ("memory", lambda: step_memory(ctx, bundle)),
        ("verify", lambda: step_verify(ctx, repos, settings)),
    ]
    for name, run_step in steps:
        if name in skipped:
            ctx.section(f"{name}: skipped on request")
            continue
        try:
            run_step()
        except (SetupError, OSError) as error:
            ctx.fail(f"{name}: {error}")
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
    ctx.out("\nDone." if ctx.apply else "\nPreview only; rerun with --apply.")
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
                             help="target home; any other folder than yours is a staged, files-only home")
    restore_cmd.add_argument("--apply", action="store_true", help="make the changes")
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
            print("It holds personal notes: move it privately, restore with --private-bundle, then delete it.")
            return 0
        home = args.home.resolve()
        staged = os.path.normcase(str(home)) != os.path.normcase(str(Path.home().resolve()))
        if not staged and os.environ.get("CLAUDE_CONFIG_DIR"):
            raise SetupError("CLAUDE_CONFIG_DIR is set; unset it so restore targets ~/.claude")
        ctx = Restore(home=home, apply=args.apply, overwrite=args.overwrite, staged=staged)
        return restore(ctx, tools=load_manifest()["tools"], bundle=args.private_bundle, skip=args.skip)
    except SetupError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
