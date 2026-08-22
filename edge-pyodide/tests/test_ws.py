"""RFC 6455 client: pure frame helpers plus WebSocket over a scripted fake socket.

No real sockets are opened. `socket.create_connection` is monkeypatched for the handshake
tests and every other test hands a fake socket object straight to the WebSocket class.
"""

from __future__ import annotations

import socket
import struct
from typing import Any, Callable

import pytest

import edge_pyodide as ep

RFC_KEY = "dGhlIHNhbXBsZSBub25jZQ=="
RFC_ACCEPT = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
MASK = b"\x12\x34\x56\x78"


# ---------------------------------------------------------------------------
# Helpers


class FakeSocket:
    """Serves scripted chunks from recv(); an exception instance in the list is raised."""

    def __init__(self, chunks: list[Any] | None = None) -> None:
        self.chunks: list[Any] = list(chunks or [])
        self.sent = bytearray()
        self.timeouts: list[float | None] = []
        self.recv_calls = 0
        self.closed = False

    def settimeout(self, timeout: float | None) -> None:
        self.timeouts.append(timeout)

    def recv(self, size: int) -> bytes:
        self.recv_calls += 1
        if not self.chunks:
            raise socket.timeout("scripted data exhausted")
        item = self.chunks.pop(0)
        if isinstance(item, BaseException):
            raise item
        assert len(item) <= size
        return item

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def close(self) -> None:
        self.closed = True


def decode_sent_frames(raw: bytes) -> list[tuple[int, bool, bytes]]:
    """Split client-sent bytes into (opcode, masked, unmasked payload) tuples."""
    frames: list[tuple[int, bool, bytes]] = []
    buf = bytes(raw)
    while buf:
        header = ep._parse_frame_header(buf[:14])
        assert header is not None, "truncated frame in sent bytes"
        _fin, opcode, masked, length, header_len = header
        payload = buf[header_len:header_len + length]
        assert len(payload) == length
        if masked:
            payload = ep._mask_payload(payload, buf[header_len - 4:header_len])
        frames.append((opcode, masked, payload))
        buf = buf[header_len + length:]
    return frames


def server_text(text: str, fin: bool = True, opcode: int = ep.OP_TEXT) -> bytes:
    return ep._encode_frame(opcode, text.encode("utf-8"), fin=fin)


# ---------------------------------------------------------------------------
# _ws_accept


def test_ws_accept_matches_rfc_6455_vector() -> None:
    assert ep._ws_accept(RFC_KEY) == RFC_ACCEPT


def test_ws_accept_differs_for_different_keys() -> None:
    assert ep._ws_accept(RFC_KEY) != ep._ws_accept("AQIDBAUGBwgJCgsMDQ4PEA==")


# ---------------------------------------------------------------------------
# _mask_payload


def test_mask_payload_is_an_involution() -> None:
    payload = bytes(range(256)) * 3 + b"tail"
    masked = ep._mask_payload(payload, MASK)
    assert masked != payload
    assert ep._mask_payload(masked, MASK) == payload


def test_mask_payload_xors_each_byte_with_the_repeating_mask() -> None:
    payload = b"\x00\x00\x00\x00\x00\x00"
    assert ep._mask_payload(payload, MASK) == MASK + MASK[:2]


def test_mask_payload_empty_payload_is_empty() -> None:
    assert ep._mask_payload(b"", MASK) == b""


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 7, 8, 9, 100])
def test_mask_payload_preserves_length_for_every_alignment(size: int) -> None:
    payload = b"\xff" * size
    assert len(ep._mask_payload(payload, MASK)) == size


def test_mask_payload_keeps_leading_zero_bytes() -> None:
    # The big-int trick must not strip leading zeros that XOR to zero.
    payload = MASK + b"x"
    masked = ep._mask_payload(payload, MASK)
    assert masked[:4] == b"\x00\x00\x00\x00"
    assert len(masked) == 5


# ---------------------------------------------------------------------------
# _encode_frame / _parse_frame_header


@pytest.mark.parametrize(
    ("size", "expected_header_len"),
    [(0, 2), (125, 2), (126, 4), (65535, 4), (65536, 10)],
)
def test_encode_and_parse_round_trip_unmasked(size: int, expected_header_len: int) -> None:
    payload = bytes(i & 0xFF for i in range(size))
    frame = ep._encode_frame(ep.OP_TEXT, payload)
    header = ep._parse_frame_header(frame[:14])
    assert header == (True, ep.OP_TEXT, False, size, expected_header_len)
    assert frame[expected_header_len:] == payload
    assert len(frame) == expected_header_len + size


@pytest.mark.parametrize(
    ("size", "expected_header_len"),
    [(0, 6), (125, 6), (126, 8), (65535, 8), (65536, 14)],
)
def test_encode_and_parse_round_trip_masked(size: int, expected_header_len: int) -> None:
    payload = bytes((i * 7) & 0xFF for i in range(size))
    frame = ep._encode_frame(ep.OP_BINARY, payload, mask=MASK)
    header = ep._parse_frame_header(frame[:14])
    assert header == (True, ep.OP_BINARY, True, size, expected_header_len)
    assert frame[expected_header_len - 4:expected_header_len] == MASK
    wire_payload = frame[expected_header_len:]
    if size:
        assert wire_payload != payload
    assert ep._mask_payload(wire_payload, MASK) == payload


def test_encode_frame_uses_7_bit_length_below_126() -> None:
    frame = ep._encode_frame(ep.OP_TEXT, b"a" * 125)
    assert frame[1] == 125


def test_encode_frame_uses_16_bit_length_from_126_to_65535() -> None:
    frame = ep._encode_frame(ep.OP_TEXT, b"a" * 126)
    assert frame[1] == 126
    assert struct.unpack(">H", frame[2:4])[0] == 126
    frame = ep._encode_frame(ep.OP_TEXT, b"a" * 65535)
    assert frame[1] == 126
    assert struct.unpack(">H", frame[2:4])[0] == 65535


def test_encode_frame_uses_64_bit_length_from_65536() -> None:
    frame = ep._encode_frame(ep.OP_TEXT, b"a" * 65536)
    assert frame[1] == 127
    assert struct.unpack(">Q", frame[2:10])[0] == 65536


def test_encode_frame_fin_bit_and_opcode() -> None:
    assert ep._encode_frame(ep.OP_TEXT, b"")[0] == 0x81
    assert ep._encode_frame(ep.OP_TEXT, b"", fin=False)[0] == 0x01
    assert ep._encode_frame(ep.OP_CONT, b"", fin=True)[0] == 0x80
    assert ep._encode_frame(ep.OP_CLOSE, b"")[0] == 0x88
    assert ep._encode_frame(ep.OP_PING, b"")[0] == 0x89
    assert ep._encode_frame(ep.OP_PONG, b"")[0] == 0x8A


def test_encode_frame_mask_bit_set_only_when_mask_given() -> None:
    assert ep._encode_frame(ep.OP_TEXT, b"x")[1] & 0x80 == 0
    assert ep._encode_frame(ep.OP_TEXT, b"x", mask=MASK)[1] & 0x80 == 0x80


def test_parse_frame_header_reports_fin_false_for_fragment() -> None:
    frame = ep._encode_frame(ep.OP_TEXT, b"abc", fin=False)
    assert ep._parse_frame_header(frame) == (False, ep.OP_TEXT, False, 3, 2)


@pytest.mark.parametrize(
    ("buf", "why"),
    [
        (b"", "empty"),
        (b"\x81", "one byte"),
        (b"\x81\x7e\x00", "126-length needs 4 bytes"),
        (b"\x81\x7f\x00\x00\x00\x00\x00\x01\x00", "127-length needs 10 bytes"),
    ],
)
def test_parse_frame_header_returns_none_when_incomplete(buf: bytes, why: str) -> None:
    assert ep._parse_frame_header(buf) is None, why


def test_parse_frame_header_does_not_require_mask_key_bytes_to_be_present() -> None:
    # The mask key is accounted for in header_len but its bytes are read by the caller.
    assert ep._parse_frame_header(b"\x81\x85") == (True, ep.OP_TEXT, True, 5, 6)


def test_parse_frame_header_ignores_trailing_payload_bytes() -> None:
    frame = ep._encode_frame(ep.OP_TEXT, b"hello world")
    assert ep._parse_frame_header(frame + b"garbage") == (True, ep.OP_TEXT, False, 11, 2)


# ---------------------------------------------------------------------------
# WebSocket.recv_text over a fake socket


def test_recv_text_decodes_unmasked_server_text_frame() -> None:
    sock = FakeSocket([server_text("hello")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) == "hello"
    assert sock.timeouts == [0.1]


def test_recv_text_decodes_utf8_payload() -> None:
    sock = FakeSocket([server_text("héllo ✓")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) == "héllo ✓"


def test_recv_text_replaces_invalid_utf8_instead_of_raising() -> None:
    sock = FakeSocket([ep._encode_frame(ep.OP_TEXT, b"ok\xff")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) == "ok�"


def test_recv_text_accepts_masked_frame_from_a_misbehaving_server() -> None:
    sock = FakeSocket([ep._encode_frame(ep.OP_TEXT, b"masked", mask=MASK)])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) == "masked"


def test_recv_text_decodes_binary_frame_as_text() -> None:
    sock = FakeSocket([ep._encode_frame(ep.OP_BINARY, b"{}")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) == "{}"


def test_recv_text_reassembles_fragmented_message() -> None:
    sock = FakeSocket([
        server_text("hel", fin=False),
        server_text("lo ", fin=False, opcode=ep.OP_CONT),
        server_text("world", fin=True, opcode=ep.OP_CONT),
    ])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) == "hello world"
    assert sock.recv_calls == 3


def test_recv_text_fragment_state_is_reset_after_reassembly() -> None:
    sock = FakeSocket([
        server_text("a", fin=False),
        server_text("b", fin=True, opcode=ep.OP_CONT),
        server_text("c", fin=False),
        server_text("d", fin=True, opcode=ep.OP_CONT),
    ])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) == "ab"
    assert ws.recv_text(0.1) == "cd"


def test_recv_text_returns_multiple_frames_from_one_chunk_without_another_recv() -> None:
    sock = FakeSocket([server_text("one") + server_text("two")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) == "one"
    assert ws.recv_text(0.1) == "two"
    assert sock.recv_calls == 1


def test_recv_text_handles_frame_split_across_recv_chunks() -> None:
    frame = server_text("split across chunks")
    sock = FakeSocket([frame[:1], frame[1:4], frame[4:]])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) == "split across chunks"
    assert sock.recv_calls == 3


def test_recv_text_handles_large_frame_with_64_bit_length() -> None:
    payload = b"x" * 70000
    frame = ep._encode_frame(ep.OP_TEXT, payload)
    sock = FakeSocket([frame[:65536], frame[65536:]])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) == payload.decode()


def test_recv_text_answers_ping_with_masked_pong_and_keeps_waiting() -> None:
    sock = FakeSocket([ep._encode_frame(ep.OP_PING, b"heartbeat"), server_text("after ping")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) == "after ping"
    frames = decode_sent_frames(sock.sent)
    assert frames == [(ep.OP_PONG, True, b"heartbeat")]


def test_recv_text_ignores_unsolicited_pong() -> None:
    sock = FakeSocket([ep._encode_frame(ep.OP_PONG, b"late"), server_text("data")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) == "data"
    assert sock.sent == b""


def test_recv_text_ignores_reserved_opcode_frame() -> None:
    sock = FakeSocket([ep._encode_frame(0x3, b"reserved"), server_text("data")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) == "data"


def test_recv_text_close_frame_raises_cdp_error_and_echoes_close() -> None:
    sock = FakeSocket([ep._encode_frame(ep.OP_CLOSE, struct.pack(">H", 1001) + b"going away")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="close code 1001") as info:
        ws.recv_text(0.1)
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    assert info.value.hint  # type: ignore[attr-defined]
    assert decode_sent_frames(sock.sent) == [(ep.OP_CLOSE, True, struct.pack(">H", 1001))]


def test_recv_text_close_frame_without_code_reports_1005() -> None:
    sock = FakeSocket([ep._encode_frame(ep.OP_CLOSE, b"")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="close code 1005"):
        ws.recv_text(0.1)
    assert decode_sent_frames(sock.sent) == [(ep.OP_CLOSE, True, b"")]


def test_recv_text_after_close_frame_raises_closed_error_without_touching_socket() -> None:
    sock = FakeSocket([ep._encode_frame(ep.OP_CLOSE, struct.pack(">H", 1000))])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        ws.recv_text(0.1)
    calls_before = sock.recv_calls
    with pytest.raises(RuntimeError, match="closed") as info:
        ws.recv_text(0.1)
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    assert sock.recv_calls == calls_before


def test_recv_text_empty_recv_means_peer_dropped_connection() -> None:
    sock = FakeSocket([b""])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="dropped") as info:
        ws.recv_text(0.1)
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="closed"):
        ws.recv_text(0.1)


def test_recv_text_socket_error_is_tagged_cdp_and_closes() -> None:
    sock = FakeSocket([ConnectionResetError("reset by peer")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="receive failed") as info:
        ws.recv_text(0.1)
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    assert isinstance(info.value.__cause__, ConnectionResetError)
    with pytest.raises(RuntimeError, match="closed"):
        ws.recv_text(0.1)


def test_recv_text_slice_timeout_returns_none_and_keeps_partial_bytes() -> None:
    frame = server_text("buffered")
    sock = FakeSocket([frame[:3], socket.timeout("slice"), frame[3:]])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.25) is None
    assert bytes(ws._buf) == frame[:3]
    assert ws.recv_text(0.25) == "buffered"
    assert sock.timeouts == [0.25, 0.25, 0.25]


def test_recv_text_timeout_with_nothing_buffered_returns_none() -> None:
    sock = FakeSocket([socket.timeout("slice")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(0.1) is None
    assert ws.recv_text(0.1) is None  # FakeSocket raises timeout when exhausted too


def test_recv_text_none_timeout_is_passed_through_to_socket() -> None:
    sock = FakeSocket([server_text("x")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text(None) == "x"
    assert sock.timeouts == [None]


def test_recv_text_default_timeout_is_recv_slice() -> None:
    sock = FakeSocket([server_text("x")])
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    assert ws.recv_text() == "x"
    assert sock.timeouts == [ep.RECV_SLICE]


# ---------------------------------------------------------------------------
# WebSocket.send_text / close


def test_send_text_sends_single_masked_text_frame() -> None:
    sock = FakeSocket()
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    ws.send_text('{"id": 1}')
    assert decode_sent_frames(sock.sent) == [(ep.OP_TEXT, True, b'{"id": 1}')]
    assert sock.sent[0] == 0x81  # FIN + text


def test_send_text_uses_a_fresh_mask_per_frame() -> None:
    sock = FakeSocket()
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    ws.send_text("a")
    ws.send_text("a")
    first, second = sock.sent[:7], sock.sent[7:]
    assert first[:2] == second[:2]
    # 32 random bits: a collision here is astronomically unlikely.
    assert first[2:6] != second[2:6]


def test_send_text_socket_error_is_tagged_and_marks_closed() -> None:
    class BrokenSocket(FakeSocket):
        def sendall(self, data: bytes) -> None:
            raise BrokenPipeError("pipe")

    sock = BrokenSocket()
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="send failed") as info:
        ws.send_text("x")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="closed"):
        ws.send_text("x")


def test_close_sends_normal_close_frame_and_closes_socket() -> None:
    sock = FakeSocket()
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    ws.close()
    assert decode_sent_frames(sock.sent) == [(ep.OP_CLOSE, True, struct.pack(">H", 1000))]
    assert sock.closed is True


def test_close_is_idempotent() -> None:
    sock = FakeSocket()
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    ws.close()
    ws.close()
    assert len(decode_sent_frames(sock.sent)) == 1


def test_close_swallows_send_and_socket_close_errors() -> None:
    class DeadSocket(FakeSocket):
        def sendall(self, data: bytes) -> None:
            raise OSError("dead")

        def close(self) -> None:
            raise OSError("already closed")

    ws = ep.WebSocket(DeadSocket())  # type: ignore[arg-type]
    ws.close()
    with pytest.raises(RuntimeError, match="closed"):
        ws.send_text("x")


def test_send_after_close_raises_cdp_error() -> None:
    sock = FakeSocket()
    ws = ep.WebSocket(sock)  # type: ignore[arg-type]
    ws.close()
    with pytest.raises(RuntimeError, match="closed") as info:
        ws.send_text("x")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# WebSocket.connect handshake


class HandshakeSocket(FakeSocket):
    """Captures the upgrade request and answers with a reply computed from it."""

    def __init__(self, respond: Callable[[bytes], bytes], chunk_size: int = 4096) -> None:
        super().__init__()
        self.respond = respond
        self.chunk_size = chunk_size
        self.reply: bytes | None = None

    def sendall(self, data: bytes) -> None:
        super().sendall(data)
        if self.reply is None:
            self.reply = self.respond(bytes(self.sent))

    def recv(self, size: int) -> bytes:
        self.recv_calls += 1
        if not self.reply:
            raise socket.timeout("no reply left")
        take = min(size, self.chunk_size)
        chunk, self.reply = self.reply[:take], self.reply[take:]
        return chunk


def request_headers(raw: bytes) -> dict[str, str]:
    head = raw.split(b"\r\n\r\n", 1)[0].decode("ascii")
    lines = head.split("\r\n")[1:]
    return {k.strip().lower(): v.strip() for k, v in (line.split(":", 1) for line in lines if line)}


def key_from_request(raw: bytes) -> str:
    return request_headers(raw)["sec-websocket-key"]


def good_reply(raw: bytes, extra: bytes = b"") -> bytes:
    accept = ep._ws_accept(key_from_request(raw))
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    ).encode("ascii") + extra


@pytest.fixture
def connection(monkeypatch: pytest.MonkeyPatch) -> Callable[[HandshakeSocket], list[tuple[Any, Any]]]:
    """Patch socket.create_connection inside the module; returns the recorded call list."""

    def install(sock: HandshakeSocket) -> list[tuple[Any, Any]]:
        calls: list[tuple[Any, Any]] = []

        def create_connection(address: Any, timeout: Any = None, **_kw: Any) -> HandshakeSocket:
            calls.append((address, timeout))
            return sock

        monkeypatch.setattr(ep.socket, "create_connection", create_connection)
        return calls

    return install


def test_connect_sends_rfc_6455_upgrade_request(connection: Any) -> None:
    sock = HandshakeSocket(good_reply)
    calls = connection(sock)
    ws = ep.WebSocket.connect("127.0.0.1", 9229, "/devtools/page/ABC", timeout=3.5)
    assert calls == [(("127.0.0.1", 9229), 3.5)]
    raw = bytes(sock.sent)
    assert raw.startswith(b"GET /devtools/page/ABC HTTP/1.1\r\n")
    assert raw.endswith(b"\r\n\r\n")
    headers = request_headers(raw)
    assert headers["host"] == "127.0.0.1:9229"
    assert headers["upgrade"].lower() == "websocket"
    assert headers["connection"].lower() == "upgrade"
    assert headers["sec-websocket-version"] == "13"
    key = headers["sec-websocket-key"]
    assert len(key) == 24 and key.endswith("==")
    assert "origin" not in headers
    assert "sec-websocket-extensions" not in headers
    assert b"Origin" not in raw
    assert b"Sec-WebSocket-Extensions" not in raw
    assert isinstance(ws, ep.WebSocket)
    assert sock.closed is False


def test_connect_uses_a_fresh_random_key_per_connection(connection: Any) -> None:
    sock1 = HandshakeSocket(good_reply)
    connection(sock1)
    ep.WebSocket.connect("127.0.0.1", 1, "/a")
    sock2 = HandshakeSocket(good_reply)
    connection(sock2)
    ep.WebSocket.connect("127.0.0.1", 1, "/a")
    assert key_from_request(bytes(sock1.sent)) != key_from_request(bytes(sock2.sent))


def test_connect_403_raises_cdp_error_with_hint_and_closes_socket(connection: Any) -> None:
    sock = HandshakeSocket(lambda raw: b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
    connection(sock)
    with pytest.raises(RuntimeError, match="refused the websocket upgrade.*403 Forbidden") as info:
        ep.WebSocket.connect("127.0.0.1", 9229, "/devtools/page/ABC")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    assert "403" in (info.value.hint or "")  # type: ignore[attr-defined]
    assert sock.closed is True


def test_connect_wrong_accept_raises_cdp_error(connection: Any) -> None:
    def bad_accept(raw: bytes) -> bytes:
        return (
            "HTTP/1.1 101 Switching Protocols\r\n"
            f"Sec-WebSocket-Accept: {RFC_ACCEPT}\r\n"
            "\r\n"
        ).encode("ascii")

    sock = HandshakeSocket(bad_accept)
    connection(sock)
    with pytest.raises(RuntimeError, match="bad Sec-WebSocket-Accept") as info:
        ep.WebSocket.connect("127.0.0.1", 9229, "/devtools/page/ABC")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    assert sock.closed is True


def test_connect_missing_accept_header_raises_cdp_error(connection: Any) -> None:
    sock = HandshakeSocket(lambda raw: b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n")
    connection(sock)
    with pytest.raises(RuntimeError, match="bad Sec-WebSocket-Accept"):
        ep.WebSocket.connect("127.0.0.1", 9229, "/x")


def test_connect_correct_accept_succeeds_and_keeps_leftover_bytes(connection: Any) -> None:
    leftover = server_text('{"method":"Target.attached"}')
    sock = HandshakeSocket(lambda raw: good_reply(raw, extra=leftover))
    connection(sock)
    ws = ep.WebSocket.connect("127.0.0.1", 9229, "/devtools/page/ABC")
    recv_calls = sock.recv_calls
    assert ws.recv_text(0.1) == '{"method":"Target.attached"}'
    assert sock.recv_calls == recv_calls  # served from the retained bytes, no extra recv
    assert ws.recv_text(0.1) is None  # and the buffer is empty afterwards


def test_connect_accept_header_tolerates_surrounding_whitespace_and_case(connection: Any) -> None:
    def reply(raw: bytes) -> bytes:
        accept = ep._ws_accept(key_from_request(raw))
        return f"HTTP/1.1 101 Switching Protocols\r\nsec-websocket-accept:   {accept}  \r\n\r\n".encode("ascii")

    sock = HandshakeSocket(reply)
    connection(sock)
    ep.WebSocket.connect("127.0.0.1", 9229, "/x")
    assert sock.closed is False


def test_connect_reads_response_delivered_in_small_chunks(connection: Any) -> None:
    leftover = server_text("after")
    sock = HandshakeSocket(lambda raw: good_reply(raw, extra=leftover), chunk_size=5)
    connection(sock)
    ws = ep.WebSocket.connect("127.0.0.1", 9229, "/x")
    assert sock.recv_calls > 10
    assert ws.recv_text(0.1) == "after"


def test_connect_refused_connection_is_tagged_cdp(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(address: Any, timeout: Any = None, **_kw: Any) -> Any:
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(ep.socket, "create_connection", refuse)
    with pytest.raises(RuntimeError, match="Cannot connect to DevTools at 127.0.0.1:9229") as info:
        ep.WebSocket.connect("127.0.0.1", 9229, "/x")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    assert isinstance(info.value.__cause__, ConnectionRefusedError)


class ClosingSocket(HandshakeSocket):
    """Peer hangs up before sending any handshake bytes."""

    def recv(self, size: int) -> bytes:
        self.recv_calls += 1
        return b""


def test_connect_peer_closing_during_handshake_is_tagged_cdp(connection: Any) -> None:
    sock = ClosingSocket(lambda raw: b"")
    connection(sock)
    with pytest.raises(RuntimeError, match="closed the connection during the websocket handshake") as info:
        ep.WebSocket.connect("127.0.0.1", 9229, "/x")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]


def test_connect_peer_closing_during_handshake_closes_socket(connection: Any) -> None:
    sock = ClosingSocket(lambda raw: b"")
    connection(sock)
    with pytest.raises(RuntimeError):
        ep.WebSocket.connect("127.0.0.1", 9229, "/x")
    assert sock.closed is True


def test_connect_socket_error_during_handshake_is_tagged_cdp(connection: Any) -> None:
    class TimingOutSocket(HandshakeSocket):
        def recv(self, size: int) -> bytes:
            raise socket.timeout("timed out")

    sock = TimingOutSocket(lambda raw: b"")
    connection(sock)
    with pytest.raises(RuntimeError, match="handshake failed") as info:
        ep.WebSocket.connect("127.0.0.1", 9229, "/x")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]
    assert sock.closed is True


OVERSIZED_REPLY = b"HTTP/1.1 101 Switching Protocols\r\nX-Pad: " + b"a" * 70000


def test_connect_oversized_response_without_terminator_is_rejected(connection: Any) -> None:
    sock = HandshakeSocket(lambda raw: OVERSIZED_REPLY)
    connection(sock)
    with pytest.raises(RuntimeError, match="too large") as info:
        ep.WebSocket.connect("127.0.0.1", 9229, "/x")
    assert info.value.error_class == ep.ERR_CDP  # type: ignore[attr-defined]


def test_connect_oversized_response_closes_socket(connection: Any) -> None:
    sock = HandshakeSocket(lambda raw: OVERSIZED_REPLY)
    connection(sock)
    with pytest.raises(RuntimeError):
        ep.WebSocket.connect("127.0.0.1", 9229, "/x")
    assert sock.closed is True


def test_connect_rejects_malformed_status_line(connection: Any) -> None:
    sock = HandshakeSocket(lambda raw: b"garbage\r\n\r\n")
    connection(sock)
    with pytest.raises(RuntimeError, match="refused the websocket upgrade"):
        ep.WebSocket.connect("127.0.0.1", 9229, "/x")


def test_ws_connect_seam_delegates_to_websocket_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []

    def fake_connect(host: str, port: int, path: str, timeout: float = 10.0) -> str:
        seen.append((host, port, path, timeout))
        return "ws"

    monkeypatch.setattr(ep.WebSocket, "connect", staticmethod(fake_connect))
    assert ep._ws_connect("127.0.0.1", 9229, "/p", timeout=2.0) == "ws"
    assert seen == [("127.0.0.1", 9229, "/p", 2.0)]
