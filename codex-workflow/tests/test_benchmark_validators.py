from __future__ import annotations

import sys
import unittest
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark.validators import capability_report, load_hook_usage, validate_accounting  # noqa: E402


class ValidatorTests(unittest.TestCase):
    def test_host_only_scenarios_are_explicitly_unsupported(self) -> None:
        for task in ("research-compaction-retention", "research-resumable-wait", "cross-urgent-peer-negation", "cross-child-review-recovery"):
            self.assertEqual(capability_report(task)["status"], "unsupported")

    def test_accounting_fails_without_hook_owned_refs(self) -> None:
        class Receipt:
            task_id = "a" * 64
            metrics = {"cost_usd": 0, "uncached_input_tokens": 0}

        self.assertFalse(validate_accounting(Receipt(), {}, object()))

    def test_checked_hook_source_exposes_usage_api(self) -> None:
        value = os.environ.get("CODEX_WORKFLOW_HOOKS_SOURCE")
        if not value:
            self.skipTest("CODEX_WORKFLOW_HOOKS_SOURCE is not configured")
        source = Path(value)
        usage = load_hook_usage(source)
        self.assertTrue(callable(usage.validate_export))
        self.assertTrue(callable(usage.summarize_observations))


if __name__ == "__main__":
    unittest.main()
