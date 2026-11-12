"""Screenshot masks include secret editable fields in every frame."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from conftest import FakeElement, FakeLocator


class CandidateSet:
    def __init__(self, candidates: list[FakeLocator]) -> None:
        self.candidates = candidates

    def all(self) -> list[FakeLocator]:
        return list(self.candidates)


class MaskFrame:
    def __init__(self, page: Any, name: str, *, inputs: list[FakeElement], editables: list[FakeElement]) -> None:
        self.page, self.name = page, name
        self.inputs, self.editables = inputs, editables

    def locator(self, selector: str) -> Any:
        if selector.startswith('input[type="password"]'):
            return FakeLocator(self.page, None, count_override=0)
        if selector.startswith("input:not("):
            locators = [FakeLocator(self.page, element) for element in self.inputs]
            # Model CSS matching: contenteditable candidates are found only once the production
            # selector explicitly includes that element type.
            if "[contenteditable]" in selector:
                locators.extend(FakeLocator(self.page, element) for element in self.editables)
            return CandidateSet(locators)
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
