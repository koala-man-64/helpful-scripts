# Checklists

Use the smallest checklist that covers the user's requested scope. Do not run Azure DevOps checks when the user requested local-only audit unless local evidence points to PR or pipeline drift that must be explained.

## Discovery Checklist

- [ ] Confirm mode and output format.
- [ ] Resolve repo path and Git root.
- [ ] Capture current branch and HEAD SHA.
- [ ] Capture default branch and origin remote.
- [ ] Capture protected branch patterns and thresholds.
- [ ] Inventory worktrees.
- [ ] Discover Azure DevOps org, project, repo name or ID when in scope.
- [ ] Confirm auth state without exposing secrets.
- [ ] Capture command timestamps and redacted outputs.

## Git Worktree Checklist

- [ ] Run `git worktree list --porcelain`.
- [ ] For each worktree, run `git status --porcelain=v1 -b`.
- [ ] Detect dirty tracked files.
- [ ] Detect untracked files.
- [ ] Detect detached HEADs.
- [ ] Detect missing or deleted upstream branches.
- [ ] Detect unpushed commits.
- [ ] Detect branches already merged to the target.
- [ ] Detect duplicate worktrees for the same branch.
- [ ] Detect paths outside approved directories.
- [ ] Mark safe-removal candidates only when clean, merged, unpushed-free, and not protected.

## Git Graph Checklist

- [ ] Run `git branch --color=never -vv`.
- [ ] Run `git for-each-ref` for local and remote refs.
- [ ] Run `git branch --merged` and `git branch --no-merged`.
- [ ] Compare branch tips to the default branch with `git rev-list --left-right --count`.
- [ ] Detect deleted upstreams and stale refs.
- [ ] Detect divergent branches.
- [ ] Detect branches violating naming conventions.
- [ ] Detect duplicate branch names across remotes.
- [ ] Detect tags that appear obsolete and report manual review only.

## Azure DevOps PR Checklist

- [ ] Query active, completed, and abandoned PRs.
- [ ] Detect stale active and stale draft PRs.
- [ ] Detect completed or abandoned PRs with source branches still present.
- [ ] Detect merge conflicts and policy blocks.
- [ ] Detect inactive reviewers.
- [ ] Detect deprecated target branches.
- [ ] Detect missing source branches.
- [ ] Detect source branches far behind target.
- [ ] Detect failing validation builds.
- [ ] Detect duplicate PRs from the same source branch.
- [ ] Detect missing linked work items when required by project conventions.

## Azure DevOps Pipeline Checklist

- [ ] List pipeline definitions and recent runs.
- [ ] Verify YAML paths exist in the repo.
- [ ] Verify default branch alignment with repo default branch.
- [ ] Detect duplicate pipeline definitions for the same YAML path.
- [ ] Detect disabled or inactive pipelines.
- [ ] Detect repeated failures.
- [ ] Detect stale schedules, branch filters, and path filters.
- [ ] Compare branch policy build validations to current pipelines.
- [ ] Detect pipeline resources that reference renamed or deleted pipelines.
- [ ] Verify variable groups referenced by YAML exist.
- [ ] Verify service connections referenced by YAML exist and are authorized when possible.
- [ ] Verify environments referenced by deployment jobs exist.
- [ ] Verify templates and repository resources resolve.
- [ ] Flag classic pipeline orphans when classic definitions are in scope.

## Cleanup Plan Checklist

- [ ] Group actions by risk and resource type.
- [ ] Include exact command or API call.
- [ ] Explain what each action does.
- [ ] Explain why it is safe or unsafe.
- [ ] Include expected impact.
- [ ] Include verification steps.
- [ ] Include rollback guidance.
- [ ] Mark required approvals.
- [ ] Avoid noisy recommendations with low confidence.

## Verification Checklist

- [ ] Re-run worktree inventory.
- [ ] Re-run branch/ref inventory.
- [ ] Confirm deleted branches match approved list.
- [ ] Confirm no protected branches were modified.
- [ ] Confirm no tags were deleted.
- [ ] Confirm PR states match approved changes.
- [ ] Confirm pipeline definitions resolve to existing YAML.
- [ ] Confirm branch policies point to valid pipelines.
- [ ] Confirm recent run visibility where relevant.
- [ ] Capture final evidence and report remaining manual-review items.
