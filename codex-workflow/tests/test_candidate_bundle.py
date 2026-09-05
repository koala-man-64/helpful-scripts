from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from render_candidate_bundle import (  # noqa: E402
    candidate_source_digest,
    validate_candidate_bundle,
)
from validate_catalog import (  # noqa: E402
    REPOSITORIES,
    parse_repository_mappings,
    validate_candidate_sources,
)
from validate_consumer_candidate import validate_consumer  # noqa: E402


def parse_installed_contract(release: Path, contract: dict) -> dict:
    """Isolate installed imports and verify that the release was not altered."""
    def inventory() -> dict[str, str]:
        return {
            path.relative_to(release).as_posix(): (
                "directory" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
            ) for path in release.rglob("*")
        }

    before = inventory()
    program = """
import json
import sys
from pathlib import Path

release = Path(sys.argv[1])
contract = json.loads(sys.argv[2])
previous = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    sys.path.insert(0, str(release / 'src'))
    from codex_workflow_hooks import subagent_routing
    policy = json.loads((release / 'policies' / 'global.json').read_text())
    message = '<codex_subagent_task_v2>' + json.dumps(contract) + '</codex_subagent_task_v2>'
    parsed = subagent_routing._contract_from_message(message, policy['subagent_routing']['task_contract'])
    print(json.dumps(parsed))
finally:
    sys.dont_write_bytecode = previous
"""
    try:
        result = subprocess.run(
            [sys.executable, "-B", "-c", program, str(release), json.dumps(contract)],
            capture_output=True, text=True, check=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    finally:
        if inventory() != before:
            raise AssertionError("installed release inventory or content changed during interoperability check")
    return json.loads(result.stdout)


class CandidateBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.output = ROOT / "candidates" / "outputs"
        self.bundle = json.loads((self.output / "candidate-bundle.json").read_text())

    def test_disabled_bundle_has_all_fifteen_bound_locks(self) -> None:
        validate_candidate_bundle(ROOT, self.output, self.bundle)
        self.assertFalse(self.bundle["activation"])
        self.assertFalse(self.bundle["readiness"])
        self.assertEqual(self.bundle["tested_commit_scope"], "helpful-scripts-catalog-base")
        self.assertEqual(len(self.bundle["repositories"]), 5)
        self.assertEqual(sum(len(item["locks"]) for item in self.bundle["repositories"]), 15)
        self.assertEqual(self.bundle["bundle_digest"], candidate_source_digest(ROOT))

    def test_forged_readiness_or_lock_digest_is_rejected(self) -> None:
        forged = copy.deepcopy(self.bundle)
        forged["readiness"] = True
        with self.assertRaisesRegex(ValueError, "activation contract"):
            validate_candidate_bundle(ROOT, self.output, forged)

    def test_shared_consumer_validation_checks_three_exact_locks(self) -> None:
        for repository in sorted(REPOSITORIES):
            with self.subTest(repository=repository):
                result = validate_consumer(ROOT, self.output, repository, self.output / "locks" / repository)
                self.assertEqual(set(result["locks"]), {"lite", "standard", "critical"})
                self.assertFalse(result["readiness"])
                self.assertEqual(result["runtime_consumption"], "unverified")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            repository = sorted(REPOSITORIES)[0]
            for lane in ("lite", "standard", "critical"):
                (directory / f"{lane}.json").write_bytes((self.output / "locks" / repository / f"{lane}.json").read_bytes())
            lock = directory / "standard.json"
            altered = json.loads(lock.read_bytes())
            altered["lane_execution_plan"]["children"][0]["effort"] = "low"
            lock.write_text(json.dumps(altered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs from pinned standard"):
                validate_consumer(ROOT, self.output, repository, directory)

    def test_release_binding_must_match_policy_observation(self) -> None:
        forged = copy.deepcopy(self.bundle)
        forged["release_digest"] = "sha256:" + "1" * 64
        with self.assertRaisesRegex(ValueError, "central policy observation"):
            validate_candidate_bundle(ROOT, self.output, forged)
        forged = copy.deepcopy(self.bundle)
        forged["repositories"][0]["locks"]["lite"]["sha256"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "content digest"):
            validate_candidate_bundle(ROOT, self.output, forged)

    def test_incomplete_lane_set_and_incomplete_origins_are_rejected(self) -> None:
        forged = copy.deepcopy(self.bundle)
        del forged["repositories"][0]["locks"]["standard"]
        with self.assertRaisesRegex(ValueError, "lane coverage"):
            validate_candidate_bundle(ROOT, self.output, forged)
        with self.assertRaisesRegex(ValueError, "exactly one mapping"):
            parse_repository_mappings(
                ["asset-allocation-contracts=C:\\only"], require_complete=True
            )
        complete = parse_repository_mappings(
            [f"{repository}=C:\\{repository}" for repository in REPOSITORIES],
            require_complete=True,
        )
        self.assertEqual(set(complete), REPOSITORIES)

    def test_source_triggers_references_and_contract_parser_are_resolvable(self) -> None:
        errors: list[str] = []
        validate_candidate_sources(ROOT, errors)
        self.assertEqual(errors, [])
        policy = json.loads((ROOT / "candidates" / "sources" / "selection-policy.json").read_text())
        self.assertEqual(
            {item["trigger"] for item in policy["skills"]},
            {"rendered_or_interactive_state_material", "explicit_git_hygiene_or_cleanup"},
        )
        for reference in policy["language_references"]:
            self.assertTrue((ROOT / "candidates" / reference["path"]).is_file())

    @unittest.skipUnless(os.environ.get("CODEX_WORKFLOW_HOOKS_RELEASE"), "set explicit hooks release for v2 interoperability")
    def test_actual_hook_v2_parser_accepts_candidate_handoff(self) -> None:
        release = Path(os.environ["CODEX_WORKFLOW_HOOKS_RELEASE"]).resolve(strict=True)
        contract = {
            "tier": "terra",
            "objective": "Validate one bounded candidate contract.",
            "scope": ["candidate source only"],
            "acceptance_checks": ["real parser accepts the tagged JSON"],
            "constraints": ["no installed configuration mutation"],
            "decomposition_attempted": True,
            "delegation": {
                "owner_tier": "leaf",
                "role": "bounded_leaf",
                "child_count": 1,
                "child_index": 1,
            },
        }
        self.assertEqual(parse_installed_contract(release, contract), contract)

    def test_installed_import_is_isolated_and_creates_no_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release = Path(temporary)
            package = release / "src" / "codex_workflow_hooks"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "subagent_routing.py").write_text(
                "import json\ndef _contract_from_message(message, policy):\n"
                "    return json.loads(message.split('>', 1)[1].rsplit('<', 1)[0])\n",
                encoding="utf-8",
            )
            policies = release / "policies"
            policies.mkdir()
            (policies / "global.json").write_text(
                json.dumps({"subagent_routing": {"task_contract": {}}}), encoding="utf-8",
            )
            def hook_modules() -> dict:
                return {name: module for name, module in sys.modules.items()
                        if name == "codex_workflow_hooks" or name.startswith("codex_workflow_hooks.")}

            before_modules = hook_modules()
            before_path = list(sys.path)
            self.assertEqual(parse_installed_contract(release, {"tier": "terra"}), {"tier": "terra"})
            self.assertEqual(list(release.rglob("__pycache__")), [])
            self.assertEqual(sys.path, before_path)
            self.assertEqual(hook_modules(), before_modules)


if __name__ == "__main__":
    unittest.main()
