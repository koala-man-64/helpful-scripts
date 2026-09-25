---
name: workflow-router
description: Route Codex work directly by task shape and risk to the smallest suitable model and effort; use for delegating implementation, investigation, review, or high-risk production work.
---

# Workflow router

Choose the route from the task itself, not from a fixed escalation ladder.

- Mechanical inventory, formatting, or bounded transformations: GPT-6 Luna at low effort.
- Investigation, debugging, test execution, and code review: GPT-6 Sol at medium effort.
- Architecture, security, incidents, migrations, data integrity, or production risk:
  GPT-6 Astra at high effort as the owner lane.

A child is never Astra or Ultra and must be strictly lower than its parent in both orders:
`Astra > Sol > Luna` and `ultra > high > medium > low`. A Luna/low parent cannot
delegate. Do not require a cumulative lower-tier blocker ladder: route directly to
the smallest permitted model that matches the work. Central hooks remain authoritative
for mutations and evidence.

`delivery-orchestrator-agent` is currently an unresolved fork. In critical work it
is a coordination **role**, not a runnable canonical skill pin, until an owner
resolves that fork. Keep available skill pins separate from the people or child
routes selected for a task.

For the catalog scenarios, record the parent route and every child route explicitly:

- Narrow text-only coding fix: Luna/low owner; no children.
- Routine reversible Standard change: Sol/medium owner and relevant tests; no automatic QA child. Add independent review for significant security, data integrity, interface, concurrency, or production risk, or when project/managed policy requires it. Use a permitted Luna/low reviewer for focused scope; route higher-risk work to the Critical lane.
- Cross-repo contract change and CI incident: Astra/high owner; Sol/medium
  investigation or QA children only.
- Production or IaC change: Astra/high owner; Sol/medium security, QA, or
  deployment-evidence children only.

These examples are routing constraints, not an installation or permission grant.

Read [references/evidence.md](references/evidence.md) only when a task depends on
delivery or runtime proof.
