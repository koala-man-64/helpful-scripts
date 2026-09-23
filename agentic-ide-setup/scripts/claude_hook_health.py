"""Report hook and delegation health from local Claude Code data over a time window.

Reads (read-only): transcripts under ~/.claude/projects/**/*.jsonl (including
subagent transcripts and their sibling .meta.json), the agent-ladder log at
~/.claude/logs/agent-ladder.jsonl, and the wait registry at
~/.claude/waits/registry.json. Consolidates the logic of usage_audit.py,
denies_and_models.py, sample_denies.py and prompt_injection_split.py
(claude-config-remediation-evidence) into one reusable, testable report.
"""

import argparse
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_LOGS_DIR = Path.home() / ".claude" / "logs"
DEFAULT_WAITS_FILE = Path.home() / ".claude" / "waits" / "registry.json"

# tool_result error text -> guard rule name (denies_and_models.py's DENY_STRINGS).
GUARD_RULES = {
    "secret-print": "secret-bearing values",
    "external recursive delete/move": "Recursive delete or move outside",
    "protected-branch push": "Direct pushes to protected branches",
    "reset --hard": "git reset --hard is blocked",
    "git clean -fd": "git clean with force/delete flags is blocked",
    "checkout --": "git checkout -- is blocked",
    "branch -D": "git branch -D is blocked",
    "push --force": "git push --force is blocked",
    "prod approval": "Production deployment approvals are user-owned",
}
SETTINGS_DENY_PREFIX = "Permission to use "
ASK_HOOK_NAMES = ("PreToolUse:Bash", "PreToolUse:PowerShell")
CONTEXT_EVENTS = ("UserPromptSubmit", "SessionStart")
STOP_FEEDBACK_PREFIX = "Stop hook feedback:"
MODEL_FAMILIES = ("fable", "opus", "sonnet", "haiku")
SYNTHETIC_FAMILY = "synthetic"
WORKFLOW_AGENT_TYPE = "workflow-subagent"
SESSION_LIMIT_TEXT = "You've hit your session limit"

_TIMESTAMP_RE = re.compile(r'"timestamp":"([^"]*)"')
_PERMISSION_DECISION_RE = re.compile(r'"permissionDecision":\s*"(\w+)"')
_BRACKET_TAG_RE = re.compile(r"\[[^\]]*\]:\s*")
_SUMMARY_RE = re.compile(r"summary:\s*(.*)$|recap[^:]*:\s*(.*)$", re.S)


def in_window(timestamp: str, since: str, until: str) -> bool:
    day = (timestamp or "")[:10]
    if len(day) != 10:
        return False
    return since <= day <= until


def pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile, matching usage_audit.py's pct()."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[k]


def middle(sorted_values: list) -> float:
    """The element at len//2 of an already-sorted list: prompt_injection_split.py's
    "median" (no averaging on an even count). Kept separate from pct() so the
    injected-context baseline stays reproducible if pct()'s formula ever changes.
    """
    if not sorted_values:
        return 0
    return sorted_values[len(sorted_values) // 2]


def family(model: str) -> str:
    low = model.lower()
    for name in MODEL_FAMILIES:
        if name in low:
            return name
    return model or "?"


def classify_source(text: str) -> str:
    if text.startswith("Team workflow routing:"):
        return "router"
    if "AGENTCOORD UNTRUSTED PEER MESSAGES" in text:
        return "agentcoord peer messages"
    if text.startswith("Team workflow context:"):
        return "session context"
    if text.startswith("Task note"):
        return "task note"
    if text.startswith("Outstanding delivery waits"):
        return "waits"
    return "other"


def _hook_base(name: str) -> str:
    """SessionStart/SubagentStart carry a trigger suffix (":startup", ":resume", ...)
    that usage_audit.py folds together; other names (e.g. PreToolUse:Bash) are kept
    whole so tool-specific stats stay distinct.
    """
    if name.startswith(("SessionStart", "SubagentStart")):
        return name.split(":")[0]
    return name


def _flatten_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content or "")


def _extract_timestamp(line: str) -> str:
    match = _TIMESTAMP_RE.search(line)
    return match.group(1) if match else ""


def _new_accumulator() -> dict:
    return {
        "guard_denials": Counter(),
        "guard_asks": Counter(),
        "settings_denials": 0,
        "stop_blocks_by_day": Counter(),
        "stop_blocks_total": 0,
        "stop_reason_items": Counter(),
        "hook_latency": {},
        "hook_cancelled": Counter(),
        "subagent_model_family": {},
        "subagent_spawn_depth": Counter(),
        "workflow_total": 0,
        "workflow_session_limit_killed": 0,
        "injected_context": {},
    }


def _record_denial(acc: dict, content) -> None:
    dumped = json.dumps(content)
    for rule, needle in GUARD_RULES.items():
        if needle in dumped:
            acc["guard_denials"][rule] += 1
            return
    if _flatten_text(content).startswith(SETTINGS_DENY_PREFIX):
        acc["settings_denials"] += 1


def _record_stop_block(acc: dict, timestamp: str, text: str) -> None:
    body = text[len(STOP_FEEDBACK_PREFIX):].strip()
    norm = _BRACKET_TAG_RE.sub("", body)
    day = timestamp[:10] or "?"
    acc["stop_blocks_by_day"][day] += 1
    acc["stop_blocks_total"] += 1
    match = _SUMMARY_RE.search(norm)
    items_text = (match.group(1) or match.group(2) or "") if match else norm
    for item in re.split(r";\s*", items_text.rstrip(". \n")):
        item = item.strip()
        if item:
            acc["stop_reason_items"][item[:120]] += 1


def _process_attachment_line(line: str, since: str, until: str, acc: dict, is_sub: bool) -> None:
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return
    if not in_window(str(rec.get("timestamp") or ""), since, until):
        return
    att = rec.get("attachment") or {}
    atype = att.get("type")
    name = str(att.get("hookName") or "")
    base = _hook_base(name)

    if atype == "hook_success":
        command = str(att.get("command") or "")
        key = f"{base} [{command}]"
        duration = att.get("durationMs")
        if isinstance(duration, (int, float)):
            acc["hook_latency"].setdefault(key, []).append(float(duration))
        if name in ASK_HOOK_NAMES:
            match = _PERMISSION_DECISION_RE.search(str(att.get("stdout") or ""))
            if match and match.group(1) == "ask":
                acc["guard_asks"][name] += 1
    elif atype == "hook_cancelled":
        command = str(att.get("command") or "")
        acc["hook_cancelled"][f"{base} [{command}]"] += 1
    elif atype == "hook_additional_context":
        if is_sub or base not in CONTEXT_EVENTS:
            return
        content = att.get("content")
        parts = content if isinstance(content, list) else [str(content or "")]
        for part in parts:
            text = str(part)
            acc["injected_context"].setdefault(classify_source(text), []).append(len(text))


def _process_user_line(line: str, since: str, until: str, acc: dict) -> None:
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return
    ts = str(rec.get("timestamp") or "")
    if not in_window(ts, since, until):
        return
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    else:
        text = "\n".join(
            b.get("text", "") for b in (content or []) if isinstance(b, dict) and b.get("type") == "text"
        )
    if text.startswith(STOP_FEEDBACK_PREFIX):
        _record_stop_block(acc, ts, text)
        return
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                _record_denial(acc, block.get("content"))


def _process_line(line: str, since: str, until: str, acc: dict, is_sub: bool) -> None:
    if not line.startswith("{"):
        return
    if '"type":"attachment"' in line:
        _process_attachment_line(line, since, until, acc, is_sub)
    elif '"type":"user"' in line:
        _process_user_line(line, since, until, acc)


def _track_subagent_assistant(line: str, state: dict) -> None:
    """Feed one line of a subagent transcript into its per-file model-family state.

    Mirrors denies_and_models.py (first real, non-synthetic message.model wins) and
    sample_denies.py (the first synthetic message's text is the "reason", but only
    matters when no real model ever appears in the transcript).
    """
    if state["real_family"] is not None:
        return
    if '"type":"assistant"' not in line:
        return
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return
    msg = rec.get("message") or {}
    model = str(msg.get("model") or "")
    if model and not model.startswith("<"):
        state["real_family"] = family(model)
        return
    if state["first_synthetic_text"] is None:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    state["first_synthetic_text"] = block.get("text", "")
                    break


def _finalize_subagent(path: Path, first_ts: str, since: str, until: str, acc: dict, state: dict) -> None:
    if not in_window(first_ts, since, until):
        return
    meta_path = path.with_suffix(".meta.json")
    if not meta_path.is_file():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    agent_type = str(meta.get("agentType") or "?")
    acc["subagent_spawn_depth"][str(meta.get("spawnDepth"))] += 1
    family_label = state["real_family"] or SYNTHETIC_FAMILY
    acc["subagent_model_family"].setdefault(agent_type, Counter())[family_label] += 1
    if agent_type == WORKFLOW_AGENT_TYPE:
        acc["workflow_total"] += 1
        killed = state["real_family"] is None and (state["first_synthetic_text"] or "").startswith(SESSION_LIMIT_TEXT)
        if killed:
            acc["workflow_session_limit_killed"] += 1


def _process_file(path: Path, since: str, until: str, acc: dict) -> None:
    is_sub = "subagents" in path.parts
    sub_state = {"real_family": None, "first_synthetic_text": None} if is_sub else None
    first_ts = ""
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for index, line in enumerate(handle):
            if index == 0:
                first_ts = _extract_timestamp(line)
            _process_line(line, since, until, acc, is_sub)
            if sub_state is not None:
                _track_subagent_assistant(line, sub_state)
    if sub_state is not None:
        _finalize_subagent(path, first_ts, since, until, acc, sub_state)


def scan_projects(projects_dir: Path, since: str, until: str) -> dict:
    """Single pass over every transcript (main and subagent) under projects_dir."""
    acc = _new_accumulator()
    if not projects_dir.is_dir():
        return acc
    for path in sorted(projects_dir.rglob("*.jsonl")):
        _process_file(path, since, until, acc)
    return acc


def read_ladder_decisions(logs_dir: Path, since: str, until: str) -> Counter:
    counts: Counter = Counter()
    path = logs_dir / "agent-ladder.jsonl"
    if not path.is_file():
        return counts
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not in_window(str(rec.get("ts") or ""), since, until):
                continue
            counts[(str(rec.get("decision") or "?"), str(rec.get("reason_code") or "?"))] += 1
    return counts


def read_wait_registry(waits_file: Path, since: str, until: str) -> dict:
    result = {"status": Counter(), "detail_code": Counter(), "unbound_diagnostics": 0}
    if not waits_file.is_file():
        return result
    try:
        data = json.loads(waits_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return result
    for wait in data.get("waits") or []:
        if not in_window(str(wait.get("created_at") or ""), since, until):
            continue
        result["status"][str(wait.get("status") or "?")] += 1
        result["detail_code"][str(wait.get("detail_code") or "(none)")] += 1
    for diag in data.get("diagnostics") or []:
        if diag.get("code") != "WAIT_TRIGGER_UNBOUND":
            continue
        if in_window(str(diag.get("recorded_at") or ""), since, until):
            result["unbound_diagnostics"] += 1
    return result


def build_report(projects_dir: Path, logs_dir: Path, waits_file: Path, since: str, until: str) -> dict:
    report = scan_projects(projects_dir, since, until)
    report["ladder_decisions"] = read_ladder_decisions(logs_dir, since, until)
    report["waits"] = read_wait_registry(waits_file, since, until)
    report["since"] = since
    report["until"] = until
    return report


def _latency_stats(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50": pct(values, 0.5),
        "p95": pct(values, 0.95),
        "max": max(values) if values else 0.0,
        "total_ms": sum(values),
    }


def _injected_stats(sizes: list[int]) -> dict:
    ordered = sorted(sizes)
    return {
        "n": len(ordered),
        "median": middle(ordered),
        "max": max(ordered) if ordered else 0,
        "total_chars": sum(ordered),
    }


def render_markdown(report: dict) -> str:
    lines: list[str] = []
    w = lines.append

    w(f"# Claude hook health report ({report['since']} .. {report['until']})")

    denials = report["guard_denials"]
    total_denials = sum(denials.values())
    w("")
    w("## 1. Guard decisions by rule")
    w(f"Denials: {total_denials} total")
    w("")
    w("| rule | denials |")
    w("|---|---|")
    for rule in GUARD_RULES:
        w(f"| {rule} | {denials.get(rule, 0)} |")
    asks = report["guard_asks"]
    w("")
    w(f"Asks (PreToolUse permissionDecision=ask): {sum(asks.values())} total")
    for name, count in sorted(asks.items(), key=lambda kv: (-kv[1], kv[0])):
        w(f"- {name}: {count}")
    w("")
    w(f'Settings-rule or user denials (start with "{SETTINGS_DENY_PREFIX}"): {report["settings_denials"]}')

    w("")
    w("## 2. Stop-hook blocks per day, by reason item")
    w(f"Total: {report['stop_blocks_total']}")
    w("")
    w("### By day")
    for day, count in sorted(report["stop_blocks_by_day"].items()):
        w(f"- {day}: {count}")
    w("")
    w("### By reason item (top 30)")
    for item, count in sorted(report["stop_reason_items"].items(), key=lambda kv: (-kv[1], kv[0]))[:30]:
        w(f"- {item}: {count}")

    w("")
    w("## 3. Ladder decisions")
    w("| decision | reason_code | count |")
    w("|---|---|---|")
    for (decision, reason), count in sorted(report["ladder_decisions"].items(), key=lambda kv: (-kv[1], kv[0])):
        w(f"| {decision} | {reason} | {count} |")

    w("")
    w("## 4. Hook latency and cancellations")
    w("### Latency (hook_success durationMs, ms)")
    w("| hook [command] | n | p50 | p95 | max | total_s |")
    w("|---|---|---|---|---|---|")
    for key, values in sorted(report["hook_latency"].items(), key=lambda kv: -sum(kv[1])):
        stats = _latency_stats(values)
        w(f"| {key} | {stats['n']} | {stats['p50']:.0f} | {stats['p95']:.0f} | {stats['max']:.0f} | "
          f"{stats['total_ms'] / 1000:.1f} |")
    w("")
    w("### Cancellations (hook_cancelled)")
    w("| hook [command] | count |")
    w("|---|---|")
    for key, count in sorted(report["hook_cancelled"].items(), key=lambda kv: (-kv[1], kv[0])):
        w(f"| {key} | {count} |")

    w("")
    w("## 5. Subagents")
    w("### Model family by agentType")
    for agent_type, families in sorted(
        report["subagent_model_family"].items(), key=lambda kv: -sum(kv[1].values())
    ):
        fam_str = ", ".join(
            f"{fam}={count}" for fam, count in sorted(families.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        w(f"- {agent_type}: {fam_str}")
    w("")
    w("### spawnDepth counts")
    for depth, count in sorted(report["subagent_spawn_depth"].items(), key=_depth_sort_key):
        w(f"- depth {depth}: {count}")
    w("")
    w(f"### Workflow agents killed by session limit: "
      f"{report['workflow_session_limit_killed']} of {report['workflow_total']}")

    w("")
    w("## 6. Injected text per prompt, by source")
    w("| source | n | median chars | max chars | total chars |")
    w("|---|---|---|---|---|")
    for source, sizes in sorted(report["injected_context"].items(), key=lambda kv: -sum(kv[1])):
        stats = _injected_stats(sizes)
        w(f"| {source} | {stats['n']} | {stats['median']} | {stats['max']} | {stats['total_chars']} |")

    w("")
    w("## 7. Waits")
    waits = report["waits"]
    w("### By status")
    for status, count in sorted(waits["status"].items(), key=lambda kv: (-kv[1], kv[0])):
        w(f"- {status}: {count}")
    w("### By detail code")
    for code, count in sorted(waits["detail_code"].items(), key=lambda kv: (-kv[1], kv[0])):
        w(f"- {code}: {count}")
    w(f"### Unbound diagnostics in window: {waits['unbound_diagnostics']}")

    return "\n".join(lines)


def _depth_sort_key(item):
    key = item[0]
    try:
        return (0, int(key))
    except (TypeError, ValueError):
        return (1, str(key))


def to_json_dict(report: dict) -> dict:
    return {
        "since": report["since"],
        "until": report["until"],
        "guard_denials": dict(report["guard_denials"]),
        "guard_denials_total": sum(report["guard_denials"].values()),
        "guard_asks": dict(report["guard_asks"]),
        "settings_or_user_denials": report["settings_denials"],
        "stop_blocks_total": report["stop_blocks_total"],
        "stop_blocks_by_day": dict(sorted(report["stop_blocks_by_day"].items())),
        "stop_reason_items": dict(report["stop_reason_items"]),
        "ladder_decisions": [
            {"decision": decision, "reason_code": reason, "count": count}
            for (decision, reason), count in sorted(
                report["ladder_decisions"].items(), key=lambda kv: (-kv[1], kv[0])
            )
        ],
        "hook_latency": {key: _latency_stats(values) for key, values in report["hook_latency"].items()},
        "hook_cancelled": dict(report["hook_cancelled"]),
        "subagent_model_family": {
            agent_type: dict(families) for agent_type, families in report["subagent_model_family"].items()
        },
        "subagent_spawn_depth": dict(report["subagent_spawn_depth"]),
        "workflow_total": report["workflow_total"],
        "workflow_session_limit_killed": report["workflow_session_limit_killed"],
        "injected_context": {
            source: _injected_stats(sizes) for source, sizes in report["injected_context"].items()
        },
        "waits": {
            "status": dict(report["waits"]["status"]),
            "detail_code": dict(report["waits"]["detail_code"]),
            "unbound_diagnostics": report["waits"]["unbound_diagnostics"],
        },
    }


def _iso_date(value: str) -> str:
    date.fromisoformat(value)  # raises argparse.ArgumentTypeError-friendly ValueError
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report Claude Code hook and delegation health over a time window (read-only)."
    )
    parser.add_argument("--projects-dir", type=Path, default=DEFAULT_PROJECTS_DIR)
    parser.add_argument("--logs-dir", type=Path, default=DEFAULT_LOGS_DIR)
    parser.add_argument("--waits-file", type=Path, default=DEFAULT_WAITS_FILE)
    parser.add_argument("--since", type=_iso_date, default="0001-01-01", help="ISO date, inclusive")
    parser.add_argument("--until", type=_iso_date, default="9999-12-31", help="ISO date, inclusive")
    parser.add_argument("--json", type=Path, default=None, help="also write the raw numbers as JSON here")
    args = parser.parse_args(argv)

    report = build_report(args.projects_dir, args.logs_dir, args.waits_file, args.since, args.until)
    print(render_markdown(report))
    if args.json:
        args.json.write_text(json.dumps(to_json_dict(report), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
