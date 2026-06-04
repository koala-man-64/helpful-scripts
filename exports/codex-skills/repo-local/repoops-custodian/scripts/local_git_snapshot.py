#!/usr/bin/env python3
"""Collect a read-only local Git evidence snapshot for RepoOps Custodian."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization:\s*)(\S+)"),
    re.compile(r"(?i)(token|pat|client_secret|password|secret)(\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(AZURE_DEVOPS_EXT_PAT=)(\S+)"),
]


def redact(text: str) -> str:
    value = text
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 3:
            value = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}REDACTED", value)
        else:
            value = pattern.sub(lambda m: f"{m.group(1)}REDACTED", value)
    return value


def run(repo: Path, command: list[str], timeout: int) -> dict[str, Any]:
    started = datetime.now(timezone.utc).isoformat()
    try:
        completed = subprocess.run(
            command,
            cwd=str(repo),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "command": command,
            "startedAt": started,
            "exitCode": completed.returncode,
            "stdout": redact(completed.stdout),
            "stderr": redact(completed.stderr),
        }
    except Exception as exc:  # noqa: BLE001 - evidence collection must not hide failures.
        return {
            "command": command,
            "startedAt": started,
            "exitCode": None,
            "stdout": "",
            "stderr": redact(f"{type(exc).__name__}: {exc}"),
        }


def build_commands(include_network_dry_run: bool) -> list[list[str]]:
    commands = [
        ["git", "rev-parse", "--show-toplevel"],
        ["git", "rev-parse", "HEAD"],
        ["git", "status", "--porcelain=v1", "-b"],
        ["git", "remote", "-v"],
        ["git", "worktree", "list", "--porcelain"],
        ["git", "branch", "--color=never", "-vv"],
        [
            "git",
            "for-each-ref",
            "--format=%(refname)|%(objectname)|%(committerdate:iso8601)|%(upstream:short)|%(upstream:track)",
            "refs/heads",
            "refs/remotes",
        ],
        ["git", "branch", "--color=never", "--merged"],
        ["git", "branch", "--color=never", "--no-merged"],
        ["git", "show-ref", "--tags"],
        ["git", "worktree", "prune", "--dry-run"],
    ]
    if include_network_dry_run:
        commands.append(["git", "fetch", "--all", "--prune", "--dry-run"])
    return commands


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect read-only Git evidence for repo maintenance reports."
    )
    parser.add_argument(
        "--repo",
        default=os.getcwd(),
        help="Repository path. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--include-network-dry-run",
        action="store_true",
        help="Also run networked Git dry-run commands such as fetch --prune --dry-run.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Per-command timeout in seconds.",
    )
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    snapshot = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "repo": str(repo),
        "mode": "read-only-local-git-snapshot",
        "commands": [
            run(repo, command, args.timeout)
            for command in build_commands(args.include_network_dry_run)
        ],
    }
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
