"""Durable command receipts. No automatic expiry, rotation, or deletion."""
from __future__ import annotations

import json
import sqlite3
import secrets
import os
import urllib.parse
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from . import common, policy

DEFAULT_QUOTA = 1024 ** 3
PRIVATE_ARGS = frozenset({"text", "js", "body", "json", "fields", "dialog_text", "headers", "token"})
INSPECTION_BUDGET = 4000  # leave room for the MCP text + structuredContent envelope
SETTLED = ("completed", "failed", "needs_human")  # a clear always removes these; unknown rows go by risk


def evidence_usage(directory: Path, skip: frozenset[str] = frozenset()) -> int:
    """Count retained evidence without touching active journal receipts or browser storage. `skip` names top-level folders to leave out."""
    total = 0
    for folder, names, files in os.walk(directory, followlinks=False):
        top = Path(folder) == directory
        names[:] = [name for name in names if name not in {"user-data", "downloads"} and not (top and name in skip)]
        for name in files:
            if name == "daemon.log":
                continue
            try:
                total += (Path(folder) / name).stat().st_size
            except FileNotFoundError:
                pass
    return total


def require_space(directory: Path, quota: int, additional: int = 0, workspace: Path | None = None) -> None:
    """The quota is profile-wide, but a clear reaches only its own session, so with `workspace` the error
    says how much of the profile's evidence belongs to that session and how much to other sessions."""
    total = evidence_usage(directory)
    if total + additional < quota:
        return
    message = "Evidence quota reached; inspect journal/state and explicitly clear evidence before another mutation."
    details: dict[str, Any] = {"evidence_bytes": total, "quota": quota}
    hint = "journal_full"
    if workspace is not None:
        # The receipts database is shared by every session; a clear shrinks it by this session's rows only.
        journal = 0
        for path in directory.glob("journal.sqlite3*"):  # with its transient rollback journal
            try:
                journal += path.stat().st_size
            except FileNotFoundError:
                pass
        if workspace == directory:  # the manual session's workspace is the profile folder itself
            own = max(evidence_usage(workspace, frozenset({"sessions"})) - journal, 0)
        else:
            own = evidence_usage(workspace)
        other = max(total - own - journal, 0)
        details.update(session_bytes=own, other_sessions_bytes=other, journal_bytes=journal)
        if other > own:
            message += f" Other sessions of this profile hold {other} of the {total} bytes, and this session's clear cannot remove them."
            hint = "journal_full_other"
    # The profile folder is named for its profile, so the hint names the profile in use.
    raise common._tag(RuntimeError(message), common.ERR_JOURNAL_FULL, hint=common._hint(hint, directory.name), details=details)


def read_receipt(directory: Path, session: str, command_id: str) -> dict[str, Any] | None:
    """Read a terminal receipt without initializing the journal or changing recovery state."""
    path = directory / "journal.sqlite3"
    if not path.exists():
        return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM commands WHERE session=? AND id=? AND status IN ('completed','failed','unknown','needs_human')",
                             (session, command_id)).fetchone()
        return dict(row) if row else None
    except sqlite3.Error as exc:
        raise common._tag(RuntimeError("Journal storage cannot be read."), "journal_unavailable") from exc


def _receipt_args(verb: str, args: dict[str, Any]) -> dict[str, Any]:
    """Argument names and structural targeting only. Values and script/body/file contents are omitted."""
    safe = {k: (common.sanitize_text(v, 160) if k in {"target", "role", "name", "label", "operation", "method"} and isinstance(v, str) else "<omitted>")
            for k, v in args.items() if not k.startswith("_") and k not in PRIVATE_ARGS}
    name = verb.rsplit(" ", 1)[-1]
    if name == "run":
        # The recipe and record number say which record a work note went to. The receipt is written before
        # h_run validates them, so only values that validation would accept are stored.
        if isinstance(args.get("recipe"), str) and args["recipe"] in common.RECIPES:
            safe["recipe"] = args["recipe"]
        if isinstance(args.get("number"), str) and common.RECORD_NUMBER_RE.fullmatch(args["number"]):
            safe["number"] = args["number"]
    if name == "api" and "url" in args:
        # The path names the endpoint and record. The query string can carry business data or secrets, so it is never stored.
        try:
            safe["path"] = common.sanitize_text(urllib.parse.urlsplit(args["url"]).path, 240) if isinstance(args["url"], str) else "<invalid>"
        except ValueError:
            safe["path"] = "<invalid>"
    return safe


def _restore_needs_human(db: sqlite3.Connection) -> None:
    """Older daemons left handoff receipts pending, and a restart then swept them to unknown although nothing ran."""
    rows = db.execute("SELECT id, outcome FROM commands WHERE status IN ('pending','unknown') AND outcome LIKE '%needs_human%'").fetchall()
    for row in rows:
        try:
            outcome = json.loads(row["outcome"])
        except ValueError:
            continue
        if isinstance(outcome, dict) and outcome.get("status") == "needs_human" and outcome.get("executed") is False:
            db.execute("UPDATE commands SET status='needs_human', outcome=? WHERE id=?", (json.dumps({**outcome, "pending": False}), row["id"]))


def _risky(verb: str, outcome: str | None) -> bool:
    """Whether an unknown receipt may record a write: a commit verb, upload, eval, a command that ran, or an
    interrupted non-read verb whose outcome was never written. The restart sweep also marks interrupted reads
    unknown; those have nothing to protect. A clear or handoff resolution changes only this journal, and
    repeating it is harmless, so its receipt is not kept either, even when that clear failed partway."""
    name = verb.rsplit(" ", 1)[-1]
    if name in {"journal", "state"}:
        return False
    if verb.startswith("commit ") or name in {"upload", "eval"}:
        return True
    try:
        parsed = json.loads(outcome) if outcome else None
    except ValueError:
        parsed = None
    executed = parsed.get("executed") if isinstance(parsed, dict) else None
    return executed is True or executed is None and name not in policy.READ


class Journal:
    def __init__(self, directory: Path, quota: int = DEFAULT_QUOTA):
        self.directory, self.quota = directory, quota
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "journal.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, verb TEXT NOT NULL,
                    started TEXT NOT NULL, finished TEXT, status TEXT NOT NULL,
                    arguments TEXT NOT NULL, url TEXT NOT NULL, outcome TEXT);
                CREATE TABLE IF NOT EXISTS handoffs (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, reason TEXT NOT NULL,
                    condition TEXT NOT NULL, status TEXT NOT NULL);
            """)
            _restore_needs_human(db)
            # A daemon restart cannot establish whether an interrupted write reached the server.
            db.execute("UPDATE commands SET status='unknown' WHERE status IN ('running','pending')")

    @contextmanager
    def connect(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=2)
            db.row_factory = sqlite3.Row
            with db:
                yield db
        except sqlite3.Error as exc:
            raise common._tag(RuntimeError("Journal storage is unavailable; no unrecorded mutation is permitted."), "journal_unavailable") from exc
        finally:
            if db is not None: db.close()

    def usage(self) -> int:
        return evidence_usage(self.directory)

    def require_space(self, additional: int = 0, workspace: Path | None = None) -> None:
        require_space(self.directory, self.quota, additional, workspace)

    def start(self, command_id: str, session: str, verb: str, args: dict[str, Any], url: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO commands VALUES (?,?,?,?,NULL,'running',?,?,NULL)",
                       (command_id, session, verb, common._utc_now(), json.dumps(_receipt_args(verb, args)), common.redact_url(url)))

    def finish(self, command_id: str, status: str, outcome: dict[str, Any]) -> None:
        keys = {"effect", "observed", "error_class", "executed", "screenshots", "completed_fields", "pending", "handoff_id", "body_file", "expectations",
                "cleared_commands", "retained_commands", "cleared_handoffs", "retained_handoffs", "files_not_removed",
                "status", "write_outcome", "persisted", "number"}
        safe = {k: outcome[k] for k in keys if k in outcome}
        with self.connect() as db:
            previous = db.execute("SELECT outcome FROM commands WHERE id=?", (command_id,)).fetchone()
            if previous and previous["outcome"]:
                safe = {**json.loads(previous["outcome"]), **safe}
            db.execute("UPDATE commands SET status=?, finished=?, outcome=? WHERE id=?",
                       (status, common._utc_now(), json.dumps(safe, default=str), command_id))

    def rows(self, session: str, command_id: str | None = None, offset: int = 0, limit: int = 25) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM commands WHERE session=? AND (? IS NULL OR id=?) ORDER BY rowid DESC LIMIT ? OFFSET ?",
                              (session, command_id, command_id, min(max(limit, 1), 100), max(offset, 0))).fetchall()
        return [dict(row) for row in rows]

    def page(self, session: str, command_id: str | None = None, offset: int = 0, budget: int = INSPECTION_BUDGET) -> dict[str, Any]:
        """Bound inspection in memory; existing receipts provide paging even at quota."""
        rows = self.rows(session, command_id, offset)
        commands, used = [], 0
        for row in rows:
            size = len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
            if size > budget:
                row = {"id": row["id"], "verb": common.sanitize_text(row["verb"], 160),
                       "started": row["started"], "status": row["status"], "truncated": True,
                       "output_id": "r" + row["id"], "receipt_pending": row["status"] in {"running", "pending"}}
                size = len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
            if commands and used + size > budget:
                break
            commands.append(row)
            used += size
        more = len(commands) < len(rows) or not command_id and len(rows) == 25
        return {"commands": commands, "next_offset": max(offset, 0) + len(commands) if more else None}

    def open_handoff(self, session: str, reason: str, condition: dict[str, Any]) -> tuple[str, bool]:
        """The open handoff with this reason and condition, or a new one. True when it was created."""
        reason = common.sanitize_text(reason, 240)
        with self.connect() as db:
            for row in db.execute("SELECT id, condition FROM handoffs WHERE session=? AND reason=? AND status='needs_human'", (session, reason)):
                if json.loads(row["condition"]) == condition:
                    return row["id"], False
            handoff_id = secrets.token_hex(16)
            db.execute("INSERT INTO handoffs VALUES (?,?,?,?, 'needs_human')", (handoff_id, session, reason, json.dumps(condition)))
        return handoff_id, True

    def handoffs(self, session: str, handoff_id: str | None = None) -> list[dict[str, Any]]:
        """Open handoffs first, newest first."""
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM handoffs WHERE session=? AND (? IS NULL OR id=?) ORDER BY status='needs_human' DESC, rowid DESC",
                                                     (session, handoff_id, handoff_id))]

    def resolve_handoff(self, session: str, handoff_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE handoffs SET status='resolved' WHERE id=? AND session=?", (handoff_id, session))

    def clear(self, session: str, keep_command_id: str = "", include_unknown: bool = False) -> tuple[dict[str, int], set[str]]:
        """Delete this session's settled receipts and resolved handoffs. Unknown receipts that may record a write
        and unresolved handoffs stay unless include_unknown, because they are what stops an uncertain write from
        being replayed. Returns the counts and the IDs of the receipts that stay, besides keep_command_id."""
        scope, params = "session=? AND id<>?", (session, keep_command_id)
        with self.connect() as db:
            unknown = db.execute(f"SELECT id, verb, outcome FROM commands WHERE {scope} AND status='unknown'", params).fetchall()
            doomed = [row["id"] for row in unknown if include_unknown or not _risky(row["verb"], row["outcome"])]
            cleared = db.execute(f"DELETE FROM commands WHERE {scope} AND status IN ({','.join('?' * len(SETTLED))})", (*params, *SETTLED)).rowcount
            db.executemany("DELETE FROM commands WHERE id=? AND session=?", [(command_id, session) for command_id in doomed])
            retained = {row["id"] for row in db.execute(f"SELECT id FROM commands WHERE {scope}", params)}
            handoffs = db.execute("DELETE FROM handoffs WHERE session=?" + ("" if include_unknown else " AND status='resolved'"), (session,)).rowcount
            kept_handoffs = db.execute("SELECT COUNT(*) FROM handoffs WHERE session=?", (session,)).fetchone()[0]
        # Explicit clearing must reclaim physical bytes so a full quota can recover.
        with self.connect() as db:
            db.execute("VACUUM")
        return {"cleared_commands": cleared + len(doomed), "retained_commands": len(retained),
                "cleared_handoffs": handoffs, "retained_handoffs": kept_handoffs}, retained
