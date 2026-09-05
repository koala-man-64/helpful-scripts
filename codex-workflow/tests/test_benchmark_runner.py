"""Synthetic event fixtures test collection; no model or billing claim is made."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark.harness import CodexExecAdapter, identity_hash  # noqa: E402
from benchmark.runner import collect_receipt, execution_identity, reference, restore_prepared, study_capabilities, write_new  # noqa: E402
from test_benchmark import prepared  # noqa: E402


class RunnerTests(unittest.TestCase):
    def test_schedule_tampering_and_silent_overwrites_are_rejected(self):
        run_set = prepared()
        value = run_set.payload() | {"run_set_digest": run_set.run_set_digest}
        self.assertEqual(restore_prepared(value), run_set)
        bad = copy.deepcopy(value)
        bad["runs"][0]["prompt"] = "Changed"
        with self.assertRaisesRegex(ValueError, "reconstructed"):
            restore_prepared(bad)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            write_new(path, {})
            with self.assertRaises(FileExistsError):
                write_new(path, {"overwritten": True})

    def test_receipt_joins_actual_session_and_retains_unknowns_and_failures(self):
        run_set = prepared()
        run = run_set.runs[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events = root / "events.jsonl"
            events.write_text(json.dumps({"type": "thread.started", "thread_id": "actual-session"}) + "\n" + json.dumps({"type": "turn.failed"}), encoding="utf-8")
            task_id = identity_hash("task", "actual-session")
            measures = root / "measurements.json"
            measures.write_text(json.dumps({"schema_version": "benchmark-measurements-v1", "run_id": run.id, "task_ids": [task_id]}), encoding="utf-8")
            usage = root / "usage.json"
            usage.write_text(json.dumps({"schema_version": 1, "observations": []}), encoding="utf-8")
            dispatch = root / "dispatch.json"
            record = {"schema_version": "codex-exec-dispatch-v1", "adapter": CodexExecAdapter.name,
                      "run_request": asdict(run), "run_set_digest": run_set.run_set_digest,
                      "raw_artifact_refs": {"events": reference(events)}}
            dispatch.write_text(json.dumps(record), encoding="utf-8")
            evidence = {"raw_artifacts": {"dispatch": str(dispatch), "events": str(events),
                                          "measurements": str(measures), "usage_observations": str(usage)},
                        "failure_artifacts": ["events"]}
            receipt = collect_receipt(run_set, run.id, evidence)
            self.assertEqual(receipt.task_id, task_id)
            self.assertEqual(receipt.completion_status, "failed")
            self.assertIsNone(receipt.metrics["cost_usd"])
            self.assertEqual(receipt.invariant_evidence, {})
            self.assertEqual(receipt.failure_artifact_digests, (reference(events)["digest"],))
            events.write_text(events.read_text() + "\nchanged", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "dispatch artifact"):
                collect_receipt(run_set, run.id, evidence)

    def test_multiple_sessions_cannot_masquerade_as_one_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            path.write_text("\n".join(json.dumps({"type": "thread.started", "thread_id": value}) for value in ("one", "two")), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                execution_identity(path)

    def test_capability_report_does_not_call_format_validation_execution_readiness(self):
        report = study_capabilities()
        self.assertFalse(report["dispatch_ready"])
        self.assertEqual(len(report["tasks"]), 12)
        self.assertTrue(report["blocked_requirements"])


if __name__ == "__main__":
    unittest.main()
