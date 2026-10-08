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

import contextlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterator

from agent_ladder import (
    ENVELOPE_TEMPLATE,
    FORK_AGENT_TYPES,
    LANE_CHILD_CAP,
    LANE_ORDER,
    LANE_SHAPE,
    MANAGED_ORIGINS,
    SESSION_CHILD_CAP,
    TIER_MODEL,
    agent_directories,
    beaten_failure,
    canonical_origin,
    child_effort,
    definition_model,
    main_thread_only_agents,
    payload_effort,
    parse_envelope,
    rank_failure,
    read_only_agents,
    resolve_child_model,
    session_profile,
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


LOCK_WAIT_SECONDS = 2.0
LOCK_STALE_SECONDS = 10.0


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[bool]:
    """Hold an exclusive lock file beside ``path``; yields False when it cannot.

    Parallel Agent calls run their hooks concurrently, so the read, check and
    write of a child count must not interleave. A lock older than
    LOCK_STALE_SECONDS belongs to a crashed hook and is broken.
    """
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    held = False
    while not held:
        try:
            lock.parent.mkdir(parents=True, exist_ok=True)
            os.close(os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            held = True
        except (FileExistsError, PermissionError):
            # Windows reports a lock file that another hook is deleting as
            # PermissionError: that is contention too, not an IO fault.
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                    lock.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        except OSError:
            break
    try:
        yield held
    finally:
        if held:
            try:
                lock.unlink()
            except OSError:
                pass


def claim_child_slot(session_id: str, lane: str) -> str:
    """Count one child against the lane's cap and the session's single budget.

    Returns ``""`` when a slot was claimed, else ``"lane"`` or ``"session"``
    naming the exhausted cap. The session budget counts every lane, so
    switching lanes cannot add children. Fail-open on an unknown session, an
    IO fault, or a lock that cannot be taken in time: the cap bounds fan-out,
    it is not a security boundary, and a filesystem problem must not block work.
    """
    path = _children_file(session_id)
    if path is None:
        return ""
    try:
        with _locked(path) as held:
            if not held:
                return ""
            counts: dict[str, Any] = {}
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    counts = loaded
            used = {name: counts.get(name, 0) for name in LANE_ORDER}
            used = {name: n if isinstance(n, int) else 0 for name, n in used.items()}
            if used[lane] >= LANE_CHILD_CAP.get(lane, 0):
                return "lane"
            if sum(used.values()) >= SESSION_CHILD_CAP:
                return "session"
            used[lane] += 1
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(used, sort_keys=True), encoding="utf-8")
    except (OSError, ValueError):
        return ""
    return ""


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


def spawn_profile(payload: dict[str, Any]) -> tuple[str, str]:
    """``(tier, effort)`` of the spawning session: the payload's effort is the
    one in force now; the transcript's is that of the last finished request."""
    tier, transcript_effort = session_profile(payload.get("transcript_path"))
    return tier, payload_effort(payload) or transcript_effort


def unmanaged_spawn(origin: str, root: Path, payload: dict[str, Any]) -> int:
    """Outside managed repositories enforce only two rules, with no envelope,
    rewrite, or cap: no beaten model and effort pair, and a child scoring
    strictly below its parent (Rudy, 2026-10-07).

    A spawn whose model cannot be resolved to a known tier is let through: the
    rules cannot be applied to it, and this is a cost guard, not a boundary.
    """
    if payload.get("agent_id"):
        return emit_json(None)
    tool_input = payload.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    # An omitted subagent_type spawns general-purpose, which inherits.
    subagent_type = str(tool_input.get("subagent_type") or "general-purpose").strip()
    directories = agent_directories(root)
    parent_tier, parent_effort = spawn_profile(payload)
    tier = resolve_child_model(
        str(tool_input.get("model") or ""),
        definition_model(subagent_type, directories),
        os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL", ""),
        parent_tier,
    )
    effort, effort_source = child_effort(
        subagent_type, directories, parent_effort, os.environ.get("CLAUDE_CODE_EFFORT_LEVEL", "")
    )
    facts = {
        "scope": "unmanaged",
        "tier": tier or "unknown",
        "parent_tier": parent_tier or "unknown",
        "parent_effort": parent_effort or "unknown",
        "effort": effort or "unknown",
        "effort_source": effort_source,
        "subagent_type": subagent_type,
    }
    if not tier:
        record(origin, {"decision": "unchecked", **facts, "reason_code": "LANE_MODEL_UNRESOLVED"})
        return emit_json(None)
    failure = beaten_failure(tier, effort) or rank_failure(tier, effort, parent_tier, parent_effort)
    if failure:
        code, message = failure
        record(origin, {"decision": "denied", **facts, "reason_code": code})
        return emit_json(
            deny_pre_tool(
                "{0} [{1}]\n\nSet the child's model with the Agent tool's `model`, or "
                "pick an agent whose definition sets a lower `effort`.".format(message, code)
            )
        )
    record(origin, {"decision": "allowed", **facts, "reason_code": "LANE_OK"})
    return emit_json(None)


def main() -> int:
    payload = read_hook_input()
    if str(payload.get("tool_name") or "") not in SUBAGENT_TOOLS:
        return emit_json(None)

    root = repo_root()
    origin = canonical_origin(root, run_git)
    if origin not in MANAGED_ORIGINS:
        return unmanaged_spawn(origin, root, payload)

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

    parent_tier, parent_effort = spawn_profile(payload)
    effort, effort_source = child_effort(
        subagent_type, directories, parent_effort, os.environ.get("CLAUDE_CODE_EFFORT_LEVEL", "")
    )
    failure = validate(
        contract,
        subagent_type,
        explicit_model,
        parent_tier,
        read_only_agents(directories),
        effort,
        parent_effort,
    )
    if failure:
        return reject(origin, str(contract.get("tier") or ""), *failure)

    lane = str(contract["lane"])
    tier = str(contract["tier"])
    exhausted = claim_child_slot(str(payload.get("session_id") or ""), lane)
    if exhausted:
        return reject(
            origin,
            tier,
            "LANE_CHILD_CAP",
            "The {0} lane allows at most {1} children, and a session at most {2} "
            "across all lanes; this session has used its {3} budget. Do the "
            "remaining work as the owner.".format(
                lane, LANE_CHILD_CAP[lane], SESSION_CHILD_CAP, lane if exhausted == "lane" else "session"
            ),
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
            "parent_effort": parent_effort or "unknown",
            "effort": effort or "unknown",
            "effort_source": effort_source,
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
