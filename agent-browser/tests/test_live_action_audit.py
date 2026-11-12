"""Real local browser coverage for form ownership, selection and action diffs."""
import base64
import json
from pathlib import Path

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
        # The popup the form opened is the current tab, but the fill-form reply showed the form's tab: an untargeted
        # command is refused until a snapshot shows the popup.
        code, _out, error = cli("eval", "document.querySelector('#second').value", "--session", session)
        assert code == 2 and error["class"] == "stale_ref" and error["details"]["previous_tab"] == original["tab_id"], error
        assert cli.ok("snapshot", "--session", session)["tab_id"] == error["details"]["current_tab"]
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


def _sample_screenshot_pixels(cli, session, screenshot: Path, boxes: dict) -> dict[str, list[int]]:
    png = base64.b64encode(screenshot.read_bytes()).decode("ascii")
    script = """(async () => {
      const image = new Image();
      image.src = __PNG__;
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const context = canvas.getContext('2d', {willReadFrequently: true});
      context.drawImage(image, 0, 0);
      const boxes = __BOXES__;
      return Object.fromEntries(Object.entries(boxes).filter(([name]) => name !== 'viewport').map(([name, box]) => {
        const x = Math.floor((box.x + box.width / 2) * image.naturalWidth / boxes.viewport.width);
        const y = Math.floor((box.y + box.height / 2) * image.naturalHeight / boxes.viewport.height);
        return [name, Array.from(context.getImageData(x, y, 1, 1).data)];
      }));
    })()""".replace("__PNG__", json.dumps("data:image/png;base64," + png)).replace("__BOXES__", json.dumps(boxes))
    return cli.ok("eval", script, "--session", session)["value"]


def test_manual_and_commit_screenshots_mask_native_and_contenteditable_pixels(cli, site):
    session = "secret-mask-pixels"
    cli.ok("goto", f"{site.url}/audit_secret_editable.html", "--session", session)

    boxes = cli.ok("eval", """(() => {
      const rect = element => {
        const box = element.getBoundingClientRect();
        return {x: box.x, y: box.y, width: box.width, height: box.height};
      };
      const iframe = document.querySelector('#secret-frame');
      const outer = rect(iframe);
      const inner = rect(iframe.contentDocument.querySelector('#iframe-code'));
      return {
        viewport: {width: innerWidth, height: innerHeight},
        native: rect(document.querySelector('#native-code')),
        main: rect(document.querySelector('#main-code')),
        iframe: {x: outer.x + inner.x, y: outer.y + inner.y, width: inner.width, height: inner.height}
      };
    })()""", "--session", session)["value"]

    manual = cli.ok("screenshot", "--session", session)
    committed = cli.ok("commit", "click", "--role", "button", "--name", "Capture only", "--session", session)
    assert len(committed["screenshots"]) == 2
    captures = {
        "manual": Path(manual["path"]),
        "commit-before": Path(committed["screenshots"][0]),
        "commit-after": Path(committed["screenshots"][1]),
    }

    for name, screenshot in captures.items():
        pixels = _sample_screenshot_pixels(cli, session, screenshot, boxes)
        assert set(pixels) == {"native", "main", "iframe"}
        assert all(color[:3] == [255, 0, 255] for color in pixels.values()), (name, pixels)
