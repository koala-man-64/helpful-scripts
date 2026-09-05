"""Synthetic CLI transcripts exercise real artifact evaluation; no model runs."""
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark.harness import evaluate_acceptance, identity_hash, prepare_run_set  # noqa: E402
from benchmark.artifacts import stage_receipt  # noqa: E402
from benchmark.runner import collect_receipt, dispatch_observed, reference, write_new  # noqa: E402
from benchmark.semantic_evidence import command_argv, read_cli_trace  # noqa: E402
from benchmark.semantic_validation import build_semantic_validators, semantic_preparation_pins, semantic_proofs, validator_digest  # noqa: E402
from test_benchmark import PINS  # noqa: E402

INPUTS = ROOT / "benchmark/task_inputs"


class SemanticIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace, self.raw = self.root / "workspace", self.root / "raw"
        self.task = "research-external-fixture"
        shutil.copytree(INPUTS / self.task, self.workspace)
        self.fixture = json.loads((self.workspace / "comparison.json").read_bytes())
        fixed = json.loads((INPUTS / "fixed-inputs.json").read_bytes())
        pins = replace(PINS, skill_pins={**PINS.skill_pins, **semantic_preparation_pins(INPUTS)["skill_pins"]},
                       external_fixtures={self.task: fixed[self.task]["input_digest"]})
        self.prepared = prepare_run_set(pins=pins, execution_mode="cold", seed=7301)
        self.run = next(run for run in self.prepared.runs if run.task_id == self.task)
        self.answer = {
            "fixture_digest": reference(self.workspace / "comparison.json")["digest"],
            "evidence": {"A": {"latency": 80, "capacity": 70}, "B": {"latency": 120, "capacity": 100}},
            "assessment": {"A": {"latency_ok": True, "capacity_ok": True},
                           "B": {"latency_ok": False, "capacity_ok": True}},
            "recommendation": "A", "inference": "Only A satisfies both recorded limits.",
            "limitations": ["snapshot_only"],
        }

    def collect(self, *, answer=None, extra_item=None):
        events = [
            {"type": "thread.started", "thread_id": "synthetic-session"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {
                "id": "read-1", "type": "command_execution", "command": "Get-Content comparison.json",
                "exit_code": 0, "status": "completed", "aggregated_output": json.dumps(self.fixture),
            }},
        ]
        if extra_item:
            events.append({"type": "item.completed", "item": extra_item})
        events.append({"type": "turn.completed"})
        answer = self.answer if answer is None else answer

        class FixtureAdapter:
            def dispatch(inner, run, prepared, **kwargs):
                raw_dir = kwargs["raw_dir"]
                path = raw_dir / "events.jsonl"
                path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
                (raw_dir / "last-message-fixture.txt").write_text(json.dumps(answer), encoding="utf-8")
                return {"schema_version": "codex-exec-dispatch-v1", "adapter": "codex-cli-exec-v1",
                        "run_request": asdict(run), "run_set_digest": prepared.run_set_digest,
                        "raw_artifact_refs": {"events": reference(path)}}

        dispatch_observed(FixtureAdapter(), self.run, self.prepared, workspace=self.workspace,
                          raw_dir=self.raw, repository_id="synthetic-only",
                          observed_skill_pins=self.prepared.pins.skill_pins)
        write_new(self.raw / "measurements.json", {
            "schema_version": "benchmark-measurements-v1", "run_id": self.run.id,
            "task_ids": [identity_hash("task", "synthetic-session")],
        })
        write_new(self.raw / "usage.json", {"schema_version": 1, "observations": []})
        evidence = {"raw_artifacts": {
            "dispatch": str(self.raw / "dispatch.json"), "events": str(self.raw / "events.jsonl"),
            "measurements": str(self.raw / "measurements.json"), "usage_observations": str(self.raw / "usage.json"),
        }}
        return collect_receipt(self.prepared, self.run.id, evidence)

    def test_production_registry_recomputes_actual_answers_and_retains_null_accounting(self):
        receipt = self.collect()
        receipt = replace(receipt, invariant_evidence=semantic_proofs(self.task, receipt, INPUTS))
        registry = build_semantic_validators(INPUTS, expected_validator_digest=validator_digest())
        self.assertTrue(evaluate_acceptance(receipt, self.prepared, registry).accepted)
        self.assertNotIn("accounting", registry)
        self.assertIsNone(receipt.metrics["cost_usd"])
        self.assertIsNone(receipt.metrics["uncached_input_tokens"])

    def test_model_pass_flags_cannot_override_wrong_recommendation(self):
        bad = copy.deepcopy(self.answer)
        bad["recommendation"] = "B"
        receipt = self.collect(answer=bad)
        proofs = semantic_proofs(self.task, receipt, INPUTS)
        self.assertEqual(proofs["recommendation_traceable"]["outcome"], "rejected")
        for proof in proofs.values():
            proof["outcome"] = "satisfied"
        receipt = replace(receipt, invariant_evidence=proofs)
        registry = build_semantic_validators(INPUTS, expected_validator_digest=validator_digest())
        self.assertFalse(evaluate_acceptance(receipt, self.prepared, registry).accepted)

    def test_staged_proofs_keep_bytes_and_all_verifier_references_inside_artifact_root(self):
        receipt = self.collect()
        receipt = replace(receipt, invariant_evidence=semantic_proofs(self.task, receipt, INPUTS))
        output = self.root / "artifacts"
        output.mkdir()
        staged = stage_receipt(output, receipt)
        registry = build_semantic_validators(INPUTS, expected_validator_digest=validator_digest())
        self.assertTrue(evaluate_acceptance(staged, self.prepared, registry).accepted)
        self.assertEqual(staged.raw_artifact_digests, receipt.raw_artifact_digests)
        for retained_ref in staged.raw_artifact_refs.values():
            self.assertTrue(Path(retained_ref["path"]).is_relative_to(output))
        for proof in staged.invariant_evidence.values():
            path = Path(proof["artifact_ref"]["path"])
            self.assertTrue(path.is_relative_to(output))
            check = json.loads(path.read_bytes())
            for retained_ref in [check["validator_ref"], *check["evidence_refs"]]:
                self.assertTrue(Path(retained_ref["path"]).is_relative_to(output))
                self.assertEqual(retained_ref["digest"], reference(Path(retained_ref["path"]))["digest"])

    def test_unrecognized_external_tool_remains_in_the_trace_and_rejects_no_browse_claim(self):
        receipt = self.collect(extra_item={"id": "web-1", "type": "web_search"})
        proofs = semantic_proofs(self.task, receipt, INPUTS)
        self.assertEqual(proofs["no_live_external_read"]["outcome"], "rejected")

    def test_changed_workspace_and_wrong_validator_pin_are_rejected(self):
        receipt = self.collect()
        (self.workspace / "comparison.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "produced workspace"):
            semantic_proofs(self.task, receipt, INPUTS)
        with self.assertRaisesRegex(ValueError, "immutable preparation pin"):
            build_semantic_validators(INPUTS, expected_validator_digest="sha256:" + "0" * 64)

    def test_trace_rejects_mixed_sessions_incomplete_items_and_opaque_shell_commands(self):
        self.assertEqual(command_argv('pwsh -Command "git status"'), ["git", "status"])
        with self.assertRaises(ValueError):
            command_argv('pwsh -Command "git status; python evil.py"')
        path = self.root / "incomplete.jsonl"
        records = [{"type": "thread.started", "thread_id": "one"},
                   {"type": "item.started", "item": {"id": "unfinished"}},
                   {"type": "turn.completed"}]
        path.write_text("\n".join(json.dumps(row) for row in records), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            read_cli_trace(path, "one")
        with self.assertRaisesRegex(ValueError, "mixed"):
            read_cli_trace(path, "different")


if __name__ == "__main__":
    unittest.main()
