---
name: strict-branch-and-merge-discipline
description: Git safety for code-changing work. Preflight before the first edit, one worktree per concurrent task, a coordination record for multi-repo changes, and the merge gate before a finish-it merge.
---

# Strict Branch and Merge Discipline

## Preflight, before the first edit

1. Confirm the repo root, current branch, worktree and `origin`, and resolve the base from `origin/HEAD`.
2. Run `git fetch --all --prune` and record the base SHA you start from.
3. If the tree has uncommitted changes that aren't this task's, stop and say so.
4. Branch from the latest remote base as `claude/<topic>`, with the work item id when there is one (`claude/AB1234-topic`). Never reuse another task's or another agent's branch.
5. If another task is already active in this repo, add a worktree (`git worktree add`) instead of switching branches in place.

## While working

- Keep commits small and task-scoped, with the work item id when there is one.
- Sync with `git fetch --all --prune` plus an explicit `git rebase origin/<base>`. Never use plain `git pull`.
- Use `--force-with-lease` only on your own unmerged branch after a rebase. Never use `--force`.
- Rerun the relevant tests after the final sync, before pushing.
- If a conflict would need guesses about code you don't own, stop and report it.

## Multi-repo changes

- Use one branch per repo under the same id, and keep a coordination record: `repo -> branch -> head SHA`, plus the PR links.
- A downstream repo isn't done while it depends on an unmerged upstream change.

## Merge gate ("finish it")

Merge only when all of these hold:

1. The branch belongs to this task and is synced with the latest base.
2. Relevant tests and checks passed after the final sync, and no conflicts remain.
3. The PR targets the right base and links any related PRs.
4. Branch protection, required reviews and checks, and any merge queue are satisfied. Squash merge unless repo policy says otherwise.

Never commit or push to a protected branch (`main`, `master`, `trunk`, `develop`, `staging`, `production`, `release/*`, `hotfix/*`). Never rewrite another agent's branch, merge a stale branch, or bundle unrelated changes.
