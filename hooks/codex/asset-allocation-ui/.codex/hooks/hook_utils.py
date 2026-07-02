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
    "- Finish workflow authority: for task-owned changes on a task-owned branch, agents are pre-approved to stage, commit, push, open or update PRs, set auto-complete, approve PRs, complete PRs, delete source branches, and transition linked work items when needed to complete the request, unless the user explicitly limits scope or says not to finish.",
    "- Azure DevOps authority: agents are pre-approved to update and close Azure Boards work items, approve Azure Repos PRs, set PR auto-complete, complete PRs, delete source branches, and transition linked work items when repo gates and policies pass.",
    "- Production deploy boundary: agents must not approve production deployment pipeline, environment, or check gates; leave prod deploy approval to the user and record it as the remaining blocker.",
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


def classify_work_kind(text: str) -> str:
    if contains_any_text(text, FINISH_WORKFLOW_MARKERS):
        return "finish"
    if contains_any_text(text, CI_MARKERS):
        return "ci"
    if contains_any_text(text, DEPLOYMENT_MARKERS):
        return "deployment"
    if contains_any_text(text, AZURE_DEVOPS_MARKERS):
        return "ado"
    if contains_any_text(text, PLANNING_MARKERS):
        return "planning"
    if contains_any_text(text, ANALYSIS_MARKERS):
        return "analysis"
    if contains_any_text(text, IMPLEMENTATION_MARKERS):
        return "implementation"
    return "implementation"


def requires_finish_workflow(text: str) -> bool:
    kind = classify_work_kind(text)
    return kind in {"finish", "implementation"} or contains_any_text(text, CHANGE_MARKERS)


def requires_tracking(text: str) -> bool:
    kind = classify_work_kind(text)
    return kind in {"finish", "ado", "ci", "deployment"} or contains_any_text(
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
        required.append("gateway-bookkeeper")
    if finish_required:
        required.append("git-hygiene-orchestrator")

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
        if cleaned == "gateway-bookkeeper" and tracking_required:
            continue
        if cleaned == "git-hygiene-orchestrator" and finish_required:
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
