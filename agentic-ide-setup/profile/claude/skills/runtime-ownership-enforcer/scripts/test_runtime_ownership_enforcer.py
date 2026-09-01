#!/usr/bin/env python3
"""Regression tests for the runtime ownership and lifecycle evidence gates."""

from __future__ import annotations

import copy
import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCANNER = SCRIPT_DIR / "scan_runtime_ownership.py"
VALIDATOR = SCRIPT_DIR / "validate_lifecycle_evidence.py"
PHASES = (
    "discovery",
    "architecture",
    "development",
    "data_ai",
    "build_supply_chain",
    "verification",
    "provisioning",
    "release_deployment",
    "operations",
    "incident_recovery",
    "retirement",
)


def run_json(*arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    result = subprocess.run(
        [sys.executable, *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    document = json.loads(result.stdout) if result.stdout else {}
    return result, document


def lifecycle_manifest() -> dict:
    return {
        "schema_version": 1,
        "system": {"name": "cloud-ai-service", "ai_enabled": True, "criticality": "high"},
        "ownership_map": [
            {
                "resource": "model-endpoint",
                "plane": "ai_control",
                "authoritative_owner": "ml-platform",
                "provisioned_by": "iac/model-endpoint.bicep",
                "runtime_identity": "cloud-ai-runtime",
                "allowed_runtime_operations": ["invoke"],
                "environments": ["staging", "production"],
                "retirement_mechanism": "IaC removal after traffic drain",
            }
        ],
        "phases": {
            phase: {
                "status": "pass",
                "owner": f"{phase}-owner",
                "evidence": [f"evidence/{phase}.json"],
                "rationale": "Gate passed.",
            }
            for phase in PHASES
        },
    }


class RuntimeOwnershipGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_clean_runtime_passes(self) -> None:
        (self.runtime / "worker.py").write_text("def process(value):\n    return value + 1\n", encoding="utf-8")
        result, document = run_json(str(SCANNER), str(self.root), "--runtime-dir", "runtime")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({"hard_failures": 0, "warnings": 0, "excluded": 0}, document["summary"])

    def test_missing_runtime_directory_is_invalid_input(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCANNER), str(self.root), "--runtime-dir", "missing"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("runtime directory does not exist", result.stderr)

    def test_cross_plane_and_mutable_ai_patterns_fail(self) -> None:
        (self.runtime / "worker.py").write_text(
            '\n'.join([
                'cursor.execute("CREATE TABLE state (id int)")',
                'model_version = "latest"',
                'command = "kubectl apply -f app.yaml"',
                'vector_client.create_index("documents")',
                'except Exception:',
                '    if "permission denied" in message:',
                '        repair()',
            ]) + "\n",
            encoding="utf-8",
        )
        result, document = run_json(str(SCANNER), str(self.root), "--runtime-dir", "runtime")
        self.assertEqual(1, result.returncode)
        patterns = {finding["pattern"] for finding in document["findings"]}
        self.assertTrue(
            {"database_ddl", "mutable_ai_asset", "kubernetes_mutation", "ai_resource_provisioning", "masked_environment_error"}
            <= patterns
        )
        self.assertTrue(all(finding["lifecycle_phase"] and finding["plane"] for finding in document["findings"]))

    def test_valid_exception_warns_and_long_exception_is_rejected(self) -> None:
        target = self.runtime / "bootstrap.py"
        target.write_text('cursor.execute("CREATE TABLE state (id int)")\n', encoding="utf-8")
        expiry = (dt.date.today() + dt.timedelta(days=60)).isoformat()
        exception = {
            "id": "local-only",
            "path": "runtime/bootstrap.py",
            "owner": "platform-team",
            "reason": "Disposable local environment",
            "expires_on": expiry,
            "tracking_work_item": "AB#1",
            "test_reference": "test_local",
            "compensating_controls": "Packaging assertion excludes the file.",
            "non_production_only": True,
            "production_disablement_plan": "Entrypoint rejects production.",
            "removal_plan": "Move setup to development provisioning.",
        }
        allowlist = self.root / "allow.json"
        allowlist.write_text(json.dumps({"exceptions": [exception]}), encoding="utf-8")
        result, document = run_json(str(SCANNER), str(self.root), "--runtime-dir", "runtime", "--allowlist", str(allowlist))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(0, document["summary"]["hard_failures"])
        self.assertGreaterEqual(document["summary"]["warnings"], 1)

        exception["expires_on"] = (dt.date.today() + dt.timedelta(days=181)).isoformat()
        allowlist.write_text(json.dumps({"exceptions": [exception]}), encoding="utf-8")
        rejected = subprocess.run(
            [sys.executable, str(SCANNER), str(self.root), "--runtime-dir", "runtime", "--allowlist", str(allowlist)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, rejected.returncode)
        self.assertIn("within 180 days", rejected.stderr)


class LifecycleEvidenceGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manifest = self.root / "lifecycle.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_manifest(self, document: dict) -> None:
        self.manifest.write_text(json.dumps(document), encoding="utf-8")

    def test_malformed_manifest_is_invalid_input(self) -> None:
        self.manifest.write_text("{not-json", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), str(self.manifest), "--environment", "production"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("lifecycle evidence error", result.stderr)

    def test_complete_production_evidence_passes(self) -> None:
        self.write_manifest(lifecycle_manifest())
        result, document = run_json(str(VALIDATOR), str(self.manifest), "--environment", "production")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({"hard_failures": 0, "warnings": 0}, document["summary"])

    def test_production_blocks_cross_plane_access_and_incomplete_gates(self) -> None:
        document = lifecycle_manifest()
        document["ownership_map"][0]["allowed_runtime_operations"] = ["create deployment"]
        expiry = (dt.date.today() + dt.timedelta(days=60)).isoformat()
        enforcement = (dt.date.today() + dt.timedelta(days=30)).isoformat()
        document["phases"]["operations"].update(
            {"status": "conditional", "tracking_work_item": "AB#2", "expires_on": expiry, "enforcement_date": enforcement}
        )
        document["phases"]["data_ai"].update(
            {"status": "not_applicable", "applicability_rationale": "Excluded for negative test."}
        )
        self.write_manifest(document)
        result, output = run_json(str(VALIDATOR), str(self.manifest), "--environment", "production")
        self.assertEqual(1, result.returncode)
        codes = {finding["code"] for finding in output["findings"]}
        self.assertTrue({"cross_plane_runtime_mutation", "production_conditional", "invalid_production_exclusion"} <= codes)

    def test_bounded_staging_condition_is_warning(self) -> None:
        document = copy.deepcopy(lifecycle_manifest())
        document["phases"]["operations"].update(
            {
                "status": "conditional",
                "tracking_work_item": "AB#3",
                "expires_on": (dt.date.today() + dt.timedelta(days=60)).isoformat(),
                "enforcement_date": (dt.date.today() + dt.timedelta(days=30)).isoformat(),
            }
        )
        self.write_manifest(document)
        result, output = run_json(str(VALIDATOR), str(self.manifest), "--environment", "staging")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({"hard_failures": 0, "warnings": 1}, output["summary"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
