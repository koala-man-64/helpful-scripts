"""Shared helpers for the global Claude Code team-workflow hooks.

Ported from the per-repo Codex hooks (.codex/hooks/hook_utils.py) so the same
team workflow applies in Claude Code across all projects. Claude-specific
differences:
- Stop hooks receive a transcript_path instead of last_assistant_message,
  so extract_last_message() falls back to parsing the transcript JSONL.
- Core agent presence is checked in .claude/agents/ (repo) and ~/.claude/agents/.
- Codex session/thread title helpers are dropped (no Claude equivalent).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


CORE_AGENTS = (
    "delivery-orchestrator-agent",
    "project-workflow-enforcer-agent",
)

# The gateway-bookkeeper agent no longer exists as a Claude definition; the
# obligation it carried does. Name the outcome so routing does not point at a
# definition that cannot be invoked.
TRACKING_STEP = "Azure DevOps tracking"

AZURE_DEVOPS_AGENT_AUTHORITY_LINES = (
    "- Finish workflow authority: for task-owned changes on a task-owned branch, agents are pre-approved to stage, commit, push, open or update PRs, set auto-complete, approve PRs, complete PRs, delete source branches, and transition linked work items when needed to complete the request, unless the user explicitly limits scope or says not to finish.",
    "- Azure DevOps authority: agents are pre-approved to update and close Azure Boards work items, approve Azure Repos PRs, set PR auto-complete, complete PRs, delete source branches, and transition linked work items when repo gates and policies pass.",
    "- Production deploy boundary: agents are pre-approved to queue deployment pipeline runs, including production ones, and to read run and gate status. Agents must not approve production deployment pipeline, environment, or check gates; leave prod deploy approval to the user and record it as the remaining blocker.",
    "- Azure permission failures are blockers to report with exact command and error; do not bypass policies, force permissions, or substitute direct protected-branch pushes.",
)

FINISH_WORKFLOW_MARKERS = (
    "finish it",
    "complete workflow",
    "complete your workflow",
    "commit",
    "pushed",
    "push",
    "pull request",
    " pr ",
    "merge",
    "squash",
    "auto-complete",
    "approve pr",
    "approve pull request",
    "complete pr",
    "complete pull request",
    "opened pr",
    "created pr",
    "close work item",
    "close workitem",
    "complete work item",
    "complete workitem",
    "transition-work-items",
)

AZURE_DEVOPS_MARKERS = (
    "azure devops",
    "azure boards",
    "az boards",
    "work item",
    "workitem",
    "ab#",
    "boards",
    "bookkeeper",
    "sprint",
)

CI_MARKERS = (
    "pipeline",
    "build failed",
    "failed build",
    "failing check",
    "failed check",
    " ci ",
    "ci/cd",
    "validation failed",
    "re-queue",
    "rerun",
)

DEPLOYMENT_MARKERS = (
    "deploy",
    "deployment",
    "release",
    "production",
    " prod ",
    "environment approval",
)

PLANNING_MARKERS = (
    "plan",
    "proposal",
    "approach",
    "design",
    "architecture",
    "tradeoff",
    "proposed_plan",
    "planning-only",
    "plan only",
)

ANALYSIS_MARKERS = (
    "analyze",
    "audit",
    "review",
    "inspect",
    "investigate",
    "summarize",
    "explain",
    "compare",
)

IMPLEMENTATION_MARKERS = (
    "implement",
    " fix ",
    "update",
    " add ",
    "remove",
    "delete",
    " edit ",
    " wire ",
    "configure",
    "refactor",
    "install",
)

EXPLICIT_IMPLEMENTATION_MARKERS = (
    "apply this change",
    "make the change",
    "get rid of",
    "ship it",
    "please fix",
    "please update",
    "please remove",
    "please add",
)

QUESTION_ANALYSIS_MARKERS = (
    "what updates need to be made",
    "what changes need to be made",
    "what needs to change",
    "need to be made",
    "should be changed",
)

NO_REMOTE_FINISH_MARKERS = (
    "local only",
    "local-only",
    "read-only",
    "question only",
    "analysis only",
    "planning only",
    "no push",
    "don't push",
    "dont push",
    "no pr",
    "no pull request",
    "no merge",
    "no azure devops",
    "skip push",
    "skip pr",
    "skip merge",
    "do not mutate azure devops",
)

CHANGE_MARKERS = (
    "added",
    "updated",
    "changed",
    "implemented",
    "wired",
    "created",
    "removed",
    "deleted",
    "patched",
    "refactored",
    "configured",
    "fixed",
    "installed",
    "edited",
    "propagated",
    "deployed",
)

MULTI_REPO_MARKERS = (
    "multi-repo",
    "multirepo",
    "cross-repo",
    "multiple repos",
    "ported to",
    "propagated",
)

TRACKING_CLAIM_MARKERS = (
    "bookkeeper recap",
    "gateway-bookkeeper recap",
    "tracked",
    "recorded",
    "updated azure",
    "updated boards",
    "closed work item",
    "transitioned work item",
    "linked work item",
)


def azure_devops_agent_authority_lines() -> tuple[str, ...]:
    return AZURE_DEVOPS_AGENT_AUTHORITY_LINES


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", f" {value.lower()} ")


def contains_any_text(text: str, markers: tuple[str, ...]) -> bool:
    normalized = normalize_text(text)
    return any(marker in normalized for marker in markers)


def looks_like_question_only(text: str) -> bool:
    raw = text.lower().strip()
    if not raw:
        return False

    starts_with_question = raw.startswith(
        ("what ", "why ", "how ", "which ", "who ", "when ", "where ")
    )
    ends_with_question = raw.endswith("?")
    if not (
        starts_with_question
        or ends_with_question
        or contains_any_text(text, QUESTION_ANALYSIS_MARKERS)
    ):
        return False

    return not contains_any_text(
        text,
        FINISH_WORKFLOW_MARKERS
        + EXPLICIT_IMPLEMENTATION_MARKERS
        + ("implement", "fix it", "please apply"),
    )


def classify_work_kind(text: str) -> str:
    if contains_any_text(text, FINISH_WORKFLOW_MARKERS):
        return "finish"
    if contains_any_text(text, CI_MARKERS):
        return "ci"
    if contains_any_text(text, DEPLOYMENT_MARKERS):
        return "deployment"
    if contains_any_text(text, AZURE_DEVOPS_MARKERS):
        return "ado"
    if looks_like_question_only(text):
        return "analysis"
    if contains_any_text(text, PLANNING_MARKERS):
        return "planning"
    if contains_any_text(text, ANALYSIS_MARKERS):
        return "analysis"
    if contains_any_text(text, IMPLEMENTATION_MARKERS + EXPLICIT_IMPLEMENTATION_MARKERS):
        return "implementation"
    return "implementation"


def requires_finish_workflow(text: str) -> bool:
    if contains_any_text(text, NO_REMOTE_FINISH_MARKERS):
        return False
    return classify_work_kind(text) in {"finish", "implementation"}


def requires_tracking(text: str) -> bool:
    if contains_any_text(text, NO_REMOTE_FINISH_MARKERS):
        return False
    kind = classify_work_kind(text)
    return kind in {"finish", "implementation", "ado", "ci", "deployment"} or contains_any_text(
        text, MULTI_REPO_MARKERS + TRACKING_CLAIM_MARKERS
    )


def is_planning_or_analysis_only(text: str) -> bool:
    kind = classify_work_kind(text)
    return kind in {"planning", "analysis"} and not (
        requires_finish_workflow(text)
        or requires_tracking(text)
        or contains_any_text(text, CHANGE_MARKERS)
    )


def requires_git_hygiene(text: str) -> bool:
    return requires_finish_workflow(text) or contains_any_text(
        text, CHANGE_MARKERS + ("committed", "pushed", "opened pr", "created pr")
    )


def requires_bookkeeper_recap(text: str) -> bool:
    if contains_any_text(text, TRACKING_CLAIM_MARKERS):
        return True
    if requires_tracking(text) and contains_any_text(
        text, CHANGE_MARKERS + ("committed", "pushed", "opened pr", "created pr")
    ):
        return True
    auditable_markers = (
        AZURE_DEVOPS_MARKERS
        + CI_MARKERS
        + DEPLOYMENT_MARKERS
        + ("pull request", " pr ", "merge", "multi-repo", "cross-repo")
    )
    return contains_any_text(text, auditable_markers) and contains_any_text(
        text, CHANGE_MARKERS + ("committed", "pushed", "opened pr", "created pr")
    )


def compact_agent_summary(
    sequence: str, *, tracking_required: bool, finish_required: bool
) -> tuple[str, str]:
    required = ["delivery-orchestrator-agent"]
    if tracking_required:
        required.append(TRACKING_STEP)
    if finish_required:
        required.append("git finish workflow")

    optional: list[str] = []
    for raw_part in sequence.split("->"):
        part = raw_part.strip()
        if not part:
            continue
        cleaned = (
            part.replace(" as needed", "")
            .replace(" when tracked", "")
            .replace("relevant ", "")
            .strip()
        )
        if not cleaned or cleaned in required:
            continue
        if cleaned == TRACKING_STEP and tracking_required:
            continue
        if cleaned == "git finish workflow" and finish_required:
            continue
        if cleaned not in optional:
            optional.append(cleaned)

    required_text = ", ".join(required) if required else "none"
    optional_text = ", ".join(optional) if optional else "none"
    return required_text, optional_text


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


def agent_status(root: Path | None = None) -> tuple[list[str], list[str]]:
    """Report which core team definitions are available for this repo.

    A definition may be an agent (.claude/agents/<name>.md) or a skill
    (.claude/skills/<name>/SKILL.md), at repo or user level. Definitions
    that must constrain the live turn are skills, so checking only
    agents/ reports them missing when they are in fact loaded.
    """
    roots = ((root or repo_root()) / ".claude", Path.home() / ".claude")
    present = []
    missing = []
    for agent in CORE_AGENTS:
        found = any(
            (base / "agents" / f"{agent}.md").exists()
            or (base / "skills" / agent / "SKILL.md").exists()
            for base in roots
        )
        (present if found else missing).append(agent)
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


def _text_from_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)
    return ""


def extract_last_message(payload: dict[str, Any]) -> str:
    """Return the final assistant message for Stop hooks.

    Claude Code Stop hooks provide a transcript_path (JSONL) rather than the
    message itself, so parse the transcript from the end.
    """
    message = payload.get("last_assistant_message")
    if isinstance(message, str) and message.strip():
        return message

    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return ""
    try:
        lines = Path(transcript_path).read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return ""

    for line in reversed(lines):
        if '"assistant"' not in line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") != "assistant" or record.get("isSidechain"):
            continue
        text = _text_from_message_content(
            (record.get("message") or {}).get("content")
        )
        if text.strip():
            return text
    return ""


MUTATING_TOOLS = frozenset(
    {"Edit", "Write", "NotebookEdit", "MultiEdit"}
)

SHELL_TOOLS = frozenset({"Bash", "PowerShell"})

MUTATING_COMMAND_PATTERN = re.compile(
    r"\bgit\s+(?:add|commit|push|merge|rebase|revert|cherry-pick|tag|am|apply)\b"
    r"|\bgit\s+(?:branch|checkout|switch|restore|reset|clean|stash)\b"
    r"|\baz\s+(?:repos|boards|pipelines)\b"
    r"|\bgh\s+(?:pr|issue|release)\s+(?:create|merge|edit|close|comment)\b"
)


def _is_real_user_turn(record: dict[str, Any]) -> bool:
    """True when this user record is an actual prompt, not a tool result."""
    if record.get("type") != "user" or record.get("isSidechain"):
        return False
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return any(
            isinstance(block, dict) and block.get("type") == "text"
            for block in content
        )
    return False


def turn_did_work(payload: dict[str, Any]) -> bool:
    """Report whether the current turn actually changed anything.

    Walks the transcript backwards to the most recent real user prompt and
    looks for tool calls that mutate files or git/Azure DevOps state. Pure
    question-and-answer turns return False, so the closeout hooks can skip
    enforcement instead of matching on words like "updated" or "changed"
    that appear in ordinary explanations.
    """
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        return True

    try:
        lines = Path(transcript_path).read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return True

    for line in reversed(lines):
        if '"user"' not in line and '"assistant"' not in line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if _is_real_user_turn(record):
            return False

        if record.get("type") != "assistant" or record.get("isSidechain"):
            continue
        content = (record.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if name in MUTATING_TOOLS:
                return True
            if name in SHELL_TOOLS:
                command = (block.get("input") or {}).get("command")
                if isinstance(command, str) and MUTATING_COMMAND_PATTERN.search(
                    command.lower()
                ):
                    return True
    return False


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


def ask_pre_tool(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
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


def pull_request_title_guidance(command: str) -> str | None:
    if not (
        re.search(r"\baz\s+repos\s+pr\s+create\b", command)
        or re.search(r"\bgh\s+pr\s+create\b", command)
    ):
        return None
    return (
        "Pull request title rule: use `[session topic] - [existing title]`, "
        "where the session topic is a short name for this conversation's task."
    )


def path_is_inside(path_text: str, root: Path | None = None) -> bool:
    """True when the path is inside the repo root, or a scratch location.

    Relative paths and unparseable input are treated as inside, so the guard
    only fires on clearly external absolute paths. The OS temp directory is
    allowed too: agents routinely create and clean up scratch files there, and
    blocking that produced false positives without protecting anything.
    """
    if not path_text:
        return True
    try:
        path = Path(os.path.expandvars(path_text.strip("\"'")))
        if not path.is_absolute():
            return True
        resolved = path.resolve()
        bases = [(root or repo_root()).resolve()]
        for scratch in (tempfile.gettempdir(), os.environ.get("TEMP"), os.environ.get("TMP")):
            if scratch:
                try:
                    bases.append(Path(scratch).resolve())
                except OSError:
                    continue
        return any(
            resolved == base or base in resolved.parents for base in bases
        )
    except Exception:
        return True
