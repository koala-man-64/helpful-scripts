from __future__ import annotations

import sys
import unittest
import json
import hashlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark.harness import (  # noqa: E402
    CodexExecAdapter,
    FrozenExecutionConfig,
    PreparationPins,
    Receipt,
    evaluate_acceptance,
    evaluate_gate,
    evidence_receipt_payload,
    prepare_run_set,
    receipt_digest,
    identity_hash,
    run_identity,
    validate_receipt,
)
from benchmark.manifest import FIXED_MANIFEST, manifest_digest, task_identity  # noqa: E402


PINS = PreparationPins(
    base_commits={"owner/repo-a": "a" * 40, "owner/repo-b": "b" * 40},
    dependency_locks={"owner/repo-a/requirements.lock": "sha256:" + "1" * 64},
    skill_pins={"workflow-router": "sha256:" + "2" * 64},
    external_fixtures={"research-snapshot": "sha256:" + "3" * 64},
    variant_configs={"baseline": FrozenExecutionConfig("gpt-6-astra", "high"), "candidate": FrozenExecutionConfig("candidate-model", "medium")},
)


def fixture_validators():
    """Synthetic test doubles only; no model execution or billing evidence."""
    return {"accounting": lambda *_: True, **{name: lambda *_: True for task in FIXED_MANIFEST for name in task.acceptance_invariants}}


def prepared(mode: str = "cold"):
    return prepare_run_set(pins=PINS, execution_mode=mode, seed=7301)


def receipt_for(run, run_set, *, variant_cost: float = 80.0) -> Receipt:
    task = next(task for task in FIXED_MANIFEST if task.id == run.task_id)
    return Receipt(
        run_id=run.id,
        run_identity=run_identity(run),
        manifest_task_id=task_identity(task),
        task_id=identity_hash("task", f"session-{run.id}"),
        parent_task_id=None,
        manifest_digest=run_set.manifest_digest,
        run_set_digest=run_set.run_set_digest,
        prompt_digest=run.prompt_digest,
        adapter=CodexExecAdapter.name,
        adapter_session_id=f"session-{run.id}",
        raw_artifact_digests={"events": "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        raw_artifact_refs={"events": {"path": str(Path(__file__).resolve()), "digest": "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}},
        completion_status="completed",
        invariant_evidence={
            invariant: {
                "artifact_digest": "sha256:" + "a" * 64,
                "validator": invariant,
                "manifest_task_id": task_identity(task),
                "task_id": identity_hash("task", f"session-{run.id}"),
                "observation_ids": ["d" * 64],
                "outcome": "satisfied",
            }
            for invariant in task.acceptance_invariants
        },
        metrics={
            "cost_usd": variant_cost,
            "uncached_input_tokens": variant_cost,
            "rework_count": variant_cost / 80,
            "avoidable_interventions": variant_cost / 80,
            "active_seconds": variant_cost,
            "lost_continuations": 0,
            "duplicate_continuations": 0,
            "load_bearing_retention": 1,
        },
        child_task_ids=(identity_hash("task", f"child-{run.id}"),) if "children" in task.scenarios else (),
        failure_artifact_digests=("sha256:" + "f" * 64,) if "failure" in task.scenarios else (),
    )


class BenchmarkManifestTests(unittest.TestCase):
    def test_fixed_manifest_has_exactly_twelve_covered_tasks(self) -> None:
        self.assertEqual(len(FIXED_MANIFEST), 12)
        self.assertEqual(
            {task.category for task in FIXED_MANIFEST},
            {"localized", "multi_file_cross_repo", "research_planning"},
        )
        self.assertEqual(
            {task.category for task in FIXED_MANIFEST if task.category == "localized"},
            {"localized"},
        )
        self.assertEqual(sum(task.category == "localized" for task in FIXED_MANIFEST), 4)
        self.assertEqual(sum(task.category == "multi_file_cross_repo" for task in FIXED_MANIFEST), 4)
        self.assertEqual(sum(task.category == "research_planning" for task in FIXED_MANIFEST), 4)
        scenarios = {scenario for task in FIXED_MANIFEST for scenario in task.scenarios}
        self.assertTrue(
            {
                "long_output", "missing_dependency", "failed_test", "protected_human_gate",
                "wrong_checkout", "stale_sha", "compaction", "urgent_peer_negation", "resumable_wait",
                "children", "reviews", "recovery", "clarification",
            }.issubset(scenarios)
        )
        self.assertTrue(manifest_digest().startswith("sha256:"))

    def test_evidence_schema_is_fixed_and_has_no_arbitrary_acceptance_field(self) -> None:
        schema = json.loads((ROOT / "benchmark" / "evidence-receipt-v1.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], "benchmark-evidence-receipt-v1")
        self.assertNotIn("accepted", schema["$defs"]["run"]["properties"])
        self.assertEqual(schema["properties"]["runs"]["minItems"], 72)

    def test_each_mode_gets_a_deterministic_72_run_matched_schedule(self) -> None:
        first = prepared("cold")
        second = prepared("cold")
        warm = prepared("warm")
        self.assertEqual(first.runs, second.runs)
        self.assertEqual(len(first.runs), 72)
        self.assertEqual(len(warm.runs), 72)
        self.assertNotEqual(first.run_set_digest, warm.run_set_digest)
        pairs = {}
        for run in first.runs:
            pairs.setdefault(run.pair_id, []).append(run.variant)
        self.assertEqual(len(pairs), 36)
        self.assertTrue(all(sorted(variants) == ["baseline", "candidate"] for variants in pairs.values()))

    def test_preparation_fails_without_every_immutable_pin_class(self) -> None:
        bad = replace(PINS, external_fixtures={})
        with self.assertRaisesRegex(ValueError, "external_fixtures"):
            prepare_run_set(pins=bad, execution_mode="cold", seed=1)


class BenchmarkReceiptAndGateTests(unittest.TestCase):
    def test_receipts_require_fixed_provenance_and_named_evidence(self) -> None:
        run_set = prepared()
        receipt = receipt_for(run_set.runs[0], run_set)
        self.assertEqual(validate_receipt(receipt, run_set), [])
        malformed = replace(receipt, run_set_digest="sha256:wrong", adapter="ad-hoc")
        self.assertTrue(validate_receipt(malformed, run_set))
        unavailable = replace(receipt, raw_artifact_refs={"events": {"path": "C:/missing-benchmark-evidence.log", "digest": receipt.raw_artifact_digests["events"]}})
        self.assertTrue(any("unavailable" in error for error in validate_receipt(unavailable, run_set)))
        with self.assertRaisesRegex(ValueError, "arbitrary acceptance boolean"):
            Receipt.from_mapping({**receipt.__dict__, "accepted": True})

    def test_acceptance_uses_trusted_named_validators_not_a_pass_boolean(self) -> None:
        run_set = prepared()
        receipt = receipt_for(run_set.runs[0], run_set)
        accepted = evaluate_acceptance(
            receipt,
            run_set,
            {name: lambda *_: True for name in receipt.invariant_evidence},
        )
        self.assertTrue(accepted.accepted)
        self.assertEqual(accepted.receipt_digest, receipt_digest(receipt))
        rejected = evaluate_acceptance(receipt, run_set, {})
        self.assertFalse(rejected.accepted)
        self.assertTrue(any("no trusted validator" in reason for reason in rejected.reasons))

    def test_predeclared_gate_accepts_only_complete_better_matched_accounting(self) -> None:
        run_set = prepared()
        receipts = {
            run.id: receipt_for(run, run_set, variant_cost=100.0 if run.variant == "baseline" else 80.0)
            for run in run_set.runs
        }
        validators = fixture_validators()
        decision = evaluate_gate(run_set, receipts, validators)
        self.assertTrue(decision.eligible, decision.reasons)
        self.assertLessEqual(decision.ratios["cost_usd_aggregate_per_accepted"], 0.90)
        evidence = evidence_receipt_payload(run_set, receipts, validators)
        self.assertEqual(evidence["schema_version"], "benchmark-evidence-receipt-v1")
        self.assertEqual(len(evidence["runs"]), 72)
        self.assertNotIn("accepted", evidence["runs"][0])
        null_metrics = dict(receipts[run_set.runs[0].id].metrics)
        null_metrics["cost_usd"] = None
        receipts[run_set.runs[0].id] = replace(receipts[run_set.runs[0].id], metrics=null_metrics)
        self.assertFalse(evaluate_gate(run_set, receipts, validators).eligible)

    def test_gate_rejects_missing_run_unknown_accounting_bad_defect_and_failed_acceptance(self) -> None:
        run_set = prepared()
        receipts = {
            run.id: receipt_for(run, run_set, variant_cost=100.0 if run.variant == "baseline" else 80.0)
            for run in run_set.runs
        }
        validators = fixture_validators()
        incomplete = dict(receipts)
        incomplete.pop(run_set.runs[0].id)
        self.assertFalse(evaluate_gate(run_set, incomplete, validators).eligible)
        candidate = next(run for run in run_set.runs if run.variant == "candidate")
        receipts[candidate.id] = replace(receipts[candidate.id], defects=({"severity": "critical", "new": True, "evidence_digest": "sha256:" + "c" * 64},))
        self.assertFalse(evaluate_gate(run_set, receipts, validators).eligible)
        receipts[candidate.id] = replace(receipts[candidate.id], defects=({"severity": "critical", "new": "true", "evidence_digest": "sha256:" + "c" * 64},))
        self.assertFalse(evaluate_gate(run_set, receipts, validators).eligible)
        self.assertFalse(evaluate_gate(run_set, receipts, {name: lambda *_: False for name in validators}).eligible)
        unknown = dict(receipts[candidate.id].metrics)
        unknown["active_seconds"] = float("nan")
        receipts[candidate.id] = replace(receipts[candidate.id], metrics=unknown)
        self.assertFalse(evaluate_gate(run_set, receipts, validators).eligible)
        child = next(run for run in run_set.runs if "children" in next(task for task in FIXED_MANIFEST if task.id == run.task_id).scenarios)
        receipts[child.id] = replace(receipts[child.id], child_task_ids=())
        self.assertFalse(evaluate_gate(run_set, receipts, validators).eligible)

    def test_gate_handles_complete_zero_baselines_and_paired_skew(self) -> None:
        run_set = prepared()
        receipts = {run.id: receipt_for(run, run_set, variant_cost=0.0) for run in run_set.runs}
        validators = fixture_validators()
        self.assertTrue(evaluate_gate(run_set, receipts, validators).eligible)
        skewed = dict(receipts)
        for pair in sorted({run.pair_id for run in run_set.runs})[:5]:
            baseline = next(run for run in run_set.runs if run.pair_id == pair and run.variant == "baseline")
            candidate = next(run for run in run_set.runs if run.pair_id == pair and run.variant == "candidate")
            skewed[baseline.id] = replace(skewed[baseline.id], metrics={**skewed[baseline.id].metrics, "cost_usd": 1.0})
            skewed[candidate.id] = replace(skewed[candidate.id], metrics={**skewed[candidate.id].metrics, "cost_usd": 100.0})
        decision = evaluate_gate(run_set, skewed, validators)
        self.assertGreater(decision.ratios["cost_usd_paired_p90"], 1.10)
        self.assertFalse(decision.eligible)

    def test_adapter_requires_explicit_model_and_only_builds_supported_cli_command(self) -> None:
        run_set = prepared()
        adapter = CodexExecAdapter()
        command = adapter.command(
            run_set.runs[0], run_set, workspace=Path("C:/work"), last_message=Path("C:/raw/last.txt")
        )
        self.assertEqual(command[:3], ["codex", "exec", "--json"])
        self.assertIn("--model", command)
        self.assertEqual(command[-1], "-")
        self.assertEqual(adapter.process_runner.__module__, "tools.output_projection")
        self.assertNotIn("--full-auto", command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)

    def test_launch_preflight_rejects_stale_head_before_process_start(self) -> None:
        from benchmark.harness import validate_launch
        with patch("benchmark.harness.capture_git_state", return_value={
            "head": "b" * 40, "branch": "task", "status": "", "git_locks": [], "pending_git_operations": [],
        }):
            with self.assertRaisesRegex(ValueError, "HEAD"):
                validate_launch(prepared(), workspace=Path("C:/work"), repository_id="owner/repo-a", observed_skill_pins=PINS.skill_pins)

    def test_dispatch_rejects_changed_request_and_revalidates_after_adapter_probe(self) -> None:
        run_set = prepared()
        run = run_set.runs[0]
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return {"exit_status": 0, "raw_file": {"path": "events.jsonl", "hash": "sha256:" + "a" * 64}}

        adapter = CodexExecAdapter(process_runner=runner)
        arguments = {"workspace": Path("workspace"), "raw_dir": Path("raw"), "repository_id": "owner/repo-a", "observed_skill_pins": PINS.skill_pins}
        with self.assertRaisesRegex(ValueError, "immutable prepared set"):
            adapter.dispatch(replace(run, prompt="changed prompt"), run_set, **arguments)
        self.assertEqual(calls, [])
        with patch("benchmark.harness.validate_launch", side_effect=ValueError("workspace changed")):
            with self.assertRaisesRegex(ValueError, "workspace changed"):
                adapter.dispatch(run, run_set, **arguments)
        self.assertEqual([call[0] for call in calls], [["codex", "exec", "--help"]])
        calls.clear()
        with patch("benchmark.harness.validate_launch"):
            adapter.dispatch(run, run_set, **arguments)
            adapter.dispatch(run, run_set, **arguments)
        commands = [command for command, _ in calls if "--output-last-message" in command]
        output_paths = [command[command.index("--output-last-message") + 1] for command in commands]
        self.assertEqual(len(set(output_paths)), 2)
        for command, kwargs in calls:
            if "--output-last-message" in command:
                self.assertEqual(kwargs["input_bytes"], run.prompt.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
