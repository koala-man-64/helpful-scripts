"""Scenario tests for the Stop closeout hooks.

Run from this directory: py -m pytest test_closeout.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stop_gateway_bookkeeper_recap as recap  # noqa: E402
import stop_team_closeout as closeout  # noqa: E402


class CloseoutHarness(unittest.TestCase):
    dirty = False

    def setUp(self) -> None:
        self.saved = {}
        for module in (closeout, recap):
            self.saved[module] = {
                name: getattr(module, name)
                for name in ("workflow_scope_enabled", "turn_did_work", "read_hook_input", "extract_last_message", "emit_json")
            }
            module.workflow_scope_enabled = lambda: True
            module.turn_did_work = lambda payload: True
            module.read_hook_input = lambda: {}
        self.saved_git = (closeout.branch_header, closeout.git_status_lines)
        closeout.branch_header = lambda: "## task-branch...origin/task-branch" + (" [ahead 1]" if self.dirty else "")
        closeout.git_status_lines = lambda: ["## task-branch"]

    def tearDown(self) -> None:
        for module, attrs in self.saved.items():
            for name, value in attrs.items():
                setattr(module, name, value)
        closeout.branch_header, closeout.git_status_lines = self.saved_git

    def run_hook(self, module, message: str):
        captured = []
        module.extract_last_message = lambda payload: message
        module.emit_json = lambda payload: captured.append(payload) or 0
        module.main()
        return captured[-1]["reason"] if captured else None

    def assertPasses(self, module, message: str) -> None:
        self.assertIsNone(self.run_hook(module, message))

    def assertBlocks(self, module, message: str, fragment: str) -> str:
        reason = self.run_hook(module, message)
        self.assertIsNotNone(reason, "expected a block")
        self.assertIn(fragment, reason)
        return reason


class TeamCloseoutScenarios(CloseoutHarness):
    def test_mechanical_edit_needs_no_gate_agents(self) -> None:
        self.assertPasses(
            closeout,
            "Fixed the README typo. Validated by rendering the file. Committed, pushed, "
            "and opened PR [#5](https://example/pr/5); merged after checks passed.",
        )

    def test_bug_fix_finish_does_not_name_specialists(self) -> None:
        reason = self.run_hook(
            closeout,
            "Fixed the pagination off-by-one and added a regression test; tests pass. "
            "Committed, pushed, opened PR #7, merged.",
        )
        self.assertIsNone(reason)

    def test_change_without_validation_still_blocks(self) -> None:
        self.assertBlocks(
            closeout,
            "Updated the parser. Committed, pushed, opened PR #8.",
            "validation run or explicit not-run reason",
        )

    def test_security_change_requires_independent_review(self) -> None:
        reason = self.assertBlocks(
            closeout,
            "Changed the authentication middleware token check; tests pass. Committed, "
            "pushed, opened PR #9.",
            "independent review",
        )
        self.assertNotIn("code-drift-sentinel", reason)
        self.assertNotIn("software-testing-validation-architect", reason)

    def test_security_change_with_review_passes(self) -> None:
        self.assertPasses(
            closeout,
            "Changed the authentication middleware token check; tests pass. Independent "
            "review by a Sonnet security specialist found no issues. Committed, pushed, "
            "opened PR #9.",
        )

    def test_critical_request_requires_review_even_if_report_omits_risk_words(self) -> None:
        import json
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "user", "message": {"content": "auth.py compares signatures with ==; make it constant-time"}}))
            path = fh.name
        self.addCleanup(Path(path).unlink)
        closeout.read_hook_input = lambda: {"transcript_path": path}
        self.assertBlocks(
            closeout,
            "Fixed check_signature to use hmac.compare_digest; tests pass. Committed on the task branch; local-only, no push.",
            "independent review",
        )

    def test_pending_human_approval_is_a_valid_stop(self) -> None:
        self.assertPasses(
            closeout,
            "Updated the deployment config; validated with the pipeline dry run. Committed, "
            "pushed, and opened PR #10. Blocked: production approval is pending with Rudy; "
            "next step is to resume monitoring once approved.",
        )

    def test_rejected_claim_is_a_valid_blocker(self) -> None:
        self.assertPasses(
            closeout,
            "Updated the shared helper tests; tests pass. Blocked: the agentcoord claim on "
            "src/shared was rejected, so the dependent write was not made. Next step: wait "
            "for the claim owner to finish.",
        )

    def test_blocker_without_next_action_blocks(self) -> None:
        self.assertBlocks(
            closeout,
            "Updated the parser; tests pass. Blocked: could not push.",
            "exact next action",
        )

    def test_clean_completion_passes(self) -> None:
        self.assertPasses(
            closeout,
            "Implemented the retry wrapper and verified it with the new unit tests. "
            "Committed, pushed, opened PR #11, and merged. Claims released; no monitors remain.",
        )


class BookkeeperRecapScenarios(CloseoutHarness):
    def test_pr_without_tracking_needs_no_recap(self) -> None:
        self.assertPasses(
            recap,
            "Fixed the pagination bug; tests pass. Committed, pushed, opened PR #7.",
        )

    def test_boards_update_needs_recap(self) -> None:
        self.assertBlocks(
            recap,
            "Updated the pipeline YAML and closed work item AB#44.",
            "Bookkeeper Recap section",
        )


if __name__ == "__main__":
    unittest.main()
