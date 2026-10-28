"""Independent high-risk regressions for the expansion's action boundaries."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_browser_core import common as core_common
from conftest import FakeElement, FakeFrame, FakeLocator, SemanticMatch, SemanticMatches


def test_semantic_ambiguity_reports_candidates_across_frames(make_daemon, outer_text: str, monkeypatch) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    daemon.dispatch("goto", {"url": "http://x/outer.html"}, 5.0)
    matches = {
        page.main_frame: [SemanticMatch("e90", "Save")],
        page.frames[1]: [SemanticMatch("e91", "Save")],
    }
    monkeypatch.setattr(
        FakeFrame,
        "get_by_role",
        lambda frame, role, **_kwargs: SemanticMatches(matches.get(frame, [])) if role == "button" else SemanticMatches([]),
        raising=False,
    )

    with pytest.raises(ValueError) as exc:
        daemon.resolve(page, {"role": "button", "name": "Save"})

    assert exc.value.error_class == core_common.ERR_AMBIGUOUS
    candidates = exc.value.details["candidates"]
    assert [item["ref"] for item in candidates] == ["e90", "f1e91"]
    assert {item["frame"] for item in candidates} == {"", "gsft_main"}


# A button labelled (aria-labelledby) by the wrapper of a password field repeats the password in its name.
LABELLED_PAGE = (
    "- generic [ref=e1]:\n"
    '  - text: "Pw:"\n'
    '  - textbox "PW" [ref=e2]: SECRETAAAA\n'
    '- button "Pw: SECRETAAAA" [ref=e90]\n'
    '- button "Pay [ref=e4]" [ref=e93]\n'
)


def test_semantic_ambiguity_candidates_never_carry_a_typed_value_or_a_forged_ref(make_daemon, monkeypatch) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=LABELLED_PAGE)
    page.elements["e2"].secret_info = {"tag": "INPUT", "type": "password", "autocomplete": "", "inputmode": "", "maxlength": -1,
                                       "text": "PW", "contenteditable": False}
    matches = [SemanticMatch("e90", "Pw: SECRETAAAA"), SemanticMatch("e93", "Pay [ref=e4]")]
    monkeypatch.setattr(FakeFrame, "get_by_role", lambda frame, role, **_kwargs: SemanticMatches(matches if frame is page.main_frame else []),
                        raising=False)

    with pytest.raises(ValueError) as exc:
        daemon.resolve(page, {"role": "button"})

    candidates = exc.value.details["candidates"]
    assert [item["ref"] for item in candidates] == ["e90", "e93"]  # the element's own ref, not [ref=] text in its name
    assert [item["description"] for item in candidates] == ['- button "Pw: ***"', '- button "Pay [ref=e4]"']
    assert "SECRETAAAA" not in str(candidates)


def test_fill_form_preflights_credentials_before_any_field_changes(make_daemon, monkeypatch) -> None:
    daemon, _ctx, page = make_daemon()
    display = FakeElement(ref="e20", name="Display name", tag="INPUT", value="before")
    password = FakeElement(ref="e21", name="Password", tag="INPUT", value="")
    password.secret_info = {
        "tag": "input", "type": "password", "autocomplete": "current-password",
        "inputmode": "", "maxlength": -1, "text": "Password", "contenteditable": False,
    }
    resolved = {"Display name": FakeLocator(page, display), "Password": FakeLocator(page, password)}
    monkeypatch.setattr(daemon, "resolve", lambda _page, target: resolved[target["label"]])
    original_evaluate = FakeLocator.evaluate

    def evaluate(locator, js: str, timeout=None):
        if js.startswith("el => ({tag:el.tagName.toLowerCase()") and "readOnly:el.readOnly" in js:
            kind = "password" if locator.element is password else "text"
            return {"tag": "input", "type": kind, "disabled": False, "readOnly": False,
                    "multiple": False, "options": []}
        return original_evaluate(locator, js, timeout)

    monkeypatch.setattr(FakeLocator, "evaluate", evaluate)

    with pytest.raises(ValueError) as exc:
        daemon.h_fill_form({"fields": [
            {"target": {"label": "Display name"}, "operation": "fill", "value": "after"},
            {"target": {"label": "Password"}, "operation": "fill", "value": "synthetic-placeholder"},
        ]}, 5.0)

    assert exc.value.error_class == core_common.ERR_GUARDED
    assert display.value == "before" and password.value == ""
    assert display.calls == [] and password.calls == []


def test_expectation_failure_keeps_one_completed_click_and_receipt(make_daemon, fake_clock) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text='- generic [ref=e1]:\n  - button "Save" [ref=e2]\n')
    button = page.elements["e2"]
    requests = []

    def post_once() -> None:
        requests.append("POST")
        response = SimpleNamespace(
            request=SimpleNamespace(method="POST"), url="http://x/save", status=201,
        )
        page.emit("response", response)

    button.click_side_effect = post_once
    first = daemon.dispatch("click", {"target": "e2", "expect": ["response:POST **/save:2xx"]}, 5.0)
    assert first["pending"] is False
    assert first["expectations"] == [{"kind": "response", "passed": True}]
    assert button.calls == [("click", "left")] and requests == ["POST"]

    daemon._text_counts = lambda _page, _text: {}
    pending = daemon.dispatch("click", {"target": "e2", "expect": ["text:never appears"]}, 5.0)
    assert pending["pending"] is True
    assert button.calls == [("click", "left"), ("click", "left")]
    assert requests == ["POST", "POST"]

    fake_clock.value += 6.0
    with pytest.raises(RuntimeError) as exc:
        daemon.dispatch("poll", {"command_id": pending["command_id"]}, 1.0)

    assert exc.value.error_class == core_common.ERR_TIMEOUT
    assert exc.value.details["executed"] is True
    assert button.calls == [("click", "left"), ("click", "left")]
    assert requests == ["POST", "POST"]


def test_annotated_screenshot_removes_overlay_when_capture_fails(make_daemon, outer_text: str, monkeypatch) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    overlay = {"present": False, "calls": 0}

    def evaluate(js: str, *_args):
        if "labels =>" in js:
            overlay["present"] = True
        elif "getElementById('__agent_browser_refs__')" in js:
            overlay["present"] = False
        overlay["calls"] += 1
        return None

    class _Unpositioned:
        def bounding_box(self, timeout: int = 500):
            return None

    monkeypatch.setattr(page, "evaluate", evaluate, raising=False)
    monkeypatch.setattr(daemon, "resolve", lambda _page, _ref: _Unpositioned())

    def fail_capture(**_kwargs):
        raise RuntimeError("synthetic screenshot failure")

    monkeypatch.setattr(page, "screenshot", fail_capture, raising=False)
    with pytest.raises(RuntimeError, match="synthetic screenshot failure"):
        daemon.h_screenshot({"annotate": True}, 5.0)

    assert overlay == {"present": False, "calls": 2}
