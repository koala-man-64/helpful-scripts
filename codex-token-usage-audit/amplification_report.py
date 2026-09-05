"""Produce a bounded, body-free amplification diagnostic from explicit rollouts.

This is a passive local report.  It reuses ``codex_token_usage_audit`` for all
token accounting and scans only activity metadata needed for body-free tool
counts, output byte counts, and repeat fingerprints.  It is not a usage ledger,
quota attribution, scheduler, or savings calculator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import codex_token_usage_audit as audit


REPORT_SCHEMA_VERSION = "amplification-report-v1"
BASELINE_SCHEMA_VERSION = "amplification-task-class-baseline-v1"
DEFAULT_TOP = 10
MAX_TOP = 100
_CALL_TYPES = frozenset({"function_call", "custom_tool_call"})
_OUTPUT_TYPES = frozenset({"function_call_output", "custom_tool_call_output"})
_STATE_FIELDS = ("state_version", "operation_id", "revision", "cursor", "path")
_STATE_VERSION_FIELDS = ("state_version", "revision", "cursor")
_STATE_IDENTITY_FIELDS = ("operation_id", "path")
_MAX_LABEL_LENGTH = 96


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _value_bytes(value: object) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return len(_canonical_bytes(value))


def _safe_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return "unknown"
    label = value.strip()
    if len(label) <= _MAX_LABEL_LENGTH:
        return label
    return label[:80] + "#" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]


def _nonempty_scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) and not (
        isinstance(value, str) and not value.strip()
    )


def _state_qualifier(payload: Mapping[str, Any]) -> tuple[str | None, str]:
    state = {
        field: payload[field]
        for field in _STATE_FIELDS
        if field in payload and _nonempty_scalar(payload[field])
    }
    nested = payload.get("state")
    if isinstance(nested, Mapping):
        for field in _STATE_FIELDS:
            value = nested.get(field)
            if field not in state and _nonempty_scalar(value):
                state[field] = value
    if not (
        any(field in state for field in _STATE_VERSION_FIELDS)
        and any(field in state for field in _STATE_IDENTITY_FIELDS)
    ):
        return None, "unknown"
    return _digest(state), "explicit"


def _activity_metrics(paths: Iterable[Path], top: int) -> dict[str, Any]:
    call_records = 0
    wait_calls = 0
    output_records = 0
    output_bytes = 0
    malformed = 0
    duplicate_records = 0
    call_conflicts = 0
    output_conflicts = 0
    missing_call_ids = 0
    missing_output_ids = 0
    groups: dict[tuple[str, str, str | None], int] = Counter()
    seen_calls: dict[str, str] = {}
    seen_outputs: dict[str, str] = {}
    for path in paths:
        try:
            lines = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with lines:
            for line in lines:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(entry, Mapping) or entry.get("type") != "response_item":
                    continue
                payload = entry.get("payload")
                if not isinstance(payload, Mapping):
                    continue
                item_type = payload.get("type")
                if item_type in _CALL_TYPES:
                    name = _safe_name(payload.get("name"))
                    call_body = {
                        "name": name,
                        "input": payload.get("input"),
                        "arguments": payload.get("arguments"),
                    }
                    state_digest, state_status = _state_qualifier(payload)
                    record_digest = _digest(payload)
                    durable_id = payload.get("call_id") or payload.get("id")
                    if isinstance(durable_id, str) and durable_id:
                        prior = seen_calls.get(durable_id)
                        if prior is not None:
                            if prior == record_digest:
                                duplicate_records += 1
                                continue
                            call_conflicts += 1
                            continue
                        seen_calls[durable_id] = record_digest
                    else:
                        missing_call_ids += 1
                    groups[(_digest(call_body), name, state_digest)] += 1
                    call_records += 1
                    if name in {"wait", "wait_threads"}:
                        wait_calls += 1
                elif item_type in _OUTPUT_TYPES:
                    output = payload.get("output")
                    record_digest = _digest(payload)
                    durable_id = payload.get("call_id") or payload.get("id")
                    if isinstance(durable_id, str) and durable_id:
                        prior = seen_outputs.get(durable_id)
                        if prior is not None:
                            if prior == record_digest:
                                duplicate_records += 1
                                continue
                            output_conflicts += 1
                            continue
                        seen_outputs[durable_id] = record_digest
                    else:
                        missing_output_ids += 1
                    if output is not None:
                        output_bytes += _value_bytes(output)
                    output_records += 1

    repeated = [
        {
            "fingerprint": fingerprint,
            "tool": tool,
            "state": "explicit" if state_digest else "unknown",
            "state_fingerprint": state_digest,
            "occurrences": occurrences,
        }
        for (fingerprint, tool, state_digest), occurrences in groups.items()
        if occurrences > 1
    ]
    repeated.sort(key=lambda row: (-row["occurrences"], row["tool"], row["fingerprint"]))
    return {
        "observed_call_records": call_records,
        "observed_unique_call_ids": len(seen_calls),
        "executed_call_count": None,
        "executed_call_count_status": "execution_not_verified",
        "observed_wait_calls": wait_calls,
        "observed_tool_result_records": output_records,
        "observed_tool_result_text_bytes_estimate": output_bytes,
        "duplicate_records_deduplicated": duplicate_records,
        "conflicting_durable_call_ids": call_conflicts,
        "conflicting_durable_output_ids": output_conflicts,
        "call_records_without_durable_ids": missing_call_ids,
        "output_records_without_durable_ids": missing_output_ids,
        "partial_reasons": ["conflicting_durable_id"] if call_conflicts or output_conflicts else [],
        "malformed_activity_lines": malformed,
        "exact_repeat_groups": len(repeated),
        "repeat_occurrences_after_first": sum(row["occurrences"] - 1 for row in repeated),
        "state_qualified_repeat_groups": sum(row["state"] == "explicit" for row in repeated),
        "unknown_state_repeat_groups": sum(row["state"] == "unknown" for row in repeated),
        "top_repeat_groups": repeated[:top],
    }


def _token_metrics(records: Sequence[audit.TurnRecord]) -> dict[str, int]:
    return {
        "observed_turns": len({(record.thread_id, record.turn_id) for record in records}),
        "observed_request_increments": sum(record.model_calls for record in records),
        "input_tokens_including_cache": sum(record.input_tokens for record in records),
        "cached_input_tokens_subset": sum(record.cached_input_tokens for record in records),
        "uncached_input_tokens": sum(record.fresh_input_tokens for record in records),
        "cache_write_input_tokens": sum(record.cache_write_input_tokens for record in records),
        "output_tokens_including_reasoning": sum(record.output_tokens for record in records),
        "reasoning_output_tokens_subset": sum(
            record.reasoning_output_tokens for record in records
        ),
        "visible_output_tokens": sum(record.visible_output_tokens for record in records),
        "total_tokens": sum(record.total_tokens for record in records),
    }


def _bounded_task_class(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("task_class must be a non-empty string")
    label = value.strip()
    if len(label) > _MAX_LABEL_LENGTH:
        raise ValueError(f"task_class must be at most {_MAX_LABEL_LENGTH} characters")
    return label


def _unique_files(paths: Iterable[Path]) -> list[Path]:
    selected: list[Path] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if any(
            resolved == existing
            or (resolved.exists() and existing.exists() and os.path.samefile(resolved, existing))
            for existing in selected
        ):
            continue
        selected.append(resolved)
    return selected


def _seal_inputs(paths: Iterable[Path]) -> dict[Path, tuple[str, int]]:
    seals: dict[Path, tuple[str, int]] = {}
    for path in paths:
        raw = path.read_bytes()
        seals[path] = ("sha256:" + hashlib.sha256(raw).hexdigest(), len(raw))
    return seals


def _verify_seals(seals: Mapping[Path, tuple[str, int]]) -> None:
    for path, (expected_digest, expected_length) in seals.items():
        raw = path.read_bytes()
        if len(raw) != expected_length or "sha256:" + hashlib.sha256(raw).hexdigest() != expected_digest:
            raise ValueError("rollout input changed during report generation")


def _source_provenance(paths: Iterable[Path], seals: Mapping[Path, tuple[str, int]]) -> dict[str, Any]:
    return {
        "source_content_digests": [
            {
                "digest": seals[path][0],
                "byte_length": seals[path][1],
            }
            for path in paths
        ],
        "parser_pin": {
            "path_label": "codex_token_usage_audit.py",
            "sha256": hashlib.sha256(Path(audit.__file__).read_bytes()).hexdigest(),
        },
        "activity_report_pin": {
            "path_label": "amplification_report.py",
            "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    }


def _read_baseline(path: Path, task_class: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("baseline must be readable JSON") from error
    if not isinstance(value, Mapping):
        raise ValueError("baseline must be a JSON object")
    if value.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise ValueError("baseline schema_version is not supported")
    if not isinstance(value.get("task_class"), str) or not value["task_class"]:
        raise ValueError("baseline task_class is required")
    if value.get("task_class") != task_class or value.get("comparable") is not True:
        return {"status": "not_comparable", "reason": "task_class_or_comparability_mismatch"}
    bounds = value.get("upper_bounds")
    if not isinstance(bounds, Mapping):
        raise ValueError("comparable baseline upper_bounds must be an object")
    accepted = {
        name: number
        for name, number in bounds.items()
        if name in {"requests_per_turn", "input_tokens_per_request"}
        and isinstance(number, (int, float))
        and not isinstance(number, bool)
        and math.isfinite(number)
        and number >= 0
    }
    if not accepted or len(accepted) != len(bounds):
        raise ValueError("comparable baseline upper_bounds must contain finite supported metrics")
    return {"status": "comparable", "upper_bounds": accepted}


def _comparison(token_metrics: Mapping[str, int], baseline: Mapping[str, Any]) -> dict[str, Any]:
    requests = token_metrics["observed_request_increments"]
    turns = token_metrics["observed_turns"]
    observed = {
        "requests_per_turn": requests / turns if turns else None,
        "input_tokens_per_request": (
            token_metrics["input_tokens_including_cache"] / requests if requests else None
        ),
    }
    if baseline.get("status") != "comparable":
        return {**baseline, "observed": observed}
    excesses = {
        name: max(0.0, observed[name] - bound)
        for name, bound in baseline["upper_bounds"].items()
        if observed[name] is not None
    }
    return {
        "status": "comparable",
        "upper_bounds": baseline["upper_bounds"],
        "upper_bounds_source": "caller_supplied_task_class_baseline",
        "observed": observed,
        "excesses_over_upper_bounds": excesses,
        "outlier_metrics": sorted(name for name, excess in excesses.items() if excess > 0),
    }


def build_report(
    rollout_paths: Sequence[Path], *, task_class: str = "unknown", baseline: Path | None = None,
    top: int = DEFAULT_TOP,
) -> dict[str, Any]:
    """Build a report without mutating rollouts, parser state, or usage totals."""
    if not rollout_paths:
        raise ValueError("at least one explicit rollout is required")
    task_class = _bounded_task_class(task_class)
    if not isinstance(top, int) or isinstance(top, bool) or not 1 <= top <= MAX_TOP:
        raise ValueError(f"top must be between 1 and {MAX_TOP}")
    rollout_paths = _unique_files(rollout_paths)
    baseline = baseline.expanduser().resolve(strict=True) if baseline is not None else None
    seals = _seal_inputs([*rollout_paths, *([baseline] if baseline is not None else [])])

    warnings: list[str] = []
    parsed = [
        session
        for path in rollout_paths
        if (session := audit.parse_rollout(path, warnings)) is not None
    ]
    sessions, duplicate_sessions = audit.select_unique_sessions(parsed, warnings)
    records = audit.build_turn_records(
        sessions, {}, {}, include_titles=False, include_credits=False, warnings=warnings
    )
    records, copied_turns = audit.deduplicate_copied_turns(records)
    token_metrics = _token_metrics(records)
    selected_activity_paths = _unique_files(
        Path(session.source_file) for session in sessions if session.source_file
    )
    if any(path not in seals for path in selected_activity_paths):
        raise ValueError("selected session source was not one of the sealed inputs")
    baseline_result = _comparison(
        token_metrics,
        _read_baseline(baseline, task_class) if baseline is not None else {"status": "not_supplied"},
    )
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "capability": "passive_body_free_diagnostic",
        "task_class": task_class,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {
            "explicit_rollouts": len(rollout_paths),
            "parsed_sessions": len(parsed),
            "selected_sessions": len(sessions),
            "activity_source_rollouts": len(selected_activity_paths),
            "duplicate_sessions_deduplicated": duplicate_sessions,
            "copied_turns_deduplicated": copied_turns,
            "parser_warning_count": len(warnings),
        },
        "provenance": _source_provenance(selected_activity_paths, seals),
        "token_metrics": token_metrics,
        "activity_metrics": _activity_metrics(selected_activity_paths, top),
        "baseline_comparison": baseline_result,
        "attribution_completeness": {
            "lineage": "partial: observed parent links only; global closure is unknown",
            "external_wait_wakes": "unknown: observed wait calls have no provider state transition or wake receipt",
            "external_io": "unknown: tool-result text-byte estimates do not identify provider I/O",
            "quota_or_subscription_debits": "unknown: local tokens are not subscription debits",
            "intervention_or_rework": "unknown: normalized observations have no attributable intervention outcome",
            "hook_latency_or_bytes": "unknown",
        },
        "limitations": [
            "Cached input is a subset of input; reasoning output is a subset of output.",
            "Static nested call sites are not executed calls and are not counted.",
            "Repeat fingerprints identify identical retained call bodies; repeats are not classified as waste.",
            "Unknown state prevents a repeat group from being state-qualified.",
            "No raw prompts, tool arguments, tool results, paths, identifiers, or prices are emitted.",
            "Tool-result bytes are serialized text estimates, not wire bytes or provider context tokens.",
        ],
    }
    report["provenance"]["baseline"] = (
        {"digest": seals[baseline][0], "byte_length": seals[baseline][1]} if baseline is not None else None
    )
    _verify_seals(seals)
    return report


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollout", action="append", required=True, type=Path)
    parser.add_argument("--task-class", default="unknown")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--output", default="-", help="JSON destination; default is stdout")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    rollout_paths = [path.expanduser().resolve() for path in args.rollout]
    if any(not path.is_file() for path in rollout_paths):
        raise ValueError("every --rollout must name an existing file")
    if args.output != "-":
        output = Path(args.output).expanduser().resolve()
        baseline = args.baseline.expanduser().resolve() if args.baseline else None
        if output in rollout_paths or output == baseline or any(
            output.exists() and path.exists() and os.path.samefile(output, path)
            for path in rollout_paths
        ):
            raise ValueError("output must not overwrite a rollout")
    report = build_report(
        rollout_paths, task_class=args.task_class, baseline=args.baseline, top=args.top
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output == "-":
        sys.stdout.write(encoded)
    else:
        try:
            with output.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
        except FileExistsError as error:
            raise ValueError("output must be a new file") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
