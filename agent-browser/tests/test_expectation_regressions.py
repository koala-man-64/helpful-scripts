"""Focused offline coverage for value, state, count, download and persistence expectations."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from agent_browser_core import actions as core_actions, common as core_common
from conftest import FakeElement


def _input(page: Any, ref: str = "f1e3", *, value: str = "", kind: str = "text") -> Any:
    element = page.elements[ref]
    element.tag = "INPUT"
    element.value = value
    element.secret_info = {
        "tag": "INPUT", "type": kind, "autocomplete": "", "inputmode": "", "maxlength": -1,
        "text": "", "contenteditable": False,
    }
    return element


def _bind_css_id(page: Any, selector: str, ref: str) -> None:
    original = page.main_frame.locator

    def locator(value: str) -> Any:
        if value == selector:
            return page.locator("aria-ref=" + ref)
        return original(value)

    page.main_frame.locator = locator


class FakeCDPSession:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.sent: list[str] = []
        self.detached = False

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def send(self, method: str, _params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sent.append(method)
        return {}

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        for handler in list(self.handlers.get(event, ())):
            handler(payload)

    def detach(self) -> None:
        self.detached = True


class FakeNavigationRequest:
    def __init__(self, frame: Any, method: str, url: str):
        self.frame = frame
        self.method = method
        self.url = url

    def is_navigation_request(self) -> bool:
        return True


class FakeResponse:
    def __init__(self, request: FakeNavigationRequest, status: int):
        self.request = request
        self.url = request.url
        self.status = status


def _main_navigation(page: Any, method: str, url: str, status: int = 200) -> None:
    request = FakeNavigationRequest(page.main_frame, method, url)
    page.emit("request", request)
    page.emit("response", FakeResponse(request, status))
    page.url = url
    page.main_frame.url = url
    page.emit("framenavigated", page.main_frame)


def _redirected_main_navigation(page: Any, final_url: str) -> None:
    post = FakeNavigationRequest(page.main_frame, "POST", "http://x/redirect")
    page.emit("request", post)
    page.emit("response", FakeResponse(post, 303))
    get = FakeNavigationRequest(page.main_frame, "GET", final_url)
    page.emit("request", get)
    page.emit("response", FakeResponse(get, 200))
    page.url = final_url
    page.main_frame.url = final_url
    page.emit("framenavigated", page.main_frame)


def _pending_download(daemon: Any, page: Any) -> tuple[dict[str, Any], FakeCDPSession]:
    cdp = FakeCDPSession()
    page.context = SimpleNamespace(new_cdp_session=lambda target: cdp)
    pending = daemon.dispatch("click", {"target": "e4", "expect": ["download:*.csv"]}, 5.0)
    assert pending["pending"] is True
    assert cdp.detached is False
    return pending, cdp


def test_value_expectation_is_exact_and_does_not_record_expected_value(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    field = _input(page)
    marker = "receipt-private=exact"
    button = page.elements["e4"]
    button.click_side_effect = lambda: setattr(field, "value", marker)

    result = daemon.dispatch("click", {"target": "e4", "expect": [f"value:f1e3={marker}"]}, 5.0)

    assert result["pending"] is False
    assert result["expectations"] == [{"kind": "value", "passed": True}]
    receipt = daemon.journal.rows("manual", result["command_id"])[0]
    assert marker not in receipt["arguments"]
    assert marker not in receipt["outcome"]
    assert marker not in json.dumps(result)


def test_value_expectation_refuses_credential_fields_before_action(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    _input(page, kind="password")

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("click", {"target": "e4", "expect": ["value:f1e3=anything"]}, 5.0)

    assert exc.value.error_class == core_common.ERR_GUARDED
    assert page.elements["e4"].calls == []


def test_checked_and_unchecked_expectations_require_native_checkbox_state(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    checkbox = _input(page, kind="checkbox")
    checkbox.role = "checkbox"
    checkbox.custom_evaluators[core_actions.CHECKED_STATE_JS] = lambda: checkbox.checked

    checked = daemon.dispatch("check", {"target": "f1e3", "expect": ["checked:f1e3"]}, 5.0)
    unchecked = daemon.dispatch("uncheck", {"target": "f1e3", "expect": ["unchecked:f1e3"]}, 5.0)

    assert checked["expectations"] == [{"kind": "checked", "passed": True}]
    assert unchecked["expectations"] == [{"kind": "unchecked", "passed": True}]


def test_count_expectation_uses_exact_frame_aggregate_after_action(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    page.main_frame.selector_matches["tr[data-kind=value]"] = 1
    page.frames[1].selector_matches["tr[data-kind=value]"] = 1
    button = page.elements["e4"]

    def add_row() -> None:
        page.main_frame.selector_matches["tr[data-kind=value]"] = 2

    button.click_side_effect = add_row
    result = daemon.dispatch("click", {"target": "e4", "expect": ["count:tr[data-kind=value]=3"]}, 5.0)

    assert result["pending"] is False
    assert result["expectations"] == [{"kind": "count", "passed": True}]


def test_persisted_waits_for_success_then_reloads_once_and_refreshes_refs(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    _main_navigation(page, "GET", page.url)
    field = _input(page)
    _bind_css_id(page, "#memo", "f1e3")
    server_value = {"value": ""}
    reloads: list[int] = []

    def reload(*, timeout: int | None = None, wait_until: str = "load") -> None:
        assert wait_until == "load"
        reloads.append(1)
        page.main_frame.body_text = ""
        replacement = FakeElement(ref="f1e3", role="textbox", value="hydrating")
        replacement.tag = "INPUT"
        replacement.secret_info = dict(field.secret_info)
        page.elements["f1e3"] = replacement

    page.reload = reload
    button = page.elements["e4"]

    def save_without_confirmation() -> None:
        server_value["value"] = "server value"

    button.click_side_effect = save_without_confirmation
    pending = daemon.dispatch(
        "click", {"target": "e4", "expect": ["text:Saved", "persisted:#memo=server value"]}, 5.0,
    )

    assert pending["pending"] is True
    assert reloads == []
    assert button.calls == [("click", "left")]

    page.main_frame.body_text = "Saved"
    completed = daemon.dispatch("poll", {"command_id": pending["command_id"]}, 5.0)

    assert completed["pending"] is True
    assert reloads == [1]
    replacement = page.elements["f1e3"]
    assert replacement is not field
    assert replacement.value == "hydrating"
    assert page.main_frame.body_text == ""
    assert button.calls == [("click", "left")]
    assert completed["command_id"] == pending["command_id"]
    assert completed["executed"] is True
    assert completed["refs_valid"] is True
    assert completed["navigated"] is True
    assert all(item["passed"] for item in completed["expectations"] if item["kind"] != "persisted")

    replacement.value = server_value["value"]
    done = daemon.dispatch("poll", {"command_id": pending["command_id"]}, 5.0)
    assert done["pending"] is False
    assert done["command_id"] == pending["command_id"]
    assert reloads == [1]
    assert button.calls == [("click", "left")]
    assert done["refs_valid"] is True
    assert "target_line" not in done and "changes" not in done and "value_after" not in done
    assert all(item["passed"] for item in done["expectations"])


def test_persisted_requires_success_signal_before_any_action(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    field = _input(page)
    _bind_css_id(page, "#memo", "f1e3")

    with pytest.raises(ValueError) as exc:
        daemon.dispatch("click", {"target": "e4", "expect": ["persisted:#memo=ready"]}, 5.0)

    assert exc.value.error_class == core_common.ERR_USAGE
    assert page.elements["e4"].calls == []
    assert field.calls == []


def test_persisted_mismatch_times_out_without_replaying_or_reloading_again(make_daemon, outer_text: str, fake_clock: Any) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    _main_navigation(page, "GET", page.url)
    field = _input(page)
    _bind_css_id(page, "#memo", "f1e3")
    reloads: list[int] = []

    def reload(*, timeout: int | None = None, wait_until: str = "load") -> None:
        reloads.append(1)
        field.value = "not persisted"

    page.reload = reload
    button = page.elements["e4"]
    button.click_side_effect = lambda: setattr(page.main_frame, "body_text", "Saved")

    pending = daemon.dispatch(
        "click", {"target": "e4", "expect": ["text:Saved", "persisted:#memo=desired"]}, 5.0,
    )
    assert pending["pending"] is True
    assert reloads == [1]
    assert button.calls == [("click", "left")]

    fake_clock.value += 10.0
    with pytest.raises(RuntimeError) as exc:
        daemon.dispatch("poll", {"command_id": pending["command_id"]}, 5.0)

    assert exc.value.error_class == core_common.ERR_TIMEOUT
    assert exc.value.details["executed"] is True
    assert reloads == [1]
    assert button.calls == [("click", "left")]


def test_download_expectation_is_nonblocking_and_waits_for_matching_completion(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    cdp = FakeCDPSession()
    page.context = SimpleNamespace(new_cdp_session=lambda target: cdp)
    button = page.elements["e4"]

    def start_downloads() -> None:
        cdp.emit("Page.downloadWillBegin", {"guid": "wrong", "suggestedFilename": "note.txt"})
        cdp.emit("Page.downloadProgress", {"guid": "wrong", "state": "completed"})
        cdp.emit("Page.downloadWillBegin", {"guid": "cancelled", "suggestedFilename": "report.csv"})
        cdp.emit("Page.downloadProgress", {"guid": "cancelled", "state": "canceled"})

    button.click_side_effect = start_downloads
    pending = daemon.dispatch("click", {"target": "e4", "expect": ["download:*.csv"]}, 5.0)

    assert pending["pending"] is True
    assert "Page.enable" in cdp.sent
    assert cdp.detached is False
    assert button.calls == [("click", "left")]

    cdp.emit("Page.downloadWillBegin", {"guid": "matched", "suggestedFilename": "report.csv"})
    cdp.emit("Page.downloadProgress", {"guid": "matched", "state": "inProgress"})
    still_pending = daemon.dispatch("poll", {"command_id": pending["command_id"]}, 5.0)
    assert still_pending["pending"] is True

    cdp.emit("Page.downloadProgress", {"guid": "matched", "state": "completed"})
    completed = daemon.dispatch("poll", {"command_id": pending["command_id"]}, 5.0)

    assert completed["pending"] is False
    assert completed["expectations"] == [{"kind": "download", "passed": True}]
    assert cdp.detached is True
    assert button.calls == [("click", "left")]


def test_download_expectation_preflight_failure_detaches_watcher(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    cdp = FakeCDPSession()
    page.context = SimpleNamespace(new_cdp_session=lambda target: cdp)

    with pytest.raises(ValueError):
        daemon.dispatch("click", {"target": "e4", "expect": ["download:*.csv", "value:e4=bad"]}, 5.0)

    assert cdp.detached is True
    assert page.elements["e4"].calls == []


def test_pending_download_watch_detaches_when_journal_is_cleared(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    _pending, cdp = _pending_download(daemon, page)

    daemon.dispatch("commit", {"operation": "journal", "clear": True}, 5.0)

    assert cdp.detached is True


def test_pending_download_watch_detaches_when_session_stops(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    _pending, cdp = _pending_download(daemon, page)

    stopped = daemon.dispatch("stop", {}, 5.0)

    assert stopped["stopped"] is True
    assert cdp.detached is True


def test_download_watch_detaches_when_action_raises(make_daemon, outer_text: str) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=outer_text)
    cdp = FakeCDPSession()
    page.context = SimpleNamespace(new_cdp_session=lambda target: cdp)
    page.elements["e4"].click_side_effect = lambda: (_ for _ in ()).throw(RuntimeError("click failed"))

    with pytest.raises(RuntimeError, match="click failed"):
        daemon.dispatch("click", {"target": "e4", "expect": ["download:*.csv"]}, 5.0)

    assert cdp.detached is True
