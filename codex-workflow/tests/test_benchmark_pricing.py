from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmark.pricing import derive_request_prices  # noqa: E402
from benchmark.semantic_evidence import file_digest  # noqa: E402


@unittest.skipUnless(
    os.environ.get("CODEX_WORKFLOW_HOOKS_SOURCE"),
    "requires explicit owning hook source",
)
class PricingDerivationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.hooks = Path(os.environ["CODEX_WORKFLOW_HOOKS_SOURCE"])
        self.hook_digest = file_digest(self.hooks / "src/codex_workflow_hooks/usage.py")
        self.usage = self.root / "usage.json"
        self.contexts = self.root / "contexts.json"
        self.row = {
            "source_id": "a" * 64,
            "event_id": "b" * 64,
            "source_event_index": 1,
            "task_id": "c" * 64,
            "parent_task_id": None,
            "turn_id": None,
            "provider_id": None,
            "request_id": None,
            "segment_id": "d" * 64,
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
            "kind": "request",
            "attribution": "root",
            "observed_at": "2026-09-05T07:02:48Z",
            "input_tokens": 100000,
            "cached_input_tokens": 50000,
            "output_tokens": 10000,
            "reasoning_tokens": 5000,
            "cache_write_tokens": None,
            "cache_write_semantics": "unknown",
            "estimated_cost_usd": None,
            "rate_card_id": None,
            "cost_basis": None,
            "baseline_complete": True,
            "reset": False,
        }
        self.context = {
            "observed_product": "codex",
            "observed_scope": "request",
            "observed_model": "gpt-5.6-sol",
            "observed_mode": "standard",
            "observed_regional": False,
            "observed_request_input_tokens": 100000,
        }

    def derive(self, *, conflicting_replay=False, identical_replay=False):
        rows = [self.row]
        if conflicting_replay:
            rows.append({**self.row, "input_tokens": self.row["input_tokens"] + 1})
        if identical_replay:
            rows.append(dict(self.row))
        self.usage.write_text(
            json.dumps({"schema_version": 1, "observations": rows}), encoding="utf-8"
        )
        self.contexts.write_text(
            json.dumps(
                {
                    "schema_version": "codex-request-pricing-contexts-v1",
                    "contexts": [
                        {
                            "source_id": self.row["source_id"],
                            "event_id": self.row["event_id"],
                            "context": self.context,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        original = self.usage.read_bytes()
        result = derive_request_prices(
            self.usage,
            self.contexts,
            hook_source=self.hooks,
            expected_hook_usage_digest=self.hook_digest,
        )
        self.assertEqual(self.usage.read_bytes(), original)
        return result

    def test_actual_hook_parser_and_subset_pricing_keep_original_rows_unmodified(self):
        result = self.derive()
        estimate = result["estimates"][0]["estimate"]
        self.assertAlmostEqual(estimate["estimated_cost_usd"], 0.42)
        self.assertEqual(
            estimate["cost_basis"], "published_codex_equivalent_not_api_billing"
        )
        self.assertIsNone(result["estimated_task_cost_usd"])
        self.assertFalse(result["complete_accounting"])
        self.assertFalse(result["promotion_eligible"])
        self.assertEqual(result["context_verification"], "unverified")

    def test_unknown_mode_or_conflicting_model_remains_unpriced(self):
        self.context["observed_mode"] = None
        self.assertIsNone(
            self.derive()["estimates"][0]["estimate"]["estimated_cost_usd"]
        )
        self.context["observed_mode"] = "standard"
        self.context["observed_model"] = "gpt-6-astra"
        self.assertIsNone(
            self.derive()["estimates"][0]["estimate"]["estimated_cost_usd"]
        )

    def test_owning_parser_rejects_conflicting_replay(self):
        with self.assertRaisesRegex(ValueError, "conflicting usage replay"):
            self.derive(conflicting_replay=True)

    def test_identical_replay_retains_one_derivation_without_rewriting_input(self):
        result = self.derive(identical_replay=True)
        self.assertEqual(len(result["estimates"]), 1)
        self.assertAlmostEqual(result["estimates"][0]["estimate"]["estimated_cost_usd"], 0.42)
        self.assertEqual(len(json.loads(self.usage.read_bytes())["observations"]), 2)
