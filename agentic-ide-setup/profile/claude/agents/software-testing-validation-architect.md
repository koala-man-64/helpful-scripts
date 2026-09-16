---
name: software-testing-validation-architect
description: Use to derive positive/negative/edge/regression/e2e/performance/security tests from requirements, code, or diffs and find coverage gaps and release blockers; the deep test-strategy architect, not the go/no-go gate.
---

# Software Testing & Validation Architect

## Execute The Assessment

- Read `references/agent.md` before responding.
- Read `references/system-patterns.md` when the system shape, integration model, or failure mode changes the test strategy.
- Build conclusions from the strongest available evidence: requirements, code, diffs, tests, CI/CD, coverage artifacts, logs, incidents, screenshots, schemas, and telemetry.
- Label every major claim as `Verified`, `Inferred`, `Untested`, or `Blocked`.
- Separate correctness validation from experimentation. Never treat A/B testing as a substitute for deterministic validation.
- Cover requirements, user flows, changed code, contracts, state transitions, roles, integrations, observability, and rollback.
- Prefer the cheapest deterministic layer that gives enough confidence, then reserve end-to-end tests for thin high-value journeys.
- Use the report structure defined in `references/agent.md`.

## Focus On Failure

- Challenge happy-path assumptions.
- Hunt for authorization gaps, data corruption risk, retry and idempotency flaws, race conditions, state machine errors, feature-flag edges, backward-compatibility problems, analytics blind spots, and weak recovery paths.
- Call out design-for-testability problems when architecture, seams, or tooling make meaningful validation too expensive or too flaky.

## Resources

- `references/agent.md` - Canonical workflow, evidence labels, release-gate questions, required output, and prioritization rules.
- `references/system-patterns.md` - Architecture-specific test heuristics, failure patterns, observability checks, and recovery concerns.
