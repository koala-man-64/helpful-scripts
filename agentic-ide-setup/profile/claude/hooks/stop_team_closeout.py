from hook_utils import (
    block,
    branch_header,
    emit_json,
    extract_last_message,
    git_status_lines,
    is_planning_or_analysis_only,
    read_hook_input,
    requires_git_hygiene,
    turn_did_work,
    workflow_scope_enabled,
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

# Phrasings that satisfy "say what happens next" when a blocker is reported.
# The original three literals rejected ordinary wordings and demanded a
# rewrite even when the next step was already stated.
NEXT_ACTION_MARKERS = (
    "next action",
    "next step",
    "remaining",
    "left",
    "follow-up",
    "follow up",
    "awaiting",
    "waiting on",
    "pending",
    "still needs",
    "to do:",
    "todo",
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

FINISH_WORKFLOW_MENTION_MARKERS = (
    "git-hygiene",
    "finish workflow",
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
    "finish workflow completed "
    "(commit, push, pull request, merge/completion) or the exact blocker reported"
)


def contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def repo_has_unfinished_git_state() -> bool:
    header = branch_header()
    has_ahead_commits = "[ahead" in header
    has_pending_paths = any(
        not line.startswith("##") for line in git_status_lines()
    )
    return has_ahead_commits or has_pending_paths


def main() -> int:
    payload = read_hook_input()
    if payload.get("stop_hook_active"):
        return 0
    if not workflow_scope_enabled():
        return 0
    if not turn_did_work(payload):
        return 0

    message = extract_last_message(payload).strip()
    normalized = f" {message.lower()} "
    if not message:
        return 0

    repo_needs_finish = repo_has_unfinished_git_state()
    if is_planning_or_analysis_only(message) or (
        contains_any(normalized, PLANNING_ONLY_MARKERS)
        and not contains_any(normalized, SUBSTANTIVE_MARKERS)
        and not repo_needs_finish
    ):
        return 0

    if not contains_any(normalized, SUBSTANTIVE_MARKERS) and not repo_needs_finish:
        return 0

    missing = []
    has_change = contains_any(normalized, CHANGE_MARKERS) or repo_needs_finish
    needs_git_hygiene = requires_git_hygiene(message) or repo_needs_finish
    has_blocker = contains_any(normalized, BLOCKER_MARKERS)
    if needs_git_hygiene and not has_change:
        missing.append("what changed")
    if (has_change or needs_git_hygiene) and not contains_any(normalized, VALIDATION_MARKERS):
        missing.append("validation run or explicit not-run reason")
    if needs_git_hygiene and not contains_any(normalized, FINISH_WORKFLOW_MENTION_MARKERS):
        missing.append(GIT_HYGIENE_FINISH_REQUIREMENT)
    elif (
        needs_git_hygiene
        and contains_any(normalized, INVALID_FINISH_BLOCKER_MARKERS)
        and not contains_any(normalized, EXPLICIT_SCOPE_LIMIT_MARKERS)
    ):
        missing.append(
            "the finish workflow must complete or report an actual blocker; blanket finish approval already exists"
        )
    elif (
        needs_git_hygiene
        and not contains_any(normalized, GIT_HYGIENE_FINISH_MARKERS)
        and not has_blocker
    ):
        missing.append(
            "finish workflow details "
            "(commit, push, pull request, merge/completion) or exact blocker"
        )
    if has_change:
        if "code-drift-sentinel" not in normalized:
            missing.append("code-drift-sentinel completed, not applicable, or blocker")
        if "software-testing-validation-architect" not in normalized:
            missing.append("software-testing-validation-architect completed, not applicable, or blocker")

    if has_blocker and not contains_any(normalized, NEXT_ACTION_MARKERS):
        missing.append("exact next action for incomplete work")

    if missing:
        reason = "Before finishing, complete the team closeout summary: " + "; ".join(missing) + "."
        return emit_json(block(reason))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
