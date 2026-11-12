"""Offline safety tests for receipt-linked browser diagnostics and opt-in traces."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_browser_core import diagnostics, runtime


def test_diagnostic_text_masks_short_secrets_and_drops_url_parameters() -> None:
    raw = ('password=hunter2 otp=123456 Authorization: Bearer abc '
           'url=https://alice:secret@example.test/reset?code=short&state=also-short#fragment')
    scrubbed = diagnostics.redact_text(raw)

    for secret in ("hunter2", "123456", "abc", "alice", "secret", "short", "also-short", "fragment"):
        assert secret not in scrubbed
    assert "password=***" in scrubbed
    assert "otp=***" in scrubbed
    assert "Bearer ***" in scrubbed
    assert "https://example.test/reset" in scrubbed


def test_diagnostic_recorder_bounds_events_and_text() -> None:
    recorder = diagnostics.CommandDiagnostics(limit=2)
    recorder.error("console_error", "token=short " + "x" * (diagnostics.MAX_EVENT_TEXT + 10))
    recorder.error("page_error", "safe")
    recorder.error("page_error", "dropped")

    result = recorder.as_dict()
    assert len(result["events"]) == 2
    assert result["truncated"] is True and result["dropped"] == 1
    assert len(result["events"][0]["message"]) <= diagnostics.MAX_EVENT_TEXT + 3
    assert "short" not in json.dumps(result)


def test_sanitized_trace_keeps_only_viewer_timing_fields(tmp_path) -> None:
    raw_path = tmp_path / "raw.zip"
    events = [
        {"version": 8, "type": "context-options", "origin": "library", "browserName": "edge",
         "playwrightVersion": "1.61.0", "options": {"token": "context-secret"}, "contextId": "context-secret"},
        {"type": "before", "callId": "call@1", "startTime": 20.0, "class": "Locator", "method": "fill",
         "pageId": "page@1", "title": "fill secret-value", "params": {"value": "form-secret"}},
        {"type": "after", "callId": "call@1", "endTime": 28.0, "result": "response-secret",
         "error": {"message": "password=hunter2"}},
        {"type": "console", "message": "console-secret"},
    ]
    with zipfile.ZipFile(raw_path, "w") as archive:
        archive.writestr("trace.trace", "".join(json.dumps(event) + "\n" for event in events))
        archive.writestr("trace.network", "network-body-secret")
        archive.writestr("resources/secret.txt", "resource-secret")

    payload = diagnostics.sanitize_trace_archive(raw_path)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == ["trace.trace"]
        safe_events = [json.loads(line) for line in archive.read("trace.trace").splitlines()]

    assert safe_events[0]["type"] == "context-options"
    assert safe_events[0]["version"] == 8 and safe_events[0]["options"] == {}
    assert safe_events[0]["contextId"] == ""
    assert safe_events[1]["method"] == "fill" and "params" not in safe_events[1] and "title" not in safe_events[1]
    assert safe_events[2]["duration_ms"] == 8 and "error" not in safe_events[2] and "result" not in safe_events[2]
    assert not any(secret in payload.decode("latin1") for secret in (
        "context-secret", "secret-value", "form-secret", "response-secret", "hunter2", "console-secret", "network-body-secret", "resource-secret"))


def test_dispatch_persists_full_redacted_diagnostics_but_returns_summary(make_daemon, monkeypatch) -> None:
    daemon, _ctx, page = make_daemon()
    foreign = type(page)(page.ctx, url="http://other.test/")
    daemon._track_page(foreign)
    daemon.page_owners[foreign] = "another-session"
    request = SimpleNamespace(url="https://user:pw@example.test/api?password=hunter2", method="POST", failure=None)
    response = SimpleNamespace(request=request, status=403)

    def snapshot(_args, _timeout):
        page.emit("request", request)
        page.emit("response", response)
        page.emit("requestfinished", request)
        page.emit("console", SimpleNamespace(type="error", text="otp=123456 Authorization: Bearer abc"))
        foreign.emit("pageerror", RuntimeError("foreign-session-secret"))
        return {"snapshot": ["safe page"]}

    monkeypatch.setattr(daemon, "h_snapshot", snapshot)
    reply = daemon.dispatch("snapshot", {}, 1)
    row = daemon.journal.rows("manual", reply["command_id"])[0]
    outcome = json.loads(row["outcome"])
    events = outcome["diagnostics"]["events"]

    assert reply["diagnostics"] == {"event_count": 2, "truncated": False, "dropped": 0}
    assert "diagnostics" in reply["untrusted"]
    assert any(event.get("status") == 403 and event.get("duration_ms") is not None for event in events)
    assert "https://example.test/api" in json.dumps(events)
    for secret in ("user", "pw", "hunter2", "123456", "abc", "foreign-session-secret"):
        assert secret not in json.dumps(outcome)


def test_trace_opt_in_uses_no_snapshots_and_stores_sanitized_artifact(make_daemon, monkeypatch) -> None:
    daemon, ctx, page = make_daemon()
    calls: dict[str, object] = {}

    class FakeTracing:
        def start(self, **kwargs):
            calls["start"] = kwargs

        def stop(self, *, path):
            calls["stop"] = True
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("trace.trace", "\n".join([
                    json.dumps({"version": 8, "type": "context-options", "options": {"secret": "trace-secret"}}),
                    json.dumps({"type": "before", "callId": "call@1", "startTime": 2, "class": "Page", "method": "goto",
                                "pageId": "page@1", "params": {"url": "https://x.test/?token=trace-secret"}}),
                    json.dumps({"type": "after", "callId": "call@1", "endTime": 5}),
                ]) + "\n")
                archive.writestr("trace.network", "trace-secret")

    ctx.tracing = FakeTracing()
    monkeypatch.setattr(daemon, "h_snapshot", lambda _args, _timeout: {"snapshot": ["safe page"]})
    reply = daemon.dispatch("snapshot", {"trace": True}, 1)

    assert calls["start"]["snapshots"] is False and calls["start"]["screenshots"] is False
    assert calls["start"]["sources"] is False
    assert calls["stop"] is True and reply.get("trace_error") is None
    trace_path = Path(reply["trace_file"])
    assert trace_path.parent == daemon.paths.outputs
    with zipfile.ZipFile(trace_path) as archive:
        assert archive.namelist() == ["trace.trace"]
        safe = archive.read("trace.trace")
    assert b"trace-secret" not in safe and b"params" not in safe
    outcome = json.loads(daemon.journal.rows("manual", reply["command_id"])[0]["outcome"])
    assert outcome["trace_file"] == reply["trace_file"]


def test_trace_temp_failure_stops_tracing_and_keeps_action(make_daemon, monkeypatch) -> None:
    daemon, ctx, _page = make_daemon()
    stopped: list[str | None] = []

    class FakeTracing:
        def start(self, **_kwargs):
            pass

        def stop(self, *, path=None):
            stopped.append(path)

    ctx.tracing = FakeTracing()
    monkeypatch.setattr(daemon, "h_snapshot", lambda _args, _timeout: {"snapshot": ["action still succeeded"]})

    def fail_temporary_directory(*_args, **_kwargs):
        raise OSError("temporary directory unavailable")

    monkeypatch.setattr(runtime.tempfile, "TemporaryDirectory", fail_temporary_directory)
    reply = daemon.dispatch("snapshot", {"trace": True}, 1)

    assert reply["snapshot"] == ["action still succeeded"]
    assert reply["trace_error"] == "trace_unavailable"
    assert stopped == [None]
    assert list(daemon.paths.outputs.iterdir()) == []


def test_trace_output_guard_stops_tracing(make_daemon, monkeypatch, tmp_path) -> None:
    daemon, ctx, _page = make_daemon()
    outside = tmp_path / "outside-outputs"
    stopped: list[str | None] = []

    class FakeTracing:
        def start(self, **_kwargs):
            pass

        def stop(self, *, path=None):
            stopped.append(path)

    ctx.tracing = FakeTracing()
    monkeypatch.setattr(daemon, "h_snapshot", lambda _args, _timeout: {"snapshot": ["action still succeeded"]})
    monkeypatch.setattr(type(daemon.paths), "outputs", property(lambda _paths: outside))
    reply = daemon.dispatch("snapshot", {"trace": True}, 1)

    assert reply["snapshot"] == ["action still succeeded"]
    assert reply["trace_error"] == "trace_unavailable"
    assert stopped == [None]
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("args", [{"trace": "true"}, {"trace": True, "_session_id": "manual"}])
def test_trace_flag_requires_bool_and_unsupported_commands_are_rejected(make_daemon, args) -> None:
    daemon, _ctx, _page = make_daemon()
    if args.get("trace") is True:
        command = "state"
        payload = {"trace": True}
    else:
        command = "snapshot"
        payload = {"trace": "true"}
    with pytest.raises(ValueError, match="trace|synchronous"):
        daemon.dispatch(command, payload, 1)
