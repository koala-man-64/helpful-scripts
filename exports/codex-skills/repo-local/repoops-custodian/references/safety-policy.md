# Safety Policy

RepoOps Custodian is audit-first and dry-run by default. Cleanup is allowed only after explicit approval of the exact action group.

## Risk Levels

- `info`: context, inventory, or informational drift with no cleanup action.
- `low`: reversible metadata cleanup, such as pruning stale remote-tracking refs.
- `medium`: local-only cleanup that can usually be recovered, such as deleting merged local branches or removing clean worktrees.
- `high`: remote or shared resource changes, such as deleting remote branches, abandoning PRs, or disabling obsolete pipelines.
- `critical`: history rewrite, force-push, protected branch modification, tag deletion, service connection mutation, or any action with poor rollback.

## Approval Requirements

Always require explicit approval before:

- Deleting worktrees.
- Deleting local branches with unmerged commits.
- Deleting remote branches.
- Abandoning PRs.
- Deleting PR source branches.
- Disabling pipelines.
- Deleting pipelines.
- Modifying branch policies.
- Modifying service connections.
- Modifying variable groups.
- Rewriting Git history.
- Force-pushing.
- Deleting tags.

The approval request must include:

- Action group name.
- Resources affected.
- Risk level.
- Exact commands or API calls.
- Expected impact.
- Why the action is considered safe or unsafe.
- Rollback or recovery guidance.
- Verification steps.

## Protected Resources

Treat these branch patterns as protected unless the user overrides them with an explicit safer or stricter list:

```text
main
master
develop
release/*
hotfix/*
```

Never delete, rewrite, force-update, or retarget protected branches as routine cleanup.

Tags are evidence. Never delete tags automatically. Report obsolete-looking tags as manual review only.

## Worktree Guardrails

Never delete a worktree when any of these are true unless the user explicitly approves that exact worktree and risk:

- `git status --porcelain=v1 -b` reports dirty tracked files.
- Untracked files exist.
- The branch has unpushed commits.
- The branch has no upstream and is not confirmed merged.
- The worktree is detached and the useful branch or commit is unclear.
- The worktree path is outside approved directories and ownership is unclear.
- Git metadata is broken or commands return inconsistent results.

Prefer preserving changes over cleanup. Stash, commit, or archive only after explicit approval.

## Branch Guardrails

Local branch deletion candidates must be classified:

- Safe local delete: local branch is merged to the target, not protected, not current, not used by a worktree, and has no unpushed commits.
- Manual review: branch is stale, has no upstream, is divergent, has no associated PR, or has unclear ownership.
- Do not delete: protected branch, current branch, branch with unmerged commits, branch used by a dirty worktree, or branch tied to active work.

Remote branch deletion is always high risk and requires explicit approval. Confirm PR status, branch policy, branch protection, and last commit before proposing it.

## Azure DevOps Guardrails

Do not abandon PRs, delete source branches, modify reviewers, update policies, disable pipelines, delete pipelines, or change variable groups or service connections without approval.

For pipeline and policy repairs, prefer migration checklists and proposed diffs over direct changes. If a change is approved, capture the previous configuration before updating so rollback is possible.

## Rollback Standards

For every action, state whether rollback is reliable.

Examples:

- Local branch delete: recoverable from reflog or known SHA if garbage collection has not removed the commit.
- Remote branch delete: recoverable by recreating the branch from a known SHA if the SHA is retained.
- Worktree removal: recoverable by recreating the worktree from the branch only if no local-only files were removed.
- Pipeline disable/delete: disabling is reversible; deletion may require recreating definition metadata and permissions.
- Branch policy change: reversible if previous policy JSON was captured.
- Service connection change: high risk; rollback depends on previous endpoint configuration and permissions.

When rollback is uncertain, mark the action `high` or `critical` and require manual review.
