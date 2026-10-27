"""Real local browser coverage for form ownership after an input opens a popup."""
import json

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
