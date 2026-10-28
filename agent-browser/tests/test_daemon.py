"""The daemon-server request layer: `_Handler` and `_Server` driven for real over a loopback socket.

`serve()` itself is not called (it would try to `_playwright_factory()` and bind forever); instead
this file reproduces its inner loop - `while daemon.stop_reason is None: server.handle_request();
daemon.pump()` - in a background thread. Critically, the `Daemon` is *constructed inside that same
thread* (via conftest.make_daemon's builder, called from the thread target) and every subsequent
touch of it - dispatch(), h_ping patching, stop_reason - happens only on that thread; the test
(main) thread never reads or writes the Daemon instance, only a plain dict of values captured once
at startup and thread-safe primitives (Event/list) for the two tests that need to steer it. The
test thread acts purely as a socket client, via `agent_browser._request` or a raw socket for the
malformed-input cases.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

import pytest

import agent_browser as ab
from agent_browser_core import common as core_common, daemon as core_daemon, session as core_session


class _ServerHarness:
    """Owns a `Daemon` + `_Server` living entirely on one background thread.

    Cross-thread controls are threading.Events: `fail_next_ping` makes the next `ping` raise once;
    `slow_next_tabs` makes the next `tabs` set `in_slow` and hold the server thread until the test sets
    `release_tabs`, as another session's long command would. `publish=True` gives the daemon its port
    and writes session.json, so the busy record a real daemon publishes is on disk.
    """

    def __init__(self, build: Callable[..., tuple[Any, Any, Any]], publish: bool = False, **build_kwargs: Any) -> None:
        self._build = build
        self._build_kwargs = build_kwargs
        self._publish = publish
        self.fail_next_ping = threading.Event()
        self.large_next_ping = threading.Event()
        self.slow_next_tabs = threading.Event()
        self.in_slow = threading.Event()
        self.release_tabs = threading.Event()
        self.stop_event = threading.Event()
        self._ready = threading.Event()
        self.info: dict[str, Any] = {}
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        daemon, ctx, page = self._build(**self._build_kwargs)  # created on this thread
        real_h_ping = daemon.h_ping

        def guarded_h_ping(args: dict[str, Any], timeout: float) -> dict[str, Any]:
            if self.fail_next_ping.is_set():
                self.fail_next_ping.clear()
                raise Exception("boom")
            if self.large_next_ping.is_set():
                self.large_next_ping.clear()
                return {**real_h_ping(args, timeout), "large": "😀\\\"" * 14000}
            return real_h_ping(args, timeout)

        daemon.h_ping = guarded_h_ping
        real_h_tabs = daemon.h_tabs

        def slow_h_tabs(args: dict[str, Any], timeout: float) -> dict[str, Any]:
            if self.slow_next_tabs.is_set():
                self.slow_next_tabs.clear()
                self.in_slow.set()
                self.release_tabs.wait(10.0)
            return real_h_tabs(args, timeout)

        daemon.h_tabs = slow_h_tabs

        server = core_daemon._Server(("127.0.0.1", 0), core_daemon._Handler)
        server.daemon = daemon
        server.timeout = 0.2
        if self._publish:
            daemon.port = server.server_address[1]
            daemon.write_session(daemon.port)
        self.info = {"port": server.server_address[1], "token": daemon.token, "profile": daemon.profile}
        self._ready.set()
        try:
            while daemon.stop_reason is None and not self.stop_event.is_set():
                server.handle_request()
                daemon.pump()
            self.info["stop_reason"] = daemon.stop_reason
        finally:
            try:
                server.server_close()
            except OSError:
                pass

    def start(self) -> "_ServerHarness":
        self.thread.start()
        assert self._ready.wait(timeout=5.0), "server thread did not come up"
        return self

    @property
    def session(self) -> dict[str, Any]:
        return {"token": self.info["token"], "port": self.info["port"], "profile": self.info["profile"]}

    def stop(self) -> None:
        self.release_tabs.set()
        self.stop_event.set()
        self.thread.join(timeout=2.0)


@pytest.fixture
def daemon_server(make_daemon: Any) -> Iterator[_ServerHarness]:
    harness = _ServerHarness(make_daemon, snapshot_text='- generic [ref=e1]:\n  - button "Go" [ref=e2]\n').start()
    yield harness
    harness.stop()


def _raw_send(port: int, raw: bytes, read_timeout: float = 2.0) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=read_timeout) as sock:
        sock.settimeout(read_timeout)
        sock.sendall(raw)
        with sock.makefile("rb") as reader:
            return reader.readline()


# ---------------------------------------------------------------------------


def test_ping_returns_pong_with_tabs(daemon_server: _ServerHarness) -> None:
    result = core_session._request(daemon_server.session, "ping", {}, timeout=2.0)
    assert result["pong"] is True
    assert result["profile"] == daemon_server.info["profile"]
    assert isinstance(result["tabs"], list) and result["tabs"]


def test_wrong_token_gets_no_reply(daemon_server: _ServerHarness) -> None:
    bad_session = {**daemon_server.session, "token": "not-the-real-token"}
    with pytest.raises(RuntimeError) as exc:
        core_session._request(bad_session, "ping", {}, timeout=2.0)
    assert exc.value.error_class == core_common.ERR_DAEMON


@pytest.mark.parametrize("supplied", ["abd", "", chr(0xE9), chr(0xD800), None, 123, ["abc"], {"token": "abc"}, b"abc"])
def test_token_comparison_is_total(supplied: Any) -> None:
    assert core_daemon._token_ok("abc", "abc") is True
    assert core_daemon._token_ok(supplied, "abc") is False


@pytest.mark.parametrize("token", [chr(0xE9), chr(0xD800), 123, None, ["x"]])
def test_odd_token_is_dropped_without_reply_or_traceback(daemon_server: _ServerHarness, capsys: pytest.CaptureFixture[str], token: Any) -> None:
    raw = json.dumps({"token": token, "cmd": "ping", "args": {}, "timeout": 2.0, "protocol_version": core_common.PROTOCOL_VERSION}) + "\n"
    assert _raw_send(daemon_server.info["port"], raw.encode("utf-8")) == b""
    assert core_session._request(daemon_server.session, "ping", {}, timeout=2.0)["pong"] is True
    err = capsys.readouterr().err
    assert "rejected a request with a bad token" in err and "Traceback" not in err


def test_slow_drip_request_line_is_cut_off_by_wall_clock(daemon_server: _ServerHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_common, "FIRST_READ_TIMEOUT", 0.5)
    sock = socket.create_connection(("127.0.0.1", daemon_server.info["port"]), timeout=5.0)
    stop = threading.Event()

    def drip() -> None:  # one byte every 0.1 s for 3 s: each byte used to restart a per-recv timeout
        for _ in range(30):
            if stop.wait(0.1):
                return
            try:
                sock.sendall(b"{")
            except OSError:
                return

    dripper = threading.Thread(target=drip, daemon=True)
    began = time.monotonic()
    dripper.start()
    try:
        try:
            closed = sock.recv(1)
        except (ConnectionResetError, ConnectionAbortedError):
            closed = b""
        elapsed = time.monotonic() - began
    finally:
        stop.set()
        dripper.join(timeout=2.0)
        sock.close()
    assert closed == b"" and elapsed < 1.5, elapsed
    assert core_session._request(daemon_server.session, "ping", {}, timeout=2.0)["pong"] is True


def test_request_line_over_the_cap_is_dropped_and_the_server_keeps_serving(daemon_server: _ServerHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    cap = core_common.REQUEST_CAP
    monkeypatch.setattr(core_common, "REQUEST_CAP", 64)
    assert _raw_send(daemon_server.info["port"], b'{"pad": "' + b"x" * 200 + b'"}\n') == b""
    monkeypatch.setattr(core_common, "REQUEST_CAP", cap)
    assert core_session._request(daemon_server.session, "ping", {}, timeout=2.0)["pong"] is True


def test_wire_cannot_forge_the_commit_flag(daemon_server: _ServerHarness) -> None:
    with pytest.raises((ValueError, RuntimeError)) as exc:
        core_session._request(daemon_server.session, "journal", {"clear": True, "_commit": True}, timeout=2.0)
    assert exc.value.error_class == core_common.ERR_GUARDED and "commit journal --clear" in str(exc.value)


def test_malformed_json_is_a_usage_error(daemon_server: _ServerHarness) -> None:
    line = _raw_send(daemon_server.info["port"], b"not json at all\n")
    reply = json.loads(line)
    assert reply["ok"] is False
    assert reply["error"]["class"] == core_common.ERR_USAGE


@pytest.mark.parametrize("raw", [b"[]\n", b"null\n", b"42\n"])
def test_nonobject_envelope_is_rejected_without_crashing(daemon_server, raw):
    reply = json.loads(_raw_send(daemon_server.info["port"], raw))
    assert reply["error"]["class"] == core_common.ERR_USAGE
    assert core_session._request(daemon_server.session, "ping", {}, timeout=2)["pong"]


@pytest.mark.parametrize("timeout", [-1, "bad", float("nan"), float("inf"), core_common.MAX_TIMEOUT + 1, True])
def test_invalid_wire_timeout_is_rejected_without_dispatch(daemon_server, timeout):
    message = {**daemon_server.session, "protocol_version": core_common.PROTOCOL_VERSION, "cmd": "ping", "timeout": timeout}
    reply = json.loads(_raw_send(daemon_server.info["port"], (json.dumps(message) + "\n").encode()))
    assert reply["error"]["class"] == core_common.ERR_VALIDATION


def test_protocol_mismatch_requires_explicit_restart(daemon_server):
    message = {**daemon_server.session, "cmd": "ping", "protocol_version": -1}
    reply = json.loads(_raw_send(daemon_server.info["port"], (json.dumps(message) + "\n").encode()))
    assert reply["error"]["class"] == "protocol_mismatch"
    assert "restart" in reply["error"]["message"]


def test_oversized_error_preserves_write_receipt_when_output_storage_is_full(monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace
    from agent_browser_core import cli
    def full(*args, **kwargs):
        raise core_common._tag(RuntimeError("quota"), "journal_full")
    monkeypatch.setattr(cli, "_json_line", full)
    handler = SimpleNamespace(server=SimpleNamespace(daemon=SimpleNamespace(profile="default", session_id="manual")), wfile=BytesIO())
    command_id = "a" * 32
    core_daemon._Handler._reply(handler, {"ok": False, "error": {"class": "timeout", "message": "😀" * 30000,
                                         "details": {"command_id": command_id, "executed": True}}})
    reply = json.loads(handler.wfile.getvalue())
    assert reply["error"]["details"] == {"command_id": command_id, "executed": True, "receipt": "state --command " + command_id}
    assert len(handler.wfile.getvalue()) <= core_common.STDOUT_BUDGET
    assert reply["error"]["class"] == "journal_full" and reply["error"]["hint"] == core_common._hint("inspect_receipt", "default")


@pytest.mark.parametrize("failure, cls", [(OSError("disk"), "output_storage"),
                                          (core_common._tag(ValueError("bad profile"), core_common.ERR_VALIDATION), "output_storage")])
def test_unstorable_reply_of_any_shape_gets_the_shared_storage_envelope(monkeypatch, failure, cls):
    from io import BytesIO
    from types import SimpleNamespace
    from agent_browser_core import cli
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(cli, "_json_line", fail)
    handler = SimpleNamespace(server=SimpleNamespace(daemon=SimpleNamespace(profile="work", session_id="manual")), wfile=BytesIO())
    core_daemon._Handler._reply(handler, {"ok": True, "result": ["x" * 30000]})
    reply = json.loads(handler.wfile.getvalue())
    assert reply["ok"] is False and reply["protocol_version"] == core_common.PROTOCOL_VERSION
    assert reply["error"] == core_common._storage_error(None, failure, "work")["error"]
    assert reply["error"]["class"] == cls and reply["error"]["details"]["receipt"] == "journal"


def test_complete_wire_reply_is_bounded_with_recoverable_output(daemon_server):
    daemon_server.large_next_ping.set()
    message = {**daemon_server.session, "cmd": "ping", "protocol_version": core_common.PROTOCOL_VERSION}
    raw = _raw_send(daemon_server.info["port"], (json.dumps(message) + "\n").encode())
    assert len(raw) <= core_common.STDOUT_BUDGET
    reply = json.loads(raw)
    assert reply["result"]["truncated"] and reply["result"]["output_id"]
    from pathlib import Path
    saved = json.loads(Path(reply["result"]["output_file"]).read_text(encoding="utf-8"))
    assert len(saved["large"]) == 3 * 14000


def test_unknown_command_is_a_usage_error(daemon_server: _ServerHarness) -> None:
    with pytest.raises(ValueError) as exc:
        core_session._request(daemon_server.session, "no-such-command", {}, timeout=2.0)
    assert exc.value.error_class == core_common.ERR_USAGE


def test_handler_exception_is_reported_as_daemon_class_and_the_loop_keeps_serving(daemon_server: _ServerHarness) -> None:
    daemon_server.fail_next_ping.set()  # a thread-safe flag; the server thread itself raises once

    with pytest.raises(RuntimeError) as exc:
        core_session._request(daemon_server.session, "ping", {}, timeout=2.0)
    assert exc.value.error_class == core_common.ERR_DAEMON

    # the flag auto-cleared after firing once; the server loop is still alive and answers normally
    result = core_session._request(daemon_server.session, "ping", {}, timeout=2.0)
    assert result["pong"] is True


def test_stop_sets_stop_reason_and_the_loop_exits(daemon_server: _ServerHarness) -> None:
    result = core_session._request(daemon_server.session, "stop", {}, timeout=2.0)
    assert result["stopping"] is True
    daemon_server.thread.join(timeout=2.0)
    assert not daemon_server.thread.is_alive()
    assert daemon_server.info["stop_reason"] == "stop"


# ---------------------------------------------------------------------------
# Several callers on one profile: the daemon serves them one at a time.


def _hold_daemon(harness: _ServerHarness) -> tuple[threading.Thread, list[Any]]:
    """Another session's long command: a `tabs` that holds the server thread until `release_tabs` is set."""
    outcome: list[Any] = []

    def run() -> None:
        try:
            outcome.append(core_session._request(harness.session, "tabs", {}, timeout=5.0))
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertions on `outcome`
            outcome.append(exc)

    harness.slow_next_tabs.set()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert harness.in_slow.wait(3.0), "the holding command never reached the server thread"
    return thread, outcome


def _queue_raw(port: int, message: dict[str, Any]) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5.0)
    sock.sendall((json.dumps(message) + "\n").encode())
    return sock


def test_request_still_queued_at_its_start_by_is_refused_unrun(daemon_server: _ServerHarness) -> None:
    holder, outcome = _hold_daemon(daemon_server)
    start_by = time.time() + 0.2
    message = {**daemon_server.session, "protocol_version": core_common.PROTOCOL_VERSION, "cmd": "stop", "start_by": start_by}
    with _queue_raw(daemon_server.info["port"], message) as sock:
        while time.time() <= start_by + 0.1:
            time.sleep(0.05)
        daemon_server.release_tabs.set()
        with sock.makefile("rb") as reader:
            reply = json.loads(reader.readline())
    holder.join(timeout=5.0)

    assert reply["error"]["class"] == core_common.ERR_BUSY
    assert reply["error"]["details"] == {"executed": False}
    assert isinstance(outcome[0], dict), outcome
    # A stop that ran would have ended the serve loop; the daemon still answers.
    assert core_session._request(daemon_server.session, "ping", {}, timeout=2.0)["pong"] is True


def test_request_whose_caller_disconnected_while_queued_is_skipped(daemon_server: _ServerHarness) -> None:
    holder, _outcome = _hold_daemon(daemon_server)
    message = {**daemon_server.session, "protocol_version": core_common.PROTOCOL_VERSION, "cmd": "stop", "start_by": time.time() + 60}
    _queue_raw(daemon_server.info["port"], message).close()  # a caller killed or cancelled while queued
    daemon_server.release_tabs.set()
    holder.join(timeout=5.0)
    assert core_session._request(daemon_server.session, "ping", {}, timeout=2.0)["pong"] is True
    assert daemon_server.thread.is_alive()


def test_queued_request_keeps_its_full_timeout_after_waiting_its_turn(daemon_server: _ServerHarness) -> None:
    holder, _outcome = _hold_daemon(daemon_server)
    answered: list[Any] = []

    def ping() -> None:
        try:
            answered.append(core_session._request(daemon_server.session, "ping", {}, timeout=0.2, grace=0.0, queue_wait=5.0))
        except Exception as exc:  # noqa: BLE001 - surfaced by the assertion
            answered.append(exc)

    caller = threading.Thread(target=ping, daemon=True)
    caller.start()
    time.sleep(0.6)  # longer than the 0.2 s that used to be this request's whole patience
    daemon_server.release_tabs.set()
    caller.join(timeout=6.0)
    holder.join(timeout=5.0)
    assert isinstance(answered[0], dict) and answered[0]["pong"] is True, answered


def test_late_poll_still_runs_because_it_only_observes(daemon_server: _ServerHarness) -> None:
    message = {**daemon_server.session, "protocol_version": core_common.PROTOCOL_VERSION, "cmd": "poll",
               "args": {"command_id": "0" * 32}, "start_by": time.time() - 60}
    reply = json.loads(_raw_send(daemon_server.info["port"], (json.dumps(message) + "\n").encode()))
    assert reply["error"]["class"] == core_common.ERR_NOT_FOUND  # dispatched, not refused as busy


@pytest.mark.parametrize("start_by", ["soon", float("nan"), float("inf"), True, [1]])
def test_invalid_start_by_is_rejected_without_dispatch(daemon_server: _ServerHarness, start_by: Any) -> None:
    message = {**daemon_server.session, "protocol_version": core_common.PROTOCOL_VERSION, "cmd": "stop", "start_by": start_by}
    reply = json.loads(_raw_send(daemon_server.info["port"], (json.dumps(message) + "\n").encode()))
    assert reply["error"]["class"] == core_common.ERR_VALIDATION
    assert core_session._request(daemon_server.session, "ping", {}, timeout=2.0)["pong"] is True


def test_status_reports_a_daemon_held_by_another_command_as_busy_not_unresponsive(
    make_daemon: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _ServerHarness(make_daemon, publish=True).start()
    try:
        paths = core_common._paths(harness.info["profile"])
        paths.lock.touch()
        monkeypatch.setattr(core_common, "_lock_held", lambda p: True)
        real_ping = core_session._ping
        monkeypatch.setattr(core_session, "_ping", lambda session, timeout=None: real_ping(session, timeout=0.25))
        holder, outcome = _hold_daemon(harness)

        state, record = core_session._liveness(paths)
        status = core_session.status_session(paths)
        harness.release_tabs.set()
        holder.join(timeout=5.0)

        assert state == "busy" and record["busy"]["cmd"] == "tabs"
        assert status["running"] is True and status["busy"]["cmd"] == "tabs"
        assert status["note"] == core_common._hint("busy_status", paths.profile)
        assert "--force" not in json.dumps(status)
        assert "tabs" not in status  # unknown while the command runs, not an empty list
        assert isinstance(outcome[0], dict), outcome
        assert core_session._read_session(paths)["busy"] is None
        monkeypatch.setattr(core_session, "_ping", real_ping)
        assert core_session._liveness(paths)[0] == "running"
    finally:
        harness.stop()


def test_busy_record_spans_the_callers_deadline_and_clears_after(make_daemon: Any) -> None:
    daemon, _ctx, _page = make_daemon()
    daemon.port = 4321
    daemon._mark_busy("poll", 15.0)
    assert core_session._read_session(daemon.paths) is None  # pings and polls are not published

    daemon._mark_busy("click", 15.0)
    busy = core_session._read_session(daemon.paths)["busy"]
    until = datetime.strptime(busy["until"], core_common.UTC_FORMAT).replace(tzinfo=timezone.utc)
    remaining = (until - datetime.now(timezone.utc)).total_seconds()
    assert busy["cmd"] == "click"
    assert 15.0 + core_common.CLIENT_GRACE - 2 <= remaining <= 15.0 + core_common.CLIENT_GRACE

    daemon._mark_idle()
    assert core_session._read_session(daemon.paths)["busy"] is None


def test_unpublishable_busy_state_never_fails_the_command(make_daemon: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    daemon, _ctx, _page = make_daemon()
    daemon.port = 4321

    def full_disk(port: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(daemon, "write_session", full_disk)
    daemon._mark_busy("click", 15.0)
    daemon._mark_idle()
    assert daemon.request_busy is None


def test_idle_connection_does_not_block_a_following_legitimate_request(
    daemon_server: _ServerHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core_common, "FIRST_READ_TIMEOUT", 0.3)  # a module constant, not part of the Daemon instance
    idle_sock = socket.create_connection(("127.0.0.1", daemon_server.info["port"]), timeout=2.0)
    try:
        result = core_session._request(daemon_server.session, "ping", {}, timeout=2.0)
        assert result["pong"] is True
    finally:
        idle_sock.close()
