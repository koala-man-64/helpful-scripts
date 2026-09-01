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
)


def main() -> int:
    root = repo_root()
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
    )
    return emit_json(additional_context("SessionStart", context))


if __name__ == "__main__":
    raise SystemExit(main())
