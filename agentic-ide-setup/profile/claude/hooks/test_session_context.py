"""Tests for the once-per-session router text and the SessionStart wait cap.

Run from this directory: py -m pytest test_session_context.py
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hook_utils  # noqa: E402
import session_start_team_context as session_start  # noqa: E402
import user_prompt_submit_router as router  # noqa: E402
import wait_registry  # noqa: E402


class SessionFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_SESSION_FLAGS_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_SESSION_FLAGS_DIR", None)
        self._tmp.cleanup()

    def test_first_call_true_then_false(self) -> None:
        self.assertTrue(hook_utils.session_flag_once("abc-123", "policy"))
        self.assertFalse(hook_utils.session_flag_once("abc-123", "policy"))
        # A different flag name in the same session is independent.
        self.assertTrue(hook_utils.session_flag_once("abc-123", "authority"))

    def test_sessions_are_independent(self) -> None:
        self.assertTrue(hook_utils.session_flag_once("s1", "policy"))
        self.assertTrue(hook_utils.session_flag_once("s2", "policy"))

    def test_missing_session_id_always_emits(self) -> None:
        self.assertTrue(hook_utils.session_flag_once("", "policy"))
        self.assertTrue(hook_utils.session_flag_once("", "policy"))

    def test_clear_resets(self) -> None:
        self.assertTrue(hook_utils.session_flag_once("s1", "policy"))
        hook_utils.clear_session_flags("s1")
        self.assertTrue(hook_utils.session_flag_once("s1", "policy"))

    def test_corrupt_flag_file_emits(self) -> None:
        path = Path(self._tmp.name) / "s1.json"
        path.write_text("not json", encoding="utf-8")
        self.assertTrue(hook_utils.session_flag_once("s1", "policy"))

    def test_prune_drops_old_files_only(self) -> None:
        old = Path(self._tmp.name) / "old.json"
        new = Path(self._tmp.name) / "new.json"
        old.write_text("{}", encoding="utf-8")
        new.write_text("{}", encoding="utf-8")
        stale = (datetime.now(UTC) - timedelta(days=30)).timestamp()
        os.utime(old, (stale, stale))
        hook_utils.prune_session_flags(max_age_days=7)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())


class RouterOncePerSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_SESSION_FLAGS_DIR"] = self._tmp.name
        self._orig = (router.workflow_scope_enabled, router.read_hook_input, router.emit_json)
        self.captured: list = []
        router.workflow_scope_enabled = lambda: True
        router.emit_json = lambda payload: self.captured.append(payload) or 0

    def tearDown(self) -> None:
        router.workflow_scope_enabled, router.read_hook_input, router.emit_json = self._orig
        os.environ.pop("CLAUDE_SESSION_FLAGS_DIR", None)
        self._tmp.cleanup()

    def _run(self, session_id: str, prompt: str) -> str:
        router.read_hook_input = lambda: {"session_id": session_id, "prompt": prompt}
        router.main()
        payload = self.captured[-1]
        return payload["hookSpecificOutput"]["additionalContext"] if payload else ""

    def test_standing_and_authority_text_emitted_once(self) -> None:
        authority = hook_utils.AZURE_DEVOPS_AGENT_AUTHORITY_LINES[0]
        first = self._run("sess-1", "finish it")
        self.assertIn("Finish authority", first)
        # Finishing is the owner's job; no mandatory child or orchestrator.
        self.assertNotIn("sonnet-tier", first)
        self.assertNotIn("delivery-orchestrator-agent", first)
        self.assertIn(authority, first)
        # Same routing as the previous turn: nothing new to say.
        self.assertEqual(self._run("sess-1", "finish it"), "")
        later = self._run("sess-1", "what does this function do?")
        self.assertIn("- Work kind:", later)
        self.assertNotIn("Finish authority", later)
        self.assertNotIn(authority, later)

    def test_authority_waits_for_a_turn_that_needs_it(self) -> None:
        authority = hook_utils.AZURE_DEVOPS_AGENT_AUTHORITY_LINES[0]
        router.requires_finish_workflow = lambda prompt: False
        router.requires_tracking = lambda prompt: False
        try:
            first = self._run("sess-2", "explain this function")
            self.assertIn("Finish authority", first)
            self.assertNotIn(authority, first)
        finally:
            router.requires_finish_workflow = hook_utils.requires_finish_workflow
            router.requires_tracking = hook_utils.requires_tracking
        later = self._run("sess-2", "finish it")
        self.assertIn(authority, later)
        again = self._run("sess-2", "finish it")
        self.assertNotIn(authority, again)

    def test_no_session_id_emits_everything_every_turn(self) -> None:
        first = self._run("", "finish it")
        second = self._run("", "finish it")
        self.assertIn("Finish authority", first)
        self.assertIn("Finish authority", second)


class OutstandingWaitCapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "registry.json"
        os.environ["CLAUDE_WAITS_PATH"] = str(self._path)

    def tearDown(self) -> None:
        os.environ.pop("CLAUDE_WAITS_PATH", None)
        self._tmp.cleanup()

    def _write(self, count: int) -> None:
        base = datetime.now(UTC) - timedelta(minutes=count)
        rows = []
        for index in range(count):
            created = (base + timedelta(minutes=index)).isoformat()
            rows.append(
                {
                    "wait_id": f"wait{index:02d}",
                    "provider": "azure_devops",
                    "operation_kind": "pull_request",
                    "resource_id": str(100 + index),
                    "repository": "repo",
                    "status": "registered",
                    "created_at": created,
                    "updated_at": created,
                }
            )
        data = {
            "schema_version": getattr(wait_registry, "SCHEMA_VERSION", 1),
            "waits": rows,
            "diagnostics": [],
        }
        self._path.write_text(json.dumps(data), encoding="utf-8")

    def test_cap_keeps_newest_and_summarises_the_rest(self) -> None:
        self._write(8)
        lines = session_start.outstanding_waits()
        listed = [line for line in lines if line.startswith("- wait")]
        self.assertEqual(len(listed), session_start.MAX_WAITS_SHOWN)
        self.assertTrue(listed[-1].startswith("- wait07:"))
        self.assertTrue(listed[0].startswith("- wait03:"))
        summary = [line for line in lines if "older wait(s) not listed" in line]
        self.assertEqual(len(summary), 1)
        self.assertIn("3 older wait(s)", summary[0])
        self.assertTrue(lines[-1].startswith("Poll with:"))

    def test_under_cap_lists_all_without_summary(self) -> None:
        self._write(3)
        lines = session_start.outstanding_waits()
        listed = [line for line in lines if line.startswith("- wait")]
        self.assertEqual(len(listed), 3)
        self.assertFalse(any("not listed" in line for line in lines))

    def test_poll_command_names_the_sibling_poller(self) -> None:
        """The hooks may run from a release clone, not ~/.claude/hooks."""
        self._write(1)
        poll = session_start.outstanding_waits()[-1]
        sibling = Path(session_start.__file__).resolve().parent / "wait_poll.py"
        self.assertIn(f'py "{sibling}" poll --all', poll)


if __name__ == "__main__":
    unittest.main()
