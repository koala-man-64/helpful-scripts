"""Resolved-target uniqueness checks for fill-form preflight."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_browser_core import common
from agent_browser_core.actions import FORM_ELEMENT_IDENTITY_JS
from conftest import FakeFrame, FakeLocator


def _install_form_preflight_fakes(monkeypatch: pytest.MonkeyPatch):
    created = []
    original_evaluate = FakeLocator.evaluate
    original_element_handle = FakeLocator.element_handle

    def element_handle(locator: FakeLocator, timeout=None):
        handle = original_element_handle(locator, timeout)
        created.append(handle)
        return handle

    def evaluate(locator: FakeLocator, script: str, arg=None, timeout=None):
        if script == FORM_ELEMENT_IDENTITY_JS:
            return original_evaluate(locator, script, arg=arg, timeout=timeout)
        if "readOnly:el.readOnly" in script:
            tag = locator.element.tag.lower()
            if locator.element.role == "textbox" and tag == "div":
                tag = "input"
            return {
                "tag": tag,
                "type": "text",
                "disabled": False,
                "readOnly": False,
                "multiple": False,
                "options": [],
            }
        return original_evaluate(locator, script, arg=arg, timeout=timeout)

    monkeypatch.setattr(FakeLocator, "element_handle", element_handle)
    monkeypatch.setattr(FakeLocator, "evaluate", evaluate)
    return created


def _label_matches(page, target: str):
    def get_by_label(frame, label: str, *, exact: bool):
        assert exact
        locator = FakeLocator(page, page.elements["e1"]) if frame is page.main_frame and label == target else None
        matches = [locator] if locator else []
        return SimpleNamespace(count=lambda: len(matches), nth=lambda index: matches[index])

    return get_by_label


@pytest.mark.parametrize(
    ("first_target", "second_target"),
    [
        ("e1", {"ref": "e1"}),
        ("[ref=e1]", {"ref": "ref=e1"}),
    ],
)
def test_form_ref_aliases_are_rejected_before_any_field_is_filled(
    make_daemon, monkeypatch: pytest.MonkeyPatch, first_target, second_target
) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text='- textbox "Name" [ref=e1]\n- textbox "Other" [ref=e2]\n')
    daemon.dispatch("snapshot", {}, 5)
    handles = _install_form_preflight_fakes(monkeypatch)

    with pytest.raises(ValueError, match="Duplicate form target") as error:
        daemon.dispatch("fill-form", {"fields": [
            {"target": "e2", "value": "must stay untouched"},
            {"target": first_target, "value": "first"},
            {"target": second_target, "value": "second"},
        ]}, 5)

    assert error.value.error_class == common.ERR_VALIDATION
    assert page.elements["e1"].value == ""
    assert page.elements["e2"].value == ""
    assert not page.elements["e1"].calls and not page.elements["e2"].calls
    assert all(handle.disposed for handle in handles)


def test_semantic_and_ref_aliases_are_rejected_before_any_field_is_filled(
    make_daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text='- textbox "Name" [ref=e1]\n- textbox "Other" [ref=e2]\n')
    daemon.dispatch("snapshot", {}, 5)
    monkeypatch.setattr(FakeFrame, "get_by_label", _label_matches(page, "Name"), raising=False)
    handles = _install_form_preflight_fakes(monkeypatch)

    with pytest.raises(ValueError, match="Duplicate form target") as error:
        daemon.dispatch("fill-form", {"fields": [
            {"target": "e2", "value": "must stay untouched"},
            {"target": {"label": "Name"}, "value": "first"},
            {"target": "e1", "value": "second"},
        ]}, 5)

    assert error.value.error_class == common.ERR_VALIDATION
    assert page.elements["e1"].value == ""
    assert page.elements["e2"].value == ""
    assert not page.elements["e1"].calls and not page.elements["e2"].calls
    assert all(handle.disposed for handle in handles)


def test_distinct_form_targets_still_fill_and_dispose_preflight_handles(
    make_daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text='- textbox "Name" [ref=e1]\n- textbox "Other" [ref=e2]\n')
    daemon.dispatch("snapshot", {}, 5)
    handles = _install_form_preflight_fakes(monkeypatch)

    result = daemon.dispatch("fill-form", {"fields": [
        {"target": "e1", "value": "first"},
        {"target": "e2", "value": "second"},
    ]}, 5)

    assert result["completed_fields"] == [0, 1]
    assert page.elements["e1"].value == "first"
    assert page.elements["e2"].value == "second"
    assert all(handle.disposed for handle in handles)
