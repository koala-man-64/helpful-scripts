"""Enforce the ordered subagent model ladder in managed repositories.

Every Agent/Task spawn inside a managed repository must carry a task contract
naming the lowest viable tier and documenting why each lower tier is
unsuitable. The gate runs in enforce mode from the start: a missing or
malformed contract, an out-of-order tier, an unbounded fork, or a conflicting
explicit model denies the spawn.

Outside managed repositories the hook emits nothing at all, so unmanaged work
keeps its existing behavior.

The gate rewrites only the model, and only through ``updatedInput`` with no
permission decision attached -- Claude applies ``updatedInput`` from a
PreToolUse hook only when the hook declines to decide permission. Emitting
"allow" here would both suppress the rewrite path and hand this hook a
permission authority it should not hold.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agent_ladder import (
    FORK_AGENT_TYPES,
    MANAGED_ORIGINS,
    TIER_MODEL,
    TIER_ORDER,
    TIER_SHAPE,
    TIER_TURN_GUIDANCE,
    canonical_origin,
    parse_envelope,
    strip_envelope,
    validate,
)
from hook_utils import (
    ascii_text,
    deny_pre_tool,
    emit_json,
    read_hook_input,
    repo_root,
    run_git,
)

SUBAGENT_TOOLS = frozenset({"Agent", "Task"})

LOG_PATH = Path.home() / ".claude" / "logs" / "agent-ladder.jsonl"
LOG_MAX_LINES = 2000

ENVELOPE_TEMPLATE = """<claude_subagent_task_v1>
{
  "tier": "haiku",
  "objective": "<one precise outcome>",
  "scope": ["<path or surface the subagent may touch>"],
  "acceptance_checks": ["<how the parent verifies the result>"],
  "constraints": ["Do not spawn another agent"],
  "decomposition_attempted": true,
  "lower_tier_blockers": {}
}
</claude_subagent_task_v1>"""


def record(origin: str, fields: dict[str, Any]) -> None:
    """Append one bounded, text-free decision record.

    Task text never reaches this file: no prompt, objective, scope, acceptance
    check, constraint, or tool output. Only the routing decision and why.
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
        # Logging is evidence, not a gate. A read-only disk, a full volume, or
        # an unwritable path must never decide whether a spawn is allowed, so
        # this swallows everything rather than just OSError.
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


def reject(origin: str, tier: str, code: str, message: str) -> int:
    record(origin, {"decision": "denied", "tier": tier, "reason_code": code})
    guidance = (
        "{0} [{1}]\n\n"
        "Subagent ladder (lowest viable tier wins):\n"
        "{2}\n\n"
        "Every spawn in a managed repository begins with this envelope:\n{3}\n\n"
        "A higher tier needs a non-empty 'lower_tier_blockers' entry for each "
        "tier beneath it. A failed lower-tier spawn is not automatic promotion: "
        "write a new contract naming that failure as the blocker."
    ).format(
        message,
        code,
        "\n".join(
            "  {0} -> model '{1}', ~{2} turns: {3}".format(
                name, TIER_MODEL[name], TIER_TURN_GUIDANCE[name], TIER_SHAPE[name]
            )
            for name in TIER_ORDER
        ),
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

    if not subagent_type or subagent_type in FORK_AGENT_TYPES:
        return reject(
            origin,
            "",
            "LADDER_FULL_HISTORY_FORK",
            "An implicit fork inherits the full parent transcript, which is the "
            "opposite of the bounded, independently verifiable leaf the ladder "
            "requires. Name an explicit subagent_type and hand it a contract.",
        )

    contract, parse_code = parse_envelope(prompt)
    if parse_code == "LADDER_MISSING_ENVELOPE":
        return reject(
            origin,
            "",
            parse_code,
            "This spawn carries no task contract. Decompose the work into "
            "independent, verifiable leaves, pick the lowest tier that can "
            "actually do one, and lead the prompt with the envelope.",
        )
    if parse_code or contract is None:
        return reject(
            origin,
            "",
            parse_code or "LADDER_MALFORMED_ENVELOPE",
            "The task contract is not a JSON object. The envelope body must "
            "parse on its own, before any prose.",
        )

    failure = validate(contract, subagent_type, explicit_model)
    if failure:
        return reject(origin, str(contract.get("tier") or ""), *failure)

    tier = str(contract["tier"])
    model = TIER_MODEL[tier]
    record(
        origin,
        {
            "decision": "routed",
            "tier": tier,
            "model": model,
            "selection_source": "explicit_model" if explicit_model else "contract_tier",
            "subagent_type": subagent_type,
            "reason_code": "LADDER_OK",
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
                    "Subagent ladder: tier '{0}' routed to model '{1}'. Keep the "
                    "task within its declared scope and acceptance checks; do "
                    "not spawn further agents.".format(tier, model)
                ),
            }
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
