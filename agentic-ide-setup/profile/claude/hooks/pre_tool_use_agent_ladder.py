"""Validate lane-based subagent routing in managed repositories.

Every Agent/Task spawn inside a managed repository carries a task contract
naming its lane and tier directly. The gate checks bounded scope, a permitted
tier for the lane, a child strictly below its parent, a per-session child cap,
and structural read-only enforcement. It never asks for lower-tier blockers:
lanes are alternatives, not a sequence to climb.

Outside managed repositories the hook emits nothing at all.

The gate rewrites only the model and prompt, and only through ``updatedInput``
with no permission decision attached -- Claude applies ``updatedInput`` from a
PreToolUse hook only when the hook declines to decide permission. Emitting
"allow" here would both suppress the rewrite path and hand this hook a
permission authority it should not hold.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from agent_ladder import (
    FORK_AGENT_TYPES,
    LANE_CHILD_CAP,
    LANE_ORDER,
    LANE_SHAPE,
    MANAGED_ORIGINS,
    TIER_MODEL,
    agent_directories,
    canonical_origin,
    main_thread_only_agents,
    parent_model,
    parse_envelope,
    read_only_agents,
    strip_envelope,
    unreadable_agents,
    validate,
)
from hook_utils import (
    ascii_text,
    deny_pre_tool,
    emit_json,
    read_hook_input,
    repo_root,
    run_git,
    session_flags_dir,
)

SUBAGENT_TOOLS = frozenset({"Agent", "Task"})

LOG_PATH = Path.home() / ".claude" / "logs" / "agent-ladder.jsonl"
LOG_MAX_LINES = 2000

ENVELOPE_TEMPLATE = """<claude_subagent_task_v2>
{
  "lane": "standard",
  "tier": "haiku",
  "objective": "<one precise outcome>",
  "scope": ["<path or surface the subagent may touch>"],
  "acceptance_checks": ["<how the parent verifies the result>"],
  "constraints": ["Do not spawn another agent"],
  "routing_reason": "<why this lane and model>"
}
</claude_subagent_task_v2>"""


def record(origin: str, fields: dict[str, Any]) -> None:
    """Append one bounded, text-free decision record.

    Task text never reaches this file: no prompt, objective, scope, acceptance
    check, constraint, routing reason, or tool output. Only the routing
    decision and why.
    """
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repository": origin.rsplit("/", 1)[-1],
        **fields,
    }
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="ascii", errors="replace") as handle:
            handle.write(json.dumps(entry, separators=(",", ":")) + "\n")
        trim_log()
    except Exception:
        # Logging is evidence, not a gate. Swallow everything so a disk fault
        # never decides whether a spawn is allowed.
        pass


def trim_log() -> None:
    try:
        lines = LOG_PATH.read_text(encoding="ascii", errors="replace").splitlines()
        if len(lines) <= LOG_MAX_LINES:
            return
        keep = lines[-LOG_MAX_LINES:]
        LOG_PATH.write_text("\n".join(keep) + "\n", encoding="ascii", errors="replace")
    except Exception:
        pass


def _children_file(session_id: str) -> Path | None:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", session_id or "")
    if not cleaned:
        return None
    # Separate from the session's flag file, which session start clears on
    # compaction; a compaction must not reset the child budget.
    return session_flags_dir() / f"{cleaned}.children.json"


def claim_child_slot(session_id: str, lane: str) -> bool:
    """Count one child against the lane's per-session cap.

    Fail-open on an unknown session or IO fault: the cap bounds fan-out, it is
    not a security boundary, and a filesystem problem must not block work.
    """
    cap = LANE_CHILD_CAP.get(lane, 0)
    path = _children_file(session_id)
    if path is None:
        return True
    try:
        counts: dict[str, Any] = {}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                counts = loaded
        used = counts.get(lane, 0)
        used = used if isinstance(used, int) else 0
        if used >= cap:
            return False
        counts[lane] = used + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(counts, sort_keys=True), encoding="utf-8")
    except (OSError, ValueError):
        return True
    return True


def reject(origin: str, tier: str, code: str, message: str) -> int:
    record(origin, {"decision": "denied", "tier": tier, "reason_code": code})
    guidance = (
        "{0} [{1}]\n\n"
        "Lanes (choose the smallest sufficient one; no lower-tier attempts or "
        "blocker justifications are needed):\n"
        "{2}\n\n"
        "Every spawn in a managed repository begins with this envelope:\n{3}"
    ).format(
        message,
        code,
        "\n".join(f"  {name}: {LANE_SHAPE[name]}" for name in LANE_ORDER),
        ENVELOPE_TEMPLATE,
    )
    return emit_json(deny_pre_tool(guidance))


def main() -> int:
    payload = read_hook_input()
    if str(payload.get("tool_name") or "") not in SUBAGENT_TOOLS:
        return emit_json(None)

    root = repo_root()
    origin = canonical_origin(root, run_git)
    if origin not in MANAGED_ORIGINS:
        return emit_json(None)

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    subagent_type = str(tool_input.get("subagent_type") or "").strip()
    explicit_model = str(tool_input.get("model") or "").strip()
    prompt = tool_input.get("prompt")
    prompt = prompt if isinstance(prompt, str) else ""

    if payload.get("agent_id"):
        return reject(
            origin,
            "",
            "LANE_NESTED_SPAWN",
            "Subagents do not spawn subagents. Return the need to the owner, "
            "which keeps integration and final validation.",
        )

    if not subagent_type or subagent_type in FORK_AGENT_TYPES:
        return reject(
            origin,
            "",
            "LANE_FULL_HISTORY_FORK",
            "An implicit fork inherits the full parent transcript, which is the "
            "opposite of a bounded, independently verifiable child. Name an "
            "explicit subagent_type and hand it a contract.",
        )

    directories = agent_directories(root)
    if subagent_type.lower() in {name.lower() for name in unreadable_agents(directories)}:
        return reject(
            origin,
            "",
            "LANE_AGENT_DEFINITION_UNREADABLE",
            f"The definition of '{subagent_type}' cannot be read reliably (no frontmatter, no "
            "closing fence, or a tool list given twice), so the gate cannot tell what it may do. "
            "Fix the definition before spawning it.",
        )
    if subagent_type.lower() in {name.lower() for name in main_thread_only_agents(directories)}:
        return reject(
            origin,
            "",
            "LANE_MAIN_THREAD_ONLY",
            f"'{subagent_type}' coordinates from the main thread (`claude --agent "
            f"{subagent_type}`, or its own session); as a spawned child it cannot "
            "route specialists. Do the work as the owner, or spawn the specialist directly.",
        )

    contract, parse_code = parse_envelope(prompt)
    if parse_code == "LANE_MISSING_ENVELOPE":
        return reject(
            origin,
            "",
            parse_code,
            "This spawn carries no task contract. Lead the prompt with the "
            "envelope naming the lane and tier you selected.",
        )
    if parse_code or contract is None:
        return reject(
            origin,
            "",
            parse_code or "LANE_MALFORMED_ENVELOPE",
            "The task contract is not a JSON object. The envelope body must "
            "parse on its own, before any prose.",
        )

    parent_tier = parent_model(payload.get("transcript_path"))
    failure = validate(contract, subagent_type, explicit_model, parent_tier, read_only_agents(directories))
    if failure:
        return reject(origin, str(contract.get("tier") or ""), *failure)

    lane = str(contract["lane"])
    tier = str(contract["tier"])
    if not claim_child_slot(str(payload.get("session_id") or ""), lane):
        return reject(
            origin,
            tier,
            "LANE_CHILD_CAP",
            "The {0} lane allows at most {1} children per session. Do the "
            "remaining work as the owner, or re-scope if the task's risk "
            "actually changed.".format(lane, LANE_CHILD_CAP[lane]),
        )

    model = TIER_MODEL[tier]
    record(
        origin,
        {
            "decision": "routed",
            "lane": lane,
            "tier": tier,
            "model": model,
            "parent_tier": parent_tier or "unknown",
            "selection_source": "explicit_model" if explicit_model else "contract_tier",
            "subagent_type": subagent_type,
            "reason_code": "LANE_OK",
        },
    )

    updated = dict(tool_input)
    updated["model"] = model
    updated["prompt"] = strip_envelope(prompt)

    # No permissionDecision: that is what makes Claude apply updatedInput, and
    # it leaves the actual allow/deny to the normal permission flow.
    return emit_json(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": updated,
                "additionalContext": ascii_text(
                    "Subagent routing: {0} lane, tier '{1}' routed to model "
                    "'{2}'. Keep the task within its declared scope and "
                    "acceptance checks; do not spawn further agents.".format(
                        lane, tier, model
                    )
                ),
            }
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
