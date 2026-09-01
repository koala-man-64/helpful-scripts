from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_inventory import build_inventory  # noqa: E402
from catalog_lib import load_document, validate_schema  # noqa: E402
from render_consumer_lock import render  # noqa: E402
from validate_catalog import validate  # noqa: E402


REPOSITORIES = (
    "asset-allocation-contracts",
    "asset-allocation-control-plane",
    "asset-allocation-jobs",
    "asset-allocation-runtime-common",
    "asset-allocation-ui",
)
ACTIVE_SKILLS = {
    "workflow-router",
    "delivery-engineer-agent",
    "qa-release-gate-agent",
    "forensic-debugger",
    "azure-devops-cicd-expert",
    "cloud-security-vulnerability-expert",
    "db-steward",
    "project-workflow-enforcer-agent",
    "audit-workitem-for-spark",
    "runtime-ownership-enforcer",
}
UNRESOLVED_FORKS = {
    "code-drift-sentinel",
    "data-engineer-data-architect-advisor",
    "delivery-orchestrator-agent",
    "git-hygiene-orchestrator",
    "project-workflow-auditor-agent",
    "provisioning-configuration-and-disaster-recovery-expert",
}


class CatalogTests(unittest.TestCase):
    def test_catalog_and_five_routing_scenarios_are_valid(self) -> None:
        self.assertEqual(validate(ROOT), [])
        scenarios = load_document(ROOT / "catalog" / "routing-scenarios.yaml")[
            "scenarios"
        ]
        self.assertEqual(len(scenarios), 5)
        self.assertEqual(scenarios[0]["primary_route"]["model"], "Luna")
        self.assertEqual(scenarios[1]["minimum_agents"], 2)
        self.assertTrue(
            all(
                item["minimum_agents"] == 1 + len(item["child_routes"])
                for item in scenarios
            )
        )
        self.assertTrue(
            all(
                len(item["evidence_required"]) == len(set(item["evidence_required"]))
                for item in scenarios
            )
        )

    def test_active_surface_and_central_force_push_denial(self) -> None:
        surface = load_document(ROOT / "catalog" / "active-surface.yaml")
        decisions = load_document(ROOT / "catalog" / "skill-decisions.yaml")
        self.assertEqual(len(surface["active_skill_ids"]), 10)
        self.assertNotIn(
            "strict-branch-and-merge-discipline", surface["active_skill_ids"]
        )
        self.assertIn("force_push", decisions["central_denials"])

    def test_invalid_lane_and_unknown_field_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "codex-workflow"
            shutil.copytree(ROOT, root)
            surface_path = root / "catalog" / "active-surface.yaml"
            surface = load_document(surface_path)
            surface["lanes"]["lite"]["subagents_permitted"] = True
            surface_path.write_text(json.dumps(surface), encoding="utf-8")
            self.assertTrue(
                any("lite lane invariant" in error for error in validate(root))
            )
            manifest_path = root / "catalog" / "skills.yaml"
            manifest = load_document(manifest_path)
            manifest["skills"][0]["unsupported"] = True
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertTrue(any("unknown field" in error for error in validate(root)))

    def test_inventory_collision_and_renderer_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            for path in (source / "one" / "same", source / "two" / "same"):
                path.mkdir(parents=True)
                (path / "SKILL.md").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "collision"):
                build_inventory(source)
        with self.assertRaisesRegex(ValueError, "unknown repository"):
            render(ROOT, "unknown", "standard")
        lock = render(ROOT, "asset-allocation-jobs", "critical")
        self.assertTrue(lock["unresolved_forks"])
        self.assertIn("force_push", lock["central_denials"])

    def test_malformed_manifest_source_fails_closed_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "codex-workflow"
            shutil.copytree(ROOT, root)
            manifest_path = root / "catalog" / "skills.yaml"
            manifest = load_document(manifest_path)
            manifest["skills"][0]["source"] = "not-an-object"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            errors = validate(root)
            self.assertTrue(
                any("source must be an object" in error for error in errors)
            )

    def test_schema_checker_rejects_unsupported_keywords_and_wrong_nested_types(
        self,
    ) -> None:
        schema = {
            "type": "object",
            "properties": {"child": {"type": "boolean"}},
            "unsupported": True,
        }
        errors = validate_schema({"child": "no"}, schema)
        self.assertTrue(any("unsupported schema keyword" in error for error in errors))
        self.assertTrue(any("$.child: wrong type" in error for error in errors))
        nested_errors = validate_schema(
            {},
            {
                "type": "object",
                "properties": {"absent": {"type": "string", "unsupported": True}},
            },
        )
        self.assertTrue(
            any("unsupported schema keyword" in error for error in nested_errors)
        )

    def test_all_consumer_locks_have_exact_operating_contract(self) -> None:
        schema = load_document(ROOT / "schemas" / "consumer-lock-v2.schema.json")
        manifest = load_document(ROOT / "catalog" / "skills.yaml")
        decisions = load_document(ROOT / "catalog" / "skill-decisions.yaml")
        entries = {item["id"]: item for item in manifest["skills"]}
        statuses = {item["id"]: item["status"] for item in decisions["decisions"]}
        self.assertFalse(entries["gateway-agent"]["runnable"])
        self.assertEqual(entries["gateway-agent"]["canonical_state"], "deprecated")
        self.assertEqual(statuses["gateway-agent"], "deprecated")

        for repository in REPOSITORIES:
            for lane in ("lite", "standard", "critical"):
                with self.subTest(repository=repository, lane=lane):
                    lock = render(ROOT, repository, lane)
                    self.assertEqual(validate_schema(lock, schema), [])
                    self.assertEqual(
                        {item["id"] for item in lock["active_surface_skill_pins"]},
                        ACTIVE_SKILLS,
                    )
                    self.assertEqual(len(lock["active_surface_skill_pins"]), 10)
                    self.assertEqual(
                        {item["id"] for item in lock["unresolved_forks"]},
                        UNRESOLVED_FORKS,
                    )
                    self.assertTrue(
                        all(
                            not item["runnable"] and item["selection_blocked"]
                            for item in lock["unresolved_forks"]
                        )
                    )
                    self.assertEqual(
                        set(lock["central_denials"]),
                        {"force_push", "self_approval", "protected_approval_bypass"},
                    )
                    self.assertEqual(
                        lock["record_authority"],
                        {
                            "mutation_evidence": "central_hooks",
                            "tracked_delivery": "azure_boards_when_applicable",
                            "coordination_ledger": "none_by_default",
                            "jsonl": "regulated_audit_only_when_explicitly_required",
                        },
                    )
                    plan = lock["lane_execution_plan"]
                    if lane == "lite":
                        self.assertEqual(
                            (plan["owner"]["model"], plan["owner"]["effort"]),
                            ("Luna", "low"),
                        )
                        self.assertEqual(plan["children"], [])
                        self.assertEqual(
                            (plan["minimum_children"], plan["maximum_children"]), (0, 0)
                        )
                        self.assertFalse(plan["orchestrator"])
                        self.assertEqual(plan["gate_owners"], {})
                    elif lane == "standard":
                        self.assertEqual(
                            (plan["owner"]["model"], plan["owner"]["effort"]),
                            ("Terra", "medium"),
                        )
                        self.assertEqual(
                            [
                                (
                                    item["model"],
                                    item["effort"],
                                    item["role"],
                                    item["required"],
                                )
                                for item in plan["children"]
                            ],
                            [
                                ("Luna", "low", "focused_qa", True),
                                ("Luna", "low", "necessary_specialist", False),
                            ],
                        )
                        self.assertEqual(
                            (plan["minimum_children"], plan["maximum_children"]), (1, 2)
                        )
                        self.assertFalse(plan["orchestrator"])
                    else:
                        self.assertEqual(
                            (plan["owner"]["model"], plan["owner"]["effort"]),
                            ("Sol", "high"),
                        )
                        self.assertEqual(
                            [
                                (
                                    item["model"],
                                    item["effort"],
                                    item["role"],
                                    item["required"],
                                )
                                for item in plan["children"]
                            ],
                            [("Terra", "medium", "bounded_specialist", True)],
                        )
                        self.assertEqual(
                            (plan["minimum_children"], plan["maximum_children"]), (1, 3)
                        )
                        self.assertTrue(plan["orchestrator"])
                        self.assertEqual(
                            set(plan["gate_owners"]), {"ownership", "security", "qa"}
                        )
                        self.assertTrue(
                            plan["gate_owners"]["security"]["human_protected"]
                        )

    def test_consumer_lock_schema_rejects_malformed_nested_values(self) -> None:
        schema = load_document(ROOT / "schemas" / "consumer-lock-v2.schema.json")
        base = render(ROOT, "asset-allocation-contracts", "critical")

        mutations = []
        value = copy.deepcopy(base)
        value["active_surface_skill_pins"][0]["source"] = "not-an-object"
        mutations.append(value)
        value = copy.deepcopy(base)
        value["active_surface_skill_pins"][0]["source"]["commit"] = "not-a-commit"
        mutations.append(value)
        value = copy.deepcopy(base)
        value["lane_execution_plan"]["owner"]["model"] = "Ultra"
        mutations.append(value)
        value = copy.deepcopy(base)
        value["lane_execution_plan"]["children"][0]["required"] = "yes"
        mutations.append(value)
        value = copy.deepcopy(base)
        del value["lane_execution_plan"]["gate_owners"]["security"]["human_protected"]
        mutations.append(value)
        value = copy.deepcopy(base)
        value["task_participant_skill_ids"][0] = "Invalid ID"
        mutations.append(value)
        value = copy.deepcopy(base)
        value["lane_required_skill_ids"].append(value["lane_required_skill_ids"][0])
        mutations.append(value)
        value = copy.deepcopy(base)
        value["unresolved_forks"].pop()
        mutations.append(value)
        value = copy.deepcopy(base)
        value["active_surface_skill_pins"].pop()
        mutations.append(value)
        value = copy.deepcopy(base)
        value["active_surface_skill_pins"][1]["id"] = value[
            "active_surface_skill_pins"
        ][0]["id"]
        mutations.append(value)
        value = copy.deepcopy(base)
        value["unresolved_forks"][1]["id"] = value["unresolved_forks"][0]["id"]
        mutations.append(value)
        value = copy.deepcopy(base)
        value["central_denials"] = ["force_push", "force_push", "self_approval"]
        mutations.append(value)

        for index, value in enumerate(mutations):
            with self.subTest(mutation=index):
                self.assertTrue(validate_schema(value, schema))


if __name__ == "__main__":
    unittest.main()
