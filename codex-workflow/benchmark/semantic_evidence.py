"""Runner-owned workspace observations and strict CLI evidence normalization."""
from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from pathlib import Path
from typing import Mapping


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def verified_reference(reference: Mapping) -> Path:
    if set(reference) != {"path", "digest"}:
        raise ValueError("raw reference needs an exact path and digest")
    path = Path(reference["path"]).resolve(strict=True)
    if not path.is_file() or file_digest(path) != reference["digest"]:
        raise ValueError("raw artifact bytes differ from the retained digest")
    return path


def inventory(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts or "__pycache__" in relative.parts:
            continue
        if path.is_symlink() or path.is_junction():
            raise ValueError("semantic workspace contains a linked path")
        if path.is_file():
            result[relative.as_posix()] = file_digest(path)
    return result


def git_value(root: Path, *arguments: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True,
                            text=True, check=True, timeout=15)
    return result.stdout.strip()


def capture_workspace(root: Path) -> dict:
    root = root.resolve(strict=True)
    repositories = {}
    for relative in (".", "upstream", "downstream"):
        target = root / relative
        if not (target / ".git").exists():
            continue
        state = {"head": git_value(target, "rev-parse", "HEAD"),
                 "branch": git_value(target, "branch", "--show-current"),
                 "status": git_value(target, "status", "--porcelain"),
                 "path": str(target.resolve())}
        try:
            state["target_head"] = git_value(target, "rev-parse", "refs/heads/task/pinned")
        except subprocess.CalledProcessError:
            state["target_head"] = None
        repositories[relative] = state
    return {"schema_version": "benchmark-workspace-observation-v1",
            "workspace": str(root), "files": inventory(root), "repositories": repositories}


def command_argv(command: str) -> list[str]:
    """Opaque shell programs are preserved as unsupported evidence."""
    if not isinstance(command, str) or any(char in command for char in ";&|`$\n\r><"):
        raise ValueError("opaque shell program")
    argv = [part.strip("\"'") for part in shlex.split(command, posix=False)]
    if not argv:
        raise ValueError("empty command")
    executable = Path(argv[0].replace("\\", "/")).name.lower()
    if executable in {"pwsh", "pwsh.exe", "powershell", "powershell.exe", "bash", "sh"}:
        options = [part.lower() for part in argv[1:-1]]
        if options in (["-command"], ["-noprofile", "-command"], ["-c"], ["-lc"]):
            return command_argv(argv[-1])
        raise ValueError("unsupported shell wrapper")
    if executable in {"python", "python.exe", "python3", "python3.exe"}:
        argv[0] = "python"
    elif executable in {"py", "py.exe"} and argv[1:2] == ["-3"]:
        argv = ["python", *argv[2:]]
    return argv


def read_cli_trace(path: Path, expected_session: str) -> list[dict]:
    sessions, completed, started, seen, records = [], 0, set(), set(), []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        kind = event.get("type")
        if kind == "thread.started":
            sessions.append(event.get("thread_id"))
        elif kind in {"turn.failed", "error"}:
            raise ValueError("failed trace cannot establish semantic acceptance")
        elif kind == "turn.completed":
            completed += 1
        elif kind in {"item.started", "item.updated", "item.completed"}:
            item = event["item"]
            identity = item["id"]
            if kind == "item.started":
                started.add(identity)
            if kind != "item.completed":
                continue
            if identity in seen:
                raise ValueError("duplicate completed CLI item")
            seen.add(identity)
            if item["type"] == "command_execution":
                try:
                    argv = command_argv(item["command"])
                except ValueError:
                    argv = None
                records.append({"type": "command_execution", "argv": argv,
                                "exit_code": item.get("exit_code"), "status": item.get("status"),
                                "output": item.get("aggregated_output", "")})
            elif item["type"] == "file_change":
                records.append({"type": "file_change", "changes": item.get("changes"), "status": item.get("status")})
            elif item["type"] not in {"agent_message", "reasoning", "todo_list"}:
                records.append({"type": "unsupported", "tool": item["type"]})
        elif kind != "turn.started":
            raise ValueError(f"unknown CLI event: {kind}")
    if sessions != [expected_session] or completed != 1 or not started.issubset(seen):
        raise ValueError("incomplete or mixed CLI task trace")
    return records


def local_read_command(row: Mapping, workspace: Path, allowed_files: set[str]) -> bool:
    if row.get("type") != "command_execution" or row.get("status") != "completed" or row.get("exit_code") != 0:
        return False
    argv = row.get("argv")
    if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
        return False
    path = None
    if len(argv) == 2 and argv[0].lower() in {"cat", "get-content", "type", "sha256sum"}:
        path = argv[1]
    elif len(argv) == 5 and [word.lower() for word in argv[:4]] == ["get-filehash", "-algorithm", "sha256", "-literalpath"]:
        path = argv[4]
    if path is None:
        return False
    target = (workspace / path).resolve()
    return any(target == (workspace / name).resolve() for name in allowed_files)
