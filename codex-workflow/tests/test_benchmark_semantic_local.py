from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "benchmark" / "task_inputs"
sys.path.insert(0, str(ROOT))
from benchmark.semantic_local import evaluate_local  # noqa: E402


TEST = ["python", "-B", "-m", "unittest", "discover", "-s", "tests"]
LOG = ["python", "-B", "generate_log.py"]


def record(argv: list[str], exit_code: int = 0) -> dict[str, object]:
    return {"type": "command_execution", "argv": argv, "exit_code": exit_code, "status": "completed", "output": "fixture"}


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class LocalSemanticTests(unittest.TestCase):
    def copies(self, task: str) -> tuple[Path, Path, tempfile.TemporaryDirectory[str]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base, work = Path(temporary.name) / "base", Path(temporary.name) / "work"
        shutil.copytree(INPUTS / task, base)
        shutil.copytree(INPUTS / task, work)
        return base, work, temporary

    def fix_failed_test(self, work: Path, *, regression: bool = True) -> None:
        (work / "src" / "allocation.py").write_text(
            "def allocate(total, weights):\n"
            "    values = [total * weight // sum(weights) for weight in weights]\n"
            "    for index in range(total - sum(values)):\n"
            "        values[index] += 1\n"
            "    return values\n",
            encoding="utf-8",
        )
        if regression:
            test = work / "tests" / "test_allocation.py"
            test.write_text(
                test.read_text(encoding="utf-8")
                + "\n\nclass RegressionCoverage(unittest.TestCase):\n"
                "    def test_single_remainder_is_stable(self):\n"
                "        self.assertEqual(allocate(1, [1, 1]), [1, 0])\n",
                encoding="utf-8",
            )

    def raw_log(self, base: Path) -> dict[str, str]:
        result = subprocess.run(
            [sys.executable, "-B", "generate_log.py"], cwd=base, capture_output=True,
            text=True, check=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0)
        path = base / "logs" / "build.log"
        return {"path": str(path), "digest": digest(path)}

    def test_positive_all_three_real_fixture_checks(self) -> None:
        base, work, _ = self.copies("localized-failed-test")
        self.fix_failed_test(work)
        failed = evaluate_local(
            "localized-failed-test", baseline=base, workspace=work, final_text="fixed",
            commands=[record(TEST, 1), record(TEST)], raw_refs={},
        )
        self.assertTrue(all(failed.values()), failed)

        base, work, _ = self.copies("localized-long-output")
        (work / "src" / "route.py").write_text("ROUTES = {'health': '/health', 'ready': '/ready'}\n", encoding="utf-8")
        long = evaluate_local(
            "localized-long-output", baseline=base, workspace=work, final_text="First actionable failure: BUILD417.",
            commands=[record(LOG), record(TEST)], raw_refs={"build_log": self.raw_log(base)},
        )
        self.assertTrue(all(long.values()), long)

        base, work, _ = self.copies("localized-missing-dependency")
        missing = evaluate_local(
            "localized-missing-dependency", baseline=base, workspace=work, final_text="Used the supported stdlib path; no install.",
            commands=[record(TEST)], raw_refs={},
        )
        self.assertTrue(all(missing.values()), missing)

    def test_wrong_fix_deleted_tests_and_comment_only_regression_are_rejected(self) -> None:
        base, work, _ = self.copies("localized-failed-test")
        self.fix_failed_test(work, regression=False)
        wrong = evaluate_local("localized-failed-test", baseline=base, workspace=work, final_text="fixed", commands=[record(TEST, 1), record(TEST)], raw_refs={})
        self.assertFalse(wrong["regression_test"])

        base, work, _ = self.copies("localized-failed-test")
        self.fix_failed_test(work)
        (work / "tests" / "test_allocation.py").unlink()
        deleted = evaluate_local("localized-failed-test", baseline=base, workspace=work, final_text="fixed", commands=[record(TEST, 1), record(TEST)], raw_refs={})
        self.assertFalse(deleted["regression_test"])

        base, work, _ = self.copies("localized-failed-test")
        self.fix_failed_test(work, regression=False)
        test = work / "tests" / "test_allocation.py"
        test.write_text(test.read_text(encoding="utf-8") + "\n# regression added\n", encoding="utf-8")
        comment = evaluate_local("localized-failed-test", baseline=base, workspace=work, final_text="fixed", commands=[record(TEST, 1), record(TEST)], raw_refs={})
        self.assertFalse(comment["regression_test"])

    def test_wrong_or_missing_log_and_changed_lock_are_rejected(self) -> None:
        base, work, _ = self.copies("localized-long-output")
        (work / "src" / "route.py").write_text("ROUTES = {'ready': '/ready'}\n", encoding="utf-8")
        missing = evaluate_local("localized-long-output", baseline=base, workspace=work, final_text="BUILD417", commands=[record(LOG), record(TEST)], raw_refs={})
        self.assertFalse(missing["raw_output_preserved"])
        raw = self.raw_log(base)
        raw["digest"] = "sha256:" + "0" * 64
        wrong = evaluate_local("localized-long-output", baseline=base, workspace=work, final_text="BUILD417", commands=[record(LOG), record(TEST)], raw_refs={"build_log": raw})
        self.assertFalse(wrong["raw_output_preserved"])

        base, work, _ = self.copies("localized-missing-dependency")
        (work / "requirements.txt").write_text("pandas\n", encoding="utf-8")
        changed = evaluate_local("localized-missing-dependency", baseline=base, workspace=work, final_text="supported", commands=[record(TEST)], raw_refs={})
        self.assertFalse(changed["lock_unchanged"])

    def test_arbitrary_command_execution_is_rejected(self) -> None:
        base, work, _ = self.copies("localized-missing-dependency")
        result = evaluate_local(
            "localized-missing-dependency", baseline=base, workspace=work, final_text="supported",
            commands=[record(["python", "-B", "-c", "print('arbitrary')"]), record(TEST)], raw_refs={},
        )
        self.assertFalse(result["no_install"])
        self.assertFalse(result["supported_path_used"])

    def test_skipped_or_reconfigured_original_coverage_is_rejected(self) -> None:
        for alteration in (
            "@unittest.skip('removed coverage')\nclass AllocationTests",
            "class AllocationTests",
        ):
            with self.subTest(alteration=alteration):
                base, work, _ = self.copies("localized-failed-test")
                self.fix_failed_test(work)
                test = work / "tests/test_allocation.py"
                text = test.read_text(encoding="utf-8").replace("class AllocationTests", alteration)
                if alteration == "class AllocationTests":
                    text = text.replace("class AllocationTests(unittest.TestCase):", "class AllocationTests(unittest.TestCase):\n    def setUp(self):\n        self.skipTest('removed coverage')")
                test.write_text(text, encoding="utf-8")
                result = evaluate_local("localized-failed-test", baseline=base, workspace=work,
                                        final_text="fixed", commands=[record(TEST, 1), record(TEST)], raw_refs={})
                self.assertFalse(result["regression_test"])
                self.assertFalse(result["offline_test_evidence"])

    def test_replacing_or_supplementing_original_tests_with_tautologies_is_rejected(self) -> None:
        for replace_original in (True, False):
            with self.subTest(replace_original=replace_original):
                base, work, _ = self.copies("localized-failed-test")
                self.fix_failed_test(work, regression=False)
                test = work / "tests/test_allocation.py"
                original = "import unittest\n" if replace_original else test.read_text(encoding="utf-8")
                test.write_text(original + "\nclass WeakTest(unittest.TestCase):\n"
                                "    def test_always_true(self):\n        self.assertTrue(True)\n", encoding="utf-8")
                result = evaluate_local("localized-failed-test", baseline=base, workspace=work,
                                        final_text="fixed", commands=[record(TEST, 1), record(TEST)], raw_refs={})
                self.assertFalse(result["regression_test"])


if __name__ == "__main__":
    unittest.main()
