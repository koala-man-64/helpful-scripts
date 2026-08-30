"""Authoritative local SQLite provisioning used only by ``draft-assistant init``."""

from __future__ import annotations

from pathlib import Path
import sqlite3


def provision_event_store(path: Path) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite existing event store: {path}")
    connection = sqlite3.connect(path, isolation_level=None, timeout=30.0)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute(
            """
            CREATE TABLE draft_events (
                sequence INTEGER PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                timestamp TEXT NOT NULL,
                version INTEGER NOT NULL CHECK (version > 0),
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_event_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE
            )
            """
        )
    finally:
        connection.close()
