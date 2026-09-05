"""Explicit artifact emission and fail-closed loading for benchmark evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .harness import FrozenExecutionConfig, InvariantValidator, PreparationPins, PreparedRunSet, Receipt, evaluate_gate, gate_results_payload, prepare_run_set, validate_evidence_payload, validate_receipt
from .manifest import canonical_digest, manifest_payload

THRESHOLDS = {
    "accepted_count": "candidate>=baseline",
    "cost_per_accepted": 0.90,
    "cost_paired_median": 0.90,
    "cost_cohort_p90": 1.10,
    "uncached_input_cohort_median": 0.90,
    "rework_cohort_median_p90": 1.00,
    "interventions_cohort_median_p90": 1.00,
    "active_time_cohort_median": 1.00,
    "active_time_cohort_p90": 1.10,
}


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write(directory: Path, name: str, value: object) -> dict[str, str]:
    data = _bytes(value)
    path = directory / name
    with path.open("xb") as target:
        target.write(data)
    return {"path": name, "digest": "sha256:" + hashlib.sha256(data).hexdigest()}


@dataclass(frozen=True)
class LoadedArtifacts:
    definition: Mapping[str, Any]
    run_set: Mapping[str, Any]
    results: Mapping[str, Any]
    observations: Mapping[str, Any]
    metadata: Mapping[str, Any]


def emit_artifacts(
    output_dir: Path,
    *,
    prepared: PreparedRunSet,
    receipts: Mapping[str, Receipt],
    validators: Mapping[str, InvariantValidator],
    observations: Mapping[str, Any],
) -> Path:
    """Write a caller-chosen immutable artifact set; never create a default ledger."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("artifact output directory must be empty")
    if set(receipts) != {run.id for run in prepared.runs}:
        raise ValueError("artifact emission requires the exact complete 72-run result set")
    output_dir.mkdir(parents=True, exist_ok=True)
    gate = evaluate_gate(prepared, receipts, validators)
    definition = {"schema_version": "benchmark-definition-v1", "manifest": manifest_payload(), "thresholds": THRESHOLDS}
    run_set = prepared.payload() | {"run_set_digest": prepared.run_set_digest}
    results = {
        "schema_version": "benchmark-results-v1",
        "manifest_digest": prepared.manifest_digest,
        "run_set_digest": prepared.run_set_digest,
        "results_digest": gate.results_digest,
        "receipts": [asdict(receipts[run.id]) for run in prepared.runs],
        "gate_evaluation": gate_results_payload(prepared, receipts, validators),
    }
    results["results_payload"] = {key: results["gate_evaluation"][key] for key in ("run_set_digest", "receipt_digests", "acceptance")}
    # Construct the public projection here to prove the emission uses the same
    # recomputed evaluation rather than a caller-supplied eligibility flag.
    from .harness import evidence_receipt_payload
    public = evidence_receipt_payload(prepared, receipts, validators)
    if validate_evidence_payload(public, prepared, receipts, validators):
        raise ValueError("cannot emit an unverifiable benchmark evidence projection")
    refs = {
        "benchmark_definition": _write(output_dir, "benchmark-definition.json", definition),
        "run_set": _write(output_dir, "run-set.json", run_set),
        "results": _write(output_dir, "results.json", results),
        "public_receipt": _write(output_dir, "evidence-receipt.json", public),
        "observations": _write(output_dir, "observations.json", observations),
    }
    raw_refs = {run_id: dict(receipt.raw_artifact_refs) for run_id, receipt in receipts.items()}
    acceptance_refs = {run_id: {name: evidence.get("artifact_ref") for name, evidence in receipt.invariant_evidence.items() if evidence.get("artifact_ref")} for run_id, receipt in receipts.items()}
    metadata = {"schema_version": "benchmark-artifact-set-v1", "evidence_artifacts": refs, "raw_artifact_refs": raw_refs, "acceptance_artifact_refs": acceptance_refs}
    _write(output_dir, "artifact-set.json", metadata)
    return output_dir / "artifact-set.json"


def load_verified_artifacts(path: Path, *, validators: Mapping[str, InvariantValidator]) -> LoadedArtifacts:
    """Load only byte-verified, fixed-manifest, exact-72 artifact sets."""
    root = path.parent.resolve()
    metadata = json.loads(path.read_bytes())
    if metadata.get("schema_version") != "benchmark-artifact-set-v1" or set(metadata.get("evidence_artifacts", ())) != {"benchmark_definition", "run_set", "results", "observations", "public_receipt"}:
        raise ValueError("artifact metadata is incomplete")
    loaded = {}
    for name, reference in metadata["evidence_artifacts"].items():
        target = (root / reference["path"]).resolve()
        if target.parent != root or not target.is_file():
            raise ValueError(f"artifact reference is not a loadable local file: {name}")
        data = target.read_bytes()
        if "sha256:" + hashlib.sha256(data).hexdigest() != reference["digest"]:
            raise ValueError(f"artifact digest changed: {name}")
        loaded[name] = json.loads(data)
    definition, run_set, results = loaded["benchmark_definition"], loaded["run_set"], loaded["results"]
    if canonical_digest(definition) != canonical_digest({"schema_version": "benchmark-definition-v1", "manifest": manifest_payload(), "thresholds": THRESHOLDS}):
        raise ValueError("benchmark definition or predeclared thresholds differ from the fixed contract")
    if run_set.get("manifest_digest") != canonical_digest(manifest_payload()) or len(run_set.get("runs", ())) != 72 or results.get("run_set_digest") != run_set.get("run_set_digest") or len(results.get("receipts", ())) != 72:
        raise ValueError("run set or results are not the exact pinned 72-run contract")
    pins_value = run_set.get("pins")
    if not isinstance(pins_value, dict):
        raise ValueError("run set has no reconstructible preparation pins")
    configs = pins_value.get("variant_configs")
    restored_configs = None if configs is None else {
        variant: None if value is None else FrozenExecutionConfig(value["model"], value["reasoning_effort"])
        for variant, value in configs.items()
    }
    pins = PreparationPins(
        base_commits=pins_value["base_commits"], dependency_locks=pins_value["dependency_locks"],
        skill_pins=pins_value["skill_pins"], external_fixtures=pins_value["external_fixtures"],
        variant_configs=restored_configs, claimed_features=tuple(pins_value.get("claimed_features", ())),
    )
    prepared = prepare_run_set(pins=pins, execution_mode=run_set["execution_mode"], seed=run_set["seed"])
    expected = prepared.payload() | {"run_set_digest": prepared.run_set_digest}
    if run_set != expected:
        raise ValueError("serialized run set does not match reconstructed pinned schedule")
    receipts = {value["run_id"]: Receipt.from_mapping(value) for value in results["receipts"]}
    if set(receipts) != {run.id for run in prepared.runs} or any(validate_receipt(receipt, prepared) for receipt in receipts.values()):
        raise ValueError("one or more emitted receipts fail raw/evidence validation")
    recomputed = gate_results_payload(prepared, receipts, validators)
    if canonical_digest(results.get("gate_evaluation")) != canonical_digest(recomputed):
        raise ValueError("emitted gate evaluation is not recomputable from trusted validators")
    if canonical_digest(results.get("results_payload")) != results.get("results_digest"):
        raise ValueError("results payload does not match its digest")
    if validate_evidence_payload(loaded["public_receipt"], prepared, receipts, validators):
        raise ValueError("public receipt differs from the verified source receipts")
    return LoadedArtifacts(definition, run_set, results, loaded["observations"], metadata)
