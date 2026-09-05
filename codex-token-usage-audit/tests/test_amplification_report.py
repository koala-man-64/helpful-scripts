from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import amplification_report as report  # noqa: E402
import codex_token_usage_audit as audit  # noqa: E402


SESSION_ID = "11111111-1111-1111-1111-111111111111"


def usage(input_tokens: int, cached: int, output: int, reasoning: int) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": 0,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "total_tokens": input_tokens + output,
    }


def session() -> dict[str, object]:
    return {
        "timestamp": "2026-09-05T10:00:00Z",
        "type": "session_meta",
        "payload": {"id": SESSION_ID, "session_id": SESSION_ID, "cwd": "C:/private/path"},
    }


def context(turn_id: str = "turn-1") -> dict[str, object]:
    return {
        "timestamp": "2026-09-05T10:00:01Z",
        "type": "turn_context",
        "payload": {"turn_id": turn_id, "model": "gpt-5.6-sol", "effort": "medium"},
    }


def token(total: dict[str, int]) -> dict[str, object]:
    return {
        "timestamp": "2026-09-05T10:00:02Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": total, "last_token_usage": total},
        },
    }


def desktop(total: dict[str, int]) -> dict[str, object]:
    return {
        "ordinal": 1,
        "timestamp": "2026-09-05T10:00:03Z",
        "type": "token_usage_record",
        "payload": {
            "thread_id": SESSION_ID,
            "session_id": SESSION_ID,
            "turn_id": "turn-1",
            "root_turn_id": "turn-1",
            "response_id": "response-1",
            "usage": total,
            "turn_token_usage": total,
            "thread_token_usage": total,
        },
    }


def call(secret: str, revision: int | None, call_id: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "function_call",
        "name": "exec",
        "input": secret,
    }
    if revision is not None:
        payload["state"] = {"operation_id": "operation-1", "revision": revision}
    if call_id is not None:
        payload["call_id"] = call_id
    return {
        "type": "response_item",
        "payload": payload,
    }


def output(secret: str) -> dict[str, object]:
    return {"type": "response_item", "payload": {"type": "function_call_output", "output": secret}}


def write_rollout(directory: Path, entries: list[dict[str, object]]) -> Path:
    path = directory / f"rollout-2026-09-05T10-00-00-{SESSION_ID}.jsonl"
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")
    return path


class AmplificationReportTests(unittest.TestCase):
    def test_same_state_repeats_are_grouped_and_changed_state_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_rollout(
                Path(temporary),
                [session(), context(), token(usage(100, 80, 10, 4)), call("secret command", 1, "one"), call("secret command", 1, "two"), call("secret command", 2, "three"), output("private tool result")],
            )

            result = report.build_report([path], task_class="ordinary", top=1)

            activity = result["activity_metrics"]
            self.assertEqual(3, activity["observed_call_records"])
            self.assertEqual(3, activity["observed_unique_call_ids"])
            self.assertIsNone(activity["executed_call_count"])
            self.assertEqual("execution_not_verified", activity["executed_call_count_status"])
            self.assertEqual(1, activity["exact_repeat_groups"])
            self.assertEqual(1, activity["state_qualified_repeat_groups"])
            self.assertEqual(2, activity["top_repeat_groups"][0]["occurrences"])
            self.assertEqual("explicit", activity["top_repeat_groups"][0]["state"])

    def test_parser_totals_are_reused_for_mixed_records_and_counter_decrease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = usage(100, 80, 10, 4)
            decreased = usage(20, 10, 3, 1)
            path = write_rollout(Path(temporary), [session(), context(), token(first), desktop(first), token(decreased)])
            warnings: list[str] = []
            parsed = audit.parse_rollout(path, warnings)
            self.assertIsNotNone(parsed)
            records = audit.build_turn_records([parsed], {}, {}, False, False, warnings)  # type: ignore[list-item]
            expected = sum(record.total_tokens for record in records)

            result = report.build_report([path], task_class="ordinary")

            self.assertEqual(expected, result["token_metrics"]["total_tokens"])
            self.assertEqual(0, result["source"]["copied_turns_deduplicated"])

    def test_missing_context_and_unknown_state_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_rollout(
                Path(temporary),
                [session(), token(usage(30, 20, 5, 2)), call("same input", None), call("same input", None)],
            )

            result = report.build_report([path])

            self.assertEqual("unknown", result["task_class"])
            self.assertEqual(1, result["activity_metrics"]["unknown_state_repeat_groups"])
            self.assertIn("unknown", result["attribution_completeness"]["external_wait_wakes"])
            self.assertIn("unknown", result["attribution_completeness"]["quota_or_subscription_debits"])
            self.assertIn("unknown", result["attribution_completeness"]["intervention_or_rework"])

    def test_body_free_output_is_bounded_and_baseline_requires_matching_task_class(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            secret = "do-not-retain-this-tool-body"
            path = write_rollout(root, [session(), context(), token(usage(100, 80, 10, 4)), call(secret, 1), call(secret, 1), output(secret)])
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps({
                "schema_version": report.BASELINE_SCHEMA_VERSION,
                "task_class": "different",
                "comparable": True,
                "upper_bounds": {"requests_per_turn": 1},
            }), encoding="utf-8")

            result = report.build_report([path], task_class="ordinary", baseline=baseline, top=1)
            encoded = json.dumps(result)

            self.assertEqual("not_comparable", result["baseline_comparison"]["status"])
            self.assertEqual(1, len(result["activity_metrics"]["top_repeat_groups"]))
            self.assertNotIn(secret, encoded)
            self.assertNotIn("C:/private/path", encoded)
            self.assertNotIn(SESSION_ID, encoded)

    def test_cli_refuses_an_output_that_is_the_input_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_rollout(Path(temporary), [session(), context(), token(usage(100, 80, 10, 4))])
            before = path.read_bytes()

            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                report.main(["--rollout", str(path), "--output", str(path)])

            self.assertEqual(before, path.read_bytes())

    def test_exclusive_output_rejects_baseline_and_hardlink_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = write_rollout(root, [session(), context(), token(usage(100, 80, 10, 4))])
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps({
                "schema_version": report.BASELINE_SCHEMA_VERSION,
                "task_class": "ordinary",
                "comparable": True,
                "upper_bounds": {"requests_per_turn": 2},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                report.main(["--rollout", str(path), "--baseline", str(baseline), "--output", str(baseline)])
            hardlink = root / "rollout-link.jsonl"
            os.link(path, hardlink)
            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                report.main(["--rollout", str(path), "--output", str(hardlink)])
            existing_output = root / "existing-diagnostic.json"
            existing_output.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "new file"):
                report.main(["--rollout", str(path), "--output", str(existing_output)])
            self.assertEqual("preserve", existing_output.read_text(encoding="utf-8"))

    def test_durable_ids_deduplicate_rescans_and_conflicts_are_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entries = [session(), context(), token(usage(100, 80, 10, 4)), call("same", 1, "call-1")]
            path = write_rollout(root, entries)
            copied = root / "copied.jsonl"
            copied.write_bytes(path.read_bytes())
            result = report.build_report([path, copied], task_class="ordinary")
            self.assertEqual(1, result["source"]["activity_source_rollouts"])
            self.assertEqual(1, result["activity_metrics"]["observed_call_records"])

            conflict = write_rollout(root, [session(), context(), token(usage(100, 80, 10, 4)), call("first", 1, "call-1"), call("second", 1, "call-1")])
            conflict_result = report.build_report([conflict], task_class="ordinary")
            self.assertEqual(["conflicting_durable_id"], conflict_result["activity_metrics"]["partial_reasons"])
            self.assertIsNone(conflict_result["activity_metrics"]["executed_call_count"])

    def test_state_requires_version_and_identity_and_baseline_bounds_produce_outliers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = call("same", None, "one"), call("same", None, "two")
            first["payload"]["state"] = {}  # type: ignore[index]
            second["payload"]["state"] = {"operation_id": "operation-1"}  # type: ignore[index]
            path = write_rollout(root, [session(), context(), token(usage(100, 80, 10, 4)), first, second])
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps({
                "schema_version": report.BASELINE_SCHEMA_VERSION,
                "task_class": "ordinary",
                "comparable": True,
                "upper_bounds": {"requests_per_turn": 0, "input_tokens_per_request": 10},
            }), encoding="utf-8")
            result = report.build_report([path], task_class="ordinary", baseline=baseline)
            self.assertEqual(1, result["activity_metrics"]["unknown_state_repeat_groups"])
            self.assertEqual(["input_tokens_per_request", "requests_per_turn"], result["baseline_comparison"]["outlier_metrics"])
            self.assertEqual("caller_supplied_task_class_baseline", result["baseline_comparison"]["upper_bounds_source"])

    def test_baseline_rejects_nonfinite_or_empty_bounds_and_rates_exist_without_one(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = write_rollout(root, [session(), context(), token(usage(100, 80, 10, 4))])
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps({
                "schema_version": report.BASELINE_SCHEMA_VERSION,
                "task_class": "ordinary",
                "comparable": True,
                "upper_bounds": {"requests_per_turn": float("inf")},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "finite supported"):
                report.build_report([path], task_class="ordinary", baseline=baseline)
            result = report.build_report([path], task_class="ordinary")
            self.assertEqual("not_supplied", result["baseline_comparison"]["status"])
            self.assertEqual(1.0, result["baseline_comparison"]["observed"]["requests_per_turn"])

    def test_baseline_is_sealed_and_changes_reject(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = write_rollout(root, [session(), context(), token(usage(100, 80, 10, 4))])
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps({"schema_version": report.BASELINE_SCHEMA_VERSION,
                                           "task_class": "ordinary", "comparable": True,
                                           "upper_bounds": {"requests_per_turn": 1}}), encoding="utf-8")
            result = report.build_report([path], task_class="ordinary", baseline=baseline)
            self.assertEqual(result["provenance"]["baseline"]["byte_length"], len(baseline.read_bytes()))
            self.assertIn("generated_at_utc", result)
            original = report._activity_metrics

            def mutate_baseline(paths, top):
                result = original(paths, top)
                baseline.write_bytes(b"{}")
                return result

            with patch.object(report, "_activity_metrics", side_effect=mutate_baseline):
                with self.assertRaisesRegex(ValueError, "changed during report generation"):
                    report.build_report([path], task_class="ordinary", baseline=baseline)

    def test_rejects_rollout_appended_during_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_rollout(
                Path(temporary), [session(), context(), token(usage(100, 80, 10, 4))]
            )
            original = report._activity_metrics

            def append_after_activity(paths: list[Path], top: int) -> dict[str, object]:
                result = original(paths, top)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(token(usage(101, 80, 10, 4))) + "\n")
                return result

            with patch.object(report, "_activity_metrics", side_effect=append_after_activity):
                with self.assertRaisesRegex(ValueError, "changed during report generation"):
                    report.build_report([path], task_class="ordinary")


if __name__ == "__main__":
    unittest.main()
