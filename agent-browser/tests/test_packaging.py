"""The Playwright range the package installs is the range the tool has been run against."""
from __future__ import annotations

import re
from pathlib import Path

from agent_browser_core import common

ROOT = Path(__file__).resolve().parents[1]


def _bounds() -> tuple[tuple[int, int], tuple[int, int]]:
    match = re.fullmatch(r"playwright>=(\d+)\.(\d+),<(\d+)\.(\d+)", common.PLAYWRIGHT_REQUIREMENT)
    assert match, common.PLAYWRIGHT_REQUIREMENT
    low_major, low_minor, high_major, high_minor = (int(group) for group in match.groups())
    return (low_major, low_minor), (high_major, high_minor)


def _line(text: str) -> tuple[int, int]:
    major, minor = text.split(".")
    return int(major), int(minor)


def test_pyproject_installs_exactly_the_documented_range() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"{common.PLAYWRIGHT_REQUIREMENT}"' in pyproject
    assert len(re.findall(r'"playwright[<>=][^"]*"', pyproject)) == 1


def test_the_range_ends_at_the_newest_tested_line() -> None:
    # The snapshot text, the aria-ref engine and the error messages this tool reads are not a versioned Playwright
    # interface. A line newer than the newest tested one must not install until the live suite has passed on it.
    low, high = _bounds()
    tested = sorted(_line(text) for text in common.TESTED_PLAYWRIGHT)
    assert tested[0] == low
    assert high == (tested[-1][0], tested[-1][1] + 1)


def test_the_install_hint_and_the_readme_name_the_same_range() -> None:
    assert common.PLAYWRIGHT_REQUIREMENT in common._hint("playwright_missing")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert common.PLAYWRIGHT_REQUIREMENT in readme
    assert "playwright>=1.61,<2" not in readme
