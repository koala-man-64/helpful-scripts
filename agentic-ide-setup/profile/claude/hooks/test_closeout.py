"""Scenario tests for the fact-based Stop closeout.

Each scenario writes a small transcript, points the hook at a real temporary
repository, and checks the one-shot nudge. The closing message's wording never
matters; only what the turn did and the repository's state.

Run from this directory: py -m pytest test_closeout.py
"""

import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stop_team_closeout as closeout  # noqa: E402

_ids = itertools.count(1)
SYNCED = "## claude/topic...origin/claude/topic"
AHEAD = "## claude/topic...origin/claude/topic [ahead 1]"


def user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def tool(name: str, **tool_input) -> dict:
    block = {"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": name, "input": tool_input}
    return {"type": "assistant", "message": {"role": "assistant", "content": [block]}}


def shell(command: str) -> dict:
    return tool("Bash", command=command)


def result(text: str) -> dict:
    block = {"type": "tool_result", "tool_use_id": "toolu_x", "content": text}
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def say(text: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


class CloseoutHarness(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="closeout-")
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        # The repository must not sit under the temp directory: edits there count as scratch.
        self.root = Path.home() / f".closeout-test-{next(_ids)}"
        self.root.mkdir()
        self.addCleanup(self._remove_root)
        subprocess.run(["git", "init", "-q", "-b", "claude/topic"], cwd=self.root, check=True)
        self.transcript = base / "transcript.jsonl"
        self.header = SYNCED
        saved = {name: getattr(closeout, name) for name in ("workflow_scope_enabled", "repo_root", "branch_header", "read_hook_input", "emit_json")}
        self.addCleanup(lambda: [setattr(closeout, k, v) for k, v in saved.items()])
        closeout.workflow_scope_enabled = lambda: True
        closeout.repo_root = lambda: self.root
        closeout.branch_header = lambda root=None: self.header

    def _remove_root(self) -> None:
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def stop(self, *records: dict, active: bool = False) -> str | None:
        self.transcript.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        captured = []
        closeout.read_hook_input = lambda: {"transcript_path": str(self.transcript), "stop_hook_active": active}
        closeout.emit_json = lambda payload: captured.append(payload) or 0
        closeout.main()
        return captured[-1]["reason"] if captured else None

    def edit(self, relative: str) -> dict:
        return tool("Edit", file_path=str(self.root / relative), old_string="a", new_string="b")


class ValidationFacts(CloseoutHarness):
    def test_source_edit_without_validation_nudges(self) -> None:
        reason = self.stop(user("fix the parser"), self.edit("src/app.py"), say("Done, fully validated."))
        self.assertIn("F3", reason)
        self.assertIn("src/app.py", reason)

    def test_validation_after_the_last_edit_passes(self) -> None:
        self.assertIsNone(self.stop(user("fix it"), self.edit("src/app.py"), shell("py -m pytest -q")))

    def test_edit_after_validation_nudges_again(self) -> None:
        reason = self.stop(user("fix it"), self.edit("src/a.py"), shell("pytest"), self.edit("src/b.py"))
        self.assertIn("src/b.py", reason)

    def test_docs_and_scratch_edits_need_no_validation(self) -> None:
        scratch = tool("Write", file_path=str(Path(tempfile.gettempdir()) / "notes.py"), content="x")
        self.assertIsNone(self.stop(user("update docs"), self.edit("README.md"), self.edit("docs/guide/setup.txt"), scratch))

    def test_data_records_and_templates_are_not_source(self) -> None:
        self.assertIsNone(self.stop(user("record the approval"), self.edit(".codedrift/approvals/ab1-x.json"), self.edit(".env.example")))

    def test_build_config_is_source(self) -> None:
        self.assertIn("package.json", self.stop(user("bump the dependency"), self.edit("web/package.json")))

    def test_browser_check_counts_as_validation(self) -> None:
        self.assertIsNone(self.stop(user("fix the page"), self.edit("web/index.html"), tool("mcp__Claude_Browser__navigate", url="http://localhost")))

    def test_heredoc_mentioning_pytest_is_not_a_test_run(self) -> None:
        commit = shell("git commit -F - <<'EOF'\nfix: parser (ran pytest)\nEOF")
        reason = self.stop(user("fix it"), self.edit("src/app.py"), commit, shell("git push -u origin claude/topic"), shell("gh pr create --title x"))
        self.assertIn("F3", reason)


class FinishFacts(CloseoutHarness):
    def test_commit_without_push_nudges(self) -> None:
        self.header = AHEAD
        reason = self.stop(user("fix it"), self.edit("README.md"), shell("git commit -m fix"))
        self.assertIn("F1", reason)

    def test_git_global_options_do_not_hide_the_subcommand(self) -> None:
        self.header = AHEAD
        reason = self.stop(user("fix it"), self.edit("README.md"), shell('git -C "C:/repo" -c core.autocrlf=false commit -m fix'))
        self.assertIn("F1", reason)
        pushed = self.stop(user("fix it"), self.edit("README.md"), shell("git -C C:/repo commit -m fix"), shell("git -C C:/repo push"))
        self.assertIn("F2", pushed)
        self.assertNotIn("F1", pushed)

    def test_commit_push_and_pr_pass(self) -> None:
        self.assertIsNone(self.stop(
            user("fix it"), self.edit("README.md"), shell("git commit -m fix"),
            shell("git push -u origin claude/topic"), shell("gh pr create --title fix"),
        ))

    def test_push_without_a_pull_request_nudges(self) -> None:
        reason = self.stop(user("fix it"), self.edit("README.md"), shell("git commit -m fix"), shell("git push"))
        self.assertIn("F2", reason)

    def test_pull_request_link_earlier_in_the_session_satisfies_f2(self) -> None:
        earlier = result("Created https://github.com/o/r/pull/12")
        self.assertIsNone(self.stop(
            user("open a PR"), earlier, user("address the review"),
            self.edit("README.md"), shell("git commit -m review"), shell("git push"),
        ))

    def test_scope_limited_prompt_switches_off_finish_facts(self) -> None:
        self.header = AHEAD
        self.assertIsNone(self.stop(user("fix it locally, no push"), self.edit("README.md"), shell("git commit -m wip")))


class ReviewFacts(CloseoutHarness):
    def test_risky_path_needs_a_reviewer_after_the_change(self) -> None:
        reason = self.stop(user("fix ci"), self.edit("azure-pipelines/ci.yml"), shell("py -m pytest"))
        self.assertIn("F4", reason)

    def test_reviewer_after_the_risky_change_passes(self) -> None:
        self.assertIsNone(self.stop(
            user("fix ci"), self.edit("azure-pipelines/ci.yml"), shell("py -m pytest"),
            tool("Agent", subagent_type="Plan", prompt="review the change"),
        ))


class Gates(CloseoutHarness):
    def test_one_shot_when_the_hook_already_fired(self) -> None:
        self.assertIsNone(self.stop(user("fix it"), self.edit("src/app.py"), active=True))

    def test_question_turn_passes(self) -> None:
        self.assertIsNone(self.stop(user("what does the parser do?"), say("It splits statements.")))

    def test_stop_hook_feedback_is_not_a_new_prompt(self) -> None:
        feedback = {"type": "user", "isMeta": True, "message": {"role": "user", "content": "Stop hook feedback:\nBefore stopping: F3"}}
        reason = self.stop(user("fix it"), self.edit("src/app.py"), say("done"), feedback, say("still done"))
        self.assertIn("F3", reason)


if __name__ == "__main__":
    unittest.main()
