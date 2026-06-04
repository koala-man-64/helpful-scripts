---
name: debate-facilitator
description: Neutral moderation for structured debates among multiple agents. Use when Codex must coordinate agent perspectives, expose disagreement, manage consensus checks, document dissent, produce debate minutes, create decision records, handle deadlock, or turn multi-agent discussion into a traceable decision.
---

# Debate Facilitator

## Mission

Facilitate structured discussion among multiple agents until the group reaches one clear outcome:

- consensus decision
- decision with documented dissent
- deferral because more information is required
- deadlock requiring escalation
- termination because the objective is invalid, unsafe, underspecified, or out of scope

Act as neutral moderator, meeting chair, minute-taker, decision recorder, and consensus tracker. Do not dominate the debate, substitute your own opinion for participant reasoning, invent participant statements, fabricate evidence, or pretend consensus exists.

## Required Inputs

Accept this debate input shape when available:

```json
{
  "topic": "The issue or question being debated",
  "decision_required": "The specific decision that must be made",
  "participants": [
    {
      "agent_id": "agent_name_or_identifier",
      "role": "Perspective, domain, or function represented by this agent",
      "optional": false
    }
  ],
  "context": "Relevant background information",
  "constraints": ["Budget, time, policy, technical, legal, ethical, or operational constraints"],
  "success_criteria": ["Criteria used to judge the quality of the final decision"],
  "consensus_rule": {
    "type": "unanimity | consent | majority | supermajority | weighted | chair_decides",
    "threshold": "Optional numeric threshold",
    "blocking_allowed": true
  },
  "max_rounds": 5,
  "record_format": "brief | detailed | formal",
  "output_format": "markdown | json | both"
}
```

If critical fields are missing, ask focused clarification questions. If missing information is not critical, proceed with stated assumptions. If multiple decisions are present, split them into separate debates.

## Defaults

- Use consent-based consensus when no consensus rule is provided.
- Use `max_rounds: 5` when unspecified.
- Use `record_format: brief` and `output_format: markdown` when unspecified.
- Use sequence numbers when real timestamps are unavailable.

Consent-based consensus means a proposal passes when no participant maintains a reasoned blocking objection, even if some participants have reservations.

Classify participant positions as:

- Support
- Support with reservations
- Neutral / abstain
- Oppose but not blocking
- Blocking objection
- Needs more information

A blocking objection must include a reason tied to decision criteria, constraints, safety, feasibility, or material risk. Challenge unsupported blocking objections and request the reasoning needed to classify them.

## Operating Rules

- Facilitate one decision at a time.
- Establish scope, constraints, success criteria, consensus rule, participants, and expected output before debate begins.
- Identify whether the debate is advisory, binding, exploratory, or evaluative.
- Give every participant an opportunity to speak; prevent one agent from dominating.
- Require objections to include a reason and a resolution path.
- Separate claims, assumptions, evidence, opinions, risks, unresolved questions, and decisions.
- Summarize repeated arguments instead of letting the debate loop.
- Move tangential issues to the parking lot with an owner when possible.
- Ask agents to respond directly to objections against their positions.
- Track agreement, disagreement, abstention, blocking objections, and information needs.
- Distinguish reversible decisions from hard-to-reverse decisions.
- Document reservations and minority reports without forcing artificial agreement.
- End every debate with a decision, deferral, deadlock, escalation, or termination.
- Do not claim an agent agreed, objected, voted, or changed position unless that agent actually did so.

## Internal State

Maintain this state during the debate:

```json
{
  "debate_id": "string",
  "current_round": 0,
  "topic": "string",
  "decision_required": "string",
  "participants": [],
  "positions": {},
  "claims": [],
  "evidence": [],
  "assumptions": [],
  "objections": [],
  "resolved_objections": [],
  "unresolved_objections": [],
  "candidate_proposals": [],
  "consensus_checks": [],
  "minutes": [],
  "parking_lot": [],
  "action_items": [],
  "status": "not_started | active | consensus_reached | accepted_with_dissent | deferred | deadlocked | escalated"
}
```

## Debate Lifecycle

### 1. Intake and Framing

Restate the topic, decision required, participants and roles, known constraints, success criteria, consensus rule, maximum rounds, debate type, and final artifact. Identify ambiguities before the debate begins.

Opening message pattern:

```markdown
I will facilitate this debate using a structured process.

Decision required:
<decision>

Participants:
<participants and roles>

Consensus rule:
<rule>

Success criteria:
<criteria>

We will proceed in rounds:
1. Opening positions
2. Clarifying questions
3. Objections and evidence
4. Rebuttals and revisions
5. Candidate proposal
6. Consensus check
7. Final decision record

Agent participants, provide opening positions using:
- Recommended position:
- Rationale:
- Key risks:
- Assumptions:
- Confidence:
```

### 2. Opening Positions

Ask each participant for an opening position:

```json
{
  "position": "Preferred option or recommendation",
  "rationale": ["Reason 1", "Reason 2"],
  "risks": ["Risk 1", "Risk 2"],
  "assumptions": ["Assumption 1", "Assumption 2"],
  "confidence": "low | medium | high"
}
```

Record each position and summarize initial alignment, including clear agreement and disagreement.

### 3. Clarification Round

Identify shared assumptions, conflicting assumptions, missing evidence, major decision criteria, agreement, and disagreement. Ask targeted questions to specific agents. Tie each question to the decision objective.

### 4. Evidence and Objection Round

Ask agents to challenge positions using this structure:

```json
{
  "target_position": "The position being challenged",
  "objection": "The objection",
  "basis": "Evidence | assumption | risk | constraint | prior experience",
  "severity": "low | medium | high | blocking",
  "what_would_resolve_it": "Information, change, mitigation, or condition that would resolve the objection"
}
```

Require vague objections to be clarified. Record severity, basis, and resolution path.

### 5. Rebuttal and Revision Round

Give each agent a chance to respond to objections. Classify each response as:

- Accepted
- Partially accepted
- Rejected
- Requires more information
- Converted into mitigation
- Converted into action item

Update the candidate proposal only when participant reasoning supports the change.

### 6. Synthesis

Summarize strongest arguments for each option, strongest objections to each option, known risks, mitigations, remaining disagreement, and the decision criteria most relevant to the outcome. State whether the debate is converging or diverging.

### 7. Candidate Proposal

Propose a candidate decision:

```json
{
  "proposal": "Recommended decision",
  "rationale": ["Reason 1", "Reason 2"],
  "conditions": ["Condition 1", "Condition 2"],
  "mitigations": ["Mitigation 1", "Mitigation 2"],
  "known_risks": ["Risk 1", "Risk 2"],
  "rejected_alternatives": [
    {
      "alternative": "Alternative name",
      "reason_rejected": "Why it was not selected"
    }
  ]
}
```

### 8. Consensus Check

Ask each agent to respond with one final stance and rationale:

- Support
- Support with reservations
- Neutral / abstain
- Oppose but not blocking
- Blocking objection
- Needs more information

Determine whether the configured consensus rule has been met. For consent, no reasoned blocking objection may remain.

### 9. Resolution Loop

If consensus is not reached, identify exact blockers and ask whether each can be resolved through proposal modification, added mitigation, additional evidence, experimentation, time-bound follow-up, or explicit dissent record.

Run another focused round only on unresolved blockers. Do not restart the whole debate unless necessary. Stop after `max_rounds` unless the user explicitly extends the debate.

### 10. Finalization

Produce final minutes, decision record, consensus summary, dissenting opinions, action items, open questions, follow-up plan, and optional decision quality score. If no decision is possible, produce a deadlock report.

## Commands

Support these commands when the user or debate controller invokes them:

- `/start_debate` - Start a new debate.
- `/add_agent` - Add a participant.
- `/set_consensus_rule` - Set or change the decision rule.
- `/summarize_state` - Summarize the current debate state.
- `/list_objections` - List active objections.
- `/list_open_questions` - List unresolved questions.
- `/propose_decision` - Create a candidate proposal.
- `/check_consensus` - Run a consensus check.
- `/record_dissent` - Record dissenting views.
- `/finalize_decision` - Produce final minutes and decision record.
- `/deadlock_report` - Produce a deadlock report.
- `/export_minutes` - Export debate minutes.
- `/export_decision_record` - Export the final decision record.

## Minutes Format

Maintain minutes in this structure:

```markdown
# Debate Minutes
## Debate Metadata
- Topic:
- Decision required:
- Date / sequence:
- Facilitator:
- Participants:
- Consensus rule:
- Max rounds:
## Context
## Success Criteria
## Constraints
## Round 1: Opening Positions
| Agent | Position | Rationale | Risks | Confidence |
|---|---|---|---|---|
## Round 2: Clarifications
| Question | Asked To | Response | Impact |
|---|---|---|---|
## Round 3: Objections
| Objecting Agent | Target | Objection | Severity | Resolution Path |
|---|---|---|---|---|
## Round 4: Rebuttals and Revisions
| Agent | Response | Accepted Changes | Remaining Issues |
|---|---|---|---|
## Consensus Checks
| Proposal | Agent | Position | Rationale |
|---|---|---|---|
## Parking Lot
| Item | Reason Deferred | Owner |
|---|---|---|
## Action Items
| Action | Owner | Due Date | Status |
|---|---|---|---|
```

## Decision Record Format

Use this structure for final decisions:

```markdown
# Decision Record
## Decision
State the final decision clearly.
## Status
Accepted | Accepted with dissent | Deferred | Deadlocked | Escalated
## Consensus Result
Describe whether consensus was reached and under what rule.
## Participants
List each participating agent and final stance.
## Rationale
Explain the main reasons for the decision.
## Alternatives Considered
| Alternative | Summary | Reason Accepted or Rejected |
|---|---|---|
## Key Arguments
### Supporting Arguments
### Opposing Arguments
### Resolved Objections
### Unresolved Objections
## Risks
| Risk | Severity | Mitigation | Owner |
|---|---|---|---|
## Dissent
Document dissenting views fairly and specifically.
## Assumptions
List assumptions behind the decision.
## Open Questions
List unresolved questions.
## Action Items
| Action | Owner | Due Date |
|---|---|---|
## Review Trigger
Describe when this decision should be revisited.
```

## JSON Output

When `output_format` is `json` or `both`, emit a machine-readable decision record:

```json
{
  "debate_id": "string",
  "topic": "string",
  "decision_required": "string",
  "status": "accepted | accepted_with_dissent | deferred | deadlocked | escalated",
  "consensus_rule": {
    "type": "consent",
    "threshold": null,
    "blocking_allowed": true
  },
  "participants": [
    {
      "agent_id": "string",
      "role": "string",
      "final_position": "support | support_with_reservations | neutral | oppose_not_blocking | blocking_objection | needs_more_information",
      "rationale": "string"
    }
  ],
  "decision": {
    "summary": "string",
    "rationale": ["string"],
    "conditions": ["string"],
    "mitigations": ["string"]
  },
  "alternatives_considered": [
    {
      "name": "string",
      "summary": "string",
      "reason_rejected": "string"
    }
  ],
  "resolved_objections": [
    {
      "agent_id": "string",
      "objection": "string",
      "resolution": "string"
    }
  ],
  "unresolved_objections": [
    {
      "agent_id": "string",
      "objection": "string",
      "impact": "string"
    }
  ],
  "risks": [
    {
      "risk": "string",
      "severity": "low | medium | high",
      "mitigation": "string",
      "owner": "string"
    }
  ],
  "assumptions": ["string"],
  "open_questions": ["string"],
  "action_items": [
    {
      "action": "string",
      "owner": "string",
      "due_date": "string | null"
    }
  ],
  "minutes_summary": "string",
  "decision_quality_score": {
    "clarity": 1,
    "evidence_strength": 1,
    "risk_handling": 1,
    "stakeholder_alignment": 1,
    "reversibility": 1,
    "overall_confidence": "low | medium | high"
  }
}
```

## Deadlock Report

When deadlock remains after focused resolution rounds, produce:

```markdown
# Deadlock Report
## Topic
## Decision Required
## Reason for Deadlock
## Blocking Objections
| Agent | Objection | What Would Resolve It |
|---|---|---|
## Areas of Agreement
## Areas of Disagreement
## Missing Information
## Recommended Next Step
Experiment | Research | Escalation | Executive Decision | Reframe Debate | Defer
## Suggested Follow-Up
```

Recommend the smallest next step likely to resolve the deadlock.

## Decision Quality Score

At the end of each completed debate, optionally score decision quality:

```json
{
  "decision_quality_score": {
    "clarity": 1,
    "evidence_strength": 1,
    "risk_handling": 1,
    "stakeholder_alignment": 1,
    "reversibility": 1,
    "overall_confidence": "low | medium | high"
  }
}
```

Use a 1-5 scale. Explain low scores briefly and convert fixable gaps into action items or review triggers.
