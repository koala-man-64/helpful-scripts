from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark.host_capture import CaptureEvidence, Frame  # noqa: E402
from benchmark.host_observations import interpret_host_frames, read_host_trace  # noqa: E402
from benchmark.app_server_capture import PINNED_PROTOCOL_SHA256  # noqa: E402
from benchmark.runner import main  # noqa: E402


def request(identifier, method, **params):
    return {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}


def response(identifier, **result):
    if "thread" in result:
        result["thread"] = {"cliVersion": "0.153.4", "createdAt": 0, "updatedAt": 0,
                            "cwd": "C:/fixture", "ephemeral": True, "modelProvider": "openai",
                            "preview": "", "projectId": None, "sessionId": "session",
                            "source": "appServer", "status": {"type": "idle"}, "turns": [],
                            **result["thread"]}
        result = {"approvalPolicy": "untrusted", "approvalsReviewer": "user", "cwd": "C:/fixture",
                  "model": "fixture-model", "modelProvider": "openai", "sandbox": {"type": "readOnly"}, **result}
    if "turn" in result:
        result["turn"] = {"items": [], "status": "inProgress", **result["turn"]}
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def event(method, thread="root", **params):
    if "turn" in params:
        params["turn"] = {"items": [], "status": "inProgress", **params["turn"]}
    return {"jsonrpc": "2.0", "method": method, "params": {"threadId": thread, **params}}


def item(kind, thread="root", **fields):
    return event("item/completed", thread, turnId="turn", completedAtMs=1,
                 item={"type": kind, "id": "item", **fields})


def collab(sender="root", receivers=None, **fields):
    return item("collabAgentToolCall", sender, senderThreadId=sender,
                receiverThreadIds=receivers if receivers is not None else ["child"],
                agentsStates={}, status="completed", tool="spawnAgent", **fields)


def evidence(outbound, inbound, partial=()):
    frames = []
    for stream, messages in (("outbound", outbound), ("inbound", inbound)):
        offset = 0
        for message in messages:
            raw = json.dumps(message, ensure_ascii=False).encode() + b"\n"
            frames.append(Frame(stream, offset, raw, "sha256:" + hashlib.sha256(raw).hexdigest()))
            offset += len(raw)
    return CaptureEvidence(tuple(frames), tuple(partial))


class HostObservationTests(unittest.TestCase):
    def trace(self, inbound, outbound=None, partial=()):
        return interpret_host_frames(evidence(
            outbound or [request(1, "thread/start")],
            [response(1, thread={"id": "root"}), *inbound], partial))

    def test_observes_compaction_and_continuation_without_claiming_retention(self):
        trace = self.trace([
            item("contextCompaction"), item("agentMessage", text="decisions retained"),
            event("turn/completed", turn={"id": "turn", "status": "completed"}),
        ])
        self.assertEqual([row.kind for row in trace.observations],
                         ["root_observed", "contextCompaction", "agentMessage", "turn/completed"])
        self.assertIn("provider_scope_closure_unavailable", trace.partial_reasons)
        self.assertEqual(trace.observations[1].frame.message()["params"]["item"]["type"], "contextCompaction")

    def test_notification_before_response_is_attributed_without_reordering_wire(self):
        trace = interpret_host_frames(evidence([request(1, "thread/start")], [
            item("contextCompaction"), response(1, thread={"id": "root"}),
        ]))
        self.assertEqual(trace.observations[0].kind, "contextCompaction")
        self.assertNotIn("foreign_thread_event", trace.partial_reasons)

    def test_only_real_spawn_enrolls_child_and_foreign_sender_injection_fails(self):
        foreign = collab()
        foreign["params"]["threadId"] = "foreign"
        trace = self.trace([foreign, item("contextCompaction", "child")])
        self.assertEqual(trace.known_thread_ids, ("root",))
        self.assertIn("foreign_thread_event", trace.partial_reasons)
        trace = self.trace([collab(), item("contextCompaction", "child")])
        self.assertEqual(trace.known_thread_ids, ("child", "root"))
        self.assertIn("collaboration/spawnAgent", [row.kind for row in trace.observations])

    def test_review_requires_response_correlation_and_is_not_child_dispatch(self):
        trace = self.trace([response(2, reviewThreadId="review", turn={"id": "review-turn"})],
                           [request(1, "thread/start"), request(2, "review/start", threadId="root",
                            delivery="detached", target={"type": "uncommittedChanges"})])
        self.assertEqual(trace.known_thread_ids, ("review", "root"))
        self.assertEqual([row.kind for row in trace.observations], ["root_observed", "review_started"])
        with self.assertRaisesRegex(ValueError, "unknown request"):
            self.trace([response(2, reviewThreadId="review", turn={"id": "review-turn"})])

    def test_review_rejects_missing_target_and_accepts_default_inline_delivery(self):
        with self.assertRaisesRegex(ValueError, "ReviewStartParams"):
            self.trace([response(2, reviewThreadId="review", turn={"id": "review-turn"})],
                       [request(1, "thread/start"), request(2, "review/start", threadId="root", delivery="detached")])
        trace = self.trace([response(2, reviewThreadId="root", turn={"id": "review-turn"})],
                           [request(1, "thread/start"), request(2, "review/start", threadId="root",
                            target={"type": "uncommittedChanges"})])
        self.assertEqual(trace.known_thread_ids, ("root",))
        self.assertEqual(trace.observations[-1].kind, "review_started")

    def test_conflicting_owners_and_cycles_reject(self):
        with self.assertRaisesRegex(ValueError, "conflicting owners"):
            self.trace([response(2, thread={"id": "other"}), collab(), collab("other")],
                       [request(1, "thread/start"), request(2, "thread/start")])
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.trace([collab(), collab("child", ["root"])])

    def test_collaboration_wait_never_becomes_external_scheduler_proof(self):
        waiting = collab()
        waiting["params"]["item"]["tool"] = "wait"
        trace = self.trace([collab(), waiting])
        self.assertIn("collaboration/wait", [row.kind for row in trace.observations])
        self.assertIn("external_wait_scheduler_unverified", trace.partial_reasons)
        self.assertFalse(any("continuation" in row.kind for row in trace.observations))

    def test_invalid_types_and_fake_markers_cannot_enroll_or_invent_boundary(self):
        malformed = collab()
        malformed["params"]["threadId"] = []
        with self.assertRaises(ValueError):
            self.trace([malformed])
        with self.assertRaises(ValueError):
            self.trace([item({"fake": True})])
        trace = self.trace([item("agentMessage", text="contextCompaction")])
        self.assertEqual(trace.known_thread_ids, ("root",))
        self.assertNotIn("contextCompaction", [row.kind for row in trace.observations])

    def test_consumed_notifications_require_provider_shape(self):
        malformed = collab()
        del malformed["params"]["completedAtMs"]
        with self.assertRaises(ValueError):
            self.trace([malformed])
        terminal = event("turn/completed", turn={"id": "turn", "status": "completed"})
        del terminal["params"]["threadId"]
        with self.assertRaises(ValueError):
            self.trace([terminal])
        with self.assertRaises(ValueError):
            interpret_host_frames(evidence([request(1, "thread/start")],
                                           [{"id": 1, "result": {"thread": {"id": "root"}}}]))

    def test_duplicate_and_conflicting_terminals_remain_explicit(self):
        terminal = event("turn/completed", turn={"id": "turn", "status": "completed"})
        conflict = event("turn/completed", turn={"id": "turn", "status": "failed"})
        trace = self.trace([terminal, terminal, conflict])
        self.assertIn("duplicate_terminal", trace.partial_reasons)
        self.assertIn("contradictory_terminal", trace.partial_reasons)
        self.assertEqual([row.terminal_status for row in trace.observations if row.kind == "turn/completed"],
                         ["completed", "completed", "failed"])

    def test_forged_frame_digest_or_offset_rejects(self):
        captured = evidence([request(1, "thread/start")], [response(1, thread={"id": "root"})])
        for forged in (replace(captured.frames[1], digest="sha256:" + "0" * 64),
                       replace(captured.frames[1], offset=1), replace(captured.frames[1], stream="fixture")):
            with self.subTest(frame=forged), self.assertRaisesRegex(ValueError, "digest, stream or offset"):
                interpret_host_frames(replace(captured, frames=(captured.frames[0], forged)))

    def test_failure_and_existing_partial_state_survive_later_success(self):
        trace = self.trace([
            event("turn/completed", turn={"id": "one", "status": "failed"}),
            event("turn/completed", turn={"id": "two", "status": "completed"}),
        ], partial=("inbound:async_error",))
        self.assertIn("unsuccessful_turn", trace.partial_reasons)
        self.assertIn("inbound:async_error", trace.partial_reasons)

    def test_wrong_run_and_missing_protocol_reject_before_reading_paths(self):
        with self.assertRaisesRegex(ValueError, "prepared run and protocol"):
            read_host_trace({}, Path("does-not-exist"), expected_run_id="run")

    def test_cli_retains_private_diagnostic_without_bodies_or_overwriting_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_frames = evidence([request(1, "thread/start")], [
                response(1, thread={"id": "root"}), item("agentMessage", text="private body"),
            ]).frames
            refs = {}
            for stream in ("outbound", "inbound", "stderr"):
                raw = b"".join(frame.raw for frame in raw_frames if frame.stream == stream)
                path = root / (stream + ".raw")
                path.write_bytes(raw)
                refs[stream] = {"path": str(path), "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
            metadata = {"schema_version": "codex-app-server-capture-v1", "adapter": "codex-app-server-stdio-v1",
                        "protocol_schema_digest": "sha256:" + PINNED_PROTOCOL_SHA256,
                        "run_context": {"run_id": "run"}, "raw_refs": refs}
            source, target = root / "capture.json", root / "diagnostic.json"
            source.write_text(json.dumps(metadata), encoding="utf-8")
            before = {path: path.read_bytes() for path in root.iterdir()}
            argv = ["runner", "inspect-host-capture", "--capture", str(source), "--artifact-root", str(root),
                    "--run-id", "run", "--output", str(target)]
            with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            result = json.loads(target.read_bytes())
            self.assertEqual(result["capability"], "diagnostic_only")
            self.assertFalse(result["complete_accounting"])
            self.assertFalse(result["host_semantics_verified"])
            self.assertFalse(result["promotion_eligible"])
            self.assertNotIn("private body", target.read_text())
            self.assertEqual(len(result["observations"]), 2)
            saved = target.read_bytes()
            with patch.object(sys, "argv", argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main()
            self.assertEqual(target.read_bytes(), saved)
            self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_cli_rejects_foreign_run_without_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / "capture.json", root / "diagnostic.json"
            source.write_text(json.dumps({"run_context": {"run_id": "foreign"}}))
            argv = ["runner", "inspect-host-capture", "--capture", str(source), "--artifact-root", str(root),
                    "--run-id", "run", "--output", str(target)]
            with patch.object(sys, "argv", argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main()
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
