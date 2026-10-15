"""Scenario tests for the fact-based Stop closeout.

Each scenario writes a small transcript, points the hook at a real temporary
repository, and checks the one-shot nudge. The closing message's wording never
matters; only what the turn did and the repository's state.

Run from this directory: py -m pytest test_closeout.py
"""

import itertools
import json
import os
import shutil
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
        self.root = Path(tempfile.mkdtemp(prefix=".closeout-test-", dir=Path.home()))
        self.addCleanup(self._rmtree, self.root)
        subprocess.run(["git", "init", "-q", "-b", "claude/topic"], cwd=self.root, check=True)
        self.transcript = base / "transcript.jsonl"
        self.header = SYNCED
        # A directory outside the repository that is not scratch either: only cd tracking keeps writes there out.
        self.elsewhere = Path(tempfile.mkdtemp(prefix=".closeout-elsewhere-", dir=Path.home()))
        self.addCleanup(self._rmtree, self.elsewhere)
        self.scratch = base  # under the temp directory, which the hook never counts as the repository
        previous_flags = os.environ.get("CLAUDE_SESSION_FLAGS_DIR")
        os.environ["CLAUDE_SESSION_FLAGS_DIR"] = str(base / "flags")
        self.addCleanup(self._restore_flags_dir, previous_flags)
        saved = {name: getattr(closeout, name) for name in ("workflow_scope_enabled", "repo_root", "branch_header", "read_hook_input", "emit_json")}
        self.addCleanup(lambda: [setattr(closeout, k, v) for k, v in saved.items()])
        closeout.workflow_scope_enabled = lambda root=None: True
        closeout.repo_root = lambda: self.root
        closeout.branch_header = lambda root=None: self.header

    @staticmethod
    def _rmtree(path: Path) -> None:
        """Remove a test directory even when git left read-only objects in it."""
        for entry in path.rglob("*"):
            try:
                entry.chmod(0o700)
            except OSError:
                pass
        shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _restore_flags_dir(previous: str | None) -> None:
        if previous is None:
            os.environ.pop("CLAUDE_SESSION_FLAGS_DIR", None)
        else:
            os.environ["CLAUDE_SESSION_FLAGS_DIR"] = previous

    def stop(self, *records: dict, active: bool = False, cwd: str | None = None, session_id: str | None = None) -> str | None:
        self.transcript.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        captured = []
        payload: dict = {"transcript_path": str(self.transcript), "stop_hook_active": active}
        if cwd:
            payload["cwd"] = cwd
        if session_id:
            payload["session_id"] = session_id
        closeout.read_hook_input = lambda: payload
        closeout.emit_json = lambda payload: captured.append(payload) or 0
        closeout.main()
        return captured[-1]["reason"] if captured else None

    def edit(self, relative: str) -> dict:
        return tool("Edit", file_path=str(self.root / relative), old_string="a", new_string="b")

    def sh(self, command: str, name: str = "Bash") -> dict:
        """A shell call recorded, as Claude Code records it, with the session's cwd."""
        return {**tool(name, command=command), "cwd": str(self.root)}


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

    def test_shell_writes_are_edits(self) -> None:
        for command in (
            "sed -i 's/a/b/' src/app.py",
            "sed -i.bak -e 's/a/b/' src/app.py",
            "cat > src/gen.py <<'EOF'\nprint(1)\nEOF",
            "echo x | tee -a src/app.py",
        ):
            with self.subTest(command=command):
                self.assertIn("F3", self.stop(user("fix it"), self.sh(command)) or "")
        powershell = self.sh("Set-Content -Path src/app.py -Value 'x'", name="PowerShell")
        self.assertIn("src/app.py", self.stop(user("fix it"), powershell))

    def test_shell_writes_to_data_scratch_or_unresolved_targets_are_not_source_edits(self) -> None:
        self.assertIsNone(self.stop(user("fix it"), self.sh("echo x > notes.json"), self.sh("echo x > /dev/null"),
                                    self.sh('echo x > "$OUT/app.py"'), self.sh("sed 's/a/b/' src/app.py")))

    def test_named_validation_scripts_count(self) -> None:
        for command in ("py scripts/validate_profile.py", "./scripts/test.sh", "pwsh ./Test-Setup.ps1", "node tools/check-links.js"):
            with self.subTest(command=command):
                self.assertIsNone(self.stop(user("fix it"), self.edit("src/app.py"), self.sh(command)))
        self.assertIn("F3", self.stop(user("fix it"), self.edit("src/app.py"), self.sh("test -f src/app.py")))

    def test_shell_write_to_a_risky_path_needs_a_reviewer(self) -> None:
        reason = self.stop(user("tune the pipeline"), self.sh("sed -i 's/a/b/' azure-pipelines/ci.yml"), self.sh("./scripts/test.sh"))
        self.assertIn("F4", reason)

    def test_running_the_edited_script_is_its_check(self) -> None:
        self.assertIsNone(self.stop(user("count the rows"), self.edit("q.py"), self.sh("py q.py --limit 5")))
        self.assertIsNone(self.stop(user("fix the runner"), self.edit("tools/run.ps1"), self.sh(".\\tools\\run.ps1", name="PowerShell")))
        self.assertIn("F3", self.stop(user("fix it"), self.edit("src/app.py"), self.sh("py tools/other.py")))
        self.assertIn("F3", self.stop(user("fix it"), self.edit("src/app.py"), self.sh("py -c 'import src.app'")))

    def test_bare_assignments_do_not_break_the_hook(self) -> None:
        reason = self.stop(user("fix it"), self.edit("src/app.py"), self.sh('S="C:/tmp/x"; echo "$S"'), self.sh("OUT=1"))
        self.assertIn("F3", reason)

    def test_data_records_and_templates_are_not_source(self) -> None:
        self.assertIsNone(self.stop(user("record the approval"), self.edit(".codedrift/approvals/ab1-x.json"),
                                    self.edit(".codedrift/approvals/ab2-y.yml"), self.edit(".env.example")))

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

    def test_tests_of_a_risky_area_are_not_risky_changes(self) -> None:
        self.assertIsNone(self.stop(user("cover the plan"), self.edit("tests/migrations/test_plan.py"), shell("py -m pytest")))
        reason = self.stop(user("add the migration"), self.edit("deploy/sql/migrations/0042_grant.sql"), shell("py -m pytest"))
        self.assertIn("F4", reason)


class Gates(CloseoutHarness):
    def test_one_shot_when_the_hook_already_fired(self) -> None:
        self.assertIsNone(self.stop(user("fix it"), self.edit("src/app.py"), active=True))

    def test_question_turn_passes(self) -> None:
        self.assertIsNone(self.stop(user("what does the parser do?"), say("It splits statements.")))

    def test_stop_hook_feedback_is_not_a_new_prompt(self) -> None:
        feedback = {"type": "user", "isMeta": True, "message": {"role": "user", "content": "Stop hook feedback:\nBefore stopping: F3"}}
        reason = self.stop(user("fix it"), self.edit("src/app.py"), say("done"), feedback, say("still done"))
        self.assertIn("F3", reason)


class ShellDirectories(CloseoutHarness):
    """Files a shell statement writes or runs are located where the statement runs."""

    def test_a_write_after_cd_elsewhere_is_not_a_repository_edit(self) -> None:
        away = self.elsewhere.as_posix()
        for command, name in (
            (f'cd "{away}" && cat > build_log.py <<\'EOF\'\nprint(1)\nEOF', "Bash"),
            (f'S="{away}"; cd "$S" && py -I tools/ado.py attach 2041 "$S/memo.md" && cat > c2041.txt <<\'EOF\'\nposted\nEOF', "Bash"),
            (f'cd "{away}" && sed -i \'s/a/b/\' azure-pipelines/ci.yml', "Bash"),
            (f'cd "{away}"; py -I - build_log.py <<\'EOF\'\nprint(1)\nEOF', "Bash"),
            (f'Set-Location "{away}"; Set-Content -Path tool.py -Value "x"', "PowerShell"),
        ):
            with self.subTest(command=command):
                self.assertIsNone(self.stop(user("read the build log"), self.sh(command, name=name)))

    def test_a_write_after_cd_inside_the_repository_is_an_edit(self) -> None:
        for command, name, expected in (
            ("cd src && cat > gen.py <<'EOF'\nprint(1)\nEOF", "Bash", "src/gen.py"),
            ("if true; then cd src; fi; echo x > s.py", "Bash", "src/s.py"),
            ('W="src"; cd "$W" && sed -i "s/a/b/" app.py', "Bash", "src/app.py"),
            ("Set-Location src; Set-Content -Path p.py -Value 'x'", "PowerShell", "src/p.py"),
        ):
            with self.subTest(command=command):
                self.assertIn(expected, self.stop(user("fix it"), self.sh(command, name=name)) or "")

    def test_relative_writes_in_an_unknown_directory_are_not_attributed(self) -> None:
        self.assertIsNone(self.stop(user("fix it"), self.sh('cd "$ELSEWHERE" && cat > x.py <<\'EOF\'\nprint(1)\nEOF')))
        self.assertIsNone(self.stop(user("fix it"), self.sh("cd - && echo x > y.py")))
        self.assertIsNone(self.stop(user("fix it"), shell("cat > z.py <<'EOF'\nprint(1)\nEOF")))  # no cwd recorded

    def test_a_cd_in_a_subshell_ends_with_it(self) -> None:
        command = f'(cd "{self.elsewhere.as_posix()}" && cat > a.py <<\'EOF\'\nx\nEOF\n); cat > b.py <<\'EOF\'\ny\nEOF'
        reason = self.stop(user("fix it"), self.sh(command)) or ""
        self.assertIn("b.py", reason)
        self.assertNotIn("a.py", reason)

    def test_popd_returns_to_the_pushed_directory(self) -> None:
        command = f'pushd "{self.elsewhere.as_posix()}" && echo x > t.py; popd; echo y > u.py'
        reason = self.stop(user("fix it"), self.sh(command)) or ""
        self.assertIn("u.py", reason)
        self.assertNotIn("t.py", reason)

    def test_running_a_same_named_script_elsewhere_is_not_the_edited_scripts_check(self) -> None:
        reason = self.stop(user("count the rows"), self.edit("q.py"), self.sh(f'cd "{self.elsewhere.as_posix()}" && py q.py'))
        self.assertIn("F3", reason or "")


class OncePerSession(CloseoutHarness):
    """A fact is nudged once per session, however many harness-started turns follow."""

    peer = {"type": "user", "isMeta": True, "message": {"role": "user", "content": "Another Claude session sent a message: hold the push"}}

    def test_the_same_fact_is_not_nudged_twice(self) -> None:
        records = [user("fix it"), self.edit("src/app.py"), say("holding")]
        self.assertIn("F3", self.stop(*records, session_id="s1") or "")
        self.assertIsNone(self.stop(*records, self.peer, say("still holding"), session_id="s1"))
        reason = self.stop(*records, self.peer, say("still holding"), self.edit("src/b.py"), session_id="s1") or ""
        self.assertIn("src/b.py", reason)

    def test_a_different_fact_still_nudges(self) -> None:
        self.header = AHEAD
        records = [user("fix it"), self.edit("src/app.py")]
        self.assertIn("F3", self.stop(*records, session_id="s2") or "")
        reason = self.stop(*records, self.peer, shell("git commit -m fix"), session_id="s2") or ""
        self.assertIn("F1", reason)
        self.assertNotIn("F3", reason)

    def test_without_a_session_id_the_nudge_repeats(self) -> None:
        records = [user("fix it"), self.edit("src/app.py")]
        self.assertIn("F3", self.stop(*records) or "")
        self.assertIn("F3", self.stop(*records) or "")


class Roots(CloseoutHarness):
    """The repository is the one under the payload's cwd when the hooks apply there."""

    def test_the_payload_cwd_names_the_repository(self) -> None:
        closeout.repo_root = lambda: self.root.parent  # the hook process started above the worktree
        with_cwd = self.stop(user("fix it"), self.edit("src/app.py"), cwd=str(self.root)) or ""
        self.assertIn("edit to src/app.py", with_cwd)
        without_cwd = self.stop(user("fix it"), self.edit("src/app.py")) or ""
        self.assertIn(f"{self.root.name}/src/app.py", without_cwd)

    def test_a_cwd_outside_any_repository_falls_back_to_the_hook_process_repository(self) -> None:
        reason = self.stop(user("fix it"), self.edit("src/app.py"), cwd=str(self.scratch)) or ""
        self.assertIn("edit to src/app.py", reason)

    def test_the_gate_does_not_widen_to_a_directory_outside_scope(self) -> None:
        closeout.repo_root = lambda: self.root.parent
        closeout.workflow_scope_enabled = lambda root=None: Path(root).resolve() == self.root.resolve()
        self.assertIn("edit to src/app.py", self.stop(user("fix it"), self.edit("src/app.py"), cwd=str(self.root)) or "")
        self.assertIsNone(self.stop(user("fix it"), self.edit("src/app.py"), cwd=str(self.scratch)))

    def test_no_repository_in_scope_is_silent(self) -> None:
        closeout.workflow_scope_enabled = lambda root=None: False
        self.assertIsNone(self.stop(user("fix it"), self.edit("src/app.py"), cwd=str(self.root)))

    def test_the_risky_repository_rule_reads_the_repository_name_not_the_directory(self) -> None:
        # A worktree of asset-allocation-contracts is named for its task; the origin still says which repository it is.
        subprocess.run(["git", "remote", "add", "origin", "https://dev.azure.com/o/p/_git/asset-allocation-contracts"], cwd=self.root, check=True)
        reason = self.stop(user("rename the field"), self.edit("notes/field.py"), shell("py -m pytest"), cwd=str(self.root)) or ""
        self.assertIn("F4", reason)
        self.assertEqual(closeout.repository_name(self.root), "asset-allocation-contracts")

    def test_a_worktree_without_an_origin_is_named_for_its_main_checkout(self) -> None:
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "root"], cwd=self.root, check=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})
        worktree = self.elsewhere / "task-worktree"
        subprocess.run(["git", "worktree", "add", "-q", "-b", "claude/task", str(worktree)], cwd=self.root, check=True)
        self.addCleanup(lambda: subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=self.root, check=False))
        self.assertEqual(closeout.repository_name(worktree), self.root.name)
        self.assertEqual(closeout.repository_name(self.scratch), self.scratch.name)


if __name__ == "__main__":
    unittest.main()
