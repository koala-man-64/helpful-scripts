"""Unit checks for claude_hook_health.py; all data is synthetic under tmp_path."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("claude_hook_health", ROOT / "scripts" / "claude_hook_health.py")
hh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hh)


def dumps(record) -> str:
    # Real transcripts are written compact (no space after ":" or ","), which is what
    # claude_hook_health.py's cheap substring pre-filters (e.g. '"type":"attachment"')
    # assume, matching usage_audit.py and friends. json.dumps's default adds spaces, so
    # fixtures must opt into the compact separators to exercise the real code path.
    return json.dumps(record, separators=(",", ":"))


def write_jsonl(path: Path, records: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def attachment(ts, atype, hook_name, **fields):
    return {"type": "attachment", "timestamp": ts, "attachment": {"type": atype, "hookName": hook_name, **fields}}


def user_text(ts, text):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}


def user_tool_result(ts, content, is_error=True):
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"role": "user", "content": [{"type": "tool_result", "content": content, "is_error": is_error}]},
    }


def assistant(ts, model, text):
    return {"type": "assistant", "timestamp": ts, "message": {"model": model, "content": [{"type": "text", "text": text}]}}


# --------------------------------------------------------------------- helpers


def test_in_window_boundaries_and_malformed():
    assert hh.in_window("2026-08-05T00:00:00Z", "2026-08-01", "2026-08-10")
    assert hh.in_window("2026-08-01T00:00:00Z", "2026-08-01", "2026-08-10")  # since is inclusive
    assert hh.in_window("2026-08-10T23:59:59Z", "2026-08-01", "2026-08-10")  # until is inclusive
    assert not hh.in_window("2026-07-31T23:59:59Z", "2026-08-01", "2026-08-10")
    assert not hh.in_window("2026-08-11T00:00:00Z", "2026-08-01", "2026-08-10")
    assert not hh.in_window("", "2026-08-01", "2026-08-10")
    assert not hh.in_window("garbage", "2026-08-01", "2026-08-10")


def test_pct_and_middle():
    values = [10, 20, 30, 40, 50]
    assert hh.pct(values, 0.5) == 30
    assert hh.pct(values, 0.95) == 50
    assert hh.pct([], 0.5) == 0.0
    assert hh.middle(sorted([10, 20, 30, 40])) == 30  # upper-middle, no averaging
    assert hh.middle([]) == 0


def test_family_and_classify_source():
    assert hh.family("claude-opus-5") == "opus"
    assert hh.family("claude-sonnet-5") == "sonnet"
    assert hh.family("claude-haiku-5") == "haiku"
    assert hh.family("some-other-model") == "some-other-model"
    assert hh.classify_source("Team workflow routing:\nfoo") == "router"
    assert hh.classify_source("blah AGENTCOORD UNTRUSTED PEER MESSAGES blah") == "agentcoord peer messages"
    assert hh.classify_source("Team workflow context:\nfoo") == "session context"
    assert hh.classify_source("Task note for AB1: still going") == "task note"
    assert hh.classify_source("Outstanding delivery waits (from earlier sessions):") == "waits"
    assert hh.classify_source("unrelated note") == "other"


def test_hook_base():
    assert hh._hook_base("SessionStart:startup") == "SessionStart"
    assert hh._hook_base("SubagentStart:resume") == "SubagentStart"
    assert hh._hook_base("PreToolUse:Bash") == "PreToolUse:Bash"
    assert hh._hook_base("UserPromptSubmit") == "UserPromptSubmit"


# --------------------------------------------------------------------- guard decisions


def test_guard_denials_by_rule(tmp_path):
    projects = tmp_path / "projects"
    write_jsonl(
        projects / "s1" / "s1.jsonl",
        [
            user_tool_result("2026-08-01T10:00:00.000Z", "Command appears to print secret-bearing values. Do not echo tokens."),
            user_tool_result("2026-08-01T10:01:00.000Z", "git reset --hard is blocked because it discards work."),
            user_tool_result("2026-08-01T10:02:00.000Z", "Permission to use Bash with command rm -rf / has been denied."),
            user_tool_result("2026-08-01T10:03:00.000Z", "some unrelated error", is_error=True),
            user_tool_result("2026-08-01T10:04:00.000Z", "not actually an error text", is_error=False),
        ],
    )
    report = hh.scan_projects(projects, "2026-08-01", "2026-08-31")
    assert report["guard_denials"]["secret-print"] == 1
    assert report["guard_denials"]["reset --hard"] == 1
    assert sum(report["guard_denials"].values()) == 2
    assert report["settings_denials"] == 1


def test_guard_asks_only_count_ask_decision(tmp_path):
    projects = tmp_path / "projects"
    write_jsonl(
        projects / "s1" / "s1.jsonl",
        [
            attachment(
                "2026-08-01T10:00:00.000Z", "hook_success", "PreToolUse:Bash",
                command="Checking shell safety", durationMs=150,
                stdout='{"hookSpecificOutput": {"permissionDecision": "ask", "permissionDecisionReason": "confirm"}}',
            ),
            attachment(
                "2026-08-01T10:00:05.000Z", "hook_success", "PreToolUse:Bash",
                command="Checking shell safety", durationMs=80,
                stdout='{"hookSpecificOutput": {"permissionDecision": "allow"}}',
            ),
            attachment(
                "2026-08-01T10:00:10.000Z", "hook_success", "PreToolUse:PowerShell",
                command="Checking shell safety", durationMs=90,
                stdout='{"hookSpecificOutput": {"permissionDecision": "ask"}}',
            ),
        ],
    )
    report = hh.scan_projects(projects, "2026-08-01", "2026-08-31")
    assert report["guard_asks"]["PreToolUse:Bash"] == 1
    assert report["guard_asks"]["PreToolUse:PowerShell"] == 1
    assert sum(report["guard_asks"].values()) == 2


# --------------------------------------------------------------------- stop blocks


def test_stop_blocks_per_day_and_reason_items(tmp_path):
    projects = tmp_path / "projects"
    write_jsonl(
        projects / "s1" / "s1.jsonl",
        [
            user_text(
                "2026-08-02T09:00:00.000Z",
                "Stop hook feedback:\n[stop-guard]: recap of what happened: Item one; Item two.",
            ),
            user_text("2026-08-02T11:00:00.000Z", "Stop hook feedback:\nDo X; Do Y; Do Z."),
            {
                "type": "user",
                "timestamp": "2026-08-02T12:00:00.000Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "Stop hook feedback:\nFinish up."}]},
            },
            user_text("2026-08-03T09:00:00.000Z", "not a stop block, just a normal prompt"),
        ],
    )
    report = hh.scan_projects(projects, "2026-08-01", "2026-08-31")
    assert report["stop_blocks_total"] == 3
    assert report["stop_blocks_by_day"] == {"2026-08-02": 3}
    assert report["stop_reason_items"]["Item one"] == 1
    assert report["stop_reason_items"]["Item two"] == 1
    assert report["stop_reason_items"]["Do X"] == 1
    assert report["stop_reason_items"]["Do Y"] == 1
    assert report["stop_reason_items"]["Do Z"] == 1
    assert report["stop_reason_items"]["Finish up"] == 1


# --------------------------------------------------------------------- ladder log


def test_ladder_decisions_window_filtering(tmp_path):
    logs_dir = tmp_path / "logs"
    write_jsonl(
        logs_dir / "agent-ladder.jsonl",
        [
            {"ts": "2026-08-01T00:00:00Z", "decision": "routed", "reason_code": "LADDER_OK"},
            {"ts": "2026-08-01T00:00:01Z", "decision": "denied", "reason_code": "LADDER_MISSING_ENVELOPE"},
            {"ts": "2026-08-01T00:00:02Z", "decision": "routed", "reason_code": "LADDER_OK"},
            {"ts": "2026-06-01T00:00:00Z", "decision": "routed", "reason_code": "LADDER_OK"},
        ],
    )
    counts = hh.read_ladder_decisions(logs_dir, "2026-08-01", "2026-08-10")
    assert counts[("routed", "LADDER_OK")] == 2
    assert counts[("denied", "LADDER_MISSING_ENVELOPE")] == 1
    assert sum(counts.values()) == 3


def test_ladder_decisions_missing_log_returns_empty(tmp_path):
    counts = hh.read_ladder_decisions(tmp_path / "no-logs", "2026-08-01", "2026-08-10")
    assert counts == {}


# --------------------------------------------------------------------- hook latency / cancellations


def test_hook_latency_normalizes_sessionstart_and_keeps_pretooluse_distinct(tmp_path):
    projects = tmp_path / "projects"
    write_jsonl(
        projects / "s1" / "s1.jsonl",
        [
            attachment(
                "2026-08-01T10:00:00.000Z", "hook_success", "SessionStart:startup",
                command="Loading team workflow context", durationMs=200, stdout="{}",
            ),
            attachment(
                "2026-08-01T10:05:00.000Z", "hook_success", "SessionStart:resume",
                command="Loading team workflow context", durationMs=300, stdout="{}",
            ),
            attachment(
                "2026-08-01T10:10:00.000Z", "hook_success", "PreToolUse:Bash",
                command="Checking shell safety", durationMs=100, stdout="{}",
            ),
        ],
    )
    report = hh.scan_projects(projects, "2026-08-01", "2026-08-31")
    assert report["hook_latency"]["SessionStart [Loading team workflow context]"] == [200.0, 300.0]
    assert report["hook_latency"]["PreToolUse:Bash [Checking shell safety]"] == [100.0]


def test_hook_cancelled_uses_same_key_shape(tmp_path):
    projects = tmp_path / "projects"
    write_jsonl(
        projects / "s1" / "s1.jsonl",
        [
            attachment(
                "2026-08-01T10:10:00.000Z", "hook_cancelled", "UserPromptSubmit",
                command="Checking agent coordination", durationMs=5827, timedOut=True, timeoutMs=5000,
            ),
        ],
    )
    report = hh.scan_projects(projects, "2026-08-01", "2026-08-31")
    assert report["hook_cancelled"]["UserPromptSubmit [Checking agent coordination]"] == 1


def test_latency_and_injected_stats_helpers():
    stats = hh._latency_stats([100.0, 200.0, 300.0])
    assert stats["n"] == 3
    assert stats["max"] == 300.0
    assert stats["total_ms"] == 600.0
    injected = hh._injected_stats([10, 30, 20])
    assert injected["n"] == 3
    assert injected["median"] == 20
    assert injected["max"] == 30
    assert injected["total_chars"] == 60


# --------------------------------------------------------------------- subagents


def test_subagent_model_family_workflow_totals_and_session_limit_deaths(tmp_path):
    projects = tmp_path / "projects"
    base = projects / "s1" / "subagents"

    # A workflow-subagent that actually ran a real model.
    write_json(base / "agent-real1.meta.json", {"agentType": "workflow-subagent", "spawnDepth": 1})
    write_jsonl(
        base / "agent-real1.jsonl",
        [
            user_text("2026-08-03T00:00:00.000Z", "do work"),
            assistant("2026-08-03T00:00:01.000Z", "claude-sonnet-5", "working"),
        ],
    )

    # A workflow-subagent killed by the session limit (synthetic-only).
    write_json(base / "agent-synth-killed.meta.json", {"agentType": "workflow-subagent", "spawnDepth": 2})
    write_jsonl(
        base / "agent-synth-killed.jsonl",
        [
            user_text("2026-08-03T01:00:00.000Z", "do work"),
            assistant("2026-08-03T01:00:01.000Z", "<synthetic>", "You've hit your session limit for this workflow."),
        ],
    )

    # A workflow-subagent that is synthetic-only but for a different reason.
    write_json(base / "agent-synth-other.meta.json", {"agentType": "workflow-subagent", "spawnDepth": 1})
    write_jsonl(
        base / "agent-synth-other.jsonl",
        [assistant("2026-08-03T02:00:00.000Z", "<synthetic>", "No response requested.")],
    )

    # A non-workflow subagent with a real (different-family) model.
    write_json(base / "agent-nonworkflow.meta.json", {"agentType": "Explore", "spawnDepth": 1})
    write_jsonl(
        base / "agent-nonworkflow.jsonl",
        [assistant("2026-08-03T03:00:00.000Z", "claude-haiku-5", "found it")],
    )

    # A workflow-subagent outside the window: must not affect any count.
    write_json(base / "agent-outside.meta.json", {"agentType": "workflow-subagent", "spawnDepth": 9})
    write_jsonl(
        base / "agent-outside.jsonl",
        [assistant("2026-06-01T00:00:00.000Z", "<synthetic>", "You've hit your session limit for this workflow.")],
    )

    report = hh.scan_projects(projects, "2026-08-01", "2026-08-10")

    assert report["workflow_total"] == 3
    assert report["workflow_session_limit_killed"] == 1
    assert report["subagent_model_family"]["workflow-subagent"] == {"sonnet": 1, "synthetic": 2}
    assert report["subagent_model_family"]["Explore"] == {"haiku": 1}
    assert report["subagent_spawn_depth"]["1"] == 3
    assert report["subagent_spawn_depth"]["2"] == 1
    assert "9" not in report["subagent_spawn_depth"]


def test_subagent_without_meta_json_is_skipped(tmp_path):
    projects = tmp_path / "projects"
    base = projects / "s1" / "subagents"
    write_jsonl(base / "agent-no-meta.jsonl", [assistant("2026-08-03T00:00:00.000Z", "claude-opus-5", "hi")])
    report = hh.scan_projects(projects, "2026-08-01", "2026-08-10")
    assert report["subagent_model_family"] == {}
    assert report["subagent_spawn_depth"] == {}


# --------------------------------------------------------------------- injected context


def test_injected_context_classification_excludes_subagents(tmp_path):
    projects = tmp_path / "projects"
    write_jsonl(
        projects / "s1" / "s1.jsonl",
        [
            attachment(
                "2026-08-01T08:00:00.000Z", "hook_additional_context", "UserPromptSubmit",
                content=["Team workflow routing:\n- Suggested lane: standard"],
            ),
            attachment(
                "2026-08-01T08:01:00.000Z", "hook_additional_context", "UserPromptSubmit",
                content=["blah AGENTCOORD UNTRUSTED PEER MESSAGES blah blah"],
            ),
            attachment(
                "2026-08-01T08:02:00.000Z", "hook_additional_context", "SessionStart",
                content=["Outstanding delivery waits (from earlier sessions):\n- one"],
            ),
            attachment(
                "2026-08-01T08:03:00.000Z", "hook_additional_context", "SessionStart",
                content=["Task note for AB1234: still going"],
            ),
            attachment(
                "2026-08-01T08:04:00.000Z", "hook_additional_context", "SessionStart",
                content=["Team workflow context:\n- repo: foo"],
            ),
            attachment(
                "2026-08-01T08:05:00.000Z", "hook_additional_context", "UserPromptSubmit",
                content=["Some unrelated note"],
            ),
            # Wrong hook event: must be excluded.
            attachment(
                "2026-08-01T08:06:00.000Z", "hook_additional_context", "SubagentStop",
                content=["Team workflow routing:\nshould not count"],
            ),
        ],
    )
    write_jsonl(
        projects / "s1" / "subagents" / "agent-ctx.jsonl",
        [
            attachment(
                "2026-08-01T08:07:00.000Z", "hook_additional_context", "UserPromptSubmit",
                content=["Team workflow routing:\nfrom a subagent, should not count"],
            ),
        ],
    )
    report = hh.scan_projects(projects, "2026-08-01", "2026-08-31")
    ctx = report["injected_context"]
    assert len(ctx["router"]) == 1
    assert len(ctx["agentcoord peer messages"]) == 1
    assert len(ctx["waits"]) == 1
    assert len(ctx["task note"]) == 1
    assert len(ctx["session context"]) == 1
    assert len(ctx["other"]) == 1


# --------------------------------------------------------------------- waits registry


def test_wait_registry_counts_and_window(tmp_path):
    waits_file = write_json(
        tmp_path / "waits" / "registry.json",
        {
            "schema_version": 1,
            "waits": [
                {"status": "succeeded", "detail_code": "pr_merged", "created_at": "2026-08-01T00:00:00+00:00"},
                {"status": "failed", "detail_code": "wait_timeout", "created_at": "2026-08-02T00:00:00+00:00"},
                {"status": "succeeded", "detail_code": "pr_merged", "created_at": "2026-06-01T00:00:00+00:00"},
            ],
            "diagnostics": [
                {"code": "WAIT_TRIGGER_UNBOUND", "recorded_at": "2026-08-01T05:00:00+00:00"},
                {"code": "WAIT_TRIGGER_UNBOUND", "recorded_at": "2026-06-01T05:00:00+00:00"},
                {"code": "SOME_OTHER_CODE", "recorded_at": "2026-08-01T06:00:00+00:00"},
            ],
        },
    )
    result = hh.read_wait_registry(waits_file, "2026-08-01", "2026-08-10")
    assert result["status"] == {"succeeded": 1, "failed": 1}
    assert result["detail_code"] == {"pr_merged": 1, "wait_timeout": 1}
    assert result["unbound_diagnostics"] == 1


def test_wait_registry_missing_file_returns_empty(tmp_path):
    result = hh.read_wait_registry(tmp_path / "nowhere.json", "2026-08-01", "2026-08-10")
    assert result == {"status": {}, "detail_code": {}, "unbound_diagnostics": 0}


# --------------------------------------------------------------------- end to end


def test_main_prints_markdown_and_writes_json(tmp_path, capsys):
    projects = tmp_path / "projects"
    write_jsonl(
        projects / "s1" / "s1.jsonl",
        [user_tool_result("2026-08-01T10:00:00.000Z", "Command appears to print secret-bearing values.")],
    )
    logs_dir = tmp_path / "logs"
    write_jsonl(logs_dir / "agent-ladder.jsonl", [{"ts": "2026-08-01T00:00:00Z", "decision": "routed", "reason_code": "LADDER_OK"}])
    waits_file = write_json(tmp_path / "waits" / "registry.json", {"waits": [], "diagnostics": []})
    json_out = tmp_path / "out.json"

    rc = hh.main(
        [
            "--projects-dir", str(projects),
            "--logs-dir", str(logs_dir),
            "--waits-file", str(waits_file),
            "--since", "2026-08-01",
            "--until", "2026-08-31",
            "--json", str(json_out),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "# Claude hook health report" in out
    assert "secret-print" in out

    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["guard_denials"]["secret-print"] == 1
    assert payload["guard_denials_total"] == 1
    assert payload["ladder_decisions"] == [{"decision": "routed", "reason_code": "LADDER_OK", "count": 1}]


def test_main_rejects_malformed_date(tmp_path, capsys):
    try:
        hh.main(["--projects-dir", str(tmp_path), "--since", "not-a-date"])
        assert False, "expected SystemExit from argparse"
    except SystemExit as exc:
        assert exc.code != 0
