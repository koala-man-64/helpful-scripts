from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from benchmark.manifest import FIXED_MANIFEST

INPUTS = Path(__file__).parents[1] / "benchmark" / "task_inputs"


class FixedInputTests(unittest.TestCase):
    def test_all_twelve_inputs_have_real_hash_pinned_files(self):
        data = json.loads((INPUTS / "fixed-inputs.json").read_text())
        self.assertEqual(set(data), {task.id for task in FIXED_MANIFEST})
        for task_id, fixture in data.items():
            self.assertTrue(fixture["files"])
            for relative, digest in fixture["files"].items():
                path = INPUTS / task_id / relative
                self.assertEqual("sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        for name in ("cross-protected-human-gate", "research-compaction-retention", "research-resumable-wait"):
            self.assertEqual(data[name]["validator"], "requires_actual_adapter_evidence")

    def test_real_baseline_fails_and_corrected_allocation_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary) / "allocation"
            shutil.copytree(INPUTS / "localized-failed-test", scratch)
            command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
            baseline = subprocess.run(command, cwd=scratch, capture_output=True, check=False)
            self.assertEqual(baseline.returncode, 1)
            self.assertIn(b"test_remainder_preserves_total", baseline.stderr)
            # Independent fixture acceptance check; this is not a model run.
            (scratch / "src" / "allocation.py").write_text("def allocate(total, weights):\n    values = [total * weight // sum(weights) for weight in weights]\n    for i in range(total - sum(values)):\n        values[i % len(values)] += 1\n    return values\n")
            corrected = subprocess.run(command, cwd=scratch, capture_output=True, check=False)
            self.assertEqual(corrected.returncode, 0, corrected.stderr.decode())

    def test_exact_long_line_fixture_contains_hidden_failure(self):
        spec = importlib.util.spec_from_file_location("fixture_log", INPUTS / "localized-long-output" / "generate_log.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        text = module.log_bytes().decode("utf-8")
        self.assertEqual(len(text), 153000)
        self.assertIn("ERROR BUILD417 src/route.py:1", text)
        self.assertNotIn("\n", text)


if __name__ == "__main__":
    unittest.main()
