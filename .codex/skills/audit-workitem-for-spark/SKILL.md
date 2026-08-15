---
name: audit-workitem-for-spark
description: Audit a software work item and decompose it into small, independent, verifiable implementation tasks that Codex Spark can complete in a fast interactive session. Use when given an Azure DevOps, Jira, GitHub, or plain-text work item and asked to size it, refine it, create child tasks, or determine which portions are appropriate for Codex Spark rather than a longer-horizon coding agent.
---

# Spark Work-Item Breakdown

Turn a work item into an implementation-ready execution plan. Optimize for Codex Spark: small, targeted code changes with a short feedback loop. Do not implement code or alter the work item unless asked.

## Inspect the work item

1. Read its title, description, acceptance criteria, comments, links, attached artifacts, and stated constraints.
2. Inspect only the relevant repository areas needed to establish the existing implementation, affected boundaries, conventions, and available test coverage. State what evidence was inspected.
3. Identify ambiguity, missing decisions, cross-repository dependencies, external coordination, and operational risk.
4. Infer the smallest demonstrable end state. Do not invent product requirements.

If essential information is missing, ask concise blocking questions before claiming the breakdown is implementation-ready. If useful, provide a provisional breakdown that clearly labels its assumptions.

## Apply the Spark test

Classify an item as Spark-suitable only when it has all of these qualities:

- A precise outcome that can be stated in one sentence.
- A narrow, bounded set of files or one well-defined module/contract boundary.
- No unresolved product or architecture decision.
- A simple verification command, test, build, or observable behavior.
- Low blast radius and a safe reviewable diff.

Split an item until each implementation task passes the test. Separate discovery, contract/schema change, implementation, tests, documentation, migration, deployment, and validation when they would otherwise create a broad or uncertain task.

Do not force-fit these into Spark tasks: ambiguous stories, multi-service architecture, broad refactors, database/data migrations, security-sensitive changes, production remediation, cross-repository coordination, or investigations without a clear hypothesis. Mark each as **Long-horizon / human decision required** and explain the boundary that must be resolved first.

## Write the task breakdown

Order tasks by dependency. Make every Spark task independently assignable and independently verifiable. Prefer vertical slices only when the slice remains narrow; otherwise stage the work through contracts, behavior, tests, and integration.

For every task, provide:

| Field | Requirement |
| --- | --- |
| ID and title | Verb-led, specific, and suitable for a child work item. |
| Goal | One sentence describing the finished outcome. |
| Scope | Expected files, components, endpoints, tables, or modules; label any uncertainty. |
| Instructions | Concrete implementation direction without prescribing unverified details. |
| Acceptance checks | Observable behavior plus the exact focused test/build/lint command where known. |
| Dependencies | Earlier task IDs, decisions, credentials, environments, or `None`. |
| Spark prompt | A self-contained prompt that tells Spark to inspect first, make only this change, run the named checks, and report files changed and remaining uncertainty. |

Keep task titles and prompts concise. Do not include unrelated cleanup. Never combine “implement it” and “test everything” in a single task; specify focused checks appropriate to the changed boundary.

## Deliverable format

Return these sections in order:

1. **Work-item assessment** — objective, affected areas, evidence inspected, and confidence level.
2. **Readiness and blockers** — resolved assumptions, open questions, and the owner needed for each decision.
3. **Spark task backlog** — a dependency-ordered table using the required fields above. Prefix ready tasks with `Spark`.
4. **Keep out of Spark** — tasks requiring a longer-horizon agent or human judgment, with the reason and prerequisite to reconsider.
5. **Execution order** — the critical path, safely parallelizable tasks, and the final integration/verification task.

Use a high bar: a useful output creates tickets that can be pasted into a tracker and prompts that can be sent to Spark unchanged.
