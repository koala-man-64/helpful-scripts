"""Read sealed app-server capture frames without assigning host semantics."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_RAW_STREAMS = frozenset({"outbound", "inbound", "stderr"})
_REFERENCE_FIELDS = frozenset({"path", "digest"})


@dataclass(frozen=True)
class Frame:
    """One byte-exact JSON-RPC line from an outbound or inbound stream."""

    stream: str
    offset: int
    raw: bytes
    digest: str

    def message(self) -> dict[str, Any]:
        """Decode a new, strictly validated message from this immutable wire frame."""
        return _decode_frame(self.raw)


@dataclass(frozen=True)
class CaptureEvidence:
    """Validated wire records and reasons this capture cannot be considered complete."""

    frames: tuple[Frame, ...]
    partial_reasons: tuple[str, ...]


def read_capture(metadata: Mapping[str, Any], artifact_root: Path) -> CaptureEvidence:
    """Read pinned capture bytes while rejecting unsealed or inconsistent evidence.

    This reader only establishes byte integrity and request/response accounting. It
    deliberately does not derive host behavior, promotion state, or completeness.
    """
    if not isinstance(metadata, Mapping):
        raise ValueError("capture metadata must be a mapping")

    root = artifact_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("artifact root must be a directory")
    raw_refs = metadata.get("raw_refs")
    if not isinstance(raw_refs, Mapping) or set(raw_refs) != _RAW_STREAMS:
        raise ValueError("raw_refs must contain exactly outbound, inbound, and stderr")

    raw_streams = {
        stream: _read_sealed_bytes(raw_refs[stream], root, stream)
        for stream in sorted(_RAW_STREAMS)
    }
    outbound = _read_json_lines("outbound", raw_streams["outbound"])
    inbound = _read_json_lines("inbound", raw_streams["inbound"])
    missing_responses = _validate_accounting(outbound, inbound)

    reasons = _metadata_partial_reasons(metadata)
    reasons.update(
        f"inbound:missing_response:{request_id!r}"
        for request_id in sorted(missing_responses, key=str)
    )
    for frame in inbound:
        message = frame.message()
        if "method" in message and "id" in message:
            reasons.add("inbound:server_request")
        elif message.get("method") == "error":
            reasons.add("inbound:async_error")
        elif "error" in message:
            reasons.add("inbound:error_response")

    # Stderr remains sealed opaque evidence. Its contents are deliberately never
    # decoded as JSON or interpreted as host behavior.
    return CaptureEvidence(
        frames=tuple(outbound + inbound), partial_reasons=tuple(sorted(reasons))
    )


def _read_sealed_bytes(reference: Any, root: Path, stream: str) -> bytes:
    if not isinstance(reference, Mapping) or set(reference) != _REFERENCE_FIELDS:
        raise ValueError(f"{stream} raw reference must contain exactly path and digest")
    raw_path, expected_digest = reference["path"], reference["digest"]
    if not isinstance(raw_path, str) or not isinstance(expected_digest, str):
        raise ValueError(f"{stream} raw reference fields must be strings")
    if not expected_digest.startswith("sha256:") or len(expected_digest) != 71:
        raise ValueError(f"{stream} raw reference must use a full sha256 digest")
    expected_hex = expected_digest.removeprefix("sha256:")
    if any(character not in "0123456789abcdef" for character in expected_hex):
        raise ValueError(f"{stream} raw reference must use a lowercase sha256 digest")

    candidate = Path(raw_path)
    path = (root / candidate).resolve(strict=True) if not candidate.is_absolute() else candidate.resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"{stream} raw path must resolve to a file inside artifact root")
    raw = path.read_bytes()
    actual_digest = hashlib.sha256(raw).hexdigest()
    if actual_digest != expected_hex:
        raise ValueError(f"{stream} raw bytes do not match the sealed digest")
    return raw


def _read_json_lines(stream: str, raw: bytes) -> list[Frame]:
    frames: list[Frame] = []
    offset = 0
    while offset < len(raw):
        newline = raw.find(b"\n", offset)
        end = len(raw) if newline < 0 else newline + 1
        frame_raw = raw[offset:end]
        if newline < 0 and frame_raw.endswith(b"\r"):
            raise ValueError(f"{stream} has a truncated CRLF line ending at byte {offset}")
        _decode_frame(frame_raw)
        frames.append(
            Frame(
                stream=stream,
                offset=offset,
                raw=frame_raw,
                digest="sha256:" + hashlib.sha256(frame_raw).hexdigest(),
            )
        )
        offset = end
    return frames


def _decode_frame(raw: bytes) -> dict[str, Any]:
    payload = raw[:-1] if raw.endswith(b"\n") else raw
    if payload.endswith(b"\r"):
        payload = payload[:-1]
    if not payload:
        raise ValueError("JSON-RPC frame cannot be blank")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
            parse_float=_finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid or truncated JSON-RPC frame") from error
    if not isinstance(value, dict):
        raise ValueError("JSON-RPC frame must be an object")
    _validate_jsonrpc_shape(value)
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite JSON number: {value}")
    return number


def _validate_jsonrpc_shape(message: Mapping[str, Any]) -> None:
    if "jsonrpc" in message and message["jsonrpc"] != "2.0":
        raise ValueError("JSON-RPC frame must declare jsonrpc 2.0 when present")
    has_method = "method" in message
    has_id = "id" in message
    has_result = "result" in message
    has_error = "error" in message
    if has_method:
        if not isinstance(message["method"], str) or not message["method"]:
            raise ValueError("JSON-RPC method must be a non-empty string")
        if has_result or has_error:
            raise ValueError("JSON-RPC request cannot include result or error")
        if "params" in message and not isinstance(message["params"], (dict, list)):
            raise ValueError("JSON-RPC params must be an object or array")
        if has_id:
            _request_id(message["id"])
        return
    if not has_id or has_result == has_error:
        raise ValueError("JSON-RPC response must contain one of result or error and an id")
    _request_id(message["id"])
    if has_error and not isinstance(message["error"], dict):
        raise ValueError("JSON-RPC error must be an object")


def _request_id(value: Any) -> str | int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("JSON-RPC id must be a string or integer")
    return value


def _validate_accounting(
    outbound: list[Frame], inbound: list[Frame]
) -> set[str | int]:
    requests: set[str | int] = set()
    for frame in outbound:
        message = frame.message()
        if "method" not in message:
            raise ValueError("outbound frame must be a client request or notification")
        if "id" not in message:
            continue
        request_id = _request_id(message["id"])
        if request_id in requests:
            raise ValueError(f"duplicate outbound request id: {request_id!r}")
        requests.add(request_id)

    responses: set[str | int] = set()
    for frame in inbound:
        message = frame.message()
        if "method" in message:
            continue
        response_id = _request_id(message["id"])
        if response_id not in requests:
            raise ValueError(f"inbound response has unknown request id: {response_id!r}")
        if response_id in responses:
            raise ValueError(f"duplicate inbound response id: {response_id!r}")
        responses.add(response_id)
    missing = requests - responses
    return missing


def _metadata_partial_reasons(metadata: Mapping[str, Any]) -> set[str]:
    reasons = {"metadata:complete_accounting_not_true"}
    if metadata.get("partial_census") is True:
        reasons.add("metadata:partial_census")
    if metadata.get("host_semantics_verified") is True:
        reasons.add("metadata:host_semantics_untrusted")
    cleanup_errors = metadata.get("cleanup_errors", [])
    if not isinstance(cleanup_errors, list) or not all(
        isinstance(error, str) and error for error in cleanup_errors
    ):
        raise ValueError("cleanup_errors must be a list of non-empty strings")
    if cleanup_errors:
        reasons.add("metadata:cleanup_errors")
    if metadata.get("pending_request_ids"):
        reasons.add("metadata:pending_request_ids")
    if metadata.get("pending_server_requests"):
        reasons.add("metadata:pending_server_requests")
    return reasons
