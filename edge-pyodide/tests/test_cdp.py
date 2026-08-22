"""CdpSession driven by the scripted FakeWebSocket from conftest.

Every test is single-threaded and offline: the fake returns queued inbound messages in
order (None simulates an empty recv slice) and records everything the session sends.
Deadline tests swap `ep._now` for a controllable clock so nothing ever sleeps.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import FakeWebSocket

import edge_pyodide as ep


class FakeClock:
    """Monotonic clock that advances by `step` on every read."""

    def __init__(self, start: float = 1000.0, step: float = 0.0) -> None:
        self.now = start
        self.step = step
        self.reads = 0

    def __call__(self) -> float:
        self.reads += 1
        current = self.now
        self.now += self.step
        return current


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(ep, "_now", fake)
    return fake


def reply(msg_id: int, result: dict[str, Any] | None = None) -> str:
    return json.dumps({"id": msg_id, "result": result if result is not None else {}})


def error_reply(msg_id: int, message: str, code: int = -32000) -> str:
    return json.dumps({"id": msg_id, "error": {"code": code, "message": message}})


def event(method: str, params: dict[str, Any] | None = None) -> str:
    return json.dumps({"method": method, "params": params or {}})


# ---------------------------------------------------------------------------
# call(): ids, params, replies


def test_call_sends_json_command_with_incrementing_ids() -> None:
    ws = FakeWebSocket(responder=lambda m: [{"id": m["id"], "result": {}}])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.call("Runtime.enable")
    cdp.call("Page.navigate", {"url": "about:blank"})
    assert ws.sent == [
        {"id": 1, "method": "Runtime.enable", "params": {}},
        {"id": 2, "method": "Page.navigate", "params": {"url": "about:blank"}},
    ]


def test_call_returns_result_dict_for_matching_reply() -> None:
    ws = FakeWebSocket(inbound=[reply(1, {"result": {"type": "string", "value": "ok"}})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.call("Runtime.evaluate", {"expression": "1"}) == {"result": {"type": "string", "value": "ok"}}


def test_call_returns_empty_dict_when_reply_has_no_result() -> None:
    ws = FakeWebSocket(inbound=[json.dumps({"id": 1})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.call("Page.enable") == {}


def test_call_returns_empty_dict_when_result_is_null() -> None:
    ws = FakeWebSocket(inbound=[json.dumps({"id": 1, "result": None})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.call("Page.enable") == {}


def test_call_skips_unrelated_events_and_other_ids_before_its_reply() -> None:
    ws = FakeWebSocket(inbound=[
        event("Runtime.consoleAPICalled", {"type": "log"}),
        reply(99, {"other": True}),
        None,
        event("Page.frameNavigated"),
        reply(1, {"mine": True}),
    ])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.call("Runtime.evaluate") == {"mine": True}
    assert ws.inbound == []


def test_call_keeps_reply_for_another_id_until_that_call_waits() -> None:
    # Reply 2 arrives before reply 1; call 1 must not consume it.
    ws = FakeWebSocket(inbound=[reply(2, {"second": True}), reply(1, {"first": True})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.call("A") == {"first": True}
    assert cdp.call("B") == {"second": True}
    assert ws.inbound == []


def test_call_accepts_string_ids_in_replies() -> None:
    ws = FakeWebSocket(inbound=[json.dumps({"id": "1", "result": {"ok": 1}})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.call("X") == {"ok": 1}


def test_call_ignores_empty_recv_slices_until_reply_arrives() -> None:
    ws = FakeWebSocket(inbound=[None, None, None, reply(1, {"late": True})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.call("Slow") == {"late": True}


def test_call_with_responder_round_trips_like_a_real_peer() -> None:
    def responder(message: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"id": message["id"], "result": {"echo": message["method"]}}]

    cdp = ep.CdpSession(FakeWebSocket(responder=responder))  # type: ignore[arg-type]
    assert cdp.call("Runtime.enable") == {"echo": "Runtime.enable"}
    assert cdp.call("Page.enable") == {"echo": "Page.enable"}


# ---------------------------------------------------------------------------
# Events and handlers


def test_events_dispatched_to_handlers_in_arrival_order_while_call_pending() -> None:
    seen: list[tuple[str, Any]] = []
    ws = FakeWebSocket(inbound=[
        event("Runtime.bindingCalled", {"name": "a", "payload": "1"}),
        event("Runtime.consoleAPICalled", {"type": "log"}),
        event("Runtime.bindingCalled", {"name": "a", "payload": "2"}),
        reply(1, {"done": True}),
    ])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.on("Runtime.bindingCalled", lambda p: seen.append(("binding", p["payload"])))
    cdp.on("Runtime.consoleAPICalled", lambda p: seen.append(("console", p["type"])))
    assert cdp.call("Runtime.evaluate") == {"done": True}
    assert seen == [("binding", "1"), ("console", "log"), ("binding", "2")]


def test_events_after_the_reply_stay_queued_for_the_next_pump() -> None:
    seen: list[dict[str, Any]] = []
    ws = FakeWebSocket(inbound=[reply(1), event("Runtime.bindingCalled", {"payload": "later"})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.on("Runtime.bindingCalled", seen.append)
    cdp.call("X")
    assert seen == []
    assert cdp.pump_once(0.0) is True
    assert seen == [{"payload": "later"}]


def test_events_without_a_handler_are_ignored() -> None:
    ws = FakeWebSocket(inbound=[event("Network.requestWillBeSent", {"x": 1}), reply(1)])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.call("X") == {}


def test_event_with_missing_params_dispatches_empty_dict() -> None:
    seen: list[dict[str, Any]] = []
    ws = FakeWebSocket(inbound=[json.dumps({"method": "Page.loadEventFired"})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.on("Page.loadEventFired", seen.append)
    assert cdp.pump_once(0.0) is True
    assert seen == [{}]


def test_on_replaces_an_existing_handler_for_the_same_event() -> None:
    first: list[dict[str, Any]] = []
    second: list[dict[str, Any]] = []
    ws = FakeWebSocket(inbound=[event("Page.loadEventFired", {"n": 1})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.on("Page.loadEventFired", first.append)
    cdp.on("Page.loadEventFired", second.append)
    cdp.pump_once(0.0)
    assert first == []
    assert second == [{"n": 1}]


def test_handler_exception_propagates_out_of_call() -> None:
    ws = FakeWebSocket(inbound=[event("Page.loadEventFired"), reply(1)])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]

    def boom(_params: dict[str, Any]) -> None:
        raise ValueError("handler failed")

    cdp.on("Page.loadEventFired", boom)
    with pytest.raises(ValueError, match="handler failed"):
        cdp.call("X")


# ---------------------------------------------------------------------------
# Error replies


def test_error_reply_raises_cdp_error_with_method_and_message() -> None:
    ws = FakeWebSocket(inbound=[error_reply(1, "Cannot find context with specified id", code=-32000)])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError) as info:
        cdp.call("Runtime.evaluate", {"expression": "1"})
    text = str(info.value)
    assert "Runtime.evaluate" in text
    assert "Cannot find context with specified id" in text
    assert "-32000" in text
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]


def test_error_reply_for_another_id_does_not_fail_the_current_call() -> None:
    ws = FakeWebSocket(inbound=[error_reply(2, "other failed"), reply(1, {"ok": True})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.call("A") == {"ok": True}
    with pytest.raises(RuntimeError, match="other failed"):
        cdp.call("B")


def test_error_reply_with_missing_fields_still_raises_cdp_error() -> None:
    ws = FakeWebSocket(inbound=[json.dumps({"id": 1, "error": {}})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="DevTools X failed") as info:
        cdp.call("X")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Detach


def test_inspector_detached_event_fails_subsequent_call_with_reason() -> None:
    ws = FakeWebSocket(inbound=[event("Inspector.detached", {"reason": "target_closed"})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.pump_once(0.0) is True
    assert cdp.detached == "target_closed"
    with pytest.raises(RuntimeError, match="target_closed") as info:
        cdp.call("Runtime.evaluate")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    assert info.value.hint  # type: ignore[attr-defined]
    assert ws.sent[-1]["method"] == "Runtime.evaluate"  # the command was still sent


def test_inspector_detached_arriving_during_a_call_aborts_it() -> None:
    ws = FakeWebSocket(inbound=[
        event("Inspector.detached", {"reason": "Render process gone."}),
        reply(1, {"never": "seen"}),
    ])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="Render process gone") as info:
        cdp.call("Runtime.evaluate")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]


def test_inspector_detached_without_reason_uses_default_wording() -> None:
    ws = FakeWebSocket(inbound=[event("Inspector.detached")])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.pump_once(0.0)
    assert cdp.detached == "detached"
    with pytest.raises(RuntimeError, match=r"DevTools detached from the page \(detached\)"):
        cdp.call("X")


def test_target_crashed_event_marks_session_detached() -> None:
    ws = FakeWebSocket(inbound=[event("Inspector.targetCrashed")])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.pump_once(0.0)
    assert cdp.detached == "target crashed"
    with pytest.raises(RuntimeError, match="target crashed"):
        cdp.call("X")


def test_reply_already_received_wins_over_detached_state() -> None:
    # A reply that landed before the detach is still delivered; only later calls fail.
    ws = FakeWebSocket(inbound=[reply(1, {"ok": True}), event("Inspector.detached", {"reason": "gone"})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.call("A") == {"ok": True}
    with pytest.raises(RuntimeError, match="gone"):
        cdp.call("B")


def test_user_handler_for_inspector_detached_replaces_builtin_tracking() -> None:
    # on() is last-writer-wins, so overriding the built-in detach handler disables it.
    seen: list[dict[str, Any]] = []
    ws = FakeWebSocket(inbound=[event("Inspector.detached", {"reason": "x"}), reply(1)])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.on("Inspector.detached", seen.append)
    assert cdp.call("A") == {}
    assert seen == [{"reason": "x"}]
    assert cdp.detached is None


# ---------------------------------------------------------------------------
# Deadlines (fake clock, never sleeps)


def test_call_with_past_deadline_and_empty_queue_raises_builtin_timeout(clock: FakeClock) -> None:
    ws = FakeWebSocket()
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    with pytest.raises(TimeoutError) as info:
        cdp.call("Runtime.evaluate", {"expression": "while(1){}"}, deadline=clock.now - 1.0)
    assert type(info.value) is TimeoutError
    assert str(info.value) == "Runtime.evaluate"
    assert not hasattr(info.value, "error_class")
    assert ws.sent == [{"id": 1, "method": "Runtime.evaluate", "params": {"expression": "while(1){}"}}]


def test_call_deadline_exactly_now_times_out(clock: FakeClock) -> None:
    ws = FakeWebSocket()
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    with pytest.raises(TimeoutError):
        cdp.call("X", deadline=clock.now)


def test_call_times_out_once_clock_passes_future_deadline(clock: FakeClock) -> None:
    clock.step = 0.5
    ws = FakeWebSocket(inbound=[None, None, None, None, None, None, None, None])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    with pytest.raises(TimeoutError, match="Slow"):
        cdp.call("Slow", deadline=clock.now + 2.0)
    assert ws.inbound  # the loop gave up before draining every slice


def test_call_returns_result_when_reply_arrives_before_deadline(clock: FakeClock) -> None:
    clock.step = 0.1
    ws = FakeWebSocket(inbound=[None, None, reply(1, {"in": "time"})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.call("X", deadline=clock.now + 60.0) == {"in": "time"}


def test_call_without_deadline_never_consults_the_clock(clock: FakeClock) -> None:
    ws = FakeWebSocket(inbound=[None, None, reply(1)])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.call("X")
    assert clock.reads == 0


def test_pending_result_beats_an_expired_deadline_if_already_received(clock: FakeClock) -> None:
    # The result check runs before the deadline check, so an already-dispatched reply is returned.
    ws = FakeWebSocket(inbound=[reply(2, {"early": True})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.pump_once(0.0)
    cdp._next_id = 2
    assert cdp.call("X", deadline=clock.now - 10.0) == {"early": True}


def test_detached_is_reported_before_deadline(clock: FakeClock) -> None:
    ws = FakeWebSocket()
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp._mark_detached("closed")
    with pytest.raises(RuntimeError, match="closed"):
        cdp.call("X", deadline=clock.now - 1.0)


# ---------------------------------------------------------------------------
# pump / pump_once


def test_pump_once_returns_false_on_empty_slice() -> None:
    ws = FakeWebSocket()
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.pump_once(0.0) is False


def test_pump_once_passes_timeout_to_websocket() -> None:
    timeouts: list[float | None] = []

    class RecordingWs(FakeWebSocket):
        def recv_text(self, timeout: float | None = 0.5) -> str | None:
            timeouts.append(timeout)
            return super().recv_text(timeout)

    cdp = ep.CdpSession(RecordingWs())  # type: ignore[arg-type]
    cdp.pump_once(0.123)
    cdp.pump_once()
    assert timeouts == [0.123, ep.RECV_SLICE]


def test_pump_once_ignores_non_json_text_and_reports_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ep, "VERBOSE", False)
    ws = FakeWebSocket(inbound=["this is not json", reply(1)])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    assert cdp.pump_once(0.0) is True
    assert cdp.call("X") == {}


def test_pump_once_ignores_non_json_text_when_verbose(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(ep, "VERBOSE", True)
    cdp = ep.CdpSession(FakeWebSocket(inbound=["{not json"]))  # type: ignore[arg-type]
    assert cdp.pump_once(0.0) is True
    assert "ignoring non-JSON" in capsys.readouterr().err


def test_pump_consumes_queued_events_without_a_pending_call(clock: FakeClock) -> None:
    clock.step = 0.1
    seen: list[str] = []
    ws = FakeWebSocket(inbound=[
        event("Runtime.bindingCalled", {"payload": "a"}),
        event("Runtime.bindingCalled", {"payload": "b"}),
        None,
        event("Runtime.bindingCalled", {"payload": "c"}),
    ])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.on("Runtime.bindingCalled", lambda p: seen.append(p["payload"]))
    cdp.pump(2.0)
    assert seen == ["a", "b", "c"]
    assert ws.inbound == []
    assert ws.sent == []


def test_pump_terminates_once_the_clock_passes_the_duration(clock: FakeClock) -> None:
    clock.step = 0.25
    cdp = ep.CdpSession(FakeWebSocket())  # type: ignore[arg-type]
    cdp.pump(1.0)
    assert clock.now >= 1000.0 + 1.0


def test_pump_zero_duration_does_not_read(clock: FakeClock) -> None:
    ws = FakeWebSocket(inbound=[event("Page.loadEventFired")])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.pump(0.0)
    assert ws.inbound  # nothing consumed: the loop condition fails immediately


def test_pump_caps_each_slice_at_recv_slice_and_never_passes_zero(clock: FakeClock) -> None:
    clock.step = 0.3
    timeouts: list[float | None] = []

    class RecordingWs(FakeWebSocket):
        def recv_text(self, timeout: float | None = 0.5) -> str | None:
            timeouts.append(timeout)
            return None

    cdp = ep.CdpSession(RecordingWs())  # type: ignore[arg-type]
    cdp.pump(5.0)
    assert timeouts
    assert all(0 < t <= ep.RECV_SLICE for t in timeouts if t is not None)


def test_pump_does_not_swallow_a_reply_it_sees_for_a_later_call(clock: FakeClock) -> None:
    clock.step = 0.1
    ws = FakeWebSocket(inbound=[reply(1, {"stashed": True})])
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.pump(0.5)
    assert ws.inbound == []  # pump read it ...
    assert 1 in cdp._results  # ... and parked it for the matching call
    assert cdp.call("X") == {"stashed": True}


# ---------------------------------------------------------------------------
# close


def test_close_closes_the_websocket() -> None:
    ws = FakeWebSocket()
    cdp = ep.CdpSession(ws)  # type: ignore[arg-type]
    cdp.close()
    assert ws.closed is True
