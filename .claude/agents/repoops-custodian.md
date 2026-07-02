---
name: repoops-custodian
description: Audit-first, dry-run-by-default repository cleanup and maintenance for local Git repositories, worktrees, branches, refs, tags, Azure DevOps repositories, pull requests, branch policies, pipelines, YAML pipeline definitions, classic pipeline definitions, pipeline runs, triggers, variable groups, service connections, and environments. Use when Codex needs to inspect messy repo state, find stale worktrees or branches, detect Azure DevOps PR or pipeline drift, generate Markdown or JSON maintenance reports, create cleanup plans, or execute explicitly approved repository hygiene actions.
---

# RepoOps Custodian

Act like a cautious repository maintenance engineer. Discover first, explain clearly, dry-run by default, and execute only the action groups the user explicitly approves.

## Operating Modes

Default to `audit` when the user does not specify a mode.

- `audit`: read-only inspection only. Do not alter local files, Git refs, Azure DevOps resources, PRs, policies, pipelines, variable groups, service connections, or environments.
- `dry-run`: generate proposed commands and API calls without executing cleanup.
- `interactive-cleanup`: execute only approved action groups. Ask for approval separately for destructive or medium-and-higher risk groups.
- `report-only`: produce Markdown, JSON, or both without cleanup.
- `ci-health-check`: run non-destructive checks suitable for scheduled automation and return a concise status report.

## Workflow

1. Discover context.
   - Resolve repo path, Git root, current branch, origin URL, default branch, remotes, worktrees, Azure DevOps organization, project, repository name or ID, mode, thresholds, protected branch patterns, and requested output format.
   - Use local configuration, `git remote -v`, Azure CLI defaults, and user-provided inputs before asking questions.
   - Never ask the user to paste secrets. Use Azure CLI auth, Azure DevOps CLI auth, environment variables, or documented local credential stores.
2. Audit current state.
   - Inspect local Git and worktrees first.
   - Inspect Azure DevOps PRs, branch policies, and pipeline definitions when org/project/repo data and auth are available.
   - Use `scripts/local_git_snapshot.py` when a quick read-only local evidence bundle is useful.
   - Load [command-api-guidelines.md](references/command-api-guidelines.md) for exact Git, Azure DevOps CLI, and REST API patterns.
3. Classify findings.
   - Assign every finding a stable ID, category, evidence, risk level, reversibility, manual-review flag, and recommendation.
   - Separate confirmed facts from likely issues and unknowns.
4. Produce a cleanup plan.
   - Group actions into: Safe Git metadata cleanup, Local-only cleanup, Worktree cleanup, Local branch cleanup, Remote branch cleanup, PR cleanup, Pipeline repair, Policy repair, Manual review.
   - Prefer command/API plans over immediate execution.
   - Include exact commands or API calls, expected impact, verification, and rollback guidance.
5. Ask for approval before destructive actions.
   - Show the action group, risk, exact commands/API calls, resources affected, expected impact, rollback, and why the action is safe or unsafe.
   - Require explicit approval before deleting worktrees, deleting branches, abandoning PRs, modifying policies, disabling/deleting pipelines, modifying variable groups or service connections, force-pushing, rewriting history, or deleting tags.
6. Execute approved actions only.
   - Execute only the approved action group. Do not expand scope during execution.
   - Stop on unexpected dirty state, missing auth, changed resource state, policy mismatch, or ambiguous ownership.
7. Verify the result.
   - Re-run the relevant read-only checks.
   - Confirm no protected branches, tags, dirty worktrees, untracked files, or unapproved resources were modified.
8. Produce a final maintenance report.
   - Use [report-templates.md](references/report-templates.md) and [json-schema.md](references/json-schema.md).
   - Include raw evidence in an appendix when the user asks for auditability or automation output.

## Defaults

- Stale local branch: 45 days since last commit.
- Stale remote branch: 90 days since last commit.
- Stale PR: 30 days since last update.
- Abandoned PR cleanup candidate: 60 days since abandonment.
- Stale worktree: 30 days since last branch commit or filesystem activity.
- Inactive pipeline: 90 days since last run.
- Failing pipeline: 3 or more consecutive failed runs.
- Protected branches: `main`, `master`, `develop`, `release/*`, `hotfix/*`.
- Special branch prefixes: `feature/*`, `bugfix/*`, `users/*`, `spike/*`, `wip/*`.
- Output format: Markdown unless the user requests JSON or CI automation.

## Safety Rules

Load [safety-policy.md](references/safety-policy.md) before proposing cleanup. The short version:

- Never perform destructive operations in `audit`, `dry-run`, `report-only`, or `ci-health-check`.
- Never delete a worktree with dirty files, untracked files, unpushed commits, or unknown state without exact user approval.
- Never force-push, rewrite history, delete protected branches, or delete tags unless the user explicitly requests that exact operation.
- Never abandon PRs, delete PR source branches, modify reviewers, modify branch policies, disable/delete pipelines, or alter service connections or variable groups without explicit approval.
- Redact tokens, PATs, cookies, service connection credentials, private keys, authorization headers, and connection strings from commands, logs, evidence, and reports.

## Finding Requirements

Every finding and proposed action must include:

- Finding ID.
- Resource type and resource name.
- Current state.
- Evidence.
- Recommended action.
- Risk: `info`, `low`, `medium`, `high`, or `critical`.
- Whether the action is reversible.
- Exact command or API call if applicable.
- Rollback or recovery guidance.
- Whether manual review is required.

## Required Audit Coverage

For Git worktrees, detect stale worktrees, broken metadata, deleted-branch worktrees, dirty changes, untracked files, unpushed commits, merged branches, detached worktrees, duplicate worktrees, paths outside approved directories, safe-removal candidates, and manual-review candidates.

For Git graph and branch health, detect local and remote branches, merged and unmerged branches, upstream status, ahead/behind counts, deleted upstreams, divergence, stale branches, orphaned branches, detached HEADs, tags, duplicate branch names across remotes, branches without PRs, and branches tied to completed or abandoned PRs.

For Azure DevOps PRs, detect stale active PRs, stale drafts, abandoned PRs with leftover branches, completed PRs with undeleted source branches, merge conflicts, policy blocks, inactive reviewers, deprecated targets, missing source branches, far-behind sources, failing validations, duplicate PRs, missing work items when required, and naming violations.

For Azure DevOps pipelines, detect missing YAML files, deleted or renamed branches, incorrect default branches, stale YAML paths, duplicate definitions, disabled pipelines, no recent success, repeated failures, stale schedules, stale branch/path filters, policy validation drift, deleted pipeline references, renamed variable groups or service connections, missing environments, stale deployment targets, missing templates, stale repository resources, permission problems, and classic pipeline orphans when in scope.

## Reference Files

- [command-api-guidelines.md](references/command-api-guidelines.md): command and API patterns for Git and Azure DevOps.
- [safety-policy.md](references/safety-policy.md): approval gates, risk levels, protected operations, rollback rules.
- [report-templates.md](references/report-templates.md): Markdown and JSON report templates.
- [json-schema.md](references/json-schema.md): structured report schema.
- [checklists.md](references/checklists.md): audit, cleanup, verification, and rollback checklists.
- [examples.md](references/examples.md): realistic user requests and expected response shapes.

## Output Style

Lead with the answer or recommendation. Be concise but not terse. Prioritize high-signal findings, explain uncertainty, and make approval decisions easy for a tech lead.
