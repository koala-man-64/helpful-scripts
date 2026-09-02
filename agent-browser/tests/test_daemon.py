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
from typing import Any, Callable, Iterator

import pytest

import agent_browser as ab


class _ServerHarness:
    """Owns a `Daemon` + `_Server` living entirely on one background thread.

    `fail_next_ping` (a threading.Event) is the only cross-thread control: when set, the next
    `ping` dispatched on the server thread raises once, then clears itself and behaves normally.
    """

    def __init__(self, build: Callable[..., tuple[Any, Any, Any]], **build_kwargs: Any) -> None:
        self._build = build
        self._build_kwargs = build_kwargs
        self.fail_next_ping = threading.Event()
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
            return real_h_ping(args, timeout)

        daemon.h_ping = guarded_h_ping

        server = ab._Server(("127.0.0.1", 0), ab._Handler)
        server.daemon = daemon
        server.timeout = 0.2
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
    result = ab._request(daemon_server.session, "ping", {}, timeout=2.0)
    assert result["pong"] is True
    assert result["profile"] == daemon_server.info["profile"]
    assert isinstance(result["tabs"], list) and result["tabs"]


def test_wrong_token_gets_no_reply(daemon_server: _ServerHarness) -> None:
    bad_session = {**daemon_server.session, "token": "not-the-real-token"}
    with pytest.raises(RuntimeError) as exc:
        ab._request(bad_session, "ping", {}, timeout=2.0)
    assert exc.value.error_class == ab.ERR_DAEMON


def test_malformed_json_is_a_usage_error(daemon_server: _ServerHarness) -> None:
    line = _raw_send(daemon_server.info["port"], b"not json at all\n")
    reply = json.loads(line)
    assert reply["ok"] is False
    assert reply["error"]["class"] == ab.ERR_USAGE


def test_unknown_command_is_a_usage_error(daemon_server: _ServerHarness) -> None:
    with pytest.raises(ValueError) as exc:
        ab._request(daemon_server.session, "no-such-command", {}, timeout=2.0)
    assert exc.value.error_class == ab.ERR_USAGE


def test_handler_exception_is_reported_as_daemon_class_and_the_loop_keeps_serving(daemon_server: _ServerHarness) -> None:
    daemon_server.fail_next_ping.set()  # a thread-safe flag; the server thread itself raises once

    with pytest.raises(RuntimeError) as exc:
        ab._request(daemon_server.session, "ping", {}, timeout=2.0)
    assert exc.value.error_class == ab.ERR_DAEMON

    # the flag auto-cleared after firing once; the server loop is still alive and answers normally
    result = ab._request(daemon_server.session, "ping", {}, timeout=2.0)
    assert result["pong"] is True


def test_stop_sets_stop_reason_and_the_loop_exits(daemon_server: _ServerHarness) -> None:
    result = ab._request(daemon_server.session, "stop", {}, timeout=2.0)
    assert result["stopping"] is True
    daemon_server.thread.join(timeout=2.0)
    assert not daemon_server.thread.is_alive()
    assert daemon_server.info["stop_reason"] == "stop"


def test_idle_connection_does_not_block_a_following_legitimate_request(
    daemon_server: _ServerHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ab, "FIRST_READ_TIMEOUT", 0.3)  # a module constant, not part of the Daemon instance
    idle_sock = socket.create_connection(("127.0.0.1", daemon_server.info["port"]), timeout=2.0)
    try:
        result = ab._request(daemon_server.session, "ping", {}, timeout=2.0)
        assert result["pong"] is True
    finally:
        idle_sock.close()
