from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from context_selection import build_context, capture_git_state, revalidate_context, revalidate_git_state, select_context
from output_projection import expand_record, project_file, run_process


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_single_line_153k_hidden_failure_and_unicode(self):
        # Fixed synthetic fixture; no retained host output or prompts.
        text = "a" * 76000 + " ERROR TEST983 hidden failure 本手 火候 " + "b" * 76966
        text = (text + "z" * 153000)[:153000]
        self.assertEqual(len(text), 153000)
        path = self.root / "long.log"
        path.write_text(text, encoding="utf-8")
        result = project_file(path, exit_status=9)
        self.assertEqual(result["exit_status"], 9)
        self.assertFalse(result["process_succeeded"])
        self.assertEqual(result["failure_records"], 1)
        row = result["records"][0]
        self.assertEqual(row["code"], "TEST983")
        self.assertIn("hidden failure 本手 火候", " ".join(row["diagnostic_windows"]))
        self.assertEqual(expand_record(row).decode("utf-8"), text)
        self.assertFalse(row["decode_replacement"])
        self.assertLess(len(json.dumps(result)), 8000)
        self.assertGreater(row["bytes"], row["characters"])

    def test_failure_at_end_not_hidden_by_routine_budget(self):
        path = self.root / "late.log"
        path.write_text("ordinary successful record\n" * 2000 + "FAILED test_ownership - expected refusal\n", encoding="utf-8")
        result = project_file(path, exit_status=1)
        self.assertGreater(result["omitted_records"], 1900)
        self.assertIn("test_ownership", result["records"][-1]["message_prefix"])

    def test_python_exception_cause_chain_survives_small_budget(self):
        trace = 'Traceback (most recent call last):\n  File "a.py", line 9\n    first()\nValueError: original\n\nThe above exception was the direct cause of the following exception:\n\nTraceback (most recent call last):\n  File "b.py", line 2\n    second()\nRuntimeError: wrapper\n'
        path = self.root / "trace.log"
        path.write_text(trace, encoding="utf-8")
        result = project_file(path, exit_status=1, target_tokens=1)
        retained = "".join(expand_record(row).decode() for row in result["records"])
        for important in ("first()", "second()", "ValueError: original", "direct cause", "RuntimeError: wrapper"):
            self.assertIn(important, retained)
        self.assertTrue(result["budget"]["target_exceeded"])

    def test_dotnet_cause_and_stack_frames_survive_small_budget(self):
        text = "System.InvalidOperationException: outer\n ---> System.IO.IOException: inner\n   at Loader.Read()\n   --- End of inner exception stack trace ---\n   at Runner.Start()\n"
        path = self.root / "dotnet.log"
        path.write_bytes(text.encode("utf-8"))
        result = project_file(path, exit_status=1, target_tokens=1)
        retained = "".join(expand_record(row).decode() for row in result["records"])
        self.assertEqual(retained, text)

    def test_expand_rejects_changed_evidence(self):
        path = self.root / "mutable.log"
        path.write_bytes(b"hello")
        record = project_file(path, exit_status=0)["records"][0]
        path.write_bytes(b"world")
        with self.assertRaisesRegex(ValueError, "changed"):
            expand_record(record)

    def test_structured_metadata_is_bounded_and_size_is_measured(self):
        path = self.root / "metadata.log"
        path.write_text(json.dumps({"message": "routine", "timestamp": "x" * 153000, "severity": {"nested": "y" * 153000}, "code": "z" * 153000, "location": ["w" * 153000]}))
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("must hash the stream")):
            result = project_file(path, exit_status=0)
        row = result["records"][0]
        self.assertEqual(set(row["metadata_clipped_or_non_scalar"]), {"timestamp", "severity", "code", "location"})
        self.assertLessEqual(len(row["timestamp"]), 64)
        self.assertLessEqual(len(row["code"]), 96)
        self.assertIsNone(row["severity"])
        self.assertIsNone(row["location"])
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertLess(len(rendered), 8000)
        self.assertEqual(result["budget"]["projected_characters"], len(rendered))
        self.assertEqual(result["budget"]["projected_utf8_bytes"], len(rendered.encode("utf-8")))

    def test_growing_source_is_reported(self):
        import output_projection
        path = self.root / "growing.log"
        path.write_bytes(b"first\n")
        original = output_projection._record

        def append_once(raw, source, offset, number, chain):
            if number == 1:
                with source.open("ab") as stream:
                    stream.write(b"second\n")
            return original(raw, source, offset, number, chain)

        with mock.patch.object(output_projection, "_record", side_effect=append_once):
            result = project_file(path, exit_status=0)
        self.assertTrue(result["raw_file"]["source_changed_during_scan"])
        self.assertEqual(result["raw_file"]["hash_basis"], "bytes observed during scan")

    def test_process_exit_status_and_binary_evidence(self):
        result = run_process([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'FAIL test_lost\\xff'); sys.exit(7)"], cwd=self.root, raw_dir=self.root / "raw")
        self.assertEqual(result["exit_status"], 7)
        self.assertTrue(result["records"][0]["decode_replacement"])
        self.assertEqual(expand_record(result["records"][0]), b"FAIL test_lost\xff")


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "--initial-branch=task")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        (self.root / "requirements.txt").write_text("example==1.0\n")
        self.git("add", "requirements.txt")
        self.git("commit", "-m", "synthetic base")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, check=True)

    def test_selection_retains_negation_order_acceptance_and_expand_ids(self):
        rows = [{"id": str(i), "kind": "task", "message": "routine"} for i in range(30)]
        rows += [{"id": "urgent", "kind": "constraint", "message": "Do NOT merge before the human gate; then resume only after approval."}, {"id": "acceptance", "kind": "acceptance", "message": "No lost or duplicate continuation."}]
        result = select_context(rows, "routine", limit=10)
        self.assertEqual(len(result["rows"]), 10)
        self.assertEqual(result["rows"][-2:], rows[-2:])
        self.assertEqual(len(result["omitted_ids"]), 22)

    def test_git_and_ownership_revalidated_and_stages_separate(self):
        path = self.root / "records.json"
        rows = [{"id": "claim", "kind": "ownership", "status": "held", "authority": "central_hooks", "owner": "fixture-task"}, {"id": "src", "kind": "evidence", "stage": "source", "status": "passed"}]
        path.write_text(json.dumps(rows))
        context = build_context(self.root, path)
        self.assertFalse(context["mutation_ready"])
        self.assertEqual(context["stage_receipts"]["source"], ["src"])
        self.assertEqual(context["stage_receipts"]["runtime"], [])
        self.assertTrue(any("fresh authoritative" in error for error in revalidate_context(context)))
        self.assertEqual(revalidate_context(context, verify_ownership=lambda _: True), [])
        rows[0]["status"] = "conflict"
        path.write_text(json.dumps(rows))
        self.assertTrue(any("records changed" in error for error in revalidate_context(context)))
        (self.root / "requirements.txt").write_text("example==2.0\n")
        self.assertTrue(any("Git/worktree" in error for error in revalidate_context(context)))

    def test_lock_and_wrong_branch_are_detected(self):
        previous = capture_git_state(self.root)
        (self.root / ".git" / "index.lock").write_bytes(b"locked")
        self.assertTrue(any("git_locks" in item for item in revalidate_git_state(previous)))
        (self.root / ".git" / "index.lock").unlink()
        self.git("switch", "-c", "wrong-task")
        self.assertTrue(any("branch" in item for item in revalidate_git_state(previous)))

    def test_unknown_ownership_blocks_mutable_reuse(self):
        context = build_context(self.root)
        self.assertTrue(any("ownership remains unknown" in item for item in revalidate_context(context)))

    def test_unavailable_conflicting_or_stale_ownership_cannot_pass(self):
        path = self.root / "claims.json"
        for status in ("unavailable", "conflict", "stale", "released"):
            path.write_text(json.dumps([{"id": "claim", "kind": "ownership", "status": status, "authority": "agentcoord", "owner": "fixture-task"}]))
            context = build_context(self.root, path)
            self.assertTrue(any("unresolved" in error for error in revalidate_context(context, verify_ownership=lambda _: True)))


if __name__ == "__main__":
    unittest.main()
