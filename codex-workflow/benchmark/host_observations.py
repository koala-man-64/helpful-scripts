"""Private host observations reconstructed from retained transport frames.

These observations establish neither semantic acceptance nor complete accounting.
They are producer internals, not a published census or an admission receipt.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .app_server_capture import PINNED_PROTOCOL_SHA256
from .host_capture import CaptureEvidence, Frame, _validate_accounting, read_capture
from .host_protocol import validate_host_payload


@dataclass(frozen=True)
class HostObservation:
    kind: str
    thread_id: str
    turn_id: str | None
    item_id: str | None
    frame: Frame
    related_threads: tuple[str, ...] = ()
    terminal_status: str | None = None


@dataclass(frozen=True)
class HostTrace:
    observations: tuple[HostObservation, ...]
    known_thread_ids: tuple[str, ...]
    partial_reasons: tuple[str, ...]


def _id(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def read_host_trace(metadata: Mapping, artifact_root: Path, *, expected_run_id: str) -> HostTrace:
    if (not isinstance(metadata, Mapping) or not _id(expected_run_id)
            or metadata.get("schema_version") != "codex-app-server-capture-v1"
            or metadata.get("adapter") != "codex-app-server-stdio-v1"
            or metadata.get("protocol_schema_digest") != "sha256:" + PINNED_PROTOCOL_SHA256
            or not isinstance(metadata.get("run_context"), dict)
            or metadata["run_context"].get("run_id") != expected_run_id):
        raise ValueError("host capture does not match the prepared run and protocol")
    return interpret_host_frames(read_capture(metadata, artifact_root))


def diagnostic_payload(trace: HostTrace) -> dict:
    """Private operator output; it is not the shared host-census wire contract."""
    return {
        "schema_version": "host-capture-diagnostic-v1",
        "capability": "diagnostic_only",
        "complete_accounting": False,
        "host_semantics_verified": False,
        "promotion_eligible": False,
        "known_thread_ids": list(trace.known_thread_ids),
        "partial_reasons": list(trace.partial_reasons),
        "observations": [{
            "kind": observation.kind,
            "thread_id": observation.thread_id,
            "turn_id": observation.turn_id,
            "item_id": observation.item_id,
            "related_threads": list(observation.related_threads),
            "terminal_status": observation.terminal_status,
            "frame": {"stream": observation.frame.stream, "offset": observation.frame.offset,
                      "length": len(observation.frame.raw), "digest": observation.frame.digest},
        } for observation in trace.observations],
    }


def interpret_host_frames(capture: CaptureEvidence) -> HostTrace:
    """Correlate explicit roots, reviews and spawn edges before reading events.

Scope discovery is a separate pass because an initial notification may precede
its request's response. No total order is inferred between transport streams.
Only a completed spawn from an already attributed sender enrolls a new child.
"""
    offsets = {"outbound": 0, "inbound": 0}
    for frame in capture.frames:
        if (not isinstance(frame.stream, str) or frame.stream not in offsets or type(frame.offset) is not int
                or frame.offset != offsets[frame.stream] or not isinstance(frame.raw, bytes)
                or frame.digest != "sha256:" + hashlib.sha256(frame.raw).hexdigest()):
            raise ValueError("host frame digest, stream or offset is invalid")
        offsets[frame.stream] += len(frame.raw)
    outbound = [(frame, frame.message()) for frame in capture.frames if frame.stream == "outbound"]
    inbound = [(frame, frame.message()) for frame in capture.frames if frame.stream == "inbound"]
    missing = _validate_accounting([frame for frame, _ in outbound], [frame for frame, _ in inbound])
    requests = {message["id"]: message for _, message in outbound
                if "id" in message and "method" in message}
    reasons = set(capture.partial_reasons)
    reasons.update(f"inbound:missing_response:{identifier!r}" for identifier in missing)
    for _, message in inbound:
        if "method" in message and "id" in message:
            reasons.add("inbound:server_request")
        if message.get("method") == "error":
            reasons.add("inbound:async_error")
        if "error" in message and "method" not in message:
            reasons.add("inbound:error_response")
    # This protocol cannot close hidden/retried attempts even for a clean trace.
    reasons.add("provider_scope_closure_unavailable")
    known: set[str] = set()
    roots: set[str] = set()
    owners: dict[str, str] = {}
    reviews: list[tuple[Frame, str, str, dict]] = []
    spawns: list[tuple[Frame, dict, dict]] = []
    observations: list[HostObservation] = []
    for frame, message in inbound:
        request = requests.get(message.get("id")) if "method" not in message else None
        result = message.get("result")
        if request and "error" not in message and isinstance(result, dict):
            if request["method"] == "thread/start":
                validate_host_payload("ThreadStartParams", request.get("params", {}))
                validate_host_payload("ThreadStartResponse", result)
                thread = result.get("thread")
                thread_id = thread.get("id") if isinstance(thread, dict) else None
                if not _id(thread_id):
                    reasons.add("invalid_root_response")
                else:
                    roots.add(thread_id)
                    known.add(thread_id)
                    observations.append(HostObservation("root_observed", thread_id, None, None, frame))
            elif request["method"] == "review/start":
                validate_host_payload("ReviewStartResponse", result)
                params = request.get("params", {})
                validate_host_payload("ReviewStartParams", params)
                if not isinstance(params, dict):
                    reasons.add("invalid_review_request")
                    continue
                parent, child = params.get("threadId"), result.get("reviewThreadId")
                turn = result.get("turn")
                delivery = params.get("delivery") or "inline"
                if (not _id(parent) or not _id(child) or not isinstance(turn, dict)
                        or not _id(turn.get("id")) or delivery not in {"inline", "detached"}
                        or (delivery == "inline") != (parent == child)):
                    reasons.add("invalid_review_response")
                else:
                    reviews.append((frame, parent, child, turn))
        params = message.get("params")
        notification_schemas = {"item/completed": "ItemCompletedNotification",
                                "turn/started": "TurnStartedNotification",
                                "turn/completed": "TurnCompletedNotification"}
        if message.get("method") in notification_schemas:
            validate_host_payload(notification_schemas[message["method"]], params)
        if message.get("method") == "item/completed" and isinstance(params, dict):
            item = params.get("item")
            if isinstance(item, dict) and item.get("type") == "collabAgentToolCall":
                if item.get("tool") == "spawnAgent" and item.get("status") == "completed":
                    spawns.append((frame, params, item))

    def enroll(parent: str, child: str) -> None:
        if child == parent or child in roots:
            raise ValueError("host ownership graph contains a cycle or conflicting root")
        previous = owners.get(child)
        if previous is not None and previous != parent:
            raise ValueError("host child has conflicting owners")
        ancestor = parent
        while ancestor in owners:
            ancestor = owners[ancestor]
            if ancestor == child:
                raise ValueError("host ownership graph contains a cycle")
        owners[child] = parent
        known.add(child)

    # Each successful pass enrolls at least one previously unknown thread.
    for _ in range(len(reviews) + len(spawns) + 1):
        previous = len(known)
        for _, parent, child, _ in reviews:
            if parent in known and child != parent:
                enroll(parent, child)
        for _, params, item in spawns:
            parent = params.get("threadId")
            children = item.get("receiverThreadIds")
            if (_id(parent) and parent in known and item.get("senderThreadId") == parent
                    and isinstance(children, list) and children and all(_id(child) for child in children)
                    and len(set(children)) == len(children) and _id(item.get("id"))
                    and _id(params.get("turnId")) and isinstance(item.get("agentsStates"), dict)):
                for child in children:
                    enroll(parent, child)
        if len(known) == previous:
            break

    for frame, parent, child, turn in reviews:
        if parent not in known:
            reasons.add("unattributed_review")
            continue
        observations.append(HostObservation("review_started", child, turn["id"], None, frame, (parent,)))
    terminals: dict[tuple[str, str], str] = {}
    for frame, message in inbound:
        method, params = message.get("method"), message.get("params")
        if not isinstance(params, dict) or "threadId" not in params:
            continue
        thread_id = params["threadId"]
        if not _id(thread_id) or thread_id not in known:
            reasons.add("foreign_thread_event")
            continue
        turn = params.get("turn")
        if method in {"turn/started", "turn/completed"}:
            if not isinstance(turn, dict) or not _id(turn.get("id")):
                reasons.add("invalid_turn_event")
                continue
            if method == "turn/completed" and turn.get("status") not in {"completed", "failed", "interrupted"}:
                reasons.add("invalid_terminal_status")
                continue
            if method == "turn/completed" and turn["status"] != "completed":
                reasons.add("unsuccessful_turn")
            status = None
            if method == "turn/completed":
                status = turn["status"]
                key = (thread_id, turn["id"])
                if key in terminals:
                    reasons.add("duplicate_terminal" if terminals[key] == status else "contradictory_terminal")
                else:
                    terminals[key] = status
            observations.append(HostObservation(method, thread_id, turn["id"], None, frame, terminal_status=status))
        elif method == "item/completed":
            item, turn_id = params.get("item"), params.get("turnId")
            if not isinstance(item, dict) or not _id(item.get("id")) or not _id(turn_id):
                reasons.add("invalid_item_event")
                continue
            kind, related = item.get("type"), ()
            if not isinstance(kind, str):
                reasons.add("invalid_item_type")
                continue
            if kind == "collabAgentToolCall":
                children = item.get("receiverThreadIds")
                if (item.get("senderThreadId") != thread_id or not isinstance(children, list)
                        or not children or not all(_id(child) and child in known for child in children)
                        or len(set(children)) != len(children) or not isinstance(item.get("agentsStates"), dict)):
                    reasons.add("unattributed_collaboration")
                    continue
                if item.get("status") != "completed":
                    reasons.add("unfinished_collaboration")
                    continue
                tool = item.get("tool")
                if tool not in ("spawnAgent", "sendInput", "resumeAgent", "wait", "closeAgent",
                                "sendMessage", "followupTask", "interruptAgent", "listAgents"):
                    reasons.add("unknown_collaboration_tool")
                    continue
                if tool == "wait":
                    reasons.add("external_wait_scheduler_unverified")
                # Tool completion is an observation, not proof of admission,
                # delivery semantics, review acceptance or scheduler activity.
                kind = "collaboration/" + tool
                related = tuple(children)
            elif kind not in {"contextCompaction", "agentMessage"}:
                continue
            observations.append(HostObservation(kind, thread_id, turn_id, item["id"], frame, related))
    # Preserve within-inbound ordering without suggesting a cross-stream order.
    observations.sort(key=lambda observation: observation.frame.offset)
    return HostTrace(tuple(observations), tuple(sorted(known)), tuple(sorted(reasons)))
