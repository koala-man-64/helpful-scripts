"""Classify the request before adding any workflow requirement.

The router states a suggested lane and answers three independent questions --
tracking, commit/PR, delegation -- so a yes to one never implies the others.
It names specialists only as optional hints; no orchestrator or specialist
sequence is required for ordinary delivery.
"""

import hashlib

from agent_ladder import TIER_ORDER, parent_model
from hook_utils import (
    additional_context,
    azure_devops_agent_authority_lines,
    classify_lane,
    classify_work_kind,
    emit_json,
    extract_prompt,
    read_hook_input,
    requires_finish_workflow,
    requires_tracking,
    session_flag_once,
    session_value_changed,
    workflow_scope_enabled,
)


# Stated once per session (see main); these do not vary by prompt.
STANDING_POLICY_LINES = (
    "- Finish authority: when task-owned files change and the user does not explicitly limit scope, the owner completes the git finish workflow (commit, push, PR, merge/completion) before closeout, without waiting for a separate 'finish it' prompt. Delegate finishing only when it is an independent, bounded deliverable the lane permits; never spawn an agent just because work reached the finish stage. When ~/.claude/state/merge-steward.json shows an active steward updated within six hours, the owner instead stops at 'PR opened and steward told'; the steward owns completion. See the merge-steward agent definition.",
    "- Lanes: lite (one owner, no children), standard (solo by default; at most a bounded Haiku reviewer and a bounded Sonnet or Haiku specialist, below the session model), critical (Opus owner; one to three bounded specialists; independent review). Select the model directly; no lower-tier attempts are required.",
    "- Contract routing: before editing shared API, schema, or serialization shapes, classify the work as local-only or contracts-repo-first.",
)


# Optional specialist hints by work area. First match wins; none are required.
SPECIALIST_HINTS = (
    (("pipeline", "build failed", "failed build", "failing check", "failed check", " ci ", "re-queue", "rerun"), "actionmedic"),
    (("production", " prod ", "incident", "outage", "traceback", "exception", "does not exist", "unavailable"), "forensic-debugger"),
    (("azure boards", "work item", "workitem", "ab#", "backlog", "sprint", "bookkeeper"), "gateway-bookkeeper"),
    (("git hygiene", "branch cleanup", "stale branch", "worktree", "repo cleanup", "prune"), "git-hygiene-orchestrator"),
    (("security", "vulnerab", "secret", "credential", " iam ", "rbac", "encryption"), "cloud-security-vulnerability-expert"),
    (("database", "postgres", "sql", "migration", "schema", "index tuning"), "db-steward"),
    (("architecture", "tradeoff", "proposal", "design review"), "architecture-review-agent"),
    (("test plan", "coverage gap", "release readiness", "go/no-go"), "qa-release-gate-agent"),
)


def specialist_hint(prompt: str) -> str:
    normalized = f" {prompt.lower()} "
    for needles, agent in SPECIALIST_HINTS:
        if any(needle in normalized for needle in needles):
            return agent
    return "none"


SHARED_CONTRACT_TERMS = (
    "api response", "api request", "payload", "schema", "serialization", "contract",
    "@asset-allocation/contracts", "asset-allocation-contracts",
)
CONTRACT_SURFACE_HINT = (
    "Potential shared contract surface detected. Route authoring through asset-allocation-contracts "
    "first unless local evidence proves this is repo-private."
)


def mentions_shared_contract(prompt: str) -> bool:
    normalized = prompt.lower()
    return any(term in normalized for term in SHARED_CONTRACT_TERMS)


def delegation_answer(lane: str) -> str:
    if lane in {"question", "lite"}:
        return "no"
    if lane == "standard":
        return "only a bounded Haiku reviewer or a Sonnet or Haiku specialist below the session model, when it clearly helps"
    return "bounded specialists as needed; independent review required"


def owner_model_lines(lane: str, payload: dict) -> list[str]:
    """Flag a session model below the lane owner; a lane never switches models."""
    if lane != "critical":
        return []
    session = parent_model(payload.get("transcript_path"))
    if session not in TIER_ORDER or session == "opus":
        return []
    return [
        f"- Session model: {session}, below the critical-lane owner (opus). Say so "
        "plainly and ask Rudy to switch models; if he proceeds on this model, the "
        "result still needs independent review before completion."
    ]


def is_harness_prompt(prompt: str) -> bool:
    """Prompts the harness injects (task notifications) carry no new request to route."""
    return prompt.lstrip().startswith("<task-notification>")


def main() -> int:
    if not workflow_scope_enabled():
        return emit_json(None)
    payload = read_hook_input()
    session_id = str(payload.get("session_id") or "")
    prompt = extract_prompt(payload)
    if is_harness_prompt(prompt):
        return emit_json(None)
    lane, lane_reason = classify_lane(prompt)
    work_kind = classify_work_kind(prompt)
    question = lane == "question"
    finish_required = not question and requires_finish_workflow(prompt)
    tracking_required = not question and requires_tracking(prompt)

    routing = [
        f"- Work kind: {work_kind}",
        f"- Suggested lane: {lane} ({lane_reason}); confirm against the actual scope and risk",
    ]
    if question:
        routing.append("- Answer from evidence; no ticket, branch, or agent spawn.")
    else:
        routing.extend(
            [
                f"- Tracking needed: {'yes' if tracking_required else 'no'}",
                f"- Commit/PR when files change: {'yes' if finish_required else 'no'}",
                f"- Delegation: {delegation_answer(lane)}",
                *owner_model_lines(lane, payload),
                f"- Optional specialist: {specialist_hint(prompt)}",
            ]
        )
    # Every emitted line is re-sent with every later request in the session, so
    # the routing block is emitted only when it differs from the last one this
    # session saw, and standing text once per session (again after compaction:
    # the session-start hook clears the flags).
    digest = hashlib.sha1("\n".join(routing).encode("utf-8")).hexdigest()[:12]
    routing_changed = session_value_changed(session_id, "router-last-routing", digest)
    lines = ["Team workflow routing:", *routing] if routing_changed else []
    if session_flag_once(session_id, "router-standing-policy"):
        lines.extend(STANDING_POLICY_LINES)
    if (tracking_required or finish_required) and session_flag_once(
        session_id, "router-azure-devops-authority"
    ):
        lines.extend(azure_devops_agent_authority_lines())
    if not question and mentions_shared_contract(prompt):
        lines.append(f"- Contract routing: {CONTRACT_SURFACE_HINT}")
    if not lines:
        return emit_json(None)
    return emit_json(additional_context("UserPromptSubmit", "\n".join(lines)))


if __name__ == "__main__":
    raise SystemExit(main())
