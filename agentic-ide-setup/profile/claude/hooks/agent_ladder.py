"""Shared vocabulary for the ordered subagent model ladder.

The ladder is deliberately three rungs, not four. Claude exposes
``haiku | sonnet | opus | fable`` as spawn models, and only the first three
form a capability ordering; Fable is a different model, not a step above Opus.
The Codex ladder's middle pair (luna, terra) collapses onto Sonnet here.
"""

from __future__ import annotations

import json
import re
from typing import Any

ENVELOPE_TAG = "claude_subagent_task_v1"
ENVELOPE_PATTERN = re.compile(
    rf"\A\s*<{ENVELOPE_TAG}>\s*(?P<body>.*?)\s*</{ENVELOPE_TAG}>",
    re.DOTALL,
)

# Ascending capability. The index is the tier rank, so every lower tier is a
# slice of this tuple.
TIER_ORDER = ("haiku", "sonnet", "opus")

TIER_MODEL = {
    "haiku": "haiku",
    "sonnet": "sonnet",
    "opus": "opus",
}

TIER_SHAPE = {
    "haiku": (
        "single precise outcome, narrow scope, decisions already resolved, "
        "focused verification, low blast radius"
    ),
    "sonnet": (
        "bounded implementation, mechanical work, or read-heavy investigation "
        "needing more context or local reasoning than one precise edit"
    ),
    "opus": (
        "architecture, security, production, migration, data-integrity, or "
        "cross-repository risk"
    ),
}

# Turn caps and tool restrictions are agent-definition fields, not Agent tool
# inputs, so the hook cannot inject them per spawn. They are advisory here and
# become real by choosing an agent whose definition already matches the tier.
TIER_TURN_GUIDANCE = {"haiku": 12, "sonnet": 30, "opus": 60}

# Spawning one of these keeps a read-only contract structurally honest rather
# than merely promised in the constraints list.
READ_ONLY_AGENTS = frozenset({"Explore", "Plan"})
READ_ONLY_MARKERS = (
    "read-only",
    "read only",
    "readonly",
    "do not modify",
    "do not edit",
    "no writes",
    "investigation only",
)

# Inheriting the full parent transcript defeats the decomposition the ladder
# exists to force, so an unbounded fork is never a ladder spawn.
FORK_AGENT_TYPES = frozenset({"fork"})

MANAGED_ORIGINS = frozenset(
    {
        "azure://rdprokes/adaptiveassetallocation/asset-allocation-contracts",
        "azure://rdprokes/adaptiveassetallocation/asset-allocation-runtime-common",
        "azure://rdprokes/adaptiveassetallocation/asset-allocation-jobs",
        "azure://rdprokes/adaptiveassetallocation/asset-allocation-control-plane",
        "azure://rdprokes/adaptiveassetallocation/asset-allocation-ui",
        # Infra is not in the Codex global.json inventory, deliberately added
        # here: it is the highest-blast-radius repo of the set, and Claude works
        # in it directly.
        "azure://rdprokes/adaptiveassetallocation/asset-allocation-infra",
        "azure://rdprokes/adaptiveassetallocation/codex-workflow-hooks",
    }
)

_AZURE_DEVOPS_ORIGIN = re.compile(
    r"dev\.azure\.com[:/](?:v3/)?([^/]+)/([^/]+)/(?:_git/)?([^/]+)\Z"
)
_LEGACY_VISUALSTUDIO_ORIGIN = re.compile(
    r"([^/@]+)\.visualstudio\.com/([^/]+)/(?:_git/)?([^/]+)\Z"
)


def normalize_origin(url: str) -> str:
    """Reduce a remote URL to ``azure://org/project/repo``.

    A worktree directory name is unreliable repository identity: a linked
    worktree is named after the task, not the repository. The remote origin
    survives that, and matches the identity the Codex policy already uses.
    """
    text = url.strip().rstrip("/").lower()
    if text.endswith(".git"):
        text = text[:-4]
    for pattern in (_AZURE_DEVOPS_ORIGIN, _LEGACY_VISUALSTUDIO_ORIGIN):
        match = pattern.search(text)
        if match:
            return "azure://{0}/{1}/{2}".format(*match.groups())
    return text


def canonical_origin(root: Any, runner: Any) -> str:
    """Canonical origin for ``root``, or ``""`` when there is no usable remote.

    ``runner`` is a ``hook_utils.run_git``-shaped callable, injected so this
    module stays free of process and filesystem dependencies.
    """
    code, url = runner(["config", "--get", "remote.origin.url"], root)
    if code != 0 or not url:
        return ""
    return normalize_origin(url)


def is_managed(root: Any, runner: Any) -> bool:
    return canonical_origin(root, runner) in MANAGED_ORIGINS


def ladder_summary() -> str:
    """One compact block describing the ladder, for session context."""
    rungs = "\n".join(
        "  {0} -> model '{1}', ~{2} turns: {3}".format(
            name, TIER_MODEL[name], TIER_TURN_GUIDANCE[name], TIER_SHAPE[name]
        )
        for name in TIER_ORDER
    )
    return (
        "- Subagent model ladder is enforced here. Decompose toward the lowest "
        "viable tier before spawning:\n"
        f"{rungs}\n"
        f"- Lead every subagent prompt with a <{ENVELOPE_TAG}> JSON envelope: "
        "tier, objective, scope, acceptance_checks, constraints, "
        "decomposition_attempted, lower_tier_blockers.\n"
        "- A higher tier needs a non-empty blocker for every tier beneath it. "
        "Unbounded forks and explicit models that contradict the tier are "
        "rejected, and a failed lower-tier spawn is never promoted silently -- "
        "write a new contract naming that failure as the blocker."
    )


def parse_envelope(prompt: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return the leading task contract, or a reason code for its absence."""
    match = ENVELOPE_PATTERN.search(prompt or "")
    if not match:
        return None, "LADDER_MISSING_ENVELOPE"
    try:
        contract = json.loads(match.group("body"))
    except json.JSONDecodeError:
        return None, "LADDER_MALFORMED_ENVELOPE"
    if not isinstance(contract, dict):
        return None, "LADDER_MALFORMED_ENVELOPE"
    return contract, None


def strip_envelope(prompt: str) -> str:
    """Drop the contract from the prompt the subagent actually receives."""
    return ENVELOPE_PATTERN.sub("", prompt or "", count=1).lstrip()


def lower_tiers(tier: str) -> tuple[str, ...]:
    return TIER_ORDER[: TIER_ORDER.index(tier)]


def _nonempty_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def validate(
    contract: dict[str, Any], subagent_type: str, explicit_model: str
) -> tuple[str, str] | None:
    """Return ``(reason_code, message)`` for the first failure, else ``None``."""
    tier = contract.get("tier")
    if not isinstance(tier, str) or tier not in TIER_ORDER:
        return (
            "LADDER_UNKNOWN_TIER",
            "Contract 'tier' must be one of {0}. The ladder has three rungs on "
            "Claude: {1}.".format(
                ", ".join(TIER_ORDER),
                "; ".join(f"{name} = {TIER_SHAPE[name]}" for name in TIER_ORDER),
            ),
        )

    if contract.get("decomposition_attempted") is not True:
        return (
            "LADDER_NO_DECOMPOSITION",
            "Contract must set 'decomposition_attempted': true. Split the task "
            "into independent, verifiable leaves before selecting a tier.",
        )

    if not _nonempty_strings(contract.get("scope")):
        return (
            "LADDER_EMPTY_SCOPE",
            "Contract 'scope' must list at least one concrete path or surface "
            "the subagent may touch.",
        )

    if not _nonempty_strings(contract.get("acceptance_checks")):
        return (
            "LADDER_MISSING_ACCEPTANCE",
            "Contract 'acceptance_checks' must state at least one verifiable "
            "outcome. A task the parent cannot check is not delegable.",
        )

    if _nonempty_strings(contract.get("depends_on")):
        return (
            "LADDER_UNRESOLVED_DEPENDENCY",
            "Contract declares unresolved dependencies in 'depends_on'. Resolve "
            "them in the parent, or spawn the dependency as its own leaf first.",
        )

    blockers = contract.get("lower_tier_blockers")
    if not isinstance(blockers, dict):
        blockers = {}
    for lower in lower_tiers(tier):
        text = blockers.get(lower)
        if not isinstance(text, str) or not text.strip():
            return (
                "LADDER_MISSING_BLOCKER",
                "Tier '{0}' requires a documented blocker for every lower tier "
                "({1}). Missing: '{2}'. Say what specifically makes {2} "
                "unsuitable ({3}), or spawn at {2}.".format(
                    tier,
                    ", ".join(lower_tiers(tier)),
                    lower,
                    TIER_SHAPE[lower],
                ),
            )

    expected_model = TIER_MODEL[tier]
    if explicit_model and explicit_model != expected_model:
        return (
            "LADDER_MODEL_CONFLICT",
            "Explicit model '{0}' conflicts with tier '{1}', which routes to "
            "'{2}'. Change the tier and its blockers, or drop the model "
            "override.".format(explicit_model, tier, expected_model),
        )

    constraints = " ".join(_nonempty_strings(contract.get("constraints"))).lower()
    if any(marker in constraints for marker in READ_ONLY_MARKERS):
        if subagent_type not in READ_ONLY_AGENTS:
            return (
                "LADDER_READONLY_TIER_VIOLATION",
                "Contract declares a read-only constraint, so spawn a subagent "
                "that cannot write: {0}. '{1}' carries edit tools, which makes "
                "the constraint a promise instead of a boundary.".format(
                    " or ".join(sorted(READ_ONLY_AGENTS)),
                    subagent_type or "(none)",
                ),
            )

    return None
