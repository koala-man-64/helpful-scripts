#!/usr/bin/env python3
"""snow - single-file ServiceNow REST client for humans and AI agents.

Create, update, and annotate ServiceNow records (incidents, changes, catalog
requests, vulnerable items, or any table), read them back, and pull journal and
audit history - as a CLI that prints JSON to stdout, or as an importable module.

Requirements:
  pip install python-dotenv

Commands (run `python servicenow_client.py --help` for full detail):
  whoami tables get query count create update comment work-note notes history
  resolve close assign user group choices schema sla approvals attachments
  attach download delete

Environment (.env at the repo root, or real env vars - read lazily):
  SERVICENOW_INSTANCE            bare instance name or full https URL   (required)
  SERVICENOW_USERNAME            service-account user                   (required)
  SERVICENOW_PASSWORD            service-account password               (required)
  SERVICENOW_AUTH                "basic" (default; "oauth" reserved)
  SERVICENOW_TIMEOUT_SECONDS     per-request timeout, default 30
  SERVICENOW_READ_ONLY           "true" refuses every write before sending
  SERVICENOW_ALLOW_DELETE        must be "true" for the delete command
  SERVICENOW_MAX_ATTACHMENT_MB   attachment size cap, default 100

Error convention (repo-wide): ValueError = bad input, fix the call (exit 2);
RuntimeError = environment or remote failure, fix config or retry (exit 1).
Failures print {"error": {...}} to stderr; stdout carries only result JSON.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Sequence

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Constants

SYS_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
DURATION_RE = re.compile(r"^(\d+)([dhm])$")

DEFAULT_TIMEOUT = 30.0
MAX_RESPONSE_BYTES = 8 * 1024 * 1024  # JSON responses; attachments have their own cap
MAX_ATTACHMENT_MB_DEFAULT = 100
DEFAULT_LIMIT = 25
MAX_LIMIT = 1000            # hard ceiling on a single page
MAX_RECORDS_CAP = 1000      # hard ceiling on an --all walk
RETRY_ATTEMPTS = 3          # total attempts, not extra retries
RETRY_AFTER_CAP = 30.0      # never sleep longer than this on a 429
DEFAULT_MAX_FIELD_CHARS = 20_000
JOURNAL_FIELDS = ("comments", "work_notes")
DISPLAY_MODES = ("value", "display", "both")

# Writes to platform-configuration tables are refused outright: an agent has no
# business editing roles, ACLs, schema, or scripts, and --table is generic
# enough to reach them by accident.
WRITE_DENYLIST = (
    "sys_user_has_role",
    "sys_user_role",
    "sys_user_grmember",
    "sys_group_has_role",
    "sys_security",
    "sys_properties",
    "sys_script",
    "sys_dictionary",
    "sys_db_object",
    "sys_choice",
    "ecc_queue",
)

VERBOSE = False


def _log(message: str) -> None:
    # Diagnostics go to stderr so stdout stays a clean JSON stream.
    if VERBOSE:
        print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# Config helpers (all env reads are lazy - nothing touched at import)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise _tag(
            RuntimeError(f"Missing required environment variable: {name}"), "config"
        )
    return value


def _env_flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _read_only() -> bool:
    return _env_flag("SERVICENOW_READ_ONLY")


def _delete_allowed() -> bool:
    return _env_flag("SERVICENOW_ALLOW_DELETE")


def _base_url() -> str:
    value = require_env("SERVICENOW_INSTANCE").strip().rstrip("/")
    if "://" in value:
        if not value.startswith("https://"):
            raise _tag(
                RuntimeError(
                    "SERVICENOW_INSTANCE must use https:// - credentials travel in "
                    "request headers and must never cross plain HTTP."
                ),
                "config",
            )
        return value
    return f"https://{value}.service-now.com"


def _attachment_cap_bytes() -> int:
    raw = os.getenv("SERVICENOW_MAX_ATTACHMENT_MB") or str(MAX_ATTACHMENT_MB_DEFAULT)
    try:
        return int(float(raw) * 1024 * 1024)
    except ValueError as exc:
        raise _tag(
            RuntimeError(f"SERVICENOW_MAX_ATTACHMENT_MB is not a number: {raw!r}"),
            "config",
        ) from exc


def _tag(
    exc: Exception,
    error_class: str,
    http_status: int | None = None,
    hint: str | None = None,
) -> Exception:
    # No custom exception classes (repo convention) - instead the error class,
    # HTTP status, and hint ride on the builtin exception instance so the CLI
    # can emit a structured error envelope for the agent to branch on.
    exc.error_class = error_class  # type: ignore[attr-defined]
    exc.http_status = http_status  # type: ignore[attr-defined]
    exc.hint = hint  # type: ignore[attr-defined]
    return exc


# ---------------------------------------------------------------------------
# Auth seam (basic today; OAuth slots in as one new branch, zero call-site edits)


def _basic_auth_headers() -> dict[str, str]:
    token = base64.b64encode(
        f"{require_env('SERVICENOW_USERNAME')}:{require_env('SERVICENOW_PASSWORD')}".encode()
    ).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _auth_headers() -> dict[str, str]:
    mode = (os.getenv("SERVICENOW_AUTH") or "basic").strip().lower()
    if mode == "basic":
        return _basic_auth_headers()
    raise _tag(
        RuntimeError(
            f"Unsupported SERVICENOW_AUTH mode {mode!r}; only 'basic' is implemented. "
            "OAuth support is a planned follow-up."
        ),
        "config",
    )


# ---------------------------------------------------------------------------
# Transport


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]  # lower-cased keys
    body: bytes


# Module-level so tests can monkeypatch a recording fake here - the single seam
# through which every byte of network traffic flows.
def _http_send(
    request: urllib.request.Request,
    timeout: float,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> HttpResponse:
    host = urllib.parse.urlsplit(request.full_url).netloc
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read(max_bytes + 1)
            headers = {k.lower(): v for k, v in resp.headers.items()}
            status = resp.status
    except urllib.error.HTTPError as exc:
        # HTTPError carries status/headers/body; normalizing it into a response
        # keeps retry and error-translation logic in one place.
        body = exc.read()
        headers = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}
        return HttpResponse(status=exc.code, headers=headers, body=body)
    except TimeoutError as exc:
        raise _tag(
            RuntimeError(
                f"Request to {host} timed out after {timeout:g}s. Raise "
                "SERVICENOW_TIMEOUT_SECONDS or --timeout, or narrow the request."
            ),
            "unavailable",
        ) from exc
    except urllib.error.URLError as exc:
        raise _tag(
            RuntimeError(
                f"Cannot reach {host}: {exc.reason}. Check SERVICENOW_INSTANCE and "
                "your network."
            ),
            "unavailable",
        ) from exc
    if len(body) > max_bytes:
        raise _tag(
            RuntimeError(
                f"Response from {host} exceeded {max_bytes // (1024 * 1024)} MiB; "
                "narrow the request with --fields or --limit."
            ),
            "validation",
        )
    return HttpResponse(status=status, headers=headers, body=body)


_sleep = time.sleep  # alias so tests can record retry delays instead of sleeping


def _retry_delay(resp: HttpResponse, attempt: int) -> float:
    retry_after = resp.headers.get("retry-after")
    if retry_after:
        try:
            return min(float(retry_after), RETRY_AFTER_CAP)
        except ValueError:
            pass  # garbage header - fall through to plain backoff
    return float(attempt)  # 1s after the first attempt, 2s after the second


def _translate_http_error(
    resp: HttpResponse, context: str, table: str | None = None
) -> Exception:
    message = ""
    detail = ""
    try:
        parsed = json.loads(resp.body.decode("utf-8", "replace"))
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict):
            message = str(err.get("message") or "").strip()
            detail = str(err.get("detail") or "").strip()
    except json.JSONDecodeError:
        pass
    if not message:
        message = resp.body.decode("utf-8", "replace")[:500].strip() or "no error body"
    text = f"ServiceNow request failed ({resp.status}) during {context}: {message}"
    if detail and detail != message:
        text += f" | {detail}"

    plugin_hint = ""
    cfg = TABLES.get(table or "")
    if cfg is not None and cfg.plugin and resp.status in (403, 404):
        plugin_hint = (
            f" Table {cfg.table} requires the {cfg.plugin} plugin and matching "
            "roles for this account."
        )

    if resp.status == 400:
        return _tag(
            ValueError(f"{text} Fix the query, fields, or payload."), "validation", 400
        )
    if resp.status == 401:
        return _tag(
            RuntimeError(
                f"{text} Check SERVICENOW_USERNAME/SERVICENOW_PASSWORD, and whether "
                "the account is locked out."
            ),
            "auth",
            401,
        )
    if resp.status == 403:
        return _tag(
            RuntimeError(
                f"{text}{plugin_hint} The service account lacks an ACL or role for "
                "this; grant it in ServiceNow."
            ),
            "forbidden",
            403,
        )
    if resp.status == 404:
        return _tag(
            ValueError(f"{text}{plugin_hint} Check the table name and identifier."),
            "not_found",
            404,
        )
    if resp.status == 429:
        retry_after = resp.headers.get("retry-after")
        hint = (
            f"Rate limited; retry after {retry_after}s."
            if retry_after
            else "Rate limited; retry later."
        )
        return _tag(RuntimeError(f"{text} {hint}"), "rate_limited", 429, hint)
    if resp.status >= 500:
        return _tag(
            RuntimeError(f"{text} The instance is unavailable; retry later."),
            "unavailable",
            resp.status,
        )
    return _tag(RuntimeError(text), "remote", resp.status)


class ServiceNowClient:
    """Thin sync client over the ServiceNow REST APIs (Table, Aggregate, Attachment).

    Every request flows through :meth:`_request` - the one choke point where
    write guardrails (read-only refusal, dry-run preview) are enforced, so no
    higher layer can bypass them.
    """

    def __init__(
        self,
        base_url: str,
        auth_provider: Callable[[], dict[str, str]],
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth_provider = auth_provider
        self.timeout = timeout

    # -- core -------------------------------------------------------------

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        result, _resp = self._request(method, path, **kwargs)
        return result

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
        table: str | None = None,
        dry_run: bool = False,
        max_bytes: int = MAX_RESPONSE_BYTES,
        parse: bool = True,
    ) -> tuple[Any, HttpResponse | None]:
        method = method.upper()
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)

        if method != "GET":
            if _read_only():
                raise _tag(
                    RuntimeError(
                        f"Refusing {method} {path}: SERVICENOW_READ_ONLY is set. "
                        "Unset it to allow writes."
                    ),
                    "guarded",
                )
            if table and _write_denied(table):
                raise _tag(
                    ValueError(
                        f"Refusing {method} on {table!r}: writes to platform-"
                        "configuration tables are blocked by this client."
                    ),
                    "guarded",
                )
            if dry_run:
                payload: Any = json_body
                if payload is None and raw_body is not None:
                    payload = f"<binary body, {len(raw_body)} bytes>"
                return (
                    {"dry_run": True, "method": method, "url": url, "payload": payload},
                    None,
                )

        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif raw_body is not None:
            data = raw_body
            headers["Content-Type"] = content_type or "application/octet-stream"
        headers.update(self._auth_provider())

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        _log(f"-> {method} {url}")
        attempt = 0
        while True:
            attempt += 1
            resp = _http_send(req, self.timeout, max_bytes)
            # A 429 was never executed server-side so any method may retry; a
            # retried POST after a 5xx could duplicate a ticket, so 5xx retries
            # are GET-only.
            retryable = resp.status == 429 or (
                resp.status in (502, 503, 504) and method == "GET"
            )
            if retryable and attempt < RETRY_ATTEMPTS:
                _sleep(_retry_delay(resp, attempt))
                continue
            break
        _log(f"<- {resp.status} ({len(resp.body)} bytes)")

        if 200 <= resp.status < 300:
            if not parse:
                return resp.body, resp
            return self._parse_success(resp, url), resp
        raise _translate_http_error(resp, f"{method} {path}", table)

    @staticmethod
    def _parse_success(resp: HttpResponse, url: str) -> Any:
        if resp.status == 204 or not resp.body:
            return None
        if "json" not in resp.headers.get("content-type", ""):
            # Developer instances answer with an HTML "hibernating" page and a
            # 200 - the most confusing failure this client can hit.
            raise _tag(
                RuntimeError(
                    f"ServiceNow returned a non-JSON response (status {resp.status}) "
                    f"from {url}. Developer instances hibernate when idle - wake the "
                    "instance at developer.servicenow.com and retry."
                ),
                "unavailable",
                resp.status,
            )
        try:
            parsed = json.loads(resp.body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise _tag(
                RuntimeError(f"ServiceNow returned unparseable JSON from {url}: {exc}"),
                "remote",
                resp.status,
            ) from exc
        if isinstance(parsed, dict) and "result" in parsed:
            return parsed["result"]
        return parsed

    # -- Table API --------------------------------------------------------

    def get_record(
        self,
        table: str,
        sys_id: str,
        *,
        fields: Sequence[str] | None = None,
        display: str = "both",
    ) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/api/now/table/{table}/{sys_id}",
            params=_table_params(fields=fields, display=display),
            table=table,
        )

    def query(
        self,
        table: str,
        *,
        query: str | None = None,
        fields: Sequence[str] | None = None,
        display: str = "display",
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        no_count: bool = False,
    ) -> tuple[list[dict[str, Any]], int | None]:
        params = _table_params(
            query=query,
            fields=fields,
            display=display,
            limit=limit,
            offset=offset,
            no_count=no_count,
        )
        result, resp = self._request(
            "GET", f"/api/now/table/{table}", params=params, table=table
        )
        rows = result if isinstance(result, list) else []
        total: int | None = None
        header = (resp.headers.get("x-total-count") or "") if resp else ""
        if header.isdigit():
            total = int(header)
        return rows, total

    def query_all(
        self,
        table: str,
        *,
        query: str | None = None,
        fields: Sequence[str] | None = None,
        display: str = "display",
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        max_records: int = MAX_RECORDS_CAP,
    ) -> tuple[list[dict[str, Any]], int | None, bool]:
        # _table_params caps the wire request at MAX_LIMIT; clamp here too so
        # the loop's page-size comparisons and cursor stride match what the
        # server can actually return.
        limit = min(limit, MAX_LIMIT)
        rows: list[dict[str, Any]] = []
        total: int | None = None
        cursor = offset
        while True:
            # Counting is expensive server-side; only the first page pays for it.
            page, page_total = self.query(
                table,
                query=query,
                fields=fields,
                display=display,
                limit=limit,
                offset=cursor,
                no_count=bool(rows),
            )
            if not rows:
                total = page_total
            rows.extend(page)
            if len(page) < limit or len(rows) >= max_records:
                truncated = len(rows) > max_records or (
                    len(page) == limit and len(rows) >= max_records
                )
                return rows[:max_records], total, truncated
            cursor += limit

    def create(
        self,
        table: str,
        payload: dict[str, Any],
        *,
        input_display: bool = False,
        fields: Sequence[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/api/now/table/{table}",
            params=_table_params(
                fields=fields, display="both", input_display=input_display
            ),
            json_body=payload,
            table=table,
            dry_run=dry_run,
        )

    def update(
        self,
        table: str,
        sys_id: str,
        payload: dict[str, Any],
        *,
        input_display: bool = False,
        fields: Sequence[str] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return self.request(
            "PATCH",
            f"/api/now/table/{table}/{sys_id}",
            params=_table_params(
                fields=fields, display="both", input_display=input_display
            ),
            json_body=payload,
            table=table,
            dry_run=dry_run,
        )

    def delete(self, table: str, sys_id: str, *, dry_run: bool = False) -> Any:
        return self.request(
            "DELETE", f"/api/now/table/{table}/{sys_id}", table=table, dry_run=dry_run
        )

    # -- Aggregate API ----------------------------------------------------

    def count(self, table: str, query: str | None = None) -> int:
        params: dict[str, str] = {"sysparm_count": "true"}
        if query:
            params["sysparm_query"] = query
        result = self.request(
            "GET", f"/api/now/stats/{table}", params=params, table=table
        )
        try:
            return int(result["stats"]["count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise _tag(
                RuntimeError(
                    f"Aggregate API returned an unexpected shape for {table}: "
                    f"{result!r}. If the account lacks Aggregate API access, use "
                    "`query --limit 1` and read `total` instead."
                ),
                "remote",
            ) from exc

    # -- Attachment API ---------------------------------------------------

    def list_attachments(self, table: str, sys_id: str) -> list[dict[str, Any]]:
        params = {
            "sysparm_query": f"table_name={table}^table_sys_id={sys_id}",
            "sysparm_limit": "100",
        }
        result = self.request("GET", "/api/now/attachment", params=params, table=table)
        return result if isinstance(result, list) else []

    def upload_attachment(
        self, table: str, sys_id: str, path: Path, *, dry_run: bool = False
    ) -> dict[str, Any]:
        if not path.is_file():
            raise _tag(ValueError(f"No such file: {path}"), "usage")
        size = path.stat().st_size
        cap = _attachment_cap_bytes()
        if size > cap:
            raise _tag(
                ValueError(
                    f"{path.name} is {size} bytes, over the "
                    f"{cap // (1024 * 1024)} MiB cap (SERVICENOW_MAX_ATTACHMENT_MB)."
                ),
                "validation",
            )
        params = {"table_name": table, "table_sys_id": sys_id, "file_name": path.name}
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return self.request(
            "POST",
            "/api/now/attachment/file",
            params=params,
            raw_body=path.read_bytes(),
            content_type=content_type,
            table=table,
            dry_run=dry_run,
        )

    def download_attachment(self, attachment_sys_id: str, dest_dir: Path) -> Path:
        meta = self.request("GET", f"/api/now/attachment/{attachment_sys_id}")
        file_name = _safe_file_name(
            str((meta or {}).get("file_name") or ""), fallback=attachment_sys_id
        )
        body, _resp = self._request(
            "GET",
            f"/api/now/attachment/{attachment_sys_id}/file",
            max_bytes=_attachment_cap_bytes(),
            parse=False,
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_path(dest_dir, file_name)
        target.write_bytes(body)
        return target


def _safe_file_name(name: str, fallback: str) -> str:
    # The attachment's file_name is remote-user-controlled metadata (a caller
    # picks it freely at upload time). Reduce it to a bare basename so
    # "..\\..\\evil.bat" or an absolute path cannot escape the destination
    # directory, and strip characters Windows rejects so write_bytes cannot
    # raise an uncaught OSError.
    base = os.path.basename(name.replace("\\", "/"))
    base = re.sub(r'[<>:"|?*]', "_", base).strip()
    if not base or base in {".", ".."}:
        return fallback
    return base


def _unique_path(directory: Path, file_name: str) -> Path:
    # Never overwrite an existing download - suffix with (1), (2), ...
    target = directory / file_name
    stem, ext = os.path.splitext(file_name)
    counter = 1
    while target.exists():
        target = directory / f"{stem} ({counter}){ext}"
        counter += 1
    return target


def _write_denied(table: str) -> bool:
    return any(table.startswith(prefix) for prefix in WRITE_DENYLIST)


@lru_cache(maxsize=None)
def _client() -> ServiceNowClient:
    # Cached process-lifetime singleton; also the coarse test seam (tests clear
    # the cache and monkeypatch _http_send underneath it).
    timeout = os.getenv("SERVICENOW_TIMEOUT_SECONDS")
    return ServiceNowClient(
        base_url=_base_url(),
        auth_provider=_auth_headers,
        timeout=float(timeout) if timeout else DEFAULT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Query building and identifier detection


def _table_params(
    *,
    query: str | None = None,
    fields: Sequence[str] | None = None,
    display: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    no_count: bool = False,
    input_display: bool = False,
) -> dict[str, str]:
    # Reference "link" URLs are pure noise for an agent, so they are always
    # excluded.
    params: dict[str, str] = {"sysparm_exclude_reference_link": "true"}
    if query:
        params["sysparm_query"] = query
    if fields:
        params["sysparm_fields"] = ",".join(fields)
    if display:
        if display not in DISPLAY_MODES:
            raise _tag(
                ValueError(
                    f"Unknown display mode {display!r}; expected one of {DISPLAY_MODES}."
                ),
                "usage",
            )
        params["sysparm_display_value"] = {
            "value": "false",
            "display": "true",
            "both": "all",
        }[display]
    if limit is not None:
        params["sysparm_limit"] = str(min(limit, MAX_LIMIT))
    if offset:
        params["sysparm_offset"] = str(offset)
    if no_count:
        params["sysparm_no_count"] = "true"
    if input_display:
        params["sysparm_input_display_value"] = "true"
    return params


def detect_table(identifier: str, table: str | None = None) -> tuple[str, bool]:
    """Return ``(table, is_sys_id)`` for a record number or 32-hex sys_id."""
    is_sys_id = bool(SYS_ID_RE.match(identifier))
    if table:
        return resolve_table(table), is_sys_id
    if is_sys_id:
        raise _tag(
            ValueError(
                f"Cannot infer a table from sys_id {identifier!r}; pass --table <name>."
            ),
            "usage",
        )
    upper = identifier.upper()
    # Longest prefix first so e.g. SCTASK never falls into a shorter overlap.
    for prefix in sorted(PREFIXES, key=len, reverse=True):
        if upper.startswith(prefix):
            return PREFIXES[prefix], False
    known = ", ".join(sorted(PREFIXES))
    raise _tag(
        ValueError(
            f"Cannot infer a table from {identifier!r}; pass --table <name>. "
            f"Known number prefixes: {known}."
        ),
        "usage",
    )


def _resolve_sys_id(client: ServiceNowClient, table: str, identifier: str) -> str:
    if SYS_ID_RE.match(identifier):
        return identifier
    rows, _total = client.query(
        table,
        query=f"number={identifier}",
        fields=("sys_id", "number"),
        display="value",
        limit=2,
    )
    if not rows:
        raise _tag(
            ValueError(f"No {table} record with number {identifier}."), "not_found"
        )
    if len(rows) > 1:
        raise _tag(
            ValueError(
                f"Number {identifier} matches multiple {table} records; use the sys_id."
            ),
            "validation",
        )
    return str(_raw_value(rows[0].get("sys_id")))


def _since(spec: str) -> str:
    match = DURATION_RE.match(spec.strip().lower())
    if not match:
        raise _tag(
            ValueError(
                f"Bad duration {spec!r}; use <N>d, <N>h, or <N>m (e.g. 7d, 12h)."
            ),
            "usage",
        )
    amount, unit = int(match.group(1)), match.group(2)
    delta = {"d": timedelta(days=amount), "h": timedelta(hours=amount), "m": timedelta(minutes=amount)}[unit]
    # ServiceNow stores datetimes in UTC, so an absolute UTC bound is portable
    # across instances - unlike RELATIVE*/javascript: operators.
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%d %H:%M:%S")


def _ref_term(reference_field: str, term: str) -> str:
    if SYS_ID_RE.match(term):
        return f"{reference_field}={term}"
    if "@" in term:
        return f"{reference_field}.email={term}"
    if reference_field == "assigned_to" and " " not in term:
        return f"{reference_field}.user_name={term}"
    return f"{reference_field}.name={term}"


# ---------------------------------------------------------------------------
# Record-type registry (pure data; instance quirks are one-row fixes)


@dataclass(frozen=True)
class TableConfig:
    table: str
    label: str
    number_prefix: str | None
    aliases: tuple[str, ...]
    default_fields: tuple[str, ...]
    journal_fields: tuple[str, ...] = JOURNAL_FIELDS
    state_field: str = "state"
    transitions: dict[str, dict[str, str]] = field(default_factory=dict)
    transition_required: dict[str, tuple[str, ...]] = field(default_factory=dict)
    close_code_field: str | None = None
    close_notes_field: str | None = None
    plugin: str | None = None


TABLES: dict[str, TableConfig] = {
    cfg.table: cfg
    for cfg in (
        TableConfig(
            table="incident",
            label="Incident",
            number_prefix="INC",
            aliases=("inc",),
            default_fields=(
                "number", "sys_id", "short_description", "description", "state",
                "priority", "impact", "urgency", "category", "assignment_group",
                "assigned_to", "caller_id", "opened_at", "resolved_at",
                "close_code", "close_notes", "sys_updated_on",
            ),
            transitions={"resolve": {"state": "6"}, "close": {"state": "7"}},
            transition_required={"resolve": ("code", "notes")},
            close_code_field="close_code",
            close_notes_field="close_notes",
        ),
        TableConfig(
            table="change_request",
            label="Change Request",
            number_prefix="CHG",
            aliases=("change", "chg"),
            default_fields=(
                "number", "sys_id", "short_description", "description", "state",
                "type", "risk", "impact", "priority", "approval",
                "assignment_group", "assigned_to", "requested_by",
                "start_date", "end_date", "sys_updated_on",
            ),
            # State codes are change-model dependent; "3" is the stock Closed.
            # Verify per instance (see README) or use `update` + `choices`.
            transitions={"close": {"state": "3"}},
            close_code_field="close_code",
            close_notes_field="close_notes",
        ),
        TableConfig(
            table="change_task",
            label="Change Task",
            number_prefix="CTASK",
            aliases=("ctask", "change-task"),
            default_fields=(
                "number", "sys_id", "short_description", "description", "state",
                "change_request", "assignment_group", "assigned_to",
                "planned_start_date", "planned_end_date", "sys_updated_on",
            ),
            transitions={"close": {"state": "3"}},
        ),
        TableConfig(
            table="sc_request",
            label="Request",
            number_prefix="REQ",
            aliases=("request", "req"),
            default_fields=(
                "number", "sys_id", "short_description", "description",
                "request_state", "stage", "approval", "requested_for",
                "opened_by", "opened_at", "sys_updated_on",
            ),
            state_field="request_state",
            # sc_request state largely derives from its RITMs; verify on instance.
            transitions={"close": {"request_state": "closed_complete"}},
        ),
        TableConfig(
            table="sc_req_item",
            label="Requested Item",
            number_prefix="RITM",
            aliases=("ritm",),
            default_fields=(
                "number", "sys_id", "short_description", "description", "state",
                "stage", "approval", "request", "cat_item", "requested_for",
                "opened_at", "assignment_group", "assigned_to", "sys_updated_on",
            ),
            transitions={"close": {"state": "3"}},
        ),
        TableConfig(
            table="sc_task",
            label="Catalog Task",
            number_prefix="SCTASK",
            aliases=("sctask", "catalog-task"),
            default_fields=(
                "number", "sys_id", "short_description", "description", "state",
                "request", "request_item", "assignment_group", "assigned_to",
                "opened_at", "sys_updated_on",
            ),
            transitions={"close": {"state": "3"}},
        ),
        TableConfig(
            table="problem",
            label="Problem",
            number_prefix="PRB",
            aliases=("prb",),
            default_fields=(
                "number", "sys_id", "short_description", "description", "state",
                "priority", "impact", "urgency", "assignment_group",
                "assigned_to", "opened_at", "sys_updated_on",
            ),
            # Problem state models vary too much to ship transitions; use
            # `update` with `choices problem state`.
        ),
        TableConfig(
            table="sn_vul_vulnerable_item",
            label="Vulnerable Item",
            number_prefix="VIT",
            aliases=("vit", "vulnerable-item", "vul"),
            default_fields=(
                "number", "sys_id", "short_description", "state", "substate",
                "vulnerability", "cmdb_ci", "risk_score", "risk_rating",
                "assignment_group", "assigned_to", "first_found", "last_found",
                "remediation_target", "sys_updated_on",
            ),
            # VITs are worked through work notes; customer comments don't apply.
            journal_fields=("work_notes",),
            # VR state values vary by release - transitions deliberately empty;
            # use `update` guided by `choices sn_vul_vulnerable_item state`.
            plugin="Vulnerability Response (com.snc.vulnerability)",
        ),
    )
}

PREFIXES: dict[str, str] = {
    cfg.number_prefix: cfg.table for cfg in TABLES.values() if cfg.number_prefix
}

ALIASES: dict[str, str] = {}
for _cfg in TABLES.values():
    ALIASES[_cfg.table] = _cfg.table
    for _alias in _cfg.aliases:
        ALIASES[_alias] = _cfg.table


def resolve_table(name: str) -> str:
    key = name.strip().lower()
    return ALIASES.get(key, key)


def config_for(table: str) -> TableConfig:
    # Unknown tables get a synthesized generic config: every verb except the
    # registered state transitions works on them.
    return TABLES.get(table) or TableConfig(
        table=table, label=table, number_prefix=None, aliases=(), default_fields=()
    )


def registry() -> dict[str, Any]:
    return {name: asdict(cfg) for name, cfg in TABLES.items()}


# ---------------------------------------------------------------------------
# Output shaping


def _raw_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def _iso(value: Any) -> Any:
    # Raw Table API datetimes are UTC "yyyy-MM-dd HH:mm:ss"; make that explicit.
    if isinstance(value, str) and DATETIME_RE.match(value):
        return value.replace(" ", "T") + "Z"
    return value


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value and "display_value" in value:
        raw = _iso(value.get("value"))
        display = value.get("display_value")
        if raw == display or display in (None, ""):
            return raw
        if raw in (None, ""):
            return display
        return {"value": raw, "display": display}
    return _iso(value)


_KEY_PRIORITY = (
    "sys_id", "number", "short_description", "description", "state",
    "request_state", "priority", "impact", "urgency",
    "assignment_group", "assigned_to",
)


def _ordered(record: dict[str, Any]) -> dict[str, Any]:
    # LLMs anchor on early tokens: identity and load-bearing fields first,
    # sys_* metadata last, the deep link at the very end.
    def sort_key(key: str) -> tuple[int, Any]:
        if key in _KEY_PRIORITY:
            return (0, _KEY_PRIORITY.index(key))
        if key == "url":
            return (3, "")
        if key.startswith("sys_"):
            return (2, key)
        return (1, key)

    return {key: record[key] for key in sorted(record, key=sort_key)}


def _normalize_record(
    record: dict[str, Any], table: str, base_url: str | None = None
) -> dict[str, Any]:
    out = {key: _normalize_value(value) for key, value in record.items()}
    sys_id = _raw_value(record.get("sys_id"))
    if sys_id and base_url:
        out["url"] = f"{base_url}/{table}.do?sys_id={sys_id}"
    return _ordered(out)


def _truncate_strings(obj: Any, max_chars: int) -> Any:
    if isinstance(obj, str):
        if len(obj) > max_chars:
            removed = len(obj) - max_chars
            return obj[:max_chars] + f"...[truncated {removed} chars]"
        return obj
    if isinstance(obj, dict):
        return {key: _truncate_strings(value, max_chars) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_truncate_strings(item, max_chars) for item in obj]
    return obj


def _with_identity_fields(fields: Sequence[str] | None) -> tuple[str, ...] | None:
    # A --fields subset must still carry sys_id/number or the deep link and
    # follow-up writes have nothing to anchor on.
    if not fields:
        return None
    out = list(fields)
    for required in ("number", "sys_id"):
        if required not in out:
            out.append(required)
    return tuple(out)


# ---------------------------------------------------------------------------
# Write verification
#
# ServiceNow answers 2xx even when an ACL silently drops a field or a business
# rule overwrites it - the response body reflects what was actually stored, so
# diffing it against the payload is the only honest success check.


def _ensure_writable() -> None:
    # Fail fast, before any lookup round-trip; ServiceNowClient._request checks
    # again at the send choke point as defense in depth.
    if _read_only():
        raise _tag(
            RuntimeError(
                "Refusing write: SERVICENOW_READ_ONLY is set. Unset it to allow writes."
            ),
            "guarded",
        )


def _fields_not_applied(
    payload: dict[str, Any], record: dict[str, Any]
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for key, requested in payload.items():
        if key in JOURNAL_FIELDS:
            continue  # journal writes are accepted but never echoed back
        # The Table API returns every value as a string: booleans come back as
        # "true"/"false" and cleared fields as "" - normalize the request side
        # or successful writes would be reported as blocked.
        if isinstance(requested, bool):
            wire_value = "true" if requested else "false"
        elif requested is None:
            wire_value = ""
        else:
            wire_value = str(requested)
        stored = record.get(key)
        if isinstance(stored, dict):
            candidates = {stored.get("value"), stored.get("display_value")}
        else:
            candidates = {stored}
        # Compare against both raw and display so a display-value input or a
        # reference passed by name doesn't false-positive.
        if wire_value not in {str(c) for c in candidates if c is not None}:
            mismatches.append(
                {"field": key, "requested": requested, "actual": _raw_value(stored)}
            )
    return mismatches


# ---------------------------------------------------------------------------
# High-level operations (the importable API; every CLI verb maps onto one)


def whoami() -> dict[str, Any]:
    client = _client()
    user = require_env("SERVICENOW_USERNAME")
    rows, _total = client.query(
        "sys_user",
        query=f"user_name={user}",
        fields=("sys_id", "user_name", "name", "email", "title", "locked_out", "time_zone"),
        display="both",
        limit=1,
    )
    if not rows:
        raise _tag(
            RuntimeError(
                f"Authenticated, but no sys_user row is visible for {user!r}. The "
                "account may lack read access to sys_user; the connection itself works."
            ),
            "forbidden",
        )
    result = _normalize_record(rows[0], "sys_user", client.base_url)
    result["instance"] = client.base_url
    return result


def get_record(
    identifier: str,
    *,
    table: str | None = None,
    fields: Sequence[str] | None = None,
    display: str = "both",
) -> dict[str, Any]:
    client = _client()
    tbl, is_sys_id = detect_table(identifier, table)
    cfg = config_for(tbl)
    flds = _with_identity_fields(fields) or cfg.default_fields or None
    if is_sys_id:
        record = client.get_record(tbl, identifier, fields=flds, display=display)
    else:
        rows, _total = client.query(
            tbl, query=f"number={identifier}", fields=flds, display=display, limit=2
        )
        if not rows:
            raise _tag(
                ValueError(f"No {tbl} record with number {identifier}."), "not_found"
            )
        if len(rows) > 1:
            raise _tag(
                ValueError(
                    f"Number {identifier} matches multiple {tbl} records; use the sys_id."
                ),
                "validation",
            )
        record = rows[0]
    return _normalize_record(record, tbl, client.base_url)


def query_records(
    table: str,
    *,
    query: str | None = None,
    fields: Sequence[str] | None = None,
    display: str = "display",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    fetch_all: bool = False,
    max_records: int = MAX_RECORDS_CAP,
) -> dict[str, Any]:
    client = _client()
    tbl = resolve_table(table)
    cfg = config_for(tbl)
    flds = _with_identity_fields(fields) or cfg.default_fields or None
    # The wire request is capped at MAX_LIMIT; keep the has_more heuristic
    # below comparing against the page size actually requested.
    limit = min(limit, MAX_LIMIT)
    truncated = False
    if fetch_all:
        rows, total, truncated = client.query_all(
            tbl,
            query=query,
            fields=flds,
            display=display,
            limit=limit,
            offset=offset,
            max_records=max_records,
        )
    else:
        rows, total = client.query(
            tbl, query=query, fields=flds, display=display, limit=limit, offset=offset
        )
    records = [_normalize_record(row, tbl, client.base_url) for row in rows]
    if total is not None:
        has_more = offset + len(records) < total
    else:
        has_more = truncated or (not fetch_all and len(records) == limit)
    return {
        "table": tbl,
        "records": records,
        "count": len(records),
        "total": total,
        "has_more": has_more,
        "next_offset": offset + len(records) if has_more else None,
        "truncated": truncated,
    }


def count_records(table: str, *, query: str | None = None) -> dict[str, Any]:
    tbl = resolve_table(table)
    return {"table": tbl, "query": query, "count": _client().count(tbl, query)}


def create_record(
    table: str,
    fields: dict[str, Any],
    *,
    input_display: bool = False,
    correlation_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not fields:
        raise _tag(
            ValueError("No fields given; pass --field key=value or --data '{...}'."),
            "usage",
        )
    _ensure_writable()
    client = _client()
    tbl = resolve_table(table)
    cfg = config_for(tbl)
    payload = dict(fields)
    if correlation_id:
        # correlation_id exists on task-family tables for exactly this: making
        # a retried create findable instead of a duplicate.
        payload.setdefault("correlation_id", correlation_id)
        dedupe_fields = tuple(cfg.default_fields) + ("correlation_id",)
        existing, _total = client.query(
            tbl,
            query=f"correlation_id={correlation_id}",
            fields=_with_identity_fields(dedupe_fields),
            display="both",
            limit=1,
        )
        # Trust the hit only if the row really carries the key: on a table
        # without a correlation_id column the query term is invalid and (by
        # default) matches every row - returning an unrelated record here
        # would be far worse than a duplicate create.
        if existing:
            stored = existing[0].get("correlation_id")
            if isinstance(stored, dict):
                candidates = {stored.get("value"), stored.get("display_value")}
            else:
                candidates = {stored}
            if correlation_id in {str(c) for c in candidates if c is not None}:
                result = _normalize_record(existing[0], tbl, client.base_url)
                result["deduplicated"] = True
                return result
    response_fields = _response_fields(cfg, payload)
    record = client.create(
        tbl,
        payload,
        input_display=input_display,
        fields=response_fields,
        dry_run=dry_run,
    )
    return _shape_write_result(record, payload, tbl, client.base_url)


def update_record(
    identifier: str,
    fields: dict[str, Any],
    *,
    table: str | None = None,
    input_display: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not fields:
        raise _tag(
            ValueError("No fields given; pass --field key=value or --data '{...}'."),
            "usage",
        )
    tbl, _is_sys_id = detect_table(identifier, table)
    return _apply_update(
        tbl, identifier, dict(fields), input_display=input_display, dry_run=dry_run
    )


def _apply_update(
    table: str,
    identifier: str,
    payload: dict[str, Any],
    *,
    input_display: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    _ensure_writable()
    client = _client()
    cfg = config_for(table)
    sys_id = _resolve_sys_id(client, table, identifier)
    record = client.update(
        table,
        sys_id,
        payload,
        input_display=input_display,
        fields=_response_fields(cfg, payload),
        dry_run=dry_run,
    )
    return _shape_write_result(record, payload, table, client.base_url)


def _response_fields(cfg: TableConfig, payload: dict[str, Any]) -> tuple[str, ...] | None:
    # Ask the write response for the default field set plus everything we sent,
    # so the output stays tight but write verification can still diff the payload.
    if not cfg.default_fields:
        return None
    out = list(cfg.default_fields)
    for key in payload:
        if key not in out:
            out.append(key)
    return tuple(_with_identity_fields(out) or ())


def _shape_write_result(
    record: Any, payload: dict[str, Any], table: str, base_url: str
) -> dict[str, Any]:
    if isinstance(record, dict) and record.get("dry_run"):
        return record
    if not isinstance(record, dict):
        raise _tag(
            RuntimeError(f"Write to {table} returned an unexpected body: {record!r}"),
            "remote",
        )
    not_applied = _fields_not_applied(payload, record)
    result = _normalize_record(record, table, base_url)
    if not_applied:
        result["fields_not_applied"] = not_applied
        print(
            f"warning: {len(not_applied)} field(s) were not applied as sent "
            "(blocked by an ACL or overwritten by a business rule): "
            + ", ".join(m["field"] for m in not_applied),
            file=sys.stderr,
        )
    return result


def add_comment(
    identifier: str, text: str, *, table: str | None = None, dry_run: bool = False
) -> dict[str, Any]:
    return _add_journal(identifier, text, "comments", table=table, dry_run=dry_run)


def add_work_note(
    identifier: str, text: str, *, table: str | None = None, dry_run: bool = False
) -> dict[str, Any]:
    return _add_journal(identifier, text, "work_notes", table=table, dry_run=dry_run)


def _add_journal(
    identifier: str,
    text: str,
    journal_field: str,
    *,
    table: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    if not text.strip():
        raise _tag(ValueError("Note text is empty."), "usage")
    tbl, _is_sys_id = detect_table(identifier, table)
    cfg = config_for(tbl)
    if journal_field not in cfg.journal_fields:
        alternative = cfg.journal_fields[0] if cfg.journal_fields else None
        hint = f" Use `work-note` instead." if alternative == "work_notes" else ""
        raise _tag(
            ValueError(
                f"Table {tbl} does not support the {journal_field!r} journal field.{hint}"
            ),
            "validation",
        )
    return _apply_update(tbl, identifier, {journal_field: text}, dry_run=dry_run)


def get_notes(
    identifier: str,
    *,
    table: str | None = None,
    note_type: str = "all",
    limit: int = 50,
) -> list[dict[str, Any]]:
    if note_type not in ("all", "comments", "work_notes"):
        raise _tag(
            ValueError(
                f"Unknown note type {note_type!r}; expected comments, work_notes, or all."
            ),
            "usage",
        )
    client = _client()
    tbl, _is_sys_id = detect_table(identifier, table)
    cfg = config_for(tbl)
    sys_id = _resolve_sys_id(client, tbl, identifier)
    elements = cfg.journal_fields if note_type == "all" else (note_type,)
    # Journal rows record the table class of the code path that wrote them;
    # entries written through task-level logic land under name=task, so match
    # the hierarchy like get_choices/get_schema do.
    query = (
        f"nameIN{tbl},task^element_id={sys_id}^elementIN{','.join(elements)}"
        "^ORDERBYDESCsys_created_on"
    )
    try:
        rows, _total = client.query(
            "sys_journal_field",
            query=query,
            fields=("sys_created_on", "sys_created_by", "element", "value"),
            display="value",
            limit=limit,
        )
    except RuntimeError as exc:
        raise _journal_acl_error(exc, "sys_journal_field") from exc
    return [
        {
            "when": _iso(_raw_value(row.get("sys_created_on"))),
            "by": _raw_value(row.get("sys_created_by")),
            "type": _raw_value(row.get("element")),
            "text": _raw_value(row.get("value")),
        }
        for row in rows
    ]


def get_history(
    identifier: str,
    *,
    table: str | None = None,
    field_name: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    client = _client()
    tbl, _is_sys_id = detect_table(identifier, table)
    sys_id = _resolve_sys_id(client, tbl, identifier)
    query = f"tablename={tbl}^documentkey={sys_id}"
    if field_name:
        query += f"^fieldname={field_name}"
    query += "^ORDERBYDESCsys_created_on"
    try:
        rows, _total = client.query(
            "sys_audit",
            query=query,
            fields=("sys_created_on", "user", "fieldname", "oldvalue", "newvalue"),
            display="value",
            limit=limit,
        )
    except RuntimeError as exc:
        raise _journal_acl_error(exc, "sys_audit") from exc
    return [
        {
            "when": _iso(_raw_value(row.get("sys_created_on"))),
            "by": _raw_value(row.get("user")),
            "field": _raw_value(row.get("fieldname")),
            "old": _raw_value(row.get("oldvalue")),
            "new": _raw_value(row.get("newvalue")),
        }
        for row in rows
    ]


def _journal_acl_error(exc: RuntimeError, audit_table: str) -> Exception:
    # Instances frequently ACL-restrict these tables to admins; explain the fix
    # instead of surfacing a bare 403.
    if getattr(exc, "http_status", None) == 403:
        return _tag(
            RuntimeError(
                f"{exc} Reading {audit_table} requires a read ACL for this account; "
                "grant it, or fetch the record with `get --display both` and rely on "
                "created/updated metadata."
            ),
            "forbidden",
            403,
        )
    return exc


def resolve_record(
    identifier: str,
    *,
    table: str | None = None,
    code: str | None = None,
    notes: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _transition("resolve", identifier, table=table, code=code, notes=notes, dry_run=dry_run)


def close_record(
    identifier: str,
    *,
    table: str | None = None,
    code: str | None = None,
    notes: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _transition("close", identifier, table=table, code=code, notes=notes, dry_run=dry_run)


def _transition(
    verb: str,
    identifier: str,
    *,
    table: str | None,
    code: str | None,
    notes: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    tbl, _is_sys_id = detect_table(identifier, table)
    cfg = config_for(tbl)
    template = cfg.transitions.get(verb)
    if template is None:
        raise _tag(
            ValueError(
                f"No {verb} transition is registered for table {tbl!r}; use `update` "
                f"with explicit values (see `choices {tbl} {cfg.state_field}`)."
            ),
            "validation",
        )
    required = cfg.transition_required.get(verb, ())
    if "code" in required and not code:
        raise _tag(
            ValueError(f"{verb} on {tbl} requires --code (the close code)."), "usage"
        )
    if "notes" in required and not notes:
        raise _tag(
            ValueError(f"{verb} on {tbl} requires --notes (the close notes)."), "usage"
        )
    payload = dict(template)
    if code:
        if not cfg.close_code_field:
            raise _tag(
                ValueError(f"Table {tbl} has no close-code field; drop --code."),
                "usage",
            )
        payload[cfg.close_code_field] = code
    if notes:
        if not cfg.close_notes_field:
            raise _tag(
                ValueError(
                    f"Table {tbl} has no close-notes field; drop --notes or use "
                    "`work-note` for commentary."
                ),
                "usage",
            )
        payload[cfg.close_notes_field] = notes
    return _apply_update(tbl, identifier, payload, dry_run=dry_run)


def assign_record(
    identifier: str,
    *,
    table: str | None = None,
    to: str | None = None,
    group: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not to and not group:
        raise _tag(
            ValueError("assign needs --to <user> and/or --group <group>."), "usage"
        )
    _ensure_writable()
    client = _client()
    tbl, _is_sys_id = detect_table(identifier, table)
    payload: dict[str, Any] = {}
    if to:
        payload["assigned_to"] = _resolve_user_sys_id(client, to)
    if group:
        payload["assignment_group"] = _resolve_group_sys_id(client, group)
    return _apply_update(tbl, identifier, payload, dry_run=dry_run)


def _resolve_user_sys_id(client: ServiceNowClient, term: str) -> str:
    if SYS_ID_RE.match(term):
        return term
    rows, _total = client.query(
        "sys_user",
        query=f"user_name={term}^ORemail={term}^ORname={term}",
        fields=("sys_id", "user_name", "name", "email"),
        display="value",
        limit=2,
    )
    if not rows:
        raise _tag(
            ValueError(f"No sys_user matches {term!r}; try `user {term}` to search."),
            "not_found",
        )
    if len(rows) > 1:
        raise _tag(
            ValueError(
                f"{term!r} matches multiple users; use `user {term}` and pass a sys_id."
            ),
            "validation",
        )
    return str(_raw_value(rows[0].get("sys_id")))


def _resolve_group_sys_id(client: ServiceNowClient, term: str) -> str:
    if SYS_ID_RE.match(term):
        return term
    rows, _total = client.query(
        "sys_user_group",
        query=f"name={term}",
        fields=("sys_id", "name"),
        display="value",
        limit=2,
    )
    if not rows:
        raise _tag(
            ValueError(
                f"No sys_user_group named {term!r}; try `group {term}` to search."
            ),
            "not_found",
        )
    if len(rows) > 1:
        raise _tag(
            ValueError(
                f"{term!r} matches multiple groups; use `group {term}` and pass a sys_id."
            ),
            "validation",
        )
    return str(_raw_value(rows[0].get("sys_id")))


def lookup_user(term: str, *, limit: int = 10) -> list[dict[str, Any]]:
    client = _client()
    rows, _total = client.query(
        "sys_user",
        query=f"user_name={term}^ORemail={term}^ORnameLIKE{term}",
        fields=("sys_id", "user_name", "name", "email", "title", "active"),
        display="value",
        limit=limit,
    )
    seen: set[str] = set()
    out = []
    for row in rows:
        sys_id = str(_raw_value(row.get("sys_id")))
        if sys_id in seen:
            continue
        seen.add(sys_id)
        out.append(_normalize_record(row, "sys_user", client.base_url))
    return out


def lookup_group(term: str, *, limit: int = 10) -> list[dict[str, Any]]:
    client = _client()
    rows, _total = client.query(
        "sys_user_group",
        query=f"nameLIKE{term}",
        fields=("sys_id", "name", "description", "manager", "active"),
        display="both",
        limit=limit,
    )
    return [_normalize_record(row, "sys_user_group", client.base_url) for row in rows]


def get_choices(
    table: str, field_name: str, *, language: str = "en"
) -> list[dict[str, Any]]:
    client = _client()
    tbl = resolve_table(table)
    # Choices for inherited fields (e.g. incident.state) can live under the
    # parent class; task covers every table this client treats as first-class.
    # sys_choice stores one row per language once I18N plugins are installed,
    # so the language filter keeps the list to one label per value.
    rows, _total = client.query(
        "sys_choice",
        query=(
            f"nameIN{tbl},task^element={field_name}^inactive=false"
            f"^language={language}^ORDERBYsequence"
        ),
        fields=("name", "value", "label", "sequence"),
        display="value",
        limit=200,
    )
    # A child class that defines its own choice list replaces the parent's
    # wholesale (ServiceNow resolves child-first); fall back to the task-level
    # rows only when the table defines none of its own.
    table_rows = [row for row in rows if str(_raw_value(row.get("name"))) == tbl]
    rows = table_rows or rows
    by_value: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(_raw_value(row.get("value")))
        if value not in by_value:
            by_value[value] = {
                "value": value,
                "label": _raw_value(row.get("label")),
                "table": str(_raw_value(row.get("name"))),
            }
    return list(by_value.values())


def get_schema(table: str) -> list[dict[str, Any]]:
    client = _client()
    tbl = resolve_table(table)
    # sys_dictionary holds one row per column per class; task-derived tables
    # inherit most fields from task, so both classes are unioned. (A full
    # class-hierarchy walk is a documented follow-up.)
    rows, _total = client.query(
        "sys_dictionary",
        query=f"nameIN{tbl},task^elementISNOTEMPTY^ORDERBYelement",
        fields=(
            "name", "element", "column_label", "internal_type", "max_length",
            "mandatory", "reference.name",
        ),
        display="value",
        limit=MAX_LIMIT,
    )
    by_element: dict[str, dict[str, Any]] = {}
    for row in rows:
        element = str(_raw_value(row.get("element")))
        owner = str(_raw_value(row.get("name")))
        if element in by_element and owner != tbl:
            continue  # table-specific definition wins over the inherited one
        by_element[element] = {
            "field": element,
            "label": _raw_value(row.get("column_label")),
            "type": _raw_value(row.get("internal_type")),
            "max_length": _raw_value(row.get("max_length")),
            "mandatory": _raw_value(row.get("mandatory")),
            "reference": _raw_value(row.get("reference.name")),
        }
    return [by_element[key] for key in sorted(by_element)]


def get_sla(identifier: str, *, table: str | None = None) -> list[dict[str, Any]]:
    client = _client()
    tbl, _is_sys_id = detect_table(identifier, table)
    sys_id = _resolve_sys_id(client, tbl, identifier)
    rows, _total = client.query(
        "task_sla",
        query=f"task={sys_id}^ORDERBYDESCsys_created_on",
        fields=(
            "sla.name", "stage", "has_breached", "percentage",
            "business_time_left", "time_left", "start_time", "planned_end_time",
        ),
        display="both",
        limit=20,
    )
    return [_normalize_record(row, "task_sla", None) for row in rows]


def list_approvals(identifier: str, *, table: str | None = None) -> list[dict[str, Any]]:
    client = _client()
    tbl, _is_sys_id = detect_table(identifier, table)
    sys_id = _resolve_sys_id(client, tbl, identifier)
    # sysapproval covers task-based approvals, document_id record-based ones.
    rows, _total = client.query(
        "sysapproval_approver",
        query=f"sysapproval={sys_id}^ORdocument_id={sys_id}^ORDERBYDESCsys_created_on",
        fields=("approver", "state", "comments", "due_date", "sys_created_on", "sys_updated_on"),
        display="both",
        limit=50,
    )
    return [_normalize_record(row, "sysapproval_approver", None) for row in rows]


def list_record_attachments(
    identifier: str, *, table: str | None = None
) -> list[dict[str, Any]]:
    client = _client()
    tbl, _is_sys_id = detect_table(identifier, table)
    sys_id = _resolve_sys_id(client, tbl, identifier)
    rows = client.list_attachments(tbl, sys_id)
    keep = ("sys_id", "file_name", "size_bytes", "content_type", "sys_created_by", "sys_created_on")
    return [
        _ordered({key: _normalize_value(row.get(key)) for key in keep if key in row})
        for row in rows
    ]


def attach_file(
    identifier: str,
    path: str | Path,
    *,
    table: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    _ensure_writable()
    client = _client()
    tbl, _is_sys_id = detect_table(identifier, table)
    sys_id = _resolve_sys_id(client, tbl, identifier)
    result = client.upload_attachment(tbl, sys_id, Path(path), dry_run=dry_run)
    if isinstance(result, dict) and result.get("dry_run"):
        return result
    keep = ("sys_id", "file_name", "size_bytes", "content_type", "table_name", "table_sys_id")
    return _ordered(
        {key: _normalize_value(result.get(key)) for key in keep if key in result}
    )


def download_file(
    attachment_sys_id: str, *, out_dir: str | Path | None = None
) -> dict[str, Any]:
    if not SYS_ID_RE.match(attachment_sys_id):
        raise _tag(
            ValueError(
                f"{attachment_sys_id!r} is not an attachment sys_id (32 hex chars); "
                "find it with `attachments <record>`."
            ),
            "usage",
        )
    target = _client().download_attachment(
        attachment_sys_id, Path(out_dir) if out_dir else Path.cwd()
    )
    return {
        "saved_to": str(target),
        "file_name": target.name,
        "size_bytes": target.stat().st_size,
    }


def delete_record(
    identifier: str,
    *,
    table: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not force:
        raise _tag(
            ValueError("Refusing delete without --force (per-invocation intent)."),
            "guarded",
        )
    if not _delete_allowed():
        raise _tag(
            RuntimeError(
                "Refusing delete: SERVICENOW_ALLOW_DELETE is not set to true "
                "(standing policy gate)."
            ),
            "guarded",
        )
    _ensure_writable()
    client = _client()
    tbl, _is_sys_id = detect_table(identifier, table)
    sys_id = _resolve_sys_id(client, tbl, identifier)
    result = client.delete(tbl, sys_id, dry_run=dry_run)
    if isinstance(result, dict) and result.get("dry_run"):
        return result
    return {"deleted": True, "table": tbl, "sys_id": sys_id}


# ---------------------------------------------------------------------------
# CLI


def _collect_fields(args: argparse.Namespace) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if getattr(args, "data", None):
        try:
            parsed = json.loads(args.data)
        except json.JSONDecodeError as exc:
            raise _tag(ValueError(f"--data is not valid JSON: {exc}"), "usage") from exc
        if not isinstance(parsed, dict):
            raise _tag(ValueError("--data must be a JSON object."), "usage")
        data.update(parsed)
    for item in getattr(args, "field", None) or []:
        if "=" not in item:
            raise _tag(
                ValueError(f"--field expects key=value, got {item!r}."), "usage"
            )
        key, _sep, value = item.partition("=")
        data[key.strip()] = value
    return data


def _split_fields(raw: str | None) -> tuple[str, ...] | None:
    if not raw:
        return None
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _compose_query(args: argparse.Namespace) -> str | None:
    parts: list[str] = []
    if getattr(args, "query", None):
        parts.append(args.query)
    if getattr(args, "active", False):
        parts.append("active=true")
    if getattr(args, "group", None):
        parts.append(_ref_term("assignment_group", args.group))
    if getattr(args, "assigned_to", None):
        parts.append(_ref_term("assigned_to", args.assigned_to))
    if getattr(args, "unassigned", False):
        parts.append("assigned_toISEMPTY")
    if getattr(args, "opened_last", None):
        parts.append(f"opened_at>={_since(args.opened_last)}")
    if getattr(args, "updated_last", None):
        parts.append(f"sys_updated_on>={_since(args.updated_last)}")
    if getattr(args, "order_by", None):
        prefix = "ORDERBYDESC" if getattr(args, "desc", False) else "ORDERBY"
        parts.append(f"{prefix}{args.order_by}")
    return "^".join(parts) or None


# -- command handlers (thin arg->operation adapters) ------------------------


def _cmd_whoami(_args: argparse.Namespace) -> Any:
    return whoami()


def _cmd_tables(_args: argparse.Namespace) -> Any:
    return registry()


def _cmd_get(args: argparse.Namespace) -> Any:
    return get_record(
        args.identifier,
        table=args.table,
        fields=_split_fields(args.fields),
        display=args.display,
    )


def _cmd_query(args: argparse.Namespace) -> Any:
    return query_records(
        args.table_name,
        query=_compose_query(args),
        fields=_split_fields(args.fields),
        display=args.display,
        limit=args.limit,
        offset=args.offset,
        fetch_all=args.all,
        max_records=args.max_records,
    )


def _cmd_count(args: argparse.Namespace) -> Any:
    return count_records(args.table_name, query=args.query)


def _cmd_create(args: argparse.Namespace) -> Any:
    return create_record(
        args.table_name,
        _collect_fields(args),
        input_display=args.input_display,
        correlation_id=args.correlation_id,
        dry_run=args.dry_run,
    )


def _cmd_update(args: argparse.Namespace) -> Any:
    return update_record(
        args.identifier,
        _collect_fields(args),
        table=args.table,
        input_display=args.input_display,
        dry_run=args.dry_run,
    )


def _cmd_comment(args: argparse.Namespace) -> Any:
    return add_comment(args.identifier, args.text, table=args.table, dry_run=args.dry_run)


def _cmd_work_note(args: argparse.Namespace) -> Any:
    return add_work_note(args.identifier, args.text, table=args.table, dry_run=args.dry_run)


def _cmd_notes(args: argparse.Namespace) -> Any:
    return get_notes(
        args.identifier, table=args.table, note_type=args.type, limit=args.limit
    )


def _cmd_history(args: argparse.Namespace) -> Any:
    return get_history(
        args.identifier, table=args.table, field_name=args.field, limit=args.limit
    )


def _cmd_resolve(args: argparse.Namespace) -> Any:
    return resolve_record(
        args.identifier, table=args.table, code=args.code, notes=args.notes,
        dry_run=args.dry_run,
    )


def _cmd_close(args: argparse.Namespace) -> Any:
    return close_record(
        args.identifier, table=args.table, code=args.code, notes=args.notes,
        dry_run=args.dry_run,
    )


def _cmd_assign(args: argparse.Namespace) -> Any:
    return assign_record(
        args.identifier, table=args.table, to=args.to, group=args.group,
        dry_run=args.dry_run,
    )


def _cmd_user(args: argparse.Namespace) -> Any:
    return lookup_user(args.term, limit=args.limit)


def _cmd_group(args: argparse.Namespace) -> Any:
    return lookup_group(args.term, limit=args.limit)


def _cmd_choices(args: argparse.Namespace) -> Any:
    return get_choices(args.table_name, args.field_name)


def _cmd_schema(args: argparse.Namespace) -> Any:
    return get_schema(args.table_name)


def _cmd_sla(args: argparse.Namespace) -> Any:
    return get_sla(args.identifier, table=args.table)


def _cmd_approvals(args: argparse.Namespace) -> Any:
    return list_approvals(args.identifier, table=args.table)


def _cmd_attachments(args: argparse.Namespace) -> Any:
    return list_record_attachments(args.identifier, table=args.table)


def _cmd_attach(args: argparse.Namespace) -> Any:
    return attach_file(
        args.identifier, args.path, table=args.table, dry_run=args.dry_run
    )


def _cmd_download(args: argparse.Namespace) -> Any:
    return download_file(args.attachment_sys_id, out_dir=args.out)


def _cmd_delete(args: argparse.Namespace) -> Any:
    return delete_record(
        args.identifier, table=args.table, force=args.force, dry_run=args.dry_run
    )


_EPILOG = """\
identifiers:
  Record numbers (INC0010023, CHG..., CTASK..., REQ..., RITM..., SCTASK...,
  PRB..., VIT...) are auto-mapped to their table; a 32-hex sys_id needs --table.

environment (.env or real env vars, read lazily):
  SERVICENOW_INSTANCE           instance name or full https URL   (required)
  SERVICENOW_USERNAME           service-account user              (required)
  SERVICENOW_PASSWORD           service-account password          (required)
  SERVICENOW_AUTH               basic (default)
  SERVICENOW_TIMEOUT_SECONDS    per-request timeout, default 30
  SERVICENOW_READ_ONLY          true = refuse every write before sending
  SERVICENOW_ALLOW_DELETE       must be true for `delete`
  SERVICENOW_MAX_ATTACHMENT_MB  attachment size cap, default 100

output contract:
  Success: result JSON on stdout, exit 0. Failure: {"error": {"class",
  "http_status", "message", "hint"}} on stderr; exit 2 = fix the call
  (bad input / not found), exit 1 = fix config or retry (auth, ACL,
  rate limit, instance down). Writes report "fields_not_applied" when
  ServiceNow silently dropped or rewrote a field.

examples:
  snow whoami
  snow get INC0010023
  snow query incident --group "Network Ops" --active --opened-last 7d --limit 10
  snow create incident --field short_description="VPN down" --field urgency=1
  snow comment INC0010023 "Customer called - still broken"      (VISIBLE to end user)
  snow work-note INC0010023 "Checked firewall, all clear"       (internal only)
  snow notes INC0010023 --type work_notes
  snow resolve INC0010023 --code "Solved (Permanently)" --notes "Rebooted edge router"
  snow update VIT0012345 --field state=3 --dry-run
  snow choices incident state
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snow",
        description=(
            "ServiceNow REST client: JSON in/out, safe for AI-agent use. "
            "Reads config from env vars / a repo-root .env."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--timeout", type=float, help="per-request timeout in seconds")
    common.add_argument("--verbose", action="store_true", help="request/response lines to stderr")
    common.add_argument("--output", choices=("json", "table"), default="json",
                        help="output format (default json)")
    common.add_argument("--max-field-chars", type=int, default=DEFAULT_MAX_FIELD_CHARS,
                        help="truncate string fields over N chars; 0 disables "
                             f"(default {DEFAULT_MAX_FIELD_CHARS})")

    rec = argparse.ArgumentParser(add_help=False)
    rec.add_argument("--table", help="table name or alias (required with a sys_id)")

    write = argparse.ArgumentParser(add_help=False)
    write.add_argument("--dry-run", action="store_true",
                       help="print the exact method/URL/payload without sending")

    def add(name: str, func: Callable[[argparse.Namespace], Any], help_text: str,
            parents: list[argparse.ArgumentParser], description: str | None = None,
            aliases: Sequence[str] = ()) -> argparse.ArgumentParser:
        p = sub.add_parser(name, parents=[common, *parents], help=help_text,
                           description=description or help_text, aliases=list(aliases))
        p.set_defaults(func=func)
        return p

    add("whoami", _cmd_whoami, "verify auth and show the service account", [])
    add("tables", _cmd_tables, "dump the record-type registry (prefixes, aliases, defaults)", [])

    p = add("get", _cmd_get, "fetch one record by number or sys_id", [rec])
    p.add_argument("identifier")
    p.add_argument("--fields", help="comma-separated field list (default: curated per table)")
    p.add_argument("--display", choices=DISPLAY_MODES, default="both",
                   help="value=raw, display=labels, both=value+label (default both)")

    p = add("query", _cmd_query, "list records matching filters", [],
            description="List records. Compose --query (encoded query) with the "
                        "canned flags; all are AND-joined.", aliases=("list",))
    p.add_argument("table_name", metavar="table", help="table name or alias")
    p.add_argument("--query", help="raw ServiceNow encoded query (^ joins terms)")
    p.add_argument("--active", action="store_true", help="only active records")
    p.add_argument("--group", help="assignment group (name or sys_id)")
    p.add_argument("--assigned-to", help="assignee (user_name, email, name, or sys_id)")
    p.add_argument("--unassigned", action="store_true", help="assigned_to is empty")
    p.add_argument("--opened-last", metavar="N{d|h|m}", help="opened in the last N days/hours/minutes")
    p.add_argument("--updated-last", metavar="N{d|h|m}", help="updated in the last N days/hours/minutes")
    p.add_argument("--order-by", help="sort field")
    p.add_argument("--desc", action="store_true", help="sort descending")
    p.add_argument("--fields", help="comma-separated field list")
    p.add_argument("--display", choices=DISPLAY_MODES, default="display")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--all", action="store_true",
                   help=f"page through everything (hard cap {MAX_RECORDS_CAP})")
    p.add_argument("--max-records", type=int, default=MAX_RECORDS_CAP,
                   help="cap for --all walks")

    p = add("count", _cmd_count, "count records (Aggregate API)", [])
    p.add_argument("table_name", metavar="table")
    p.add_argument("--query", help="encoded query to count within")

    p = add("create", _cmd_create, "create a record", [write])
    p.add_argument("table_name", metavar="table")
    p.add_argument("--field", action="append", metavar="k=v", help="field value (repeatable)")
    p.add_argument("--data", help="JSON object of fields (merged; --field wins)")
    p.add_argument("--input-display", action="store_true",
                   help="values are display values (labels), not raw codes")
    p.add_argument("--correlation-id", help="idempotency key: if a record with this "
                                            "correlation_id exists it is returned instead")

    p = add("update", _cmd_update, "update fields on a record", [rec, write])
    p.add_argument("identifier")
    p.add_argument("--field", action="append", metavar="k=v")
    p.add_argument("--data", help="JSON object of fields")
    p.add_argument("--input-display", action="store_true")

    p = add("comment", _cmd_comment, "add a customer-VISIBLE comment", [rec, write],
            description="Add a comment. Comments are VISIBLE TO THE END USER; "
                        "use `work-note` for internal notes.")
    p.add_argument("identifier")
    p.add_argument("text")

    p = add("work-note", _cmd_work_note, "add an internal work note", [rec, write])
    p.add_argument("identifier")
    p.add_argument("text")

    p = add("notes", _cmd_notes, "read the journal (comments/work notes), newest first", [rec])
    p.add_argument("identifier")
    p.add_argument("--type", choices=("all", "comments", "work_notes"), default="all")
    p.add_argument("--limit", type=int, default=50)

    p = add("history", _cmd_history, "field-level audit trail (sys_audit; may need ACL)", [rec])
    p.add_argument("identifier")
    p.add_argument("--field", help="only changes to this field")
    p.add_argument("--limit", type=int, default=100)

    p = add("resolve", _cmd_resolve, "resolve a record (incident: state 6 + close code/notes)", [rec, write])
    p.add_argument("identifier")
    p.add_argument("--code", help="close code")
    p.add_argument("--notes", help="close notes")

    p = add("close", _cmd_close, "close a record via its registered transition", [rec, write])
    p.add_argument("identifier")
    p.add_argument("--code", help="close code (tables that have one)")
    p.add_argument("--notes", help="close notes (tables that have them)")

    p = add("assign", _cmd_assign, "set assignee and/or assignment group", [rec, write])
    p.add_argument("identifier")
    p.add_argument("--to", help="user (user_name, email, name, or sys_id)")
    p.add_argument("--group", help="group (name or sys_id)")

    p = add("user", _cmd_user, "look up users by user_name/email/name", [])
    p.add_argument("term")
    p.add_argument("--limit", type=int, default=10)

    p = add("group", _cmd_group, "look up assignment groups by name", [])
    p.add_argument("term")
    p.add_argument("--limit", type=int, default=10)

    p = add("choices", _cmd_choices, "valid values/labels for a choice field on this instance", [])
    p.add_argument("table_name", metavar="table")
    p.add_argument("field_name", metavar="field")

    p = add("schema", _cmd_schema, "field dictionary for a table (incl. task-inherited)", [])
    p.add_argument("table_name", metavar="table")

    p = add("sla", _cmd_sla, "SLA timers for a record (task_sla)", [rec])
    p.add_argument("identifier")

    p = add("approvals", _cmd_approvals, "approval states for a record (read-only)", [rec])
    p.add_argument("identifier")

    p = add("attachments", _cmd_attachments, "list attachments on a record", [rec])
    p.add_argument("identifier")

    p = add("attach", _cmd_attach, "upload a file to a record", [rec, write])
    p.add_argument("identifier")
    p.add_argument("path")

    p = add("download", _cmd_download, "download an attachment by its sys_id", [])
    p.add_argument("attachment_sys_id")
    p.add_argument("--out", help="destination directory (default: cwd)")

    p = add("delete", _cmd_delete, "hard-delete a record (double-gated)", [rec, write],
            description="Hard-delete a record. Requires --force AND "
                        "SERVICENOW_ALLOW_DELETE=true.")
    p.add_argument("identifier")
    p.add_argument("--force", action="store_true")

    return parser


def _emit_error(exc: Exception) -> None:
    default_class = "validation" if isinstance(exc, ValueError) else "remote"
    payload = {
        "error": {
            "class": getattr(exc, "error_class", default_class),
            "http_status": getattr(exc, "http_status", None),
            "message": str(exc),
            "hint": getattr(exc, "hint", None),
        }
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)


def _render_table(result: Any) -> str:
    rows: list[dict[str, Any]]
    if isinstance(result, dict) and isinstance(result.get("records"), list):
        rows = result["records"]
    elif isinstance(result, list):
        rows = [row for row in result if isinstance(row, dict)]
    else:
        return json.dumps(result, indent=2, ensure_ascii=False)
    if not rows:
        return "(no records)"

    def cell(value: Any) -> str:
        if isinstance(value, dict):
            value = value.get("display", value.get("value", ""))
        text = str(value if value is not None else "")
        return text if len(text) <= 40 else text[:37] + "..."

    columns = [key for key in rows[0] if key != "url"]
    widths = {
        col: max(len(col), *(len(cell(row.get(col))) for row in rows))
        for col in columns
    }
    lines = ["  ".join(col.ljust(widths[col]) for col in columns)]
    lines.append("  ".join("-" * widths[col] for col in columns))
    for row in rows:
        lines.append("  ".join(cell(row.get(col)).ljust(widths[col]) for col in columns))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # Explicit repo-root path because callers may run from anywhere; a second
    # default-search load covers the copied-out-of-repo case. Real env wins.
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    load_dotenv(override=False)

    args = _build_parser().parse_args(argv)
    global VERBOSE
    VERBOSE = bool(getattr(args, "verbose", False))
    if getattr(args, "timeout", None):
        os.environ["SERVICENOW_TIMEOUT_SECONDS"] = str(args.timeout)

    try:
        result = args.func(args)
    except ValueError as exc:
        _emit_error(exc)
        return 2
    except RuntimeError as exc:
        _emit_error(exc)
        return 1

    max_chars = getattr(args, "max_field_chars", DEFAULT_MAX_FIELD_CHARS)
    if max_chars:
        result = _truncate_strings(result, max_chars)
    if getattr(args, "output", "json") == "table":
        print(_render_table(result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
