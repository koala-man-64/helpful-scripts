# Report Templates

Use these templates for Markdown and JSON reports. Keep reports concise by default, then include raw evidence in an appendix when auditability matters.

## Markdown Maintenance Report

````markdown
# RepoOps Maintenance Report

## Executive Summary
- Repo: <repo path/name>
- Azure DevOps: <org/project/repo or unavailable>
- Generated: <timestamp>
- Mode: <audit | dry-run | interactive-cleanup | report-only | ci-health-check>
- Overall recommendation: <answer first>
- Findings by risk: info=<n>, low=<n>, medium=<n>, high=<n>, critical=<n>
- Cleanup posture: <safe cleanup available | manual review required | blocked>

## Repository Context
- Git root:
- Current branch:
- Default branch:
- Origin:
- Worktrees inspected:
- Protected branch patterns:
- Thresholds:
- Auth state:

## Git/Worktree Findings
| ID | Risk | Resource | Current State | Evidence | Recommendation | Manual Review |
| --- | --- | --- | --- | --- | --- | --- |

## Git Graph Findings
| ID | Risk | Resource | Current State | Evidence | Recommendation | Manual Review |
| --- | --- | --- | --- | --- | --- | --- |

## Azure DevOps PR Findings
| ID | Risk | Resource | Current State | Evidence | Recommendation | Manual Review |
| --- | --- | --- | --- | --- | --- | --- |

## Azure DevOps Pipeline Findings
| ID | Risk | Resource | Current State | Evidence | Recommendation | Manual Review |
| --- | --- | --- | --- | --- | --- | --- |

## Out-of-Sync Pipeline Findings
| ID | Risk | Mismatch | Evidence | Recommendation |
| --- | --- | --- | --- | --- |

## Safe Cleanup Candidates
- <low-risk or reversible action group>

## Manual Review Required
- <finding IDs and reason>

## High-Risk Items
- <finding IDs and reason>

## Proposed Command Plan
| Group | Risk | Command | What It Does | Why Safe/Unsafe | Verify | Rollback |
| --- | --- | --- | --- | --- | --- | --- |

## Proposed Azure DevOps API/CLI Plan
| Group | Risk | Command/API | Payload Summary | Expected Effect | Verify | Rollback |
| --- | --- | --- | --- | --- | --- | --- |

## Rollback Guidance
- <resource-specific rollback notes>

## Verification Checklist
- [ ] Worktree list rechecked.
- [ ] No dirty worktrees removed.
- [ ] Branches deleted only as approved.
- [ ] Remotes pruned only as approved.
- [ ] PR states match expected state.
- [ ] Pipeline definitions still resolve to existing YAML.
- [ ] Branch policies point to valid pipelines.
- [ ] Recent pipeline run status is visible when relevant.
- [ ] Protected branches were not modified.

## Appendix: Raw Evidence
<redacted command outputs, API response excerpts, and timestamps>
````

## JSON Report Skeleton

```json
{
  "summary": {
    "repo": "",
    "project": "",
    "organization": "",
    "generatedAt": "",
    "mode": "",
    "findingCountsByRisk": {},
    "recommendedActionCountsByRisk": {}
  },
  "findings": [
    {
      "id": "",
      "category": "",
      "resourceType": "",
      "resourceName": "",
      "risk": "",
      "currentState": "",
      "evidence": [],
      "recommendation": "",
      "proposedActions": [
        {
          "type": "",
          "description": "",
          "command": "",
          "apiCall": "",
          "requiresApproval": true,
          "reversible": true,
          "rollback": ""
        }
      ],
      "manualReviewRequired": true
    }
  ]
}
```

## Approval Prompt Template

````markdown
Approval required for: <action group>

Risk: <medium | high | critical>
Resources affected:
- <resource>

Exact commands/API calls:
```powershell
<command>
```

Expected impact:
- <impact>

Rollback:
- <rollback guidance>

Verification:
- <verification command/check>

Reply with explicit approval for this action group only, for example:
`Approve <action group>`.
````
