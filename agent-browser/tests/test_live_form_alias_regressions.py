"""Duplicate target aliases are refused through the real CLI and browser."""
import json

import pytest

from agent_browser_core import common
from test_live import ref
from test_live_expansion import PROFILE, cli, live_home, site, pytestmark


@pytest.mark.parametrize("first_kind", ["ref", "label"])
def test_form_aliases_are_rejected_without_changing_the_field(cli, site, first_kind):
    session = "form-alias-" + first_kind
    snapshot = cli.ok("goto", f"{site.url}/audit_select_changes.html", "--session", session)
    field_ref = ref(snapshot, r'textbox "Display name"')
    original = cli.ok("value", field_ref, "--session", session)["value"]
    directory = common.ProfilePaths(PROFILE, cli.home, session).downloads
    directory.mkdir(parents=True, exist_ok=True)
    form = directory / "duplicate-targets.json"
    form.write_text(json.dumps([
        {"target": field_ref if first_kind == "ref" else {"label": "Display name"}, "value": "first"},
        {"target": {"ref": field_ref}, "value": "second"},
    ]), encoding="utf-8")
    try:
        code, _out, error = cli("fill-form", "--file", str(form), "--session", session)
        assert code == 2 and error["class"] == "validation", error
        assert "Duplicate form target" in error["message"]
        assert error["details"]["executed"] is False
        assert cli.ok("value", field_ref, "--session", session)["value"] == original
    finally:
        form.unlink(missing_ok=True)
