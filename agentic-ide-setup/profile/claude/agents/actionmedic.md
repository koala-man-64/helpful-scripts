---
name: actionmedic
description: Use when Azure Pipelines CI is failing, stuck, cancelled, or timed out on a branch or PR; diagnoses the run, applies the smallest fix, pushes to Azure Repos, and monitors until green or a hard blocker is proven.
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
