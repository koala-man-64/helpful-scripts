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

from . import common

DEFAULT_QUOTA = 1024 ** 3
PRIVATE_ARGS = frozenset({"text", "js", "body", "json", "fields", "dialog_text", "headers", "token"})
INSPECTION_BUDGET = 4000  # leave room for the MCP text + structuredContent envelope


def evidence_usage(directory: Path) -> int:
    """Count retained evidence without touching active journal receipts or browser storage."""
    total = 0
    for folder, names, files in os.walk(directory, followlinks=False):
        names[:] = [name for name in names if name not in {"user-data", "downloads"}]
        for name in files:
            if name == "daemon.log":
                continue
            try:
                total += (Path(folder) / name).stat().st_size
            except FileNotFoundError:
                pass
    return total


def require_space(directory: Path, quota: int, additional: int = 0) -> None:
    if evidence_usage(directory) + additional >= quota:
        raise common._tag(RuntimeError("Evidence quota reached; inspect journal/state and explicitly clear evidence before another mutation."), "journal_full")


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
        # The path names the endpoint and record. The query string can carry business data or secrets, so it is never
        # stored, and credentials in the path itself (;jsessionid=, a reset or JWT token segment) are masked.
        try:
            safe["path"] = common.sanitize_text(common.redact_path(urllib.parse.urlsplit(args["url"]).path), 240) if isinstance(args["url"], str) else "<invalid>"
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

    def require_space(self, additional: int = 0) -> None:
        require_space(self.directory, self.quota, additional)

    def start(self, command_id: str, session: str, verb: str, args: dict[str, Any], url: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO commands VALUES (?,?,?,?,NULL,'running',?,?,NULL)",
                       (command_id, session, verb, common._utc_now(), json.dumps(_receipt_args(verb, args)), common.redact_url(url)))

    def finish(self, command_id: str, status: str, outcome: dict[str, Any]) -> None:
        keys = {"effect", "observed", "error_class", "executed", "screenshots", "completed_fields", "pending", "handoff_id", "body_file", "expectations", "cleared_commands", "status", "write_outcome", "persisted", "number"}
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

    def clear(self, session: str, keep_command_id: str = "") -> int:
        with self.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM commands WHERE session=? AND id<>?", (session, keep_command_id)).fetchone()[0]
            db.execute("DELETE FROM commands WHERE session=? AND id<>?", (session, keep_command_id))
            db.execute("DELETE FROM handoffs WHERE session=?", (session,))
        # Explicit clearing must reclaim physical bytes so a full quota can recover.
        with self.connect() as db:
            db.execute("VACUUM")
        return count
