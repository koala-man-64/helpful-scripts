"""Shared vocabulary for direct, lane-based subagent routing.

Lanes are alternatives chosen from a task's scope and risk, not a cumulative
ladder: a spawn names its lane and model directly, and nothing requires a
failed or justified lower tier first. The gate checks that the chosen model
is one the lane permits, that it ranks strictly below the parent, and that the
task is bounded and verifiable.

Claude exposes ``haiku | sonnet | opus | fable`` as spawn models. Only the
first three form a capability order; Fable is a different model, not a step
above Opus, so it is never a routed child and a Fable parent counts as unknown.

The module is still named ``agent_ladder`` so the installed hook paths in
settings.json do not change.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ENVELOPE_TAG = "claude_subagent_task_v2"
# v1 envelopes still parse so an old prompt gets a precise lane error instead
# of "missing envelope"; their ladder-only fields are ignored.
ENVELOPE_PATTERN = re.compile(
    r"\A\s*<(?P<tag>claude_subagent_task_v[12])>\s*(?P<body>.*?)\s*</(?P=tag)>",
    re.DOTALL,
)

# Ascending capability; the index is the rank.
TIER_ORDER = ("haiku", "sonnet", "opus")

TIER_MODEL = {
    "haiku": "haiku",
    "sonnet": "sonnet",
    "opus": "opus",
}

LANE_ORDER = ("lite", "standard", "critical")

LANE_SHAPE = {
    "lite": "bounded mechanical work; one owner, no children",
    "standard": "Sonnet owner, solo by default; at most two bounded Haiku children (reviewer, specialist)",
    "critical": "Opus owner; one to three bounded Sonnet or Haiku specialists with independent evidence",
}

# Which child tiers each lane permits, and how many children per session.
LANE_CHILD_TIERS = {
    "lite": (),
    "standard": ("haiku",),
    "critical": ("haiku", "sonnet"),
}
LANE_CHILD_CAP = {"lite": 0, "standard": 2, "critical": 3}

# Turn caps and effort are agent-definition fields, not Agent tool inputs, so
# the hook cannot inject them per spawn. They become real only by choosing an
# agent whose definition already sets them.
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

# Inheriting the full parent transcript defeats a bounded child.
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


def lane_summary() -> str:
    """One compact block describing lane routing, for session context."""
    lanes = "\n".join(f"  {name}: {LANE_SHAPE[name]}" for name in LANE_ORDER)
    return (
        "- Subagent routing is lane-based here. Pick the smallest sufficient lane "
        "and select the model directly; no lower-tier attempts or blocker "
        "justifications are required:\n"
        f"{lanes}\n"
        "- A child must rank strictly below its parent (haiku < sonnet < opus); "
        "Fable is never a routed child. Effort is set only by agent-definition "
        "frontmatter, and a lane never changes the running session's model.\n"
        f"- Lead every subagent prompt with a <{ENVELOPE_TAG}> JSON envelope: "
        "lane, tier, objective, scope, acceptance_checks, constraints, "
        "routing_reason."
    )


def parse_envelope(prompt: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return the leading task contract, or a reason code for its absence."""
    match = ENVELOPE_PATTERN.search(prompt or "")
    if not match:
        return None, "LANE_MISSING_ENVELOPE"
    try:
        contract = json.loads(match.group("body"))
    except json.JSONDecodeError:
        return None, "LANE_MALFORMED_ENVELOPE"
    if not isinstance(contract, dict):
        return None, "LANE_MALFORMED_ENVELOPE"
    return contract, None


def strip_envelope(prompt: str) -> str:
    """Drop the contract from the prompt the subagent actually receives."""
    return ENVELOPE_PATTERN.sub("", prompt or "", count=1).lstrip()


def model_family(model_id: Any) -> str:
    """Map a transcript model id (``claude-opus-5``) to a tier name, or ``""``."""
    if not isinstance(model_id, str):
        return ""
    text = model_id.lower()
    for tier in TIER_ORDER:
        if tier in text:
            return tier
    return ""


def parent_model(transcript_path: Any, max_lines: int = 400) -> str:
    """Tier of the session model that issued the spawn, or ``""`` when unknown.

    Reads the newest main-thread assistant record. Unknown is a real outcome
    (no transcript, synthetic records, Fable) and callers must treat it as the
    most restrictive case rather than guessing.
    """
    if not isinstance(transcript_path, str) or not transcript_path:
        return ""
    try:
        lines = Path(transcript_path).read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()[-max_lines:]
    except OSError:
        return ""
    for line in reversed(lines):
        if '"assistant"' not in line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") != "assistant" or record.get("isSidechain"):
            continue
        model = (record.get("message") or {}).get("model")
        if not isinstance(model, str) or model.startswith("<"):
            continue
        return model_family(model)
    return ""


def _nonempty_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def validate(
    contract: dict[str, Any],
    subagent_type: str,
    explicit_model: str,
    parent_tier: str,
) -> tuple[str, str] | None:
    """Return ``(reason_code, message)`` for the first failure, else ``None``.

    ``parent_tier`` is the parent's tier name, or ``""`` when unknown.
    """
    lane = contract.get("lane")
    if not isinstance(lane, str) or lane not in LANE_ORDER:
        return (
            "LANE_UNKNOWN_LANE",
            "Contract 'lane' must be one of {0}: {1}.".format(
                ", ".join(LANE_ORDER),
                "; ".join(f"{name} = {LANE_SHAPE[name]}" for name in LANE_ORDER),
            ),
        )

    tier = contract.get("tier")
    if not isinstance(tier, str) or tier not in TIER_ORDER:
        return (
            "LANE_UNKNOWN_TIER",
            "Contract 'tier' must be one of {0}.".format(", ".join(TIER_ORDER)),
        )

    if lane == "lite":
        return (
            "LANE_LITE_NO_CHILDREN",
            "The lite lane has one owner and no children. Do the work directly, "
            "or re-scope the task to standard or critical if it genuinely needs "
            "a bounded child.",
        )

    if tier not in LANE_CHILD_TIERS[lane]:
        return (
            "LANE_TIER_NOT_PERMITTED",
            "The {0} lane permits child tiers: {1}. Choose one of those, or "
            "re-scope the lane if the task's risk actually warrants it.".format(
                lane, ", ".join(LANE_CHILD_TIERS[lane])
            ),
        )

    if not _nonempty_strings(contract.get("scope")):
        return (
            "LANE_EMPTY_SCOPE",
            "Contract 'scope' must list at least one concrete path or surface "
            "the subagent may touch.",
        )

    if not _nonempty_strings(contract.get("acceptance_checks")):
        return (
            "LANE_MISSING_ACCEPTANCE",
            "Contract 'acceptance_checks' must state at least one verifiable "
            "outcome. A task the parent cannot check is not delegable.",
        )

    if _nonempty_strings(contract.get("depends_on")):
        return (
            "LANE_UNRESOLVED_DEPENDENCY",
            "Contract declares unresolved dependencies in 'depends_on'. Resolve "
            "them in the parent first.",
        )

    child_rank = TIER_ORDER.index(tier)
    if parent_tier in TIER_ORDER:
        if child_rank >= TIER_ORDER.index(parent_tier):
            return (
                "LANE_CHILD_NOT_LOWER",
                "A child must rank strictly below its parent. This session runs "
                "on {0}, so '{1}' is not permitted. Pick a lower tier, or do the "
                "work in this session. A lane choice does not change the "
                "session's model; if the task needs a stronger owner, say so and "
                "ask Rudy to switch models.".format(parent_tier, tier),
            )
    elif child_rank > 0:
        return (
            "LANE_PARENT_UNKNOWN",
            "The parent session's model could not be determined, so only a "
            "haiku child is permitted. Use tier 'haiku' or do the work directly.",
        )

    expected_model = TIER_MODEL[tier]
    if explicit_model and explicit_model != expected_model:
        return (
            "LANE_MODEL_CONFLICT",
            "Explicit model '{0}' conflicts with tier '{1}', which routes to "
            "'{2}'. Change the tier or drop the model override.".format(
                explicit_model, tier, expected_model
            ),
        )

    constraints = " ".join(_nonempty_strings(contract.get("constraints"))).lower()
    if any(marker in constraints for marker in READ_ONLY_MARKERS):
        if subagent_type not in READ_ONLY_AGENTS:
            return (
                "LANE_READONLY_AGENT_VIOLATION",
                "Contract declares a read-only constraint, so spawn a subagent "
                "that cannot write: {0}. '{1}' carries edit tools, which makes "
                "the constraint a promise instead of a boundary.".format(
                    " or ".join(sorted(READ_ONLY_AGENTS)),
                    subagent_type or "(none)",
                ),
            )

    return None
