from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import codex_token_usage_audit as audit  # noqa: E402


ROOT_ID = "11111111-1111-1111-1111-111111111111"
CHILD_ID = "22222222-2222-2222-2222-222222222222"
GRANDCHILD_ID = "33333333-3333-3333-3333-333333333333"


def token_entry(
    timestamp: str,
    total_usage: dict[str, int],
    last_usage: dict[str, int] | None = None,
) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": total_usage,
                "last_token_usage": last_usage if last_usage is not None else total_usage,
            },
        },
    }


def turn_entry(timestamp: str, turn_id: str, model: str, effort: str) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "turn_context",
        "payload": {
            "turn_id": turn_id,
            "model": model,
            "effort": effort,
            "secret_tool_argument": "must-not-be-exported",
        },
    }


def session_entry(
    session_id: str,
    *,
    source: object = "vscode",
    conversation_id: str | None = None,
    direct_parent_thread_id: str = "",
    timestamp: str = "2026-08-23T10:00:00Z",
) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "session_id": conversation_id or session_id,
            "parent_thread_id": direct_parent_thread_id,
            "timestamp": timestamp,
            "cwd": r"C:\work\helpful-scripts",
            "originator": "Codex Desktop",
            "cli_version": "0.test",
            "source": source,
            "thread_source": "subagent" if isinstance(source, dict) else "user",
            "base_instructions": {"text": "must-not-be-exported"},
            "git": {
                "repository_url": "https://github.com/example/helpful-scripts.git",
            },
        },
    }


def usage(
    input_tokens: int,
    cached: int,
    output: int,
    reasoning: int,
    cache_write: int = 0,
) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": cache_write,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "total_tokens": input_tokens + output,
    }


def task_entry(timestamp: str, event_type: str, turn_id: str) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {"type": event_type, "turn_id": turn_id},
    }


def write_rollout(
    codex_home: Path,
    session_id: str,
    entries: list[dict[str, object] | str],
    *,
    archived: bool = False,
    suffix: str = "10-00-00",
) -> Path:
    if archived:
        directory = codex_home / "archived_sessions"
    else:
        directory = codex_home / "sessions" / "2026" / "08" / "23"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"rollout-2026-08-23T{suffix}-{session_id}.jsonl"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(entry if isinstance(entry, str) else json.dumps(entry))
            handle.write("\n")
    return path


def root_entries() -> list[dict[str, object] | str]:
    first_total = usage(100, 80, 10, 5)
    final_first_turn = usage(220, 180, 30, 10)
    final_second_turn = usage(300, 200, 50, 15)
    return [
        session_entry(ROOT_ID),
        turn_entry("2026-08-23T10:01:00Z", "turn-root-1", "gpt-5.6-sol", "ultra"),
        token_entry("2026-08-23T10:01:10Z", first_total),
        token_entry("2026-08-23T10:01:11Z", first_total),
        token_entry("2026-08-23T10:01:20Z", final_first_turn),
        {"type": "response_item", "payload": {"text": "must-not-be-exported"}},
        turn_entry("2026-08-23T10:02:00Z", "turn-root-2", "gpt-5.6-terra", "medium"),
        token_entry("2026-08-23T10:02:20Z", final_second_turn),
    ]


class ParserTests(unittest.TestCase):
    def test_cumulative_snapshots_produce_one_exact_delta_per_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            path = write_rollout(codex_home, ROOT_ID, root_entries())
            warnings: list[str] = []

            session = audit.parse_rollout(path, warnings)
            self.assertIsNotNone(session)
            records = audit.build_turn_records(
                [session], {}, {}, True, True, warnings  # type: ignore[list-item]
            )

        self.assertEqual([], warnings)
        self.assertEqual(2, len(records))
        first, second = records
        self.assertEqual(250, first.total_tokens)
        self.assertEqual(220, first.input_tokens)
        self.assertEqual(180, first.cached_input_tokens)
        self.assertEqual(40, first.fresh_input_tokens)
        self.assertEqual(30, first.output_tokens)
        self.assertEqual(10, first.reasoning_output_tokens)
        self.assertEqual(20, first.visible_output_tokens)
        self.assertEqual("gpt-5.6-sol", first.model)
        self.assertEqual("ultra", first.reasoning_effort)

        self.assertEqual(100, second.total_tokens)
        self.assertEqual(80, second.input_tokens)
        self.assertEqual(20, second.cached_input_tokens)
        self.assertEqual(60, second.fresh_input_tokens)
        self.assertEqual(20, second.output_tokens)
        self.assertEqual(5, second.reasoning_output_tokens)
        self.assertEqual("gpt-5.6-terra", second.model)
        self.assertAlmostEqual(0.0208, first.estimated_credits or 0.0)
        self.assertAlmostEqual(0.0091, second.estimated_credits or 0.0)

        exported = json.dumps([audit.asdict(record) for record in records])
        self.assertNotIn("must-not-be-exported", exported)

    def test_nested_subagents_roll_up_to_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            root_path = write_rollout(codex_home, ROOT_ID, root_entries())
            child_source = {
                "subagent": {
                    "thread_spawn": {
                        "parent_thread_id": ROOT_ID,
                        "depth": 1,
                        "agent_path": "/root/research",
                        "agent_nickname": "Gauss",
                    }
                }
            }
            grandchild_source = {
                "subagent": {
                    "thread_spawn": {
                        "parent_thread_id": CHILD_ID,
                        "depth": 2,
                        "agent_path": "/root/research/check",
                        "agent_nickname": "Halley",
                    }
                }
            }
            child_path = write_rollout(
                codex_home,
                CHILD_ID,
                [
                    session_entry(CHILD_ID, source=child_source, conversation_id=ROOT_ID),
                    turn_entry("2026-08-23T10:03:00Z", "child-turn", "gpt-5.6-luna", "low"),
                    token_entry("2026-08-23T10:03:10Z", usage(50, 40, 10, 2)),
                ],
                suffix="10-03-00",
            )
            grandchild_path = write_rollout(
                codex_home,
                GRANDCHILD_ID,
                [
                    session_entry(GRANDCHILD_ID, source=grandchild_source, conversation_id=ROOT_ID),
                    turn_entry("2026-08-23T10:04:00Z", "grandchild-turn", "gpt-5.6-luna", "low"),
                    token_entry("2026-08-23T10:04:10Z", usage(25, 20, 5, 1)),
                ],
                suffix="10-04-00",
            )
            warnings: list[str] = []
            sessions = [audit.parse_rollout(path, warnings) for path in (root_path, child_path, grandchild_path)]
            state = {
                ROOT_ID: audit.ThreadMetadata(ROOT_ID, title="Audit my Codex usage"),
            }
            records = audit.build_turn_records(
                [session for session in sessions if session], state, {}, True, True, warnings
            )

        child = next(record for record in records if record.thread_id == CHILD_ID)
        grandchild = next(record for record in records if record.thread_id == GRANDCHILD_ID)
        self.assertEqual(ROOT_ID, child.root_thread_id)
        self.assertEqual(ROOT_ID, grandchild.root_thread_id)
        self.assertEqual(1, child.depth)
        self.assertEqual(2, grandchild.depth)
        self.assertEqual("subagent", child.thread_type)
        self.assertEqual("Audit my Codex usage", grandchild.root_title)
        self.assertEqual(3, len({record.thread_id for record in records}))

        task_totals = audit.aggregate(records, audit.DIMENSION_KEYS["task"])
        self.assertEqual(1, len(task_totals))
        self.assertEqual(sum(record.total_tokens for record in records), next(iter(task_totals.values()))["total_tokens"])

    def test_counter_reset_is_explicit_and_counted_from_zero(self) -> None:
        entries = [
            session_entry(ROOT_ID),
            turn_entry("2026-08-23T10:01:00Z", "turn-1", "gpt-5.6-sol", "high"),
            token_entry("2026-08-23T10:01:10Z", usage(100, 80, 10, 5)),
            turn_entry("2026-08-23T10:02:00Z", "turn-2", "gpt-5.6-sol", "high"),
            token_entry("2026-08-23T10:02:10Z", usage(40, 30, 5, 2)),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = write_rollout(Path(temporary), ROOT_ID, entries)
            warnings: list[str] = []
            session = audit.parse_rollout(path, warnings)
            records = audit.build_turn_records([session], {}, {}, True, False, warnings)  # type: ignore[list-item]

        self.assertEqual([110, 45], [record.total_tokens for record in records])

    def test_inherited_baseline_and_repeated_snapshot_are_not_counted(self) -> None:
        inherited = usage(1000, 800, 100, 20)
        inherited_last = usage(100, 80, 10, 2)
        increased = usage(1150, 920, 125, 25)
        increased_last = usage(150, 120, 25, 5)
        entries = [
            session_entry(CHILD_ID, conversation_id=ROOT_ID, direct_parent_thread_id=ROOT_ID),
            task_entry("2026-08-23T10:00:01Z", "task_started", "child-turn"),
            turn_entry("2026-08-23T10:00:02Z", "child-turn", "gpt-5.6-sol", "high"),
            token_entry("2026-08-23T10:00:03Z", inherited, inherited_last),
            token_entry("2026-08-23T10:00:04Z", inherited, inherited_last),
            token_entry("2026-08-23T10:00:05Z", increased, increased_last),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = write_rollout(Path(temporary), CHILD_ID, entries)
            warnings: list[str] = []
            session = audit.parse_rollout(path, warnings)
            records = audit.build_turn_records([session], {}, {}, True, False, warnings)  # type: ignore[list-item]

        self.assertEqual([], warnings)
        self.assertEqual(1, len(records))
        record = records[0]
        self.assertEqual(250, record.input_tokens)
        self.assertEqual(200, record.cached_input_tokens)
        self.assertEqual(35, record.output_tokens)
        self.assertEqual(7, record.reasoning_output_tokens)
        self.assertEqual(285, record.total_tokens)
        self.assertEqual(2, record.model_calls)

    def test_task_reset_marker_preserves_previous_turn_and_backfills_context(self) -> None:
        invalid_zero_last = usage(0, 0, 0, 0)
        invalid_zero_last["total_tokens"] = 9147
        entries = [
            session_entry(ROOT_ID),
            task_entry("2026-08-23T10:01:00Z", "task_started", "turn-1"),
            turn_entry("2026-08-23T10:01:01Z", "turn-1", "gpt-5.6-sol", "ultra"),
            token_entry("2026-08-23T10:01:02Z", usage(100, 80, 10, 5)),
            token_entry("2026-08-23T10:01:03Z", usage(200, 160, 20, 10)),
            task_entry("2026-08-23T10:01:04Z", "task_complete", "turn-1"),
            task_entry("2026-08-23T10:02:00Z", "task_started", "turn-2"),
            token_entry("2026-08-23T10:02:01Z", usage(0, 0, 0, 0), invalid_zero_last),
            token_entry("2026-08-23T10:02:02Z", usage(40, 30, 5, 2)),
            turn_entry("2026-08-23T10:02:03Z", "turn-2", "gpt-5.6-terra", "high"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = write_rollout(Path(temporary), ROOT_ID, entries)
            warnings: list[str] = []
            session = audit.parse_rollout(path, warnings)
            records = audit.build_turn_records([session], {}, {}, True, False, warnings)  # type: ignore[list-item]

        self.assertEqual([], warnings)
        self.assertEqual([220, 45], [record.total_tokens for record in records])
        self.assertEqual("gpt-5.6-terra", records[1].model)
        self.assertEqual("high", records[1].reasoning_effort)
        self.assertEqual(1, records[1].model_calls)

    def test_model_context_change_splits_usage_without_reassigning_prior_increments(self) -> None:
        entries = [
            session_entry(ROOT_ID),
            task_entry("2026-08-23T10:01:00Z", "task_started", "mixed-turn"),
            turn_entry("2026-08-23T10:01:01Z", "mixed-turn", "gpt-5.6-sol", "high"),
            token_entry("2026-08-23T10:01:02Z", usage(100, 80, 10, 5)),
            turn_entry("2026-08-23T10:01:03Z", "mixed-turn", "custom-unknown", "low"),
            token_entry("2026-08-23T10:01:04Z", usage(150, 120, 15, 7)),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = write_rollout(Path(temporary), ROOT_ID, entries)
            warnings: list[str] = []
            session = audit.parse_rollout(path, warnings)
            records = audit.build_turn_records(
                [session], {}, {}, False, True, warnings  # type: ignore[list-item]
            )

        self.assertTrue(any("custom-unknown" in warning for warning in warnings))
        self.assertEqual(
            [("gpt-5.6-sol", "high", 110), ("custom-unknown", "low", 55)],
            [
                (record.model, record.reasoning_effort, record.total_tokens)
                for record in records
            ],
        )
        self.assertEqual(1, audit.totals(records)["turns"])
        self.assertEqual(2, audit.totals(records)["model_calls"])
        self.assertEqual(165, audit.totals(records)["total_tokens"])
        self.assertEqual(1, audit.totals(records)["unpriced_turns"])
        self.assertEqual(1, audit.totals(list(reversed(records)))["unpriced_turns"])

    def test_malformed_line_is_warned_and_valid_usage_survives(self) -> None:
        entries = root_entries()
        entries.insert(2, "{not-json")
        with tempfile.TemporaryDirectory() as temporary:
            path = write_rollout(Path(temporary), ROOT_ID, entries)
            warnings: list[str] = []
            session = audit.parse_rollout(path, warnings)

        self.assertIsNotNone(session)
        self.assertEqual(350, session.final_usage.total_tokens)  # type: ignore[union-attr]
        self.assertTrue(any("malformed JSONL" in warning for warning in warnings))

    def test_date_filter_keeps_prior_counter_state_and_ignores_next_day_duplicate(self) -> None:
        first = usage(100, 80, 10, 5)
        increased = usage(150, 120, 15, 7)
        entries = [
            session_entry(ROOT_ID, timestamp="2026-08-22T23:59:00Z"),
            task_entry("2026-08-22T23:59:01Z", "task_started", "overnight-turn"),
            turn_entry("2026-08-22T23:59:02Z", "overnight-turn", "gpt-5.6-sol", "high"),
            token_entry("2026-08-22T23:59:59Z", first),
            token_entry("2026-08-23T00:00:01Z", first),
            token_entry("2026-08-23T00:00:02Z", increased, usage(50, 40, 5, 2)),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = write_rollout(Path(temporary), ROOT_ID, entries)
            warnings: list[str] = []
            session = audit.parse_rollout(path, warnings)
            records = audit.build_turn_records([session], {}, {}, True, False, warnings)  # type: ignore[list-item]
            args = audit.build_parser().parse_args(["--since", "2026-08-23"])
            selected = audit.filter_records(records, args)

        self.assertEqual([], warnings)
        self.assertEqual(2, len(records))
        self.assertEqual(55, audit.totals(selected)["total_tokens"])
        self.assertEqual(1, audit.totals(selected)["model_calls"])

    def test_unpriced_turn_is_deduplicated_across_days_and_disabled_credits_are_explicit(self) -> None:
        entries = [
            session_entry(ROOT_ID, timestamp="2026-08-22T23:59:00Z"),
            turn_entry("2026-08-22T23:59:01Z", "overnight-turn", "gpt-unknown", "high"),
            token_entry("2026-08-22T23:59:59Z", usage(100, 80, 10, 5)),
            token_entry("2026-08-23T00:00:02Z", usage(150, 120, 15, 7)),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = write_rollout(Path(temporary), ROOT_ID, entries)
            warnings: list[str] = []
            session = audit.parse_rollout(path, warnings)
            priced = audit.build_turn_records(
                [session], {}, {}, False, True, warnings  # type: ignore[list-item]
            )
            disabled = audit.build_turn_records(
                [session], {}, {}, False, False, []  # type: ignore[list-item]
            )

        self.assertEqual(2, len(priced))
        self.assertEqual(1, audit.totals(priced)["turns"])
        self.assertEqual(1, audit.totals(priced)["unpriced_turns"])
        self.assertTrue(all(record.credits_enabled for record in priced))
        self.assertEqual(0, audit.totals(disabled)["unpriced_turns"])
        self.assertTrue(all(not record.credits_enabled for record in disabled))
        self.assertTrue(any("gpt-unknown" in warning for warning in warnings))


class StateAndCliTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows extended path behavior")
    def test_windows_extended_and_normal_paths_share_one_identity(self) -> None:
        normal = Path(r"C:\Users\rdpro\.codex\sessions\rollout-example.jsonl")
        extended = Path(r"\\?\C:\Users\rdpro\.codex\sessions\rollout-example.jsonl")
        self.assertEqual(audit._canonical_path_key(normal), audit._canonical_path_key(extended))
        self.assertEqual(
            audit._canonical_path_key(Path(r"C:\Temp\Report.JSON")),
            audit._canonical_path_key(Path(r"c:\temp\report.json")),
        )

    @unittest.skipUnless(os.name == "nt", "Windows path comparison behavior")
    def test_output_destinations_are_compared_case_insensitively(self) -> None:
        parser = audit.build_parser()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            audit.validate_output_destinations(
                parser,
                r"C:\Temp\Report.JSON",
                r"c:\temp\report.json",
                Path(r"C:\Temp\state_5.sqlite"),
                [],
            )

        self.assertIn("must use different destination paths", stderr.getvalue())

    def test_root_prefilter_discovers_child_missing_from_state_database(self) -> None:
        child_source = {
            "subagent": {
                "thread_spawn": {
                    "parent_thread_id": ROOT_ID,
                    "depth": 1,
                    "agent_path": "/root/research",
                }
            }
        }
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            root_path = write_rollout(codex_home, ROOT_ID, root_entries())
            child_path = write_rollout(
                codex_home,
                CHILD_ID,
                [
                    session_entry(CHILD_ID, source=child_source, conversation_id=ROOT_ID),
                    turn_entry("2026-08-23T10:03:00Z", "child-turn", "gpt-5.6-luna", "low"),
                    token_entry("2026-08-23T10:03:10Z", usage(50, 40, 10, 2)),
                ],
                suffix="10-03-00",
            )
            state = {
                ROOT_ID: audit.ThreadMetadata(ROOT_ID, rollout_path=str(root_path)),
            }
            selected = audit.restrict_rollouts_from_state(
                [root_path, child_path], None, ROOT_ID[:8], state, {}
            )

        self.assertEqual({root_path, child_path}, set(selected))

    def test_state_database_is_optional_read_only_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state_5.sqlite"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE threads (
                    id TEXT PRIMARY KEY,
                    rollout_path TEXT,
                    title TEXT,
                    model TEXT,
                    reasoning_effort TEXT,
                    agent_path TEXT,
                    agent_nickname TEXT,
                    agent_role TEXT,
                    thread_source TEXT,
                    cwd TEXT,
                    project_id TEXT,
                    tokens_used INTEGER
                );
                CREATE TABLE thread_spawn_edges (
                    parent_thread_id TEXT,
                    child_thread_id TEXT,
                    status TEXT
                );
                """
            )
            connection.execute(
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ROOT_ID, "root.jsonl", "Root title", "gpt-5.6-sol", "ultra", "", "", "", "user", r"C:\work", "helpful-scripts", 123),
            )
            connection.execute(
                "INSERT INTO thread_spawn_edges VALUES (?, ?, ?)",
                (ROOT_ID, CHILD_ID, "closed"),
            )
            connection.commit()
            connection.close()

            metadata, parents, warnings = audit.load_state_metadata(database)
            private_metadata, _, private_warnings = audit.load_state_metadata(
                database, include_titles=False
            )

        self.assertEqual([], warnings)
        self.assertEqual([], private_warnings)
        self.assertEqual("Root title", metadata[ROOT_ID].title)
        self.assertEqual("", private_metadata[ROOT_ID].title)
        self.assertEqual(123, metadata[ROOT_ID].tokens_used)
        self.assertEqual(ROOT_ID, parents[CHILD_ID])

    def test_prompt_derived_titles_are_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_rollout(Path(temporary), ROOT_ID, root_entries())
            warnings: list[str] = []
            session = audit.parse_rollout(path, warnings)
            state = {
                ROOT_ID: audit.ThreadMetadata(ROOT_ID, title="sensitive first prompt"),
            }
            private_records = audit.build_turn_records(
                [session], state, {}, False, False, warnings  # type: ignore[list-item]
            )
            titled_records = audit.build_turn_records(
                [session], state, {}, True, False, warnings  # type: ignore[list-item]
            )

        self.assertTrue(all(not record.root_title for record in private_records))
        self.assertTrue(all(record.root_title == "sensitive first prompt" for record in titled_records))

    def test_duplicate_session_prefers_largest_cumulative_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            small = write_rollout(
                codex_home,
                ROOT_ID,
                [
                    session_entry(ROOT_ID),
                    turn_entry("2026-08-23T10:00:00Z", "turn", "gpt-5.6-sol", "high"),
                    token_entry("2026-08-23T10:00:01Z", usage(10, 5, 2, 1)),
                ],
                archived=True,
            )
            large = write_rollout(codex_home, ROOT_ID, root_entries())
            warnings: list[str] = []
            sessions = [audit.parse_rollout(path, warnings) for path in (small, large)]
            selected, duplicates = audit.select_unique_sessions(
                [session for session in sessions if session], warnings
            )

        self.assertEqual(1, duplicates)
        self.assertEqual(1, len(selected))
        self.assertEqual(350, selected[0].final_usage.total_tokens)
        self.assertFalse(selected[0].archived)

    def test_json_stdout_is_machine_readable_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            write_rollout(codex_home, ROOT_ID, root_entries())
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = audit.main(
                    [
                        "--root",
                        str(codex_home),
                        "--no-state-db",
                        "--by",
                        "model_effort,thread",
                        "--json",
                        "-",
                    ]
                )
            report = json.loads(stdout.getvalue())

        self.assertEqual(0, exit_code)
        self.assertEqual(350, report["totals"]["total_tokens"])
        self.assertEqual(2, len(report["records"]))
        self.assertIn("model_effort", report["breakdowns"])
        self.assertEqual("<redacted>", report["codex_home"])
        self.assertTrue(all(not record["cwd"] for record in report["records"]))
        self.assertTrue(all(not record["source_file"] for record in report["records"]))
        self.assertNotIn("must-not-be-exported", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_json_stdout_stays_clean_when_csv_is_written_to_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            csv_path = codex_home / "turns.csv"
            write_rollout(codex_home, ROOT_ID, root_entries())
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = audit.main(
                    [
                        "--root",
                        str(codex_home),
                        "--no-state-db",
                        "--json",
                        "-",
                        "--csv",
                        str(csv_path),
                    ]
                )
            report = json.loads(stdout.getvalue())
            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(0, exit_code)
        self.assertEqual(350, report["totals"]["total_tokens"])
        self.assertEqual(2, len(rows))
        self.assertIn("wrote 2 turn rows", stderr.getvalue())
        self.assertNotIn("wrote", stdout.getvalue())

    def test_output_destinations_cannot_overwrite_inputs_or_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            rollout_path = write_rollout(codex_home, ROOT_ID, root_entries())
            original_rollout = rollout_path.read_bytes()
            state_path = codex_home / "state_5.sqlite"
            shared_path = codex_home / "report.out"

            for arguments, expected in (
                (["--json", str(rollout_path)], "rollout input"),
                (["--json", str(state_path)], "state database"),
                (["--json", str(shared_path), "--csv", str(shared_path)], "different destination"),
            ):
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                    audit.main(
                        ["--root", str(codex_home), "--no-state-db", *arguments]
                    )
                self.assertIn(expected, stderr.getvalue())

            self.assertEqual(original_rollout, rollout_path.read_bytes())
            self.assertFalse(state_path.exists())
            self.assertFalse(shared_path.exists())

    def test_output_hardlink_to_rollout_is_rejected_before_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            rollout_path = write_rollout(codex_home, ROOT_ID, root_entries())
            original_rollout = rollout_path.read_bytes()
            hardlink_path = codex_home / "report.json"
            try:
                os.link(rollout_path, hardlink_path)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                audit.main(
                    [
                        "--root",
                        str(codex_home),
                        "--no-state-db",
                        "--json",
                        str(hardlink_path),
                    ]
                )

            self.assertIn("rollout input", stderr.getvalue())
            self.assertEqual(original_rollout, rollout_path.read_bytes())

    def test_explicit_missing_state_database_warns_and_strict_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryDirectory() as external:
            codex_home = Path(temporary)
            write_rollout(codex_home, ROOT_ID, root_entries())
            missing = Path(external) / "missing-state.sqlite"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = audit.main(
                    [
                        "--root",
                        str(codex_home),
                        "--state-db",
                        str(missing),
                        "--strict",
                        "--json",
                        "-",
                    ]
                )
            report = json.loads(stdout.getvalue())

        self.assertEqual(2, exit_code)
        self.assertTrue(any("explicit state database" in item for item in report["warnings"]))
        self.assertIn("<STATE_DB>", stderr.getvalue())
        self.assertNotIn(str(missing), stdout.getvalue())
        self.assertNotIn(str(missing), stderr.getvalue())

    def test_full_ids_keep_breakdown_buckets_distinct(self) -> None:
        first_id = "aaaaaaaa-0000-0000-0000-000000000001"
        second_id = "aaaaaaaa-0000-0000-0000-000000000002"
        first = audit.TurnRecord(
            timestamp="2026-08-23T10:00:00Z",
            day="2026-08-23",
            root_thread_id=first_id,
            root_title="",
            thread_id=first_id,
            parent_thread_id="",
            depth=0,
            thread_type="root",
            agent_path="",
            agent_nickname="",
            agent_role="",
            turn_id="turn-1",
            turn_index=1,
            model_calls=1,
            model="gpt-5.6-sol",
            reasoning_effort="high",
            client_source="vscode",
            originator="Codex Desktop",
            cli_version="0.test",
            project="helpful-scripts",
            cwd="",
            archived=False,
            input_tokens=10,
            cached_input_tokens=5,
            fresh_input_tokens=5,
            cache_write_input_tokens=0,
            output_tokens=2,
            reasoning_output_tokens=1,
            visible_output_tokens=1,
            total_tokens=12,
            estimated_credits=0.001,
            source_file="",
        )
        second = replace(
            first,
            root_thread_id=second_id,
            thread_id=second_id,
            turn_id="turn-2",
        )

        for dimension in ("task", "session", "turn"):
            rows = audit.aggregate([first, second], audit.DIMENSION_KEYS[dimension])
            self.assertEqual(2, len(rows), dimension)

    def test_csv_stdout_remains_clean_while_strict_warnings_use_stderr(self) -> None:
        entries = root_entries()
        entries.insert(2, "{not-json")
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            write_rollout(codex_home, ROOT_ID, entries)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = audit.main(
                    [
                        "--root",
                        str(codex_home),
                        "--no-state-db",
                        "--strict",
                        "--csv",
                        "-",
                    ]
                )

        rows = list(csv.DictReader(io.StringIO(stdout.getvalue())))
        self.assertEqual(2, exit_code)
        self.assertEqual(2, len(rows))
        self.assertIn("malformed JSONL", stderr.getvalue())
        self.assertIn("<CODEX_HOME>", stderr.getvalue())
        self.assertNotIn(temporary, stderr.getvalue())

    def test_date_and_subagent_filters_are_applied_after_tree_linking(self) -> None:
        records = [
            audit.TurnRecord(
                timestamp="2026-08-22T10:00:00Z",
                day="2026-08-22",
                root_thread_id=ROOT_ID,
                root_title="",
                thread_id=ROOT_ID,
                parent_thread_id="",
                depth=0,
                thread_type="root",
                agent_path="",
                agent_nickname="",
                agent_role="",
                turn_id="root-turn",
                turn_index=1,
                model_calls=1,
                model="gpt-5.6-sol",
                reasoning_effort="high",
                client_source="vscode",
                originator="Codex Desktop",
                cli_version="0.test",
                project="helpful-scripts",
                cwd=r"C:\work\helpful-scripts",
                archived=False,
                input_tokens=10,
                cached_input_tokens=5,
                fresh_input_tokens=5,
                cache_write_input_tokens=0,
                output_tokens=2,
                reasoning_output_tokens=1,
                visible_output_tokens=1,
                total_tokens=12,
                estimated_credits=0.001,
                source_file="root.jsonl",
            ),
            audit.TurnRecord(
                timestamp="2026-08-23T10:00:00Z",
                day="2026-08-23",
                root_thread_id=ROOT_ID,
                root_title="",
                thread_id=CHILD_ID,
                parent_thread_id=ROOT_ID,
                depth=1,
                thread_type="subagent",
                agent_path="/root/research",
                agent_nickname="Gauss",
                agent_role="",
                turn_id="child-turn",
                turn_index=1,
                model_calls=1,
                model="gpt-5.6-luna",
                reasoning_effort="low",
                client_source="subagent",
                originator="Codex Desktop",
                cli_version="0.test",
                project="helpful-scripts",
                cwd=r"C:\work\helpful-scripts",
                archived=False,
                input_tokens=20,
                cached_input_tokens=10,
                fresh_input_tokens=10,
                cache_write_input_tokens=0,
                output_tokens=4,
                reasoning_output_tokens=2,
                visible_output_tokens=2,
                total_tokens=24,
                estimated_credits=0.001,
                source_file="child.jsonl",
            ),
        ]
        args = audit.build_parser().parse_args(
            [
                "--since",
                "2026-08-23",
                "--thread-type",
                "subagent",
                "--agent",
                "research",
                "--model",
                "luna",
                "--effort",
                "low",
            ]
        )

        selected = audit.filter_records(records, args)

        self.assertEqual([CHILD_ID], [record.thread_id for record in selected])

    def test_copied_uuid_turns_keep_the_most_complete_copy(self) -> None:
        base = audit.TurnRecord(
            timestamp="2026-08-23T10:00:00Z",
            day="2026-08-23",
            root_thread_id=ROOT_ID,
            root_title="",
            thread_id=ROOT_ID,
            parent_thread_id="",
            depth=0,
            thread_type="root",
            agent_path="",
            agent_nickname="",
            agent_role="",
            turn_id="44444444-4444-4444-4444-444444444444",
            turn_index=1,
            model_calls=1,
            model="gpt-5.6-sol",
            reasoning_effort="high",
            client_source="vscode",
            originator="Codex Desktop",
            cli_version="0.test",
            project="helpful-scripts",
            cwd=r"C:\work\helpful-scripts",
            archived=False,
            input_tokens=80,
            cached_input_tokens=40,
            fresh_input_tokens=40,
            cache_write_input_tokens=0,
            output_tokens=20,
            reasoning_output_tokens=10,
            visible_output_tokens=10,
            total_tokens=100,
            estimated_credits=0.001,
            source_file="root.jsonl",
        )
        fuller_child = replace(
            base,
            thread_id=CHILD_ID,
            parent_thread_id=ROOT_ID,
            depth=1,
            thread_type="subagent",
            agent_path="/root/research",
            total_tokens=120,
            source_file="child.jsonl",
        )
        tied_root = replace(
            base,
            turn_id="55555555-5555-5555-5555-555555555555",
            total_tokens=50,
        )
        tied_child = replace(
            fuller_child,
            turn_id=tied_root.turn_id,
            total_tokens=50,
        )
        legacy_root = replace(base, turn_id="legacy-unattributed", total_tokens=10)
        legacy_child = replace(
            fuller_child,
            turn_id="legacy-unattributed",
            total_tokens=20,
        )

        selected, removed = audit.deduplicate_copied_turns(
            [base, fuller_child, tied_root, tied_child, legacy_root, legacy_child]
        )

        identities = {(record.thread_id, record.turn_id) for record in selected}
        self.assertEqual(2, removed)
        self.assertIn((CHILD_ID, base.turn_id), identities)
        self.assertNotIn((ROOT_ID, base.turn_id), identities)
        self.assertIn((ROOT_ID, tied_root.turn_id), identities)
        self.assertNotIn((CHILD_ID, tied_root.turn_id), identities)
        self.assertIn((ROOT_ID, "legacy-unattributed"), identities)
        self.assertIn((CHILD_ID, "legacy-unattributed"), identities)


if __name__ == "__main__":
    unittest.main()
