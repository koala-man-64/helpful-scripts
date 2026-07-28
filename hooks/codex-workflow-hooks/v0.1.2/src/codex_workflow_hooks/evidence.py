"""Repository-scoped, privacy-minimized SQLite evidence ledger."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .models import DeliveryState, EventEnvelope, RepoContext
from .utils import parse_exit_code, run_git, sha256_text, stable_json


SCHEMA_VERSION = 2
_EPHEMERAL_STATES = {
    DeliveryState.SOURCE_MODIFIED.value,
    DeliveryState.VALIDATED.value,
}


class EvidenceLedger:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "evidence.sqlite3"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=0.25)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=250")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS repository_sessions (
                    scope_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    repository_id TEXT NOT NULL DEFAULT '',
                    origin_hash TEXT NOT NULL DEFAULT '',
                    common_dir_hash TEXT NOT NULL DEFAULT '',
                    branch TEXT NOT NULL DEFAULT '',
                    baseline_head TEXT NOT NULL DEFAULT '',
                    baseline_status_hash TEXT NOT NULL DEFAULT '',
                    mutation_count INTEGER NOT NULL DEFAULT 0,
                    validation_count INTEGER NOT NULL DEFAULT 0,
                    provider_write_count INTEGER NOT NULL DEFAULT 0,
                    provider_readback_count INTEGER NOT NULL DEFAULT 0,
                    continuation_issued INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS repository_sessions_by_host
                    ON repository_sessions(session_id);
                CREATE TABLE IF NOT EXISTS repository_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    recorded_at TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    repository_id TEXT NOT NULL DEFAULT '',
                    action_type TEXT NOT NULL,
                    success INTEGER,
                    reason_code TEXT NOT NULL DEFAULT '',
                    command_hash TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(scope_id) REFERENCES repository_sessions(scope_id)
                );
                CREATE INDEX IF NOT EXISTS repository_events_by_scope
                    ON repository_events(scope_id);
                CREATE TABLE IF NOT EXISTS repository_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    evidence_key TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    digest TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(scope_id, state, evidence_key),
                    FOREIGN KEY(scope_id) REFERENCES repository_sessions(scope_id)
                );
                CREATE INDEX IF NOT EXISTS repository_artifacts_by_scope
                    ON repository_artifacts(scope_id, state);
                CREATE TABLE IF NOT EXISTS registrations (
                    repository_id TEXT NOT NULL,
                    origin_hash TEXT NOT NULL,
                    common_dir_hash TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    rollout_mode TEXT NOT NULL,
                    PRIMARY KEY(repository_id, common_dir_hash)
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _now()),
            )

    def scope_id(self, session_id: str, context: RepoContext) -> str:
        """Return a stable host-session plus repository identity key."""
        common_dir = str(context.git_common_dir) if context.git_common_dir else ""
        repo_root = str(context.repo_root) if context.repo_root else ""
        identity = {
            "session_id": session_id,
            "repository_id": context.repository_id,
            "origin_hash": sha256_text(context.origin) if context.origin else "",
            "common_dir_hash": sha256_text(common_dir) if common_dir else "",
            "repo_root_hash": sha256_text(repo_root) if repo_root else "",
            "global": context.repo_root is None,
        }
        return sha256_text(stable_json(identity))

    def start_session(self, event: EventEnvelope, context: RepoContext) -> str:
        return self._ensure_scope(event.session_id, context)

    def _ensure_scope(self, session_id: str, context: RepoContext) -> str:
        scope_id = self.scope_id(session_id, context)
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM repository_sessions WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
        if exists is not None:
            return scope_id
        status_hash = ""
        if context.repo_root:
            _, status, _ = run_git(context.repo_root, "status", "--porcelain=v1", timeout=4.0)
            status_hash = sha256_text(status)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO repository_sessions(
                    scope_id, session_id, started_at, repository_id, origin_hash,
                    common_dir_hash, branch, baseline_head, baseline_status_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope_id,
                    session_id,
                    _now(),
                    context.repository_id,
                    sha256_text(context.origin) if context.origin else "",
                    sha256_text(str(context.git_common_dir)) if context.git_common_dir else "",
                    context.branch,
                    context.head,
                    status_hash,
                ),
            )
        return scope_id

    def record_event(
        self,
        event: EventEnvelope,
        context: RepoContext,
        *,
        action_type: str,
        reason_code: str = "",
    ) -> bool:
        scope_id = self.start_session(event, context)
        exit_code = parse_exit_code(event.tool_response)
        success: int | None = None if exit_code is None else int(exit_code == 0)
        command = event.command()
        command_hash = sha256_text(command) if command else ""
        identity = event.tool_use_id or sha256_text(
            stable_json(
                {
                    "scope": scope_id,
                    "turn": event.turn_id,
                    "event": event.hook_event_name,
                    "tool": event.tool_name,
                    "command_hash": command_hash,
                }
            )
        )
        key = f"{scope_id}:{event.hook_event_name}:{identity}:{action_type}:{reason_code}"
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO repository_events(
                    idempotency_key, recorded_at, scope_id, event_name, repository_id,
                    action_type, success, reason_code, command_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    _now(),
                    scope_id,
                    event.hook_event_name,
                    context.repository_id,
                    action_type,
                    success,
                    reason_code,
                    command_hash,
                ),
            )
            inserted = cursor.rowcount == 1
            if inserted and event.hook_event_name == "PostToolUse":
                # Mutations and provider writes may partially apply even when a tool
                # reports failure or an unknown result, so they always create a gate.
                mutation = int(action_type == "mutation")
                provider_write = int(action_type == "provider_write")
                validation = int(action_type == "validation" and success == 1)
                provider_readback = int(action_type == "provider_readback" and success == 1)
                connection.execute(
                    """
                    UPDATE repository_sessions
                    SET mutation_count = mutation_count + ?,
                        validation_count = validation_count + ?,
                        provider_write_count = provider_write_count + ?,
                        provider_readback_count = provider_readback_count + ?
                    WHERE scope_id = ?
                    """,
                    (
                        mutation,
                        validation,
                        provider_write,
                        provider_readback,
                        scope_id,
                    ),
                )
            return inserted

    def mark_state(
        self,
        session_id: str,
        context: RepoContext,
        state: DeliveryState,
        *,
        evidence_key: str,
        source: str,
        digest: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        scope_id = self._ensure_scope(session_id, context)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO repository_artifacts(
                    scope_id, state, evidence_key, recorded_at, source, digest, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope_id,
                    state.value,
                    evidence_key,
                    _now(),
                    source,
                    digest,
                    stable_json(metadata or {}),
                ),
            )

    def record_registration(
        self,
        repository_id: str,
        origin: str,
        common_dir: Path,
        rollout_mode: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO registrations(
                    repository_id, origin_hash, common_dir_hash, registered_at, rollout_mode
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    repository_id,
                    sha256_text(origin),
                    sha256_text(str(common_dir)),
                    _now(),
                    rollout_mode,
                ),
            )

    def summary(self, session_id: str, context: RepoContext) -> dict[str, Any]:
        scope_id = self.scope_id(session_id, context)
        return self._summary_for_scope(scope_id)

    def summaries(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            scopes = connection.execute(
                """
                SELECT scope_id
                FROM repository_sessions
                WHERE session_id = ?
                ORDER BY started_at, scope_id
                """,
                (session_id,),
            ).fetchall()
        return [self._summary_for_scope(str(row["scope_id"])) for row in scopes]

    def _summary_for_scope(self, scope_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            session = connection.execute(
                "SELECT * FROM repository_sessions WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
            states = connection.execute(
                """
                SELECT DISTINCT state
                FROM repository_artifacts
                WHERE scope_id = ?
                ORDER BY state
                """,
                (scope_id,),
            ).fetchall()
        if session is None:
            return {}
        result = dict(session)
        result["states"] = [row["state"] for row in states]
        return result

    def evidence_keys(
        self,
        session_id: str,
        context: RepoContext,
        state: DeliveryState,
    ) -> set[str]:
        scope_id = self.scope_id(session_id, context)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT evidence_key
                FROM repository_artifacts
                WHERE scope_id = ? AND state = ?
                """,
                (scope_id, state.value),
            ).fetchall()
        return {str(row["evidence_key"]) for row in rows}

    def latest_artifact(
        self,
        session_id: str,
        context: RepoContext,
        state: DeliveryState,
    ) -> dict[str, Any] | None:
        scope_id = self.scope_id(session_id, context)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT evidence_key, recorded_at, source, digest, metadata_json
                FROM repository_artifacts
                WHERE scope_id = ? AND state = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (scope_id, state.value),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["metadata"] = _metadata(result.pop("metadata_json", "{}"))
        return result

    def unmatched_provider_writes(
        self,
        session_id: str,
        context: RepoContext,
    ) -> set[str]:
        scope_id = self.scope_id(session_id, context)
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT action_type, reason_code, success
                FROM repository_events
                WHERE scope_id = ?
                  AND action_type IN ('provider_write', 'provider_readback')
                """,
                (scope_id,),
            ).fetchall()
        writes = {
            str(row["reason_code"])
            for row in rows
            if row["action_type"] == "provider_write" and row["reason_code"]
        }
        readbacks = {
            str(row["reason_code"])
            for row in rows
            if row["action_type"] == "provider_readback"
            and row["success"] == 1
            and row["reason_code"]
        }
        return writes.difference(readbacks)

    def issue_continuation_once(self, session_id: str, context: RepoContext) -> bool:
        scope_id = self.scope_id(session_id, context)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE repository_sessions SET continuation_issued = 1
                WHERE scope_id = ? AND continuation_issued = 0
                """,
                (scope_id,),
            )
            return cursor.rowcount == 1

    def end_session(self, session_id: str, context: RepoContext) -> None:
        scope_id = self.scope_id(session_id, context)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE repository_sessions
                SET ended_at = COALESCE(ended_at, ?)
                WHERE scope_id = ?
                """,
                (_now(), scope_id),
            )

    def cleanup(self, retain_days: int) -> dict[str, int]:
        """Delete only expired, ended scopes without durable delivery records."""
        modifier = f"-{max(1, retain_days)} days"
        durable_states = sorted(
            state.value for state in DeliveryState if state.value not in _EPHEMERAL_STATES
        )
        placeholders = ", ".join("?" for _ in durable_states)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT session.scope_id
                FROM repository_sessions AS session
                WHERE session.ended_at IS NOT NULL
                  AND julianday(session.ended_at) < julianday('now', ?)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM repository_artifacts AS artifact
                      WHERE artifact.scope_id = session.scope_id
                        AND artifact.state IN ({placeholders})
                  )
                """,
                (modifier, *durable_states),
            ).fetchall()
            scopes = [str(row["scope_id"]) for row in rows]
            if not scopes:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                return {"artifacts": 0, "events": 0, "sessions": 0}
            scope_placeholders = ", ".join("?" for _ in scopes)
            events = connection.execute(
                f"DELETE FROM repository_events WHERE scope_id IN ({scope_placeholders})",
                scopes,
            ).rowcount
            artifacts = connection.execute(
                f"DELETE FROM repository_artifacts WHERE scope_id IN ({scope_placeholders})",
                scopes,
            ).rowcount
            sessions = connection.execute(
                f"DELETE FROM repository_sessions WHERE scope_id IN ({scope_placeholders})",
                scopes,
            ).rowcount
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return {"artifacts": artifacts, "events": events, "sessions": sessions}


def _metadata(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _now() -> str:
    return datetime.now(UTC).isoformat()
