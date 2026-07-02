from hook_utils import (
    block,
    emit_json,
    extract_last_message,
    read_hook_input,
    requires_bookkeeper_recap,
)


SUBSTANTIVE_MARKERS = (
    "added ",
    "updated ",
    "changed ",
    "implemented ",
    "wired ",
    "created ",
    "removed ",
    "deleted ",
    "patched ",
    "refactored ",
    "configured ",
    "fixed ",
    "ran ",
    "validated ",
    "verified ",
    "tested ",
    "installed ",
    "edited ",
    "propagated ",
    "deployed ",
    "committed ",
    "pushed ",
    "opened pr",
    "created pr",
)

DECISION_MARKERS = (
    "decided ",
    "decision",
    "classified ",
    "routed ",
    "scoped ",
    "deferred ",
    "blocked ",
    "contracts-repo-first",
    "local-only",
)

PLANNING_ONLY_MARKERS = (
    "proposed_plan",
    "plan only",
    "planning-only",
    "recommendation:",
    "i would",
    "suggest",
)

RECAP_MARKERS = (
    "bookkeeper recap",
    "gateway-bookkeeper recap",
    "final bookkeeper update",
)

RECAP_EVIDENCE_MARKERS = (
    "recorded",
    "tracked",
    "updated",
    "completed",
    "blocked",
    "decided",
    "decision",
    "work item",
    "azure devops",
    "boards",
    "pr ",
    "branch",
    "commit",
    "no azure boards",
)


def contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def main() -> int:
    payload = read_hook_input()
    if payload.get("stop_hook_active"):
        return 0

    message = extract_last_message(payload).strip()
    if not message:
        return 0

    normalized = f" {message.lower()} "
    has_substantive_work = contains_any(normalized, SUBSTANTIVE_MARKERS)
    has_decision = contains_any(normalized, DECISION_MARKERS)
    planning_only = contains_any(normalized, PLANNING_ONLY_MARKERS) and not (
        has_substantive_work or has_decision
    )

    if planning_only or not requires_bookkeeper_recap(message):
        return 0

    missing = []
    if not contains_any(normalized, RECAP_MARKERS):
        missing.append("Bookkeeper Recap section")
    if not contains_any(normalized, RECAP_EVIDENCE_MARKERS):
        missing.append("what gateway-bookkeeper recorded, decided, or blocked")

    if missing:
        reason = (
            "Before finishing, include a gateway-bookkeeper recap of what was "
            "done or decided: "
            + "; ".join(missing)
            + "."
        )
        return emit_json(block(reason))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
