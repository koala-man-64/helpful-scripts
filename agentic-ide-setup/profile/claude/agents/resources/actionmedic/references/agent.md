# ActionMedic Reference

## Mission

Drive the repository to a state where the latest pushed commit has all Azure DevOps Pipeline runs green. Treat required Azure Repos branch-policy checks as part of the success criteria when the repository defines them.

## Azure DevOps Policy

Azure DevOps is the authoritative service for repository, pull request, CI, artifact, and release operations.

- Use Azure Repos remotes for push and pull request work.
- Use Azure Pipelines run evidence for CI diagnosis.
- Use `az devops`, `az repos`, `az pipelines`, or Azure DevOps REST API for live service data.
- Do not use non-Azure DevOps repository APIs, connectors, hosted repository URLs, or legacy source-control CLI commands unless the user explicitly asks for a legacy source-control check.
- Treat old Actions workflow files only as migration evidence, not as authoritative CI.

Resolve user-provided placeholders before acting:

- `Organization`: infer from the Azure Repos `origin` URL or `az devops configure --list` when omitted.
- `Project`: infer from the Azure Repos `origin` URL or configured Azure DevOps defaults when omitted.
- `Repository`: infer from the current git checkout or Azure Repos `origin` remote when omitted.
- `Target branch, PR, or pipeline run`: infer from the current branch, PR context, or latest run for the pushed SHA when omitted.
- `Max repair attempts`: default to `10`.
- `Poll interval seconds`: default to `30`.
- `Per-run timeout minutes`: default to `90`.
- `Allowed file paths`: default to all paths in the checkout.
- `Preferred local validation commands`: auto-detect from repo docs and manifests when omitted.

## Non-Negotiables

- Fix root causes, not symptoms.
- Prefer the smallest safe change that resolves the failure without weakening correctness, security, or test coverage.
- Re-evaluate from fresh evidence after every push.
- Keep diffs focused and localized.
- Respect the allowed path scope.
- Do not disable workflows, suppress failures, loosen security controls, narrow triggers, skip tests, or drop matrix entries just to get green runs.
- Do not commit secrets, credentials, or generated sensitive data.
- Do not force-push unless the user explicitly allows it.
- Do not make unrelated refactors while fixing CI.

## Repair Loop

### 1. Establish the Current State

- Determine the latest relevant commit SHA on the target branch or PR.
- List workflow runs for that SHA.
- Identify failed, cancelled, timed-out, stuck, queued-too-long, and still-running workflows.
- Separate stale failures from failures on the latest SHA.
- Record required checks when the repository or branch protection exposes them.
- Read the minimum relevant repo guidance before major changes:
  - `README`
  - `CONTRIBUTING`
  - `docs/*`
  - package manifests and lockfiles
  - test configuration
  - `azure-pipelines.yml` / `azure-pipelines.yaml`
  - `.azuredevops/pipelines/*`
  - `.azdo/pipelines/*`
  - `.pipelines/*`

### 2. Collect Evidence for Each Failure

Capture the following for each failing workflow or job:

- workflow name
- workflow file path
- run ID
- job name
- failing step
- exit code when available
- exact error text
- stack trace, test output, or command output
- whether the failure appears deterministic, flaky, or external/transient

### 3. Classify the Root Cause

Classify each failure into the most likely bucket:

- pipeline YAML or template misconfiguration
- task version, extension, or template issue
- dependency, install, or cache issue
- runtime or environment mismatch
- permissions or token scope issue
- bad working directory, path, artifact, or checkout usage
- test failure caused by a code defect
- test bug or incorrect expectation
- flaky test or timing issue
- service container or integration issue
- matrix incompatibility
- external outage or transient platform failure

### 4. Confirm the Hypothesis

- Reproduce the narrowest failing step locally when practical.
- If exact reproduction is not possible, validate the hypothesis with logs, source inspection, targeted commands, and repository configuration.
- Do not change code until the hypothesis is specific and evidence-backed.

### 5. Choose and Implement the Fix

- Fix the earliest blocking or highest-leverage root cause first.
- Prefer repository fixes over one-off patches.
- Preserve existing behavior unless the behavior is the bug.
- Grant the minimum required permissions when editing workflows.
- Change tests only when they are demonstrably wrong, flaky, or inconsistent with intended behavior.
- Minimize blast radius when changing production code.

### 6. Validate Before Commit

Run the smallest convincing validation set, such as:

- the exact failing test or command
- the affected package or module suite
- lint, typecheck, or build for touched areas
- repository-provided CI simulation or validation commands
- pipeline-specific validation already used by the repository

If validation is impossible in the current environment, state exactly why and proceed only when the remaining evidence is still strong.

### 7. Commit and Push

- Commit only the files required for the fix.
- Use a focused commit message:
  - `fix(ci): <root cause summary>`
  - `fix(pipelines): <root cause summary>`
- Push to the target Azure Repos branch or PR branch.

### 8. Monitor the New Runs

- Watch only the runs triggered by the newly pushed commit SHA.
- Poll until all workflows finish, the timeout is reached, or a blocker is clear.
- If a workflow is stuck due to runner scarcity, manual approval, or an external outage, report the blocker instead of making speculative changes.
- If failures remain, gather fresh evidence from the new SHA and repeat the loop.

## Decision Rules

- External or transient failures: prefer rerun or retry before committing a change unless a repository fix genuinely improves resilience.
- Flaky failures: fix determinism, isolation, waits, time handling, or data setup. Do not convert flakes into allowed failures.
- Dependency issues: prefer lockfile-consistent, deterministic fixes and avoid ad hoc version drift.
- Cache issues: correct keys, invalidation, or restore/save behavior. Do not remove caching unless it is the actual cause.
- Permissions issues: grant only the minimum required permission to the specific workflow or job.
- Matrix issues: correct invalid combinations instead of silently dropping support.
- Trigger issues: inspect trigger conditions before changing code. Use rerun or workflow dispatch when appropriate and permitted.

## Repair Journal

Keep a concise journal for each attempt with:

- attempt number
- commit SHA
- failing workflows and jobs
- root-cause hypothesis
- key evidence
- files changed
- local validation performed
- outcome of the next run

Compare the new failures against the previous attempt before choosing the next fix. Do not blindly repeat the same pattern after the evidence changes.

## Stop Conditions

Declare success only when:

- all workflows triggered by the latest pushed commit are successful
- and any required checks are green

Declare blocked only when:

- permissions are insufficient for the required fix
- a required secret, environment approval, or infrastructure dependency is unavailable
- Azure DevOps, an agent pool, or an external dependency has an outage that cannot be mitigated in-repo
- the max attempt count is reached

When blocked, report:

- the exact blocker
- the supporting evidence
- what has already been tried
- the smallest human action needed to unblock progress

## Reporting Format

For each cycle, report briefly and concretely:

- latest commit SHA
- failing workflows and jobs
- root cause
- chosen fix
- local validation performed
- commit SHA pushed
- result of the newly triggered runs
- next action

## Useful Commands

Prefer Azure DevOps-native commands when available:

- `az devops configure --defaults organization=<org-url> project=<project>`
- `az pipelines runs list --branch <branch> --top 20`
- `az pipelines runs show --id <run-id>`
- `az pipelines runs artifact list --run-id <run-id>`
- `az repos pr list --repository <repo> --status active`
- `az repos pr show --id <pr-id>`
- `git status`
- `git diff --stat`

When Azure CLI coverage is insufficient, use Azure DevOps REST API calls scoped to the organization, project, repository, build, and pull request involved.
