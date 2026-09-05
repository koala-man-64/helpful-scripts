"""Trusted evaluation against runner-retained artifacts, not model pass flags."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from .manifest import FIXED_MANIFEST
from .semantic_evidence import capture_workspace, file_digest, inventory, read_cli_trace, verified_reference
from .semantic_git import _changed_paths, evaluate_git
from .semantic_local import evaluate_local
from .semantic_research import evaluate_research

HOST_TASKS = {"cross-urgent-peer-negation", "cross-child-review-recovery",
              "research-compaction-retention", "research-resumable-wait"}
LOCAL_TASKS = {"localized-failed-test", "localized-long-output", "localized-missing-dependency"}
RESEARCH_TASKS = {"research-external-fixture", "research-clarification-and-failure"}


def validator_digest() -> str:
    digest = hashlib.sha256()
    for name in ("semantic_evidence.py", "semantic_git.py", "semantic_local.py",
                 "semantic_research.py", "semantic_validation.py"):
        raw = Path(__file__).with_name(name).read_bytes()
        digest.update(name.encode() + len(raw).to_bytes(8, "big") + raw)
    return "sha256:" + digest.hexdigest()


def fixture_record(task_inputs: Path, task_id: str) -> dict:
    record = json.loads((task_inputs / "fixed-inputs.json").read_bytes())[task_id]
    actual = inventory(task_inputs / task_id)
    if actual != record["files"]:
        raise ValueError("fixed semantic input bytes differ from their manifest")
    return record


def semantic_preparation_pins(task_inputs: Path) -> dict:
    return {"skill_pins": {
        "semantic-validators": validator_digest(),
        "validator:deterministic-semantic-v1": file_digest(Path(__file__).resolve()),
    }, "external_fixtures": {task.id: fixture_record(task_inputs, task.id)["input_digest"] for task in FIXED_MANIFEST}}


def evaluate_semantics(task_id: str, receipt, task_inputs: Path) -> dict[str, bool]:
    task = next(task for task in FIXED_MANIFEST if task.id == task_id)
    rejected = dict.fromkeys(task.acceptance_invariants, False)
    if task_id in HOST_TASKS:
        return rejected
    fixture_record(task_inputs, task_id)
    refs = receipt.raw_artifact_refs
    paths = {name: verified_reference(refs[name]) for name in
             ("dispatch", "semantic_before", "semantic_after", "events", "last_message")}
    dispatch = json.loads(paths["dispatch"].read_bytes())
    for name in ("semantic_before", "semantic_after", "events", "last_message"):
        retained = dispatch.get("raw_artifact_refs", {}).get(name, {})
        if retained.get("digest") != refs[name]["digest"]:
            raise ValueError("semantic artifact is not bound to the actual dispatch")
    if dispatch.get("run_request", {}).get("id") != receipt.run_id:
        raise ValueError("semantic dispatch belongs to a different prepared run")
    before = json.loads(paths["semantic_before"].read_bytes())
    after = json.loads(paths["semantic_after"].read_bytes())
    workspace = Path(after["workspace"]).resolve(strict=True)
    if before.get("workspace") != str(workspace) or after != capture_workspace(workspace):
        raise ValueError("produced workspace differs from retained runner observation")
    fixed = fixture_record(task_inputs, task_id)["files"]
    exceptions = {"version.txt"} if task_id == "localized-wrong-checkout" else set()
    if any(before["files"].get(name) != digest for name, digest in fixed.items() if name not in exceptions):
        raise ValueError("initial workspace did not contain the pinned task inputs")
    commands = read_cli_trace(paths["events"], receipt.adapter_session_id)
    final_text = paths["last_message"].read_text(encoding="utf-8")
    if task_id in LOCAL_TASKS:
        allowed = set(fixed) | {"logs/build.log"}
        filtered = []
        for row in commands:
            if row.get("type") == "file_change":
                changed = _changed_paths(row, workspace)
                if changed is None or not changed <= allowed:
                    return rejected
            else:
                filtered.append(row)
        # Produced Python executes only in a temporary copy with bytecode off.
        # This is process isolation, not an OS security sandbox.
        with tempfile.TemporaryDirectory(prefix="benchmark-semantic-") as temporary:
            baseline_copy, produced_copy = Path(temporary) / "baseline", Path(temporary) / "produced"
            shutil.copytree(task_inputs / task_id, baseline_copy)
            shutil.copytree(workspace, produced_copy, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            results = evaluate_local(task_id, baseline=baseline_copy, workspace=produced_copy,
                                     final_text=final_text, commands=filtered, raw_refs=refs)
    elif task_id in RESEARCH_TASKS:
        results = evaluate_research(task_id, baseline=task_inputs / task_id, workspace=workspace,
                                    final_text=final_text, commands=commands, raw_refs=refs)
    else:
        results = evaluate_git(task_id, baseline=task_inputs / task_id, workspace=workspace,
                               final_text=final_text, commands=commands, raw_refs=refs)
    if after != capture_workspace(workspace):
        raise ValueError("workspace changed while semantic checks were running")
    return {name: results.get(name) is True for name in task.acceptance_invariants}


def semantic_proofs(task_id: str, receipt, task_inputs: Path) -> dict:
    results = evaluate_semantics(task_id, receipt, task_inputs)
    output_dir = Path(receipt.raw_artifact_refs["semantic_after"]["path"]).parent / "semantic-checks"
    output_dir.mkdir(exist_ok=True)
    implementation = Path(__file__).resolve()
    observations = semantic_observations(receipt)
    ids = [row["observation_id"] for row in observations["observations"]]
    proofs = {}
    for name, passed in results.items():
        proof = {"schema_version": "benchmark-check-v1", "run_id": receipt.run_id,
                 "task_id": receipt.task_id, "invariant_id": name,
                 "validator_id": "deterministic-semantic-v1", "outcome": "satisfied" if passed else "rejected",
                 "observation_ids": ids,
                 "validator_ref": {"path": str(implementation), "digest": file_digest(implementation)},
                 "evidence_refs": list(receipt.raw_artifact_refs.values())}
        path = output_dir / (name + ".json")
        raw = json.dumps(proof, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError("semantic proof changed; retain it and use a fresh attempt directory")
        else:
            with path.open("xb") as stream:
                stream.write(raw)
        proofs[name] = {"validator_id": proof["validator_id"], "validator_digest": validator_digest(),
                        "manifest_task_id": receipt.manifest_task_id, "task_id": receipt.task_id,
                        "artifact_digest": file_digest(path),
                        "artifact_ref": {"path": str(path.resolve()), "digest": file_digest(path)},
                        "observation_ids": ids, "outcome": proof["outcome"]}
    return proofs


def semantic_observations(receipt) -> dict:
    """Purpose-specific raw evidence linkage; this is not usage accounting."""
    raw_digest = receipt.raw_artifact_refs["events"]["digest"]
    identity = hashlib.sha256(("benchmark-semantic-v1:" + receipt.task_id + ":" + raw_digest).encode()).hexdigest()
    return {"schema_version": "benchmark-observations-v1", "observations": [{
        "observation_id": identity, "task_id": receipt.task_id,
        "adapter_session_id": receipt.adapter_session_id, "artifact_digest": raw_digest,
    }]}


def build_semantic_validators(task_inputs: Path, *, expected_validator_digest: str) -> dict:
    if validator_digest() != expected_validator_digest:
        raise ValueError("semantic evaluator source differs from its immutable preparation pin")

    def check(run, receipt, evidence, invariant):
        if validator_digest() != expected_validator_digest or evidence.get("validator_digest") != expected_validator_digest:
            return False
        proof = json.loads(verified_reference(evidence.get("artifact_ref", {})).read_bytes())
        if (proof.get("run_id") != receipt.run_id or proof.get("task_id") != receipt.task_id
                or proof.get("invariant_id") != invariant
                or proof.get("validator_id") != "deterministic-semantic-v1"
                or proof.get("observation_ids") != evidence.get("observation_ids")
                or [item.get("digest") for item in proof.get("evidence_refs", [])]
                    != [item["digest"] for item in receipt.raw_artifact_refs.values()]):
            return False
        for reference in proof["evidence_refs"]:
            verified_reference(reference)
        if file_digest(verified_reference(proof["validator_ref"])) != file_digest(Path(__file__).resolve()):
            return False
        return evaluate_semantics(run.task_id, receipt, task_inputs).get(invariant, False)

    return {name: (lambda run, receipt, evidence, name=name: check(run, receipt, evidence, name))
            for task in FIXED_MANIFEST for name in task.acceptance_invariants}
