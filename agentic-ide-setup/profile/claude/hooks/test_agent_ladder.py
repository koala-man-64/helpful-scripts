"""Tests for the lane-based subagent routing gate.

Run from this directory:

    py -3 -m pytest test_agent_ladder.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_ladder  # noqa: E402
import pre_tool_use_agent_ladder as gate  # noqa: E402

MANAGED = "https://dev.azure.com/rdprokes/AdaptiveAssetAllocation/_git/asset-allocation-jobs"
UNMANAGED = "https://github.com/rdprokes/some-other-thing"

TAG = agent_ladder.ENVELOPE_TAG

PARENT_MODELS = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
    "fable": "claude-fable-5-1",
}


def contract(**overrides):
    base = {
        "lane": "standard",
        "tier": "haiku",
        "objective": "Rename the stale feature flag",
        "scope": ["src/app.py"],
        "acceptance_checks": ["pytest tests/test_app.py passes"],
        "constraints": [],
        "routing_reason": "bounded mechanical rename",
    }
    base.update(overrides)
    return base


def envelope(body: str, tail: str = "Do the thing.", tag: str = TAG) -> str:
    return f"<{tag}>\n{body}\n</{tag}>\n\n{tail}"


class LadderTestCase(unittest.TestCase):
    parent = "opus"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "agent-ladder.jsonl"
        os.environ["CLAUDE_SESSION_FLAGS_DIR"] = str(Path(self.tmp.name) / "flags")
        self.addCleanup(os.environ.pop, "CLAUDE_SESSION_FLAGS_DIR", None)
        self.session_counter = 0

        self._saved = (gate.LOG_PATH, gate.repo_root, gate.run_git)
        gate.LOG_PATH = self.log
        gate.repo_root = lambda: Path(self.tmp.name)
        self.set_origin(MANAGED)

        def restore():
            gate.LOG_PATH, gate.repo_root, gate.run_git = self._saved

        self.addCleanup(restore)

    def set_origin(self, url: str) -> None:
        gate.run_git = lambda args, cwd=None: (0, url)

    def transcript(self, parent: str | None) -> str:
        path = Path(self.tmp.name) / f"transcript-{parent}.jsonl"
        records = [{"type": "user", "message": {"content": "hi"}}]
        if parent is not None:
            records.append(
                {"type": "assistant", "message": {"model": PARENT_MODELS[parent], "content": []}}
            )
        records.append(
            {"type": "assistant", "message": {"model": "<synthetic>", "content": []}}
        )
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        return str(path)

    def payload(
        self,
        body=None,
        subagent_type="delivery-engineer-agent",
        model=None,
        tool_name="Agent",
        raw_prompt=None,
        parent="default",
        session_id=None,
    ):
        if raw_prompt is not None:
            prompt = raw_prompt
        elif body is None:
            prompt = "Just do this, no contract."
        else:
            prompt = envelope(body if isinstance(body, str) else json.dumps(body))
        tool_input = {"prompt": prompt, "description": "test task"}
        if subagent_type is not None:
            tool_input["subagent_type"] = subagent_type
        if model:
            tool_input["model"] = model
        if session_id is None:
            # Fresh session per payload unless a test is exercising the cap.
            self.session_counter += 1
            session_id = f"sess-{self.session_counter}"
        return {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "session_id": session_id,
            "transcript_path": self.transcript(self.parent if parent == "default" else parent),
        }

    def run_gate(self, data):
        stdin = io.StringIO(json.dumps(data))
        out = io.StringIO()
        saved_stdin = sys.stdin
        sys.stdin = stdin
        try:
            with contextlib.redirect_stdout(out):
                code = gate.main()
        finally:
            sys.stdin = saved_stdin
        self.assertEqual(code, 0)
        text = out.getvalue().strip()
        return json.loads(text) if text else None

    def assertDenied(self, result, reason_code):
        self.assertIsNotNone(result, "expected a denial, got no hook output")
        hook = result["hookSpecificOutput"]
        self.assertEqual(hook["permissionDecision"], "deny")
        self.assertIn(reason_code, hook["permissionDecisionReason"])
        self.assertNotIn("updatedInput", hook)
        # Guidance never reintroduces the cumulative ladder.
        self.assertNotIn("lower_tier_blockers", hook["permissionDecisionReason"])

    def assertRouted(self, result, model):
        self.assertIsNotNone(result, "expected a rewrite, got no hook output")
        hook = result["hookSpecificOutput"]
        # updatedInput only applies when the hook declines to decide permission.
        self.assertNotIn("permissionDecision", hook)
        self.assertEqual(hook["updatedInput"]["model"], model)
        return hook

    def log_entries(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]


class DirectSelection(LadderTestCase):
    def test_standard_haiku_routes(self):
        self.assertRouted(self.run_gate(self.payload(contract())), "haiku")

    def test_critical_sonnet_routes_without_any_blockers(self):
        body = contract(lane="critical", tier="sonnet")
        self.assertRouted(self.run_gate(self.payload(body)), "sonnet")

    def test_legacy_blocker_fields_are_ignored_not_required(self):
        body = contract(
            lane="critical", tier="sonnet", lower_tier_blockers={}, decomposition_attempted=False
        )
        self.assertRouted(self.run_gate(self.payload(body)), "sonnet")

    def test_v1_envelope_without_lane_gets_a_lane_error(self):
        body = {k: v for k, v in contract().items() if k != "lane"}
        prompt = envelope(json.dumps(body), tag="claude_subagent_task_v1")
        self.assertDenied(
            self.run_gate(self.payload(raw_prompt=prompt)), "LANE_UNKNOWN_LANE"
        )

    def test_envelope_is_stripped_from_the_delivered_prompt(self):
        hook = self.assertRouted(self.run_gate(self.payload(contract())), "haiku")
        prompt = hook["updatedInput"]["prompt"]
        self.assertEqual(prompt, "Do the thing.")
        self.assertNotIn(TAG, prompt)

    def test_unrelated_input_fields_survive_the_rewrite(self):
        data = self.payload(contract())
        data["tool_input"]["run_in_background"] = True
        hook = self.assertRouted(self.run_gate(data), "haiku")
        self.assertTrue(hook["updatedInput"]["run_in_background"])
        self.assertEqual(hook["updatedInput"]["subagent_type"], "delivery-engineer-agent")


class LanePermissions(LadderTestCase):
    def test_lite_lane_has_no_children(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(lane="lite"))), "LANE_LITE_NO_CHILDREN"
        )

    def test_standard_lane_does_not_permit_sonnet(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(tier="sonnet"))), "LANE_TIER_NOT_PERMITTED"
        )

    def test_opus_is_never_a_child(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(lane="critical", tier="opus"))),
            "LANE_TIER_NOT_PERMITTED",
        )

    def test_unknown_lane_is_rejected(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(lane="ultra"))), "LANE_UNKNOWN_LANE"
        )

    def test_unknown_tier_is_rejected(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(tier="sol"))), "LANE_UNKNOWN_TIER"
        )


class ParentOrdering(LadderTestCase):
    def test_sonnet_parent_cannot_spawn_sonnet(self):
        body = contract(lane="critical", tier="sonnet")
        result = self.run_gate(self.payload(body, parent="sonnet"))
        self.assertDenied(result, "LANE_CHILD_NOT_LOWER")
        self.assertIn("switch models", result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_sonnet_parent_can_spawn_haiku(self):
        self.assertRouted(self.run_gate(self.payload(contract(), parent="sonnet")), "haiku")

    def test_haiku_parent_cannot_spawn_haiku(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(), parent="haiku")), "LANE_CHILD_NOT_LOWER"
        )

    def test_unknown_parent_caps_at_haiku(self):
        body = contract(lane="critical", tier="sonnet")
        self.assertDenied(self.run_gate(self.payload(body, parent=None)), "LANE_PARENT_UNKNOWN")
        self.assertRouted(self.run_gate(self.payload(contract(), parent=None)), "haiku")

    def test_fable_parent_ranks_above_opus(self):
        body = contract(lane="critical", tier="sonnet")
        self.assertRouted(self.run_gate(self.payload(body, parent="fable")), "sonnet")
        self.assertRouted(self.run_gate(self.payload(contract(), parent="fable")), "haiku")

    def test_fable_parent_still_obeys_lane_and_child_rules(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(tier="sonnet"), parent="fable")),
            "LANE_TIER_NOT_PERMITTED",
        )
        self.assertDenied(
            self.run_gate(self.payload(contract(lane="critical", tier="opus"), parent="fable")),
            "LANE_TIER_NOT_PERMITTED",
        )
        self.assertDenied(
            self.run_gate(self.payload(contract(lane="critical", tier="fable"), parent="fable")),
            "LANE_UNKNOWN_TIER",
        )

    def test_nested_spawn_is_rejected(self):
        data = self.payload(contract())
        data["agent_id"] = "child-1"
        self.assertDenied(self.run_gate(data), "LANE_NESTED_SPAWN")

    def test_model_family_mapping(self):
        self.assertEqual(agent_ladder.model_family("claude-opus-5"), "opus")
        self.assertEqual(agent_ladder.model_family("claude-haiku-4-5-20251001"), "haiku")
        self.assertEqual(agent_ladder.model_family("claude-fable-5-1"), "fable")
        self.assertEqual(agent_ladder.model_family("claude-unknown-9"), "")
        self.assertEqual(agent_ladder.model_family(None), "")


class ChildCap(LadderTestCase):
    def test_standard_allows_two_children_per_session(self):
        for _ in range(2):
            self.assertRouted(
                self.run_gate(self.payload(contract(), session_id="cap")), "haiku"
            )
        self.assertDenied(
            self.run_gate(self.payload(contract(), session_id="cap")), "LANE_CHILD_CAP"
        )

    def test_critical_allows_three_children_per_session(self):
        body = contract(lane="critical", tier="sonnet")
        for _ in range(3):
            self.assertRouted(self.run_gate(self.payload(body, session_id="crit")), "sonnet")
        self.assertDenied(self.run_gate(self.payload(body, session_id="crit")), "LANE_CHILD_CAP")

    def test_denied_spawns_do_not_consume_the_cap(self):
        for _ in range(3):
            self.run_gate(self.payload(contract(tier="sonnet"), session_id="cap2"))
        for _ in range(2):
            self.assertRouted(
                self.run_gate(self.payload(contract(), session_id="cap2")), "haiku"
            )

    def test_cap_survives_session_flag_clear(self):
        import hook_utils

        for _ in range(2):
            self.run_gate(self.payload(contract(), session_id="compact"))
        hook_utils.clear_session_flags("compact")
        self.assertDenied(
            self.run_gate(self.payload(contract(), session_id="compact")), "LANE_CHILD_CAP"
        )


class ContractShape(LadderTestCase):
    def test_missing_envelope_is_rejected(self):
        self.assertDenied(self.run_gate(self.payload()), "LANE_MISSING_ENVELOPE")

    def test_malformed_json_is_rejected(self):
        self.assertDenied(
            self.run_gate(self.payload("{not json,,,}")), "LANE_MALFORMED_ENVELOPE"
        )

    def test_non_object_envelope_is_rejected(self):
        self.assertDenied(
            self.run_gate(self.payload('["a list"]')), "LANE_MALFORMED_ENVELOPE"
        )

    def test_envelope_must_lead_the_prompt(self):
        trailing = "Some preamble first.\n" + envelope(json.dumps(contract()))
        self.assertDenied(
            self.run_gate(self.payload(raw_prompt=trailing)), "LANE_MISSING_ENVELOPE"
        )

    def test_empty_scope_is_rejected(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(scope=[]))), "LANE_EMPTY_SCOPE"
        )

    def test_whitespace_scope_is_rejected(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(scope=["  "]))), "LANE_EMPTY_SCOPE"
        )

    def test_missing_acceptance_checks_is_rejected(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(acceptance_checks=[]))),
            "LANE_MISSING_ACCEPTANCE",
        )

    def test_unresolved_dependencies_are_rejected(self):
        body = contract(depends_on=["the other leaf must land first"])
        self.assertDenied(self.run_gate(self.payload(body)), "LANE_UNRESOLVED_DEPENDENCY")


class SpawnShape(LadderTestCase):
    def test_full_history_fork_is_rejected(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(), subagent_type="fork")),
            "LANE_FULL_HISTORY_FORK",
        )

    def test_omitted_subagent_type_is_rejected(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(), subagent_type=None)),
            "LANE_FULL_HISTORY_FORK",
        )

    def test_explicit_model_conflicting_with_tier_is_rejected(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(tier="haiku"), model="sonnet")),
            "LANE_MODEL_CONFLICT",
        )

    def test_explicit_model_agreeing_with_tier_is_allowed(self):
        result = self.run_gate(self.payload(contract(tier="haiku"), model="haiku"))
        self.assertRouted(result, "haiku")
        self.assertEqual(self.log_entries()[-1]["selection_source"], "explicit_model")

    def test_read_only_contract_requires_a_read_only_agent(self):
        body = contract(constraints=["Read-only investigation"])
        self.assertDenied(
            self.run_gate(self.payload(body, subagent_type="delivery-engineer-agent")),
            "LANE_READONLY_AGENT_VIOLATION",
        )

    def test_read_only_contract_accepts_explore(self):
        body = contract(constraints=["Read-only investigation"])
        self.assertRouted(self.run_gate(self.payload(body, subagent_type="Explore")), "haiku")


class Scope(LadderTestCase):
    def test_unmanaged_repository_is_untouched(self):
        self.set_origin(UNMANAGED)
        self.assertIsNone(self.run_gate(self.payload()))
        self.assertEqual(self.log_entries(), [])

    def test_repository_without_a_remote_is_untouched(self):
        gate.run_git = lambda args, cwd=None: (1, "")
        self.assertIsNone(self.run_gate(self.payload()))

    def test_non_subagent_tools_are_untouched(self):
        self.assertIsNone(self.run_gate(self.payload(contract(), tool_name="Bash")))

    def test_task_alias_is_gated_like_agent(self):
        self.assertRouted(
            self.run_gate(self.payload(contract(), tool_name="Task")), "haiku"
        )

    def test_ssh_origin_resolves_to_the_same_managed_repository(self):
        self.set_origin(
            "git@ssh.dev.azure.com:v3/rdprokes/AdaptiveAssetAllocation/asset-allocation-jobs"
        )
        self.assertRouted(self.run_gate(self.payload(contract())), "haiku")

    def test_dot_git_suffix_and_case_do_not_defeat_matching(self):
        self.set_origin(
            "https://dev.azure.com/rdprokes/AdaptiveAssetAllocation/_git/Asset-Allocation-UI.git"
        )
        self.assertRouted(self.run_gate(self.payload(contract())), "haiku")

    def test_every_managed_origin_is_recognised(self):
        for origin in agent_ladder.MANAGED_ORIGINS:
            self.assertEqual(agent_ladder.normalize_origin(origin), origin)

    def test_the_seven_expected_repositories_are_managed(self):
        self.assertEqual(
            sorted(o.rsplit("/", 1)[-1] for o in agent_ladder.MANAGED_ORIGINS),
            [
                "asset-allocation-contracts",
                "asset-allocation-control-plane",
                "asset-allocation-infra",
                "asset-allocation-jobs",
                "asset-allocation-runtime-common",
                "asset-allocation-ui",
                "codex-workflow-hooks",
            ],
        )

    def test_infra_is_gated(self):
        self.set_origin(
            "https://dev.azure.com/rdprokes/AdaptiveAssetAllocation/_git/asset-allocation-infra"
        )
        self.assertRouted(self.run_gate(self.payload(contract())), "haiku")
        self.assertDenied(self.run_gate(self.payload()), "LANE_MISSING_ENVELOPE")


class Redaction(LadderTestCase):
    SECRETS = (
        "Rename the stale feature flag",  # objective
        "src/app.py",  # scope
        "pytest tests/test_app.py passes",  # acceptance check
        "bounded mechanical rename",  # routing reason
        "Do the thing.",  # prompt tail
        "test task",  # description
    )

    def test_routed_records_carry_no_task_text(self):
        self.run_gate(self.payload(contract()))
        blob = self.log.read_text()
        for secret in self.SECRETS:
            self.assertNotIn(secret, blob)

    def test_denied_records_carry_no_task_text(self):
        self.run_gate(self.payload(contract(tier="opus")))
        blob = self.log.read_text()
        for secret in self.SECRETS:
            self.assertNotIn(secret, blob)

    def test_records_hold_only_routing_facts(self):
        self.run_gate(self.payload(contract()))
        entry = self.log_entries()[-1]
        self.assertEqual(
            set(entry),
            {
                "ts",
                "repository",
                "decision",
                "lane",
                "tier",
                "model",
                "parent_tier",
                "selection_source",
                "subagent_type",
                "reason_code",
            },
        )

    def test_log_is_bounded(self):
        gate.LOG_MAX_LINES = 5
        self.addCleanup(setattr, gate, "LOG_MAX_LINES", 2000)
        for _ in range(12):
            self.run_gate(self.payload(contract()))
        self.assertLessEqual(len(self.log_entries()), 5)

    def test_logging_failure_does_not_decide_the_spawn(self):
        gate.LOG_PATH = Path(self.tmp.name) / "nope" / "\0bad" / "x.jsonl"
        self.assertRouted(self.run_gate(self.payload(contract())), "haiku")


if __name__ == "__main__":
    unittest.main(verbosity=2)
