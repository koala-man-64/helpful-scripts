---
name: candidate-git-hygiene
description: Audit Git branches, refs, worktrees, conflicts, or explicit cleanup requests conservatively. A plain finish-it request uses the current task delivery workflow and does not trigger a repository-wide hygiene audit.
---

# Candidate Git hygiene

Use this disabled candidate for an explicit Git hygiene, stale-branch, worktree,
merge-conflict, or cleanup request. Audit before mutation, preserve uncommitted work,
and require explicit approval before deleting branches, worktrees, or remote refs.

For `finish it`, inspect the current task scope, validate changed files, stage only
task-owned work, and use the repository delivery path. Do not expand that phrase into
repository-wide pruning or cleanup. Never bypass protected review, force-push, delete
unrelated work, or merge failing checks.
