"""Fail-closed bookkeeping for the document method used by persisted expectations."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import FakeElement


class NavigationRequest:
    def __init__(self, frame: Any, method: str, url: str):
        self.frame = frame
        self.method = method
        self.url = url

    def is_navigation_request(self) -> bool:
        return True


class Response:
    def __init__(self, request: NavigationRequest, status: int):
        self.request = request
        self.url = request.url
        self.status = status


class UnconfirmableNavigationRequest:
    method = "POST"
    url = "http://x/unconfirmable"

    def is_navigation_request(self) -> bool:
        return True

    @property
    def frame(self) -> Any:
        raise RuntimeError("frame is unavailable")


def _navigation(page: Any, method: str, url: str, status: int = 200, frame: Any = None) -> None:
    frame = frame or page.main_frame
    request = NavigationRequest(frame, method, url)
    page.emit("request", request)
    page.emit("response", Response(request, status))
    frame.url = url
    if frame is page.main_frame:
        page.url = url
    page.emit("framenavigated", frame)


def _main_get(page: Any) -> None:
    _navigation(page, "GET", page.url)


def _bind_css_id(page: Any, selector: str, ref: str) -> None:
    original = page.main_frame.locator

    def locator(value: str) -> Any:
        if value == selector:
            return page.locator("aria-ref=" + ref)
        return original(value)

    page.main_frame.locator = locator


def _field(page: Any) -> Any:
    field = page.elements["f1e3"]
    field.tag = "INPUT"
    field.value = "before"
    field.secret_info = {
        "tag": "INPUT", "type": "text", "autocomplete": "", "inputmode": "", "maxlength": -1,
        "text": "", "contenteditable": False,
    }
    _bind_css_id(page, "#memo", "f1e3")
    return field


def test_main_document_method_tracks_direct_post_and_final_redirect_method(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    _main_get(page)
    assert daemon._main_document_method(page) == "GET"

    post = NavigationRequest(page.main_frame, "POST", "http://x/redirect")
    page.emit("request", post)
    page.emit("response", Response(post, 303))
    get = NavigationRequest(page.main_frame, "GET", "http://x/final")
    page.emit("request", get)
    page.emit("response", Response(get, 200))
    page.url = page.main_frame.url = get.url
    page.emit("framenavigated", page.main_frame)
    assert daemon._main_document_method(page) == "GET"

    _navigation(page, "POST", "http://x/direct", 200)
    assert daemon._main_document_method(page) == "POST"


def test_pending_or_unobserved_main_navigation_is_unknown(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    _main_get(page)
    request = NavigationRequest(page.main_frame, "POST", "http://x/pending")
    page.emit("request", request)

    assert daemon._main_document_method(page) is None

    page.url = page.main_frame.url = "http://x/unobserved"
    page.emit("framenavigated", page.main_frame)
    assert daemon._main_document_method(page) is None


def test_unconfirmable_navigation_frame_fails_closed(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    _main_get(page)
    page.emit("request", UnconfirmableNavigationRequest())

    assert daemon._main_document_method(page) is None


def test_only_changed_fragment_navigation_preserves_known_method(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    _main_get(page)
    page.url = page.main_frame.url = page.url + "#section"
    page.emit("framenavigated", page.main_frame)
    assert daemon._main_document_method(page) == "GET"

    page.emit("framenavigated", page.main_frame)
    assert daemon._main_document_method(page) is None


def test_child_frame_post_does_not_replace_main_document_method(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    _main_get(page)
    child = page.frames[1]

    _navigation(page, "POST", "http://x/child-post", 200, frame=child)

    assert daemon._main_document_method(page) == "GET"


def test_persisted_refuses_direct_post_after_action_without_reloading(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    _main_get(page)
    field = _field(page)
    reloads: list[int] = []
    page.reload = lambda **_kwargs: reloads.append(1)
    button = page.elements["e4"]

    def submit_native_post() -> None:
        field.value = "stored"
        page.main_frame.body_text = "Saved direct"
        _navigation(page, "POST", "http://x/direct", 200)

    button.click_side_effect = submit_native_post
    with pytest.raises(RuntimeError) as exc:
        daemon.dispatch("click", {"target": "e4", "expect": [
            "response:POST **/direct:2xx", "text:Saved direct", "persisted:#memo=stored",
        ]}, 5.0)

    assert exc.value.error_class == "verification_unavailable"
    assert exc.value.details["executed"] is True
    command_id = exc.value.details["command_id"]
    assert daemon.journal.rows("manual", command_id)[0]["status"] == "unknown"
    assert reloads == []
    assert button.calls == [("click", "left")]
    assert daemon._main_document_method(page) == "POST"


def test_persisted_allows_post_redirected_to_get(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    _main_get(page)
    field = _field(page)
    reloads: list[int] = []

    def reload(*, timeout: int | None = None, wait_until: str = "load") -> None:
        assert wait_until == "load"
        reloads.append(1)
        field.value = "redirected value"

    page.reload = reload
    button = page.elements["e4"]

    def submit_redirecting_post() -> None:
        field.value = "redirected value"
        page.main_frame.body_text = "Saved after redirect"
        post = NavigationRequest(page.main_frame, "POST", "http://x/redirect")
        page.emit("request", post)
        page.emit("response", Response(post, 303))
        get = NavigationRequest(page.main_frame, "GET", "http://x/final")
        page.emit("request", get)
        page.emit("response", Response(get, 200))
        page.url = page.main_frame.url = get.url
        page.emit("framenavigated", page.main_frame)

    button.click_side_effect = submit_redirecting_post
    result = daemon.dispatch("click", {"target": "e4", "expect": [
        "response:GET **/final:2xx", "text:Saved after redirect", "persisted:#memo=redirected value",
    ]}, 5.0)

    assert result["pending"] is False
    assert result["executed"] is True
    assert reloads == [1]
    assert button.calls == [("click", "left")]
    assert result["expectations"] == [
        {"kind": "response", "passed": True},
        {"kind": "text", "passed": True},
        {"kind": "persisted", "passed": True},
    ]
    assert daemon._main_document_method(page) == "GET"


def test_document_method_state_is_released_when_page_closes(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    _main_get(page)

    assert daemon.main_document_state.get(page) is not None
    page.close()
    assert daemon.main_document_state.get(page) is None
