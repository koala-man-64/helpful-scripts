# Examples

Use these as response-shape examples, not as fixed outputs.

## Audit This Repo

User:

```text
Audit this repo and tell me what can be cleaned up.
```

Expected behavior:

- Default to `audit`.
- Inspect local Git and worktrees.
- Discover Azure DevOps org/project/repo from `origin` if present.
- Query Azure DevOps only if credentials and scope are available.
- Return a concise maintenance report with safe candidates, manual-review items, and a dry-run cleanup plan.
- Do not execute cleanup.

Lead with:

```text
Recommendation: run only low-risk metadata cleanup now; branch/worktree deletions need review because <reason>.
```

## Find Stale Worktrees

User:

```text
Find stale worktrees and branches.
```

Expected behavior:

- Run worktree and branch inventory commands.
- Identify stale worktrees by age threshold, branch commit age, and filesystem evidence when available.
- Mark dirty, untracked, detached, or unpushed worktrees as manual review.
- Propose `git worktree prune --dry-run` and clean worktree removals only as a dry-run plan unless the user requested cleanup mode.

## Upstream Gone

User:

```text
Show me local branches whose upstream is gone.
```

Expected behavior:

- Run `git branch --color=never -vv`.
- Identify branches with gone upstreams.
- Check whether each branch is merged to the default branch.
- Classify merged local branches as medium-risk delete candidates.
- Classify unmerged or divergent branches as manual review.

## Completed PR Source Branches

User:

```text
Find completed PRs whose source branches were not deleted.
```

Expected behavior:

- Query completed PRs in Azure DevOps.
- Verify source refs still exist.
- Exclude protected branches.
- Report high-risk remote branch delete candidates with exact `az repos ref delete` or `git push origin --delete` plans.
- Require approval before any deletion.

## Pipeline YAML Drift

User:

```text
Find pipelines that point to missing YAML files.
```

Expected behavior:

- List Azure DevOps pipeline definitions.
- Compare each definition's YAML path to files in the default branch.
- Report definitions pointing to missing paths.
- Recommend updating YAML path, disabling obsolete pipeline, or migrating definition.
- Do not disable or modify pipelines without approval.

## CI Health Check

User:

```text
Generate a JSON report for CI.
```

Expected behavior:

- Use `ci-health-check` unless the user gave a different mode.
- Run non-destructive local and Azure DevOps checks available to the environment.
- Emit JSON matching `references/json-schema.md`.
- Avoid interactive approval prompts.
- Exit with a concise human summary plus the JSON artifact or content requested.
