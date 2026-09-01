from __future__ import annotations
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_inventory import build_inventory
from catalog_lib import load_document
from render_consumer_lock import render
from validate_catalog import validate

class CatalogTests(unittest.TestCase):
    def test_catalog_and_five_routing_scenarios_are_valid(self) -> None:
        self.assertEqual(validate(ROOT), [])
        scenarios = load_document(ROOT / "catalog" / "routing-scenarios.yaml")["scenarios"]
        self.assertEqual(len(scenarios), 5)
        self.assertTrue(all(item["protected_gates"] == "human" for item in scenarios))
        self.assertTrue(all(len(item["evidence"]) == len(set(item["evidence"])) for item in scenarios))

    def test_active_surface_and_central_force_push_denial(self) -> None:
        surface = load_document(ROOT / "catalog" / "active-surface.yaml")
        decisions = load_document(ROOT / "catalog" / "skill-decisions.yaml")
        self.assertEqual(len(surface["active_skill_ids"]), 10)
        self.assertNotIn("strict-branch-and-merge-discipline", surface["active_skill_ids"])
        self.assertIn("force_push", decisions["central_denials"])

    def test_invalid_lane_and_unknown_field_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "codex-workflow"
            shutil.copytree(ROOT, root)
            surface_path = root / "catalog" / "active-surface.yaml"
            surface = load_document(surface_path)
            surface["lanes"]["lite"]["subagents_permitted"] = True
            surface_path.write_text(json.dumps(surface), encoding="utf-8")
            self.assertTrue(any("lite lane invariant" in error for error in validate(root)))
            manifest_path = root / "catalog" / "skills.yaml"
            manifest = load_document(manifest_path)
            manifest["skills"][0]["unsupported"] = True
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertTrue(any("unknown field" in error for error in validate(root)))

    def test_inventory_collision_and_renderer_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            for path in (source / "one" / "same", source / "two" / "same"):
                path.mkdir(parents=True); (path / "SKILL.md").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "collision"):
                build_inventory(source)
        with self.assertRaisesRegex(ValueError, "unknown repository"):
            render(ROOT, "unknown", "standard")
        lock = render(ROOT, "asset-allocation-jobs", "critical")
        self.assertEqual(lock["overlays"], [])
        self.assertIn("force_push", lock["central_denials"])

if __name__ == "__main__": unittest.main()
