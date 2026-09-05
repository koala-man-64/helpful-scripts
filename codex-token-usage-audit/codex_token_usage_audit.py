#!/usr/bin/env python3
"""Audit locally retained Codex token usage by turn, model, and subagent tree.

The script is intentionally read-only and dependency-free. It parses cumulative
``token_count`` snapshots from Codex rollout JSONL files, then calculates one
delta per user turn. Optional ``state_5.sqlite`` reads enrich the report with
task titles and parent/child relationships; the database is opened in read-only
mode and is never required for token accounting.

No prompt, response, reasoning, tool argument, or tool-result content is copied
into the normalized records. Prompt-derived task titles and raw local paths are
omitted unless ``--include-titles`` or ``--include-paths`` is explicitly supplied.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, TextIO
from urllib.parse import urlparse


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)

# Standard Codex credit rates per 1M tokens, verified 2026-08-23.
# Values are (fresh input, cached input, output). Reasoning is part of output.
CREDIT_RATES: dict[str, tuple[float, float, float]] = {
    "gpt-5.6-sol": (100.0, 10.0, 500.0),
    "gpt-5.6-terra": (50.0, 5.0, 300.0),
    "gpt-5.6-luna": (5.0, 0.5, 30.0),
}

# Pin the external interchange contract. The local credit rates above are
# credits, not USD API pricing, so they are deliberately never exported as
# ``estimated_cost_usd`` observations.
USAGE_OBSERVATIONS_SCHEMA_SHA256 = (
    "2c8791ab436bf6142743a4976bd901e6f3d0f82b97331ef3987356f5fc0186d7"
)
USAGE_OBSERVATIONS_SCHEMA_VERSION = 1

ROUTING_POLICY_VERSION = "builtin-v1"
KNOWN_ROUTING_MODELS = {
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.3-codex-spark",
}
KNOWN_ROUTING_EFFORTS = {"low", "medium", "high", "xhigh", "max", "ultra"}
CANONICAL_ROOT_ROUTES = {
    ("gpt-5.6-luna", "low"),
    ("gpt-5.6-terra", "medium"),
    ("gpt-5.6-sol", "high"),
}
CANONICAL_SUBAGENT_ROUTES = {
    ("gpt-5.6-luna", "low"),
    ("gpt-5.6-terra", "medium"),
}
ROUTING_STATUSES = (
    "canonical",
    "explicit_exception",
    "noncanonical",
    "unknown",
)

DEFAULT_DIMENSIONS = (
    "thread",
    "model_effort",
    "agent",
    "task",
    "turn",
    "day",
)
ALL_DIMENSIONS = (
    "thread",
    "model",
    "effort",
    "model_effort",
    "model_thread",
    "agent",
    "task",
    "session",
    "turn",
    "project",
    "day",
    "client",
    "version",
)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Usage":
        parsed: dict[str, int] = {}
        for name in TOKEN_FIELDS:
            raw = value.get(name, 0)
            try:
                parsed[name] = max(0, int(raw or 0))
            except (TypeError, ValueError):
                parsed[name] = 0
        if not parsed["total_tokens"]:
            parsed["total_tokens"] = parsed["input_tokens"] + parsed["output_tokens"]
        return cls(**parsed)

    @property
    def fresh_input_tokens(self) -> int:
        return max(0, self.input_tokens - self.cached_input_tokens)

    @property
    def visible_output_tokens(self) -> int:
        return max(0, self.output_tokens - self.reasoning_output_tokens)

    def delta_from(self, previous: "Usage") -> tuple["Usage", bool]:
        current = asdict(self)
        prior = asdict(previous)
        reset = any(current[name] < prior[name] for name in TOKEN_FIELDS)
        if reset:
            return self, True
        delta = {name: current[name] - prior[name] for name in TOKEN_FIELDS}
        expected_total = delta["input_tokens"] + delta["output_tokens"]
        if delta["total_tokens"] != expected_total:
            delta["total_tokens"] = expected_total
        return Usage(**delta), False

    def plus(self, other: "Usage") -> "Usage":
        return Usage(
            **{
                name: getattr(self, name) + getattr(other, name)
                for name in TOKEN_FIELDS
            }
        )


@dataclass
class ThreadMetadata:
    thread_id: str
    rollout_path: str = ""
    title: str = ""
    model: str = ""
    effort: str = ""
    agent_path: str = ""
    agent_nickname: str = ""
    agent_role: str = ""
    thread_source: str = ""
    cwd: str = ""
    project_id: str = ""
    tokens_used: int = 0


@dataclass
class UsageIncrement:
    timestamp: str
    usage: Usage
    model: str = ""
    effort: str = ""


@dataclass
class UsageSnapshot:
    """One validated provider cumulative snapshot and its optional request usage.

    This is retained alongside accounting increments so the observation export
    can expose source evidence without reparsing rollout JSONL.
    """

    timestamp: str
    source_line: int
    cumulative: Usage | None
    last_request: Usage | None
    cumulative_known: frozenset[str]
    last_request_known: frozenset[str]
    segment_index: int
    baseline_complete: bool
    reset: bool
    model: str = ""
    effort: str = ""
    request_identifier: str = ""
    provider_identifier: str = ""


@dataclass
class TurnSnapshot:
    turn_id: str
    index: int
    timestamp: str = ""
    last_usage_timestamp: str = ""
    model: str = ""
    effort: str = ""
    increments: list[UsageIncrement] = field(default_factory=list)
    snapshots: list[UsageSnapshot] = field(default_factory=list)

    @property
    def usage(self) -> Usage:
        result = Usage()
        for increment in self.increments:
            result = result.plus(increment.usage)
        return result

    @property
    def model_calls(self) -> int:
        return len(self.increments)


@dataclass
class ParsedSession:
    session_id: str
    source_file: str
    archived: bool
    session_timestamp: str = ""
    cwd: str = ""
    originator: str = ""
    cli_version: str = ""
    client_source: str = ""
    thread_source: str = ""
    parent_thread_id: str = ""
    depth: int | None = None
    agent_path: str = ""
    agent_nickname: str = ""
    agent_role: str = ""
    repository_url: str = ""
    turns: list[TurnSnapshot] = field(default_factory=list)

    @property
    def final_usage(self) -> Usage:
        result = Usage()
        for turn in self.turns:
            result = result.plus(turn.usage)
        return result


@dataclass
class TurnRecord:
    timestamp: str
    day: str
    root_thread_id: str
    root_title: str
    thread_id: str
    parent_thread_id: str
    depth: int
    thread_type: str
    agent_path: str
    agent_nickname: str
    agent_role: str
    turn_id: str
    turn_index: int
    model_calls: int
    model: str
    reasoning_effort: str
    client_source: str
    originator: str
    cli_version: str
    project: str
    cwd: str
    archived: bool
    input_tokens: int
    cached_input_tokens: int
    fresh_input_tokens: int
    cache_write_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    visible_output_tokens: int
    total_tokens: int
    estimated_credits: float | None
    source_file: str
    credits_enabled: bool = True


@dataclass(frozen=True)
class RouteAssessment:
    status: str
    reason: str


@dataclass
class ScanStats:
    rollout_files_discovered: int = 0
    rollout_files_parsed: int = 0
    rollout_files_skipped: int = 0
    duplicate_sessions: int = 0
    copied_turns_deduplicated: int = 0
    state_threads: int = 0
    state_edges: int = 0


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def load_state_metadata(
    database_path: Path,
    *,
    include_titles: bool = True,
    warn_if_missing: bool = False,
) -> tuple[dict[str, ThreadMetadata], dict[str, str], list[str]]:
    """Read optional task metadata and spawn edges without mutating SQLite."""

    warnings: list[str] = []
    metadata: dict[str, ThreadMetadata] = {}
    parent_by_child: dict[str, str] = {}
    if not database_path.is_file():
        if warn_if_missing:
            warnings.append(f"explicit state database does not exist: {database_path}")
        return metadata, parent_by_child, warnings

    try:
        uri = database_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
    except (OSError, sqlite3.Error) as exc:
        warnings.append(f"state database unavailable: {database_path}: {exc}")
        return metadata, parent_by_child, warnings

    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "threads" in tables:
            available = _table_columns(connection, "threads")
            wanted = [
                "id",
                "rollout_path",
                "model",
                "reasoning_effort",
                "agent_path",
                "agent_nickname",
                "agent_role",
                "thread_source",
                "cwd",
                "project_id",
                "tokens_used",
            ]
            if include_titles:
                wanted.insert(2, "title")
            selected = [name for name in wanted if name in available]
            if "id" in selected:
                query = "SELECT " + ", ".join(selected) + " FROM threads"
                for row in connection.execute(query):
                    values = dict(row)
                    thread_id = str(values.get("id") or "")
                    if not thread_id:
                        continue
                    metadata[thread_id] = ThreadMetadata(
                        thread_id=thread_id,
                        rollout_path=str(values.get("rollout_path") or ""),
                        title=str(values.get("title") or ""),
                        model=str(values.get("model") or ""),
                        effort=str(values.get("reasoning_effort") or ""),
                        agent_path=str(values.get("agent_path") or ""),
                        agent_nickname=str(values.get("agent_nickname") or ""),
                        agent_role=str(values.get("agent_role") or ""),
                        thread_source=str(values.get("thread_source") or ""),
                        cwd=str(values.get("cwd") or ""),
                        project_id=str(values.get("project_id") or ""),
                        tokens_used=_safe_int(values.get("tokens_used")),
                    )
        if "thread_spawn_edges" in tables:
            available = _table_columns(connection, "thread_spawn_edges")
            if {"parent_thread_id", "child_thread_id"}.issubset(available):
                query = "SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges"
                for parent, child in connection.execute(query):
                    if parent and child:
                        parent_by_child[str(child)] = str(parent)
    except sqlite3.Error as exc:
        warnings.append(f"state database schema could not be read: {database_path}: {exc}")
    finally:
        connection.close()
    return metadata, parent_by_child, warnings


def _canonical_path_key(path: Path) -> str:
    try:
        text = str(path.expanduser().resolve(strict=False))
    except (OSError, RuntimeError):
        text = os.path.abspath(str(path))
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.normpath(text))


def _is_archived_rollout(path: Path) -> bool:
    return "archived_sessions" in {part.lower() for part in path.parts}


def _same_existing_file(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and left.samefile(right)
    except OSError:
        return False


def validate_output_destinations(
    parser: argparse.ArgumentParser,
    csv_path: str | None,
    json_path: str | None,
    database_path: Path,
    rollout_paths: Sequence[Path],
    observations_path: str | None = None,
) -> None:
    """Reject destinations that could overwrite another output or Codex input."""

    outputs = {
        name: Path(value).expanduser()
        for name, value in (
            ("csv", csv_path),
            ("json", json_path),
            ("observations", observations_path),
        )
        if value and value != "-"
    }
    output_items = list(outputs.items())
    for index, (first_name, first) in enumerate(output_items):
        for second_name, second in output_items[index + 1 :]:
            if (
                _canonical_path_key(first) == _canonical_path_key(second)
                or _same_existing_file(first, second)
            ):
                parser.error(
                    f"--{first_name} and --{second_name} must use different destination paths"
                )

    protected = [(database_path, "state database")]
    protected.extend((path, "rollout input") for path in rollout_paths)
    for name, output_path in output_items:
        output_key = _canonical_path_key(output_path)
        for protected_path, source in protected:
            if (
                output_key == _canonical_path_key(protected_path)
                or _same_existing_file(output_path, protected_path)
            ):
                parser.error(
                    f"--{name} destination collides with Codex {source}: {output_path}"
                )


def discover_rollouts(
    codex_home: Path,
    include_archived: bool,
    state_metadata: Mapping[str, ThreadMetadata],
) -> list[Path]:
    paths: dict[str, Path] = {}

    def add(path: Path) -> None:
        paths.setdefault(_canonical_path_key(path), path)

    active_root = codex_home / "sessions"
    if active_root.is_dir():
        for path in active_root.rglob("rollout-*.jsonl"):
            if path.is_file():
                add(path)

    if include_archived:
        archive_root = codex_home / "archived_sessions"
        if archive_root.is_dir():
            for path in archive_root.rglob("rollout-*.jsonl"):
                if path.is_file():
                    add(path)

    for item in state_metadata.values():
        if not item.rollout_path:
            continue
        path = Path(item.rollout_path).expanduser()
        is_archived = "archived_sessions" in {part.lower() for part in path.parts}
        if not is_archived or include_archived:
            add(path)

    return sorted(paths.values(), key=lambda item: str(item).lower())


def restrict_rollouts_from_state(
    paths: Sequence[Path],
    session_prefix: str | None,
    root_prefix: str | None,
    state_metadata: Mapping[str, ThreadMetadata],
    database_parents: Mapping[str, str],
) -> list[Path]:
    """Resolve ID filters with a metadata-only pass before parsing token data."""

    if not session_prefix and not root_prefix:
        return list(paths)

    thread_by_path: dict[str, str] = {
        _canonical_path_key(Path(item.rollout_path)): thread_id
        for thread_id, item in state_metadata.items()
        if item.rollout_path
    }
    parent_by_child = dict(database_parents)
    for path in paths:
        key = _canonical_path_key(path)
        thread_id = thread_by_path.get(key, _session_id_from_filename(path))
        rollout_parent = ""
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, Mapping) or entry.get("type") != "session_meta":
                        continue
                    payload = entry.get("payload")
                    if isinstance(payload, Mapping):
                        thread_id = str(payload.get("id") or thread_id)
                        spawn = _subagent_spawn(payload.get("source"))
                        rollout_parent = str(
                            payload.get("parent_thread_id")
                            or spawn.get("parent_thread_id")
                            or ""
                        )
                    break
        except OSError:
            pass
        thread_by_path[key] = thread_id
        if rollout_parent:
            parent_by_child.setdefault(thread_id, rollout_parent)

    allowed_ids = set(thread_by_path.values())
    if session_prefix:
        allowed_ids = {
            thread_id for thread_id in allowed_ids if thread_id.startswith(session_prefix)
        }
    if root_prefix:
        allowed_ids = {
            thread_id
            for thread_id in allowed_ids
            if _root_thread_id(thread_id, parent_by_child).startswith(root_prefix)
        }
    return [
        path
        for path in paths
        if thread_by_path.get(_canonical_path_key(path), _session_id_from_filename(path))
        in allowed_ids
    ]


def _subagent_spawn(source: Any) -> Mapping[str, Any]:
    if not isinstance(source, Mapping):
        return {}
    subagent = source.get("subagent")
    if not isinstance(subagent, Mapping):
        return {}
    spawn = subagent.get("thread_spawn")
    return spawn if isinstance(spawn, Mapping) else {}


def _session_id_from_filename(path: Path) -> str:
    name = path.stem
    candidate = name.rsplit("-", 5)
    if len(candidate) == 6:
        return "-".join(candidate[-5:])
    return name


def _usage_vector(value: Any) -> Usage | None:
    if not isinstance(value, Mapping):
        return None
    parsed: dict[str, int] = {}
    for name in TOKEN_FIELDS:
        raw = value.get(name, 0)
        if raw is None or isinstance(raw, bool) or not isinstance(raw, int):
            return None
        parsed[name] = raw
        if parsed[name] < 0:
            return None
    if not parsed["total_tokens"]:
        parsed["total_tokens"] = parsed["input_tokens"] + parsed["output_tokens"]
    usage = Usage(**parsed)
    if usage.total_tokens != usage.input_tokens + usage.output_tokens:
        return None
    if usage.cached_input_tokens > usage.input_tokens:
        return None
    if usage.reasoning_output_tokens > usage.output_tokens:
        return None
    if usage.cache_write_input_tokens > usage.input_tokens:
        return None
    return usage


def _known_usage_fields(value: Any) -> frozenset[str]:
    if not isinstance(value, Mapping):
        return frozenset()
    return frozenset(
        name
        for name in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
            "cache_write_input_tokens",
        )
        if name in value and type(value[name]) is int
    )


def _componentwise_at_most(left: Usage, right: Usage) -> bool:
    return all(getattr(left, name) <= getattr(right, name) for name in TOKEN_FIELDS)


def _usage_increment(
    current: Usage,
    last: Usage | None,
    previous: Usage | None,
) -> tuple[Usage, str | None]:
    """Return the new usage represented by one cumulative snapshot.

    Codex can repeat cumulative events, reset a counter to zero, or seed a
    subagent rollout with an inherited parent baseline. The first and rebased
    events therefore use a validated ``last_token_usage`` vector; monotone
    events use exact cumulative differences.
    """

    valid_last = last is not None and _componentwise_at_most(last, current)
    if previous is None:
        if valid_last:
            return last, None
        if current.total_tokens == 0:
            return Usage(), None
        return Usage(), "first cumulative snapshot had no valid last-token usage"

    if current == previous:
        return Usage(), None

    if _componentwise_at_most(previous, current):
        delta, reset = current.delta_from(previous)
        if not reset and _usage_vector(asdict(delta)) is not None:
            return delta, None
        if valid_last:
            return last, "monotone cumulative delta was invalid; used last-token usage"
        return Usage(), "monotone cumulative delta was invalid and could not be attributed"

    if valid_last:
        return last, None
    if current.total_tokens == 0:
        return Usage(), None
    return Usage(), "counter rebase had no valid last-token usage"


def _observation_identifier(kind: str, value: str) -> str:
    """Return the hooks v1 namespaced, content-free identifier."""

    return hashlib.sha256(f"codex-usage-v1:{kind}:{value}".encode("utf-8")).hexdigest()


def _provider_identifier(info: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = info.get(name)
        if isinstance(value, str) and value:
            return value
    return ""


def parse_rollout(path: Path, warnings: list[str]) -> ParsedSession | None:
    session_payload: Mapping[str, Any] = {}
    turns_by_id: dict[str, TurnSnapshot] = {}
    turn_order: list[str] = []
    active_turn_id = ""
    context_turn_id = ""
    active_turn_completed = False
    task_markers_seen = False
    previous_cumulative: Usage | None = None
    segment_index = 0
    malformed_lines = 0
    accounting_warnings: list[str] = []

    def ensure_turn(turn_id: str, timestamp: str = "") -> TurnSnapshot:
        if turn_id not in turns_by_id:
            turns_by_id[turn_id] = TurnSnapshot(
                turn_id=turn_id,
                index=len(turn_order) + 1,
                timestamp=timestamp,
            )
            turn_order.append(turn_id)
        turn = turns_by_id[turn_id]
        if timestamp and not turn.timestamp:
            turn.timestamp = timestamp
        return turn

    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError as exc:
        warnings.append(f"rollout unreadable: {path}: {exc}")
        return None

    with handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                malformed_lines += 1
                continue
            if not isinstance(entry, Mapping):
                continue
            entry_type = entry.get("type")
            payload = entry.get("payload")
            if not isinstance(payload, Mapping):
                continue

            if entry_type == "session_meta" and not session_payload:
                session_payload = payload
                continue

            if entry_type == "turn_context":
                turn_id = str(payload.get("turn_id") or f"turn-{len(turn_order) + 1}")
                turn = ensure_turn(turn_id, str(entry.get("timestamp") or ""))
                context_model = str(payload.get("model") or "")
                context_effort = str(payload.get("effort") or "")
                turn.model = context_model or turn.model
                turn.effort = context_effort or turn.effort
                for existing_increment in turn.increments:
                    if context_model and not existing_increment.model:
                        existing_increment.model = context_model
                    if context_effort and not existing_increment.effort:
                        existing_increment.effort = context_effort
                for snapshot in turn.snapshots:
                    if context_model and not snapshot.model:
                        snapshot.model = context_model
                    if context_effort and not snapshot.effort:
                        snapshot.effort = context_effort
                context_turn_id = turn_id
                if not task_markers_seen:
                    active_turn_id = turn_id
                    active_turn_completed = False
                elif not active_turn_id or (active_turn_completed and active_turn_id != turn_id):
                    active_turn_id = turn_id
                    active_turn_completed = False
                continue

            if entry_type != "event_msg":
                continue
            event_type = payload.get("type")
            if event_type == "task_started":
                turn_id = str(payload.get("turn_id") or f"turn-{len(turn_order) + 1}")
                ensure_turn(turn_id, str(entry.get("timestamp") or ""))
                task_markers_seen = True
                active_turn_id = turn_id
                active_turn_completed = False
                continue
            if event_type == "task_complete":
                completed_turn_id = str(payload.get("turn_id") or "")
                if not completed_turn_id or completed_turn_id == active_turn_id:
                    active_turn_completed = True
                continue
            if event_type != "token_count":
                continue

            info = payload.get("info")
            if not isinstance(info, Mapping):
                continue
            current = _usage_vector(info.get("total_token_usage"))
            last = _usage_vector(info.get("last_token_usage"))
            if current is None and last is None:
                accounting_warnings.append(
                    f"line {line_number}: invalid cumulative token vector"
                )
                continue
            if current is None:
                turn_id = active_turn_id or context_turn_id or "legacy-unattributed"
                timestamp = str(entry.get("timestamp") or "")
                turn = ensure_turn(turn_id, timestamp)
                turn.snapshots.append(UsageSnapshot(timestamp, line_number, None, last, frozenset(), _known_usage_fields(info.get("last_token_usage")), segment_index, False, False, turn.model, turn.effort, _provider_identifier(info, "request_id", "requestId", "response_id", "responseId"), _provider_identifier(info, "provider_id", "providerId", "provider")))
                accounting_warnings.append(f"line {line_number}: invalid cumulative token vector; retained valid last-token usage")
                continue
            had_previous = previous_cumulative is not None
            reset = previous_cumulative is not None and not _componentwise_at_most(
                previous_cumulative, current
            )
            if reset:
                segment_index += 1
            increment, accounting_warning = _usage_increment(current, last, previous_cumulative)
            previous_cumulative = current
            if accounting_warning:
                accounting_warnings.append(f"line {line_number}: {accounting_warning}")
            turn_id = active_turn_id or context_turn_id or "legacy-unattributed"
            timestamp = str(entry.get("timestamp") or "")
            turn = ensure_turn(turn_id, timestamp)
            turn.last_usage_timestamp = timestamp or turn.last_usage_timestamp
            valid_last = last if last and _componentwise_at_most(last, current) else None
            turn.snapshots.append(
                UsageSnapshot(
                    timestamp=timestamp,
                    source_line=line_number,
                    cumulative=current,
                    last_request=valid_last,
                    cumulative_known=_known_usage_fields(info.get("total_token_usage")),
                    last_request_known=(
                        _known_usage_fields(info.get("last_token_usage"))
                        if valid_last is not None
                        else frozenset()
                    ),
                    segment_index=segment_index,
                    baseline_complete=had_previous and not reset,
                    reset=reset,
                    model=turn.model,
                    effort=turn.effort,
                    request_identifier=_provider_identifier(
                        info, "request_id", "requestId", "response_id", "responseId"
                    ),
                    provider_identifier=_provider_identifier(
                        info, "provider_id", "providerId", "provider"
                    ),
                )
            )
            # Retain raw snapshots even when they contribute no local delta;
            # only a prior snapshot in the same segment completes a baseline.
            if not increment.total_tokens:
                continue
            turn.increments.append(
                UsageIncrement(
                    timestamp=timestamp,
                    usage=increment,
                    model=turn.model,
                    effort=turn.effort,
                )
            )

    if malformed_lines:
        warnings.append(f"ignored {malformed_lines} malformed JSONL line(s): {path}")
    if accounting_warnings:
        sample = "; ".join(accounting_warnings[:3])
        suffix = f"; +{len(accounting_warnings) - 3} more" if len(accounting_warnings) > 3 else ""
        warnings.append(f"token accounting warnings in {path}: {sample}{suffix}")

    session_id = str(
        session_payload.get("id")
        or _session_id_from_filename(path)
    )
    source = session_payload.get("source")
    spawn = _subagent_spawn(source)
    client_source = str(source) if isinstance(source, str) else "subagent" if spawn else "?"
    archived = "archived_sessions" in {part.lower() for part in path.parts}
    git_data = session_payload.get("git")
    repository_url = ""
    if isinstance(git_data, Mapping):
        repository_url = str(git_data.get("repository_url") or "")

    session = ParsedSession(
        session_id=session_id,
        source_file=str(path.resolve()),
        archived=archived,
        session_timestamp=str(session_payload.get("timestamp") or ""),
        cwd=str(session_payload.get("cwd") or ""),
        originator=str(session_payload.get("originator") or ""),
        cli_version=str(session_payload.get("cli_version") or ""),
        client_source=client_source,
        thread_source=str(session_payload.get("thread_source") or ""),
        parent_thread_id=str(
            session_payload.get("parent_thread_id") or spawn.get("parent_thread_id") or ""
        ),
        depth=(
            _safe_int(session_payload.get("depth"), -1)
            if session_payload.get("depth") is not None
            else _safe_int(spawn.get("depth"), -1) if spawn else None
        ),
        agent_path=str(session_payload.get("agent_path") or spawn.get("agent_path") or ""),
        agent_nickname=str(
            session_payload.get("agent_nickname") or spawn.get("agent_nickname") or ""
        ),
        agent_role=str(session_payload.get("agent_role") or spawn.get("agent_role") or ""),
        repository_url=repository_url,
        turns=[turns_by_id[turn_id] for turn_id in turn_order],
    )
    if not any(turn.increments for turn in session.turns):
        warnings.append(f"no cumulative token snapshots found: {path}")
    return session


def select_unique_sessions(
    sessions: Iterable[ParsedSession], warnings: list[str]
) -> tuple[list[ParsedSession], int]:
    selected: dict[str, ParsedSession] = {}
    duplicates = 0
    for session in sessions:
        previous = selected.get(session.session_id)
        if previous is None:
            selected[session.session_id] = session
            continue
        duplicates += 1
        current_rank = (
            session.final_usage.total_tokens,
            not session.archived,
            len(session.turns),
        )
        previous_rank = (
            previous.final_usage.total_tokens,
            not previous.archived,
            len(previous.turns),
        )
        if current_rank > previous_rank:
            selected[session.session_id] = session
        warnings.append(
            "duplicate session retained once: "
            f"{session.session_id} ({previous.source_file}, {session.source_file})"
        )
    return sorted(selected.values(), key=lambda item: item.session_timestamp), duplicates


def _root_thread_id(thread_id: str, parent_by_child: Mapping[str, str]) -> str:
    current = thread_id
    visited: set[str] = set()
    while parent_by_child.get(current):
        if current in visited:
            return thread_id
        visited.add(current)
        current = parent_by_child[current]
    return current


def _calculated_depth(thread_id: str, parent_by_child: Mapping[str, str]) -> int:
    depth = 0
    current = thread_id
    visited: set[str] = set()
    while parent_by_child.get(current):
        if current in visited:
            return depth
        visited.add(current)
        current = parent_by_child[current]
        depth += 1
    return depth


def _repo_name(repository_url: str, cwd: str, project_id: str) -> str:
    if repository_url:
        parsed = urlparse(repository_url)
        path = parsed.path.rstrip("/")
        if "/_git/" in path:
            return path.rsplit("/_git/", 1)[-1]
        leaf = path.rsplit("/", 1)[-1]
        if leaf.endswith(".git"):
            leaf = leaf[:-4]
        if leaf:
            return leaf
    if project_id:
        return project_id
    if cwd:
        return Path(cwd).name
    return "?"


def _credit_rate(model: str) -> tuple[float, float, float] | None:
    for prefix in sorted(CREDIT_RATES, key=len, reverse=True):
        if model.startswith(prefix):
            return CREDIT_RATES[prefix]
    return None


def estimate_credits(usage: Usage, model: str) -> float | None:
    rate = _credit_rate(model)
    if rate is None:
        return None
    fresh_rate, cached_rate, output_rate = rate
    return (
        usage.fresh_input_tokens * fresh_rate
        + usage.cached_input_tokens * cached_rate
        + usage.output_tokens * output_rate
    ) / 1_000_000


def build_turn_records(
    sessions: Sequence[ParsedSession],
    state_metadata: Mapping[str, ThreadMetadata],
    database_parents: Mapping[str, str],
    include_titles: bool,
    include_credits: bool,
    warnings: list[str],
) -> list[TurnRecord]:
    parent_by_child = dict(database_parents)
    session_by_id = {session.session_id: session for session in sessions}
    for session in sessions:
        if session.parent_thread_id:
            existing = parent_by_child.get(session.session_id)
            if existing and existing != session.parent_thread_id:
                warnings.append(
                    "parent mismatch for "
                    f"{session.session_id}: state={existing}, rollout={session.parent_thread_id}"
                )
            else:
                parent_by_child[session.session_id] = session.parent_thread_id

    records: list[TurnRecord] = []
    unpriced_models: set[str] = set()
    for session in sessions:
        state = state_metadata.get(session.session_id, ThreadMetadata(session.session_id))
        parent_thread_id = parent_by_child.get(session.session_id, session.parent_thread_id)
        root_thread_id = _root_thread_id(session.session_id, parent_by_child)
        root_state = state_metadata.get(root_thread_id, ThreadMetadata(root_thread_id))
        root_session = session_by_id.get(root_thread_id)
        depth = session.depth
        if depth is None or depth < 0:
            depth = _calculated_depth(session.session_id, parent_by_child)
        is_subagent = bool(
            parent_thread_id
            or depth
            or session.thread_source == "subagent"
            or state.thread_source == "subagent"
            or session.agent_path
            or state.agent_path
        )
        thread_type = "subagent" if is_subagent else "root"
        cwd = session.cwd or state.cwd or (root_session.cwd if root_session else "")
        project = _repo_name(
            session.repository_url or (root_session.repository_url if root_session else ""),
            cwd,
            state.project_id or root_state.project_id,
        )
        agent_path = session.agent_path or state.agent_path
        agent_nickname = session.agent_nickname or state.agent_nickname
        agent_role = session.agent_role or state.agent_role
        root_title = root_state.title if include_titles else ""

        for turn in session.turns:
            if not turn.increments:
                continue
            usage_by_group: dict[tuple[str, str, str], Usage] = {}
            calls_by_group: dict[tuple[str, str, str], int] = defaultdict(int)
            timestamp_by_group: dict[tuple[str, str, str], str] = {}
            for increment in turn.increments:
                timestamp = increment.timestamp or turn.timestamp or session.session_timestamp
                day = timestamp[:10] if len(timestamp) >= 10 else ""
                model = increment.model or turn.model or "?"
                effort = increment.effort or turn.effort or "?"
                group = (day, model, effort)
                usage_by_group[group] = usage_by_group.get(group, Usage()).plus(increment.usage)
                calls_by_group[group] += 1
                timestamp_by_group.setdefault(group, timestamp)

            for (day, model, effort), usage in usage_by_group.items():
                credits = estimate_credits(usage, model) if include_credits else None
                if include_credits and credits is None:
                    unpriced_models.add(model)
                records.append(
                    TurnRecord(
                        timestamp=timestamp_by_group[(day, model, effort)],
                        day=day,
                        root_thread_id=root_thread_id,
                        root_title=root_title,
                        thread_id=session.session_id,
                        parent_thread_id=parent_thread_id,
                        depth=depth,
                        thread_type=thread_type,
                        agent_path=agent_path,
                        agent_nickname=agent_nickname,
                        agent_role=agent_role,
                        turn_id=turn.turn_id,
                        turn_index=turn.index,
                        model_calls=calls_by_group[(day, model, effort)],
                        model=model,
                        reasoning_effort=effort,
                        client_source=session.client_source,
                        originator=session.originator,
                        cli_version=session.cli_version,
                        project=project,
                        cwd=cwd,
                        archived=session.archived,
                        input_tokens=usage.input_tokens,
                        cached_input_tokens=usage.cached_input_tokens,
                        fresh_input_tokens=usage.fresh_input_tokens,
                        cache_write_input_tokens=usage.cache_write_input_tokens,
                        output_tokens=usage.output_tokens,
                        reasoning_output_tokens=usage.reasoning_output_tokens,
                        visible_output_tokens=usage.visible_output_tokens,
                        total_tokens=usage.total_tokens,
                        estimated_credits=credits,
                        source_file=session.source_file,
                        credits_enabled=include_credits,
                    )
                )
    if unpriced_models:
        warnings.append(
            "no standard credit rate for model(s): " + ", ".join(sorted(unpriced_models))
        )
    return records


UUID_TURN_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def deduplicate_copied_turns(
    records: Sequence[TurnRecord],
) -> tuple[list[TurnRecord], int]:
    """Remove parent-history turn copies repeated across rollout threads.

    UUID turn IDs are globally stable in retained Codex rollouts. When the same
    UUID appears in more than one thread, retain the thread copy with the most
    token evidence. Exact ties prefer the root thread, then the lexical thread
    ID for deterministic output. Legacy/non-UUID turn labels remain scoped to
    their thread because they are not globally unique.
    """

    rows_by_copy: dict[tuple[str, str], list[TurnRecord]] = defaultdict(list)
    for record in records:
        rows_by_copy[(record.thread_id, record.turn_id)].append(record)

    copies_by_uuid: dict[str, list[tuple[tuple[str, str], list[TurnRecord]]]] = (
        defaultdict(list)
    )
    selected_copies: set[tuple[str, str]] = set()
    for identity, rows in rows_by_copy.items():
        if UUID_TURN_ID.fullmatch(identity[1]):
            copies_by_uuid[identity[1]].append((identity, rows))
        else:
            selected_copies.add(identity)

    removed = 0
    for copies in copies_by_uuid.values():
        winner, _ = min(
            copies,
            key=lambda item: (
                -sum(record.total_tokens for record in item[1]),
                0 if item[1][0].thread_type == "root" else 1,
                item[0][0],
            ),
        )
        selected_copies.add(winner)
        removed += len(copies) - 1

    return (
        [
            record
            for record in records
            if (record.thread_id, record.turn_id) in selected_copies
        ],
        removed,
    )


def _observation_timestamp(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _observation_label(value: str) -> str | None:
    return value if value and value != "?" else None


def _observation_usage(usage: Usage, known: frozenset[str]) -> dict[str, int | None]:
    return {
        "input_tokens": usage.input_tokens if "input_tokens" in known else None,
        "cached_input_tokens": (
            usage.cached_input_tokens if "cached_input_tokens" in known else None
        ),
        "output_tokens": usage.output_tokens if "output_tokens" in known else None,
        "reasoning_tokens": (
            usage.reasoning_output_tokens if "reasoning_output_tokens" in known else None
        ),
        "cache_write_tokens": (
            usage.cache_write_input_tokens if "cache_write_input_tokens" in known else None
        ),
    }


def build_observations(
    sessions: Iterable[ParsedSession], records: Iterable[TurnRecord], warnings: list[str], args: argparse.Namespace | None = None
) -> dict[str, Any]:
    """Project already parsed usage snapshots into the hooks v1 interchange.

    Request and cumulative observations remain separate so consumers cannot add
    an exact request measurement to a cumulative stream. Raw provider snapshots
    are never reconstructed from the accounting deltas used by this auditor.
    """

    sessions = list(sessions)
    selected = {
        (record.thread_id, record.turn_id, record.day, record.model, record.reasoning_effort): record
        for record in records
    }
    parents = {session.session_id: session.parent_thread_id for session in sessions if session.parent_thread_id}
    copies: dict[str, list[tuple[ParsedSession, TurnSnapshot]]] = defaultdict(list)
    allowed_copies: set[tuple[str, str]] = set()
    for session in sessions:
        for turn in session.turns:
            if UUID_TURN_ID.fullmatch(turn.turn_id):
                copies[turn.turn_id].append((session, turn))
            else:
                allowed_copies.add((session.session_id, turn.turn_id))
    for turn_id, candidates in copies.items():
        winner = min(candidates, key=lambda item: (-sum(snapshot.cumulative.total_tokens for snapshot in item[1].snapshots if snapshot.cumulative), 0 if not parents.get(item[0].session_id) else 1, item[0].session_id))
        allowed_copies.add((winner[0].session_id, turn_id))

    def eligible(session: ParsedSession, turn: TurnSnapshot, timestamp: str, model: str, effort: str, thread_type: str) -> bool:
        if (session.session_id, turn.turn_id) not in allowed_copies:
            return False
        day = timestamp[:10]
        root = _root_thread_id(session.session_id, parents)
        if args is None:
            return True
        if args.since and day < args.since or args.until and day > args.until:
            return False
        if args.session and not session.session_id.startswith(args.session) or args.root_session and not root.startswith(args.root_session):
            return False
        if args.model and args.model.lower() not in model.lower() or args.effort and args.effort.lower() != effort.lower():
            return False
        if args.thread_type and args.thread_type != thread_type:
            return False
        if args.agent:
            text = " ".join((session.agent_path, session.agent_nickname, session.agent_role)).lower()
            if args.agent.lower() not in text:
                return False
        if args.project:
            if args.project.lower() not in session.cwd.lower() and args.project.lower() not in session.repository_url.lower():
                return False
        return True
    observations: list[dict[str, Any]] = []
    skipped_timestamps = 0
    for session in sessions:
        source_id = _observation_identifier("source", session.session_id)
        task_id = _observation_identifier("task", session.session_id)
        source_kind = session.thread_source.lower()
        for turn in session.turns:
            for snapshot in turn.snapshots:
                timestamp = _observation_timestamp(snapshot.timestamp)
                if timestamp is None:
                    skipped_timestamps += 1
                    continue
                model = snapshot.model or turn.model or "?"
                effort = snapshot.effort or turn.effort or "?"
                identity = (session.session_id, turn.turn_id, timestamp[:10], model, effort)
                record = selected.get(identity)
                guessed_type = "subagent" if parents.get(session.session_id) else "root"
                if not eligible(session, turn, timestamp, model, effort, guessed_type):
                    continue
                if record is None:
                    # Accounting rows are intentionally absent for an unproven
                    # first baseline or duplicate snapshot. Keep source evidence
                    # for an unfiltered export rather than making it disappear.
                    parent_id = session.parent_thread_id
                    thread_type = "subagent" if parent_id else "root"
                else:
                    parent_id = record.parent_thread_id
                    thread_type = record.thread_type
                parent_task_id = (
                    _observation_identifier("task", parent_id)
                    if parent_id
                    else None
                )
                if turn.turn_id == "legacy-unattributed":
                    attribution = "unattributed"
                elif source_kind == "aggregate":
                    attribution = "aggregate"
                elif thread_type == "subagent":
                    attribution = "child"
                else:
                    attribution = "root"
                turn_id = (
                    None
                    if turn.turn_id == "legacy-unattributed"
                    else _observation_identifier("turn", turn.turn_id)
                )
                segment_id = _observation_identifier(
                    "segment", f"{session.session_id}:{snapshot.segment_index}"
                )
                provider_id = (
                    _observation_identifier("provider", snapshot.provider_identifier)
                    if snapshot.provider_identifier
                    else None
                )
                common = {
                    "source_id": source_id,
                    "source_event_index": snapshot.source_line,
                    "task_id": task_id,
                    "parent_task_id": parent_task_id,
                    "turn_id": turn_id,
                    "provider_id": provider_id,
                    "segment_id": segment_id,
                    "model": _observation_label(model),
                    "reasoning_effort": _observation_label(effort),
                    "attribution": attribution,
                    "observed_at": timestamp,
                    # Local rate cards describe Codex credits only. They are
                    # intentionally not converted into USD API charges.
                    "estimated_cost_usd": None,
                    "rate_card_id": None,
                    "cost_basis": None,
                }
                if snapshot.last_request is not None:
                    observations.append(
                        {
                            **common,
                            "event_id": _observation_identifier(
                                "event", f"{source_id}:{snapshot.source_line}"
                            ),
                            "request_id": (
                                _observation_identifier("request", snapshot.request_identifier)
                                if snapshot.request_identifier
                                else None
                            ),
                            "kind": "request",
                            **_observation_usage(snapshot.last_request, snapshot.last_request_known),
                            "cache_write_semantics": (
                                "included"
                                if "cache_write_input_tokens" in snapshot.last_request_known
                                else "unknown"
                            ),
                            "baseline_complete": True,
                            "reset": False,
                        }
                    )
                if snapshot.cumulative is not None:
                    observations.append(
                        {
                        **common,
                        "event_id": _observation_identifier(
                            "event", f"{source_id}:{snapshot.source_line}"
                        ),
                        "request_id": None,
                        "kind": "cumulative",
                        **_observation_usage(snapshot.cumulative, snapshot.cumulative_known),
                        "cache_write_semantics": (
                            "included"
                            if "cache_write_input_tokens" in snapshot.cumulative_known
                            else "unknown"
                        ),
                        "baseline_complete": snapshot.baseline_complete,
                        "reset": snapshot.reset,
                        }
                    )
    if skipped_timestamps:
        warnings.append(
            f"skipped {skipped_timestamps} usage observation(s) without an RFC 3339 timestamp"
        )
    return {"schema_version": USAGE_OBSERVATIONS_SCHEMA_VERSION, "observations": observations}


def filter_records(records: Iterable[TurnRecord], args: argparse.Namespace) -> list[TurnRecord]:
    selected: list[TurnRecord] = []
    for record in records:
        if args.since and (not record.day or record.day < args.since):
            continue
        if args.until and (not record.day or record.day > args.until):
            continue
        if args.project:
            needle = args.project.lower()
            if needle not in record.project.lower() and needle not in record.cwd.lower():
                continue
        if args.session and not record.thread_id.startswith(args.session):
            continue
        if args.root_session and not record.root_thread_id.startswith(args.root_session):
            continue
        if args.model and args.model.lower() not in record.model.lower():
            continue
        if args.effort and args.effort.lower() != record.reasoning_effort.lower():
            continue
        if args.agent:
            agent_text = " ".join(
                (record.agent_path, record.agent_nickname, record.agent_role)
            ).lower()
            if args.agent.lower() not in agent_text:
                continue
        if args.thread_type and record.thread_type != args.thread_type:
            continue
        selected.append(record)
    return selected


def _task_label(record: TurnRecord) -> str:
    title = " ".join(record.root_title.split())
    if len(title) > 52:
        title = title[:49] + "..."
    return f"{record.root_thread_id} {title}".rstrip()


def _session_label(record: TurnRecord) -> str:
    if record.thread_type == "root":
        title = " ".join(record.root_title.split())
        detail = title[:40] if title else "root"
    else:
        detail = record.agent_path or record.agent_nickname or "subagent"
    return f"{record.thread_id} {detail}".rstrip()


def _agent_label(record: TurnRecord) -> str:
    if record.thread_type == "root":
        return "(root task)"
    label = record.agent_path or "(unnamed subagent)"
    if record.agent_nickname:
        label += f" [{record.agent_nickname}]"
    return label


def _turn_label(record: TurnRecord) -> str:
    owner = record.agent_path if record.thread_type == "subagent" else "root"
    return (
        f"{record.thread_id}#{record.turn_id} "
        f"{owner} {record.model}@{record.reasoning_effort}"
    )


DIMENSION_KEYS: dict[str, Callable[[TurnRecord], str]] = {
    "thread": lambda record: record.thread_type,
    "model": lambda record: record.model,
    "effort": lambda record: record.reasoning_effort,
    "model_effort": lambda record: f"{record.model} @ {record.reasoning_effort}",
    "model_thread": lambda record: f"{record.model} / {record.thread_type}",
    "agent": _agent_label,
    "task": _task_label,
    "session": _session_label,
    "turn": _turn_label,
    "project": lambda record: record.project,
    "day": lambda record: record.day or "?",
    "client": lambda record: record.client_source or record.originator or "?",
    "version": lambda record: record.cli_version or "?",
}


def empty_metrics() -> dict[str, Any]:
    return {
        "turns": 0,
        "model_calls": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "fresh_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "visible_output_tokens": 0,
        "total_tokens": 0,
        "estimated_credits": 0.0,
        "unpriced_turns": 0,
    }


def add_record(
    metrics: dict[str, Any],
    record: TurnRecord,
    count_turn: bool = True,
    count_unpriced_turn: bool = True,
) -> None:
    if count_turn:
        metrics["turns"] += 1
    metrics["model_calls"] += record.model_calls
    for name in (
        "input_tokens",
        "cached_input_tokens",
        "fresh_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "visible_output_tokens",
        "total_tokens",
    ):
        metrics[name] += getattr(record, name)
    if record.estimated_credits is None:
        if record.credits_enabled and count_unpriced_turn:
            metrics["unpriced_turns"] += 1
    else:
        metrics["estimated_credits"] += record.estimated_credits


def aggregate(records: Iterable[TurnRecord], key: Callable[[TurnRecord], str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = defaultdict(empty_metrics)
    seen_turns: dict[str, set[tuple[str, str]]] = defaultdict(set)
    seen_unpriced_turns: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for record in records:
        bucket = key(record)
        identity = (record.thread_id, record.turn_id)
        count_turn = identity not in seen_turns[bucket]
        seen_turns[bucket].add(identity)
        count_unpriced_turn = identity not in seen_unpriced_turns[bucket]
        if record.credits_enabled and record.estimated_credits is None:
            seen_unpriced_turns[bucket].add(identity)
        add_record(
            result[bucket],
            record,
            count_turn=count_turn,
            count_unpriced_turn=count_unpriced_turn,
        )
    return dict(result)


def totals(records: Iterable[TurnRecord]) -> dict[str, Any]:
    result = empty_metrics()
    seen_turns: set[tuple[str, str]] = set()
    seen_unpriced_turns: set[tuple[str, str]] = set()
    for record in records:
        identity = (record.thread_id, record.turn_id)
        count_turn = identity not in seen_turns
        seen_turns.add(identity)
        count_unpriced_turn = identity not in seen_unpriced_turns
        if record.credits_enabled and record.estimated_credits is None:
            seen_unpriced_turns.add(identity)
        add_record(
            result,
            record,
            count_turn=count_turn,
            count_unpriced_turn=count_unpriced_turn,
        )
    return result


def parse_routing_exception(value: str) -> tuple[str, str, str]:
    match = re.fullmatch(
        r"(root|subagent):([^@\s:]+)@([a-z0-9_-]+)", value.strip().lower()
    )
    if not match:
        raise argparse.ArgumentTypeError(
            "expected TYPE:MODEL@EFFORT, where TYPE is root or subagent"
        )
    thread_type, model, effort = match.groups()
    if effort not in KNOWN_ROUTING_EFFORTS:
        raise argparse.ArgumentTypeError(
            "unknown routing-exception effort: " + effort
        )
    return thread_type, model, effort


def assess_route(
    record: TurnRecord,
    exceptions: frozenset[tuple[str, str, str]] = frozenset(),
) -> RouteAssessment:
    thread_type = record.thread_type.lower()
    model = record.model.strip().lower()
    effort = record.reasoning_effort.strip().lower()
    identity = (thread_type, model, effort)
    if identity in exceptions:
        return RouteAssessment(
            "explicit_exception", "matched an explicit CLI routing exception"
        )
    if thread_type not in {"root", "subagent"}:
        return RouteAssessment("unknown", "thread type is missing or unrecognized")
    if not model or not effort:
        return RouteAssessment("unknown", "model or reasoning effort is missing")
    if model not in KNOWN_ROUTING_MODELS:
        return RouteAssessment("unknown", "model is not in the routing policy snapshot")
    if effort not in KNOWN_ROUTING_EFFORTS:
        return RouteAssessment(
            "unknown", "reasoning effort is not in the routing policy snapshot"
        )

    route = (model, effort)
    allowed = (
        CANONICAL_ROOT_ROUTES
        if thread_type == "root"
        else CANONICAL_SUBAGENT_ROUTES
    )
    if route in allowed:
        return RouteAssessment("canonical", "matches the canonical route")
    if thread_type == "subagent" and model == "gpt-5.6-sol":
        reason = "Sol is not permitted as a subagent"
    elif model == "gpt-5.3-codex-spark":
        reason = f"Spark has no canonical {thread_type} route"
    else:
        expected = sorted(
            expected_effort
            for expected_model, expected_effort in allowed
            if expected_model == model
        )
        reason = (
            f"{model} requires {expected[0]} effort for {thread_type} work"
            if expected
            else f"{model} is not permitted for {thread_type} work"
        )
    return RouteAssessment("noncanonical", reason)


def build_routing_adherence(
    records: Sequence[TurnRecord],
    exceptions: frozenset[tuple[str, str, str]] = frozenset(),
) -> dict[str, Any]:
    assessments = {id(record): assess_route(record, exceptions) for record in records}
    by_status = aggregate(records, lambda record: assessments[id(record)].status)
    by_thread_status = aggregate(
        records,
        lambda record: f"{record.thread_type} / {assessments[id(record)].status}",
    )
    issues = aggregate(
        (
            record
            for record in records
            if assessments[id(record)].status in {"noncanonical", "unknown"}
        ),
        lambda record: (
            f"{record.thread_type} / "
            f"{record.model or '?'} @ {record.reasoning_effort or '?'} / "
            f"{assessments[id(record)].status}: {assessments[id(record)].reason}"
        ),
    )
    return {
        "policy_version": ROUTING_POLICY_VERSION,
        "scope": (
            "model, reasoning effort, and thread type only; task-to-lane fit "
            "is not inferred"
        ),
        "canonical_root_routes": [
            {"model": model, "effort": effort}
            for model, effort in sorted(CANONICAL_ROOT_ROUTES)
        ],
        "canonical_subagent_routes": [
            {"model": model, "effort": effort}
            for model, effort in sorted(CANONICAL_SUBAGENT_ROUTES)
        ],
        "explicit_exceptions": [
            {"thread_type": thread_type, "model": model, "effort": effort}
            for thread_type, model, effort in sorted(exceptions)
        ],
        "by_status": [
            {"status": status, **by_status.get(status, empty_metrics())}
            for status in ROUTING_STATUSES
        ],
        "by_thread_status": [
            {"key": key, **metrics}
            for key, metrics in _sorted_rows("routing", by_thread_status)
        ],
        "issues": [
            {"key": key, **metrics}
            for key, metrics in _sorted_rows("routing", issues)
        ],
    }


def _sorted_rows(dimension: str, rows: Mapping[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    if dimension == "day":
        return sorted(rows.items(), key=lambda item: item[0], reverse=True)
    return sorted(rows.items(), key=lambda item: (-item[1]["total_tokens"], item[0]))


def build_report(
    records: Sequence[TurnRecord],
    dimensions: Sequence[str],
    codex_home: Path | str,
    stats: ScanStats,
    warnings: Sequence[str],
    filters: Mapping[str, Any],
    routing_exceptions: frozenset[tuple[str, str, str]] = frozenset(),
) -> dict[str, Any]:
    breakdowns: dict[str, list[dict[str, Any]]] = {}
    for dimension in dimensions:
        rows = aggregate(records, DIMENSION_KEYS[dimension])
        breakdowns[dimension] = [
            {"key": key, **metrics}
            for key, metrics in _sorted_rows(dimension, rows)
        ]
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "codex_home": str(codex_home),
        "scope": "retained local Codex rollout files; not account billing",
        "filters": dict(filters),
        "scan": asdict(stats),
        "totals": totals(records),
        "routing_adherence": build_routing_adherence(records, routing_exceptions),
        "breakdowns": breakdowns,
        "pricing": {
            "schema_version": "rate-card-metadata/v1",
            "rate_card_id": "codex-standard-credits-2026-08-23",
            "measurement": "estimated",
            "cost_basis": "standard_credit_equivalent",
            "subscription_charge": None,
            "api_equivalent_usd": None,
            "unit": "estimated standard Codex credits per 1M tokens",
            "verified_date": "2026-08-23",
            "rates": {
                model: {
                    "fresh_input": rate[0],
                    "cached_input": rate[1],
                    "output_including_reasoning": rate[2],
                }
                for model, rate in CREDIT_RATES.items()
            },
            "note": "Rate-card estimate only; included-plan usage and billing can differ.",
        },
        "warnings": list(warnings),
        "records": [asdict(record) for record in records],
    }


def format_number(value: int, raw: bool) -> str:
    if raw:
        return f"{value:,}"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    return str(value)


def print_table(
    dimension: str,
    rows: Mapping[str, dict[str, Any]],
    raw: bool,
    top: int,
    include_credits: bool,
    output: TextIO,
) -> None:
    ordered = _sorted_rows(dimension, rows)
    shown = ordered[:top]
    key_width = min(64, max([len(key) for key, _ in shown] + [8]))
    grand_total = sum(row["total_tokens"] for _, row in ordered) or 1
    print(f"\n== by {dimension.replace('_', ' ')} ==", file=output)
    credit_header = "  est cr" if include_credits else ""
    header = (
        f"{'':<{key_width}}  {'turns':>5} {'calls':>5} {'input':>9} {'cached':>9} "
        f"{'fresh':>9} {'output':>9} {'reason':>9} {'total':>9} {'%tok':>5}"
        f"{credit_header}"
    )
    print(header, file=output)
    print("-" * len(header), file=output)
    for key, row in shown:
        display_key = key if len(key) <= key_width else key[: key_width - 3] + "..."
        credit = ""
        if include_credits:
            suffix = "+?" if row["unpriced_turns"] else ""
            credit = f"  {row['estimated_credits']:7.2f}{suffix}"
        print(
            f"{display_key:<{key_width}}  {row['turns']:>5} {row['model_calls']:>5} "
            f"{format_number(row['input_tokens'], raw):>9} "
            f"{format_number(row['cached_input_tokens'], raw):>9} "
            f"{format_number(row['fresh_input_tokens'], raw):>9} "
            f"{format_number(row['output_tokens'], raw):>9} "
            f"{format_number(row['reasoning_output_tokens'], raw):>9} "
            f"{format_number(row['total_tokens'], raw):>9} "
            f"{100 * row['total_tokens'] / grand_total:>4.0f}%{credit}",
            file=output,
        )
    if len(ordered) > top:
        remaining = ordered[top:]
        print(
            f"{'(+' + str(len(remaining)) + ' more)':<{key_width}}  "
            f"{sum(row['turns'] for _, row in remaining):>5} "
            f"{sum(row['model_calls'] for _, row in remaining):>5}"
            f"{'':>54}{format_number(sum(row['total_tokens'] for _, row in remaining), raw):>9}",
            file=output,
        )


def print_report(
    records: Sequence[TurnRecord],
    dimensions: Sequence[str],
    stats: ScanStats,
    raw: bool,
    top: int,
    include_credits: bool,
    output: TextIO,
    routing_exceptions: frozenset[tuple[str, str, str]] = frozenset(),
) -> None:
    summary = totals(records)
    days = sorted({record.day for record in records if record.day})
    date_range = f"{days[0]} -> {days[-1]}" if days else "unknown dates"
    roots = len({record.root_thread_id for record in records})
    sessions = len({record.thread_id for record in records})
    print(
        f"Codex token usage audit - {summary['turns']:,} turns, "
        f"{summary['model_calls']:,} model calls, {sessions:,} threads, "
        f"{roots:,} root tasks, {date_range}",
        file=output,
    )
    print(
        f"  input {summary['input_tokens']:,} "
        f"(cached {summary['cached_input_tokens']:,}, fresh {summary['fresh_input_tokens']:,})",
        file=output,
    )
    print(
        f"  output {summary['output_tokens']:,} "
        f"(reasoning subset {summary['reasoning_output_tokens']:,}, "
        f"visible {summary['visible_output_tokens']:,})",
        file=output,
    )
    print(f"  total {summary['total_tokens']:,}", file=output)
    if include_credits:
        suffix = "+ unknown-model usage" if summary["unpriced_turns"] else ""
        print(
            f"  estimated standard-rate credits {summary['estimated_credits']:,.2f} {suffix}".rstrip(),
            file=output,
        )
        print("  NOTE: credits are a rate-card estimate, not a billing statement.", file=output)
    print(
        f"  scanned {stats.rollout_files_parsed}/{stats.rollout_files_discovered} rollout files; "
        f"skipped {stats.rollout_files_skipped}; duplicate sessions {stats.duplicate_sessions}; "
        f"copied turns {stats.copied_turns_deduplicated}",
        file=output,
    )

    adherence = build_routing_adherence(records, routing_exceptions)
    print("\n== routing adherence ==", file=output)
    for row in adherence["by_status"]:
        print(
            f"  {row['status']:<18} {row['turns']:>5} turns "
            f"{row['model_calls']:>6} calls "
            f"{format_number(row['total_tokens'], raw):>9} total",
            file=output,
        )
    if adherence["issues"]:
        print("  issues:", file=output)
        for row in adherence["issues"][:top]:
            print(
                f"    {row['key']} - {row['turns']} turns, "
                f"{format_number(row['total_tokens'], raw)} total",
                file=output,
            )

    for dimension in dimensions:
        print_table(
            dimension,
            aggregate(records, DIMENSION_KEYS[dimension]),
            raw,
            top,
            include_credits,
            output,
        )


def write_json(path: str, report: Mapping[str, Any], output: TextIO) -> None:
    if path == "-":
        json.dump(report, output, indent=2, ensure_ascii=False)
        output.write("\n")
        return
    destination = Path(path)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_csv(path: str, records: Sequence[TurnRecord], output: TextIO) -> None:
    fields = list(TurnRecord.__dataclass_fields__)
    if path == "-":
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(asdict(record) for record in records)
        return
    destination = Path(path)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(asdict(record) for record in records)


def parse_iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def redact_codex_home(value: str, codex_home: Path) -> str:
    variants = {
        str(codex_home),
        str(codex_home.resolve()),
        str(codex_home).replace("\\", "/"),
    }
    result = value
    for variant in sorted((item for item in variants if item), key=len, reverse=True):
        result = re.sub(re.escape(variant), "<CODEX_HOME>", result, flags=re.IGNORECASE)
    return result


def redact_input_paths(
    value: str,
    codex_home: Path,
    database_path: Path,
    rollout_paths: Sequence[Path],
) -> str:
    """Redact known input paths, including sources located outside Codex home."""

    result = redact_codex_home(value, codex_home)
    root = codex_home.resolve()
    candidates = [(database_path, "<STATE_DB>")]
    candidates.extend((path, "<ROLLOUT>") for path in rollout_paths)
    for path, replacement in candidates:
        resolved = path.expanduser().resolve()
        try:
            resolved.relative_to(root)
            continue
        except ValueError:
            pass
        variants = {
            str(path),
            str(resolved),
            str(path).replace("\\", "/"),
            str(resolved).replace("\\", "/"),
        }
        for variant in sorted((item for item in variants if item), key=len, reverse=True):
            result = re.sub(re.escape(variant), replacement, result, flags=re.IGNORECASE)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit locally retained Codex token usage by turn and subagent tree."
    )
    parser.add_argument(
        "--root",
        help="Codex data root (default: CODEX_HOME or ~/.codex)",
    )
    parser.add_argument("--state-db", help="state database path (default: ROOT/state_5.sqlite)")
    parser.add_argument("--no-state-db", action="store_true", help="skip optional SQLite enrichment")
    parser.add_argument("--active-only", action="store_true", help="exclude archived_sessions")
    parser.add_argument("--since", type=parse_iso_date, help="UTC date, inclusive")
    parser.add_argument("--until", type=parse_iso_date, help="UTC date, inclusive")
    parser.add_argument("--project", help="substring filter on project/repository or cwd")
    parser.add_argument("--session", help="thread/session id prefix")
    parser.add_argument("--root-session", help="root task id prefix, including descendants")
    parser.add_argument("--model", help="substring filter on model id")
    parser.add_argument("--effort", help="exact reasoning-effort filter")
    parser.add_argument("--agent", help="substring filter on subagent path, nickname, or role")
    parser.add_argument("--thread-type", choices=("root", "subagent"))
    parser.add_argument(
        "--by",
        action="append",
        default=[],
        help="comma list: " + ",".join(ALL_DIMENSIONS) + ",all",
    )
    parser.add_argument("--top", type=int, default=20, help="rows per table (default: 20)")
    parser.add_argument("--raw", action="store_true", help="show exact integers instead of K/M/B")
    parser.add_argument(
        "--include-titles",
        action="store_true",
        help="read/export prompt-derived task titles (off by default)",
    )
    parser.add_argument(
        "--include-paths",
        action="store_true",
        help="export Codex home, cwd, and rollout paths (off by default)",
    )
    parser.add_argument("--no-credits", action="store_true", help="suppress rate-card credit estimates")
    parser.add_argument("--strict", action="store_true", help="return exit 2 when scan warnings occur")
    parser.add_argument(
        "--routing-exception",
        action="append",
        default=[],
        type=parse_routing_exception,
        metavar="TYPE:MODEL@EFFORT",
        help=(
            "classify one exact root/subagent model-effort route as an explicit "
            "exception; repeatable"
        ),
    )
    parser.add_argument("--csv", metavar="PATH", help="write normalized turn rows; use - for stdout")
    parser.add_argument("--json", metavar="PATH", help="write full snapshot; use - for stdout")
    parser.add_argument(
        "--observations",
        metavar="PATH",
        help="write content-free usage-observations/v1 JSON; use - for stdout",
    )
    return parser


def _dimensions(values: Sequence[str], parser: argparse.ArgumentParser) -> list[str]:
    requested: list[str] = []
    for value in values:
        requested.extend(part.strip() for part in value.split(",") if part.strip())
    if not requested:
        return list(DEFAULT_DIMENSIONS)
    if "all" in requested:
        return list(ALL_DIMENSIONS)
    invalid = sorted(set(requested) - set(ALL_DIMENSIONS))
    if invalid:
        parser.error("unknown --by dimension(s): " + ", ".join(invalid))
    return list(dict.fromkeys(requested))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.since and args.until and args.since > args.until:
        parser.error("--since must be on or before --until")
    if args.top < 1:
        parser.error("--top must be at least 1")
    if sum(path == "-" for path in (args.csv, args.json, args.observations)) > 1:
        parser.error("only one machine-readable output may use stdout")

    root_value = args.root or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    codex_home = Path(root_value).expanduser().resolve()
    if not codex_home.is_dir():
        print(f"Codex data root does not exist: {codex_home}", file=sys.stderr)
        return 2
    dimensions = _dimensions(args.by, parser)
    routing_exceptions = frozenset(args.routing_exception)
    warnings: list[str] = []
    stats = ScanStats()

    database_path = (
        Path(args.state_db).expanduser().resolve()
        if args.state_db
        else codex_home / "state_5.sqlite"
    )

    state_metadata: dict[str, ThreadMetadata] = {}
    database_parents: dict[str, str] = {}
    if not args.no_state_db:
        state_metadata, database_parents, state_warnings = load_state_metadata(
            database_path,
            include_titles=args.include_titles,
            warn_if_missing=bool(args.state_db),
        )
        warnings.extend(state_warnings)
        stats.state_threads = len(state_metadata)
        stats.state_edges = len(database_parents)

    all_rollout_paths = discover_rollouts(codex_home, True, state_metadata)
    validate_output_destinations(
        parser,
        args.csv,
        args.json,
        database_path,
        all_rollout_paths,
        args.observations,
    )
    rollout_paths = (
        [path for path in all_rollout_paths if not _is_archived_rollout(path)]
        if args.active_only
        else all_rollout_paths
    )
    rollout_paths = restrict_rollouts_from_state(
        rollout_paths,
        args.session,
        args.root_session,
        state_metadata,
        database_parents,
    )
    stats.rollout_files_discovered = len(rollout_paths)
    parsed_sessions: list[ParsedSession] = []
    for path in rollout_paths:
        session = parse_rollout(path, warnings)
        if session is None:
            stats.rollout_files_skipped += 1
            continue
        stats.rollout_files_parsed += 1
        parsed_sessions.append(session)

    sessions, stats.duplicate_sessions = select_unique_sessions(parsed_sessions, warnings)
    records = build_turn_records(
        sessions,
        state_metadata,
        database_parents,
        include_titles=args.include_titles,
        include_credits=not args.no_credits,
        warnings=warnings,
    )
    records, stats.copied_turns_deduplicated = deduplicate_copied_turns(records)
    selected = filter_records(records, args)
    selected.sort(key=lambda record: (record.timestamp, record.thread_id, record.turn_index))
    observations = build_observations(sessions, selected, warnings, args) if args.observations else None
    if not args.include_paths:
        for record in selected:
            record.cwd = ""
            record.source_file = ""

    filters = {
        "since": args.since,
        "until": args.until,
        "project": args.project,
        "session": args.session,
        "root_session": args.root_session,
        "model": args.model,
        "effort": args.effort,
        "agent": args.agent,
        "thread_type": args.thread_type,
        "active_only": args.active_only,
        "titles_included": args.include_titles,
        "paths_included": args.include_paths,
        "routing_exceptions": [
            f"{thread_type}:{model}@{effort}"
            for thread_type, model, effort in sorted(routing_exceptions)
        ],
    }
    output_warnings = (
        warnings
        if args.include_paths
        else [
            redact_input_paths(
                warning,
                codex_home,
                database_path,
                all_rollout_paths,
            )
            for warning in warnings
        ]
    )
    report_home: Path | str = codex_home if args.include_paths else "<redacted>"
    report = build_report(
        selected,
        dimensions,
        report_home,
        stats,
        output_warnings,
        filters,
        routing_exceptions,
    )
    machine_stdout = args.csv == "-" or args.json == "-" or args.observations == "-"

    if not machine_stdout:
        if selected:
            print_report(
                selected,
                dimensions,
                stats,
                args.raw,
                args.top,
                include_credits=not args.no_credits,
                output=sys.stdout,
                routing_exceptions=routing_exceptions,
            )
        else:
            print("no Codex token usage records matched", file=sys.stdout)

    if args.csv:
        write_csv(args.csv, selected, sys.stdout)
        if args.csv != "-":
            destination = sys.stderr if machine_stdout else sys.stdout
            print(f"\nwrote {len(selected):,} turn rows -> {args.csv}", file=destination)
    if args.json:
        write_json(args.json, report, sys.stdout)
        if args.json != "-":
            destination = sys.stderr if machine_stdout else sys.stdout
            print(f"wrote snapshot -> {args.json}", file=destination)
    if args.observations:
        write_json(args.observations, observations, sys.stdout)
        if args.observations != "-":
            destination = sys.stderr if machine_stdout else sys.stdout
            print(f"wrote usage observations -> {args.observations}", file=destination)

    if output_warnings:
        print(f"\nWarnings ({len(output_warnings)}):", file=sys.stderr)
        for warning in output_warnings[:10]:
            print(f"- {warning}", file=sys.stderr)
        if len(output_warnings) > 10:
            print(f"- (+{len(output_warnings) - 10} more; use --json for all)", file=sys.stderr)
    return 2 if args.strict and warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
