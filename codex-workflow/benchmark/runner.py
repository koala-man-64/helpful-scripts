"""Runnable preparation and receipt collection; no implicit model dispatch."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Mapping

from .fixtures import prepare_fixture_scratch
from .harness import (
    CodexExecAdapter, FrozenExecutionConfig, PreparationPins, PreparedRunSet,
    Receipt, identity_hash, prepare_run_set, run_identity, validate_receipt,
)
from .manifest import FIXED_MANIFEST, manifest_payload, task_identity
from .validators import capability_report
from .semantic_evidence import capture_workspace
from .semantic_validation import fixture_record, semantic_observations, semantic_preparation_pins, semantic_proofs, validator_digest
from .pricing import derive_request_prices


def read_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def write_new(path: Path, value: Any) -> None:
    """Refuse overwrites so attempts and evidence cannot silently replace each other."""
    with path.open("xb") as stream:
        stream.write((json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))


def pins_from_mapping(value: Mapping[str, Any]) -> PreparationPins:
    configs = value.get("variant_configs")
    return PreparationPins(
        base_commits=value["base_commits"], dependency_locks=value["dependency_locks"],
        skill_pins=value["skill_pins"], external_fixtures=value["external_fixtures"],
        variant_configs=None if configs is None else {
            name: None if config is None else FrozenExecutionConfig(**config)
            for name, config in configs.items()
        }, claimed_features=tuple(value.get("claimed_features", ())),
    )


def restore_prepared(value: Mapping[str, Any]) -> PreparedRunSet:
    prepared = prepare_run_set(
        pins=pins_from_mapping(value["pins"]), execution_mode=value["execution_mode"], seed=value["seed"],
    )
    if value != prepared.payload() | {"run_set_digest": prepared.run_set_digest}:
        raise ValueError("prepared run set differs from the exact reconstructed 72-run schedule")
    return prepared


def study_capabilities() -> dict[str, Any]:
    return {
        "schema_version": "benchmark-capabilities-v1",
        "dispatch_ready": False,
        "tasks": {task.id: capability_report(task.id) for task in FIXED_MANIFEST},
        "semantic_preparation_pins": semantic_preparation_pins(Path(__file__).with_name("task_inputs")),
        "blocked_requirements": [
            "independent complete root/child/retry/review/recovery/clarification census",
            "supported host compaction, wait, peer-message and child-review event producers",
            "central acceptance of separate request-price derivations and verified product/configuration context; original usage prices remain null",
            "authority-bound expected semantic validator pins and exact observation join verification",
        ],
        "implemented": ["fixed preparation", "explicit CodexExecAdapter API", "raw execution receipt collection", "artifact verification", "eight deterministic semantic evaluators"],
    }


def reference(path: Path) -> dict[str, str]:
    path = path.resolve(strict=True)
    return {"path": str(path), "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}


def execution_identity(events: Path) -> tuple[str, str]:
    """Read documented CLI lifecycle events, never infer usage from this stream."""
    sessions: list[str] = []
    completed = failed = False
    for line in events.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue  # Combined raw stderr is preserved but is not an event.
        if not isinstance(event, dict):
            continue
        if event.get("type") == "thread.started":
            sessions.append(event.get("thread_id"))
        completed |= event.get("type") == "turn.completed"
        failed |= event.get("type") in {"turn.failed", "error"}
    if len(sessions) != 1 or not isinstance(sessions[0], str) or not sessions[0]:
        raise ValueError("raw adapter events must identify exactly one actual session")
    return sessions[0], "failed" if failed else "completed" if completed else "incomplete"


def dispatch_observed(adapter, run, prepared, *, workspace: Path, raw_dir: Path,
                      repository_id: str, observed_skill_pins: Mapping[str, str]) -> dict:
    """Explicit one-run API; retain independent before/after workspace evidence."""
    workspace, raw_dir = workspace.resolve(strict=True), raw_dir.resolve()
    if raw_dir == workspace or raw_dir.is_relative_to(workspace):
        raise ValueError("retained raw evidence must be outside the model workspace")
    if prepared.pins.skill_pins.get("semantic-validators") != validator_digest():
        raise ValueError("semantic validator implementation is not pinned")
    task_inputs = Path(__file__).with_name("task_inputs")
    if any(prepared.pins.skill_pins.get(key) != value for key, value in semantic_preparation_pins(task_inputs)["skill_pins"].items()):
        raise ValueError("hook-compatible semantic validator source reference is not pinned")
    if prepared.pins.external_fixtures.get(run.task_id) != fixture_record(task_inputs, run.task_id)["input_digest"]:
        raise ValueError("concrete fixture is not pinned for this run")
    raw_dir.mkdir(parents=True, exist_ok=True)
    before_path, after_path = raw_dir / "semantic-before.json", raw_dir / "semantic-after.json"
    write_new(before_path, capture_workspace(workspace))
    dispatch = dict(adapter.dispatch(run, prepared, workspace=workspace, raw_dir=raw_dir,
                                    repository_id=repository_id, observed_skill_pins=observed_skill_pins))
    write_new(after_path, capture_workspace(workspace))
    # Each dispatch uses a unique last-message filename. Identify it through the
    # actual invocation rather than guessing a prepared task/session identity.
    messages = list(raw_dir.glob("last-message-*.txt"))
    if len(messages) != 1:
        raise ValueError("observed attempt requires one retained last-message artifact")
    refs = dict(dispatch["raw_artifact_refs"])
    refs.update({"semantic_before": reference(before_path), "semantic_after": reference(after_path),
                 "last_message": reference(messages[0])})
    for key, relative in {"comparison": "comparison.json", "failure": "failed-validation.json", "build_log": "logs/build.log"}.items():
        if (workspace / relative).is_file():
            refs[key] = reference(workspace / relative)
    dispatch.update(raw_artifact_refs=refs, raw_artifact_digests={name: ref["digest"] for name, ref in refs.items()})
    write_new(raw_dir / "dispatch.json", dispatch)
    return dispatch


def collect_receipt(
    prepared: PreparedRunSet, run_id: str, evidence: Mapping[str, Any],
) -> Receipt:
    """Assemble actual supplied artifacts; missing proof remains missing, never passes."""
    run = next((run for run in prepared.runs if run.id == run_id), None)
    if run is None:
        raise ValueError("run_id is absent from preparation")
    raw = {name: reference(Path(path)) for name, path in evidence["raw_artifacts"].items()}
    if not {"dispatch", "events", "measurements", "usage_observations"}.issubset(raw):
        raise ValueError("dispatch, events, measurements and usage_observations artifacts are required")
    dispatch = read_json(Path(raw["dispatch"]["path"]))
    if (dispatch.get("schema_version") != "codex-exec-dispatch-v1"
            or dispatch.get("adapter") != CodexExecAdapter.name
            or dispatch.get("run_request") != asdict(run)
            or dispatch.get("run_set_digest") != prepared.run_set_digest
            or dispatch.get("raw_artifact_refs", {}).get("events") != raw["events"]):
        raise ValueError("dispatch artifact does not bind the exact request, preparation and event bytes")
    for name, retained in dispatch.get("raw_artifact_refs", {}).items():
        actual = reference(Path(retained["path"]))
        if actual != retained or (name in raw and raw[name] != retained):
            raise ValueError("retained dispatch artifact bytes or supplied reference changed")
        raw[name] = retained
    session_id, status = execution_identity(Path(raw["events"]["path"]))
    task_id = identity_hash("task", session_id)
    measures = read_json(Path(raw["measurements"]["path"]))
    if measures.get("schema_version") != "benchmark-measurements-v1" or measures.get("run_id") != run.id:
        raise ValueError("measurements do not bind this exact prepared run")
    task_ids = measures.get("task_ids")
    if not isinstance(task_ids, list) or task_id not in task_ids or len(set(task_ids)) != len(task_ids):
        raise ValueError("measurements lack a unique actual root/child task scope")
    metric_names = (
        "rework_count", "avoidable_interventions", "active_seconds", "lost_continuations",
        "duplicate_continuations", "load_bearing_retention",
    )
    metrics = {name: measures.get(name) for name in metric_names}
    # Cost/token totals are supplied by hook-owned accounting, and independently
    # rechecked there. Receipt assembly never invents a rate card or usage parser.
    metrics.update({name: evidence.get("accounting_totals", {}).get(name)
                    for name in ("cost_usd", "uncached_input_tokens")})
    task = next(task for task in FIXED_MANIFEST if task.id == run.task_id)
    failures = tuple(raw[name]["digest"] for name in evidence.get("failure_artifacts", ()))
    return Receipt(
        run_id=run.id, run_identity=run_identity(run), manifest_task_id=task_identity(task),
        task_id=task_id, parent_task_id=None, manifest_digest=prepared.manifest_digest,
        run_set_digest=prepared.run_set_digest, prompt_digest=run.prompt_digest,
        adapter=CodexExecAdapter.name, adapter_session_id=session_id,
        raw_artifact_digests={name: item["digest"] for name, item in raw.items()},
        raw_artifact_refs=raw, completion_status=status,
        invariant_evidence=evidence.get("invariant_evidence", {}), metrics=metrics,
        defects=tuple(evidence.get("defects", ())),
        child_task_ids=tuple(sorted(set(task_ids) - {task_id})), failure_artifact_digests=failures,
        feature_consumption=evidence.get("feature_consumption", {}),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities", help="report exact support gaps without model execution")
    pricing = commands.add_parser("derive-pricing", help="retain request estimates separately from unmodified usage")
    pricing.add_argument("--usage", type=Path, required=True)
    pricing.add_argument("--contexts", type=Path, required=True)
    pricing.add_argument("--hook-source", type=Path, required=True)
    pricing.add_argument("--hook-usage-digest", required=True)
    pricing.add_argument("--output", type=Path, required=True)
    prepare = commands.add_parser("prepare", help="write immutable schedule and concrete scratch inputs")
    prepare.add_argument("--pins", type=Path, required=True)
    prepare.add_argument("--mode", choices=("cold", "warm"), required=True)
    prepare.add_argument("--seed", type=int, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    collect = commands.add_parser("collect", help="retain an execution receipt without claiming acceptance")
    collect.add_argument("--run-set", type=Path, required=True)
    collect.add_argument("--run-id", required=True)
    collect.add_argument("--evidence", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--semantic", action="store_true", help="run pinned deterministic checks against retained produced artifacts")
    args = parser.parse_args()
    try:
        if args.command == "capabilities":
            print(json.dumps(study_capabilities(), indent=2, sort_keys=True))
            return 0
        if args.command == "derive-pricing":
            result = derive_request_prices(args.usage, args.contexts, hook_source=args.hook_source,
                                           expected_hook_usage_digest=args.hook_usage_digest)
            write_new(args.output, result)
            print(json.dumps({"derived": True, "complete_accounting": False, "promotion_eligible": False}))
            return 0
        if args.command == "prepare":
            prepared = prepare_run_set(pins=pins_from_mapping(read_json(args.pins)), execution_mode=args.mode, seed=args.seed)
            if args.output.exists() and any(args.output.iterdir()):
                raise ValueError("preparation output must be empty")
            args.output.mkdir(parents=True, exist_ok=True)
            prepare_fixture_scratch(args.output / "task-inputs")
            write_new(args.output / "run-set.json", prepared.payload() | {"run_set_digest": prepared.run_set_digest})
            write_new(args.output / "definition.json", manifest_payload())
            write_new(args.output / "capabilities.json", study_capabilities())
            print(json.dumps({"prepared": True, "dispatch_ready": False, "run_set_digest": prepared.run_set_digest}))
            return 0
        prepared = restore_prepared(read_json(args.run_set))
        receipt = collect_receipt(prepared, args.run_id, read_json(args.evidence))
        semantic_errors = []
        if args.semantic:
            if prepared.pins.skill_pins.get("semantic-validators") != validator_digest():
                raise ValueError("semantic evaluator differs from the preparation pin")
            request = next(run for run in prepared.runs if run.id == args.run_id)
            if receipt.completion_status == "completed":
                try:
                    receipt = replace(receipt, invariant_evidence=semantic_proofs(
                        request.task_id, receipt, Path(__file__).with_name("task_inputs")))
                except (OSError, ValueError, KeyError, TypeError) as error:
                    semantic_errors.append(str(error))
            else:
                semantic_errors.append("execution did not complete; semantic acceptance remains absent")
        errors = validate_receipt(receipt, prepared)
        write_new(args.output, {"schema_version": "collected-benchmark-receipt-v1",
                               "receipt": asdict(receipt), "structural_errors": errors,
                               "semantic_errors": semantic_errors,
                               "semantic_observations": semantic_observations(receipt) if receipt.invariant_evidence else None,
                               "acceptance_verified": False})
        print(json.dumps({"collected": True, "acceptance_verified": False, "structural_errors": errors}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
