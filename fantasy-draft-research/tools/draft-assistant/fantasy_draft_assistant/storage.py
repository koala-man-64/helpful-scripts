"""Durable append-only SQLite event storage."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator, Mapping, TypeVar

from .models import DraftEvent, DraftState, JsonValue, datetime_text, parse_datetime, utc_now


GENESIS_HASH = "0" * 64
TState = TypeVar("TState")


class EventStoreError(RuntimeError):
    """Base error for event persistence failures."""


class EventConflictError(EventStoreError):
    """An idempotency key was reused for a different event."""


class CorruptEventLogError(EventStoreError):
    """The event log sequence or hash chain is invalid."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def compute_event_hash(
    *,
    sequence: int,
    idempotency_key: str,
    timestamp: datetime,
    version: int,
    event_type: str,
    payload: Mapping[str, JsonValue],
    previous_event_hash: str,
) -> str:
    serialized = canonical_json(
        {
            "sequence": sequence,
            "idempotency_key": idempotency_key,
            "timestamp": datetime_text(timestamp),
            "version": version,
            "event_type": event_type,
            "payload": payload,
            "previous_event_hash": previous_event_hash,
        }
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class EventStore:
    """SQLite event log configured for RPO-0 acknowledgement semantics."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        if not self.path.is_file():
            raise EventStoreError(
                f"event store is not provisioned: {self.path}; run draft-assistant init"
            )
        self._validate_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        # WAL isolates readers and FULL sync makes a successful COMMIT durable.
        journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if str(journal_mode).casefold() != "wal":
            connection.close()
            raise EventStoreError("SQLite did not enable WAL journal mode")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _validate_schema(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(draft_events)")
            }
        except sqlite3.DatabaseError as exc:
            raise EventStoreError(f"cannot open event store {self.path}: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()
        required = {
            "sequence",
            "idempotency_key",
            "timestamp",
            "version",
            "event_type",
            "payload_json",
            "previous_event_hash",
            "event_hash",
        }
        if columns != required:
            raise EventStoreError(
                f"event store schema is missing or incompatible: {self.path}; rerun init in a new run directory"
            )

    def append(
        self,
        event_type: str,
        payload: Mapping[str, JsonValue],
        *,
        idempotency_key: str,
        version: int = 1,
        timestamp: datetime | None = None,
    ) -> DraftEvent:
        if not event_type.strip():
            raise ValueError("event_type must be non-empty")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be non-empty")
        if version <= 0:
            raise ValueError("version must be positive")
        payload_json = canonical_json(payload)
        event_timestamp = timestamp or utc_now()
        # Normalize and validate before beginning the write transaction.
        event_timestamp = parse_datetime(event_timestamp, "timestamp")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            # Validate the durable prefix while holding the write lock.  This
            # makes transition validation and persistence one atomic operation:
            # another writer cannot change the state between replay and insert.
            durable_events = tuple(
                self._row_to_event(row)
                for row in connection.execute("SELECT * FROM draft_events ORDER BY sequence")
            )
            verify_event_chain(durable_events)

            # Import locally so storage's hash-chain helpers remain usable
            # without introducing a module import cycle.
            from .reducer import replay as replay_events, reduce_event

            current_state = replay_events(durable_events, DraftState())
            existing = connection.execute(
                "SELECT * FROM draft_events WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                event = self._row_to_event(existing)
                if event.event_type != event_type or canonical_json(event.payload) != payload_json or event.version != version:
                    raise EventConflictError(f"idempotency key {idempotency_key!r} identifies a different event")
                connection.execute("COMMIT")
                return event

            last = connection.execute(
                "SELECT sequence, event_hash FROM draft_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = 1 if last is None else int(last["sequence"]) + 1
            previous_hash = GENESIS_HASH if last is None else str(last["event_hash"])
            canonical_payload = json.loads(payload_json)
            event_hash = compute_event_hash(
                sequence=sequence,
                idempotency_key=idempotency_key,
                timestamp=event_timestamp,
                version=version,
                event_type=event_type,
                payload=canonical_payload,
                previous_event_hash=previous_hash,
            )
            candidate = DraftEvent(
                sequence=sequence,
                idempotency_key=idempotency_key,
                timestamp=event_timestamp,
                version=version,
                event_type=event_type,
                payload=canonical_payload,
                previous_event_hash=previous_hash,
                event_hash=event_hash,
            )

            # This is deliberately before INSERT.  InvalidEventError (or any
            # future reducer validation error) rolls back an otherwise empty
            # transaction, so malformed commands never enter the hash chain.
            reduce_event(current_state, candidate)
            connection.execute(
                """
                INSERT INTO draft_events (
                    sequence, idempotency_key, timestamp, version, event_type,
                    payload_json, previous_event_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sequence,
                    idempotency_key,
                    datetime_text(event_timestamp),
                    version,
                    event_type,
                    payload_json,
                    previous_hash,
                    event_hash,
                ),
            )
            connection.execute("COMMIT")
            return candidate
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def events(self, *, to_sequence: int | None = None, verify: bool = True) -> tuple[DraftEvent, ...]:
        if to_sequence is not None and to_sequence <= 0:
            raise ValueError("to_sequence must be positive")
        query = "SELECT * FROM draft_events"
        parameters: tuple[int, ...] = ()
        if to_sequence is not None:
            query += " WHERE sequence <= ?"
            parameters = (to_sequence,)
        query += " ORDER BY sequence"
        connection = self._connect()
        try:
            result = tuple(self._row_to_event(row) for row in connection.execute(query, parameters))
        finally:
            connection.close()
        if verify:
            verify_event_chain(result)
        return result

    def iter_events(self, *, to_sequence: int | None = None, verify: bool = True) -> Iterator[DraftEvent]:
        yield from self.events(to_sequence=to_sequence, verify=verify)

    def replay(
        self,
        reducer: Callable[[TState, DraftEvent], TState],
        initial_state: TState,
        *,
        to_sequence: int | None = None,
    ) -> TState:
        state = initial_state
        for event in self.events(to_sequence=to_sequence, verify=True):
            state = reducer(state, event)
        return state

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> DraftEvent:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise CorruptEventLogError(f"event {row['sequence']} has invalid JSON") from error
        if not isinstance(payload, dict):
            raise CorruptEventLogError(f"event {row['sequence']} payload is not an object")
        return DraftEvent(
            sequence=int(row["sequence"]),
            idempotency_key=str(row["idempotency_key"]),
            timestamp=parse_datetime(str(row["timestamp"]), "timestamp"),
            version=int(row["version"]),
            event_type=str(row["event_type"]),
            payload=payload,
            previous_event_hash=str(row["previous_event_hash"]),
            event_hash=str(row["event_hash"]),
        )


def verify_event_chain(events: tuple[DraftEvent, ...] | list[DraftEvent]) -> None:
    previous_hash = GENESIS_HASH
    for expected_sequence, event in enumerate(events, start=1):
        if event.sequence != expected_sequence:
            raise CorruptEventLogError(
                f"event sequence gap: expected {expected_sequence}, found {event.sequence}"
            )
        if event.previous_event_hash != previous_hash:
            raise CorruptEventLogError(f"event {event.sequence} previous hash does not match")
        expected_hash = compute_event_hash(
            sequence=event.sequence,
            idempotency_key=event.idempotency_key,
            timestamp=event.timestamp,
            version=event.version,
            event_type=event.event_type,
            payload=event.payload,
            previous_event_hash=event.previous_event_hash,
        )
        if event.event_hash != expected_hash:
            raise CorruptEventLogError(f"event {event.sequence} hash does not match its contents")
        previous_hash = event.event_hash
