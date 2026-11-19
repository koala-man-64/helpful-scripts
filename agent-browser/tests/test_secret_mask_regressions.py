"""Screenshot masks include secret editable fields in every frame."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_browser_core import daemon as core_daemon
from conftest import FakeElement, FakeLocator


class CandidateSet:
    """A frame's candidate locator: every control's SECRET_JS answer in one evaluate_all round trip."""

    def __init__(self, candidates: list[FakeLocator], *, broken: Any = False) -> None:
        self.candidates = candidates
        self.broken = broken              # True: the frame cannot be evaluated; "no-list": it answers something else
        self.round_trips = 0

    def evaluate_all(self, js: str) -> Any:
        assert js == core_daemon.SECRET_ALL_JS
        self.round_trips += 1
        if self.broken == "no-list":
            return None
        if self.broken:
            raise RuntimeError("frame was detached")
        infos = []
        for locator in self.candidates:
            try:
                infos.append(locator.evaluate(core_daemon.SECRET_JS))
            except Exception:  # noqa: BLE001 - the in-page try/catch reports a control that throws as unknown
                infos.append({"unknown": True})
        return infos

    def nth(self, index: int) -> FakeLocator:
        return self.candidates[index]


class MaskFrame:
    def __init__(self, page: Any, name: str, *, inputs: list[FakeElement], editables: list[FakeElement], broken: Any = False) -> None:
        self.page, self.name = page, name
        self.inputs, self.editables = inputs, editables
        self.broken = broken
        self.candidate_sets: list[CandidateSet] = []

    def locator(self, selector: str) -> Any:
        if selector.startswith('input[type="password"]'):
            return FakeLocator(self.page, None, count_override=0)
        if selector.startswith("input:not("):
            locators = [FakeLocator(self.page, element) for element in self.inputs]
            # Model CSS matching: contenteditable candidates are found only once the production
            # selector explicitly includes that element type.
            if "[contenteditable]" in selector:
                locators.extend(FakeLocator(self.page, element) for element in self.editables)
            self.candidate_sets.append(CandidateSet(locators, broken=self.broken))
            return self.candidate_sets[-1]
        raise AssertionError(f"Unexpected screenshot mask selector: {selector}")


def _secret_info(tag: str, *, editable: bool = False, text: str = "security code") -> dict[str, Any]:
    return {"tag": tag, "type": "text" if tag == "INPUT" else "", "autocomplete": "", "inputmode": "",
            "maxlength": -1, "text": text, "contenteditable": editable}


def _secret_refs(masks: list[Any]) -> set[str]:
    return {mask.element.ref for mask in masks if getattr(mask, "element", None) is not None}


def test_manual_and_commit_screenshots_mask_native_and_contenteditable_secrets_in_iframes(
    make_daemon, monkeypatch
) -> None:
    daemon, _ctx, page = make_daemon()
    native = FakeElement(ref="native-code", tag="INPUT", secret_info=_secret_info("INPUT"))
    clear = FakeElement(ref="search", tag="INPUT", secret_info=_secret_info("INPUT", text="Search"))
    editable = FakeElement(ref="editable-code", tag="DIV", secret_info=_secret_info("DIV", editable=True))
    unknown = FakeElement(ref="unknown-editable", tag="DIV", raise_on={"secret_check": RuntimeError("detached")})

    main_frame = MaskFrame(page, "main", inputs=[native, clear], editables=[])
    child_frame = MaskFrame(page, "child", inputs=[], editables=[editable, unknown])
    page.frames = [main_frame, child_frame]

    captures: list[tuple[str, list[Any]]] = []

    def screenshot(path: str, **kwargs: Any) -> None:
        masks = kwargs.get("mask") or []
        captures.append((path, masks))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setattr(page, "screenshot", screenshot)

    daemon.dispatch("screenshot", {}, 1)
    daemon._capture("a" * 32, "before", page)

    assert len(captures) == 2
    for _path, masks in captures:
        assert {"native-code", "editable-code", "unknown-editable"} <= _secret_refs(masks)
        assert "search" not in _secret_refs(masks)


def test_a_large_form_is_inspected_in_one_round_trip_per_frame(make_daemon) -> None:
    # One evaluate per control took 11 s on a form with 800 inputs, and every commit takes two screenshots.
    daemon, _ctx, page = make_daemon()
    fields = [FakeElement(ref=f"field-{i}", tag="INPUT", secret_info=_secret_info("INPUT", text=f"Field {i}")) for i in range(800)]
    fields[412] = FakeElement(ref="recovery-code", tag="INPUT", secret_info=_secret_info("INPUT", text="recovery code"))
    main_frame = MaskFrame(page, "main", inputs=fields, editables=[])
    child_frame = MaskFrame(page, "child", inputs=[], editables=[])
    page.frames = [main_frame, child_frame]

    masks = daemon._secret_masks(page)

    assert _secret_refs(masks) == {"recovery-code"}
    assert [len(frame.candidate_sets) for frame in (main_frame, child_frame)] == [1, 1]
    assert [frame.candidate_sets[0].round_trips for frame in (main_frame, child_frame)] == [1, 1]


@pytest.mark.parametrize("broken", [True, "no-list"])
def test_a_frame_whose_controls_cannot_be_inspected_is_masked_whole(make_daemon, broken: Any) -> None:
    daemon, _ctx, page = make_daemon()
    hidden_from_us = FakeElement(ref="unreadable", tag="INPUT", secret_info=_secret_info("INPUT", text="Search"))
    readable = FakeElement(ref="search", tag="INPUT", secret_info=_secret_info("INPUT", text="Search"))
    broken_frame = MaskFrame(page, "navigating", inputs=[hidden_from_us], editables=[], broken=broken)
    page.frames = [MaskFrame(page, "main", inputs=[readable], editables=[]), broken_frame]

    masks = daemon._secret_masks(page)

    # Nothing in the broken frame was confirmed clear, so its whole candidate locator is a mask.
    assert broken_frame.candidate_sets[0] in masks
    assert "search" not in _secret_refs(masks)
