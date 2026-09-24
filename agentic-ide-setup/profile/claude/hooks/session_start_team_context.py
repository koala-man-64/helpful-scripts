from pathlib import Path

import wait_registry
from agent_ladder import is_managed, lane_summary
from hook_utils import (
    additional_context,
    branch_header,
    clear_session_flags,
    current_branch,
    dirty_summary,
    emit_json,
    prune_session_flags,
    read_hook_input,
    repo_name,
    repo_root,
    run_git,
    workflow_scope_enabled,
)
from task_notes import main_repo_name, task_note_lines

# Every line here is re-sent with every request for the rest of the session,
# so the wait list is capped to the newest entries; the poll command lists all.
MAX_WAITS_SHOWN = 5


def note_lines(root: Path) -> list[str]:
    """The per-branch task note, re-injected so it survives compaction.

    The note is a recovery aid: it is reconciled with current instructions and
    live evidence and never authorizes work. A note problem must never take
    down session start.
    """
    try:
        return task_note_lines(root)
    except Exception:
        return []


def outstanding_waits(repository: str = "") -> list[str]:
    """Waits from earlier sessions that have not reached a terminal status.

    Without this a wait survives in the registry but nothing tells the next
    session it exists, which is how Codex heartbeats die: they are attached to
    a thread and vanish with it.
    """
    try:
        rows = wait_registry.active()
    except Exception:
        return []
    if not rows:
        return []
    # Only this repository's waits are this session's business; the others are
    # counted so they stay discoverable without costing context everywhere.
    here = [row for row in rows if not repository or row.get("repository") == repository]
    elsewhere = len(rows) - len(here)
    if not here:
        return [f"{elsewhere} outstanding delivery wait(s) in other repositories; `wait_poll.py list` shows them."]
    # active() sorts ascending by created_at, so the newest are at the end.
    shown = here[-MAX_WAITS_SHOWN:]
    hidden = here[:-MAX_WAITS_SHOWN]
    lines = ["Outstanding delivery waits for this repository (from earlier sessions):"]
    for row in shown:
        expired = " - PAST TIMEOUT" if wait_registry.is_expired(row) else ""
        lines.append(f"- {row['wait_id']}: {wait_registry.describe(row)}{expired}")
    if hidden:
        past = sum(1 for row in hidden if wait_registry.is_expired(row))
        lines.append(
            f"- ... {len(hidden)} older wait(s) not listed ({past} past timeout)."
        )
    if elsewhere:
        lines.append(f"- {elsewhere} more in other repositories; `wait_poll.py list` shows them.")
    # Resolved from this file, not ~/.claude/hooks: the hooks run from wherever
    # settings.json points, and a fixed home path would name a stale copy.
    script = Path(__file__).resolve().parent / "wait_poll.py"
    lines.append(
        f'Poll with: py "{script}" poll --all. '
        "Registration is not delivery evidence; a wait is resolved only by a terminal status."
    )
    return lines


def main() -> int:
    payload = read_hook_input()
    session_id = str(payload.get("session_id") or "")
    source = str(payload.get("source") or "")
    # A fresh or compacted context has lost the router's once-per-session
    # standing text, so let the next prompt state it again. A resume keeps
    # its transcript, so its flags stay.
    if source in {"startup", "clear", "compact"}:
        clear_session_flags(session_id)
    prune_session_flags()
    root = repo_root()
    waits = outstanding_waits(main_repo_name(root) if current_branch(root) != "not-git" else "")
    notes = note_lines(root)
    if not workflow_scope_enabled(root):
        # Team routing is repository-scoped, but an outstanding wait is not:
        # it belongs to whatever operation was launched, wherever that was.
        # The task note is per checkout and applies in any git repository.
        extra = notes + waits
        if extra:
            return emit_json(additional_context("SessionStart", "\n".join(extra)))
        return emit_json(None)
    header = branch_header(root) or current_branch(root)

    context = "\n".join(
        [
            "Team workflow context:",
            f"- Repo: {repo_name(root)}",
            f"- Root: {root}",
            f"- Branch: {header}",
            f"- Working tree: {dirty_summary(root)}",
            "- Follow CLAUDE.md and prefer repo-local .claude/agents and .claude/skills definitions.",
            "- Choose the smallest sufficient lane. Lite and standard work need no orchestrator; the owner investigates, implements, validates, and delivers.",
            "- Record Azure DevOps tracking only when the work names Azure Boards, spans repositories, or touches CI/CD or deployment; a commit or PR alone does not require it.",
            "- Work on a task-owned branch and finish through commit, push, and PR rather than pushing to protected branches.",
            "- Classify changes as local-only or contracts-repo-first before editing shared API, schema, serialization, or mirrored contract shapes.",
        ]
        # The lane gate only fires in managed repositories, so only describe
        # it where it actually applies.
        + ([lane_summary()] if is_managed(root, run_git) else [])
        + notes
        + waits
    )
    return emit_json(additional_context("SessionStart", context))


if __name__ == "__main__":
    raise SystemExit(main())
