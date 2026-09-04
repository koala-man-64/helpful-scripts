from pathlib import Path

import wait_registry
from agent_ladder import is_managed, ladder_summary
from hook_utils import (
    additional_context,
    agent_status,
    branch_header,
    current_branch,
    dirty_summary,
    emit_json,
    repo_name,
    repo_root,
    run_git,
    workflow_scope_enabled,
)


def outstanding_waits() -> list[str]:
    """Waits from earlier sessions that have not reached a terminal status.

    Without this a wait survives in the registry but nothing tells the next
    session it exists, which is how Codex heartbeats die: they are attached to
    a thread and vanish with it.
    """
    try:
        rows = wait_registry.active()
    except Exception:
        return []
    if not rows:
        return []
    lines = ["Outstanding delivery waits (from earlier sessions):"]
    for row in rows:
        expired = " - PAST TIMEOUT" if wait_registry.is_expired(row) else ""
        lines.append(f"- {row['wait_id']}: {wait_registry.describe(row)}{expired}")
    script = Path.home() / ".claude" / "hooks" / "wait_poll.py"
    lines.append(
        f'Poll with: py "{script}" poll --all. '
        "Registration is not delivery evidence; a wait is resolved only by a terminal status."
    )
    return lines


def main() -> int:
    root = repo_root()
    waits = outstanding_waits()
    if not workflow_scope_enabled(root):
        # Team routing is repository-scoped, but an outstanding wait is not:
        # it belongs to whatever operation was launched, wherever that was.
        if waits:
            return emit_json(additional_context("SessionStart", "\n".join(waits)))
        return emit_json(None)
    _present, missing = agent_status(root)
    missing_text = ", ".join(missing) if missing else "none"
    header = branch_header(root) or current_branch(root)

    context = "\n".join(
        [
            "Team workflow context:",
            f"- Repo: {repo_name(root)}",
            f"- Root: {root}",
            f"- Branch: {header}",
            f"- Working tree: {dirty_summary(root)}",
            f"- Core team definitions missing: {missing_text}",
            "- Follow CLAUDE.md and prefer repo-local .claude/agents and .claude/skills definitions.",
            "- Start substantive work through delivery-orchestrator-agent.",
            "- Record Azure DevOps tracking only for auditable multi-repo, PR, CI/CD, deployment, or Azure Boards work.",
            "- Work on a task-owned branch and finish through commit, push, and PR rather than pushing to protected branches.",
            "- Classify changes as local-only or contracts-repo-first before editing shared API, schema, serialization, or mirrored contract shapes.",
        ]
        # The ladder gate only fires in managed repositories, so only describe
        # it where it actually applies.
        + ([ladder_summary()] if is_managed(root, run_git) else [])
        + waits
    )
    return emit_json(additional_context("SessionStart", context))


if __name__ == "__main__":
    raise SystemExit(main())
