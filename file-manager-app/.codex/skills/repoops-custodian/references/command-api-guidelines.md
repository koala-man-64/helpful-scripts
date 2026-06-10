# Command and API Guidelines

Use these patterns to gather evidence and build cleanup plans. Prefer read-only commands first and exact command plans before execution.

## Local Git Discovery

Run from the repository root unless a worktree-specific path is being inspected.

```powershell
git rev-parse --show-toplevel
git status --porcelain=v1 -b
git remote -v
git symbolic-ref refs/remotes/origin/HEAD
git worktree list --porcelain
git branch --color=never -vv
git for-each-ref --format="%(refname)|%(objectname)|%(committerdate:iso8601)|%(upstream:short)|%(upstream:track)" refs/heads refs/remotes
git branch --color=never --merged
git branch --color=never --no-merged
git show-ref --tags
```

Use networked dry-runs only when remote state is in scope:

```powershell
git fetch --all --prune --dry-run
git fetch --prune --dry-run
git worktree prune --dry-run
```

Use these checks before proposing branch deletion:

```powershell
git rev-list --left-right --count <target>...<branch>
git merge-base --is-ancestor <branch> <target>
git log --decorate --oneline <target>..<branch>
```

Use these checks before proposing worktree removal:

```powershell
git -C <worktree-path> status --porcelain=v1 -b
git -C <worktree-path> rev-parse --abbrev-ref HEAD
git -C <worktree-path> rev-list --left-right --count @{upstream}...HEAD
```

If a branch has no upstream, compare it to the default branch and mark deletion as manual review unless it is clearly merged.

## Git Cleanup Command Plans

Low risk examples:

```powershell
git remote prune origin --dry-run
git remote prune origin
git worktree prune --dry-run
git worktree prune
```

Medium risk examples:

```powershell
git branch -d <local-branch>
git worktree remove <clean-worktree-path>
```

High risk examples:

```powershell
git push origin --delete <remote-branch>
az repos ref delete --name refs/heads/<branch> --object-id <sha> --repository <repo>
```

Critical operations are not normal cleanup. Do not propose them unless the user explicitly asks for the exact operation:

```powershell
git push --force-with-lease
git tag -d <tag>
git push origin --delete <tag>
```

## Azure DevOps Discovery

Prefer Azure DevOps CLI when available:

```powershell
az devops configure --list
az repos show --repository <repo> --org <org> --project <project> -o json
az repos pr list --repository <repo> --status active --org <org> --project <project> -o json
az repos pr list --repository <repo> --status abandoned --org <org> --project <project> -o json
az repos pr list --repository <repo> --status completed --org <org> --project <project> -o json
az repos policy list --repository-id <repo-id> --branch <branch> --org <org> --project <project> -o json
az pipelines list --org <org> --project <project> -o json
az pipelines show --id <pipeline-id> --org <org> --project <project> -o json
az pipelines runs list --pipeline-ids <pipeline-id> --top 10 --org <org> --project <project> -o json
az pipelines variable-group list --org <org> --project <project> -o json
az pipelines runs show --id <run-id> --org <org> --project <project> -o json
```

The Azure DevOps CLI does not cover every resource. Use REST API only through an approved local auth context, never by asking the user to paste a token into chat.

Common REST patterns:

```text
GET https://dev.azure.com/{organization}/{project}/_apis/git/repositories/{repositoryId}/pullrequests?searchCriteria.status=active&api-version=7.1
GET https://dev.azure.com/{organization}/{project}/_apis/pipelines?api-version=7.1
GET https://dev.azure.com/{organization}/{project}/_apis/build/definitions?repositoryId={repositoryId}&api-version=7.1
GET https://dev.azure.com/{organization}/{project}/_apis/policy/configurations?repositoryId={repositoryId}&api-version=7.1
GET https://dev.azure.com/{organization}/{project}/_apis/distributedtask/variablegroups?api-version=7.1
GET https://dev.azure.com/{organization}/{project}/_apis/serviceendpoint/endpoints?api-version=7.1-preview.4
GET https://dev.azure.com/{organization}/{project}/_apis/distributedtask/environments?api-version=7.1-preview.1
```

For proposed write calls, show method, endpoint, payload summary, expected effect, risk, rollback, and verification. Do not include authorization headers in reports.

## Pipeline Drift Checks

Compare these surfaces:

- Azure DevOps pipeline definition metadata.
- YAML file path and content in the repository.
- Repository default branch.
- Pipeline default branch.
- Branch policy build validation configuration.
- Trigger, PR trigger, schedule, branch filter, and path filter configuration.
- Variable groups referenced by YAML.
- Service connections referenced by YAML.
- Environments referenced by deployment jobs.
- Templates and repository resources referenced by YAML.
- Pipeline permissions and authorization state.
- Classic definitions tied to deleted repos, branches, or obsolete artifacts.

Useful local YAML searches:

```powershell
rg --files -g "azure-pipelines*.yml" -g "azure-pipelines*.yaml" -g ".azuredevops/**" -g ".azdo/**" -g ".pipelines/**"
rg -n "^\s*(trigger|pr|schedules|resources|extends|stages|jobs|deployment|environment|variables|template|checkout):" azure-pipelines*.yml azure-pipelines*.yaml .azuredevops .azdo .pipelines
rg -n "(group:|variableGroups|serviceConnection|azureSubscription|connectedServiceName|environment:|repository:|ref:|template:)" azure-pipelines*.yml azure-pipelines*.yaml .azuredevops .azdo .pipelines
```

If YAML parsing is needed, use a real YAML parser from the local toolchain instead of ad hoc string parsing.

## Secret Handling

Do not print or store tokens, PATs, cookies, authorization headers, service connection credentials, connection strings, private keys, or client secrets. Redact likely values in outputs and reports:

```text
Authorization: REDACTED
System.AccessToken: REDACTED
AZURE_DEVOPS_EXT_PAT: REDACTED
client_secret: REDACTED
```
