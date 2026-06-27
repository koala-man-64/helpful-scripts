import re
from pathlib import Path

from hook_utils import (
    additional_context,
    allow_pre_tool,
    azure_devops_agent_authority_lines,
    deny_pre_tool,
    dirty_summary,
    emit_json,
    extract_command,
    normalized_command,
    path_is_inside,
    pull_request_title_guidance,
    read_hook_input,
    repo_root,
    upstream_gone,
)


PRINT_COMMAND_PATTERN = re.compile(
    r"(?:^|[;&|]\s*)"
    r"(?:echo|write-output|write-host|printenv|env|set|cat|type|get-content)\b"
)
SECRET_VALUE_PATTERN = re.compile(
    r"(?<![a-z0-9])"
    r"(?:client_secret|connection_string|private_key|connectionstring|password|secret|token|oauth|pat)"
    r"(?![a-z0-9])"
)


PROD_DEPLOY_APPROVAL_PATTERN = re.compile(
    r"\b(?:prod|production|deploy-prod|prod-[a-z0-9-]*)\b"
)
AZURE_PIPELINE_APPROVAL_COMMAND_PATTERN = re.compile(
    r"\baz\s+pipelines\b.*\bapprov|\baz\s+devops\s+invoke\b.*\bapprov|\b(?:curl|invoke-restmethod|irm|iwr|invoke-webrequest)\b.*\bapprove-check\b"
)


def contains_destructive_git(command: str) -> str | None:
    checks = (
        (
            r"\bgit\s+reset\s+--hard\b",
            "git reset --hard is blocked. Preserve the current worktree and inspect diffs instead.",
        ),
        (
            r"\bgit\s+clean\b[^\n;|&]*(?:-[a-z]*f[a-z]*d|-[a-z]*d[a-z]*f)",
            "git clean with force/delete flags is blocked. Review untracked files and remove only explicit task-owned paths.",
        ),
        (
            r"\bgit\s+checkout\s+--\b",
            "git checkout -- is blocked because it can discard work. Inspect the file diff and request explicit approval before reverting.",
        ),
        (
            r"\bgit\s+restore\s+\.\b",
            "git restore . is blocked because it can discard broad worktree changes. Restore only explicit task-owned paths after approval.",
        ),
        (
            r"\bgit\s+branch\s+-D\b",
            "git branch -D is blocked. Use git branch -d only after proving the branch is merged and not checked out in any worktree.",
        ),
        (
            r"\bgit\s+push\b[^\n;|&]*(?:--force(?!-with-lease)|\s-f(?:\s|$))",
            "git push --force is blocked. Use the repo-approved sync path and avoid rewriting shared history.",
        ),
    )
    for pattern, reason in checks:
        if re.search(pattern, command):
            return reason
    if re.search(
        r"\bgit\s+push\s+(?:origin\s+)?(?:main|master|trunk|develop|staging|production)\b",
        command,
    ):
        return "Direct pushes to protected branches are blocked. Use a task branch and PR policy path."
    if re.search(
        r"\bgit\s+push\s+origin\s+head:(?:main|master|trunk|develop|staging|production)\b",
        command,
    ):
        return "Direct pushes to protected branches are blocked. Use a task branch and PR policy path."
    return None


def contains_secret_print(command: str) -> str | None:
    if not PRINT_COMMAND_PATTERN.search(command):
        return None
    if SECRET_VALUE_PATTERN.search(command):
        return "Command appears to print secret-bearing values. Do not echo tokens, PATs, OAuth codes, connection strings, or private keys."
    return None


def contains_prod_deploy_approval(command: str) -> str | None:
    if not PROD_DEPLOY_APPROVAL_PATTERN.search(command):
        return None
    if not AZURE_PIPELINE_APPROVAL_COMMAND_PATTERN.search(command):
        return None
    return (
        "Production deployment approvals are user-owned. Do not approve production "
        "pipeline, environment, or check gates; record the approval as the remaining blocker."
    )


def contains_external_recursive_delete_or_move(command: str, root: Path) -> str | None:
    patterns = (
        r"\brm\s+-[a-z]*r[a-z]*f?\s+([^\s;&|]+)",
        r"\bremove-item\b[^\n;|&]*\s-recurse\b[^\n;|&]*\s+([^\s;&|]+)",
        r"\brmdir\s+/s\b[^\n;|&]*\s+([^\s;&|]+)",
        r"\bmove-item\b\s+([^\s;&|]+)",
        r"\bmv\s+([^\s;&|]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, command)
        if match and not path_is_inside(match.group(1), root):
            return "Recursive delete or move outside the repository root is blocked. Restrict filesystem changes to explicit task-owned paths inside the workspace."
    return None


def contains_azure_devops_completion_command(command: str) -> bool:
    if "az boards work-item update" in command:
        return True
    if not re.search(r"\baz\s+repos\s+pr\b", command):
        return False
    return any(
        marker in command
        for marker in (
            "set-vote",
            "--vote",
            "--auto-complete",
            "--status completed",
            "--transition-work-items",
            "--delete-source-branch",
            "--squash",
        )
    )


def contains_finish_workflow_command(command: str) -> bool:
    if "git commit" in command:
        return True
    if re.search(r"\bgit\s+push\b", command):
        return True
    if re.search(r"\baz\s+repos\s+pr\s+create\b", command):
        return True
    if re.search(r"\bgh\s+pr\s+create\b", command):
        return True
    if re.search(r"\baz\s+repos\s+pr\s+update\b", command) and any(
        marker in command
        for marker in (
            "--auto-complete",
            "--status completed",
            "--delete-source-branch",
            "--squash",
            "--transition-work-items",
        )
    ):
        return True
    if re.search(r"\baz\s+repos\s+pr\s+set-vote\b", command):
        return True
    if re.search(r"\baz\s+repos\s+pr\b", command) and "--vote" in command:
        return True
    return False


def finish_workflow_permission_reason(command: str, root: Path) -> str | None:
    if not contains_finish_workflow_command(command):
        return None

    notes = [
        "Allowed finish workflow command after safety checks passed for commit, push, "
        "pull request, or merge/completion work."
    ]
    title_guidance = pull_request_title_guidance(command)
    if title_guidance:
        notes.append(title_guidance)
    status = dirty_summary(root)
    if status.startswith("dirty"):
        notes.append(
            f"Working tree is {status}; confirm task-owned scope before committing or pushing."
        )
    if upstream_gone(root):
        notes.append(
            "Current branch upstream is gone; confirm branch ownership before pushing."
        )
    if contains_azure_devops_completion_command(command):
        notes.append(
            "Azure DevOps PR/work-item completion authority applies only after branch "
            "policies and review rules pass."
        )
    return " ".join(notes)


def risky_context(command: str, root: Path) -> str | None:
    ado_completion_command = contains_azure_devops_completion_command(command)
    risky = (
        "git commit" in command
        or re.search(r"\bgit\s+push\b", command)
        or "az boards work-item update" in command
        or "az repos pr create" in command
        or ado_completion_command
    )
    if not risky:
        return None

    notes = []
    status = dirty_summary(root)
    if status.startswith("dirty"):
        notes.append(f"working tree is {status}")
    if upstream_gone(root):
        notes.append("current branch upstream is gone")
    if not notes and not ado_completion_command:
        return None

    context_lines = []
    if notes:
        context_lines.append(
            "Caution before running this command: "
            + "; ".join(notes)
            + ". Confirm task-owned scope, validation, and git-hygiene expectations before proceeding."
        )
    if ado_completion_command:
        context_lines.append("Azure DevOps authority reminder:")
        context_lines.extend(azure_devops_agent_authority_lines())
        context_lines.append(
            "Proceed only when branch policies, required checks, and review rules allow it."
        )
    return "\n".join(context_lines)


def main() -> int:
    payload = read_hook_input()
    command = normalized_command(extract_command(payload))
    if not command:
        return 0

    root = repo_root()
    for check in (
        contains_destructive_git,
        contains_secret_print,
        contains_prod_deploy_approval,
    ):
        reason = check(command)
        if reason:
            return emit_json(deny_pre_tool(reason))

    reason = contains_external_recursive_delete_or_move(command, root)
    if reason:
        return emit_json(deny_pre_tool(reason))

    allow_reason = finish_workflow_permission_reason(command, root)
    if allow_reason:
        return emit_json(allow_pre_tool(allow_reason))

    context = risky_context(command, root)
    if context:
        return emit_json(additional_context("PreToolUse", context))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
