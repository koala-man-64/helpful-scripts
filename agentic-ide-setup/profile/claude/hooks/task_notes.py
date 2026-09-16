"""Per-branch task notes that survive compaction.

Compaction drops conversation detail such as work item ids, PR urls,
validation status, and mid-session decisions. When durable state materially
helps, the model keeps it in a small note on disk, keyed by repository and
branch, and the SessionStart hook re-injects the note on startup, resume,
clear and compact. The note is a recovery aid, not authority.

Notes live outside the repository so they never appear in git status and
never need a commit.
"""

from __future__ import annotations

import re
from pathlib import Path

from hook_utils import current_branch, run_git

NOTES_DIR = Path.home() / ".claude" / "task-notes"
MAX_CHARS = 6000
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

NOTE_GUIDANCE = (
    "Use a note only when durable state materially helps: objective, "
    "decisions, ownership, evidence locations, blockers, next action. Notes "
    "and summaries are recovery aids; reconcile them with current "
    "instructions and live evidence. Neither authorizes new work or "
    "overrides a newer decision."
)


def _slug(value: str) -> str:
    return _UNSAFE.sub("-", value).strip("-.") or "unnamed"


def note_key(repo: str, branch: str) -> str:
    return f"{_slug(repo)}--{_slug(branch)}"


def note_path(repo: str, branch: str, notes_dir: Path = NOTES_DIR) -> Path:
    return notes_dir / f"{note_key(repo, branch)}.md"


def read_note(path: Path, limit: int = MAX_CHARS) -> str | None:
    """The note text, or None when there is no usable note."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text:
        return None
    if len(text) > limit:
        text = text[:limit].rstrip() + f"\n[note truncated at {limit} chars; trim it]"
    return text


def main_repo_name(root: Path) -> str:
    """Name of the primary checkout, so a linked worktree shares its note.

    Claude Code worktrees live under <repo>/.claude/worktrees/<name>. Their
    toplevel is the worktree, but the common git dir is the primary one.
    Submodules report <super>/.git/modules/<sub>, which is not a checkout
    name, so anything that is not a plain .git falls back to the toplevel.
    """
    code, common = run_git(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"], root
    )
    if code == 0 and common:
        common_path = Path(common)
        if common_path.name == ".git" and common_path.parent.name:
            return common_path.parent.name
    return root.name


def context_lines(repo: str, branch: str, notes_dir: Path = NOTES_DIR) -> list[str]:
    path = note_path(repo, branch, notes_dir)
    note = read_note(path)
    if note is None:
        return [f"Task note: {path} (none yet). {NOTE_GUIDANCE}"]
    return [f"Task note ({path}):", note]


def task_note_lines(root: Path) -> list[str]:
    """Lines for SessionStart context; empty outside a git checkout."""
    branch = current_branch(root)
    if branch == "not-git":
        return []
    return context_lines(main_repo_name(root), branch)
