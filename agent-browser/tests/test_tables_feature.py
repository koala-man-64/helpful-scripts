"""Offline tests for bounded table extraction and evidence exports."""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_browser_core import common
from agent_browser_core import tables


class _Collection:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def count(self) -> int:
        return len(self.values)

    def nth(self, index: int) -> Any:
        return self.values[index]

    def all(self) -> list[Any]:
        return list(self.values)


class _Locator:
    def __init__(self, text: str = "", *, page: "_Page | None" = None,
                 disabled: bool = False, href: str | None = None) -> None:
        self.text = text
        self.page = page
        self.disabled = disabled
        self.href = href

    def inner_text(self, timeout: float | None = None) -> str:
        return self.text

    def evaluate(self, script: str, timeout: float | None = None) -> Any:
        if script == tables.NEXT_DISABLED_JS:
            return self.disabled
        if script == tables.CELL_TEXT_JS:
            return self.text[:2000]
        if script == tables.HEADER_TEXT_JS:
            return self.text[:500]
        if script == tables.TABLE_TEXT_JS:
            return self.text[:100000]
        if script.startswith("el => el.tagName"):
            return False
        raise AssertionError(f"unexpected locator evaluation: {script}")

    def is_visible(self) -> bool:
        return True

    def is_enabled(self) -> bool:
        return not self.disabled

    def get_attribute(self, name: str) -> str | None:
        return self.href if name == "href" else None


class _Handle:
    def __init__(self, frame: object) -> None:
        self.frame = frame
        self.disposed = False

    def owner_frame(self) -> object:
        return self.frame

    def dispose(self) -> None:
        self.disposed = True


class _Row:
    def __init__(self, page: "_Page", cells: list[str], ref: str, *, header: bool = False) -> None:
        self.page, self.values, self.ref, self.header = page, cells, ref, header

    def is_visible(self) -> bool:
        return True

    def get_by_role(self, role: str) -> _Collection:
        return _Collection([_Locator(page=self.page)] if self.header and role == "columnheader" else [])

    def locator(self, selector: str) -> _Collection:
        assert selector == tables.CELL_SELECTOR
        return _Collection([_Locator(value, page=self.page) for value in self.values])

    def aria_snapshot(self, *, mode: str, timeout: float | None = None) -> str:
        assert mode == "ai"
        return f'- row [ref={self.ref}]'


class _Table:
    def __init__(self, page: "_Page", headers: list[str], rows: list[list[str]]) -> None:
        self.page, self.headers, self.data = page, headers, rows
        self._header_rows = [_Row(page, [], "head", header=True)]
        self._rows = [_Row(page, [], "head", header=True)] + [
            _Row(page, row, f"e{index}") for index, row in enumerate(rows, start=1)
        ]

    def evaluate(self, script: str, timeout: float | None = None) -> bool:
        if script == tables.TABLE_TEXT_JS:
            return self.inner_text()[:100000]
        assert script.startswith("el => el.tagName")
        return False

    def get_by_role(self, role: str) -> _Collection:
        if role == "columnheader":
            return _Collection([_Locator(name, page=self.page) for name in self.headers])
        if role == "row":
            return _Collection(self._rows)
        raise AssertionError(f"unexpected role: {role}")

    def element_handle(self) -> _Handle:
        return _Handle(self.page.main_frame)

    def inner_text(self, timeout: float | None = None) -> str:
        return "\n".join(["\t".join(self.headers), *("\t".join(row) for row in self.data)])


class _Page:
    def __init__(self, pages: list[dict[str, Any]], *, delays_ms: list[int] | None = None,
                 next_href: str | None = None) -> None:
        self.pages = pages
        self.delays_ms = delays_ms or [0] * len(pages)
        self.index = 0
        self.url = "http://127.0.0.1:4321/table"
        self.nav_count = 0
        self.main_frame = object()
        self.pending: tuple[int, float] | None = None
        self.next_href = next_href
        self.clicks = 0

    @property
    def table(self) -> _Table:
        page = self.pages[self.index]
        return _Table(self, page["columns"], page["rows"])

    @property
    def next_enabled(self) -> bool:
        return bool(self.pages[self.index].get("next", False))

    def schedule_next(self) -> None:
        self.clicks += 1
        self.pending = (self.index + 1, time.monotonic() + self.delays_ms[self.index] / 1000)

    def wait_for_timeout(self, milliseconds: int) -> None:
        time.sleep(min(milliseconds, 50) / 1000)
        if self.pending and time.monotonic() >= self.pending[1]:
            self.index = self.pending[0]
            self.nav_count += 1
            self.pending = None


class _NextLocator(_Locator):
    def __init__(self, page: _Page) -> None:
        super().__init__(page=page, href=page.next_href)
        self.next_page = page

    def is_enabled(self) -> bool:
        return self.next_page.next_enabled

    def evaluate(self, script: str, timeout: float | None = None) -> Any:
        if script == tables.NEXT_DISABLED_JS:
            return not self.next_page.next_enabled
        return super().evaluate(script, timeout)


class _Journal:
    def __init__(self) -> None:
        self.space_checks: list[int] = []

    def require_space(self, additional: int = 0, workspace: Path | None = None) -> None:
        self.space_checks.append(additional)


class _Engine:
    def __init__(self, tmp_path: Path, pages: list[dict[str, Any]], *, delays_ms: list[int] | None = None,
                 next_href: str | None = None) -> None:
        self.page = _Page(pages, delays_ms=delays_ms, next_href=next_href)
        self.pages = [self.page]
        self.session_id = "tables"
        self.page_owners = {self.page: self.session_id}
        self.served: dict[Any, Any] = {}
        self.table_rows: dict[Any, set[str]] = {}
        self.table_row_epochs: dict[Any, Any] = {}
        self.new_pages: list[Any] = []
        self.paths = common.ProfilePaths("tables", tmp_path, self.session_id)
        self.journal = _Journal()
        self.clicks = 0

    def target_page(self, args: dict[str, Any]) -> _Page:
        return self.page

    def _tab_id(self, page: _Page) -> str:
        return "t1"

    def _tab_index(self, page: _Page) -> int:
        return 1

    def _nav_signature(self, page: _Page) -> tuple[str, int, int]:
        return page.url, page.nav_count, page.index

    def _serve_snapshot(self, page: _Page) -> Any:
        served = SimpleNamespace(frame_by_prefix={"f2": page.main_frame})
        self.served[page] = served
        return served

    def _only_table(self, page: _Page) -> _Table:
        return page.table

    def resolve(self, page: _Page, target: Any) -> Any:
        if isinstance(target, str) and target in {"#grid", "table[aria-label='Records']"}:
            return page.table
        if target == {"role": "table", "name": "Records"}:
            return page.table
        if isinstance(target, str) and target in {"#next", "button.next"}:
            return _NextLocator(page)
        if target == {"role": "button", "name": "Next"}:
            return _NextLocator(page)
        raise common._tag(ValueError("Nothing matches target."), common.ERR_NOT_FOUND)

    def h_click(self, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        target = args["target"]
        assert isinstance(target, str) and target in {"#next", "button.next"} or target == {"role": "button", "name": "Next"}
        self.clicks += 1
        self.page.schedule_next()
        return {"action": "click"}


def _pages() -> list[dict[str, Any]]:
    return [
        {"columns": ["ID", "Name"], "rows": [["1", "A"], ["2", "B"]], "next": True},
        {"columns": ["ID", "Name"], "rows": [["2", "B"], ["3", "C"]], "next": True},
        {"columns": ["ID", "Name"], "rows": [["3", "C"], ["4", "D"]], "next": False},
    ]


def _args(**values: Any) -> dict[str, Any]:
    return {"target": "#grid", "_command_id": "a" * 32,
            "_deadline": common._now() + 10, **values}


def test_default_table_response_keeps_legacy_refs_and_shape(tmp_path: Path) -> None:
    engine = _Engine(tmp_path, _pages()[:1])

    result = tables.extract_table(engine, {"target": None}, 5)

    assert set(result) == {"columns", "rows", "tab", "tab_id", "untrusted"}
    assert result["columns"] == ["ID", "Name"]
    assert result["rows"] == [
        {"ref": "f2e1", "cells": ["1", "A"]},
        {"ref": "f2e2", "cells": ["2", "B"]},
    ]
    assert engine.table_rows[engine.page] == {"f2e1", "f2e2"}


def test_async_pagination_deduplicates_selects_columns_and_writes_json(tmp_path: Path) -> None:
    engine = _Engine(tmp_path, _pages(), delays_ms=[20, 20, 0])
    args = _args(next="#next", max_pages=10, max_rows=20, columns=["Name"], keys=["ID"], export_format="json")

    result = tables.extract_table(engine, args, 10)

    assert result["rows"] == [{"cells": ["A"]}, {"cells": ["B"]}, {"cells": ["C"]}, {"cells": ["D"]}]
    assert all("ref" not in row for row in result["rows"])
    assert (result["pages"], result["row_count"], result["rows_scanned"]) == (3, 4, 6)
    assert result["duplicate_rows"] == 2
    assert result["complete"] is True and result["truncated"] is False
    assert result["stop_reason"] == "next_disabled"
    assert result["loop_count"] == 0 and engine.clicks == 2
    assert engine.table_rows.get(engine.page, set()) == set()
    saved = json.loads(Path(result["export_file"]).read_text(encoding="utf-8"))
    assert saved["rows"] == result["rows"]
    assert saved["page_count"] == 3 and saved["duplicate_rows"] == 2 and saved["complete"] is True
    assert engine.journal.space_checks and engine.journal.space_checks[0] == Path(result["export_file"]).stat().st_size


def test_headerless_pages_use_generated_stable_column_names(tmp_path: Path) -> None:
    pages = [
        {"columns": [], "rows": [["a", "first"]], "next": True},
        {"columns": [], "rows": [["b", "second"]], "next": False},
    ]
    engine = _Engine(tmp_path, pages)

    result = tables.extract_table(
        engine,
        _args(next="#next", columns=["column_2"], keys=["column_1"]),
        10,
    )

    assert result["columns"] == ["column_2"]
    assert result["rows"] == [{"cells": ["first"]}, {"cells": ["second"]}]
    assert result["pages"] == 2 and result["complete"] is True
    assert result["stop_reason"] == "next_disabled"


def test_schema_change_stops_before_mixing_different_pages(tmp_path: Path) -> None:
    pages = [
        {"columns": ["ID", "Name"], "rows": [["1", "A"]], "next": True},
        {"columns": ["ID", "Other"], "rows": [["2", "changed"]], "next": False},
    ]
    engine = _Engine(tmp_path, pages)

    result = tables.extract_table(engine, _args(next="#next"), 10)

    assert result["columns"] == ["ID", "Name"]
    assert result["rows"] == [{"cells": ["1", "A"]}]
    assert result["pages"] == 2
    assert result["stop_reason"] == "schema_changed"
    assert result["complete"] is False and result["truncated"] is True


def test_same_key_with_different_cells_is_reported_as_conflict(tmp_path: Path) -> None:
    pages = [
        {"columns": ["ID", "Name"], "rows": [["1", "A"]], "next": True},
        {"columns": ["ID", "Name"], "rows": [["1", "changed"]], "next": False},
    ]
    engine = _Engine(tmp_path, pages)

    result = tables.extract_table(engine, _args(next="#next", keys=["ID"]), 10)

    assert result["rows"] == [{"cells": ["1", "A"]}]
    assert result["key_conflict_count"] == 1
    assert result["stop_reason"] == "key_conflict"
    assert result["complete"] is False


def test_repeated_page_is_bounded_and_reports_a_loop(tmp_path: Path) -> None:
    pages = [
        {"columns": ["ID", "Name"], "rows": [["1", "A"]], "next": True},
        {"columns": ["ID", "Name"], "rows": [["2", "B"]], "next": True},
        {"columns": ["ID", "Name"], "rows": [["1", "A"]], "next": True},
    ]
    engine = _Engine(tmp_path, pages)

    result = tables.extract_table(engine, _args(next="#next", max_pages=10), 10)

    assert [row["cells"] for row in result["rows"]] == [["1", "A"], ["2", "B"]]
    assert all("ref" not in row for row in result["rows"])
    assert result["pages"] == 3 and result["loop_count"] == 1
    assert result["duplicate_rows"] == 1
    assert result["stop_reason"] == "page_loop" and result["truncated"] is True


def test_exact_row_limit_is_complete_when_the_next_control_is_disabled(tmp_path: Path) -> None:
    page = [{"columns": ["ID", "Name"], "rows": [["1", "A"], ["2", "B"]], "next": False}]
    engine = _Engine(tmp_path, page)

    result = tables.extract_table(engine, _args(next="#next", max_rows=2), 10)

    assert result["row_count"] == 2
    assert result["complete"] is True and result["truncated"] is False
    assert result["stop_reason"] == "next_disabled"
    assert engine.clicks == 0


def test_csv_export_round_trips_unicode_newlines_and_quotes_and_neutralizes_formulas(tmp_path: Path) -> None:
    page = [{
        "columns": ["@Name", "Text"],
        "rows": [["=1+1", '雪\n"quoted"'], ["-2", "+SUM(A1:A2)"], ["\t=3", "plain"]],
        "next": False,
    }]
    engine = _Engine(tmp_path, page)

    result = tables.extract_table(engine, _args(columns=["@Name", "Text"], export_format="csv"), 10)

    with Path(result["export_file"]).open("r", encoding="utf-8", newline="") as handle:
        output = list(csv.reader(handle))
    assert output == [
        ["'@Name", "Text"],
        ["'=1+1", '雪\n"quoted"'],
        ["'-2", "'+SUM(A1:A2)"],
        ["'\t=3", "plain"],
    ]


def test_paginated_targets_and_next_control_cannot_use_refs(tmp_path: Path) -> None:
    engine = _Engine(tmp_path, _pages())

    with pytest.raises(ValueError) as next_error:
        tables.extract_table(engine, _args(next="e12"), 5)
    assert next_error.value.error_class == common.ERR_VALIDATION

    with pytest.raises(ValueError) as table_error:
        tables.extract_table(engine, _args(target="[ref=e12]", next="#next"), 5)
    assert table_error.value.error_class == common.ERR_VALIDATION
    assert engine.clicks == 0


def test_off_origin_anchor_is_rejected_before_the_click(tmp_path: Path) -> None:
    engine = _Engine(tmp_path, _pages(), next_href="https://outside.invalid/next")

    with pytest.raises(ValueError) as error:
        tables.extract_table(engine, _args(next="#next"), 10)

    assert error.value.error_class == common.ERR_GUARDED
    assert engine.clicks == 0


def test_export_failure_removes_same_command_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _Engine(tmp_path, [{"columns": ["ID"], "rows": [["1"]], "next": False}])
    original_replace = Path.replace

    def fail_replace(self: Path, target: Path) -> Path:
        if self.name.startswith("." + "a" * 32):
            raise OSError("injected export rename failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(RuntimeError):
        tables.extract_table(engine, _args(export_format="json"), 10)

    assert not list(engine.paths.outputs.glob("*"))
    assert not list(engine.paths.outputs.glob(".*.tmp"))
