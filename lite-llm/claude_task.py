"""Dispatch resumable background tasks to the existing Windows Claude CLI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

INSTRUCTIONS_PATH = Path(__file__).with_name("AGENT_INSTRUCTIONS.md")


def conversation_instructions() -> str:
    """Fail before dispatch if the required agent guidance cannot be loaded."""
    try:
        instructions = INSTRUCTIONS_PATH.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError("Cannot read required AGENT_INSTRUCTIONS.md; restore it before dispatch.") from exc
    if not instructions:
        raise ValueError("Required AGENT_INSTRUCTIONS.md is empty; restore it before dispatch.")
    return instructions


def task_command(executable: str, prompt: str,
                 model: str | None = None, settings: Path | None = None) -> list[str]:
    """Keep task text as arguments, never shell source."""
    if not prompt.strip():
        raise ValueError("The task prompt must not be empty.")
    command = [executable, "--background", "--append-system-prompt", conversation_instructions()]
    if model is not None:
        if not model.strip():
            raise ValueError("The model must not be empty.")
        command.append("--model=" + model)
    if settings is not None:
        if not settings.is_file():
            raise ValueError("The settings file does not exist.")
        command.append("--settings=" + str(settings.resolve()))
    return [*command, "--", prompt]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", required=True, type=Path)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", help="Optional Claude model or LiteLLM alias")
    parser.add_argument("--settings", type=Path, help="Existing Claude settings JSON")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    cwd = args.cwd.resolve()
    if not cwd.is_dir():
        parser.error("--cwd must be an existing directory")
    executable = shutil.which("claude.exe")
    if not executable:
        parser.error("claude.exe is not on PATH; Windows Claude CLI is required")
    try:
        command = task_command(executable, args.prompt, args.model, args.settings)
    except ValueError as exc:
        parser.error(str(exc))
    if args.dry_run:
        print(json.dumps({"cwd": str(cwd), "argv": [*command[:-1], "<prompt omitted>"]}))
        return 0
    try:
        result = subprocess.run(command, cwd=cwd, shell=False, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        print("Dispatch outcome unknown. Inspect claude agents --all --json before retrying.", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Could not launch Claude: {exc}", file=sys.stderr)
        return 2
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
