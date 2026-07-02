#!/usr/bin/env python3
"""
Generate a sanitized, repo-local audit snapshot for governance reviews.

- Uses only Python stdlib.
- Avoids printing file contents or potential secrets.
- Focuses on inventory and workflow safety signals.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
}


def _run(cmd: List[str], cwd: Path) -> Optional[str]:
    try:
        out = subprocess.check_output(
            cmd, cwd=str(cwd), stderr=subprocess.STDOUT, text=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out.strip()


def _walk_files(repo: Path, target_name: str) -> List[str]:
    matches: List[str] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        if target_name in files:
            matches.append(str(Path(root, target_name).relative_to(repo)))
    return sorted(matches)


def _check_presence(repo: Path, candidates: List[str]) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for rel in candidates:
        out[rel] = (repo / rel).exists()
    return out


def _pipeline_signals(text: str) -> Dict[str, Any]:
    tasks = re.findall(r"(?m)^\s*task:\s*([^\s#]+)", text)
    tasks = [task.strip() for task in tasks if task.strip()]
    unversioned_tasks: List[str] = []
    versioned_tasks: List[str] = []
    for task in tasks:
        if "@" not in task:
            unversioned_tasks.append(task)
            continue
        _, version = task.rsplit("@", 1)
        if re.fullmatch(r"\d+(?:\.\d+)?", version):
            versioned_tasks.append(task)
        else:
            unversioned_tasks.append(task)

    return {
        "has_trigger": bool(re.search(r"(?m)^\s*trigger\s*:", text)),
        "has_pr": bool(re.search(r"(?m)^\s*pr\s*:", text)),
        "has_schedules": bool(re.search(r"(?m)^\s*schedules\s*:", text)),
        "has_extends": bool(re.search(r"(?m)^\s*extends\s*:", text)),
        "has_environment": bool(re.search(r"(?m)^\s*environment\s*:", text)),
        "has_persist_credentials": bool(
            re.search(r"(?im)^\s*persistCredentials\s*:\s*true\s*$", text)
        ),
        "references_system_access_token": "System.AccessToken" in text,
        "references_service_connection": bool(
            re.search(
                r"(?i)(serviceConnection|azureSubscription|connectedServiceName)",
                text,
            )
        ),
        "has_curl_pipe_shell": bool(re.search(r"curl[^\n]*\|\s*(bash|sh)\b", text)),
        "has_wget_pipe_shell": bool(re.search(r"wget[^\n]*\|\s*(bash|sh)\b", text)),
        "inline_script_count": len(
            re.findall(r"(?m)^\s*(script|bash|pwsh|powershell)\s*:", text)
        ),
        "task_count": len(tasks),
        "task_unversioned_count": len(unversioned_tasks),
        "task_unversioned_sample": sorted(set(unversioned_tasks))[:25],
        "task_versioned_count": len(versioned_tasks),
    }


_YAML_DOUBLE_QUOTE_ESCAPES = set('0abtnvfre "\\/N_LP')


def _yaml_syntax_errors(text: str) -> List[str]:
    """Return sanitized, best-effort YAML syntax errors without parsing values."""
    errors: List[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        in_double_quote = False
        index = 0
        while index < len(line):
            char = line[index]
            if char == '"' and not in_double_quote:
                in_double_quote = True
                index += 1
                continue
            if char == '"' and in_double_quote:
                in_double_quote = False
                index += 1
                continue
            if char == "\\" and in_double_quote:
                next_char = line[index + 1] if index + 1 < len(line) else ""
                if next_char not in _YAML_DOUBLE_QUOTE_ESCAPES:
                    errors.append(
                        f"line {line_number}: invalid escape in double-quoted scalar"
                    )
                    break
                index += 2
                continue
            index += 1
    return errors


def _codex_skills_snapshot(repo: Path) -> Dict[str, Any]:
    skills_dir = repo / ".codex" / "skills"
    if not skills_dir.exists():
        return {
            "present": False,
            "skill_directory_count": 0,
            "skill_md_count": 0,
            "openai_yaml_count": 0,
            "config_toml_zero_byte": False,
            "yaml_parse_errors": [],
        }

    yaml_parse_errors: List[str] = []
    for path in sorted(skills_dir.rglob("*.yaml")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            yaml_parse_errors.append(f"{path.relative_to(repo)}: unreadable")
            continue
        for error in _yaml_syntax_errors(text):
            yaml_parse_errors.append(f"{path.relative_to(repo)}: {error}")

    config_path = skills_dir / "config.toml"
    return {
        "present": True,
        "skill_directory_count": len([path for path in skills_dir.iterdir() if path.is_dir()]),
        "skill_md_count": len(list(skills_dir.glob("*/SKILL.md"))),
        "openai_yaml_count": len(list(skills_dir.glob("*/agents/openai.yaml"))),
        "config_toml_zero_byte": config_path.exists() and config_path.stat().st_size == 0,
        "yaml_parse_errors": yaml_parse_errors,
    }


def build_snapshot(repo: Path) -> Dict[str, Any]:
    repo = repo.resolve()

    snapshot: Dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo),
        "git": {},
        "instruction_files": {},
        "pipelines": {"paths": [], "files": []},
        "project_files": {},
        "azure_devops": {},
        "codex_skills": {},
    }

    # Git context (best-effort)
    if (repo / ".git").exists():
        snapshot["git"] = {
            "branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo),
            "commit": _run(["git", "rev-parse", "HEAD"], cwd=repo),
            "status_porcelain": _run(["git", "status", "--porcelain"], cwd=repo),
        }

    # Instruction files
    snapshot["instruction_files"] = {
        "AGENTS.md": _walk_files(repo, "AGENTS.md"),
        "CONTRIBUTING.md": _walk_files(repo, "CONTRIBUTING.md"),
        "SECURITY.md": _walk_files(repo, "SECURITY.md"),
    }

    # Common project files (root or conventional locations)
    snapshot["project_files"] = _check_presence(
        repo,
        [
            "README.md",
            "LICENSE",
            "pyproject.toml",
            "python/pyproject.toml",
            "requirements.txt",
            "requirements-dev.txt",
            "requirements.lock.txt",
            "requirements-dev.lock.txt",
            "requirements-ci.txt",
            "package.json",
            "pnpm-lock.yaml",
            "ts/package.json",
            "ts/pnpm-lock.yaml",
            ".editorconfig",
            ".pre-commit-config.yaml",
            "Dockerfile",
            "docker-compose.yml",
            "Makefile",
            "azure-pipelines.yml",
            "azure-pipelines.yaml",
            ".azuredevops",
            ".azdo",
            ".pipelines",
            "CODEOWNERS",
        ],
    )

    # Azure DevOps and Azure Pipelines quick checks
    snapshot["azure_devops"] = _check_presence(
        repo,
        [
            "azure-pipelines.yml",
            "azure-pipelines.yaml",
            ".azuredevops",
            ".azuredevops/pipelines",
            ".azdo",
            ".azdo/pipelines",
            ".pipelines",
        ],
    )

    snapshot["codex_skills"] = _codex_skills_snapshot(repo)

    pipeline_patterns = [
        "azure-pipelines.yml",
        "azure-pipelines.yaml",
        ".azuredevops/**/*.yml",
        ".azuredevops/**/*.yaml",
        ".azdo/**/*.yml",
        ".azdo/**/*.yaml",
        ".pipelines/**/*.yml",
        ".pipelines/**/*.yaml",
    ]
    pipeline_files = sorted(
        {
            path
            for pattern in pipeline_patterns
            for path in repo.glob(pattern)
            if path.is_file()
        }
    )
    snapshot["pipelines"]["paths"] = sorted(
        {
            str(path.parent.relative_to(repo))
            for path in pipeline_files
            if path.parent != repo
        }
    )
    for pipeline in pipeline_files:
        try:
            text = pipeline.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        snapshot["pipelines"]["files"].append(
            {
                "path": str(pipeline.relative_to(repo)),
                "signals": _pipeline_signals(text),
            }
        )

    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a repo audit snapshot (sanitized)."
    )
    parser.add_argument("--repo", default=".", help="Path to repo root (default: .)")
    parser.add_argument(
        "--out", default=None, help="Write JSON output to this path (default: stdout)"
    )
    args = parser.parse_args()

    snapshot = build_snapshot(Path(args.repo))
    encoded = json.dumps(snapshot, indent=2, sort_keys=True)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(encoded + "\n", encoding="utf-8")
        print(f"Wrote audit snapshot: {out_path}")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
