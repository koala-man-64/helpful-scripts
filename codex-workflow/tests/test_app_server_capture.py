from __future__ import annotations

import io
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark.app_server_capture import (  # noqa: E402
    AppServerCapture,
    CaptureFiles,
    RuntimePin,
    verify_runtime_pin,
    verify_model_catalog,
)


class FakeProcess:
    def __init__(self) -> None:
        self.running, self.terminated, self.killed = True, False, False

    def poll(self) -> None | int:
        return None if self.running else 0

    def terminate(self) -> None:
        self.terminated = True
        self.running = False

    def kill(self) -> None:
        self.killed = True
        self.running = False

    def wait(self, timeout: float) -> int:
        return 0


class CaptureTests(unittest.TestCase):
    def observer(
        self, incoming: bytes = b"", *, process: FakeProcess | None = None
    ) -> tuple[AppServerCapture, io.BytesIO, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root, outbound = Path(temporary.name), io.BytesIO()
        capture = AppServerCapture(
            outbound,
            io.BytesIO(incoming),
            io.BytesIO(b"stderr\n"),
            CaptureFiles(root / "out.raw", root / "in.raw", root / "err.raw"),
            process=process,
            run_context={
                "run_id": "run-1",
                "workspace": str(root),
                "model": "gpt-5.6-terra",
                "effort": "medium",
            },
        )
        self.addCleanup(capture.close)
        return capture, outbound, root

    def handshake(self, capture: AppServerCapture) -> None:
        capture.initialize("benchmark", "1")
        capture._observe(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "codexHome": str(ROOT),
                    "platformFamily": "windows",
                    "platformOs": "windows",
                    "userAgent": "codex-cli/0.153.4",
                },
            }
        )
        capture.initialized()

    def test_retains_raw_bytes_correlates_thread_history_items_and_review(self) -> None:
        capture, outgoing, root = self.observer()
        self.handshake(capture)
        start = capture.request("thread/start", {"model": "gpt-5.6-terra"})
        capture._observe(
            {
                "jsonrpc": "2.0",
                "id": start,
                "result": {
                    "thread": {
                        "id": "parent",
                        "turns": [
                            {
                                "id": "old",
                                "items": [
                                    {
                                        "type": "collabAgentToolCall",
                                        "senderThreadId": "parent",
                                        "receiverThreadIds": ["child"],
                                    }
                                ],
                            }
                        ],
                    }
                },
            }
        )
        read = capture.request(
            "thread/read", {"threadId": "parent", "includeTurns": True}
        )
        capture._observe(
            {
                "jsonrpc": "2.0",
                "id": read,
                "result": {
                    "thread": {
                        "id": "parent",
                        "turns": [{"id": "history", "items": []}],
                    },
                    "item": {"turnId": "item-turn"},
                },
            }
        )
        review = capture.request(
            "review/start",
            {
                "threadId": "parent",
                "target": {"type": "uncommittedChanges"},
                "delivery": "detached",
            },
        )
        capture._observe(
            {
                "jsonrpc": "2.0",
                "id": review,
                "result": {
                    "reviewThreadId": "review-child",
                    "turn": {"id": "review-turn", "items": []},
                },
            }
        )
        self.assertEqual(capture.census.thread_ids, {"parent", "child", "review-child"})
        self.assertTrue(
            {"old", "history", "item-turn", "review-turn"} <= capture.census.turn_ids
        )
        self.assertEqual(
            capture.census.collaboration_edges,
            {("parent", "child"), ("parent", "review-child")},
        )
        self.assertEqual(capture.census.detached_review_thread_ids, {"review-child"})
        self.assertIn(b'"method":"initialized"', outgoing.getvalue())
        self.assertEqual((root / "out.raw").read_bytes(), outgoing.getvalue())

    def test_unknown_scope_policy_override_and_prehandshake_calls_fail(self) -> None:
        capture, _, _ = self.observer()
        with self.assertRaisesRegex(RuntimeError, "handshake"):
            capture.request("thread/start", {})
        self.handshake(capture)
        with self.assertRaisesRegex(ValueError, "unknown"):
            capture.request(
                "turn/start",
                {
                    "threadId": "not-known",
                    "input": [],
                    "model": "gpt-5.6-terra",
                    "effort": "medium",
                },
            )
        with self.assertRaisesRegex(ValueError, "policy"):
            capture.request(
                "thread/start",
                {"model": "gpt-5.6-terra", "sandbox": "danger-full-access"},
            )
        with self.assertRaisesRegex(ValueError, "unsupported"):
            capture.request("config/write", {})

    def test_initialize_response_without_initialized_notification_cannot_start_work(
        self,
    ) -> None:
        capture, _, _ = self.observer()
        capture.initialize("benchmark", "1")
        capture._observe(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "codexHome": str(ROOT),
                    "platformFamily": "windows",
                    "platformOs": "windows",
                    "userAgent": "codex-cli/0.153.4",
                },
            }
        )
        with self.assertRaisesRegex(RuntimeError, "handshake"):
            capture.request("thread/start", {"model": "gpt-5.6-terra"})

    def test_server_approval_is_pending_and_blocks_dependent_mutation(self) -> None:
        capture, _, _ = self.observer()
        self.handshake(capture)
        start = capture.request("thread/start", {"model": "gpt-5.6-terra"})
        capture._observe(
            {
                "jsonrpc": "2.0",
                "id": start,
                "result": {"thread": {"id": "parent", "turns": []}},
            }
        )
        capture._observe(
            {
                "jsonrpc": "2.0",
                "id": "approval",
                "method": "commandExecution/requestApproval",
                "params": {"threadId": "parent"},
            }
        )
        self.assertEqual(len(capture.census.pending_server_requests), 1)
        with self.assertRaisesRegex(RuntimeError, "pending"):
            capture.request(
                "turn/start",
                {
                    "threadId": "parent",
                    "input": [],
                    "model": "gpt-5.6-terra",
                    "effort": "medium",
                },
            )
        self.assertTrue(capture.census.partial_census)

    def test_duplicate_unknown_error_and_timeout_fail_closed(self) -> None:
        capture, _, _ = self.observer()
        self.handshake(capture)
        capture._observe({"jsonrpc": "2.0", "id": 999, "result": {}})
        self.assertTrue(capture.census.partial_census)
        start = capture.request("thread/start", {"model": "gpt-5.6-terra"})
        capture._observe(
            {
                "jsonrpc": "2.0",
                "id": start,
                "error": {"message": "failed", "willRetry": True},
            }
        )
        capture._observe({"jsonrpc": "2.0", "id": start, "result": {}})
        self.assertIsNone(capture.receive(timeout=0))
        self.assertFalse(capture.census.complete_accounting)

    def test_unrelated_notification_cannot_contaminate_thread_or_collaboration_census(
        self,
    ) -> None:
        capture, _, _ = self.observer()
        self.handshake(capture)
        capture._observe(
            {
                "jsonrpc": "2.0",
                "method": "thread/started",
                "params": {
                    "threadId": "foreign",
                    "turnId": "foreign-turn",
                    "item": {
                        "type": "collabAgentToolCall",
                        "senderThreadId": "foreign",
                        "receiverThreadIds": ["other"],
                    },
                },
            }
        )
        self.assertEqual(capture.census.thread_ids, set())
        self.assertEqual(capture.census.collaboration_edges, set())
        self.assertEqual(capture.census.turn_ids, set())
        self.assertTrue(capture.census.partial_census)

    def test_pumped_process_drains_stderr_preserves_raw_and_is_closed(self) -> None:
        process = FakeProcess()
        incoming = b'{"jsonrpc":"2.0","method":"context/compacted","params":{"threadId":"t","turnId":"u"}}\n'
        capture, _, root = self.observer(incoming, process=process)
        self.assertIsNotNone(capture.receive(timeout=1))
        capture.close()
        self.assertTrue(process.terminated)
        self.assertEqual((root / "in.raw").read_bytes(), incoming)
        self.assertEqual((root / "err.raw").read_bytes(), b"stderr\n")
        metadata = capture.artifact_metadata()
        self.assertEqual(metadata["schema_version"], "codex-app-server-capture-v1")
        self.assertFalse(metadata["complete_accounting"])
        self.assertFalse(metadata["host_semantics_verified"])

    def test_invalid_initialize_shape_repeated_handshake_and_unfrozen_metadata_are_rejected(
        self,
    ):
        capture, _, _ = self.observer()
        capture.initialize("benchmark", "1")
        capture._observe(
            {"id": 1, "result": {"serverInfo": {"name": "invalid-for-0.153.4"}}}
        )
        with self.assertRaisesRegex(RuntimeError, "must succeed"):
            capture.initialized()
        with self.assertRaisesRegex(RuntimeError, "already sent"):
            capture.initialize("benchmark", "1")
        with self.assertRaisesRegex(RuntimeError, "close and finish"):
            capture.artifact_metadata()

    def test_foreign_event_cannot_inject_child_through_known_sender(self):
        capture, _, _ = self.observer()
        self.handshake(capture)
        start = capture.request("thread/start", {"model": "gpt-5.6-terra"})
        capture._observe(
            {"id": start, "result": {"thread": {"id": "parent", "turns": []}}}
        )
        capture._observe(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "foreign",
                    "turnId": "foreign-turn",
                    "item": {
                        "type": "collabAgentToolCall",
                        "senderThreadId": "parent",
                        "receiverThreadIds": ["injected"],
                    },
                },
            }
        )
        self.assertEqual(capture.census.thread_ids, {"parent"})
        self.assertEqual(capture.census.turn_ids, set())

    def test_runtime_pin_rejects_any_identity_other_than_approved(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        binary = Path(temporary.name) / "codex.exe"
        binary.write_bytes(b"not the approved binary")
        with self.assertRaisesRegex(ValueError, "approved"):
            verify_runtime_pin(RuntimePin(binary.resolve(), sha256="0" * 64))

    def test_async_error_notification_marks_known_thread_partial(self):
        for will_retry in (True, False):
            with self.subTest(will_retry=will_retry):
                capture, _, _ = self.observer()
                self.handshake(capture)
                start = capture.request("thread/start", {"model": "gpt-5.6-terra"})
                capture._observe(
                    {"id": start, "result": {"thread": {"id": "parent", "turns": []}}}
                )
                self.assertFalse(capture.census.partial_census)
                capture._observe(
                    {
                        "method": "error",
                        "params": {
                            "threadId": "parent",
                            "turnId": "turn-1",
                            "willRetry": will_retry,
                            "error": {"message": "upstream stream disconnected"},
                        },
                    }
                )
                self.assertTrue(capture.census.partial_census)
                self.assertFalse(capture.census.complete_accounting)

    def test_double_shutdown_timeout_closes_idle_handles_and_allows_cleanup_retry(self):
        class HungProcess(FakeProcess):
            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

            def wait(self, timeout):
                raise subprocess.TimeoutExpired("app-server", timeout)

        process = HungProcess()
        capture, _, _ = self.observer(process=process)
        for reader in capture._readers:
            reader.join(timeout=2)
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            capture.close()
        self.assertTrue(process.terminated and process.killed)
        for stream in (
            capture._stdin,
            capture._stdout,
            capture._stderr,
            capture._out_file,
            capture._in_file,
            capture._err_file,
        ):
            self.assertTrue(stream.closed)
        self.assertTrue(capture.census.partial_census)
        with self.assertRaisesRegex(RuntimeError, "close and finish"):
            capture.artifact_metadata()
        process.running = False
        capture.close()
        self.assertTrue(capture.artifact_metadata()["cleanup_errors"])

    def test_start_failure_closes_process_streams_even_if_kill_wait_times_out(self):
        class FailedStartProcess(FakeProcess):
            def __init__(self):
                super().__init__()
                self.stdin, self.stdout, self.stderr = (
                    io.BytesIO(),
                    io.BytesIO(),
                    io.BytesIO(),
                )

            def wait(self, timeout):
                raise subprocess.TimeoutExpired("app-server", timeout)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            schema = root / "schema.json"
            schema.write_bytes(b"fixture schema")
            process = FailedStartProcess()
            with (
                patch(
                    "benchmark.app_server_capture.verify_runtime_pin",
                    return_value=root / "codex.exe",
                ),
                patch(
                    "benchmark.app_server_capture.PINNED_PROTOCOL_SHA256",
                    hashlib.sha256(schema.read_bytes()).hexdigest(),
                ),
                patch(
                    "benchmark.app_server_capture.subprocess.Popen",
                    return_value=process,
                ),
                patch.object(
                    CaptureFiles,
                    "open",
                    side_effect=FileExistsError("existing capture"),
                ),
            ):
                with self.assertRaises(subprocess.TimeoutExpired):
                    AppServerCapture.start(
                        CaptureFiles(root / "out", root / "in", root / "err"),
                        authorized_run_context={
                            "run_id": "fixture",
                            "workspace": str(workspace),
                            "model": "gpt-5.6-terra",
                            "effort": "medium",
                        },
                        runtime_pin=RuntimePin(root / "codex.exe"),
                        protocol_schema_version="0.153.4",
                        protocol_schema_path=schema,
                    )
            self.assertTrue(process.killed)
            self.assertTrue(
                all(
                    stream.closed
                    for stream in (process.stdin, process.stdout, process.stderr)
                )
            )

    def test_catalog_binds_exact_runtime_complete_catalog_and_supported_effort(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pin = RuntimePin(root / "codex.exe")
            catalog = {
                "version": pin.version,
                "executable": str(pin.binary),
                "executable_sha256": pin.sha256,
                "catalog_complete": True,
                "models": [
                    {
                        "model": "gpt-5.6-terra",
                        "supportedReasoningEfforts": [{"reasoningEffort": "medium"}],
                    }
                ],
            }
            path = root / "catalog.json"

            def retain(value):
                path.write_text(json.dumps(value), encoding="utf-8")
                return {
                    "path": str(path),
                    "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                }

            reference = retain(catalog)
            self.assertEqual(
                verify_model_catalog(reference, pin, "gpt-5.6-terra", "medium"),
                reference,
            )
            with self.assertRaisesRegex(ValueError, "not advertised"):
                verify_model_catalog(reference, pin, "gpt-5.6-terra", "max")
            for field, value in (
                ("version", "0.116.0"),
                ("executable_sha256", "0" * 64),
                ("catalog_complete", False),
            ):
                with (
                    self.subTest(field=field),
                    self.assertRaisesRegex(ValueError, "does not bind"),
                ):
                    verify_model_catalog(
                        retain({**catalog, field: value}),
                        pin,
                        "gpt-5.6-terra",
                        "medium",
                    )
            with self.assertRaisesRegex(ValueError, "changed"):
                verify_model_catalog(reference, pin, "gpt-5.6-terra", "medium")

    def test_mutating_caller_request_after_send_cannot_change_retained_metadata(self):
        capture, outgoing, _ = self.observer()
        self.handshake(capture)
        start = capture.request("thread/start", {"model": "gpt-5.6-terra"})
        response = {"id": start, "result": {"thread": {"id": "parent", "turns": []}}}
        capture._observe(response)
        inputs = []
        request = capture.request(
            "turn/start",
            {
                "threadId": "parent",
                "input": inputs,
                "model": "gpt-5.6-terra",
                "effort": "medium",
            },
        )
        inputs.append({"type": "text", "text": "not sent"})
        response["result"]["thread"]["id"] = "not received"
        wire = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        capture.close()
        metadata = capture.artifact_metadata()
        self.assertEqual(
            metadata["requests"][str(request)]["params"], wire[-1]["params"]
        )
        self.assertEqual(
            metadata["responses"][start]["result"]["thread"]["id"], "parent"
        )


if __name__ == "__main__":
    unittest.main()
