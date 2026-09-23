"""Scenario tests for prompt routing: lanes, and independent tracking/finish/delegation answers.

Run from this directory: py -m pytest test_router.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hook_utils  # noqa: E402
import user_prompt_submit_router as router  # noqa: E402

FORBIDDEN = (
    "delivery-orchestrator-agent",
    "sonnet-tier",
    "lower_tier_blockers",
    "code-drift-sentinel",
    "software-testing-validation-architect",
    "Required agents",
)


class RouterScenarios(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CLAUDE_SESSION_FLAGS_DIR"] = self._tmp.name
        self._orig = (router.workflow_scope_enabled, router.read_hook_input, router.emit_json)
        self.captured: list = []
        router.workflow_scope_enabled = lambda: True
        router.emit_json = lambda payload: self.captured.append(payload) or 0
        self.counter = 0

    def tearDown(self) -> None:
        router.workflow_scope_enabled, router.read_hook_input, router.emit_json = self._orig
        os.environ.pop("CLAUDE_SESSION_FLAGS_DIR", None)
        self._tmp.cleanup()

    def route(self, prompt: str) -> str:
        self.counter += 1
        router.read_hook_input = lambda: {"session_id": f"s{self.counter}", "prompt": prompt}
        router.main()
        text = self.captured[-1]["hookSpecificOutput"]["additionalContext"]
        for phrase in FORBIDDEN:
            self.assertNotIn(phrase, text)
        return text

    def test_question_needs_no_ticket_branch_or_spawn(self) -> None:
        text = self.route("What does this function do?")
        self.assertIn("Suggested lane: question", text)
        self.assertIn("no ticket, branch, or agent spawn", text)
        self.assertNotIn("Tracking needed", text)
        self.assertNotIn("Finish workflow authority", text)

    def test_one_file_mechanical_edit_is_lite_and_solo(self) -> None:
        text = self.route("Fix the typo in the README heading")
        self.assertIn("Suggested lane: lite", text)
        self.assertIn("Delegation: no", text)
        self.assertIn("Tracking needed: no", text)

    def test_ordinary_bug_fix_is_standard_owner_delivery(self) -> None:
        text = self.route("Fix the off-by-one bug in the pagination helper")
        self.assertIn("Suggested lane: standard", text)
        self.assertIn("Tracking needed: no", text)
        self.assertIn("Commit/PR when files change: yes", text)
        self.assertIn("only a bounded Haiku reviewer or specialist", text)

    def test_security_change_is_critical_with_review(self) -> None:
        text = self.route("Update the token validation in the authentication middleware")
        self.assertIn("Suggested lane: critical", text)
        self.assertIn("independent review required", text)

    def test_critical_on_sonnet_session_is_flagged(self) -> None:
        import json

        path = Path(self._tmp.name) / "t.jsonl"
        path.write_text(json.dumps({"type": "assistant", "message": {"model": "claude-sonnet-5", "content": []}}), encoding="utf-8")
        router.read_hook_input = lambda: {"session_id": "crit", "prompt": "Fix the authentication bypass", "transcript_path": str(path)}
        router.main()
        text = self.captured[-1]["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Session model: sonnet, below the critical-lane owner", text)

    def test_git_finish_is_owner_work_without_tracking(self) -> None:
        text = self.route("finish it")
        self.assertIn("Commit/PR when files change: yes", text)
        self.assertIn("Tracking needed: no", text)
        self.assertIn("the owner completes the git finish workflow", text)
        self.assertIn("never spawn an agent just because work reached the finish stage", text)

    def test_boards_work_needs_tracking(self) -> None:
        text = self.route("Update work item AB#123 with the rollout status")
        self.assertIn("Tracking needed: yes", text)

    def test_documentation_correction_needs_no_specialists(self) -> None:
        text = self.route("Correct the wording in the setup docs")
        self.assertIn("Suggested lane: lite", text)
        self.assertIn("Optional specialist: none", text)

    def test_no_remote_marker_suppresses_finish(self) -> None:
        text = self.route("Refactor the parser, local-only, no push")
        self.assertIn("Commit/PR when files change: no", text)


class RouterRepetition(unittest.TestCase):
    """Emitted text is re-sent with every later request, so nothing is emitted twice in a row."""

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

    def route(self, prompt: str, session_id: str = "same") -> str:
        router.read_hook_input = lambda: {"session_id": session_id, "prompt": prompt}
        router.main()
        payload = self.captured[-1]
        return payload["hookSpecificOutput"]["additionalContext"] if payload else ""

    def test_same_routing_in_a_row_is_emitted_once(self) -> None:
        first = self.route("Fix the off-by-one bug in the pagination helper")
        self.assertIn("Suggested lane: standard", first)
        self.assertIn("the owner completes the git finish workflow", first)
        self.assertEqual(self.route("Fix the off-by-one bug in the sort helper"), "")

    def test_routing_returns_when_the_classification_changes_back(self) -> None:
        self.route("Fix the off-by-one bug in the pagination helper")
        self.assertIn("Suggested lane: question", self.route("What does this function do?"))
        again = self.route("Fix the off-by-one bug in the sort helper")
        self.assertIn("Suggested lane: standard", again)
        self.assertNotIn("the owner completes the git finish workflow", again)

    def test_task_notification_emits_nothing_and_keeps_state(self) -> None:
        self.route("What does this function do?")
        self.assertEqual(self.route("<task-notification>\n<task-id>b1</task-id>\n</task-notification>"), "")
        self.assertIsNone(self.captured[-1])
        self.assertEqual(self.route("What does this other function do?"), "")

    def test_contract_hint_follows_the_prompt_and_the_generic_line_is_standing(self) -> None:
        first = self.route("Rename a local helper")
        self.assertIn("classify the work as local-only or contracts-repo-first", first)
        self.assertNotIn("Potential shared contract surface", first)
        second = self.route("Add a field to the API response payload")
        self.assertIn("Potential shared contract surface", second)
        self.assertNotIn("classify the work as local-only or contracts-repo-first", second)
        self.assertIn("Potential shared contract surface", self.route("Add another field to the API response payload"))

    def test_cleared_flags_restate_everything(self) -> None:
        self.route("Fix the off-by-one bug in the pagination helper")
        hook_utils.clear_session_flags("same")
        again = self.route("Fix the off-by-one bug in the sort helper")
        self.assertIn("Suggested lane: standard", again)
        self.assertIn("the owner completes the git finish workflow", again)

    def test_unknown_session_always_emits(self) -> None:
        self.assertIn("Suggested lane", self.route("Fix the typo in the README heading", session_id=""))
        self.assertIn("Suggested lane", self.route("Fix the typo in the README heading", session_id=""))


class LaneClassification(unittest.TestCase):
    def test_critical_wins_over_lite(self) -> None:
        self.assertEqual(hook_utils.classify_lane("rename the secret rotation job")[0], "critical")

    def test_commit_alone_does_not_require_tracking(self) -> None:
        self.assertFalse(hook_utils.requires_tracking("commit and open a pull request"))

    def test_multi_repo_requires_tracking(self) -> None:
        self.assertTrue(hook_utils.requires_tracking("propagate the change multi-repo"))

    def test_pr_alone_does_not_require_bookkeeper_recap(self) -> None:
        self.assertFalse(hook_utils.requires_bookkeeper_recap("Changed the parser, committed, and opened PR 12."))

    def test_released_claims_are_not_a_release(self) -> None:
        self.assertFalse(hook_utils.requires_bookkeeper_recap("Implemented the wrapper. Claims released; monitors removed."))

    def test_boards_update_requires_bookkeeper_recap(self) -> None:
        self.assertTrue(hook_utils.requires_bookkeeper_recap("Updated work item AB#12 and closed work item."))


if __name__ == "__main__":
    unittest.main()
