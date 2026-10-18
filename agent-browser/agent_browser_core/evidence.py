"""Durable command receipts. No automatic expiry, rotation, or deletion."""
from __future__ import annotations

import json
import sqlite3
import secrets
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import common

DEFAULT_QUOTA = 1024 ** 3
PRIVATE_ARGS = frozenset({"text", "js", "body", "json", "fields", "dialog_text", "headers", "token"})


def evidence_usage(directory: Path) -> int:
    """Count retained evidence without touching active journal receipts or browser storage."""
    total = 0
    for folder, names, files in os.walk(directory, followlinks=False):
        names[:] = [name for name in names if name != "user-data"]
        for name in files:
            try:
                total += (Path(folder) / name).stat().st_size
            except FileNotFoundError:
                pass
    return total


def require_space(directory: Path, quota: int, additional: int = 0) -> None:
    if evidence_usage(directory) + additional >= quota:
        raise common._tag(RuntimeError("Evidence quota reached; inspect journal/state and explicitly clear evidence before another mutation."), "journal_full")


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
        # Record argument names and structural targeting only. Values and script/body/file contents are omitted.
        safe = {k: (common.sanitize_text(v, 160) if k in {"target", "role", "name", "label", "operation", "method"} and isinstance(v, str) else "<omitted>")
                for k, v in args.items() if not k.startswith("_") and k not in PRIVATE_ARGS}
        with self.connect() as db:
            db.execute("INSERT INTO commands VALUES (?,?,?,?,NULL,'running',?,?,NULL)",
                       (command_id, session, verb, common._utc_now(), json.dumps(safe), common.redact_url(url)))

    def finish(self, command_id: str, status: str, outcome: dict[str, Any]) -> None:
        keys = {"effect", "observed", "error_class", "executed", "screenshots", "completed_fields", "pending", "handoff_id", "body_file", "expectations", "cleared_commands", "status", "write_outcome", "persisted"}
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

    def handoff(self, session: str, reason: str, condition: dict[str, Any]) -> str:
        handoff_id = secrets.token_hex(16)
        with self.connect() as db:
            db.execute("INSERT INTO handoffs VALUES (?,?,?,?, 'needs_human')",
                       (handoff_id, session, common.sanitize_text(reason, 240), json.dumps(condition)))
        return handoff_id

    def handoffs(self, session: str, handoff_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM handoffs WHERE session=? AND (? IS NULL OR id=?)", (session, handoff_id, handoff_id))]

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
