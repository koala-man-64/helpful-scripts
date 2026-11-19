"""Live: wait until something is no longer on the page, and list the sessions that hold evidence."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_live_expansion import cli, live_home, site


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("AGENT_BROWSER_LIVE") != "1", reason="set AGENT_BROWSER_LIVE=1 to run the live wait checks"),
]


def test_wait_gone_and_text_gone_follow_a_status_line(cli, site):
    session = "wait-gone"
    cli.ok("goto", f"{site.url}/wait_gone.html", "--session", session)

    cli.ok("click", "--role", "button", "--name", "Save", "--session", session)   # the status line shows for 8 s
    done = cli.ok("wait", "--gone", "#spinner", "--timeout", "40", "--session", session)
    assert done["pending"] is False and done["satisfied"] == ["gone"] and done["gone_seen"] is True, done
    assert "Saved" in cli.ok("text", "--session", session)["text"]

    cli.ok("click", "--role", "button", "--name", "Save", "--session", session)
    done = cli.ok("wait", "--text-gone", "Saving changes", "--text", "Saved", "--timeout", "40", "--session", session)
    assert set(done["satisfied"]) == {"text_gone", "text"}, done


def test_wait_gone_on_something_never_there_says_so_and_a_bad_selector_is_an_error(cli, site):
    session = "wait-gone-absent"
    cli.ok("goto", f"{site.url}/wait_gone.html", "--session", session)

    absent = cli.ok("wait", "--gone", "#never-there", "--session", session)
    assert absent["satisfied"] == ["gone"] and absent["gone_seen"] is False, absent

    code, _out, error = cli("wait", "--gone", "div[[", "--session", session)
    assert code == 2 and error["class"] == "validation", error


def test_journal_sessions_lists_the_sessions_that_hold_receipts(cli, site):
    for session in ("listing-a", "listing-b"):
        cli.ok("goto", f"{site.url}/home.html", "--session", session)

    listed = cli.ok("journal", "--sessions", "--session", "listing-a")

    by_id = {entry["session"]: entry for entry in listed["sessions"]}
    assert by_id["listing-a"]["receipts"] >= 1 and by_id["listing-a"]["current"] is True, listed
    assert by_id["listing-b"]["receipts"] >= 1 and by_id["listing-b"]["current"] is False, listed
    assert listed["evidence_retention_days"] is None and "--session" in listed["note"]
