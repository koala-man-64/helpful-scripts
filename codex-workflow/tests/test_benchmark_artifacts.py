from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from benchmark.artifacts import emit_artifacts, load_verified_artifacts
from benchmark.fixtures import prepare_fixture_scratch
from benchmark.harness import evaluate_gate, evidence_receipt_payload, validate_evidence_payload
from test_benchmark import fixture_validators, prepared, receipt_for


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.run_set = prepared()
        self.receipts = {run.id: receipt_for(run, self.run_set, variant_cost=100 if run.variant == "baseline" else 80) for run in self.run_set.runs}
        self.validators = fixture_validators()

    def test_bare_metrics_without_complete_accounting_validator_cannot_promote(self):
        self.validators.pop("accounting")
        result = evaluate_gate(self.run_set, self.receipts, self.validators)
        self.assertFalse(result.eligible)
        self.assertTrue(any("complete root/child/attempt accounting" in reason for reason in result.reasons))

    def test_changed_exported_metrics_are_rejected_even_with_original_digest(self):
        payload = evidence_receipt_payload(self.run_set, self.receipts, self.validators)
        payload["runs"][0]["metrics"]["cost_usd"] = 0
        self.assertTrue(validate_evidence_payload(payload, self.run_set, self.receipts, self.validators))

    def test_loader_reconstructs_schedule_and_recomputes_after_byte_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "evidence"
            # This is a synthetic artifact plumbing check, not 72 model runs.
            path = emit_artifacts(root, prepared=self.run_set, receipts=self.receipts, validators=self.validators, observations={"schema_version": "fixture-only"})
            loaded = load_verified_artifacts(path, validators=self.validators)
            self.assertEqual(len(loaded.run_set["runs"]), 72)
            metadata = json.loads(path.read_bytes())
            reference = metadata["evidence_artifacts"]["run_set"]
            run_path = root / reference["path"]
            payload = json.loads(run_path.read_bytes())
            payload["runs"][1] = payload["runs"][0]
            changed = json.dumps(payload).encode()
            run_path.write_bytes(changed)
            # Even a matching updated file checksum does not prove exact72.
            reference["digest"] = "sha256:" + hashlib.sha256(changed).hexdigest()
            path.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, "reconstructed pinned schedule"):
                load_verified_artifacts(path, validators=self.validators)

    def test_scratch_materialization_contains_real_source_and_tests(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixtures"
            index = json.loads(prepare_fixture_scratch(root).read_bytes())
            self.assertEqual(len(index["fixtures"]), 12)
            self.assertTrue((root / "localized-failed-test/src/allocation.py").is_file())
            self.assertTrue((root / "localized-failed-test/tests/test_allocation.py").is_file())
            self.assertTrue(next(row for row in index["fixtures"] if row["task_id"] == "research-compaction-retention")["host_event_requirement"])


if __name__ == "__main__":
    unittest.main()
