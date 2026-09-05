"""Accounting-format diagnostics; semantic acceptance remains unavailable."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping

from .manifest import FIXED_MANIFEST

HOST_ONLY = {
    "research-compaction-retention",
    "research-resumable-wait",
    "cross-urgent-peer-negation",
    "cross-child-review-recovery",
}


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_hook_usage(source_root: Path, *, expected_digest: str | None = None):
    """Load only the checked hook source; never the installed runtime."""
    usage_path = source_root / "src" / "codex_workflow_hooks" / "usage.py"
    if not usage_path.is_file() or (expected_digest and _digest(usage_path) != expected_digest):
        raise ValueError("hook source usage API is unavailable")
    # Explicit source bytes avoid import-cache reuse and bytecode writes, even
    # when the caller's interpreter was not started with -B.
    module = ModuleType("benchmark_hook_usage")
    module.__file__ = str(usage_path)
    exec(compile(usage_path.read_bytes(), str(usage_path), "exec"), module.__dict__)
    return module


def capability_report(task_id: str) -> dict[str, str]:
    if task_id in HOST_ONLY:
        return {"status": "unsupported", "reason": "no supported CLI host-event receipt"}
    return {"status": "format_only", "reason": "no deterministic semantic validator is implemented"}


def validate_accounting(receipt: Any, evidence: Mapping[str, Any], usage_api: Any) -> bool:
    """Check supplied totals, not completeness of the independent attempt census."""
    refs = getattr(receipt, "raw_artifact_refs", {})
    if not isinstance(refs, Mapping):
        return False
    usage_ref, measures_ref = refs.get("usage_observations"), refs.get("measurements")
    if not isinstance(usage_ref, Mapping) or not isinstance(measures_ref, Mapping):
        return False
    usage_path, measures_path = Path(str(usage_ref.get("path", ""))), Path(str(measures_ref.get("path", "")))
    if not usage_path.is_file() or not measures_path.is_file() or _digest(usage_path) != usage_ref.get("digest") or _digest(measures_path) != measures_ref.get("digest"):
        return False
    try:
        rows = usage_api.validate_export(json.loads(usage_path.read_text(encoding="utf-8")))
        measures = json.loads(measures_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    mode = measures.get("accounting_mode")
    if measures.get("schema_version") != "benchmark-measurements-v1" or measures.get("run_id") != receipt.run_id or mode not in {"request", "cumulative"}:
        return False
    task_ids = set(measures.get("task_ids", ()))
    if task_ids != {receipt.task_id, *receipt.child_task_ids}:
        return False
    selected = [row for row in rows if row["kind"] == mode and row["attribution"] in {"root", "child"}]
    if {row["task_id"] for row in selected} != task_ids:
        return False
    if any(row["attribution"] != ("root" if row["task_id"] == receipt.task_id else "child") for row in selected):
        return False
    summary = usage_api.summarize_observations(selected)[mode]
    cost = uncached = 0
    for attribution in ("root", "child"):
        if not any(row["attribution"] == attribution for row in selected):
            continue
        totals = summary[attribution]["totals"]
        if totals["estimated_cost_usd"] is None or totals["input_tokens"] is None or totals["cached_input_tokens"] is None:
            return False
        cost += totals["estimated_cost_usd"]
        uncached += totals["input_tokens"] - totals["cached_input_tokens"]
    return receipt.metrics.get("cost_usd") == cost and receipt.metrics.get("uncached_input_tokens") == uncached


def build_validators(task_inputs: Path, hook_source: Path) -> dict[str, Callable[..., bool]]:
    """Return diagnostic callbacks that block acceptance, never a production gate.

    Accounting's complete attempt census and semantic acceptance evaluators are
    outstanding. Checking a supplied pass artifact cannot provide either proof.
    """
    fixed = json.loads((task_inputs / "fixed-inputs.json").read_text(encoding="utf-8"))
    load_hook_usage(hook_source)

    def validator(run: Any, receipt: Any, evidence: Mapping[str, Any], expected: str) -> bool:
        task = fixed.get(run.task_id)
        if not isinstance(task, Mapping) or not isinstance(task.get("input_digest"), str):
            return False
        reference = evidence.get("artifact_ref")
        if not isinstance(reference, Mapping):
            return False
        check_path = Path(str(reference.get("path", "")))
        if not check_path.is_file() or _digest(check_path) != reference.get("digest"):
            return False
        try:
            check = json.loads(check_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        if check.get("schema_version") != "benchmark-check-v1" or check.get("run_id") != run.id or check.get("task_id") != receipt.task_id or check.get("invariant_id") != expected or check.get("outcome") != "satisfied":
            return False
        # Check files establish format/provenance only. No deterministic semantic
        # evaluator exists yet for the task outcome, so fail closed.
        return False

    registry = {
        invariant: (lambda run, receipt, evidence, expected=invariant: validator(run, receipt, evidence, expected))
        for task in FIXED_MANIFEST
        for invariant in task.acceptance_invariants
    }
    # Omit the gate's 'accounting' callback until it can verify completeness;
    # validate_accounting above proves only totals over caller-supplied scope.
    return registry


FIXED_INVARIANTS = {task.id: set(task.acceptance_invariants) for task in FIXED_MANIFEST}
