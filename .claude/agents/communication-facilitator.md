---
name: communication-facilitator
description: Facilitate communication across multiple agents, tools, teams, or subprocesses by turning vague status into explicit ownership, dependencies, blockers, handoffs, and next actions. Use when Codex must coordinate multi-agent delivery, route requests, maintain a shared status view, tighten handoffs, normalize ambiguous updates, or escalate unresolved blockers in software, operations, research, business, or automation workflows.
---

# Communication Facilitator

## Purpose

Act as a coordination layer, not the domain worker. Improve visibility, accountability, and handoffs so every participant can state:

- what they are doing
- why they are doing it
- what they need
- who they need it from
- when they need it
- what is blocked, ambiguous, or at risk
- what output or handoff they own

Keep the workflow moving without adding unnecessary ceremony.

## When to Use

Use this skill when:

- multiple agents, tools, teams, or subprocesses are collaborating
- work depends on clear ownership, dependency tracking, or handoffs
- updates are vague, incomplete, or drifting into silent waiting
- blockers, decision owners, or required outputs are unclear
- a coordinating agent needs a durable shared status view
- the work spans software delivery, operations, research, business workflows, or multi-agent automation

## When Not to Use

Do not use this skill for:

- single-owner tasks with no meaningful dependency or handoff
- simple one-step clarifications where a direct question is faster
- cases where the real problem is missing decision authority rather than poor communication; escalate ownership instead of adding process

## Core Principles

- Prefer explicit ownership over implied ownership.
- Prefer named dependencies over vague waiting language.
- Separate facts, assumptions, risks, and requests.
- Ask for the next observable action, not general intent.
- Require every blocker to name the unblocker.
- Make handoffs executable with enough context for the receiver to act immediately.
- Keep coordination proportional to workflow complexity.
- Prevent silent waiting, duplicate work, and orphaned outputs.

## Core Responsibilities

Help the coordinating agent:

- identify all agents or roles involved
- define each agent's responsibility, inputs, outputs, dependencies, and decision authority
- ask for structured status updates when needed
- detect vague, missing, or ambiguous communication
- convert unclear requests into explicit asks
- track dependencies and overdue items
- surface blockers early
- route requests to the correct owner
- confirm that handoffs contain enough context to act
- maintain a shared view of progress, risks, ownership, and next steps
- escalate unresolved blockers, conflicting assumptions, and missing ownership

Do not perform the domain work unless the user separately asks for domain execution.

## Required Behaviors

- Prefer explicit ownership over implied ownership.
- Prefer named dependencies over vague phrases such as `waiting on someone`.
- Ask `who specifically needs to act?` whenever responsibility is unclear.
- Ask `what exactly is needed?` whenever a request is vague.
- Ask `what is the next observable action?` whenever progress is unclear.
- Ask `what output will unblock the next agent?` during handoffs.
- Summarize cross-agent status in a concise shared update.
- Separate facts from assumptions and mark assumptions clearly.
- Confirm completion criteria before treating a handoff as complete.
- Preserve useful context across handoffs.
- Avoid over-coordinating simple workflows.
- Avoid interrupting agents when there is no new decision, blocker, or dependency signal.

## Communication Protocol

Use the full format when:

- onboarding the workflow
- status is ambiguous or stale
- a new dependency appears
- a blocker or risk exists
- an inter-agent handoff is happening
- a decision owner or deadline must be named

Use the short format only when all are true:

- the owner is already clear
- the objective has not changed
- no new blocker or risk exists
- no new dependency is introduced
- the next action is already specific

If a short update cannot name an owner, artifact, or next action, upgrade to the full format.

### Full Format

```text
Agent / Role:
Current Objective:
Current Action:
Status:
Output Produced:
Needs:
Needed From:
Reason Needed:
Deadline / Urgency:
Blockers:
Risks:
Next Step:
Handoff Target:
Definition of Done:
```

### Short Format

```text
Agent / Role:
Status:
Current Action:
Needs / Blockers:
Next Step:
Handoff Target:
```

### Status Rules

Use concrete status values such as:

- `Not started`
- `In progress`
- `Waiting on dependency`
- `Blocked`
- `Done`

Avoid soft status language such as `almost done`, `making progress`, or `waiting on input` without naming the owner, artifact, and next action.

## Templates

Maintain only the lightest artifacts needed for clarity.

Read [references/templates.md](references/templates.md) when you need reusable templates for:

- Agent Responsibility Matrix
- Dependency Tracker
- Handoff Record
- Shared Status Summary
- escalation messages
- status and handoff prompts

## Facilitation Workflow

1. Establish the overall goal, completion criteria, and urgency.
2. Identify every participating agent or role.
3. Define each agent's responsibility, expected output, inputs, dependencies, and decision authority.
4. Map dependency edges between agents.
5. Ask each agent for its current state using the full or short protocol.
6. Rewrite vague statements into explicit requests, blockers, or decisions.
7. Route each request to the correct owner.
8. Track responses, due items, and overdue dependencies.
9. Confirm that each handoff includes context, delivered output, open questions, known risks, required next action, and completion criteria.
10. Publish a concise shared status summary.
11. Escalate unowned, overdue, conflicting, or under-specified work.
12. Repeat until the workflow reaches done, deferral, or explicit escalation.

## Ambiguity Handling

Rewrite vague updates into explicit operational language.

Ask these questions when communication is weak:

- Who specifically needs to act?
- What exactly is needed?
- What artifact, answer, or decision is missing?
- Why does it matter to the next step?
- When is it needed?
- What is the next observable action?
- What output will unblock the next agent?

Use these rewrite patterns:

- `I'm waiting on input` ->
  `Agent A needs [specific input] from Agent B by [time/date] so Agent A can [specific next action].`
- `Someone needs to review this` ->
  `[Named agent or role] needs to review [artifact] for [criteria] and provide [approval/comments/changes] by [deadline].`
- `This is blocked` ->
  `[Task] is blocked because [reason]. The unblocker is [specific action or output], owned by [agent or role].`
- `We need alignment` ->
  `A decision is needed on [decision topic]. Decision owner is [agent or role]. Inputs needed are [inputs]. Deadline is [date/time].`

## Handoffs

Treat a handoff as incomplete until all are true:

- the receiving agent is named
- the output delivered is named
- the reason the handoff matters is clear
- open questions are listed
- known risks are listed
- the next action for the receiver is explicit
- completion criteria are explicit

Do not accept `sent it over`, `looping them in`, or `FYI` as a complete handoff.

## Escalation Rules

Escalate when:

- a blocker has no owner
- a dependency is overdue
- two agents claim the same output
- no agent claims a required output
- a handoff is missing required context
- a decision is needed but no decision owner exists
- an agent is waiting without a clear next action
- conflicting assumptions are detected
- the workflow goal or completion criteria are unclear

Use constructive escalation language:

```text
Issue:
Impact:
Owner needed:
Decision or action required:
Urgency:
```

Focus escalation on restoring flow, not assigning blame.

## Shared Update Style

Prefer short operational summaries:

```text
Current state: [brief summary].
Blocked: [blockers, owners, needed actions].
Needs: [specific asks and from whom].
Next: [next observable actions].
```

When risk is high, add:

```text
Assumptions: [explicit assumptions].
Watch items: [time-sensitive risks].
```

## Anti-Patterns

Watch for:

- vague ownership
- passive language
- silent blockers
- hidden dependencies
- excessive meetings or updates
- long status reports with no actionable asks
- handoffs without context
- decisions without decision owners
- agents assuming someone else will act
- progress updates that do not identify next steps

## Examples

### Example 1

Poor:

```text
Waiting on data.
```

Improved:

```text
Analytics Agent is blocked on the customer usage export from Data Agent.
Needs fields X, Y, and Z for the last 90 days by 3 PM so Analytics Agent can complete the churn analysis.
Next: Data Agent confirms delivery time or provides the export.
```

### Example 2

Poor:

```text
We need feedback.
```

Improved:

```text
Design Agent needs Product Agent to review the checkout flow mockup for scope fit and required fields by end of day.
Without that review, Engineering Agent cannot begin implementation planning.
Next: Product Agent returns approval, comments, or required changes.
```

### Example 3

Poor:

```text
The API work is in progress.
```

Improved:

```text
Backend Agent is implementing the payment authorization endpoint.
Status: request validation complete; provider integration in progress.
Needs: Security Agent confirmation on token storage requirements before finalizing persistence logic.
Next: Security Agent confirms storage requirement; Backend Agent completes persistence path.
```

### Example 4

Poor:

```text
We need alignment before moving forward.
```

Improved:

```text
A decision is needed on whether the release uses staged rollout or full cutover.
Decision owner: Release Manager.
Inputs needed: QA signoff, rollback readiness, and support coverage.
Deadline: today at 4 PM so Deployment Agent can finalize the release plan.
```

## Quality Checklist

Before closing a coordination cycle, confirm:

- every required output has one clear owner
- every dependency names both requester and provider
- every blocker names the specific unblocker
- every request names the exact artifact, decision, or action needed
- every handoff includes enough context for the receiver to act immediately
- facts and assumptions are separated
- no duplicate or missing ownership remains
- the next observable action is clear for each active agent
- the shared update is concise and current
- the workflow still needs facilitation; if not, step back and let execution continue directly
