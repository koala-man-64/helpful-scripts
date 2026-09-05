"""Offline preparation, dispatch, receipts, and promotion gates.

The harness never fabricates a model completion.  Dispatch is an explicit caller
action through the locally verified ``codex exec`` interface; receipt acceptance
requires named invariant validators and immutable raw-artifact digests.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import random
import statistics
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .manifest import FIXED_MANIFEST, BenchmarkTask, canonical_digest, manifest_digest, task_identity
from .app_server_capture import RuntimePin, verify_model_catalog, verify_runtime_pin
from tools.context_selection import capture_git_state, revalidate_git_state
from tools.output_projection import run_process as owned_run_process

VARIANTS = ("baseline", "candidate")
EXECUTION_MODES = ("cold", "warm")
REPETITIONS = 3
COMMIT = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def identity_hash(kind: str, value: str) -> str:
    """Match the hooks' content-free usage identity convention exactly."""
    return hashlib.sha256(f"codex-usage-v1:{kind}:{value}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FrozenExecutionConfig:
    model: str
    reasoning_effort: str

    def payload(self) -> dict[str, str]:
        return {"model": self.model, "reasoning_effort": self.reasoning_effort}

    def validate(self) -> list[str]:
        return [] if self.model.strip() and self.reasoning_effort.strip() else ["model and reasoning_effort must be explicit"]


@dataclass(frozen=True)
class PreparationPins:
    base_commits: Mapping[str, str]
    dependency_locks: Mapping[str, str]
    skill_pins: Mapping[str, str]
    external_fixtures: Mapping[str, str]
    variant_configs: Mapping[str, FrozenExecutionConfig | None] | None = None
    claimed_features: tuple[str, ...] = ()

    def validate(self) -> list[str]:
        errors: list[str] = []
        collections = {
            "base_commits": self.base_commits,
            "dependency_locks": self.dependency_locks,
            "skill_pins": self.skill_pins,
            "external_fixtures": self.external_fixtures,
        }
        for label, values in collections.items():
            if not values:
                errors.append(f"{label} must be pinned at preparation")
            for name, digest in values.items():
                if not isinstance(name, str) or not name or not isinstance(digest, str) or not digest:
                    errors.append(f"{label} contains an invalid pin")
                if digest.lower() in {"latest", "head", "main", "none", "unknown"}:
                    errors.append(f"{label}.{name} is not immutable")
                if label == "base_commits" and not COMMIT.fullmatch(digest):
                    errors.append(f"{label}.{name} must be an exact commit SHA")
                if label != "base_commits" and not DIGEST.fullmatch(digest):
                    errors.append(f"{label}.{name} must be a sha256 digest")
        if self.variant_configs is not None:
            if set(self.variant_configs) != set(VARIANTS):
                errors.append("variant_configs must name baseline and candidate exactly")
            elif any(config is None for config in self.variant_configs.values()) and any(config is not None for config in self.variant_configs.values()):
                errors.append("variant_configs must be fully frozen or fully unavailable")
            else:
                for variant, config in self.variant_configs.items():
                    if config is not None:
                        errors.extend(f"{variant}: {error}" for error in config.validate())
        if len(set(self.claimed_features)) != len(self.claimed_features) or any(not isinstance(feature, str) or not feature for feature in self.claimed_features):
            errors.append("claimed_features must be unique nonempty feature IDs")
        return errors

    def payload(self) -> dict[str, Mapping[str, str]]:
        return {
            "base_commits": dict(sorted(self.base_commits.items())),
            "dependency_locks": dict(sorted(self.dependency_locks.items())),
            "skill_pins": dict(sorted(self.skill_pins.items())),
            "external_fixtures": dict(sorted(self.external_fixtures.items())),
            "variant_configs": None if self.variant_configs is None else {
                key: None if value is None else value.payload()
                for key, value in sorted(self.variant_configs.items())
            },
            "claimed_features": list(self.claimed_features),
        }


@dataclass(frozen=True)
class RunRequest:
    id: str
    pair_id: str
    task_id: str
    variant: str
    repetition: int
    execution_mode: str
    prompt: str
    prompt_digest: str


@dataclass(frozen=True)
class PreparedRunSet:
    manifest_digest: str
    pins: PreparationPins
    pins_digest: str
    execution_mode: str
    seed: int
    runs: tuple[RunRequest, ...]
    run_set_digest: str
    execution_config_digest: str | None

    def payload(self) -> dict[str, object]:
        return {
            "manifest_digest": self.manifest_digest,
            "pins": self.pins.payload(),
            "pins_digest": self.pins_digest,
            "execution_mode": self.execution_mode,
            "seed": self.seed,
            "runs": [asdict(run) for run in self.runs],
            "execution_config_digest": self.execution_config_digest,
        }


def _task_by_id(task_id: str) -> BenchmarkTask:
    return next(task for task in FIXED_MANIFEST if task.id == task_id)


def build_run_set(*, execution_mode: str, seed: int) -> tuple[RunRequest, ...]:
    """Return exactly 72 requests, randomized only inside 36 matched pairs."""
    if execution_mode not in EXECUTION_MODES:
        raise ValueError(f"execution_mode must be one of {EXECUTION_MODES}")
    generator = random.Random(seed)
    runs: list[RunRequest] = []
    for task in FIXED_MANIFEST:
        for repetition in range(1, REPETITIONS + 1):
            pair_id = f"{execution_mode}:{task.id}:r{repetition}"
            variants = list(VARIANTS)
            generator.shuffle(variants)
            for variant in variants:
                prompt = task.prompt
                runs.append(
                    RunRequest(
                        id=f"{pair_id}:{variant}",
                        pair_id=pair_id,
                        task_id=task.id,
                        variant=variant,
                        repetition=repetition,
                        execution_mode=execution_mode,
                        prompt=prompt,
                        prompt_digest=_sha256_bytes(prompt.encode("utf-8")),
                    )
                )
    if len(runs) != 72:
        raise AssertionError("fixed manifest must produce exactly 72 model runs")
    return tuple(runs)


def prepare_run_set(*, pins: PreparationPins, execution_mode: str, seed: int) -> PreparedRunSet:
    errors = pins.validate()
    if errors:
        raise ValueError("invalid preparation pins: " + "; ".join(errors))
    runs = build_run_set(execution_mode=execution_mode, seed=seed)
    pins_digest = canonical_digest(pins.payload())
    unsigned = {
        "manifest_digest": manifest_digest(),
        "pins": pins.payload(),
        "pins_digest": pins_digest,
        "execution_mode": execution_mode,
        "seed": seed,
        "runs": [asdict(run) for run in runs],
        "execution_config_digest": None if pins.variant_configs is None or any(value is None for value in pins.variant_configs.values()) else canonical_digest(pins.payload()["variant_configs"]),
    }
    return PreparedRunSet(
        manifest_digest=manifest_digest(),
        pins=pins,
        pins_digest=pins_digest,
        execution_mode=execution_mode,
        seed=seed,
        runs=runs,
        run_set_digest=canonical_digest(unsigned),
        execution_config_digest=unsigned["execution_config_digest"],
    )


@dataclass(frozen=True)
class Receipt:
    run_id: str
    run_identity: str
    manifest_task_id: str
    task_id: str
    parent_task_id: str | None
    manifest_digest: str
    run_set_digest: str
    prompt_digest: str
    adapter: str
    adapter_session_id: str
    raw_artifact_digests: Mapping[str, str]
    raw_artifact_refs: Mapping[str, Mapping[str, str]]
    completion_status: str
    invariant_evidence: Mapping[str, Mapping[str, str]]
    metrics: Mapping[str, int | float | None]
    defects: tuple[Mapping[str, Any], ...] = ()
    child_task_ids: tuple[str, ...] = ()
    failure_artifact_digests: tuple[str, ...] = ()
    feature_consumption: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Receipt":
        prohibited = {"accepted", "acceptance_passed", "passed", "success"}.intersection(value)
        if prohibited:
            raise ValueError("receipt cannot contain an arbitrary acceptance boolean")
        return cls(
            run_id=str(value["run_id"]),
            run_identity=str(value["run_identity"]),
            manifest_task_id=str(value["manifest_task_id"]),
            task_id=str(value["task_id"]),
            parent_task_id=None if value.get("parent_task_id") is None else str(value["parent_task_id"]),
            manifest_digest=str(value["manifest_digest"]),
            run_set_digest=str(value["run_set_digest"]),
            prompt_digest=str(value["prompt_digest"]),
            adapter=str(value["adapter"]),
            adapter_session_id=str(value["adapter_session_id"]),
            raw_artifact_digests=dict(value["raw_artifact_digests"]),
            raw_artifact_refs=dict(value["raw_artifact_refs"]),
            completion_status=str(value["completion_status"]),
            invariant_evidence=dict(value["invariant_evidence"]),
            metrics=dict(value["metrics"]),
            defects=tuple(value.get("defects", ())),
            child_task_ids=tuple(value.get("child_task_ids", ())),
            failure_artifact_digests=tuple(value.get("failure_artifact_digests", ())),
            feature_consumption=dict(value.get("feature_consumption", {})),
        )


def receipt_digest(receipt: Receipt) -> str:
    """Digest the complete receipt so a gate cannot consume a bare pass boolean."""
    return canonical_digest(asdict(receipt))


@dataclass(frozen=True)
class AcceptanceResult:
    run_id: str
    receipt_digest: str
    accepted: bool
    reasons: tuple[str, ...]


def _run_index(prepared: PreparedRunSet) -> dict[str, RunRequest]:
    return {run.id: run for run in prepared.runs}


def run_identity(run: RunRequest) -> str:
    return hashlib.sha256(run.id.encode("utf-8")).hexdigest()


def validate_receipt(receipt: Receipt, prepared: PreparedRunSet) -> list[str]:
    """Validate provenance and structure, never a caller-supplied pass flag."""
    errors: list[str] = []
    run = _run_index(prepared).get(receipt.run_id)
    if run is None:
        return ["receipt run_id is absent from the immutable run set"]
    if receipt.manifest_digest != prepared.manifest_digest:
        errors.append("receipt manifest digest differs from preparation")
    if receipt.run_set_digest != prepared.run_set_digest:
        errors.append("receipt run set digest differs from preparation")
    if receipt.prompt_digest != run.prompt_digest:
        errors.append("receipt prompt digest differs from fixed prompt")
    if receipt.run_identity != run_identity(run):
        errors.append("receipt run identity is not the deterministic namespaced run identity")
    task = _task_by_id(run.task_id)
    if receipt.manifest_task_id != task_identity(task):
        errors.append("receipt task identity differs from the fixed task")
    if receipt.task_id != identity_hash("task", receipt.adapter_session_id):
        errors.append("receipt task_id is not the actual adapter-session usage identity")
    if receipt.parent_task_id is not None and (len(receipt.parent_task_id) != 64 or any(c not in "0123456789abcdef" for c in receipt.parent_task_id)):
        errors.append("receipt parent_task_id is not a hooks-compatible task identity")
    if receipt.adapter != CodexExecAdapter.name:
        errors.append("receipt adapter is not the supported Codex task-start adapter")
    if not receipt.adapter_session_id:
        errors.append("receipt has no supported-adapter session identifier")
    if receipt.completion_status != "completed":
        errors.append("receipt has no completed model execution")
    if not receipt.raw_artifact_digests or any(
        not isinstance(value, str) or not DIGEST.fullmatch(value)
        for value in receipt.raw_artifact_digests.values()
    ):
        errors.append("receipt lacks immutable raw execution artifact digests")
    if set(receipt.raw_artifact_refs) != set(receipt.raw_artifact_digests):
        errors.append("receipt raw artifact references do not match digest keys")
    for name, reference in receipt.raw_artifact_refs.items():
        if not isinstance(reference, Mapping) or set(reference) != {"path", "digest"}:
            errors.append(f"raw artifact {name} reference is invalid")
            continue
        path, digest = Path(reference["path"]), reference["digest"]
        if digest != receipt.raw_artifact_digests.get(name) or not path.is_file():
            errors.append(f"raw artifact {name} is unavailable or its digest binding changed")
            continue
        if _sha256_bytes(path.read_bytes()) != digest:
            errors.append(f"raw artifact {name} no longer matches its immutable digest")
    for invariant in task.acceptance_invariants:
        evidence = receipt.invariant_evidence.get(invariant)
        if not isinstance(evidence, Mapping) or not evidence.get("artifact_digest"):
            errors.append(f"missing immutable evidence for invariant {invariant}")
            continue
        if evidence.get("manifest_task_id") != receipt.manifest_task_id:
            errors.append(f"invariant {invariant} is not joined to the fixed manifest task")
        if evidence.get("task_id") != receipt.task_id:
            errors.append(f"invariant {invariant} is not joined to the executed task identity")
        observation_ids = evidence.get("observation_ids")
        if not isinstance(observation_ids, list) or not observation_ids or any(
            not isinstance(item, str) or len(item) != 64 for item in observation_ids
        ):
            errors.append(f"invariant {invariant} lacks deterministic observation identities")
    for metric in _METRICS:
        value = receipt.metrics.get(metric)
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0):
            errors.append(f"receipt has invalid finite accounting for {metric}")
    if len(set(receipt.child_task_ids)) != len(receipt.child_task_ids) or any(
        len(identity) != 64 or any(character not in "0123456789abcdef" for character in identity)
        for identity in receipt.child_task_ids
    ):
        errors.append("receipt child task identities are invalid or duplicated")
    if any(not DIGEST.fullmatch(digest) for digest in receipt.failure_artifact_digests):
        errors.append("receipt failure evidence digest is invalid")
    for defect in receipt.defects:
        if not isinstance(defect, Mapping) or set(defect) != {"severity", "new", "evidence_digest"}:
            errors.append("receipt defect evidence has an invalid shape")
            continue
        if defect["severity"] not in {"low", "medium", "high", "critical"} or not isinstance(defect["new"], bool) or not DIGEST.fullmatch(str(defect["evidence_digest"])):
            errors.append("receipt defect evidence is not typed and immutable")
    if "children" in task.scenarios and not receipt.child_task_ids:
        errors.append("child scenario lacks joined child task evidence")
    if "failure" in task.scenarios and not receipt.failure_artifact_digests:
        errors.append("failure scenario lacks immutable failure evidence")
    if receipt.feature_consumption is None:
        errors.append("feature consumption evidence is unavailable")
    return errors


InvariantValidator = Callable[[RunRequest, Receipt, Mapping[str, str]], bool]


def evaluate_acceptance(
    receipt: Receipt,
    prepared: PreparedRunSet,
    validators: Mapping[str, InvariantValidator],
) -> AcceptanceResult:
    """Accept only after every fixed invariant has a named trusted validator."""
    errors = validate_receipt(receipt, prepared)
    run = _run_index(prepared).get(receipt.run_id)
    if run is None:
        return AcceptanceResult(receipt.run_id, receipt_digest(receipt), False, tuple(errors))
    for invariant in _task_by_id(run.task_id).acceptance_invariants:
        validator = validators.get(invariant)
        if validator is None:
            errors.append(f"no trusted validator registered for invariant {invariant}")
            continue
        evidence = receipt.invariant_evidence.get(invariant)
        if evidence is None:
            continue
        if evidence.get("outcome") != "satisfied":
            errors.append(f"invariant {invariant} is not satisfied")
            continue
        try:
            if not validator(run, receipt, evidence):
                errors.append(f"validator rejected invariant {invariant}")
        except Exception as error:  # validators are external trust boundaries
            errors.append(f"validator errored for invariant {invariant}: {error}")
    return AcceptanceResult(
        receipt.run_id,
        receipt_digest(receipt),
        not errors,
        tuple(errors),
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile without values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


@dataclass(frozen=True)
class GateDecision:
    eligible: bool
    reasons: tuple[str, ...]
    ratios: Mapping[str, float]
    results_digest: str


def gate_results_payload(prepared: PreparedRunSet, receipts: Mapping[str, Receipt], validators: Mapping[str, InvariantValidator]) -> dict[str, object]:
    """Full recomputable gate input, never a detached eligibility assertion."""
    decision = evaluate_gate(prepared, receipts, validators)
    return {
        "run_set_digest": prepared.run_set_digest,
        "receipt_digests": {run_id: receipt_digest(receipt) for run_id, receipt in sorted(receipts.items())},
        "acceptance": {
            run_id: {"receipt_digest": result.receipt_digest, "accepted": result.accepted, "reasons": result.reasons}
            for run_id, result in ((run_id, evaluate_acceptance(receipt, prepared, validators)) for run_id, receipt in sorted(receipts.items()))
        },
        "gates": {"eligible": decision.eligible, "reasons": decision.reasons, "ratios": decision.ratios},
        "results_digest": decision.results_digest,
    }


_METRICS = ("cost_usd", "uncached_input_tokens", "rework_count", "avoidable_interventions", "active_seconds")


def evaluate_gate(
    prepared: PreparedRunSet,
    receipts: Mapping[str, Receipt],
    validators: Mapping[str, InvariantValidator],
) -> GateDecision:
    """Apply the declared promotion gate; missing accounting fails closed."""
    reasons: list[str] = []
    ratios: dict[str, float] = {}
    if prepared.execution_config_digest is None:
        reasons.append("actual baseline/candidate execution configuration is not frozen")
    index = _run_index(prepared)
    if set(receipts) != set(index):
        reasons.append("receipt set is incomplete or contains unknown runs")
    valid_receipts = {run_id: receipt for run_id, receipt in receipts.items() if run_id in index}
    acceptance_results = {
        run_id: evaluate_acceptance(receipt, prepared, validators)
        for run_id, receipt in valid_receipts.items()
    }
    for run_id, receipt in valid_receipts.items():
        reasons.extend(f"{run_id}: {error}" for error in validate_receipt(receipt, prepared))
        accounting_validator = validators.get("accounting")
        if accounting_validator is None:
            reasons.append(f"{run_id}: no trusted validator for complete root/child/attempt accounting")
        else:
            try:
                if not accounting_validator(index[run_id], receipt, {"required_scope": "root_children_failures_reviews_recovery_clarifications"}):
                    reasons.append(f"{run_id}: complete accounting evidence was not verified")
            except Exception:
                reasons.append(f"{run_id}: accounting evidence validator failed")
        result = acceptance_results[run_id]
        if result.run_id != run_id or result.receipt_digest != receipt_digest(receipt):
            reasons.append(f"{run_id}: acceptance result is not bound to this receipt")
    for run_id, receipt in valid_receipts.items():
        if index[run_id].variant != "candidate":
            continue
        if not acceptance_results[run_id].accepted:
            reasons.append(f"{run_id}: candidate acceptance or safety invariant failed")
        for feature in prepared.pins.claimed_features:
            evidence = (receipt.feature_consumption or {}).get(feature)
            reference = evidence.get("artifact_ref") if isinstance(evidence, Mapping) else None
            if not isinstance(evidence, Mapping) or evidence.get("outcome") != "consumed" or evidence.get("feature_id") != feature or evidence.get("task_id") != receipt.task_id or evidence.get("execution_config_digest") != prepared.execution_config_digest or not isinstance(reference, Mapping) or set(reference) != {"path", "digest"} or not DIGEST.fullmatch(str(reference.get("digest"))) or not Path(reference["path"]).is_file() or _sha256_bytes(Path(reference["path"]).read_bytes()) != reference["digest"]:
                reasons.append(f"{run_id}: claimed feature {feature} has no actual consumption evidence")
        if any(defect["severity"] in {"high", "critical"} and defect["new"] is True for defect in receipt.defects if isinstance(defect, Mapping) and set(defect) == {"severity", "new", "evidence_digest"}):
            reasons.append(f"{run_id}: new high or critical defect")
        task = _task_by_id(index[run_id].task_id)
        if any(invariant not in receipt.invariant_evidence for invariant in task.acceptance_invariants):
            reasons.append(f"{run_id}: safety invariant evidence is incomplete")
        for continuity in ("lost_continuations", "duplicate_continuations"):
            if receipt.metrics.get(continuity) != 0:
                reasons.append(f"{run_id}: {continuity} must be zero")
        if "compaction" in task.scenarios and receipt.metrics.get("load_bearing_retention") != 1:
            reasons.append(f"{run_id}: load-bearing compaction retention is incomplete")
    baseline_accepted = sum(
        acceptance_results.get(run.id, AcceptanceResult(run.id, "", False, ())).accepted
        for run in index.values()
        if run.variant == "baseline"
    )
    candidate_accepted = sum(
        acceptance_results.get(run.id, AcceptanceResult(run.id, "", False, ())).accepted
        for run in index.values()
        if run.variant == "candidate"
    )
    if candidate_accepted < baseline_accepted:
        reasons.append("candidate accepted count is below baseline")
    pairs: dict[str, dict[str, Receipt]] = {}
    for run_id, receipt in valid_receipts.items():
        pairs.setdefault(index[run_id].pair_id, {})[index[run_id].variant] = receipt
    pair_metrics: dict[str, list[tuple[float, float]]] = {metric: [] for metric in _METRICS}
    for pair_id, pair in pairs.items():
        if set(pair) != set(VARIANTS):
            reasons.append(f"{pair_id}: matched baseline/candidate receipts are required")
            continue
        for metric in _METRICS:
            baseline, candidate = pair["baseline"].metrics.get(metric), pair["candidate"].metrics.get(metric)
            if baseline is None or candidate is None:
                reasons.append(f"{pair_id}: null {metric} blocks monetary/default-promotion claim")
                continue
            if not isinstance(baseline, (int, float)) or not isinstance(candidate, (int, float)) or baseline < 0 or candidate < 0:
                reasons.append(f"{pair_id}: invalid {metric} accounting")
                continue
            pair_metrics[metric].append((float(baseline), float(candidate)))
    for metric, values in pair_metrics.items():
        if len(values) != 36:
            reasons.append(f"{metric}: complete matched accounting is required")
            continue
        pair_ratios = [candidate / baseline if baseline else (0.0 if candidate == 0 else math.inf) for baseline, candidate in values]
        ratios[f"{metric}_paired_median"] = statistics.median(pair_ratios)
        ratios[f"{metric}_paired_p90"] = _percentile(pair_ratios, 0.9)
    if baseline_accepted == 0 or candidate_accepted == 0:
        reasons.append("accepted-count denominator is zero; monetary/default promotion is blocked")
    else:
        baseline_costs = [receipt.metrics.get("cost_usd") for run_id, receipt in valid_receipts.items() if index[run_id].variant == "baseline"]
        candidate_costs = [receipt.metrics.get("cost_usd") for run_id, receipt in valid_receipts.items() if index[run_id].variant == "candidate"]
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in baseline_costs + candidate_costs):
            baseline_cost, candidate_cost = sum(baseline_costs), sum(candidate_costs)
            ratios["cost_usd_aggregate_per_accepted"] = (candidate_cost / candidate_accepted) / (baseline_cost / baseline_accepted) if baseline_cost else (0.0 if candidate_cost == 0 else math.inf)
    def require(key: str, maximum: float) -> None:
        if key in ratios and ratios[key] > maximum:
            reasons.append(f"{key} exceeds {maximum:.0%}")
    require("cost_usd_aggregate_per_accepted", 0.90)
    require("cost_usd_paired_median", 0.90)
    # Cohort comparisons are the declared p90/median gates; paired values are
    # retained as diagnostics and never substitute for them.
    for metric, median_limit, p90_limit in (("cost_usd", None, 1.10), ("uncached_input_tokens", 0.90, None), ("rework_count", 1.00, 1.00), ("avoidable_interventions", 1.00, 1.00), ("active_seconds", 1.00, 1.10)):
        values = pair_metrics[metric]
        if len(values) == 36:
            baseline_values = [base for base, _ in values]
            candidate_values = [candidate for _, candidate in values]
            cohort_median = statistics.median(candidate_values) / statistics.median(baseline_values) if statistics.median(baseline_values) else (0.0 if statistics.median(candidate_values) == 0 else math.inf)
            cohort_p90 = _percentile(candidate_values, 0.9) / _percentile(baseline_values, 0.9) if _percentile(baseline_values, 0.9) else (0.0 if _percentile(candidate_values, 0.9) == 0 else math.inf)
            ratios[f"{metric}_cohort_median"] = cohort_median
            ratios[f"{metric}_cohort_p90"] = cohort_p90
            if median_limit is not None:
                require(f"{metric}_cohort_median", median_limit)
            if p90_limit is not None:
                require(f"{metric}_cohort_p90", p90_limit)
    results_payload = {
        "run_set_digest": prepared.run_set_digest,
        "receipt_digests": {run_id: receipt_digest(receipt) for run_id, receipt in sorted(valid_receipts.items())},
        "acceptance": {
            run_id: {
                "receipt_digest": result.receipt_digest,
                "accepted": result.accepted,
                "reasons": result.reasons,
            }
            for run_id, result in sorted(acceptance_results.items())
        },
    }
    return GateDecision(not reasons, tuple(dict.fromkeys(reasons)), ratios, canonical_digest(results_payload))


def evidence_receipt_payload(
    prepared: PreparedRunSet,
    receipts: Mapping[str, Receipt],
    validators: Mapping[str, InvariantValidator],
) -> dict[str, object]:
    """Project complete evaluated evidence into the published receipt schema.

    This intentionally omits an acceptance boolean.  Consumers recompute
    acceptance from the fixed task contract and individual evidence outcomes.
    """
    if set(receipts) != set(_run_index(prepared)):
        raise ValueError("cannot export an incomplete immutable run set")
    gate = evaluate_gate(prepared, receipts, validators)
    runs: list[dict[str, object]] = []
    for request in prepared.runs:
        receipt = receipts[request.id]
        task = _task_by_id(request.task_id)
        evidence = []
        for invariant in task.acceptance_invariants:
            item = dict(receipt.invariant_evidence[invariant])
            item["invariant_id"] = invariant
            evidence.append(item)
        runs.append(
            {
                "run_id": receipt.run_id,
                "run_identity": receipt.run_identity,
                "manifest_task_id": receipt.manifest_task_id,
                "task_id": receipt.task_id,
                "parent_task_id": receipt.parent_task_id,
                "prompt_digest": receipt.prompt_digest,
                "variant": request.variant,
                "repetition": request.repetition,
                "adapter": receipt.adapter,
                "adapter_session_id": receipt.adapter_session_id,
                "raw_artifact_digests": dict(receipt.raw_artifact_digests),
                "raw_artifact_refs": dict(receipt.raw_artifact_refs),
                "completion_status": receipt.completion_status,
                "acceptance_evidence": evidence,
                "metrics": dict(receipt.metrics),
                "child_task_ids": list(receipt.child_task_ids),
                "failure_artifact_digests": list(receipt.failure_artifact_digests),
                "defects": list(receipt.defects),
                "feature_consumption": dict(receipt.feature_consumption),
            }
        )
    return {
        "schema_version": "benchmark-evidence-receipt-v1",
        "benchmark_id": "benchmark-v1:" + hashlib.sha256(prepared.run_set_digest.encode("utf-8")).hexdigest(),
        "manifest_digest": prepared.manifest_digest,
        "run_set_digest": prepared.run_set_digest,
        "results_digest": gate.results_digest,
        "execution_mode": prepared.execution_mode,
        "runs": runs,
    }


def validate_evidence_payload(
    value: Mapping[str, Any], prepared: PreparedRunSet, receipts: Mapping[str, Receipt], validators: Mapping[str, InvariantValidator]
) -> list[str]:
    """Bind exported rows to every exact prepared request and recomputed gate."""
    errors: list[str] = []
    rows = value.get("runs")
    if not isinstance(rows, list) or len(rows) != 72:
        return ["export does not contain the exact 72-run set"]
    by_id = {row.get("run_id"): row for row in rows if isinstance(row, Mapping)}
    if len(by_id) != 72 or set(by_id) != set(_run_index(prepared)):
        errors.append("exported run identities are missing, unknown, or duplicated")
    for run in prepared.runs:
        row = by_id.get(run.id, {})
        if row.get("prompt_digest") != run.prompt_digest or row.get("variant") != run.variant or row.get("repetition") != run.repetition:
            errors.append(f"{run.id}: export does not bind the prepared prompt/request")
    gate = evaluate_gate(prepared, receipts, validators)
    if value.get("results_digest") != gate.results_digest:
        errors.append("export results digest is not recomputed from receipts and validators")
    expected = evidence_receipt_payload(prepared, receipts, validators)
    if canonical_digest(value) != canonical_digest(expected):
        errors.append("exported receipt content differs from the recomputed source receipts")
    return errors


ProcessRunner = Callable[..., Mapping[str, Any]]


def validate_launch(
    prepared: PreparedRunSet,
    *,
    workspace: Path,
    repository_id: str,
    observed_skill_pins: Mapping[str, str],
) -> None:
    """Re-read the exact mutable launch state; never trust preparation alone."""
    if repository_id not in prepared.pins.base_commits:
        raise ValueError("repository_id has no prepared base commit")
    if dict(observed_skill_pins) != dict(prepared.pins.skill_pins):
        raise ValueError("observed skill pins do not match immutable preparation")
    before = capture_git_state(workspace)
    if before["head"] != prepared.pins.base_commits[repository_id]:
        raise ValueError("workspace HEAD differs from the prepared base commit")
    if before["branch"] is None or before["status"] or before["git_locks"] or before["pending_git_operations"]:
        raise ValueError("workspace is not a clean, settled prepared worktree")
    prefix = repository_id.rstrip("/") + "/"
    expected_locks = {
        key[len(prefix):]: digest
        for key, digest in prepared.pins.dependency_locks.items()
        if key.startswith(prefix)
    }
    if not expected_locks:
        raise ValueError("prepared dependency pins do not cover the launch repository")
    for relative, digest in expected_locks.items():
        path = workspace / relative
        if not path.is_file() or _sha256_bytes(path.read_bytes()) != digest:
            raise ValueError(f"dependency lock does not match preparation: {relative}")
    if errors := revalidate_git_state(before):
        raise ValueError("workspace changed immediately before launch: " + "; ".join(errors))


class CodexExecAdapter:
    """The only dispatch adapter accepted by this benchmark version."""

    name = "codex-cli-exec-v1"

    def __init__(self, executable: str = "codex", process_runner: ProcessRunner | None = None,
                 *, runtime_pin: RuntimePin | None = None, model_catalog_ref: Mapping[str, str] | None = None) -> None:
        self.executable = executable
        self.process_runner = process_runner or owned_run_process
        self.runtime_pin = runtime_pin
        self.model_catalog_ref = model_catalog_ref

    def runtime_preflight(self, run: RunRequest, prepared: PreparedRunSet) -> dict:
        if self.runtime_pin is None or self.model_catalog_ref is None:
            raise ValueError("dispatch requires an explicit pinned executable and model catalog")
        runtime_digest = "sha256:" + self.runtime_pin.sha256.removeprefix("sha256:").lower()
        if (prepared.pins.skill_pins.get("codex-runtime") != runtime_digest
                or prepared.pins.skill_pins.get("codex-model-catalog") != self.model_catalog_ref.get("digest")):
            raise ValueError("runtime and catalog evidence must match immutable preparation pins")
        configs = prepared.pins.variant_configs
        if configs is None or configs.get(run.variant) is None:
            raise ValueError("actual model and reasoning configuration is not frozen")
        config = configs[run.variant]
        binary = verify_runtime_pin(self.runtime_pin)
        catalog_ref = verify_model_catalog(self.model_catalog_ref, self.runtime_pin, config.model, config.reasoning_effort)
        self.executable = str(binary)
        return {"binary": str(binary), "version": self.runtime_pin.version,
                "sha256": self.runtime_pin.sha256, "model_catalog_ref": catalog_ref}

    def supported(self, *, cwd: Path, raw_dir: Path) -> bool:
        try:
            result = self.process_runner([self.executable, "exec", "--help"], cwd=cwd, raw_dir=raw_dir)
        except FileNotFoundError as error:
            raise RuntimeError("Codex CLI is unavailable; benchmark dispatch is unsupported") from error
        return result.get("exit_status") == 0

    def command(self, run: RunRequest, prepared: PreparedRunSet, *, workspace: Path, last_message: Path) -> list[str]:
        configs = prepared.pins.variant_configs
        if configs is None or configs.get(run.variant) is None or prepared.execution_config_digest is None:
            raise ValueError("actual model and reasoning configuration is not frozen")
        config = configs[run.variant]
        return [self.executable, "exec", "--json", "--model", config.model, "--config", f"model_reasoning_effort={json.dumps(config.reasoning_effort)}", "--cd", str(workspace), "--output-last-message", str(last_message), "-"]

    def dispatch(
        self,
        run: RunRequest,
        prepared: PreparedRunSet,
        *,
        workspace: Path,
        raw_dir: Path,
        repository_id: str,
        observed_skill_pins: Mapping[str, str],
    ) -> Mapping[str, Any]:
        if _run_index(prepared).get(run.id) != run:
            raise ValueError("run is not in the immutable prepared set")
        runtime = self.runtime_preflight(run, prepared)
        if not self.supported(cwd=workspace, raw_dir=raw_dir / "adapter-check"):
            raise RuntimeError("installed Codex CLI does not support codex exec")
        last_message = raw_dir / f"last-message-{uuid.uuid4().hex}.txt"
        command = self.command(run, prepared, workspace=workspace, last_message=last_message)
        validate_launch(
            prepared,
            workspace=workspace,
            repository_id=repository_id,
            observed_skill_pins=observed_skill_pins,
        )
        projection = self.process_runner(
            command,
            cwd=workspace,
            raw_dir=raw_dir,
            failure_target_tokens=4000,
            routine_target_tokens=2000,
            input_bytes=run.prompt.encode("utf-8"),
        )
        raw_file = projection.get("raw_file")
        if not isinstance(raw_file, Mapping) or not isinstance(raw_file.get("path"), str) or not isinstance(raw_file.get("hash"), str) or not DIGEST.fullmatch(raw_file["hash"]):
            raise RuntimeError("owned output projection did not preserve an immutable raw reference")
        return {
            "schema_version": "codex-exec-dispatch-v1",
            "adapter": self.name,
            "run_request": asdict(run),
            "run_set_digest": prepared.run_set_digest,
            "runtime": runtime,
            "projection": projection,
            "raw_artifact_digests": {"events": raw_file["hash"]},
            "raw_artifact_refs": {"events": {"path": raw_file["path"], "digest": raw_file["hash"]}},
        }
