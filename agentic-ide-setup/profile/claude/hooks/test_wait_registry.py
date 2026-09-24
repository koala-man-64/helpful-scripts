"""Tests for the durable wait registry and the PostToolUse wait detector.

Run from this directory:

    py -3 -m unittest test_wait_registry -v

The load-bearing cases are in DetectorSeamTests. The codex-workflow-hooks
equivalent of this feature passed its whole unit suite while having never once
run in production, because every test hand-placed the artifact that the
recorder was supposed to produce and then asserted the consumer read it back.
The seam between producer and consumer was the only broken part and the only
untested one. So: nothing below writes a wait row directly and then checks it
was written. The detector is driven with real hook payloads and the registry is
inspected afterwards.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import post_tool_use_wait_detector as detector  # noqa: E402
import wait_poll  # noqa: E402
import wait_registry  # noqa: E402

AZ_PR_JSON = json.dumps({"pullRequestId": 4242, "status": "active"})
AZ_RUN_JSON = json.dumps({"id": 991, "definition": {"id": 10}, "status": "inProgress"})
GH_PR_URL = "https://github.com/koala-man-64/helpful-scripts/pull/200"


def payload(command: str, response, tool: str = "Bash") -> dict:
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool,
        "tool_input": {"command": command},
        "tool_response": response,
        "session_id": "session-under-test",
    }


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="waits-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "registry.json"

    def register(self, **overrides):
        base = dict(
            provider="azure_devops",
            operation_kind="pull_request",
            resource_id="1",
            repository="repo",
            branch="agent/x",
            commit="a" * 40,
            target_state="merged",
            path=self.path,
        )
        base.update(overrides)
        return wait_registry.register(**base)

    def test_register_then_read_back(self):
        created = self.register()
        rows = wait_registry.active(self.path)
        self.assertEqual([r["wait_id"] for r in rows], [created["wait_id"]])
        self.assertEqual(rows[0]["status"], "registered")

    def test_registration_is_idempotent_for_one_resource(self):
        first = self.register()
        second = self.register()
        self.assertEqual(first["wait_id"], second["wait_id"])
        self.assertEqual(len(wait_registry.active(self.path)), 1)

    def test_distinct_resources_register_separately(self):
        self.register(resource_id="1")
        self.register(resource_id="2")
        self.assertEqual(len(wait_registry.active(self.path)), 2)

    def test_terminal_status_leaves_active_set(self):
        created = self.register()
        wait_registry.update_status(
            created["wait_id"], status="succeeded", detail_code="pr_completed", path=self.path
        )
        self.assertEqual(wait_registry.active(self.path), [])
        stored = wait_registry.get(created["wait_id"], self.path)
        self.assertEqual(stored["status"], "succeeded")
        self.assertTrue(stored["last_checked_at"])

    def test_expiry_uses_per_kind_timeout(self):
        old = (datetime.now(UTC) - timedelta(hours=80)).isoformat()
        self.assertTrue(
            wait_registry.is_expired({"operation_kind": "pull_request", "created_at": old})
        )
        recent = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
        self.assertFalse(
            wait_registry.is_expired({"operation_kind": "pull_request", "created_at": recent})
        )
        # A pipeline expires sooner than a pull request at the same age.
        self.assertTrue(
            wait_registry.is_expired({"operation_kind": "pipeline", "created_at": recent})
        )

    def test_unparseable_created_at_is_treated_as_expired(self):
        self.assertTrue(
            wait_registry.is_expired({"operation_kind": "pull_request", "created_at": "junk"})
        )

    def test_corrupt_registry_does_not_raise(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(wait_registry.active(self.path), [])
        created = self.register()
        self.assertTrue(created["wait_id"])

    def test_concurrent_writer_rows_are_not_clobbered(self):
        mine = self.register(resource_id="1")
        # Simulate another session appending between this process's read and
        # write by writing straight to the file, then registering again.
        other = dict(mine)
        other.update({"wait_id": "b" * 24, "resource_id": "2"})
        data = wait_registry.load(self.path)
        data["waits"].append(other)
        wait_registry.save(data, self.path)
        self.register(resource_id="3")
        ids = {row["wait_id"] for row in wait_registry.active(self.path)}
        self.assertIn(mine["wait_id"], ids)
        self.assertIn("b" * 24, ids)
        self.assertEqual(len(ids), 3)

    def test_doctor_flags_overdue_and_unbound(self):
        healthy = wait_registry.doctor(self.path)
        self.assertTrue(healthy["healthy"])
        self.assertEqual(healthy["total"], 0)

        created = self.register()
        stale = wait_registry.load(self.path)
        for row in stale["waits"]:
            if row["wait_id"] == created["wait_id"]:
                row["created_at"] = (datetime.now(UTC) - timedelta(hours=200)).isoformat()
        wait_registry.save(stale, self.path)
        wait_registry.record_diagnostic("WAIT_TRIGGER_UNBOUND", "no id", path=self.path)

        report = wait_registry.doctor(self.path)
        self.assertFalse(report["healthy"])
        self.assertEqual(report["overdue"], [created["wait_id"]])
        self.assertEqual(report["unbound_last_7_days"], 1)
        self.assertEqual(len(report["problems"]), 2)


class DetectorSeamTests(unittest.TestCase):
    """Drive the hook end to end; never write a wait row by hand."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="waits-seam-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "registry.json"
        os.environ["CLAUDE_WAITS_PATH"] = str(self.path)
        self.addCleanup(os.environ.pop, "CLAUDE_WAITS_PATH", None)

        patches = {
            "repo_root": lambda: Path(self.tmp.name),
            "main_repo_name": lambda root=None: "helpful-scripts",
            "current_branch": lambda root=None: "claude/topic",
            "head_commit": lambda root: "c" * 40,
        }
        for name, replacement in patches.items():
            original = getattr(detector, name)
            setattr(detector, name, replacement)
            self.addCleanup(setattr, detector, name, original)

    def run_hook(self, event: dict) -> str:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            original = sys.stdin
            sys.stdin = io.StringIO(json.dumps(event))
            try:
                detector.main()
            finally:
                sys.stdin = original
        return stdout.getvalue()

    def waits(self) -> list[dict]:
        return wait_registry.active(self.path)

    def test_azure_pr_with_no_exit_code_still_registers(self):
        """The exact case that killed the Codex feature.

        Every one of its 581 provider-write events arrived without a parseable
        exit code, and `explicit_success = exit_code == 0` treated unknown as
        failure and returned before detection. Unknown must not mean failed.
        """
        out = self.run_hook(payload("az repos pr create --title x", {"stdout": AZ_PR_JSON}))
        rows = self.waits()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["resource_id"], "4242")
        self.assertEqual(rows[0]["operation_kind"], "pull_request")
        self.assertEqual(rows[0]["provider"], "azure_devops")
        self.assertEqual(rows[0]["branch"], "claude/topic")
        self.assertEqual(rows[0]["commit"], "c" * 40)
        self.assertIn("Wait registered", out)

    def test_poll_hint_names_the_sibling_poller(self):
        """The hooks may run from a release clone, not ~/.claude/hooks."""
        out = self.run_hook(payload("az repos pr create --title x", {"stdout": AZ_PR_JSON}))
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        sibling = Path(detector.__file__).resolve().parent / "wait_poll.py"
        self.assertIn(f'py "{sibling}" poll ', context)

    def test_chained_command_still_registers(self):
        """Codex required the command to be a single segment. This must not."""
        command = "git add -A && git commit -m x && git push && az repos pr create --title x"
        self.run_hook(payload(command, {"stdout": AZ_PR_JSON}))
        self.assertEqual(len(self.waits()), 1)

    def test_azure_scope_flags_are_captured_for_polling(self):
        self.run_hook(
            payload(
                "az repos pr create --organization https://dev.azure.com/o --project P",
                {"stdout": AZ_PR_JSON},
            )
        )
        row = self.waits()[0]
        self.assertEqual(row["organization"], "https://dev.azure.com/o")
        self.assertEqual(row["project"], "P")

    def test_pipeline_run_registers_with_run_id(self):
        self.run_hook(payload("az pipelines run --name ci", {"stdout": AZ_RUN_JSON}))
        row = self.waits()[0]
        self.assertEqual(row["operation_kind"], "pipeline")
        self.assertEqual(row["resource_id"], "991")
        self.assertEqual(row["target_state"], "succeeded")

    def test_github_pr_registers_from_url_output(self):
        self.run_hook(payload("gh pr create --fill", {"stdout": GH_PR_URL}))
        row = self.waits()[0]
        self.assertEqual(row["provider"], "github")
        self.assertEqual(row["resource_id"], "200")
        self.assertEqual(row["repo_slug"], "koala-man-64/helpful-scripts")

    def test_the_binding_comes_from_the_pull_request_not_the_checkout(self):
        """WP4.1: the checkout is on claude/topic; the pull request is from its own branch."""
        created = json.dumps({
            "pullRequestId": 4243,
            "sourceRefName": "refs/heads/claude/other",
            "lastMergeSourceCommit": {"commitId": "e" * 40},
        })
        self.run_hook(payload("az repos pr create --title x", {"stdout": created}))
        row = self.waits()[0]
        self.assertEqual((row["branch"], row["commit"]), ("claude/other", "e" * 40))
        self.run_hook(payload("az repos pr create --source-branch refs/heads/claude/named --title y",
                              {"stdout": json.dumps({"pullRequestId": 4244})}))
        self.assertEqual(wait_registry.active(self.path)[-1]["branch"], "claude/named")
        self.run_hook(payload("gh pr create --head claude/gh-head --fill", {"stdout": GH_PR_URL.replace("200", "201")}))
        self.assertEqual(wait_registry.active(self.path)[-1]["branch"], "claude/gh-head")

    def test_ids_are_read_from_tsv_output_and_pull_request_urls(self):
        self.run_hook(payload("az repos pr create --title x --query pullRequestId -o tsv", {"stdout": "4245\n"}))
        url = "https://dev.azure.com/o/P/_git/repo/pullrequest/4246"
        self.run_hook(payload("az repos pr create --title y", {"stdout": f"Created: {url}"}))
        self.run_hook(payload("az pipelines run --name ci --query id --output tsv", {"stdout": "992"}))
        self.assertEqual(sorted(r["resource_id"] for r in self.waits()), ["4245", "4246", "992"])

    def test_ids_come_from_the_shapes_real_creates_print(self):
        """Each shape is from a real az call in the last week that used to register nothing."""
        api_url = "https://dev.azure.com/o/abc/_apis/git/repositories/def/pullRequests/3538"
        cases = [
            ("az repos pr create --query '{id: pullRequestId, url: url}'", '{"id": 3536, "url": "x"}', "3536"),
            ("az repos pr create --title t", f' "status": "active", "url": "{api_url}", "workItemRefs": null }}', "3538"),
            ("az repos pr create -o table", "pullRequestId    status    title\n---------------  --------  -----\n3603             active    AB#1: x", "3603"),
            ("az repos pr create --query '[pullRequestId,status]' -o tsv", "3620\tactive", "3620"),
            ("az repos pr create --title t", "To https://x\n * [new branch] a -> a\n3627", "3627"),
            ("az pipelines run --id 12 --query '{id:id,v:sourceVersion}' -o tsv", "24193\tnotStarted\t" + "a" * 40, "24193"),
            ("az pipelines run --id 7", "848bbaf4 Merge pull request 3541 from x into main\n24455 notStarted " + "b" * 40, "24455"),
        ]
        for command, output, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(detector.detect(command, output)["resource_id"], expected)

    def test_a_gh_create_whose_body_mentions_az_is_a_github_pr(self):
        """PR #269 itself: its --body quoted `az repos pr create`, and no wait was registered."""
        command = ('gh pr create --base main --head claude/AB3695-wait-ids --title t --body "A replay of '
                   '`az repos pr create` / `az pipelines run` calls"')
        url = "https://github.com/koala-man-64/helpful-scripts/pull/269"
        detected = detector.detect(command, f"remote:\n{url}")
        self.assertEqual((detected["provider"], detected["resource_id"], detected["branch"]),
                         ("github", "269", "claude/AB3695-wait-ids"))

    def test_ambiguous_output_is_not_guessed(self):
        nested = json.dumps({"status": "active", "workItemRefs": [{"id": "3683"}]})
        self.assertEqual(detector.detect("az repos pr create --title t", nested)["resource_id"], "")
        self.assertEqual(detector.detect("az repos pr create --title t", "PR 12 of 40 done")["resource_id"], "")

    def test_an_az_error_is_a_failure_not_an_unbound_success(self):
        out = self.run_hook(payload("az repos pr create --title t", {"stdout": "ERROR: TF401179: An active pull request already exists."}))
        self.assertEqual(out.strip(), "")
        self.assertEqual(wait_registry.load(self.path).get("diagnostics", []), [])

    def test_the_repository_is_the_primary_checkout_not_the_worktree(self):
        self.run_hook(payload("az repos pr create --title x", {"stdout": AZ_PR_JSON}))
        self.assertEqual(self.waits()[0]["repository"], "helpful-scripts")

    def test_observed_failure_registers_nothing(self):
        self.run_hook(payload("az repos pr create", {"isError": True, "stdout": "boom"}))
        self.assertEqual(self.waits(), [])
        self.run_hook(payload("az repos pr create", {"exit_code": 1, "stdout": AZ_PR_JSON}))
        self.assertEqual(self.waits(), [])

    def test_dry_run_registers_nothing(self):
        self.run_hook(payload("az pipelines run --name ci --dry-run", {"stdout": AZ_RUN_JSON}))
        self.assertEqual(self.waits(), [])

    def test_unrelated_command_registers_nothing(self):
        self.run_hook(payload("git status", {"stdout": "clean"}))
        self.assertEqual(self.waits(), [])

    def test_non_shell_tool_is_ignored(self):
        self.run_hook(payload("az repos pr create", {"stdout": AZ_PR_JSON}, tool="Read"))
        self.assertEqual(self.waits(), [])

    def test_missing_resource_id_records_a_diagnostic(self):
        """Codex returned None silently here; that is why its outage was invisible."""
        out = self.run_hook(payload("az repos pr create --title x", {"stdout": "created"}))
        self.assertEqual(self.waits(), [])
        diagnostics = wait_registry.load(self.path)["diagnostics"]
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["code"], "WAIT_TRIGGER_UNBOUND")
        self.assertIn("no resource id", out)

    def test_replayed_event_does_not_duplicate_the_wait(self):
        event = payload("az repos pr create --title x", {"stdout": AZ_PR_JSON})
        self.run_hook(event)
        self.run_hook(event)
        self.assertEqual(len(self.waits()), 1)

    def test_registered_wait_is_visible_to_the_next_session(self):
        """The surfacing path SessionStart depends on."""
        self.run_hook(payload("gh pr create --fill", {"stdout": GH_PR_URL}))
        rows = wait_registry.active(self.path)
        self.assertEqual(len(rows), 1)
        self.assertIn("pull_request 200", wait_registry.describe(rows[0]))


class BindingVerificationTests(unittest.TestCase):
    """The poller must refuse a status whose resource no longer matches."""

    def setUp(self) -> None:
        self.wait = {
            "wait_id": "w" * 24,
            "provider": "github",
            "operation_kind": "pull_request",
            "resource_id": "200",
            "branch": "claude/topic",
            "commit": "c" * 40,
            "repo_slug": "koala-man-64/helpful-scripts",
        }

    def stub(self, payload):
        original = wait_poll.run_json
        wait_poll.run_json = lambda args: payload
        self.addCleanup(setattr, wait_poll, "run_json", original)

    def view(self, **overrides):
        base = {
            "state": "MERGED",
            "headRefName": "claude/topic",
            "headRefOid": "c" * 40,
            "baseRefName": "main",
        }
        base.update(overrides)
        return base

    def test_matching_bindings_resolve_to_success(self):
        self.stub(self.view())
        result = wait_poll.poll_github_pull_request(self.wait)
        self.assertEqual(result["status"], "succeeded")

    def test_retargeted_pull_request_is_a_mismatch_not_a_success(self):
        self.stub(self.view(baseRefName="some-feature-branch"))
        result = wait_poll.poll_github_pull_request(self.wait)
        self.assertEqual(result["status"], "failed")
        self.assertIn("protected_target", result["detail_code"])

    def test_a_new_head_on_the_same_branch_moves_the_binding(self):
        """A pull request's identity is its source branch, which the provider never lets change.

        Review fixes pushed after registration are the same pull request moving
        forward. Failing them was the PR #257 false failure: merged, reported failed.
        """
        self.stub(self.view(headRefOid="d" * 40))
        merged = wait_poll.poll_github_pull_request(self.wait)
        self.assertEqual((merged["status"], merged["rebind_commit"]), ("succeeded", "d" * 40))
        self.stub(self.view(state="OPEN", headRefOid="d" * 40))
        open_pr = wait_poll.poll_github_pull_request(self.wait)
        self.assertEqual((open_pr["status"], open_pr["detail_code"]), ("pending", "head_advanced"))

    def test_a_different_source_branch_is_still_a_mismatch(self):
        self.stub(self.view(headRefName="claude/other"))
        result = wait_poll.poll_github_pull_request(self.wait)
        self.assertEqual(result["status"], "failed")
        self.assertIn("source_branch", result["detail_code"])

    def test_open_pull_request_is_pending(self):
        self.stub(self.view(state="OPEN"))
        self.assertEqual(wait_poll.poll_github_pull_request(self.wait)["status"], "pending")

    def test_closed_pull_request_is_abandoned(self):
        self.stub(self.view(state="CLOSED"))
        self.assertEqual(wait_poll.poll_github_pull_request(self.wait)["status"], "abandoned")

    def test_unreadable_provider_stays_pending_rather_than_failing(self):
        self.stub(None)
        result = wait_poll.poll_github_pull_request(self.wait)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["detail_code"], "provider_unreadable")

    def test_protected_branch_set(self):
        self.assertTrue(wait_poll.is_protected("refs/heads/main"))
        self.assertTrue(wait_poll.is_protected("production"))
        self.assertFalse(wait_poll.is_protected("refs/heads/claude/topic"))

    def test_pending_approval_is_recognised_by_run_id(self):
        approvals = [{"pipeline": {"owner": {"id": "991"}}}]
        self.assertTrue(wait_poll.approval_matches(approvals, "991"))
        self.assertFalse(wait_poll.approval_matches(approvals, "992"))


class PollLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="waits-poll-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "registry.json"
        os.environ["CLAUDE_WAITS_PATH"] = str(self.path)
        self.addCleanup(os.environ.pop, "CLAUDE_WAITS_PATH", None)
        self.wait = wait_registry.register(
            provider="github",
            operation_kind="pull_request",
            resource_id="200",
            repository="helpful-scripts",
            branch="claude/topic",
            commit="c" * 40,
            target_state="merged",
            repo_slug="koala-man-64/helpful-scripts",
        )

    def stub(self, payload):
        original = wait_poll.run_json
        wait_poll.run_json = lambda args: payload
        self.addCleanup(setattr, wait_poll, "run_json", original)

    def test_a_new_head_is_persisted_as_the_binding(self):
        self.stub({"state": "OPEN", "headRefName": "claude/topic", "headRefOid": "d" * 40, "baseRefName": "main"})
        result = wait_poll.poll_one(self.wait["wait_id"])
        stored = wait_registry.get(self.wait["wait_id"])
        self.assertEqual((result["status"], result["detail_code"]), ("pending", "head_advanced"))
        self.assertEqual((stored["commit"], stored["registered_commit"]), ("d" * 40, "c" * 40))

    def test_provider_strings_are_normalized(self):
        other = wait_registry.register(
            provider="Azure-DevOps", operation_kind="pull_request", resource_id="77",
            repository="helpful-scripts", branch="claude/topic", commit="c" * 40, target_state="merged",
        )
        self.assertEqual(other["provider"], "azure_devops")
        self.stub({"status": "completed", "sourceRefName": "refs/heads/claude/topic",
                   "targetRefName": "refs/heads/main", "lastMergeSourceCommit": {"commitId": "c" * 40}})
        self.assertEqual(wait_poll.poll_one(other["wait_id"])["status"], "succeeded")

    def test_reresolve_gives_binding_mismatches_their_true_status(self):
        """The 40 rows that failed only because registration bound the checkout's branch or HEAD."""
        wait_registry.update_status(self.wait["wait_id"], status="failed",
                                    detail_code="binding_mismatch:source_branch,source_commit")
        kept = wait_registry.register(
            provider="github", operation_kind="pull_request", resource_id="201", repository="helpful-scripts",
            branch="claude/x", commit="c" * 40, target_state="merged", repo_slug="koala-man-64/helpful-scripts",
        )
        wait_registry.update_status(kept["wait_id"], status="failed", detail_code="binding_mismatch:protected_target")
        self.stub({"state": "MERGED", "headRefName": "claude/real", "headRefOid": "d" * 40, "baseRefName": "main"})
        results = wait_poll.reresolve()
        self.assertEqual([(r["wait_id"], r["status"]) for r in results], [(self.wait["wait_id"], "succeeded")])
        stored = wait_registry.get(self.wait["wait_id"])
        self.assertEqual((stored["branch"], stored["registered_branch"]), ("claude/real", "claude/topic"))
        self.assertEqual(wait_registry.get(kept["wait_id"])["status"], "failed")

    def test_reresolve_rebinds_a_pipeline_run_to_its_own_source_version(self):
        run = wait_registry.register(
            provider="azure_devops", operation_kind="pipeline", resource_id="991", repository="helpful-scripts",
            branch="claude/topic", commit="c" * 40, target_state="succeeded", project="P",
        )
        wait_registry.update_status(run["wait_id"], status="failed", detail_code="binding_mismatch:source_commit")
        wait_registry.update_status(self.wait["wait_id"], status="succeeded", detail_code="pr_merged")
        self.stub({"status": "completed", "result": "succeeded", "sourceVersion": "f" * 40})
        with unittest.mock.patch.object(wait_poll, "has_pending_approval", return_value=False):
            results = wait_poll.reresolve()
        self.assertEqual([(r["wait_id"], r["status"]) for r in results], [(run["wait_id"], "succeeded")])
        self.assertEqual(wait_registry.get(run["wait_id"])["commit"], "f" * 40)

    def test_session_start_lists_only_this_repositorys_waits(self):
        import session_start_team_context as start

        wait_registry.register(
            provider="github", operation_kind="pull_request", resource_id="300", repository="other-repo",
            branch="b", commit="c" * 40, target_state="merged",
        )
        lines = start.outstanding_waits("helpful-scripts")
        self.assertTrue(any(self.wait["wait_id"] in line for line in lines))
        self.assertFalse(any("other-repo" in line for line in lines))
        self.assertTrue(any("1 more in other repositories" in line for line in lines))
        self.assertIn("in other repositories", start.outstanding_waits("unrelated")[0])

    def test_poll_persists_the_terminal_status(self):
        self.stub(
            {
                "state": "MERGED",
                "headRefName": "claude/topic",
                "headRefOid": "c" * 40,
                "baseRefName": "main",
            }
        )
        result = wait_poll.poll_one(self.wait["wait_id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(wait_registry.active(), [])

    def test_expired_wait_times_out_without_calling_the_provider(self):
        def explode(args):
            raise AssertionError("expired wait must not reach the provider")

        original = wait_poll.run_json
        wait_poll.run_json = explode
        self.addCleanup(setattr, wait_poll, "run_json", original)

        data = wait_registry.load(self.path)
        for row in data["waits"]:
            row["created_at"] = (datetime.now(UTC) - timedelta(hours=200)).isoformat()
        wait_registry.save(data, self.path)

        result = wait_poll.poll_one(self.wait["wait_id"])
        self.assertEqual(result["status"], "timed_out")

    def test_unknown_wait_id_is_an_error_not_a_crash(self):
        self.assertEqual(wait_poll.poll_one("nope")["detail_code"], "unknown_wait")

    def test_already_terminal_wait_is_not_repolled(self):
        wait_registry.update_status(
            self.wait["wait_id"], status="succeeded", detail_code="pr_merged"
        )

        def explode(args):
            raise AssertionError("terminal wait must not reach the provider")

        original = wait_poll.run_json
        wait_poll.run_json = explode
        self.addCleanup(setattr, wait_poll, "run_json", original)
        self.assertEqual(
            wait_poll.poll_one(self.wait["wait_id"])["detail_code"], "already_terminal"
        )


class AuditRegressionTests(unittest.TestCase):
    """One test per defect confirmed by the code-drift and test-adequacy gates.

    Every assertion below failed against the first implementation.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="waits-audit-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "registry.json"
        os.environ["CLAUDE_WAITS_PATH"] = str(self.path)
        self.addCleanup(os.environ.pop, "CLAUDE_WAITS_PATH", None)
        for name, replacement in {
            "repo_root": lambda: Path(self.tmp.name),
            "main_repo_name": lambda root=None: "repo",
            "current_branch": lambda root=None: "claude/topic",
            "head_commit": lambda root: "c" * 40,
        }.items():
            original = getattr(detector, name)
            setattr(detector, name, replacement)
            self.addCleanup(setattr, detector, name, original)

    def run_hook(self, event: dict) -> str:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            original = sys.stdin
            sys.stdin = io.StringIO(json.dumps(event))
            try:
                detector.main()
            finally:
                sys.stdin = original
        return stdout.getvalue()

    def diagnostics(self) -> list[dict]:
        return wait_registry.load(self.path)["diagnostics"]

    def test_failed_command_that_returned_a_resource_id_is_not_silent(self):
        """A reported failure can still have created the resource."""
        out = self.run_hook(
            payload("az repos pr create --title x", {"exit_code": 1, "stdout": AZ_PR_JSON})
        )
        self.assertEqual(wait_registry.active(self.path), [])
        self.assertEqual(len(self.diagnostics()), 1)
        self.assertEqual(self.diagnostics()[0]["code"], "WAIT_OPERATION_FAILED_WITH_RESOURCE")
        self.assertIn("4242", out)

    def test_failed_command_with_no_resource_id_stays_quiet(self):
        self.run_hook(payload("az repos pr create", {"exit_code": 1, "stdout": "denied"}))
        self.assertEqual(wait_registry.active(self.path), [])
        self.assertEqual(self.diagnostics(), [])

    def test_stringified_exit_code_counts_as_failure(self):
        self.assertTrue(detector.observed_failure({"exit_code": "1"}))
        self.assertFalse(detector.observed_failure({"exit_code": "0"}))
        self.assertFalse(detector.observed_failure({"exit_code": "not-a-number"}))

    def test_identifier_comes_from_the_last_payload_not_the_first(self):
        """An earlier chained segment must not capture the wait."""
        noise = json.dumps({"pullRequestId": 111})
        self.run_hook(
            payload(
                "echo decoy && az repos pr create --title x",
                {"stdout": f"{noise}\n{AZ_PR_JSON}"},
            )
        )
        rows = wait_registry.active(self.path)
        self.assertEqual([r["resource_id"] for r in rows], ["4242"])

    def test_unverifiable_commit_never_resolves_a_pipeline_to_succeeded(self):
        """The reproduced false success: an empty commit made the check vacuous.

        A pipeline run is immutable, so its commit binding stays strict. A pull
        request is bound by its source branch and target instead (see
        test_a_new_head_on_the_same_branch_moves_the_binding); a missing commit
        there is filled in from the provider's head.
        """
        wait = {
            "resource_id": "200",
            "branch": "claude/topic",
            "commit": "",
            "repo_slug": "o/r",
            "project": "P",
        }
        original = wait_poll.run_json
        self.addCleanup(setattr, wait_poll, "run_json", original)

        wait_poll.run_json = lambda args: {
            "status": "completed",
            "sourceVersion": "d" * 40,
            "repository": {"id": "x"},
        }
        pipeline = wait_poll.poll_azure_pipeline(wait)
        self.assertNotEqual(pipeline["status"], "succeeded")

        wait_poll.run_json = lambda args: {
            "status": "completed",
            "sourceRefName": "refs/heads/claude/other",
            "targetRefName": "refs/heads/main",
            "lastMergeSourceCommit": {"commitId": "d" * 40},
            "repository": {"id": "x"},
        }
        azure = wait_poll.poll_azure_pull_request(wait)
        self.assertNotEqual(azure["status"], "succeeded")
        self.assertIn("source_branch", azure["detail_code"])

        wait_poll.run_json = lambda args: {
            "status": "completed",
            "sourceRefName": "refs/heads/claude/topic",
            "targetRefName": "refs/heads/feature-x",
            "lastMergeSourceCommit": {"commitId": "d" * 40},
            "repository": {"id": "x"},
        }
        retargeted = wait_poll.poll_azure_pull_request(wait)
        self.assertNotEqual(retargeted["status"], "succeeded")
        self.assertIn("protected_target", retargeted["detail_code"])

    def test_registry_write_failure_does_not_crash_the_hook(self):
        """The hook observes every shell call; it must not take one down."""
        original = wait_registry._write_atomic
        wait_registry._write_atomic = lambda *a, **k: (_ for _ in ()).throw(
            OSError(28, "No space left on device")
        )
        self.addCleanup(setattr, wait_registry, "_write_atomic", original)

        self.assertFalse(wait_registry.save({"waits": [], "diagnostics": []}, self.path))
        registered = wait_registry.register(
            provider="github",
            operation_kind="pull_request",
            resource_id="1",
            repository="r",
            branch="b",
            commit="c" * 40,
            target_state="merged",
            path=self.path,
        )
        self.assertTrue(registered["wait_id"])
        # And the hook itself survives, emitting rather than raising.
        self.run_hook(payload("gh pr create --fill", {"stdout": GH_PR_URL}))

    def test_malformed_payload_does_not_crash_the_hook(self):
        for broken in ({"tool_name": "Bash"}, {"tool_name": "Bash", "tool_input": "oops"}):
            self.run_hook(broken)

    def test_stale_concurrent_write_cannot_revert_a_terminal_failure(self):
        """A proved binding_mismatch must not be overwritten by a stale success.

        poll_one refuses to re-examine a terminal status, so a lost update here
        would be permanent.
        """
        wait = wait_registry.register(
            provider="github",
            operation_kind="pull_request",
            resource_id="7",
            repository="r",
            branch="b",
            commit="c" * 40,
            target_state="merged",
            path=self.path,
        )
        wait_registry.update_status(
            wait["wait_id"],
            status="failed",
            detail_code="binding_mismatch:source_commit",
            path=self.path,
        )
        proved = wait_registry.get(wait["wait_id"], self.path)
        self.assertEqual(proved["status"], "failed")

        # A second process holding a pre-failure snapshot writes succeeded.
        # Give it the newer timestamp, the case recency alone cannot catch.
        stale_success = dict(proved)
        stale_success["status"] = "succeeded"
        stale_success["detail_code"] = "pr_merged"
        stale_success["updated_at"] = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()

        for left, right in ((stale_success, proved), (proved, stale_success)):
            merged = wait_registry._merge_rows([left], [right])
            winner = next(r for r in merged if r["wait_id"] == wait["wait_id"])
            self.assertEqual(winner["status"], "failed", "merge must never upgrade to succeeded")

    def test_interleaved_registration_collapses_to_one_active_wait(self):
        common = dict(
            provider="github",
            operation_kind="pull_request",
            resource_id="9",
            repository="r",
            branch="b",
            commit="c" * 40,
            target_state="merged",
        )
        first = wait_registry.register(**common, path=self.path)
        # A second process that read before the first write committed.
        snapshot = wait_registry.load(self.path)
        snapshot["waits"] = []
        wait_registry.save(snapshot, self.path)
        second = wait_registry.register(**common, path=self.path)
        merged = wait_registry._merge_rows([first], [second])
        active = [r for r in merged if r["status"] in wait_registry.ACTIVE_STATUSES]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["wait_id"], first["wait_id"])

    def test_pattern_inside_another_program_is_not_an_invocation(self):
        """Caught in production by doctor: two false WAIT_TRIGGER_UNBOUND rows.

        The command text merely mentioned the pattern; nothing was created.
        """
        for command in (
            'grep -n "az repos pr create" detector.py',
            "echo 'run az repos pr create next'",
            'python -c "print(\'gh pr create\')"',
            "cat <<EOF\naz repos pr create\nEOF",
        ):
            self.run_hook(payload(command, {"stdout": AZ_PR_JSON}))
        self.assertEqual(wait_registry.active(self.path), [])
        self.assertEqual(self.diagnostics(), [])

    def test_real_invocation_still_detected_in_a_chain(self):
        self.run_hook(
            payload(
                "git push && AZURE_DEVOPS_EXT_PAT=x az repos pr create --title t",
                {"stdout": AZ_PR_JSON},
            )
        )
        self.assertEqual([r["resource_id"] for r in wait_registry.active(self.path)], ["4242"])

    def test_git_helpers_are_bounded(self):
        """A hung git call would block the turn, not just the hook."""
        import hook_utils

        self.assertIsInstance(hook_utils.GIT_TIMEOUT_SECONDS, int)
        self.assertGreater(hook_utils.GIT_TIMEOUT_SECONDS, 0)

        captured = {}
        original = hook_utils.subprocess.run

        def spy(*args, **kwargs):
            captured.update(kwargs)
            return original(*args, **kwargs)

        hook_utils.subprocess.run = spy
        self.addCleanup(setattr, hook_utils.subprocess, "run", original)
        hook_utils.run_git(["rev-parse", "HEAD"], Path(self.tmp.name))
        self.assertEqual(captured.get("timeout"), hook_utils.GIT_TIMEOUT_SECONDS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
