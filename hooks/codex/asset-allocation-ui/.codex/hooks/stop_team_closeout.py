from hook_utils import (
    block,
    emit_json,
    extract_last_message,
    is_planning_or_analysis_only,
    read_hook_input,
    requires_git_hygiene,
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

INVALID_FINISH_BLOCKER_MARKERS = (
    "not requested",
    "was not requested",
    "workflow was not requested",
)

EXPLICIT_SCOPE_LIMIT_MARKERS = (
    "user explicitly asked not to",
    "user explicitly limited scope",
    "read-only",
    "no commit",
    "no push",
    "local-only",
    "no pr",
    "no pull request",
)

GIT_HYGIENE_FINISH_MARKERS = (
    "committed",
    "pushed",
    "opened pr",
    "created pr",
    "pull request",
    "merged",
    "merge",
    "auto-complete",
    "completed pr",
    "completed pull request",
)

GIT_HYGIENE_FINISH_REQUIREMENT = (
    "git-hygiene-orchestrator completed the allowed finish workflow "
    "(commit, push, pull request, merge/completion) or reported the exact blocker"
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

    if is_planning_or_analysis_only(message) or (
        contains_any(normalized, PLANNING_ONLY_MARKERS)
        and not contains_any(normalized, SUBSTANTIVE_MARKERS)
    ):
        return 0

    if not contains_any(normalized, SUBSTANTIVE_MARKERS):
        return 0

    missing = []
    has_change = contains_any(normalized, CHANGE_MARKERS)
    needs_git_hygiene = requires_git_hygiene(message)
    has_blocker = contains_any(normalized, BLOCKER_MARKERS)
    if needs_git_hygiene and not has_change:
        missing.append("what changed")
    if (has_change or needs_git_hygiene) and not contains_any(normalized, VALIDATION_MARKERS):
        missing.append("validation run or explicit not-run reason")
    if needs_git_hygiene and "git-hygiene-orchestrator" not in normalized:
        missing.append(GIT_HYGIENE_FINISH_REQUIREMENT)
    elif (
        needs_git_hygiene
        and contains_any(normalized, INVALID_FINISH_BLOCKER_MARKERS)
        and not contains_any(normalized, EXPLICIT_SCOPE_LIMIT_MARKERS)
    ):
        missing.append(
            "git-hygiene-orchestrator must complete the allowed finish workflow or report an actual blocker; blanket finish approval already exists"
        )
    elif (
        needs_git_hygiene
        and not contains_any(normalized, GIT_HYGIENE_FINISH_MARKERS)
        and not has_blocker
    ):
        missing.append(
            "git-hygiene-orchestrator finish workflow details "
            "(commit, push, pull request, merge/completion) or exact blocker"
        )

    if has_blocker and not ("next action" in normalized or "remaining" in normalized or "left" in normalized):
        missing.append("exact next action for incomplete work")

    if missing:
        reason = "Before finishing, complete the team closeout summary: " + "; ".join(missing) + "."
        return emit_json(block(reason))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
