"""Live CLI coverage for bounded pagination and table exports."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from test_live_expansion import PROFILE, cli, live_home, site, pytestmark


def test_live_async_pagination_deduplicates_and_exports_complete_json(cli, site):
    session = "table-pages"
    cli.ok("goto", f"{site.url}/audit_table_pages.html", "--session", session)

    result = cli.ok(
        "table", "#records", "--next", "#next", "--max-pages", "10", "--max-rows", "20",
        "--column", "Name", "--key", "ID", "--export", "json", "--session", session,
    )

    assert result["columns"] == ["Name"]
    assert result["rows"] == [{"cells": ["A"]}, {"cells": ["B"]}, {"cells": ["C"]}, {"cells": ["D"]}]
    assert all("ref" not in row for row in result["rows"])
    assert result["pages"] == 3 and result["row_count"] == 4 and result["rows_scanned"] == 6
    assert result["duplicate_rows"] == 2 and result["loop_count"] == 0
    assert result["complete"] is True and result["truncated"] is False
    assert result["stop_reason"] == "next_disabled"
    exported = json.loads(Path(result["export_file"]).read_text(encoding="utf-8"))
    assert exported["page_count"] == 3 and exported["row_count"] == 4
    assert exported["rows"] == result["rows"] and exported["complete"] is True


@pytest.mark.parametrize(
    ("mode", "expected_reason", "expected_pages", "expected_rows", "expected_loops"),
    [
        ("drift", "schema_changed", 2, 1, 0),
        ("loop", "page_loop", 3, 2, 1),
    ],
)
def test_live_schema_drift_and_page_loop_stop_with_partial_metadata(
    cli, site, mode, expected_reason, expected_pages, expected_rows, expected_loops
):
    session = "table-" + mode
    cli.ok("goto", f"{site.url}/audit_table_pages.html?mode={mode}", "--session", session)

    result = cli.ok("table", "#records", "--next", "#next", "--session", session)

    assert result["stop_reason"] == expected_reason
    assert result["pages"] == expected_pages and result["row_count"] == expected_rows
    assert result["loop_count"] == expected_loops
    assert result["complete"] is False and result["truncated"] is True
    assert all("ref" not in row for row in result["rows"])


def test_live_csv_formula_cells_are_neutralized_and_unicode_is_preserved(cli, site):
    session = "table-csv"
    cli.ok("goto", f"{site.url}/audit_table_csv.html", "--session", session)

    result = cli.ok("table", "#records", "--export", "csv", "--session", session)

    with Path(result["export_file"]).open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows == [
        ["'@Name", "Text"],
        ["'=1+1", "雪" + "\n" + '"quoted"'],
        ["'-2", "'+SUM(A1:A2)"],
    ]
