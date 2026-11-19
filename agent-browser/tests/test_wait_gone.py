"""wait --gone and --text-gone: wait until something is no longer on the page."""
from __future__ import annotations

from typing import Any

import pytest

from agent_browser_core import common
from conftest import FakeFrame, FakeLocator, FakePlaywrightError

PAGE = '- generic [ref=e1]:\n  - status "Saving" [ref=e2]\n  - button "Save" [ref=e3]\n'


def _wait(daemon: Any, **conditions: Any) -> dict[str, Any]:
    return daemon.dispatch("wait", {**conditions, "_cooperative": True}, 30)


def _poll(daemon: Any, pending: dict[str, Any]) -> dict[str, Any]:
    return daemon.dispatch("poll", {"command_id": pending["command_id"]}, 2)


# ---------------------------------------------------------------------------
# The cooperative wait every CLI and MCP call uses


def test_wait_gone_holds_while_the_ref_is_visible_and_ends_when_it_is_not(make_daemon: Any) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=PAGE)
    daemon.dispatch("snapshot", {}, 5.0)

    pending = _wait(daemon, gone="e2")
    assert pending["pending"] is True and pending["satisfied"] == []
    assert _poll(daemon, pending)["pending"] is True

    page.elements["e2"].visible = False
    done = _poll(daemon, pending)
    assert done["pending"] is False and done["satisfied"] == ["gone"]
    assert done["gone_seen"] is True          # it was there, then it went
    assert daemon.jobs == {}


def test_a_ref_that_is_no_longer_on_the_page_is_gone(make_daemon: Any) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=PAGE)
    daemon.dispatch("snapshot", {}, 5.0)
    page.elements["e2"].exists = False        # the spinner was removed from the DOM before the wait began

    done = _wait(daemon, gone="e2")

    assert done["pending"] is False and done["satisfied"] == ["gone"]
    assert done["gone_seen"] is False         # never seen: it had already gone, or the ref never matched


def test_wait_gone_takes_a_selector_and_checks_every_frame(make_daemon: Any) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=PAGE)
    inner = FakeFrame(name="child")
    page.frames.append(inner)
    inner.selector_matches[".spinner"] = 2

    pending = _wait(daemon, gone=".spinner")
    assert pending["pending"] is True

    inner.selector_matches[".spinner"] = 0
    done = _poll(daemon, pending)
    assert done["satisfied"] == ["gone"] and done["gone_seen"] is True


def test_a_selector_that_cannot_be_parsed_is_an_error_not_an_absence(make_daemon: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # "Nothing matches" must never be how a typo in the selector reads.
    daemon, _ctx, page = make_daemon(snapshot_text=PAGE)

    def bad_selector(selector: str) -> Any:
        raise FakePlaywrightError('Locator.count: Unexpected token "[" while parsing css selector "' + selector + '"')

    monkeypatch.setattr(page.main_frame, "locator", bad_selector)

    with pytest.raises(ValueError) as raised:
        _wait(daemon, gone="div[[")

    assert raised.value.error_class == common.ERR_VALIDATION and daemon.jobs == {}


def test_a_frame_that_cannot_be_read_is_not_evidence_of_absence(make_daemon: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=PAGE)
    navigating = {"now": True}
    real_locator = page.main_frame.locator

    def locator(selector: str) -> Any:
        if navigating["now"]:
            raise FakePlaywrightError("Execution context was destroyed, most likely because of a navigation")
        return real_locator(selector)

    monkeypatch.setattr(page.main_frame, "locator", locator)

    pending = _wait(daemon, gone=".spinner", text_gone="Saving")
    assert pending["pending"] is True and pending["satisfied"] == []

    navigating["now"] = False
    done = _poll(daemon, pending)
    assert done["pending"] is False and set(done["satisfied"]) == {"gone", "text_gone"}


def test_wait_text_gone_holds_until_no_frame_shows_the_text(make_daemon: Any) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=PAGE)
    inner = FakeFrame(name="child")
    page.frames.append(inner)
    inner.body_text = "Record INC0010023 - SAVING changes"   # case-insensitive, in an inner frame

    pending = _wait(daemon, text_gone="Saving")
    assert pending["pending"] is True

    inner.body_text = "Record INC0010023 - Saved"
    done = _poll(daemon, pending)
    assert done["pending"] is False and done["satisfied"] == ["text_gone"] and "gone_seen" not in done


def test_gone_combines_with_the_other_conditions(make_daemon: Any) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=PAGE)
    daemon.dispatch("snapshot", {}, 5.0)
    page.elements["e2"].visible = False

    pending = _wait(daemon, gone="e2", text="Saved")
    assert pending["pending"] is True and pending["satisfied"] == ["gone"]   # every condition must hold

    page.main_frame.body_text = "Saved"
    assert set(_poll(daemon, pending)["satisfied"]) == {"gone", "text"}


# ---------------------------------------------------------------------------
# The blocking path (a direct dispatch without the cooperative flag)


def test_blocking_wait_supports_the_same_conditions(make_daemon: Any) -> None:
    daemon, _ctx, page = make_daemon(snapshot_text=PAGE)
    daemon.dispatch("snapshot", {}, 5.0)
    page.elements["e2"].visible = False
    page.main_frame.body_text = "Saved"

    done = daemon.dispatch("wait", {"gone": "e2", "text_gone": "Saving"}, 5.0)

    assert set(done["satisfied"]) == {"gone", "text_gone"} and done["gone_seen"] is False


def test_gone_alone_is_a_condition_for_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_browser_core import cli

    sent: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(cli, "_session_for", lambda args, verb, autostart=False: (None, {"token": "t", "port": 1, "profile": "default"}))
    monkeypatch.setattr(cli.core_session, "_request", lambda session, cmd, payload, timeout, **kw: sent.append((cmd, payload)) or {"ok": True})

    assert cli.main(["wait", "--gone", "#spinner", "--text-gone", "Saving"]) == 0

    command, payload = sent[0]
    assert command == "wait" and payload["gone"] == "#spinner" and payload["text_gone"] == "Saving"
    assert "--gone" in common._hint("no_wait_condition") and "--text-gone" in common._hint("no_wait_condition")


def test_visible_filter_fake_matches_playwright_semantics() -> None:
    # The fake's filter(visible=True) is what _gone relies on: hidden matches do not count.
    from conftest import FakeElement

    shown = FakeLocator(None, FakeElement(ref="a", visible=True), count_override=3)
    hidden = FakeLocator(None, FakeElement(ref="b", visible=False), count_override=3)
    assert shown.filter(visible=True).count() == 3 and hidden.filter(visible=True).count() == 0
