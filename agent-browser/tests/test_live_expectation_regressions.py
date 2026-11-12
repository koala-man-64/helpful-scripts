"""Live CLI coverage for the success expectation contract on an isolated loopback page."""

from __future__ import annotations

import os

import pytest

from test_live_expansion import cli, live_home, pytestmark, site


pytestmark = [*pytestmark, pytest.mark.skipif(os.getenv("AGENT_BROWSER_LIVE") != "1", reason="set AGENT_BROWSER_LIVE=1 to run live expectation journeys")]


def test_cli_action_expectations_cover_value_state_count_persistence_and_download(cli, live_home, site) -> None:
    assert live_home.exists()
    opened = cli.ok("goto", f"{site.url}/expectation_contract.html")
    assert opened["navigated"] is True

    filled = cli.ok("fill", "#memo", "persisted-value", "--expect", "value:#memo=persisted-value")
    assert filled["pending"] is False
    assert filled["expectations"] == [{"kind": "value", "passed": True}]

    checked = cli.ok("check", "#agree", "--expect", "checked:#agree")
    unchecked = cli.ok("uncheck", "#agree", "--expect", "unchecked:#agree")
    assert checked["expectations"] == [{"kind": "checked", "passed": True}]
    assert unchecked["expectations"] == [{"kind": "unchecked", "passed": True}]

    counted = cli.ok("click", "#add-row", "--expect", "count:tbody tr=2")
    assert counted["expectations"] == [{"kind": "count", "passed": True}]

    with site.server.save_lock:
        saves_before = site.server.save_calls
    saved = cli.ok(
        "click", "#save",
        "--expect", "response:POST **/save:2xx",
        "--expect", "text:Saved",
        "--expect", "persisted:#memo=persisted-value",
    )
    assert saved["pending"] is False
    assert saved["executed"] is True
    assert saved["navigated"] is True and saved["refs_valid"] is True
    assert all(item["passed"] for item in saved["expectations"])
    with site.server.save_lock:
        assert site.server.save_calls == saves_before + 1
    assert cli.ok("eval", "document.querySelector('#memo').value")["value"] == "persisted-value"

    downloaded = cli.ok("click", "#download", "--expect", "download:expectation_contract.csv")
    assert downloaded["pending"] is False
    assert downloaded["expectations"] == [{"kind": "download", "passed": True}]
