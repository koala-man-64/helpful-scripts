import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


CORE_AGENTS = (
    "delivery-orchestrator-agent",
    "gateway-bookkeeper",
    "git-hygiene-orchestrator",
    "strict-branch-and-merge-discipline",
)

AZURE_DEVOPS_AGENT_AUTHORITY_LINES = (
    "- Azure DevOps authority: agents are pre-approved to update and close Azure Boards work items, approve Azure Repos PRs, set PR auto-complete, complete PRs, delete source branches, and transition linked work items when repo gates and policies pass.",
    "- Production deploy boundary: agents must not approve production deployment pipeline, environment, or check gates; leave prod deploy approval to the user and record it as the remaining blocker.",
    "- Azure permission failures are blockers to report with exact command and error; do not bypass policies, force permissions, or substitute direct protected-branch pushes.",
)


def azure_devops_agent_authority_lines() -> tuple[str, ...]:
    return AZURE_DEVOPS_AGENT_AUTHORITY_LINES


def read_hook_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def repo_root() -> Path:
    try:
        output = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if output:
            return Path(output)
    except Exception:
        pass

    # hook_utils.py lives in <repo>/.codex/hooks.
    try:
        return Path(__file__).resolve().parents[2]
    except Exception:
        return Path.cwd()


def run_git(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd or repo_root()),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode, result.stdout.strip()
    except Exception:
        return 1, ""


def repo_name(root: Path | None = None) -> str:
    return (root or repo_root()).name


def origin_url(root: Path | None = None) -> str:
    _, output = run_git(["remote", "get-url", "origin"], root)
    return output or "unavailable"


def current_branch(root: Path | None = None) -> str:
    code, output = run_git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    if code != 0 or not output:
        return "not-git"
    return output


def git_status_lines(root: Path | None = None) -> list[str]:
    code, output = run_git(["status", "--short", "--branch"], root)
    if code != 0:
        return []
    return output.splitlines()


def dirty_summary(root: Path | None = None) -> str:
    lines = git_status_lines(root)
    if not lines:
        return "not-git-or-clean"
    changes = [line for line in lines if not line.startswith("##")]
    if not changes:
        return "clean"
    return f"dirty ({len(changes)} pending path(s))"


def branch_header(root: Path | None = None) -> str:
    for line in git_status_lines(root):
        if line.startswith("##"):
            return line
    return ""


def upstream_gone(root: Path | None = None) -> bool:
    return "[gone]" in branch_header(root)


def skill_status(root: Path | None = None) -> tuple[list[str], list[str]]:
    skills_dir = (root or repo_root()) / ".codex" / "skills"
    present = []
    missing = []
    for agent in CORE_AGENTS:
        if (skills_dir / agent / "SKILL.md").exists():
            present.append(agent)
        else:
            missing.append(agent)
    return present, missing


def extract_command(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command
    command = payload.get("command")
    return command if isinstance(command, str) else ""


def extract_prompt(payload: dict[str, Any]) -> str:
    prompt = payload.get("prompt")
    return prompt if isinstance(prompt, str) else ""


def extract_last_message(payload: dict[str, Any]) -> str:
    message = payload.get("last_assistant_message")
    return message if isinstance(message, str) else ""


def additional_context(event_name: str, text: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": ascii_text(text),
        }
    }


def deny_pre_tool(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": ascii_text(reason),
        }
    }


def allow_pre_tool(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": ascii_text(reason),
        }
    }


def block(reason: str) -> dict[str, Any]:
    return {"decision": "block", "reason": ascii_text(reason)}


def emit_json(payload: dict[str, Any] | None) -> int:
    if payload:
        json.dump(payload, sys.stdout)
    return 0


def ascii_text(value: str) -> str:
    return value.encode("ascii", "replace").decode("ascii")


def normalized_command(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip()).lower()


PR_TITLE_PREFIX_PATTERN = re.compile(r"^\[[^\]\r\n]{1,160}\]\s+-\s+")


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".codex"


def _walk_payload_strings(value: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in keys and isinstance(item, str) and item.strip():
                return item.strip()
        for item in value.values():
            found = _walk_payload_strings(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _walk_payload_strings(item, keys)
            if found:
                return found
    return None


def current_codex_thread_id(payload: dict[str, Any] | None = None) -> str | None:
    for name in ("CODEX_THREAD_ID", "CODEX_CONVERSATION_ID", "CODEX_SESSION_ID"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    if payload:
        return _walk_payload_strings(
            payload,
            ("threadid", "thread_id", "conversationid", "conversation_id", "sessionid", "session_id"),
        )
    return None


def thread_name_from_session_index(thread_id: str | None = None) -> str | None:
    resolved_thread_id = thread_id or current_codex_thread_id()
    if not resolved_thread_id:
        return None
    index_path = _codex_home() / "session_index.jsonl"
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in reversed(lines):
        if resolved_thread_id not in line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("id") != resolved_thread_id:
            continue
        title = record.get("thread_name") or record.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return None


def codex_conversation_title(payload: dict[str, Any] | None = None) -> str | None:
    for name in ("CODEX_CONVERSATION_TITLE", "CODEX_THREAD_TITLE", "CODEX_SESSION_TITLE"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    if payload:
        found = _walk_payload_strings(
            payload,
            ("conversationtitle", "conversation_title", "threadtitle", "thread_title", "sessiontitle", "session_title"),
        )
        if found:
            return found
    return thread_name_from_session_index(current_codex_thread_id(payload))


def format_pull_request_title(
    existing_title: str,
    *,
    conversation_title: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    base_title = re.sub(r"\s+", " ", existing_title).strip()
    if not base_title:
        return ""

    title = re.sub(r"\s+", " ", conversation_title or codex_conversation_title(payload) or "").strip()
    if not title:
        return base_title

    prefix = f"[{title}] - "
    if base_title.startswith(prefix):
        return base_title
    return f"{prefix}{base_title}"


def pull_request_title_guidance(command: str) -> str | None:
    if not (
        re.search(r"\baz\s+repos\s+pr\s+create\b", command)
        or re.search(r"\bgh\s+pr\s+create\b", command)
    ):
        return None
    return (
        "Pull request title rule: use `[conversation name] - [existing title]`. "
        "Run `py -3 .codex/hooks/pr_title_helper.py \"<existing title>\"` to build the title; "
        "it resolves CODEX_THREAD_ID from local Codex session metadata and falls back to the existing title."
    )


def path_is_inside(path_text: str, root: Path | None = None) -> bool:
    if not path_text:
        return True
    try:
        path = Path(os.path.expandvars(path_text.strip("\"'")))
        if not path.is_absolute():
            return True
        base = (root or repo_root()).resolve()
        resolved = path.resolve()
        return resolved == base or base in resolved.parents
    except Exception:
        return True
