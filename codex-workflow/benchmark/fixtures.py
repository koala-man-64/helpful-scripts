"""Deterministic scratch inputs; no fixture asserts a real host event occurred."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .manifest import FIXED_MANIFEST

TASK_INPUTS = Path(__file__).with_name("task_inputs")


def prepare_fixture_scratch(output: Path) -> Path:
    """Materialize fixed descriptors in a caller-selected empty scratch directory."""
    if output.exists() and any(output.iterdir()):
        raise ValueError("fixture scratch directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    fixed = json.loads((TASK_INPUTS / "fixed-inputs.json").read_text(encoding="utf-8"))
    descriptors = []
    for task in FIXED_MANIFEST:
        source = TASK_INPUTS / task.id
        if not source.is_dir():
            raise ValueError(f"missing concrete task input directory: {task.id}")
        target = output / task.id
        shutil.copytree(source, target)
        host_event = any(item in task.scenarios for item in ("compaction", "resumable_wait", "children"))
        descriptor = {"task_id": task.id, "fixture_file": f"{task.id}.json", "acceptance_invariants": task.acceptance_invariants, "scenarios": task.scenarios, "host_event_requirement": host_event, "status": "requires_actual_adapter_evidence" if host_event else "input_ready_only"}
        descriptor["fixed_input_digest"] = fixed[task.id]["input_digest"]
        data = json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode("utf-8")
        (output / descriptor["fixture_file"]).write_bytes(data)
        descriptors.append(descriptor | {"digest": "sha256:" + hashlib.sha256(data).hexdigest()})
    index = {"schema_version": "benchmark-fixtures-v1", "fixtures": descriptors}
    (output / "index.json").write_bytes(json.dumps(index, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return output / "index.json"
