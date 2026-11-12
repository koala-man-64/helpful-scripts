"""Bounded, redacted command diagnostics and opt-in action timing traces."""
from __future__ import annotations

import io
import json
import math
import re
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

from . import api, common


MAX_EVENTS = 64
MAX_EVENT_TEXT = 320
MAX_TRACE_INPUT_BYTES = 16 * 1024 * 1024
MAX_TRACE_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_TRACE_EVENTS = 2000

# These are BrowserEngine handler verbs that can perform or inspect a synchronous browser command.
# Control / receipt verbs and the cooperative wait are intentionally excluded.
TRACE_COMMANDS = frozenset({
    "goto", "snapshot", "click", "screenshot", "table", "diagnose", "run", "fill-form", "api",
    "fill", "type", "press", "select", "check", "uncheck", "hover", "scroll", "upload", "text",
    "value", "back", "reload", "tabs", "tab", "downloads", "eval",
})

_SAFE_METHOD = re.compile(r"[A-Za-z_$][A-Za-z0-9_.$]{0,79}\Z")
_SAFE_ID = re.compile(r"[A-Za-z0-9_:@.-]{1,100}\Z")
_STANDALONE_TOKEN = re.compile(
    r"(?=[A-Za-z0-9_.~+/-]{24,})(?=[A-Za-z0-9_.~+/-]*[A-Z])(?=[A-Za-z0-9_.~+/-]*[0-9])"
    r"[A-Za-z0-9_.~+/-]{24,}"
)
_OPEN_QUOTE = re.compile(r"\s*([\"'])")
_AUTH_SCHEME = re.compile(r"(?i)^(Bearer|Basic)\s+([^\s,;&}\]]+)")
_CREDENTIAL_SEPARATOR = re.compile(api.SEPARATOR)


def redact_url(value: Any) -> str:
    """Keep only an HTTP(S)/WS(S) origin and a redacted path; discard all URL credentials and parameters."""
    raw = common.strip_invisible(value or "")
    try:
        parts = urllib.parse.urlsplit(raw)
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https", "ws", "wss"} or not parts.hostname:
            return f"{scheme}:***" if scheme else common.MASK
        host = parts.hostname.lower()
        if ":" in host:  # retain valid IPv6 brackets, never userinfo
            host = f"[{host}]"
        port = parts.port
        netloc = f"{host}:{port}" if port is not None else host
        path = common.redact_path(parts.path)
        return common.sanitize_text(urllib.parse.urlunsplit((scheme, netloc, path, "", "")), MAX_EVENT_TEXT)
    except (ValueError, UnicodeError):
        return common.MASK


def redact_text(value: Any) -> str:
    """Redact URLs, credential carriers, and token-shaped standalone values before retaining error text."""
    raw = value if isinstance(value, str) else str(value)
    was_truncated = len(raw) > 4096
    text = common.strip_invisible(raw[:4096])
    if was_truncated:
        text += "..."

    def redact_match(match: re.Match[str]) -> str:
        raw = match.group(0)
        bare = raw.rstrip(common.URL_TRAILING)
        return redact_url(bare) + raw[len(bare):]

    text = common.URL_IN_TEXT_RE.sub(redact_match, text)
    text = common.redact_text(text)
    # Secret-like key/value pairs are redacted even when short or not token-shaped. Unlike an API body,
    # diagnostic strings have no useful structured fields to preserve, so err on masking the whole value.
    out: list[str] = []
    last = 0
    for match in api.KEY_RE.finditer(text):
        key = match.group(0)
        if not (common._sensitive_key(key) or common.SECRET_STRONG_RE.search(key)):
            continue
        separator = _CREDENTIAL_SEPARATOR.match(text, match.end())
        if separator is None or match.start() < last:
            continue
        start = separator.end()
        if start >= len(text):
            continue
        quoted = _OPEN_QUOTE.match(text, start)
        if quoted:
            quote = quoted.group(1)
            start = quoted.end()
            end = start
            while end < len(text):
                if text[end] == quote and (end == start or text[end - 1] != "\\"):
                    break
                end += 1
            if end >= len(text):
                end = text.find("\n", start)
                if end < 0:
                    end = len(text)
        else:
            start += len(text[start:]) - len(text[start:].lstrip())
            auth = _AUTH_SCHEME.match(text[start:]) if common._sensitive_key(key) else None
            if auth:
                start += auth.start(2)
                end = start + len(auth.group(2))
            else:
                end = start
                while end < len(text) and text[end] not in ",;}&] \t\r\n":
                    end += 1
        if end <= start:
            continue
        out.extend((text[last:start], common.MASK))
        last = end
    if out:
        text = "".join(out) + text[last:]
    text, _ = api.mask_token_carriers(text)
    text = common.JWT_RE.sub(common.MASK, text)
    text = _STANDALONE_TOKEN.sub(common.MASK, text)
    return common.sanitize_text(text, MAX_EVENT_TEXT, single_line=True)


class CommandDiagnostics:
    """One command's small in-memory event list; only normalized values are exposed or persisted."""

    def __init__(self, limit: int = MAX_EVENTS) -> None:
        self.limit = max(1, int(limit))
        self.events: list[dict[str, Any]] = []
        self.dropped = 0

    def _append(self, event: dict[str, Any]) -> int | None:
        if len(self.events) >= self.limit:
            self.dropped += 1
            return None
        self.events.append(event)
        return len(self.events) - 1

    def error(self, kind: str, message: Any) -> None:
        if kind not in {"console_error", "page_error"}:
            return
        self._append({"kind": kind, "message": redact_text(message)})

    def request(self, url: Any, method: Any) -> int | None:
        verb = common.sanitize_text(method, 20, single_line=True).upper()
        if not re.fullmatch(r"[A-Z]{1,20}", verb):
            verb = "***"
        return self._append({"kind": "request", "url": redact_url(url), "method": verb})

    def finish_request(self, index: int | None, *, status: Any = None, duration_ms: Any = None,
                       failure: Any = None) -> None:
        if index is None or index < 0 or index >= len(self.events):
            return
        event = self.events[index]
        if event.get("kind") != "request":
            return
        if type(status) is int and 0 <= status <= 999:
            event["status"] = status
        if type(duration_ms) in (int, float) and math.isfinite(duration_ms) and duration_ms >= 0:
            event["duration_ms"] = min(int(duration_ms), 86_400_000)
        if failure:
            event["failure"] = redact_text(failure)

    def as_dict(self) -> dict[str, Any]:
        return {"events": list(self.events), "truncated": self.dropped > 0, "dropped": self.dropped}

    def summary(self) -> dict[str, Any]:
        return {"event_count": len(self.events) + self.dropped, "truncated": self.dropped > 0, "dropped": self.dropped}


def _safe_trace_event(event: Any, starts: dict[str, float]) -> dict[str, Any] | None:
    if not isinstance(event, dict) or event.get("type") not in {"before", "after"}:
        return None
    call_id = event.get("callId")
    if not isinstance(call_id, str) or not _SAFE_ID.fullmatch(call_id):
        return None
    kind = event["type"]
    safe: dict[str, Any] = {"type": kind, "callId": call_id}
    if kind == "before":
        method, class_name = event.get("method"), event.get("class")
        timestamp = event.get("startTime")
        if not isinstance(method, str) or not _SAFE_METHOD.fullmatch(method):
            return None
        if not isinstance(class_name, str) or not _SAFE_METHOD.fullmatch(class_name):
            return None
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
            return None
        safe.update({"startTime": timestamp, "class": class_name, "method": method})
        page_id = event.get("pageId")
        if isinstance(page_id, str) and _SAFE_ID.fullmatch(page_id):
            safe["pageId"] = page_id
        starts[call_id] = float(timestamp)
    else:
        timestamp = event.get("endTime")
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
            return None
        start = starts.pop(call_id, None)
        if start is None:
            return None
        safe["endTime"] = timestamp
        safe["failed"] = bool(event.get("error"))
        safe["duration_ms"] = max(0, min(int(float(timestamp) - start), 86_400_000))
    return safe


def sanitize_trace_archive(raw_path: str | Path) -> bytes:
    """Return a zip with only redacted action timing records; drop snapshots, bodies, headers, and raw params."""
    source = Path(raw_path)
    if source.stat().st_size > MAX_TRACE_INPUT_BYTES:
        raise ValueError("trace_too_large")
    try:
        with zipfile.ZipFile(source, "r") as archive:
            entries = archive.infolist()
            if len(entries) > 256 or sum(item.file_size for item in entries) > MAX_TRACE_INPUT_BYTES:
                raise ValueError("trace_too_large")
            trace_entries = [item for item in entries if item.filename == "trace.trace"]
            if len(trace_entries) != 1:
                raise ValueError("trace_invalid")
            raw = archive.read(trace_entries[0])
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError("trace_invalid") from exc

    starts: dict[str, float] = {}
    kept: list[dict[str, Any]] = []
    version = 8
    for line in raw.splitlines():
        if len(line) > 256 * 1024:
            continue
        try:
            event = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(event, dict) and event.get("type") == "context-options":
            raw_version = event.get("version")
            if type(raw_version) is int and 1 <= raw_version <= 8:
                version = raw_version
            continue
        safe = _safe_trace_event(event, starts)
        if safe is not None:
            kept.append(safe)
            if len(kept) >= MAX_TRACE_EVENTS:
                break
    if not kept:
        raise ValueError("trace_empty")
    header = {
        "version": version, "type": "context-options", "origin": "library", "browserName": "",
        "playwrightVersion": "", "options": {}, "platform": "unknown", "wallTime": 0,
        "monotonicTime": 0, "sdkLanguage": "python", "testIdAttributeName": "", "contextId": "",
    }
    encoded = "".join(json.dumps(item, separators=(",", ":"), ensure_ascii=True) + "\n"
                     for item in [header, *kept]).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("trace.trace", encoded)
    payload = output.getvalue()
    if len(payload) > MAX_TRACE_OUTPUT_BYTES:
        raise ValueError("trace_too_large")
    return payload
