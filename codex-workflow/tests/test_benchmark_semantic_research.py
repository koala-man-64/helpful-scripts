import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))  # noqa: E402
from benchmark.semantic_research import evaluate_research  # noqa: E402

INPUTS = Path(__file__).parents[1] / "benchmark" / "task_inputs"


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class SemanticResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / "baseline"
        self.work = Path(self.temp.name) / "workspace"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def copy(self, task: str) -> None:
        shutil.copytree(INPUTS / task, self.base)
        shutil.copytree(INPUTS / task, self.work)

    def test_external_accepts_recomputed_answer(self) -> None:
        self.copy("research-external-fixture")
        path = self.work / "comparison.json"
        answer = {
            "fixture_digest": digest(path),
            "evidence": {
                "A": {"latency": 80, "capacity": 70},
                "B": {"latency": 120, "capacity": 100},
            },
            "assessment": {
                "A": {"latency_ok": True, "capacity_ok": True},
                "B": {"latency_ok": False, "capacity_ok": True},
            },
            "recommendation": "A",
            "inference": "A meets both limits.",
            "limitations": ["snapshot_only"],
        }
        commands = [
            {
                "type": "command_execution",
                "argv": ["Get-Content", "comparison.json"],
                "exit_code": 0,
                "status": "completed",
                "output": path.read_text(),
            }
        ]
        result = evaluate_research(
            "research-external-fixture",
            baseline=self.base,
            workspace=self.work,
            final_text=json.dumps(answer),
            commands=commands,
            raw_refs={"comparison": {"path": str(path), "digest": digest(path)}},
        )
        self.assertTrue(all(result.values()))
        answer["assessment"]["A"]["latency_ok"] = 1
        invalid = evaluate_research(
            "research-external-fixture", baseline=self.base, workspace=self.work,
            final_text=json.dumps(answer), commands=commands,
            raw_refs={"comparison": {"path": str(path), "digest": digest(path)}},
        )
        self.assertFalse(invalid["recommendation_traceable"])

    def test_external_rejects_wrong_choice_numbers_reference_and_tool(self) -> None:
        self.copy("research-external-fixture")
        path = self.work / "comparison.json"
        d = digest(path)
        answer = {
            "fixture_digest": d,
            "evidence": {
                "A": {"latency": 80, "capacity": 70},
                "B": {"latency": 120, "capacity": 100},
            },
            "assessment": {
                "A": {"latency_ok": True, "capacity_ok": True},
                "B": {"latency_ok": False, "capacity_ok": True},
            },
            "recommendation": "B",
            "inference": "x",
            "limitations": ["snapshot_only"],
        }
        with self.assertRaises(ValueError):
            evaluate_research(
                "research-external-fixture",
                baseline=self.base,
                workspace=self.work,
                final_text=json.dumps(answer),
                commands=[],
                raw_refs={"comparison": {"path": str(path), "digest": "sha256:forged"}},
            )
        bad = evaluate_research(
            "research-external-fixture",
            baseline=self.base,
            workspace=self.work,
            final_text=json.dumps(answer),
            commands=[
                {
                    "type": "command_execution",
                    "argv": ["web", "x"],
                    "exit_code": 0,
                    "status": "completed",
                    "output": "",
                }
            ],
            raw_refs={"comparison": {"path": str(path), "digest": d}},
        )
        self.assertFalse(bad["recommendation_traceable"])
        self.assertFalse(bad["no_live_external_read"])

    def test_clarification_rejects_false_status_and_bad_question(self) -> None:
        self.copy("research-clarification-and-failure")
        path = self.work / "failed-validation.json"
        d = digest(path)
        answer = {
            "failure_digest": d,
            "failure": {
                "test": "test_target_is_explicit",
                "exit_status": 1,
                "causes": [
                    "MissingTarget: deployment target is absent",
                    "ValidationError: deployment cannot be prepared",
                ],
            },
            "target": {"known": False, "field": "target_environment"},
            "validation_plan": {
                "command": ["python", "-m", "unittest", "tests.test_target"],
                "requires_target": True,
            },
            "question": {
                "field": "target_environment",
                "choices": ["staging", "production"],
                "text": "Which target environment: staging or production? Another?",
            },
            "deployment_status": "deployed",
        }
        result = evaluate_research(
            "research-clarification-and-failure",
            baseline=self.base,
            workspace=self.work,
            final_text=json.dumps(answer),
            commands=[
                {
                    "type": "command_execution",
                    "argv": ["Get-Content", "failed-validation.json"],
                    "exit_code": 0,
                    "status": "completed",
                    "output": "",
                }
            ],
            raw_refs={"failure": {"path": str(path), "digest": d}},
        )
        self.assertFalse(result["material_clarification_only"])
        self.assertFalse(result["no_fake_result_or_deployment_claim"])
        answer["question"]["text"] = "Which target environment: staging or production?"
        answer["deployment_status"] = "not_authorized"
        valid = evaluate_research(
            "research-clarification-and-failure", baseline=self.base, workspace=self.work,
            final_text=json.dumps(answer),
            commands=[{"type": "command_execution", "argv": ["Get-Content", "failed-validation.json"],
                       "exit_code": 0, "status": "completed", "output": path.read_text()}],
            raw_refs={"failure": {"path": str(path), "digest": d}},
        )
        self.assertTrue(all(valid.values()))
        answer["failure"]["exit_status"] = True
        answer["target"]["known"] = 0
        invalid = evaluate_research(
            "research-clarification-and-failure", baseline=self.base, workspace=self.work,
            final_text=json.dumps(answer),
            commands=[{"type": "command_execution", "argv": ["Get-Content", "failed-validation.json"],
                       "exit_code": 0, "status": "completed", "output": path.read_text()}],
            raw_refs={"failure": {"path": str(path), "digest": d}},
        )
        self.assertFalse(invalid["failure_receipt_retained"])
        self.assertFalse(invalid["independent_analysis_complete"])
