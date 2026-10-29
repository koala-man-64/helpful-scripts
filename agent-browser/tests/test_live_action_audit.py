"""Real local browser coverage for form ownership, selection and action diffs."""
import json

import pytest

from agent_browser_core import common
from test_live_expansion import PROFILE, cli, live_home, site, pytestmark


def test_form_popup_keeps_later_semantic_field_in_original_iframe(cli, site):
    session = "form-popup-audit"
    original = cli.ok("goto", f"{site.url}/expansion_form_popup.html", "--session", session)
    directory = common.ProfilePaths(PROFILE, cli.home, session).downloads
    directory.mkdir(parents=True, exist_ok=True)
    form = directory / "popup-form.json"
    form.write_text(json.dumps([
        {"target": {"label": "First"}, "operation": "check"},
        {"target": {"label": "Second"}, "value": "original only"},
    ]), encoding="utf-8")
    try:
        result = cli.ok("fill-form", "--file", str(form), "--session", session)
        assert result["completed_fields"] == [0, 1]
        assert result["tab_id"] == original["tab_id"]
        assert cli.ok("eval", "document.querySelector('iframe').contentDocument.querySelector('#second').value",
                      "--tab", original["tab_id"], "--session", session)["value"] == "original only"
        assert cli.ok("eval", "document.querySelector('#second').value", "--session", session)["value"] == ""
    finally:
        form.unlink(missing_ok=True)


@pytest.mark.parametrize("option_value", ["", "   "])
def test_form_selects_empty_option_after_earlier_field_write(cli, site, option_value):
    session = "empty-select-audit"
    cli.ok("goto", f"{site.url}/audit_select_changes.html", "--session", session)
    directory = common.ProfilePaths(PROFILE, cli.home, session).downloads
    directory.mkdir(parents=True, exist_ok=True)
    form = directory / "empty-select-form.json"
    form.write_text(json.dumps([
        {"target": {"label": "Display name"}, "value": "Updated name"},
        {"target": {"label": "Category"}, "operation": "select", "value": option_value},
    ]), encoding="utf-8")
    try:
        result = cli.ok("fill-form", "--file", str(form), "--session", session)
        assert result["completed_fields"] == [0, 1]
        values = cli.ok("eval", "[document.querySelector('#display-name').value, document.querySelector('#category').value]",
                        "--session", session)["value"]
        assert values == ["Updated name", option_value]
    finally:
        form.unlink(missing_ok=True)


def test_action_reports_truncation_when_41_controls_are_added(cli, site):
    session = "change-count-audit"
    cli.ok("goto", f"{site.url}/audit_select_changes.html", "--session", session)

    result = cli.ok("click", "--role", "button", "--name", "Add controls", "--session", session)

    assert result["truncated"] is True
    assert len(result["changes"]["added"]) == common.CHANGES_CAP
    count = cli.ok("eval", "document.querySelectorAll('#controls button').length", "--session", session)["value"]
    assert count == 41
