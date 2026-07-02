---
name: actionmedic
description: Autonomous Azure DevOps Pipelines troubleshooting and repair for repositories with failing CI. Use when Codex needs to audit Azure Pipeline runs on a branch or pull request, diagnose failed, cancelled, timed-out, queued, or stuck jobs, implement the smallest justified fix, validate locally, commit and push to Azure Repos, and monitor until the latest commit is green or a hard blocker is proven.
---

# ActionMedic

## Overview

Drive a repository to a state where the latest pushed commit has all Azure DevOps Pipeline runs green. Work from fresh run evidence each cycle, fix root causes instead of symptoms, and keep changes minimal, local, and defensible.

## Azure DevOps Policy

- Treat Azure DevOps as the authoritative repository, pull request, CI, artifact, and release system.
- Use Azure Repos remotes and Azure Pipelines run evidence. Do not use non-Azure DevOps repository APIs, connectors, hosted repository URLs, or legacy source-control CLI commands unless the user explicitly asks for a legacy source-control check.
- Prefer the Azure CLI DevOps extension (`az devops`, `az repos`, `az pipelines`) or Azure DevOps REST API when live run data is needed.
- Treat `azure-pipelines.yml`, `azure-pipelines.yaml`, `.azuredevops/pipelines/*`, `.azdo/pipelines/*`, and `.pipelines/*` as the primary CI configuration surfaces.
- Treat old Actions workflow files only as migration evidence, not as authoritative CI.

## Workflow

- Read `references/agent.md` before acting.
- Infer the repository and target ref from the current checkout unless the user overrides them.
- Respect any user-provided limits such as max attempts, allowed paths, validation commands, or timeout.
- Maintain a concise repair journal for each attempt so new evidence is compared against the prior run.
- Audit only runs for the latest relevant SHA; separate stale failures from current failures.
- Fix one logical root cause per commit unless multiple failures clearly share the same cause.
- Validate the narrowest convincing set of local checks before committing.
- Commit with `fix(ci): ...` or `fix(pipelines): ...`, push to Azure Repos, then monitor only Azure Pipeline runs from the new SHA.
- Stop only when the latest commit is fully green or a documented blocker remains.

## Resources

- `references/agent.md` - Canonical repair loop, guardrails, evidence checklist, and reporting format.
