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
import time
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

        self._saved = (gate.LOG_PATH, gate.repo_root, gate.run_git, gate.agent_directories)
        gate.LOG_PATH = self.log
        gate.repo_root = lambda: Path(self.tmp.name)
        self.set_origin(MANAGED)
        # Hermetic definitions: the default child is a delivery engineer at high
        # effort, as the profile ships it, not whatever the host has installed.
        default_agents = Path(self.tmp.name) / "default-agents"
        default_agents.mkdir()
        (default_agents / "delivery-engineer-agent.md").write_text(
            "---\nname: delivery-engineer-agent\ndescription: test\nmodel: sonnet\neffort: high\n---\n",
            encoding="utf-8",
        )
        gate.agent_directories = lambda root: [default_agents]
        for name in ("CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_SUBAGENT_MODEL"):
            saved_env = os.environ.pop(name, None)
            if saved_env is not None:
                self.addCleanup(os.environ.__setitem__, name, saved_env)

        def restore():
            gate.LOG_PATH, gate.repo_root, gate.run_git, gate.agent_directories = self._saved

        self.addCleanup(restore)

    def set_origin(self, url: str) -> None:
        gate.run_git = lambda args, cwd=None: (0, url)

    def transcript(self, parent: str | None, effort: str | None = None) -> str:
        path = Path(self.tmp.name) / f"transcript-{parent}-{effort}.jsonl"
        records = [{"type": "user", "message": {"content": "hi"}}]
        if parent is not None:
            record = {"type": "assistant", "message": {"model": PARENT_MODELS[parent], "content": []}}
            if effort:
                record["effort"] = effort
            records.append(record)
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
        effort="default",
        payload_effort=None,
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
        data = {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "session_id": session_id,
            # Sessions record their effort; xhigh owns every lane.
            "transcript_path": self.transcript(
                self.parent if parent == "default" else parent,
                "xhigh" if effort == "default" else effort,
            ),
        }
        if payload_effort:
            data["effort"] = {"level": payload_effort}
        return data

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


class AgentDefinitionsCase(LadderTestCase):
    """Agent definitions in a temporary directory, with model and effort env cleared.

    ``low-agent``, ``medium-agent``, ``high-agent`` and ``xhigh-agent`` set that
    effort; ``plain-agent`` sets none, so it inherits the session's like a built-in.
    """

    def setUp(self):
        super().setUp()
        self.agents = Path(self.tmp.name) / "agents"
        self.agents.mkdir()
        gate.agent_directories = lambda root: [self.agents]
        self.addCleanup(os.environ.pop, "CLAUDE_CODE_EFFORT_LEVEL", None)
        self.addCleanup(os.environ.pop, "CLAUDE_CODE_SUBAGENT_MODEL", None)
        for effort in ("low", "medium", "high", "xhigh"):
            self.define(f"{effort}-agent", f"effort: {effort}")
        self.define("plain-agent")

    def define(self, name: str, *fields: str) -> None:
        body = ["---", f"name: {name}", "description: test agent", *fields, "---", "", "# body"]
        (self.agents / f"{name}.md").write_text("\n".join(body), encoding="utf-8")

    def spawn(self, lane="critical", tier="sonnet", agent="high-agent", **kwargs):
        body = contract(lane=lane, tier=tier)
        return self.run_gate(self.payload(body, subagent_type=agent, **kwargs))


class LanePermissions(AgentDefinitionsCase):
    def test_lite_lane_has_no_children(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(lane="lite"))), "LANE_LITE_NO_CHILDREN"
        )

    def test_unknown_lane_is_rejected(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(lane="ultra"))), "LANE_UNKNOWN_LANE"
        )

    def test_unknown_tier_is_rejected(self):
        self.assertDenied(
            self.run_gate(self.payload(contract(tier="sol"))), "LANE_UNKNOWN_TIER"
        )
        self.assertDenied(self.spawn(tier="fable", parent="fable"), "LANE_UNKNOWN_TIER")

    def test_standard_lane_children(self):
        """Haiku, Sonnet low or high, Opus low; checked under the weakest standard owner."""
        medium = {"lane": "standard", "effort": "medium"}
        self.assertRouted(self.spawn(tier="haiku", agent="plain-agent", **medium), "haiku")
        self.assertRouted(self.spawn(agent="low-agent", **medium), "sonnet")
        self.assertRouted(self.spawn(agent="high-agent", **medium), "sonnet")
        self.assertRouted(self.spawn(tier="opus", agent="low-agent", **medium), "opus")

    def test_standard_lane_refuses_stronger_opus_even_under_a_strong_owner(self):
        result = self.spawn(lane="standard", tier="opus", agent="medium-agent", effort="max")
        self.assertDenied(result, "LANE_PROFILE_NOT_PERMITTED")
        self.assertIn("only at low effort", result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_critical_lane_takes_opus_up_to_xhigh_below_the_owner(self):
        for agent in ("low-agent", "medium-agent", "high-agent"):
            with self.subTest(agent=agent):
                self.assertRouted(self.spawn(tier="opus", agent=agent), "opus")
        self.assertRouted(self.spawn(tier="opus", agent="xhigh-agent", effort="max"), "opus")


class LaneOwners(AgentDefinitionsCase):
    """A session cannot claim a lane above its own model and effort."""

    def haiku(self, lane, **kwargs):
        return self.spawn(lane=lane, tier="haiku", agent="plain-agent", **kwargs)

    def test_standard_is_owned_from_opus_medium(self):
        self.assertRouted(self.haiku("standard", effort="medium"), "haiku")
        self.assertDenied(self.haiku("standard", effort="low"), "LANE_OWNER_BELOW_LANE")
        self.assertDenied(self.haiku("standard", parent="sonnet", effort="high"), "LANE_OWNER_BELOW_LANE")

    def test_critical_is_owned_from_opus_xhigh(self):
        self.assertDenied(self.haiku("critical", effort="high"), "LANE_OWNER_BELOW_LANE")
        for effort in ("xhigh", "max"):
            with self.subTest(effort=effort):
                self.assertRouted(self.haiku("critical", effort=effort), "haiku")
        self.assertRouted(self.haiku("critical", parent="fable", effort="low"), "haiku")

    def test_unknown_parent_owns_no_lane(self):
        self.assertDenied(self.haiku("standard", parent=None), "LANE_OWNER_BELOW_LANE")
        # An unknown effort reads as the model's weakest, Opus low.
        self.assertDenied(self.haiku("standard", effort=None), "LANE_OWNER_BELOW_LANE")

    def test_denial_asks_for_a_model_switch(self):
        reason = self.haiku("critical", effort="high")["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("opus at xhigh effort or stronger", reason)
        self.assertIn("ask Rudy to switch", reason)


class ParentOrdering(AgentDefinitionsCase):
    """Children rank by model at effort, so an Opus child can sit below an Opus parent."""

    def test_opus_child_at_or_above_the_parent_effort_is_denied(self):
        self.assertDenied(self.spawn(tier="opus", agent="xhigh-agent"), "LANE_CHILD_NOT_LOWER")
        self.assertDenied(self.spawn(tier="opus", agent="plain-agent"), "LANE_CHILD_NOT_LOWER")

    def test_sonnet_high_sits_below_opus_medium(self):
        self.assertRouted(self.spawn(lane="standard", effort="medium"), "sonnet")

    def test_fable_parent_takes_any_permitted_child(self):
        self.assertRouted(self.spawn(tier="opus", agent="xhigh-agent", parent="fable", effort="max"), "opus")

    def test_rank_rule(self):
        rank = agent_ladder.rank_failure
        self.assertIsNone(rank("haiku", "", "sonnet", "low"))
        self.assertEqual(rank("haiku", "", "haiku", "")[0], "LANE_CHILD_NOT_LOWER")
        self.assertIsNone(rank("haiku", "", "", ""))
        self.assertEqual(rank("sonnet", "low", "", "")[0], "LANE_PARENT_UNKNOWN")
        # Parent at unknown effort reads as its weakest; child as its strongest.
        self.assertEqual(rank("sonnet", "high", "opus", "")[0], "LANE_CHILD_NOT_LOWER")
        self.assertEqual(rank("opus", "", "opus", "max")[0], "LANE_CHILD_NOT_LOWER")
        self.assertIsNone(rank("opus", "max", "fable", ""))
        self.assertEqual(rank("fable", "max", "fable", "max")[0], "LANE_CHILD_NOT_LOWER")

    def test_denial_names_both_scores(self):
        reason = agent_ladder.rank_failure("opus", "high", "opus", "high")[1]
        self.assertIn("opus at high effort (score 53.7)", reason)
        self.assertIn("switch models", reason)

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

    def test_switching_lanes_does_not_add_children(self):
        for _ in range(2):
            self.assertRouted(self.run_gate(self.payload(contract(), session_id="shop")), "haiku")
        critical = contract(lane="critical")
        self.assertRouted(self.run_gate(self.payload(critical, session_id="shop")), "haiku")
        result = self.run_gate(self.payload(critical, session_id="shop"))
        self.assertDenied(result, "LANE_CHILD_CAP")
        self.assertIn("used its session budget", result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_denied_spawns_do_not_consume_the_cap(self):
        for _ in range(3):
            self.run_gate(self.payload(contract(tier="opus"), session_id="cap2"))
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

    def test_negative_counts_buy_no_extra_children(self):
        path = gate._children_file("negative")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"standard": -50, "critical": 2}), encoding="utf-8")
        self.assertEqual(gate.claim_child_slot("negative", "standard"), "")
        self.assertEqual(gate.claim_child_slot("negative", "standard"), "session")

    def test_a_legacy_per_lane_count_file_is_read(self):
        path = gate._children_file("legacy")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"standard": 2}), encoding="utf-8")
        self.assertEqual(gate.claim_child_slot("legacy", "standard"), "lane")
        self.assertEqual(gate.claim_child_slot("legacy", "critical"), "")
        self.assertEqual(gate.claim_child_slot("legacy", "critical"), "session")

    def test_parallel_claims_never_exceed_the_cap(self):
        import threading

        results: list[str] = []
        barrier = threading.Barrier(12)

        def claim():
            barrier.wait()
            results.append(gate.claim_child_slot("parallel", "critical"))

        threads = [threading.Thread(target=claim) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count(""), 3)
        self.assertEqual(results.count("lane"), 9)

    def test_a_stale_lock_is_broken(self):
        path = gate._children_file("stale")
        lock = path.with_name(path.name + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("", encoding="utf-8")
        old = time.time() - gate.LOCK_STALE_SECONDS - 5
        os.utime(lock, (old, old))
        self.assertEqual(gate.claim_child_slot("stale", "standard"), "")
        self.assertFalse(lock.exists())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["standard"], 1)

    def test_a_held_lock_fails_open_without_counting(self):
        path = gate._children_file("held")
        lock = path.with_name(path.name + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("", encoding="utf-8")
        saved = gate.LOCK_WAIT_SECONDS
        gate.LOCK_WAIT_SECONDS = 0.05
        self.addCleanup(setattr, gate, "LOCK_WAIT_SECONDS", saved)
        self.assertEqual(gate.claim_child_slot("held", "standard"), "")
        self.assertFalse(path.exists())
        self.assertTrue(lock.exists(), "another hook's lock is never removed while fresh")


class EffortProfiles(AgentDefinitionsCase):
    """A child's capability is its model at its effort; beaten pairs are refused."""

    def test_session_profile_reads_model_and_effort(self):
        self.assertEqual(agent_ladder.session_profile(self.transcript("opus", "xhigh")), ("opus", "xhigh"))
        self.assertEqual(agent_ladder.session_profile(self.transcript("opus")), ("opus", ""))
        self.assertEqual(agent_ladder.session_profile(self.transcript("opus", "turbo")), ("opus", ""))
        self.assertEqual(agent_ladder.session_profile(None), ("", ""))

    def test_inherited_medium_lands_sonnet_on_a_beaten_pair(self):
        result = self.spawn(lane="standard", agent="plain-agent", effort="medium")
        self.assertDenied(result, "LANE_BEATEN_PROFILE")
        self.assertIn("'opus' at low", result["hookSpecificOutput"]["permissionDecisionReason"])

    def test_sonnet_xhigh_and_max_are_beaten(self):
        for effort in ("xhigh", "max"):
            with self.subTest(effort=effort):
                self.assertDenied(self.spawn(agent="plain-agent", effort=effort), "LANE_BEATEN_PROFILE")

    def test_sonnet_low_and_high_route(self):
        for agent in ("low-agent", "high-agent"):
            with self.subTest(agent=agent):
                self.assertRouted(self.spawn(agent=agent), "sonnet")

    def test_definition_effort_overrides_the_session(self):
        self.assertRouted(self.spawn(lane="standard", effort="medium"), "sonnet")
        entry = self.log_entries()[-1]
        self.assertEqual((entry["effort"], entry["effort_source"]), ("high", "definition"))

    def test_payload_effort_wins_over_the_transcript(self):
        standard = {"lane": "standard", "agent": "plain-agent"}
        self.assertRouted(self.spawn(effort="medium", payload_effort="high", **standard), "sonnet")
        self.assertDenied(self.spawn(effort="high", payload_effort="medium", **standard), "LANE_BEATEN_PROFILE")

    def test_environment_effort_overrides_the_definition(self):
        os.environ["CLAUDE_CODE_EFFORT_LEVEL"] = "max"
        self.assertDenied(self.spawn(), "LANE_BEATEN_PROFILE")
        self.assertEqual(self.log_entries()[-1]["tier"], "sonnet")

    def test_unknown_effort_is_not_called_beaten(self):
        self.assertIsNone(agent_ladder.beaten_failure("sonnet", ""))

    def test_haiku_is_never_beaten(self):
        self.assertRouted(
            self.spawn(lane="standard", tier="haiku", agent="plain-agent", effort="medium"), "haiku"
        )

    def test_every_beaten_pair_has_a_cheaper_stronger_alternative(self):
        for pair, better in agent_ladder.BEATEN_PROFILES.items():
            with self.subTest(pair=pair):
                self.assertGreaterEqual(agent_ladder.PROFILE_SCORES[better], agent_ladder.PROFILE_SCORES[pair])

    def test_unknown_effort_scores_as_the_strongest(self):
        self.assertEqual(agent_ladder.profile_score("opus", ""), 57.6)
        self.assertEqual(agent_ladder.profile_score("opus", "low"), 42.3)
        self.assertIsNone(agent_ladder.profile_score("haiku", "low"))

    def test_every_lane_child_profile_is_scored_and_unbeaten(self):
        for lane, profiles in agent_ladder.LANE_CHILD_PROFILES.items():
            for tier, efforts in profiles.items():
                for effort in efforts or ():
                    with self.subTest(lane=lane, tier=tier, effort=effort):
                        self.assertIn((tier, effort), agent_ladder.PROFILE_SCORES)
                        self.assertNotIn((tier, effort), agent_ladder.BEATEN_PROFILES)


class UnmanagedRules(AgentDefinitionsCase):
    """Outside managed repositories: beaten pairs and child-below-parent only, no envelope."""

    def setUp(self):
        super().setUp()
        self.set_origin(UNMANAGED)

    def spawn(self, subagent_type="general-purpose", model=None, **kwargs):
        return self.run_gate(self.payload(subagent_type=subagent_type, model=model, **kwargs))

    def test_no_envelope_rewrite_or_cap(self):
        for _ in range(5):
            self.assertIsNone(self.spawn(model="haiku", session_id="many"))

    def test_inheriting_built_in_is_not_below_its_parent(self):
        self.assertDenied(self.spawn(), "LANE_CHILD_NOT_LOWER")
        self.assertDenied(self.spawn(subagent_type=None), "LANE_CHILD_NOT_LOWER")

    def test_explicit_lower_model_passes(self):
        # The child inherits the session's high: Sonnet high (46.8) below Opus high (53.7).
        self.assertIsNone(self.spawn(model="sonnet", payload_effort="high"))

    def test_beaten_pair_is_denied(self):
        self.assertDenied(self.spawn(model="sonnet", effort="medium"), "LANE_BEATEN_PROFILE")

    def test_definition_model_and_effort_apply(self):
        self.define("reviewer", "model: opus", "effort: low")
        self.assertIsNone(self.spawn(subagent_type="reviewer"))
        self.define("pinned", "model: opus")
        self.assertDenied(self.spawn(subagent_type="pinned"), "LANE_CHILD_NOT_LOWER")

    def test_explicit_model_beats_the_definition(self):
        self.define("pinned", "model: opus")
        self.assertIsNone(self.spawn(subagent_type="pinned", model="haiku"))

    def test_environment_subagent_model_applies_after_the_definition(self):
        os.environ["CLAUDE_CODE_SUBAGENT_MODEL"] = "claude-haiku-4-5-20251001"
        self.assertIsNone(self.spawn())
        self.define("pinned", "model: opus")
        self.assertDenied(self.spawn(subagent_type="pinned"), "LANE_CHILD_NOT_LOWER")

    def test_fable_is_never_a_child(self):
        self.assertDenied(self.spawn(model="fable", parent="fable"), "LANE_CHILD_NOT_LOWER")

    def test_unresolved_model_is_let_through_and_logged(self):
        self.assertIsNone(self.spawn(model="gpt-6"))
        self.assertEqual(self.log_entries()[-1]["reason_code"], "LANE_MODEL_UNRESOLVED")

    def test_nested_spawns_are_left_to_the_depth_limit(self):
        data = self.payload(subagent_type="general-purpose")
        data["agent_id"] = "child-1"
        self.assertIsNone(self.run_gate(data))

    def test_denial_carries_no_envelope_guidance(self):
        reason = self.spawn()["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertNotIn(agent_ladder.ENVELOPE_TAG, reason)


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
        self.assertDenied(
            self.run_gate(self.payload(contract(), subagent_type="Fork")),
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

    def test_explicit_model_matches_by_family(self):
        for model in ("Haiku", "claude-haiku-4-5-20251001"):
            with self.subTest(model=model):
                self.assertRouted(self.run_gate(self.payload(contract(tier="haiku"), model=model)), "haiku")

    def test_read_only_contract_requires_a_read_only_agent(self):
        body = contract(constraints=["Read-only investigation"])
        self.assertDenied(
            self.run_gate(self.payload(body, subagent_type="delivery-engineer-agent")),
            "LANE_READONLY_AGENT_VIOLATION",
        )

    def test_read_only_contract_accepts_explore(self):
        body = contract(constraints=["Read-only investigation"])
        self.assertRouted(self.run_gate(self.payload(body, subagent_type="Explore")), "haiku")

    def test_read_only_agent_names_match_without_case(self):
        body = contract(constraints=["Read-only investigation"])
        self.assertRouted(self.run_gate(self.payload(body, subagent_type="explore")), "haiku")

    def test_main_thread_only_agents_are_not_spawned(self):
        for name in sorted(agent_ladder.MAIN_THREAD_ONLY_AGENTS):
            with self.subTest(agent=name):
                self.assertDenied(self.run_gate(self.payload(contract(), subagent_type=name)), "LANE_MAIN_THREAD_ONLY")


class ReadOnlyFromFrontmatter(LadderTestCase):
    """Whether an agent can write is read from its definition, not a hardcoded list."""

    def setUp(self):
        super().setUp()
        self.project = Path(self.tmp.name) / "project-agents"
        self.user = Path(self.tmp.name) / "user-agents"
        self.project.mkdir()
        self.user.mkdir()
        saved = gate.agent_directories
        gate.agent_directories = lambda root: [self.project, self.user]
        self.addCleanup(setattr, gate, "agent_directories", saved)

    def define(self, directory: Path, name: str, *fields: str) -> None:
        body = "\n".join(["---", f"name: {name}", "description: test agent", *fields, "---", "", "# body"])
        (directory / f"{name}.md").write_text(body, encoding="utf-8")

    def spawn_read_only(self, name: str):
        return self.run_gate(self.payload(contract(constraints=["Read-only review"]), subagent_type=name))

    def test_a_reviewer_without_write_tools_takes_a_read_only_contract(self):
        self.define(self.user, "db-steward", "disallowedTools: Agent, Edit, Write, NotebookEdit, MultiEdit")
        self.assertRouted(self.spawn_read_only("db-steward"), "haiku")

    def test_a_writer_is_refused_a_read_only_contract(self):
        self.define(self.user, "delivery-engineer-agent", "disallowedTools: Agent")
        self.assertDenied(self.spawn_read_only("delivery-engineer-agent"), "LANE_READONLY_AGENT_VIOLATION")

    def test_a_tools_allowlist_without_write_tools_is_read_only(self):
        self.define(self.user, "scanner", "tools: Read, Grep, Glob, Bash")
        self.assertRouted(self.spawn_read_only("scanner"), "haiku")

    def test_yaml_list_frontmatter_is_read(self):
        self.define(self.user, "lister", "disallowedTools:", "  - Agent", "  - Edit", "  - Write", "  - NotebookEdit", "  - MultiEdit")
        self.assertRouted(self.spawn_read_only("lister"), "haiku")

    def test_a_project_definition_shadows_the_user_one(self):
        self.define(self.project, "qa-release-gate-agent", "disallowedTools: Agent")
        self.define(self.user, "qa-release-gate-agent", "disallowedTools: Agent, Edit, Write, NotebookEdit, MultiEdit")
        self.assertDenied(self.spawn_read_only("qa-release-gate-agent"), "LANE_READONLY_AGENT_VIOLATION")

    def test_undefined_agents_are_not_read_only(self):
        self.assertDenied(self.spawn_read_only("no-such-agent"), "LANE_READONLY_AGENT_VIOLATION")

    def test_ambiguous_or_wildcard_definitions_fail_closed(self):
        """From the independent review: each of these is a writer the parser must not call read-only."""
        writers = {
            "star": ['tools: "*"'],
            "mcp-wildcard": ["tools: Read, mcp__files__*"],
            "quoted-block-edit": ["tools:", "  - Read", '  - "Edit"', "  - Bash"],
            "comment-after-edit": ["tools: Read, Grep, Edit  # for doc fixes"],
            "multiedit": ["tools: Read, MultiEdit"],
            "wrong-case-key": ["DisallowedTools: Agent, Edit, Write, NotebookEdit, MultiEdit"],
        }
        for name, fields in writers.items():
            self.define(self.user, name, *fields)
        for name in writers:
            with self.subTest(agent=name):
                self.assertDenied(self.spawn_read_only(name), "LANE_READONLY_AGENT_VIOLATION")
        # Ambiguous definitions are refused before any contract is read.
        self.define(self.user, "twice", "disallowedTools: Agent, Edit, Write, NotebookEdit, MultiEdit", "disallowedTools: Agent")
        (self.user / "unclosed.md").write_text(
            "---\nname: unclosed\ndisallowedTools: Agent, Edit, Write, NotebookEdit, MultiEdit\n\n# body without a closing fence\n",
            encoding="utf-8",
        )
        for name in ("twice", "unclosed"):
            with self.subTest(agent=name):
                self.assertDenied(self.spawn_read_only(name), "LANE_AGENT_DEFINITION_UNREADABLE")

    def test_quoted_and_commented_read_only_lists_are_read(self):
        self.define(self.user, "quoted", "disallowedTools:", '  - "Agent"', "  - 'Edit'", '  - "Write"', "  - NotebookEdit", "  - MultiEdit")
        self.define(self.user, "commented", "disallowedTools: Agent, Edit, Write, NotebookEdit, MultiEdit  # reviewer")
        for name in ("quoted", "commented"):
            with self.subTest(agent=name):
                self.assertRouted(self.spawn_read_only(name), "haiku")

    def test_multiedit_and_mcp_tools_count_as_writing(self):
        self.define(self.user, "no-multiedit", "disallowedTools: Agent, Edit, Write, NotebookEdit")
        self.define(self.user, "mcp-writer", "tools: Read, Grep, mcp__github__create_or_update_file")
        for name in ("no-multiedit", "mcp-writer"):
            with self.subTest(agent=name):
                self.assertDenied(self.spawn_read_only(name), "LANE_READONLY_AGENT_VIOLATION")

    def test_an_apostrophe_does_not_hide_a_comment(self):
        self.define(self.user, "apostrophe", "note: it's the reviewer # legacy", "disallowedTools: Agent, Edit, Write, NotebookEdit, MultiEdit # it's read-only")
        self.assertRouted(self.spawn_read_only("apostrophe"), "haiku")

    def test_unreadable_definitions_are_not_spawned(self):
        (self.user / "broken.md").write_text(
            "---\nname: broken\nmainThreadOnly: true\n\n# no closing fence\n", encoding="utf-8"
        )
        self.define(self.user, "doubled", "mainThreadOnly: true", "tools: Read", "tools: Read, Edit")
        for name in ("broken", "doubled"):
            with self.subTest(agent=name):
                self.assertDenied(self.run_gate(self.payload(contract(), subagent_type=name)), "LANE_AGENT_DEFINITION_UNREADABLE")

    def test_main_thread_only_follows_the_definition_marker(self):
        self.define(self.user, "renamed-steward", "mainThreadOnly: true", "disallowedTools: Agent")
        for name in ("renamed-steward", "Merge-Steward"):
            with self.subTest(agent=name):
                self.assertDenied(self.run_gate(self.payload(contract(), subagent_type=name)), "LANE_MAIN_THREAD_ONLY")


class ProfileAgentFrontmatter(unittest.TestCase):
    """Every spawnable profile agent names its model, caps its turns, and cannot spawn."""

    AGENTS = Path(__file__).resolve().parent.parent / "agents"

    def test_spawnable_agents_carry_model_turns_and_no_agent_tool(self):
        if not self.AGENTS.is_dir():
            # The release clone is a sparse checkout of hooks/ only; the profile is checked in the repo.
            self.skipTest("profile agents are not checked out here (sparse release clone)")
        definitions = sorted(self.AGENTS.glob("*.md"))
        self.assertTrue(definitions)
        for path in definitions:
            fields = agent_ladder.agent_frontmatter(path)
            name = str(fields.get("name") or path.stem)
            with self.subTest(agent=name):
                self.assertTrue(fields, "frontmatter must parse (closed fence, one tool list)")
                if name in agent_ladder.MAIN_THREAD_ONLY_AGENTS:
                    self.assertEqual(fields.get("mainThreadOnly"), "true")
                    continue
                self.assertIn(fields.get("model"), {"haiku", "sonnet"})
                # Effort is the only effort control a spawn has. Low and high are
                # the efforts where Sonnet is not beaten on score and cost.
                self.assertIn(fields.get("effort"), {"low", "high"})
                self.assertRegex(str(fields.get("maxTurns") or ""), r"^\d+$")
                self.assertGreaterEqual(int(str(fields["maxTurns"])), 60)
                disallowed = set(fields.get("disallowedTools") or [])
                self.assertIn("Agent", disallowed)
                # A reviewer loses every write tool, never only some of them.
                if disallowed & agent_ladder.WRITE_TOOLS:
                    self.assertLessEqual(agent_ladder.WRITE_TOOLS, disallowed)


class Scope(LadderTestCase):
    def test_unmanaged_repository_needs_no_envelope(self):
        self.set_origin(UNMANAGED)
        self.assertIsNone(self.run_gate(self.payload(subagent_type="Explore", model="haiku")))
        self.assertEqual([e["scope"] for e in self.log_entries()], ["unmanaged"])

    def test_repository_without_a_remote_is_untouched(self):
        gate.run_git = lambda args, cwd=None: (1, "")
        self.assertIsNone(self.run_gate(self.payload()))

    def use_repositories(self, managed: Path, unmanaged: Path) -> None:
        """Fake git: two repositories, identified by the directory git runs in."""
        managed.mkdir()
        unmanaged.mkdir()

        def run_git(args, cwd=None):
            where = Path(cwd) if cwd else Path(self.tmp.name)
            if args[:2] == ["rev-parse", "--show-toplevel"]:
                return 0, str(where)
            return 0, MANAGED if where == managed else UNMANAGED

        gate.run_git = run_git

    def test_the_payload_cwd_decides_the_repository(self):
        """A session started outside a managed repository is gated once it works in one."""
        managed, unmanaged = Path(self.tmp.name) / "managed", Path(self.tmp.name) / "elsewhere"
        self.use_repositories(managed, unmanaged)
        gate.repo_root = lambda: unmanaged
        data = self.payload()
        data["cwd"] = str(managed)
        self.assertDenied(self.run_gate(data), "LANE_MISSING_ENVELOPE")

    def test_a_cd_out_of_a_managed_repository_does_not_escape_its_gate(self):
        """The process directory is managed, the payload cwd is not: still gated."""
        managed, unmanaged = Path(self.tmp.name) / "managed", Path(self.tmp.name) / "elsewhere"
        self.use_repositories(managed, unmanaged)
        gate.repo_root = lambda: managed
        data = self.payload()
        data["cwd"] = str(unmanaged)
        self.assertDenied(self.run_gate(data), "LANE_MISSING_ENVELOPE")

    def test_a_missing_cwd_falls_back_to_the_hook_directory(self):
        data = self.payload()
        data["cwd"] = str(Path(self.tmp.name) / "gone")
        self.assertDenied(self.run_gate(data), "LANE_MISSING_ENVELOPE")

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
                "parent_effort",
                "effort",
                "effort_source",
                "selection_source",
                "subagent_type",
                "reason_code",
            },
        )

    def test_a_long_subagent_type_is_cut_in_the_log(self):
        self.run_gate(self.payload(contract(), subagent_type="x" * 500))
        self.assertEqual(len(self.log_entries()[-1]["subagent_type"]), gate.SUBAGENT_TYPE_LOG_CHARS)

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
