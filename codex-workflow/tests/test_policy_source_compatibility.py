from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OBSERVATION = ROOT / "candidates" / "central-policy-source-observation.json"
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
from render_consumer_lock import render  # noqa: E402
from validate_catalog import REPOSITORIES  # noqa: E402
SOURCE_COMMIT = "08a2073c0651699454f9e1f793e9c3c438f0195a"
RAW_POLICY_SHA256 = "fec04455a30971ebebb963aa7028623c4c93c2355df604e6680a44ba5e65aaf3"
CANONICAL_POLICY_SHA256 = "c5184cc0b5ca087d7d384f48690c705679316237ccf9b9fa9287c3cd2c08b392"
INSTALLED_MANIFEST_SHA256 = "3d5df72bae74736007a27b385dcaebb04e89db020a0747b638d83d69a1e04451"
INSTALLED_POLICY_SHA256 = "c8370b86982c00b0292b6bb1f1b61ba4d0dad7b4f6034f6ed5ab423b94075961"
MODEL_NAMES = {
    "Luna": "gpt-5.6-luna",
    "Terra": "gpt-5.6-terra",
    "Sol": "gpt-5.6-sol",
}


def source_worktree() -> Path | None:
    configured = os.environ.get("CODEX_WORKFLOW_HOOKS_SOURCE")
    if not configured:
        return None
    source = Path(configured).resolve()
    return source if (source / ".git").exists() else None


def source_results(source: Path) -> dict[str, object]:
    archive = subprocess.run(
        ["git", "-C", str(source), "archive", "--format=tar", SOURCE_COMMIT],
        capture_output=True,
        check=True,
    ).stdout
    with tempfile.TemporaryDirectory() as directory:
        extracted = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for member in tar.getmembers():
                destination = (extracted / member.name).resolve()
                if not destination.is_relative_to(extracted) or not (member.isfile() or member.isdir()):
                    raise AssertionError("source archive contains an unsafe member")
                tar.extract(member, extracted, filter="data")
        program = r'''
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
from codex_workflow_hooks.models import EventEnvelope
from codex_workflow_hooks.subagent_routing import evaluate_subagent_route, validate_subagent_routing_policy

policy = json.loads((root / "policies" / "global.json").read_text(encoding="utf-8"))
routing = policy["subagent_routing"]
validate_subagent_routing_policy(routing)
base_contract = {
    "objective": "Validate one bounded source-only candidate handoff.",
    "scope": ["candidate source only"],
    "acceptance_checks": ["the pinned source parser accepts the v2 tag"],
    "constraints": ["no installed configuration mutation"],
    "decomposition_attempted": True,
    "delegation": {"owner_tier": "leaf", "role": "bounded_leaf", "child_count": 1, "child_index": 1},
}
def evaluate(parent_model, parent_effort, tier, owner_tier, role, child_count=1, child_index=1):
    contract = {**base_contract, "tier": tier, "delegation": {"owner_tier": owner_tier, "role": role, "child_count": child_count, "child_index": child_index}}
    tool_input = {"fork_turns": "none", "message": "<codex_subagent_task_v2>" + json.dumps(contract) + "</codex_subagent_task_v2>"}
    event = EventEnvelope("source-test", Path("."), "PreToolUse", model=parent_model, reasoning_effort=parent_effort, tool_input=tool_input)
    decision, rewritten = evaluate_subagent_route(event, policy, mode="enforce", require_contract=True)
    return {"reason_code": decision.reason_code, "deny": decision.deny, "rewritten": rewritten}
print(json.dumps({
    "standard_child": evaluate("gpt-5.6-terra", "medium", "lite", "standard", "focused_qa"),
    "standard_optional_child": evaluate("gpt-5.6-terra", "medium", "lite", "standard", "necessary_specialist", 2, 2),
    "critical_child": evaluate("gpt-5.6-sol", "high", "standard", "critical", "bounded_specialist"),
}))
'''
        result = subprocess.run(
            [sys.executable, "-B", "-c", program, str(extracted)],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if list(extracted.rglob("__pycache__")):
            raise AssertionError("source compatibility import created bytecode")
        return json.loads(result.stdout)


class PolicySourceCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.observation = json.loads(OBSERVATION.read_text(encoding="utf-8"))

    def test_source_observation_is_historical_and_not_ready(self) -> None:
        self.assertEqual(self.observation["schema_version"], "central-policy-source-observation-v1")
        self.assertEqual(self.observation["candidate_installation"], "not_installed")
        self.assertFalse(self.observation["readiness"]["ready"])
        self.assertEqual(self.observation["source"]["commit"], SOURCE_COMMIT)
        self.assertEqual(self.observation["source"]["status"], "historical_source_snapshot_not_install_identity")
        self.assertEqual(self.observation["observed_release"]["manifest_sha256"], "sha256:" + INSTALLED_MANIFEST_SHA256)
        self.assertEqual(self.observation["observed_release"]["policy_raw_bytes_sha256"], "sha256:" + INSTALLED_POLICY_SHA256)
        self.assertEqual(
            self.observation["user_validation"]["status"],
            "owner_confirmed_not_independently_rechecked",
        )
        self.assertIn("per-route admission", self.observation["readiness"]["reason"])

    def test_observation_describes_all_actual_legacy_lock_pairs(self) -> None:
        actual: set[tuple[str, str, str, str]] = set()
        for repository in REPOSITORIES:
            for lane in ("lite", "standard", "critical"):
                lock = render(ROOT, repository, lane, validate_catalog=False)
                plan = lock["lane_execution_plan"]
                actual.add((lock["lane"], "owner", MODEL_NAMES[plan["owner"]["model"]], plan["owner"]["effort"]))
                for child in plan["children"]:
                    actual.add((lock["lane"], "child", MODEL_NAMES[child["model"]], child["effort"]))
        observed = {
            (item["lane"], item["participant"], item["model"], item["reasoning_effort"])
            for item in self.observation["legacy_lane_pair_observations"]
        }
        self.assertEqual(observed, actual)
        children = [
            item for item in self.observation["legacy_lane_pair_observations"]
            if item["participant"] == "child"
        ]
        self.assertEqual(len(children), 2)
        self.assertTrue(all("source_evaluator_accepts" in item["compatibility"] for item in children))

    def test_all_fifteen_rendered_lane_plans_use_v4_child_pairs(self) -> None:
        expected = {
            "lite": [],
            "standard": [
                {"model": "Luna", "effort": "max", "role": "focused_qa", "required": True},
                {"model": "Luna", "effort": "max", "role": "necessary_specialist", "required": False},
            ],
            "critical": [
                {"model": "Terra", "effort": "high", "role": "bounded_specialist", "required": True}
            ],
        }
        count = 0
        for repository in sorted(REPOSITORIES):
            for lane, children in expected.items():
                lock = render(ROOT, repository, lane, validate_catalog=False)
                self.assertEqual(lock["lane_execution_plan"]["children"], children)
                count += 1
        self.assertEqual(count, 15)

    @unittest.skipUnless(os.environ.get("CODEX_WORKFLOW_HOOKS_SOURCE"), "set hooks source worktree")
    def test_immutable_source_policy_hashes_and_v2_compatibility(self) -> None:
        source = source_worktree()
        self.assertIsNotNone(source)
        raw = subprocess.run(
            ["git", "-C", str(source), "show", f"{SOURCE_COMMIT}:policies/global.json"],
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(hashlib.sha256(raw).hexdigest(), RAW_POLICY_SHA256)
        policy = json.loads(raw)
        canonical = json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), CANONICAL_POLICY_SHA256)
        results = source_results(source)
        self.assertEqual(results["standard_child"]["reason_code"], "SUBAGENT_ROUTE_SELECTED")
        self.assertEqual(results["standard_child"]["rewritten"]["model"], "gpt-5.6-luna")
        self.assertEqual(results["standard_child"]["rewritten"]["reasoning_effort"], "max")
        self.assertEqual(results["standard_optional_child"]["reason_code"], "SUBAGENT_ROUTE_SELECTED")
        self.assertEqual(results["standard_optional_child"]["rewritten"]["reasoning_effort"], "max")
        self.assertEqual(results["critical_child"]["reason_code"], "SUBAGENT_ROUTE_SELECTED")
        self.assertEqual(results["critical_child"]["rewritten"]["model"], "gpt-5.6-terra")
        self.assertEqual(results["critical_child"]["rewritten"]["reasoning_effort"], "high")

    @unittest.skipUnless(os.environ.get("CODEX_WORKFLOW_HOOKS_RELEASE"), "set installed hooks release")
    def test_observed_installed_release_bytes_match_without_importing_modules(self) -> None:
        release = Path(os.environ["CODEX_WORKFLOW_HOOKS_RELEASE"]).resolve(strict=True)
        manifest = (release / "manifest.json").read_bytes()
        policy = (release / "policies" / "global.json").read_bytes()
        self.assertEqual(hashlib.sha256(manifest).hexdigest(), INSTALLED_MANIFEST_SHA256)
        self.assertEqual(hashlib.sha256(policy).hexdigest(), INSTALLED_POLICY_SHA256)
        self.assertEqual(hashlib.sha256(policy.replace(b"\r\n", b"\n")).hexdigest(), RAW_POLICY_SHA256)
        self.assertEqual(
            json.loads(policy)["subagent_routing"]["task_contract"]["message_tag"],
            "codex_subagent_task_v2",
        )


if __name__ == "__main__":
    unittest.main()
