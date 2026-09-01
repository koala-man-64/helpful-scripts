"""Tests for the ordered subagent model ladder gate.

Run from this directory:

    py -3 -m unittest test_agent_ladder -v
"""

from __future__ import annotations

import contextlib
import io
import json
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


def contract(**overrides):
    base = {
        "tier": "haiku",
        "objective": "Rename the stale feature flag",
        "scope": ["src/app.py"],
        "acceptance_checks": ["pytest tests/test_app.py passes"],
        "constraints": [],
        "decomposition_attempted": True,
        "lower_tier_blockers": {},
    }
    base.update(overrides)
    return base


def envelope(body: str, tail: str = "Do the thing.") -> str:
    return f"<{TAG}>\n{body}\n</{TAG}>\n\n{tail}"


def payload(
    body=None,
    subagent_type="delivery-engineer-agent",
    model=None,
    tool_name="Agent",
    raw_prompt=None,
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
    return {"tool_name": tool_name, "tool_input": tool_input}


class LadderTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "agent-ladder.jsonl"

        self._saved = (gate.LOG_PATH, gate.repo_root, gate.run_git)
        gate.LOG_PATH = self.log
        gate.repo_root = lambda: Path(self.tmp.name)
        self.set_origin(MANAGED)

        def restore():
            gate.LOG_PATH, gate.repo_root, gate.run_git = self._saved

        self.addCleanup(restore)

    def set_origin(self, url: str) -> None:
        gate.run_git = lambda args, cwd=None: (0, url)

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


class TierRouting(LadderTestCase):
    def test_haiku_needs_no_blockers(self):
        result = self.run_gate(payload(contract()))
        self.assertRouted(result, "haiku")

    def test_sonnet_routes_when_haiku_is_blocked(self):
        body = contract(
            tier="sonnet",
            lower_tier_blockers={"haiku": "Root cause spans three modules"},
        )
        self.assertRouted(self.run_gate(payload(body)), "sonnet")

    def test_opus_routes_when_both_lower_tiers_are_blocked(self):
        body = contract(
            tier="opus",
            lower_tier_blockers={
                "haiku": "Not a single precise edit",
                "sonnet": "Changes a production migration boundary",
            },
        )
        self.assertRouted(self.run_gate(payload(body)), "opus")

    def test_every_tier_maps_to_its_own_model(self):
        self.assertEqual(
            [agent_ladder.TIER_MODEL[t] for t in agent_ladder.TIER_ORDER],
            ["haiku", "sonnet", "opus"],
        )

    def test_envelope_is_stripped_from_the_delivered_prompt(self):
        hook = self.assertRouted(self.run_gate(payload(contract())), "haiku")
        prompt = hook["updatedInput"]["prompt"]
        self.assertEqual(prompt, "Do the thing.")
        self.assertNotIn(TAG, prompt)

    def test_unrelated_input_fields_survive_the_rewrite(self):
        data = payload(contract())
        data["tool_input"]["run_in_background"] = True
        hook = self.assertRouted(self.run_gate(data), "haiku")
        self.assertTrue(hook["updatedInput"]["run_in_background"])
        self.assertEqual(hook["updatedInput"]["subagent_type"], "delivery-engineer-agent")


class OrderEnforcement(LadderTestCase):
    def test_sonnet_without_haiku_blocker_is_rejected(self):
        body = contract(tier="sonnet", lower_tier_blockers={})
        self.assertDenied(self.run_gate(payload(body)), "LADDER_MISSING_BLOCKER")

    def test_opus_skipping_sonnet_is_rejected(self):
        body = contract(
            tier="opus", lower_tier_blockers={"haiku": "Too broad for one edit"}
        )
        result = self.run_gate(payload(body))
        self.assertDenied(result, "LADDER_MISSING_BLOCKER")
        self.assertIn("sonnet", result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_blank_blocker_text_does_not_satisfy_the_gate(self):
        body = contract(tier="sonnet", lower_tier_blockers={"haiku": "   "})
        self.assertDenied(self.run_gate(payload(body)), "LADDER_MISSING_BLOCKER")

    def test_unknown_tier_is_rejected(self):
        self.assertDenied(
            self.run_gate(payload(contract(tier="sol"))), "LADDER_UNKNOWN_TIER"
        )


class ContractShape(LadderTestCase):
    def test_missing_envelope_is_rejected(self):
        self.assertDenied(self.run_gate(payload()), "LADDER_MISSING_ENVELOPE")

    def test_malformed_json_is_rejected(self):
        self.assertDenied(
            self.run_gate(payload("{not json,,,}")), "LADDER_MALFORMED_ENVELOPE"
        )

    def test_non_object_envelope_is_rejected(self):
        self.assertDenied(
            self.run_gate(payload('["a list"]')), "LADDER_MALFORMED_ENVELOPE"
        )

    def test_envelope_must_lead_the_prompt(self):
        trailing = "Some preamble first.\n" + envelope(json.dumps(contract()))
        self.assertDenied(
            self.run_gate(payload(raw_prompt=trailing)), "LADDER_MISSING_ENVELOPE"
        )

    def test_empty_scope_is_rejected(self):
        self.assertDenied(
            self.run_gate(payload(contract(scope=[]))), "LADDER_EMPTY_SCOPE"
        )

    def test_whitespace_scope_is_rejected(self):
        self.assertDenied(
            self.run_gate(payload(contract(scope=["  "]))), "LADDER_EMPTY_SCOPE"
        )

    def test_missing_acceptance_checks_is_rejected(self):
        self.assertDenied(
            self.run_gate(payload(contract(acceptance_checks=[]))),
            "LADDER_MISSING_ACCEPTANCE",
        )

    def test_undeclared_decomposition_is_rejected(self):
        self.assertDenied(
            self.run_gate(payload(contract(decomposition_attempted=False))),
            "LADDER_NO_DECOMPOSITION",
        )

    def test_unresolved_dependencies_are_rejected(self):
        body = contract(depends_on=["the other leaf must land first"])
        self.assertDenied(self.run_gate(payload(body)), "LADDER_UNRESOLVED_DEPENDENCY")


class SpawnShape(LadderTestCase):
    def test_full_history_fork_is_rejected(self):
        self.assertDenied(
            self.run_gate(payload(contract(), subagent_type="fork")),
            "LADDER_FULL_HISTORY_FORK",
        )

    def test_omitted_subagent_type_is_rejected(self):
        self.assertDenied(
            self.run_gate(payload(contract(), subagent_type=None)),
            "LADDER_FULL_HISTORY_FORK",
        )

    def test_explicit_model_conflicting_with_tier_is_rejected(self):
        self.assertDenied(
            self.run_gate(payload(contract(tier="haiku"), model="opus")),
            "LADDER_MODEL_CONFLICT",
        )

    def test_explicit_model_agreeing_with_tier_is_allowed(self):
        result = self.run_gate(payload(contract(tier="haiku"), model="haiku"))
        self.assertRouted(result, "haiku")
        self.assertEqual(self.log_entries()[-1]["selection_source"], "explicit_model")

    def test_read_only_contract_requires_a_read_only_agent(self):
        body = contract(constraints=["Read-only investigation"])
        self.assertDenied(
            self.run_gate(payload(body, subagent_type="delivery-engineer-agent")),
            "LADDER_READONLY_TIER_VIOLATION",
        )

    def test_read_only_contract_accepts_explore(self):
        body = contract(constraints=["Read-only investigation"])
        self.assertRouted(self.run_gate(payload(body, subagent_type="Explore")), "haiku")


class NoSilentPromotion(LadderTestCase):
    def test_a_rejected_spawn_is_never_rewritten_upward(self):
        body = contract(tier="sonnet", lower_tier_blockers={})
        result = self.run_gate(payload(body))
        self.assertDenied(result, "LADDER_MISSING_BLOCKER")
        # The gate refuses; it does not quietly hand the work to a bigger model.
        self.assertEqual(
            [e["decision"] for e in self.log_entries()], ["denied"]
        )

    def test_a_documented_lower_tier_failure_permits_the_higher_tier(self):
        first = contract()
        self.assertRouted(self.run_gate(payload(first)), "haiku")

        retry = contract(
            tier="sonnet",
            lower_tier_blockers={
                "haiku": "haiku attempt failed: the flag is read in two modules"
            },
        )
        self.assertRouted(self.run_gate(payload(retry)), "sonnet")

        decisions = [e["decision"] for e in self.log_entries()]
        self.assertEqual(decisions, ["routed", "routed"])


class Scope(LadderTestCase):
    def test_unmanaged_repository_is_untouched(self):
        self.set_origin(UNMANAGED)
        self.assertIsNone(self.run_gate(payload()))
        self.assertEqual(self.log_entries(), [])

    def test_repository_without_a_remote_is_untouched(self):
        gate.run_git = lambda args, cwd=None: (1, "")
        self.assertIsNone(self.run_gate(payload()))

    def test_non_subagent_tools_are_untouched(self):
        self.assertIsNone(self.run_gate(payload(contract(), tool_name="Bash")))

    def test_task_alias_is_gated_like_agent(self):
        self.assertRouted(
            self.run_gate(payload(contract(), tool_name="Task")), "haiku"
        )

    def test_ssh_origin_resolves_to_the_same_managed_repository(self):
        self.set_origin(
            "git@ssh.dev.azure.com:v3/rdprokes/AdaptiveAssetAllocation/asset-allocation-jobs"
        )
        self.assertRouted(self.run_gate(payload(contract())), "haiku")

    def test_dot_git_suffix_and_case_do_not_defeat_matching(self):
        self.set_origin(
            "https://dev.azure.com/rdprokes/AdaptiveAssetAllocation/_git/Asset-Allocation-UI.git"
        )
        self.assertRouted(self.run_gate(payload(contract())), "haiku")

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
        self.assertRouted(self.run_gate(payload(contract())), "haiku")
        self.assertDenied(self.run_gate(payload()), "LADDER_MISSING_ENVELOPE")


class Redaction(LadderTestCase):
    SECRETS = (
        "Rename the stale feature flag",  # objective
        "src/app.py",  # scope
        "pytest tests/test_app.py passes",  # acceptance check
        "Do the thing.",  # prompt tail
        "test task",  # description
    )

    def test_routed_records_carry_no_task_text(self):
        self.run_gate(payload(contract()))
        blob = self.log.read_text()
        for secret in self.SECRETS:
            self.assertNotIn(secret, blob)

    def test_denied_records_carry_no_task_text(self):
        self.run_gate(payload(contract(tier="opus")))
        blob = self.log.read_text()
        for secret in self.SECRETS:
            self.assertNotIn(secret, blob)

    def test_records_hold_only_routing_facts(self):
        self.run_gate(payload(contract()))
        entry = self.log_entries()[-1]
        self.assertEqual(
            set(entry),
            {
                "ts",
                "repository",
                "decision",
                "tier",
                "model",
                "selection_source",
                "subagent_type",
                "reason_code",
            },
        )

    def test_log_is_bounded(self):
        gate.LOG_MAX_LINES = 5
        self.addCleanup(setattr, gate, "LOG_MAX_LINES", 2000)
        for _ in range(12):
            self.run_gate(payload(contract()))
        self.assertLessEqual(len(self.log_entries()), 5)

    def test_logging_failure_does_not_decide_the_spawn(self):
        gate.LOG_PATH = Path(self.tmp.name) / "nope" / "\0bad" / "x.jsonl"
        self.assertRouted(self.run_gate(payload(contract())), "haiku")


if __name__ == "__main__":
    unittest.main(verbosity=2)
