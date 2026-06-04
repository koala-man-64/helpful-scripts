from hook_utils import (
    additional_context,
    azure_devops_agent_authority_lines,
    branch_header,
    current_branch,
    dirty_summary,
    emit_json,
    origin_url,
    repo_name,
    repo_root,
    skill_status,
)


def main() -> int:
    root = repo_root()
    present, missing = skill_status(root)
    missing_text = ", ".join(missing) if missing else "none"
    present_text = ", ".join(present) if present else "none"
    header = branch_header(root) or current_branch(root)

    context = "\n".join(
        [
            "Codex team workflow context:",
            f"- Repo: {repo_name(root)}",
            f"- Root: {root}",
            f"- Origin: {origin_url(root)}",
            f"- Branch: {header}",
            f"- Working tree: {dirty_summary(root)}",
            f"- Core repo-local agents present: {present_text}",
            f"- Core repo-local agents missing: {missing_text}",
            "- Follow AGENTS.md and prefer repo-local .codex/skills instructions.",
            "- Start substantive work through delivery-orchestrator-agent.",
            "- Establish gateway-bookkeeper tracking for auditable, multi-repo, PR, pipeline, or Azure DevOps work.",
            "- Apply strict-branch-and-merge-discipline before edits, branches, commits, pushes, or PRs.",
            "- Classify changes as local-only or contracts-repo-first before editing shared API, schema, serialization, or mirrored contract shapes.",
            "- Avoid mixing new work into unrelated active branches; use an isolated branch or worktree when history is already scoped to another task.",
            *azure_devops_agent_authority_lines(),
        ]
    )
    return emit_json(additional_context("SessionStart", context))


if __name__ == "__main__":
    raise SystemExit(main())
