"""Construct partial census records from sealed, attributed host observations.

Transport requests are not model requests. A known thread is retained as an
unclosed observed attempt; this producer cannot close its hidden turns/retries.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Mapping

from .census_contract import strict_json, validate_census
from .host_capture import Frame, read_capture
from .host_observations import read_host_trace


PIN_NAMES = frozenset({"run_set_digest", "manifest_digest", "producer_digest",
                       "runtime_digest", "protocol_digest"})
SOURCE_FILES = ("host_census.py", "host_capture.py", "host_observations.py", "host_protocol.py",
                "census_contract.py", "protocol/host-events-0.153.4.schema.json")


def producer_digest() -> str:
    """Length-framed source digest, to be frozen independently before capture."""
    digest = hashlib.sha256()
    for name in SOURCE_FILES:
        raw = (Path(__file__).parent / name).read_bytes()
        encoded = name.encode()
        digest.update(len(encoded).to_bytes(4, "big") + encoded + len(raw).to_bytes(8, "big") + raw)
    return "sha256:" + digest.hexdigest()


def _ref(frame: Frame) -> dict:
    return {"artifact_id": frame.stream, "offset": frame.offset, "length": len(frame.raw),
            "frame_digest": frame.digest}


def build_partial_census(capture_path: Path, artifact_root: Path, *, run_id: str,
                         expected_pins: Mapping[str, str], sealed_at: str) -> dict:
    """Read immutable input; validate an envelope without admitting any claim.

The caller supplies preparation pins and its artifact sealing timestamp, not a
provider closure timestamp. No inferred usage, pricing or scheduler rows exist.
"""
    if (not isinstance(expected_pins, Mapping) or set(expected_pins) != PIN_NAMES
            or any(not isinstance(v, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", v)
                   for v in expected_pins.values())):
        raise ValueError("five independently prepared census pins are required")
    if expected_pins["producer_digest"] != producer_digest():
        raise ValueError("census producer differs from preparation")
    raw_metadata = capture_path.read_bytes()
    metadata = strict_json(raw_metadata)
    trace = read_host_trace(metadata, artifact_root, expected_run_id=run_id)
    if "contradictory_terminal" in trace.partial_reasons:
        raise ValueError("contradictory terminal evidence cannot produce a census")
    runtime = metadata.get("runtime")
    if (not isinstance(runtime, dict) or not isinstance(runtime.get("sha256"), str)
            or "sha256:" + runtime["sha256"].removeprefix("sha256:").lower() != expected_pins["runtime_digest"]
            or metadata["protocol_schema_digest"] != expected_pins["protocol_digest"]):
        raise ValueError("capture runtime/protocol differs from preparation")
    capture = read_capture(metadata, artifact_root)
    roots = [row for row in trace.observations if row.kind == "root_observed"]
    if len(roots) != 1:
        raise ValueError("partial census requires exactly one observed root")
    root = artifact_root.resolve(strict=True)
    artifacts = {}
    for stream, reference in metadata["raw_refs"].items():
        path = Path(reference["path"])
        if ".." in path.parts:
            raise ValueError("census artifact path contains traversal")
        path = path if path.is_absolute() else root / path
        # Refuse links/reparse points even when their resolved target is inside.
        current = path
        while current != root:
            if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
                raise ValueError("census artifact cannot be a link or reparse point")
            if current == current.parent:
                raise ValueError("census artifact is outside its root")
            current = current.parent
        path = path.resolve(strict=True)
        raw = path.read_bytes()
        if "sha256:" + hashlib.sha256(raw).hexdigest() != reference["digest"]:
            raise ValueError("capture changed while census was constructed")
        artifacts[stream] = {"path": path.relative_to(root).as_posix(), "digest": reference["digest"],
                             "byte_length": len(raw), "sealed_at": sealed_at, "kind": stream}
    outgoing = {frame.message()["id"]: frame for frame in capture.frames
                if frame.stream == "outbound" and "id" in frame.message()}
    root_row = roots[0]
    root_id = "thread:" + root_row.thread_id
    attempts = {}

    def observed_attempt(thread: str, role: str, parent: str | None, dispatch: Frame, ownership: Frame) -> None:
        identity = "thread:" + thread
        if identity in attempts:
            return
        attempts[identity] = {"id": identity, "role": role, "root_attempt_id": root_id,
                              "parent_attempt_id": parent, "retry_of": None, "thread_id": thread,
                              "turn_id": None, "dispatch": _ref(dispatch), "ownership": _ref(ownership),
                              "terminal": None, "terminal_status": "unknown", "request_ids": [],
                              "partial_reasons": ["thread_attempt_boundary_unverified",
                                                  "model_request_attribution_unavailable",
                                                  "attempt_terminal_scope_unavailable"]}

    observed_attempt(root_row.thread_id, "root", None,
                     outgoing[root_row.frame.message()["id"]], root_row.frame)
    # Fixed-point traversal handles child events arriving before parent responses.
    for _ in range(len(trace.known_thread_ids)):
        for row in trace.observations:
            parent = "thread:" + row.thread_id
            if row.kind == "collaboration/spawnAgent" and parent in attempts:
                for child in row.related_threads:
                    observed_attempt(child, "child", parent, row.frame, row.frame)
            elif row.kind == "review_started" and row.related_threads:
                parent = "thread:" + row.related_threads[0]
                if parent in attempts and row.thread_id != row.related_threads[0]:
                    observed_attempt(row.thread_id, "review", parent,
                                     outgoing[row.frame.message()["id"]], row.frame)
    request_ids = set(outgoing)
    response_ids = {frame.message()["id"] for frame in capture.frames
                    if frame.stream == "inbound" and "method" not in frame.message()
                    and "id" in frame.message()}
    pending_ids = [str(key) for key in sorted(request_ids - response_ids, key=str)]
    if len(pending_ids) != len(set(pending_ids)):
        raise ValueError("pending RPC IDs collide in the census text representation")
    pending_server = [_ref(frame) for frame in capture.frames if frame.stream == "inbound"
                      and "method" in frame.message() and "id" in frame.message()]
    reasons = sorted(set(trace.partial_reasons) | {"model_request_attribution_unavailable",
                     "host_semantic_evaluators_unavailable", "thread_session_usage_binding_unverified",
                     "capture_segment_closure_unavailable"})
    value = {"schema_version": "benchmark-host-census-v1", "run_id": run_id,
             **dict(expected_pins), "capture_id": "capture:" + hashlib.sha256(raw_metadata).hexdigest(),
             "artifacts": artifacts,
             "segments": [{"id": "segment-0", "sequence": 0, "prior_segment_id": None,
                           "start": _ref(capture.frames[0]), "stop": None, "cutoff": None,
                           "closed": False, "recovery_proof": None, "partial_reasons": reasons}],
             "attempts": list(attempts.values()), "requests": [], "host_events": [], "scenarios": [],
             "reconciliation": {"scope_closure": None, "terminal_cutoff": None, "enumeration_refs": [],
                                "pending_rpc_ids": pending_ids,
                                "pending_server_request_refs": pending_server, "missing_attempt_ids": [],
                                "conflicting_ids": [], "partial_reasons": reasons,
                                "complete_accounting": False, "host_semantics_verified": False,
                                "promotion_eligible": False}}
    validate_census(value)
    return value
