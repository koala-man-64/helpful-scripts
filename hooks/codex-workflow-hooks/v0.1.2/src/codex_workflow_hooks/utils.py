"""Small deterministic utilities shared by hook handlers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


SECRET_NAME_RE = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|PASSWD|PAT|API_KEY|PRIVATE_KEY|CONNECTION_STRING)(?:$|_)",
    re.IGNORECASE,
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=False)
        handle.write("\n")
    os.replace(temporary, path)


def canonical_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=False)
    except OSError:
        return Path(os.path.abspath(os.path.expandvars(str(path))))


def is_within(path: Path, root: Path) -> bool:
    candidate = os.path.normcase(str(canonical_path(path)))
    boundary = os.path.normcase(str(canonical_path(root)))
    try:
        return os.path.commonpath((candidate, boundary)) == boundary
    except ValueError:
        return False


def canonical_origin(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    azure_ssh = re.match(
        r"^git@ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/(.+?)(?:\.git)?$",
        raw,
        re.IGNORECASE,
    )
    if azure_ssh:
        org, project, repo = azure_ssh.groups()
        return f"azure://{org.lower()}/{project.lower()}/{repo.lower()}"
    if raw.startswith(("http://", "https://")):
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower()
        path = parsed.path.rstrip("/")
        path = path[:-4] if path.lower().endswith(".git") else path
        match = re.match(r"^/([^/]+)/([^/]+)/_git/(.+)$", path, re.IGNORECASE)
        if host == "dev.azure.com" and match:
            org, project, repo = match.groups()
            return f"azure://{org.lower()}/{project.lower()}/{repo.lower()}"
        clean = urlunsplit((parsed.scheme.lower(), host, path.lower(), "", ""))
        return clean
    return raw.replace("\\", "/").rstrip("/").lower()


def run_process(
    arguments: Iterable[str],
    *,
    cwd: Path | None = None,
    timeout: float = 2.0,
    environment: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            list(arguments),
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
        )
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", exc.__class__.__name__


def run_git(cwd: Path, *arguments: str, timeout: float = 2.0) -> tuple[int, str, str]:
    environment = minimal_environment()
    environment.update(
        {
            "GCM_INTERACTIVE": "never",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return run_process(
        (
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-C",
            str(cwd),
            *arguments,
        ),
        timeout=timeout,
        environment=environment,
    )


def minimal_environment() -> dict[str, str]:
    """Environment for event-time Git probes; intentionally excludes secret-shaped names."""
    allow = {
        "COMSPEC",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in allow and not SECRET_NAME_RE.search(key)
    }


def parse_exit_code(response: Any) -> int | None:
    if isinstance(response, dict):
        for key in ("exit_code", "exitCode", "returncode", "status"):
            value = response.get(key)
            if isinstance(value, int):
                return value
        metadata = response.get("metadata")
        if isinstance(metadata, dict):
            nested = parse_exit_code(metadata)
            if nested is not None:
                return nested
    if isinstance(response, str):
        match = re.search(r"(?im)\bExit code:\s*(-?\d+)\b", response)
        if match:
            return int(match.group(1))
    return None


def redact_text(value: str) -> str:
    redacted = re.sub(
        r"(?i)\b(token|secret|password|passwd|api[_-]?key)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        value,
    )
    redacted = re.sub(r"(?i)(https?://)([^/@\s]+)@", r"\1<redacted>@", redacted)
    return redacted[:1000]
