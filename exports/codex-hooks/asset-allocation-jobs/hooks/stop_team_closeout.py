from hook_utils import block, emit_json, extract_last_message, read_hook_input


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

PLANNING_ONLY_MARKERS = (
    "proposed_plan",
    "plan only",
    "planning-only",
    "recommendation:",
    "i would",
    "suggest",
)

VALIDATION_MARKERS = (
    "validated",
    "verified",
    "test",
    "tests",
    "compile",
    "py_compile",
    "build",
    "not run",
    "could not run",
    "was not run",
)

CHANGE_MARKERS = (
    "changed",
    "updated",
    "added",
    "removed",
    "implemented",
    "propagated",
    "created",
    "configured",
    "touched",
)

BLOCKER_MARKERS = (
    "blocker",
    "blocked",
    "unable",
    "could not",
    "not run",
    "not completed",
    "not finished",
)


def contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def main() -> int:
    payload = read_hook_input()
    if payload.get("stop_hook_active"):
        return 0

    message = extract_last_message(payload).strip()
    normalized = f" {message.lower()} "
    if not message:
        return 0

    if contains_any(normalized, PLANNING_ONLY_MARKERS) and not contains_any(normalized, SUBSTANTIVE_MARKERS):
        return 0

    if not contains_any(normalized, SUBSTANTIVE_MARKERS):
        return 0

    missing = []
    if not contains_any(normalized, CHANGE_MARKERS):
        missing.append("what changed")
    if not contains_any(normalized, VALIDATION_MARKERS):
        missing.append("validation run or explicit not-run reason")
    if "git-hygiene-orchestrator" not in normalized:
        missing.append("git-hygiene-orchestrator completed or blocker")
    if "gateway-bookkeeper" not in normalized:
        missing.append("gateway-bookkeeper completed or blocker")

    incomplete = contains_any(normalized, BLOCKER_MARKERS)
    if incomplete and not ("next action" in normalized or "remaining" in normalized or "left" in normalized):
        missing.append("exact next action for incomplete work")

    if missing:
        reason = "Before finishing, complete the team closeout summary: " + "; ".join(missing) + "."
        return emit_json(block(reason))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
