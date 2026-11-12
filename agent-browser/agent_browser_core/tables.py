"""Bounded extraction of rendered tables, with opt-in pagination and export."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import unicodedata
import urllib.parse
from typing import Any

from . import api, common, snapshots


CELL_SELECTOR = ":scope > td, :scope > th, :scope > [role=cell], :scope > [role=gridcell], :scope > [role=rowheader]"
MAX_PAGES = 100
DEFAULT_PAGES = 10
MAX_ROWS = 10_000
DEFAULT_ROWS = 1_000
MAX_COLUMNS = 256
MAX_EXPORT_BYTES = 32 * 1024 * 1024
MAX_TABLE_BYTES = 16 * 1024 * 1024
PAGE_CHANGE_WAIT_SECONDS = 3.0
PAGE_NAV_STABILITY_SECONDS = 0.5
NEXT_DISABLED_JS = "el => el.matches(':disabled') || el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true'"
CELL_TEXT_JS = "el => String(el.innerText || '').slice(0, 2000)"
HEADER_TEXT_JS = "el => String(el.innerText || '').slice(0, 500)"
TABLE_TEXT_JS = "el => String(el.innerText || '').slice(0, 100000)"
_REF_TARGET_KEYS = frozenset({"ref"})
_VALID_EXPORT_FORMATS = frozenset({"json", "csv"})
_COMMAND_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class _DeadlineExceeded(Exception):
    """Internal signal: a bounded page read could not finish within its command budget."""


def extract_table(engine: Any, args: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Extract one table or explicitly traverse its next control.

    With no table options this preserves the original one-page response,
    including current-page row refs. Paginated responses omit refs because refs
    from pages already traversed are stale by the time the reply is returned.
    """
    values = _validate_options(args)
    paginating = values["next"] is not None
    no_options = not paginating and not any(
        args.get(name) is not None for name in ("max_rows", "columns", "keys", "export_format")
    )
    if no_options:
        return _extract_compat_page(engine, args)

    if paginating and _is_ref_target(args.get("target")):
        raise common._tag(
            ValueError("Paginated table targets must be CSS or semantic locators; refs can become stale after a page change."),
            common.ERR_VALIDATION,
        )

    page = engine.target_page(args)
    tab_id = engine._tab_id(page)
    page_origin = None
    if paginating:
        _ensure_page_owner(engine, page)
        page_origin = _page_origin(page.url)
        if not page_origin:
            raise common._tag(ValueError("Pagination needs an HTTP(S) page with a stable origin."), common.ERR_GUARDED)
        # A prior one-page table read may have cached row refs. They must not
        # survive the first pagination click or be used against the new page.
        if not hasattr(engine, "table_rows"):
            engine.table_rows, engine.table_row_epochs = {}, {}
        engine.table_rows.pop(page, None)
        engine.table_row_epochs.pop(page, None)

    max_rows = values["max_rows"] or DEFAULT_ROWS
    max_pages = values["max_pages"] or DEFAULT_PAGES
    deadline = args.get("_deadline", common._now() + max(float(timeout), 0.0))
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
        raise common._tag(ValueError("Invalid table command deadline."), common.ERR_VALIDATION)

    rows: list[dict[str, Any]] = []
    seen_keys: dict[tuple[str, ...], tuple[str, ...]] = {}
    page_signatures: set[str] = set()
    output_columns: list[str] = []
    schema: tuple[str, ...] | None = None
    schema_headerless: bool | None = None
    selected_indexes: list[int] = []
    key_indexes: list[int] | None = None
    duplicate_rows = loop_count = key_conflict_count = 0
    rows_scanned = page_count = output_bytes = 0
    stop_reason: str | None = None
    complete = True
    truncated = False
    table_target = args.get("target")
    table = _resolve_table(engine, page, table_target)

    while stop_reason is None:
        raw_limit = MAX_ROWS - rows_scanned
        if raw_limit <= 0:
            stop_reason, complete, truncated = "row_limit", False, True
            break
        try:
            page_headers, raw_rows, more_rows, byte_limited, deadline_hit, headers_complete = _read_page(
                engine, page, table, keep_refs=not paginating,
                row_limit=raw_limit, bounded=True, deadline=deadline,
            )
        except _DeadlineExceeded:
            stop_reason, complete, truncated = "deadline", False, True
            break
        if not headers_complete:
            stop_reason, complete, truncated = "deadline", False, True
            break

        page_count += 1
        has_dom_headers = bool(page_headers)
        widths = {len(row["cells"]) for row in raw_rows}
        if len(widths) > 1:
            if page_count == 1:
                raise common._tag(ValueError("Table rows have inconsistent cell counts."), common.ERR_VALIDATION)
            stop_reason, complete, truncated = "schema_changed", False, True
            break
        if not has_dom_headers and widths:
            page_headers = [f"column_{index + 1}" for index in range(next(iter(widths)))]
        elif not has_dom_headers and not raw_rows and schema is not None:
            if schema_headerless:
                page_headers = list(schema)
            else:
                stop_reason, complete, truncated = "schema_changed", False, True
                break

        if schema is None:
            if has_dom_headers or raw_rows:
                schema = tuple(page_headers)
                schema_headerless = not has_dom_headers
                output_columns, selected_indexes = _select_columns(page_headers, values["columns"])
                key_indexes = _select_keys(page_headers, values["keys"])
        elif has_dom_headers == schema_headerless or tuple(page_headers) != schema:
            stop_reason, complete, truncated = "schema_changed", False, True
            break

        if schema is not None and any(len(row["cells"]) != len(schema) for row in raw_rows):
            if page_count == 1:
                raise common._tag(ValueError("Table row width does not match its headers."), common.ERR_VALIDATION)
            stop_reason, complete, truncated = "schema_changed", False, True
            break

        rows_scanned += len(raw_rows)
        fingerprint = _page_fingerprint(raw_rows)
        if paginating and fingerprint in page_signatures:
            loop_count += 1
            duplicate_rows += len(raw_rows)
            stop_reason, complete, truncated = "page_loop", False, True
            break
        page_signatures.add(fingerprint)

        should_deduplicate = paginating or key_indexes is not None
        if should_deduplicate:
            conflict, added, duplicates, output_bytes, byte_limited_output, row_limited = _append_page_rows(
                raw_rows, page_headers, selected_indexes, key_indexes, rows, seen_keys,
                max_rows=max_rows, keep_refs=not paginating,
                byte_budget=MAX_TABLE_BYTES, current_bytes=output_bytes,
            )
            duplicate_rows += duplicates
        else:
            conflict, added, output_bytes, byte_limited_output, row_limited = _project_page_rows(
                raw_rows, selected_indexes, rows, max_rows=max_rows,
                keep_refs=not paginating, byte_budget=MAX_TABLE_BYTES,
                current_bytes=output_bytes,
            )
            duplicates = 0

        if conflict:
            key_conflict_count += 1
            stop_reason, complete, truncated = "key_conflict", False, True
            break
        if byte_limited or byte_limited_output:
            stop_reason, complete, truncated = "byte_limit", False, True
            break
        if deadline_hit:
            stop_reason, complete, truncated = "deadline", False, True
            break
        if row_limited or more_rows:
            stop_reason, complete, truncated = "row_limit", False, True
            break
        if not paginating:
            stop_reason = "single_page"
            break

        if page_count > 1 and added == 0:
            next_state, _locator = _next_state(engine, page, values["next"])
            if next_state in {"missing", "disabled"}:
                stop_reason = "next_missing" if next_state == "missing" else "next_disabled"
            else:
                loop_count += 1
                stop_reason, complete, truncated = "no_progress", False, True
            break

        next_state, next_locator = _next_state(engine, page, values["next"])
        if next_state != "enabled":
            stop_reason = "next_missing" if next_state == "missing" else "next_disabled"
            break
        if page_count >= max_pages:
            stop_reason, complete, truncated = "page_limit", False, True
            break
        if len(rows) >= max_rows:
            stop_reason, complete, truncated = "row_limit", False, True
            break
        if rows_scanned >= MAX_ROWS:
            stop_reason, complete, truncated = "row_limit", False, True
            break
        remaining = float(deadline) - common._now()
        if remaining <= 0:
            stop_reason, complete, truncated = "deadline", False, True
            break

        before_navigation = engine._nav_signature(page)
        try:
            before_text = _table_text(table, deadline)
        except _DeadlineExceeded:
            stop_reason, complete, truncated = "deadline", False, True
            break
        _check_next_href(page, next_locator, page_origin)
        new_pages_before = set(getattr(engine, "new_pages", []))
        try:
            engine.h_click({"target": values["next"], "tab": tab_id}, remaining)
        except Exception as exc:  # noqa: BLE001 - a click may navigate before settling times out
            if getattr(exc, "error_class", None) != common.ERR_TIMEOUT:
                raise
            _ensure_page_owner(engine, page)
            _ensure_page_origin(engine, page, page_origin)
            stop_reason, complete, truncated = "deadline", False, True
            break

        _ensure_page_owner(engine, page)
        _ensure_page_origin(engine, page, page_origin)
        if set(getattr(engine, "new_pages", [])) - new_pages_before:
            stop_reason, complete, truncated = "new_tab", False, True
            break
        changed = _wait_for_table_change(
            engine, page, table_target, before_text, before_navigation, page_origin,
            min(float(deadline), common._now() + PAGE_CHANGE_WAIT_SECONDS),
        )
        if changed is None:
            if _deadline_expired(deadline):
                stop_reason, complete, truncated = "deadline", False, True
            else:
                next_state, _locator = _next_state(engine, page, values["next"])
                if next_state in {"missing", "disabled"}:
                    stop_reason = "next_missing" if next_state == "missing" else "next_disabled"
                else:
                    loop_count += 1
                    stop_reason, complete, truncated = "no_progress", False, True
            break
        page, table = changed
        _ensure_page_owner(engine, page)
        _ensure_page_origin(engine, page, page_origin)

    if schema is None and (values["columns"] or values["keys"]):
        raise common._tag(ValueError("Table headers could not be established for the requested columns or keys."), common.ERR_VALIDATION)
    result: dict[str, Any] = {
        "columns": output_columns,
        "rows": rows,
        "tab": engine._tab_index(page),
        "tab_id": engine._tab_id(page),
        "untrusted": ["columns", "rows"],
        "pages": page_count,
        "row_count": len(rows),
        "rows_scanned": rows_scanned,
        "duplicate_rows": duplicate_rows,
        "loop_count": loop_count,
        "key_conflict_count": key_conflict_count,
        "complete": complete,
        "truncated": truncated,
        "stop_reason": stop_reason,
    }
    if not paginating:
        _remember_row_refs(engine, page, rows)
    if values["export_format"]:
        export_doc = {
            "columns": output_columns,
            "rows": rows,
            "page_count": page_count,
            "row_count": len(rows),
            "rows_scanned": rows_scanned,
            "duplicate_rows": duplicate_rows,
            "loop_count": loop_count,
            "key_conflict_count": key_conflict_count,
            "complete": complete,
            "truncated": truncated,
            "stop_reason": stop_reason,
        }
        result["export_file"] = _write_export(engine, args, export_doc, values["export_format"])
    return result


def _validate_options(args: dict[str, Any]) -> dict[str, Any]:
    next_target = args.get("next")
    if next_target is not None and not _valid_stable_target(next_target):
        raise common._tag(ValueError("--next needs a CSS selector or semantic label/role target; refs are not valid across pages."), common.ERR_VALIDATION)
    if next_target is None and args.get("max_pages") is not None:
        raise common._tag(ValueError("--max-pages requires --next."), common.ERR_USAGE)
    max_pages = _limit(args.get("max_pages"), "max-pages", DEFAULT_PAGES, MAX_PAGES) if next_target is not None else None
    max_rows = _limit(args.get("max_rows"), "max-rows", DEFAULT_ROWS, MAX_ROWS) if next_target is not None or args.get("max_rows") is not None else None
    return {
        "next": next_target,
        "max_pages": max_pages,
        "max_rows": max_rows,
        "columns": _names(args.get("columns"), "column"),
        "keys": _names(args.get("keys"), "key"),
        "export_format": _export_format(args.get("export_format")),
    }


def _limit(value: Any, name: str, default: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise common._tag(ValueError(f"--{name} must be an integer from 1 to {maximum}."), common.ERR_VALIDATION)
    return value


def _names(value: Any, name: str) -> list[str] | None:
    if value is None:
        return None
    names = [value] if isinstance(value, str) else value
    if not isinstance(names, list) or not 1 <= len(names) <= MAX_COLUMNS or not all(isinstance(item, str) and item.strip() for item in names):
        raise common._tag(ValueError(f"{name.title()} names must be 1 to {MAX_COLUMNS} nonempty strings."), common.ERR_VALIDATION)
    if len(set(names)) != len(names):
        raise common._tag(ValueError(f"Duplicate {name} names are not allowed."), common.ERR_VALIDATION)
    return names


def _export_format(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _VALID_EXPORT_FORMATS:
        raise common._tag(ValueError("--export must be json or csv."), common.ERR_VALIDATION)
    return value


def _valid_stable_target(target: Any) -> bool:
    if isinstance(target, str):
        return bool(target.strip()) and not snapshots.is_ref(target)
    if not isinstance(target, dict):
        return False
    if set(target) == {"label"}:
        return isinstance(target["label"], str) and bool(target["label"].strip())
    if "role" in target and set(target) <= {"role", "name"}:
        return isinstance(target["role"], str) and bool(target["role"].strip()) and (
            "name" not in target or isinstance(target["name"], str) and bool(target["name"].strip())
        )
    return False


def _is_ref_target(target: Any) -> bool:
    if isinstance(target, str):
        return snapshots.is_ref(target)
    return isinstance(target, dict) and set(target) == _REF_TARGET_KEYS


def _resolve_table(engine: Any, page: Any, target: Any) -> Any:
    table = engine.resolve(page, target) if target is not None else engine._only_table(page)
    if table.evaluate("el => el.tagName.toLowerCase() !== 'table' && !['table','grid'].includes(el.getAttribute('role'))"):
        raise common._tag(ValueError("Target is not a table or grid."), common.ERR_VALIDATION)
    return table


def _extract_compat_page(engine: Any, args: dict[str, Any]) -> dict[str, Any]:
    page = engine.target_page(args)
    table = _resolve_table(engine, page, args.get("target"))
    headers, rows, _more, _byte_limited, _deadline_hit, _headers_complete = _read_page(
        engine, page, table, keep_refs=True, row_limit=None, bounded=False, deadline=None
    )
    _remember_row_refs(engine, page, rows)
    return {
        "columns": headers,
        "rows": rows,
        "tab": engine._tab_index(page),
        "tab_id": engine._tab_id(page),
        "untrusted": ["columns", "rows"],
    }


def _read_page(engine: Any, page: Any, table: Any, *, keep_refs: bool, row_limit: int | None,
               bounded: bool, deadline: float | None) -> tuple[list[str], list[dict[str, Any]], bool, bool, bool, bool]:
    headers: list[str] = []
    headers_complete = True
    if not bounded:
        headers = [common.sanitize_text(loc.inner_text(), 500)
                   for loc in table.get_by_role("columnheader").all() if loc.is_visible()]
    else:
        header_locator = table.get_by_role("columnheader")
        header_count = header_locator.count()
        if header_count > MAX_COLUMNS:
            raise common._tag(ValueError(f"Table exceeds the {MAX_COLUMNS}-column extraction bound."), common.ERR_VALIDATION)
        for index in range(header_count):
            if _deadline_expired(deadline):
                headers_complete = False
                return headers, [], False, False, True, False
            loc = header_locator.nth(index)
            if not loc.is_visible():
                continue
            try:
                headers.append(common.sanitize_text(_locator_text(loc, HEADER_TEXT_JS, deadline), 500))
            except _DeadlineExceeded:
                headers_complete = False
                return headers, [], False, False, True, False

    if bounded and len(headers) > MAX_COLUMNS:
        raise common._tag(ValueError(f"Table exceeds the {MAX_COLUMNS}-column extraction bound."), common.ERR_VALIDATION)
    prefix = ""
    if keep_refs:
        if page not in engine.served:
            engine._serve_snapshot(page)
        served = engine.served[page]
        handle = table.element_handle()
        try:
            frame = handle.owner_frame()
        finally:
            try:
                handle.dispose()
            except Exception:  # noqa: BLE001 - handle cleanup is best effort
                pass
        prefix = next((p for p, f in served.frame_by_prefix.items() if f is frame), "")

    row_locator = table.get_by_role("row")
    row_count = row_locator.count()
    rows: list[dict[str, Any]] = []
    has_more = byte_limited = deadline_hit = False
    raw_bytes = 0
    for index in range(row_count):
        if bounded and _deadline_expired(deadline):
            deadline_hit = True
            break
        row = row_locator.nth(index)
        if not row.is_visible() or row.get_by_role("columnheader").count():
            continue
        if row_limit is not None and len(rows) >= row_limit:
            has_more = True
            break
        cell_locator = row.locator(CELL_SELECTOR)
        if bounded:
            cell_count = cell_locator.count()
            if cell_count > MAX_COLUMNS:
                raise common._tag(ValueError(f"Table exceeds the {MAX_COLUMNS}-column extraction bound."), common.ERR_VALIDATION)
            cell_locs = [cell_locator.nth(cell_index) for cell_index in range(cell_count)]
        else:
            cell_locs = cell_locator.all()
        cells: list[str] = []
        row_bytes = 2
        for cell_index, cell in enumerate(cell_locs):
            try:
                text = (common.sanitize_text(_locator_text(cell, CELL_TEXT_JS, deadline), 2000)
                        if bounded else common.sanitize_text(cell.inner_text(), 2000))
            except _DeadlineExceeded:
                deadline_hit = True
                break
            if bounded:
                size = len(json.dumps(text, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                row_bytes += size + (1 if cell_index else 0)
                if raw_bytes + row_bytes > MAX_TABLE_BYTES:
                    byte_limited = True
                    break
            cells.append(text)
        if deadline_hit or byte_limited:
            break
        if bounded:
            raw_bytes += row_bytes
        ref = None
        if keep_refs:
            if _deadline_expired(deadline):
                deadline_hit = True
                break
            snapshot = row.aria_snapshot(mode="ai", **({"timeout": _remaining_ms(deadline)} if bounded else {}))
            match = re.search(r"\[ref=([^\]]+)\]", snapshot)
            ref = match[1] if match else None
            if ref and prefix and not ref.startswith("f"):
                ref = prefix + ref
        rows.append({"ref": ref, "cells": cells})
    if keep_refs:
        engine._serve_snapshot(page)
    return headers, rows, has_more, byte_limited, deadline_hit, headers_complete


def _locator_text(locator: Any, script: str, deadline: float | None) -> str:
    remaining = _remaining_ms(deadline)
    if deadline is not None and remaining <= 0:
        raise _DeadlineExceeded
    try:
        return str(locator.evaluate(script, timeout=remaining) or "")
    except Exception as exc:  # noqa: BLE001 - convert only expiry; preserve real locator errors
        if deadline is not None and _deadline_expired(deadline):
            raise _DeadlineExceeded from exc
        raise


def _remaining_ms(deadline: float | None) -> int | None:
    if deadline is None:
        return None
    remaining = deadline - common._now()
    return max(0, int(remaining * 1000))


def _deadline_expired(deadline: float | None) -> bool:
    return deadline is not None and deadline - common._now() <= 0


def _remember_row_refs(engine: Any, page: Any, rows: list[dict[str, Any]]) -> None:
    if not hasattr(engine, "table_rows"):
        engine.table_rows, engine.table_row_epochs = {}, {}
    engine.table_rows[page] = {row["ref"] for row in rows if row.get("ref")}
    engine.table_row_epochs[page] = engine._nav_signature(page)


def _select_columns(headers: list[str], selected: list[str] | None) -> tuple[list[str], list[int]]:
    if selected is None:
        return list(headers), list(range(len(headers)))
    indexes = _header_indexes(headers, selected, "column")
    return [headers[index] for index in indexes], indexes


def _select_keys(headers: list[str], keys: list[str] | None) -> list[int] | None:
    if keys is None:
        return None
    return _header_indexes(headers, keys, "key")


def _header_indexes(headers: list[str], names: list[str], kind: str) -> list[int]:
    indexes = []
    for name in names:
        matches = [index for index, header in enumerate(headers) if header == name]
        if len(matches) != 1:
            raise common._tag(ValueError(f"{kind.title()} {name!r} must match exactly one table header."), common.ERR_VALIDATION)
        indexes.append(matches[0])
    return indexes


def _key_for(cells: tuple[str, ...], key_indexes: list[int] | None) -> tuple[str, ...]:
    if key_indexes is None:
        return cells
    if any(index >= len(cells) for index in key_indexes):
        return ("__row_width_mismatch__",)
    return tuple(cells[index] for index in key_indexes)


def _append_page_rows(raw_rows: list[dict[str, Any]], headers: list[str], selected_indexes: list[int],
                      key_indexes: list[int] | None, rows: list[dict[str, Any]],
                      seen_keys: dict[tuple[str, ...], tuple[str, ...]], *, max_rows: int,
                      keep_refs: bool, byte_budget: int, current_bytes: int) -> tuple[bool, int, int, int, bool, bool]:
    added = duplicates = 0
    for raw in raw_rows:
        cells = tuple(raw["cells"])
        if len(cells) != len(headers):
            return True, added, duplicates, current_bytes, False, False
        key = _key_for(cells, key_indexes)
        previous = seen_keys.get(key)
        if previous is not None:
            if previous != cells:
                return True, added, duplicates, current_bytes, False, False
            duplicates += 1
            continue
        if len(rows) >= max_rows:
            return False, added, duplicates, current_bytes, False, True
        item = {"cells": [raw["cells"][index] for index in selected_indexes]}
        if keep_refs:
            item["ref"] = raw.get("ref")
        size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if current_bytes + size > byte_budget:
            return False, added, duplicates, current_bytes, True, False
        seen_keys[key] = cells
        rows.append(item)
        current_bytes += size
        added += 1
    return False, added, duplicates, current_bytes, False, False


def _project_page_rows(raw_rows: list[dict[str, Any]], selected_indexes: list[int], rows: list[dict[str, Any]], *,
                       max_rows: int, keep_refs: bool, byte_budget: int,
                       current_bytes: int) -> tuple[bool, int, int, bool, bool]:
    added = 0
    for raw in raw_rows:
        if len(rows) >= max_rows:
            return False, added, current_bytes, False, True
        item = {"cells": [raw["cells"][index] for index in selected_indexes]}
        if keep_refs:
            item["ref"] = raw.get("ref")
        size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if current_bytes + size > byte_budget:
            return False, added, current_bytes, True, False
        rows.append(item)
        current_bytes += size
        added += 1
    return False, added, current_bytes, False, False


def _page_fingerprint(raw_rows: list[dict[str, Any]]) -> str:
    payload = json.dumps([row["cells"] for row in raw_rows], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _next_state(engine: Any, page: Any, target: Any) -> tuple[str, Any | None]:
    try:
        locator = engine.resolve(page, target)
    except ValueError as exc:
        if getattr(exc, "error_class", None) in {common.ERR_NOT_FOUND, common.ERR_REF_NOT_FOUND}:
            return "missing", None
        raise
    if not locator.is_visible():
        return "disabled", locator
    if not locator.is_enabled() or locator.evaluate(NEXT_DISABLED_JS):
        return "disabled", locator
    return "enabled", locator


def _table_text(table: Any, deadline: float | None) -> str:
    return common.sanitize_text(_locator_text(table, TABLE_TEXT_JS, deadline), 100_000)


def _wait_for_table_change(engine: Any, page: Any, table_target: Any, before_text: str,
                           before_navigation: Any, origin: tuple[str, str, int], deadline: float) -> tuple[Any, Any] | None:
    nav_changed_at: float | None = None
    while common._now() < deadline:
        _ensure_page_owner(engine, page)
        _ensure_page_origin(engine, page, origin)
        try:
            table = _resolve_table(engine, page, table_target)
            current_text = _table_text(table, deadline)
            if current_text != before_text:
                return page, table
            if engine._nav_signature(page) != before_navigation:
                if nav_changed_at is None:
                    nav_changed_at = common._now()
                elif common._now() - nav_changed_at >= PAGE_NAV_STABILITY_SECONDS:
                    return page, table
        except _DeadlineExceeded:
            return None
        except ValueError as exc:
            if getattr(exc, "error_class", None) not in {common.ERR_NOT_FOUND, common.ERR_REF_NOT_FOUND}:
                raise
        if _deadline_expired(deadline):
            return None
        page.wait_for_timeout(50)
    return None


def _ensure_page_owner(engine: Any, page: Any) -> None:
    if page not in getattr(engine, "pages", []):
        raise common._tag(RuntimeError("Table pagination tab closed."), common.ERR_BROWSER_CLOSED)
    if getattr(engine, "page_owners", {}).get(page) != getattr(engine, "session_id", None):
        raise common._tag(ValueError("Table pagination left its owning session."), common.ERR_GUARDED)


def _page_origin(url: str) -> tuple[str, str, int] | None:
    try:
        return api.origin(url)
    except ValueError:
        return None


def _ensure_page_origin(engine: Any, page: Any, origin: tuple[str, str, int]) -> None:
    if _page_origin(page.url) != origin:
        raise common._tag(ValueError("Table pagination stopped because the page left its starting origin."), common.ERR_GUARDED)


def _check_next_href(page: Any, locator: Any, origin: tuple[str, str, int]) -> None:
    href = locator.get_attribute("href")
    if not href:
        return
    resolved = urllib.parse.urljoin(page.url, href)
    if _page_origin(resolved) != origin:
        raise common._tag(ValueError("Next control links outside the starting page origin."), common.ERR_GUARDED)


def _write_export(engine: Any, args: dict[str, Any], document: dict[str, Any], export_format: str) -> str:
    command_id = args.get("_command_id")
    if not isinstance(command_id, str) or not _COMMAND_ID_RE.fullmatch(command_id):
        raise common._tag(ValueError("A valid command ID is required for table export."), common.ERR_VALIDATION)
    if export_format == "json":
        payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        suffix = ".json"
    else:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow([_csv_safe(value) for value in document["columns"]])
        for row in document["rows"]:
            writer.writerow([_csv_safe(value) for value in row["cells"]])
        payload = stream.getvalue().encode("utf-8")
        suffix = ".csv"
    if len(payload) > MAX_EXPORT_BYTES:
        raise common._tag(ValueError(f"Table export exceeds the {MAX_EXPORT_BYTES}-byte artifact limit."), common.ERR_VALIDATION)

    paths = engine.paths
    workspace = paths.workspace.resolve()
    root = paths.dir.resolve()
    if not (workspace == root or workspace.parent == root / "sessions"):
        raise common._tag(ValueError("Unsafe table export workspace."), common.ERR_GUARDED)
    output_dir = paths.outputs
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_dir = workspace / "outputs"
    if output_dir.resolve() != expected_dir:
        raise common._tag(ValueError("Unsafe table export directory."), common.ERR_GUARDED)
    target = output_dir / (command_id + suffix)
    temporary = output_dir / ("." + command_id + suffix + ".tmp")
    if target.exists():
        raise common._tag(ValueError("A table export already exists for this command."), common.ERR_GUARDED)
    engine.journal.require_space(len(payload), workspace=paths.workspace)
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            raise common._tag(ValueError("A table export already exists for this command."), common.ERR_GUARDED)
        temporary.replace(target)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if getattr(exc, "error_class", None):
            raise
        raise common._tag(RuntimeError("Table export could not be saved."), "artifact_unavailable") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return str(target).replace("\\", "/")


def _csv_safe(value: str) -> str:
    value = str(value)
    first = next((char for char in value if not (
        char.isspace() or unicodedata.category(char) in {"Cc", "Cf"} or char == "\ufeff"
    )), "")
    if first in {"=", "+", "-", "@"}:
        return "'" + value
    return value
