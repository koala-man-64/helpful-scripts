"""Conversation persistence for the mcp-chatbot server.

Each conversation is one JSON file at ``<data_dir>/<name>.json``. The data dir
is ``MCP_CHATBOT_DATA_DIR`` when set, else ``~/.mcp-chatbot/conversations``.
Writes are atomic (tmp file + os.replace) and serialized behind a lock because
FastMCP may run sync tools on multiple worker threads.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_LOCK = threading.Lock()


def data_dir() -> Path:
    override = os.getenv("MCP_CHATBOT_DATA_DIR")
    return Path(override) if override else Path.home() / ".mcp-chatbot" / "conversations"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_name(name: str) -> str:
    # fullmatch, not match: with match, '$' would accept a trailing newline,
    # which passes validation but produces an invalid filename on Windows.
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"Invalid conversation name {name!r}: use 1-64 characters from letters, "
            "digits, '.', '_' and '-', starting with a letter or digit."
        )
    return name


def _path_for(name: str) -> Path:
    return data_dir() / f"{validate_name(name)}.json"


def new_record(name: str, mode: str, model: str, system: str | None) -> dict[str, Any]:
    now = utc_now()
    return {
        "version": 1,
        "name": name,
        "mode": mode,
        "model": model,
        "system": system,
        "created_at": now,
        "updated_at": now,
        "last_response_id": None,
        "agent_name": None,
        "remote_conversation_id": None,
        "messages": [],
    }


def load(name: str) -> dict[str, Any] | None:
    """Return the stored record, or None if the conversation does not exist."""
    path = _path_for(name)
    with _LOCK:
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Conversation file {path} is corrupt ({exc}); delete or repair it."
            ) from exc
    stored_name = record.get("name") if isinstance(record, dict) else None
    if stored_name != name:
        # On case-insensitive filesystems (Windows) 'Alpha' and 'alpha' resolve
        # to the same file; refuse to silently continue a different conversation.
        # ValueError (bad input name), so delete() does not treat it as corruption.
        raise ValueError(
            f"Conversation file for {name!r} belongs to {stored_name!r} (names are "
            "case-insensitive on this filesystem); use the exact stored name."
        )
    return record


def save(record: dict[str, Any]) -> None:
    path = _path_for(record["name"])
    record["updated_at"] = utc_now()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / (path.name + ".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def delete(name: str) -> dict[str, Any] | None:
    """Delete a stored conversation; returns its record, or None if it was unreadable."""
    path = _path_for(name)
    if not path.exists():
        raise ValueError(f"No conversation named {name!r}.")
    try:
        record = load(name)
    except RuntimeError:
        record = None  # corrupt or aliased file: deletion must still be possible
    with _LOCK:
        path.unlink(missing_ok=True)
    return record


def list_all() -> list[dict[str, Any]]:
    """Summaries of all stored conversations, most recently updated first.

    Files whose stem is not a valid conversation name were not written by this
    server and are skipped; unreadable files are reported as an error entry so
    one bad file never hides the rest.
    """
    directory = data_dir()
    if not directory.exists():
        return []
    summaries = []
    for path in directory.glob("*.json"):
        if not _NAME_RE.fullmatch(path.stem):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            summaries.append(
                {"name": path.stem, "error": f"unreadable conversation file: {exc}"}
            )
            continue
        if not isinstance(record, dict):
            summaries.append({"name": path.stem, "error": "unreadable conversation file"})
            continue
        summaries.append(
            {
                "name": record.get("name", path.stem),
                "mode": record.get("mode"),
                "model": record.get("model"),
                "message_count": len(record.get("messages", [])),
                "updated_at": record.get("updated_at", ""),
            }
        )
    return sorted(summaries, key=lambda s: s.get("updated_at", ""), reverse=True)
