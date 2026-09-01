"""Fail-closed, stdlib-only validation for the advisory workflow catalog."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from catalog_lib import load_document

ID = re.compile(r"^[a-z0-9-]+$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
TREE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORIES = {
    "asset-allocation-contracts",
    "asset-allocation-control-plane",
    "asset-allocation-jobs",
    "asset-allocation-runtime-common",
    "asset-allocation-ui",
}
FORKS = {
    "code-drift-sentinel",
    "data-engineer-data-architect-advisor",
    "delivery-orchestrator-agent",
    "git-hygiene-orchestrator",
    "project-workflow-auditor-agent",
    "provisioning-configuration-and-disaster-recovery-expert",
}
EVIDENCE = {"source", "ci", "release", "deployment", "runtime", "user_path"}
MODEL = {"Luna": 0, "Terra": 1, "Sol": 2}
EFFORT = {"low": 0, "medium": 1, "high": 2, "ultra": 3}
SCENARIOS = {
    "narrow local fix",
    "standard feature",
    "cross-repo contract change",
    "CI incident",
    "production/IaC change",
}


def doc(root: Path, name: str, errors: list[str]):
    try:
        value = load_document(root / "catalog" / name)
    except (OSError, ValueError) as error:
        errors.append(str(error))
        return {}
    if not isinstance(value, dict):
        errors.append(f"{name}: root must be an object")
        return {}
    return value


def string_list(value, label, errors):
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        errors.append(f"{label}: must be a string list")
        return []
    if len(value) != len(set(value)):
        errors.append(f"{label}: duplicates are not allowed")
    return value


def exact_keys(value, keys, label, errors):
    if not isinstance(value, dict):
        errors.append(f"{label}: must be an object")
        return False
    if set(value) != set(keys):
        errors.append(f"{label}: unknown field or missing required field")
        return False
    return True


def route(value, label, errors):
    if not exact_keys(value, {"model", "effort", "role"}, label, errors):
        return None
    if (
        value["model"] not in MODEL
        or value["effort"] not in EFFORT
        or not isinstance(value["role"], str)
    ):
        errors.append(f"{label}: invalid route")
        return None
    return value


def validate(root: Path) -> list[str]:
    errors = []
    inventory = doc(root, "origin-inventory.yaml", errors)
    manifest = doc(root, "skills.yaml", errors)
    decisions = doc(root, "skill-decisions.yaml", errors)
    surface = doc(root, "active-surface.yaml", errors)
    variants = doc(root, "observed-variants.yaml", errors)
    scenarios = doc(root, "routing-scenarios.yaml", errors)
    if (
        inventory.get("schema_version") != "origin-inventory-v1"
        or inventory.get("hash_algorithm") != "git-tree-sha1"
    ):
        errors.append("origin inventory version or hash algorithm is invalid")
    repos = inventory.get("repositories")
    shared = string_list(
        inventory.get("shared_skill_ids"), "origin inventory IDs", errors
    )
    if len(shared) != 52 or any(not ID.fullmatch(x) for x in shared):
        errors.append("origin inventory must contain exactly 52 valid IDs")
    if (
        not isinstance(repos, list)
        or {x.get("id") for x in repos if isinstance(x, dict)} != REPOSITORIES
    ):
        errors.append("origin inventory repository coverage is invalid")
    for item in repos if isinstance(repos, list) else []:
        if (
            not exact_keys(
                item,
                {"id", "origin_ref", "origin_sha", "skill_root"},
                "origin repository",
                errors,
            )
            or item.get("origin_ref") != "origin/main"
            or not isinstance(item.get("origin_sha"), str)
            or not COMMIT.fullmatch(item["origin_sha"])
            or item.get("skill_root") != ".codex/skills"
        ):
            errors.append("origin repository provenance is invalid")
    if (
        manifest.get("schema_version") != "skill-manifest-v2"
        or manifest.get("inventory") != "origin-inventory.yaml"
    ):
        errors.append("manifest version or inventory reference is invalid")
    skills = manifest.get("skills")
    ids = []
    if not isinstance(skills, list):
        errors.append("skills must be a list")
        skills = []
    for item in skills:
        if not exact_keys(
            item,
            {
                "id",
                "owner",
                "source",
                "content_hash",
                "content_hash_algorithm",
                "version",
                "supported_repositories",
                "risk_tier",
                "external_state_behavior",
                "conflicts",
                "supersession",
                "review_date",
                "canonical_state",
                "runnable",
            },
            "manifest skill",
            errors,
        ):
            continue
        ids.append(item.get("id"))
        source = item.get("source")
        if (
            not isinstance(item.get("id"), str)
            or not ID.fullmatch(item["id"])
            or not isinstance(source, dict)
            or set(source) != {"repository", "path", "commit"}
            or not isinstance(source.get("path"), str)
            or not isinstance(source.get("commit"), str)
            or not COMMIT.fullmatch(source["commit"])
        ):
            errors.append("manifest source path or claimed commit is invalid")
        if (
            source.get("repository") not in REPOSITORIES | {"helpful-scripts"}
            or item.get("content_hash_algorithm") not in {"git-tree-sha1", "sha256"}
            or not isinstance(item.get("content_hash"), str)
            or not item["content_hash"].startswith(item["content_hash_algorithm"] + ":")
            or item.get("canonical_state")
            not in {"verified_nondivergent", "unresolved_fork"}
            or not isinstance(item.get("runnable"), bool)
            or not isinstance(item.get("owner"), str)
            or not isinstance(item.get("version"), str)
            or item.get("risk_tier") not in {"lite", "standard", "critical"}
            or item.get("external_state_behavior")
            not in {"advisory", "read_only", "mutation_requires_hook"}
            or not isinstance(item.get("conflicts"), list)
            or item.get("supersession") is not None
            and not isinstance(item.get("supersession"), str)
            or not isinstance(item.get("review_date"), str)
        ):
            errors.append("manifest source metadata is invalid")
        supported = string_list(
            item.get("supported_repositories"),
            f"{item.get('id', 'unknown')} repositories",
            errors,
        )
        if set(supported) != REPOSITORIES:
            errors.append(
                f"{item.get('id', 'unknown')}: repository coverage is invalid"
            )
        if item.get("canonical_state") == "unresolved_fork" and item.get("runnable"):
            errors.append(f"{item.get('id')}: unresolved fork is runnable")
    if len(ids) != len(set(ids)) or set(ids) != set(shared) | {"workflow-router"}:
        errors.append(
            "manifest must cover the exact 52 inventory IDs plus workflow-router"
        )
    router = next(
        (x for x in skills if isinstance(x, dict) and x.get("id") == "workflow-router"),
        {},
    )
    path = root.parent / router.get("source", {}).get("path", "")
    if not path.is_dir():
        errors.append("workflow-router source path does not exist")
    else:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root.parent),
                "cat-file",
                "-e",
                router["source"]["commit"] + ":" + router["source"]["path"],
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode:
            errors.append("workflow-router claimed commit does not contain source path")
    rows = decisions.get("decisions")
    decision_ids = []
    if not isinstance(rows, list):
        errors.append("decisions must be a list")
        rows = []
    for row in rows:
        if not exact_keys(
            row, {"id", "status", "disposition", "rationale"}, "decision", errors
        ):
            continue
        decision_ids.append(row.get("id"))
        if (
            row.get("status") not in {"active", "on_demand", "blocked", "deprecated"}
            or row.get("disposition") not in {"keep", "merge", "deprecate"}
            or not isinstance(row.get("rationale"), str)
        ):
            errors.append("decision enum is invalid")
    if len(decision_ids) != len(set(decision_ids)) or set(decision_ids) != set(ids):
        errors.append("decision matrix must cover every catalog skill exactly once")
    status = {x["id"]: x["status"] for x in rows if isinstance(x, dict) and "id" in x}
    if any(status.get(x) != "blocked" for x in FORKS):
        errors.append("unresolved forks must be blocked")
    if decisions.get("record_authority") != {
        "mutation_and_evidence": "central_hooks",
        "tracked_delivery": "azure_boards",
        "coordination_or_regulated_audit": "csv_jsonl_when_explicitly_required",
    }:
        errors.append("record authority is invalid")
    active = string_list(surface.get("active_skill_ids"), "active surface", errors)
    if not 10 <= len(active) <= 15 or any(
        x not in ids or status.get(x) != "active" for x in active
    ):
        errors.append("active surface must contain 10-15 active known IDs")
    runnable = {
        x["id"]: x["runnable"] for x in skills if isinstance(x, dict) and "id" in x
    }
    if any(not runnable.get(x) for x in active):
        errors.append("active surface cannot select unresolved skills")
    lanes = surface.get("lanes", {})
    if not isinstance(lanes, dict) or set(lanes) != {"lite", "standard", "critical"}:
        errors.append("lane coverage is invalid")
        lanes = {}
    for name, lane in lanes.items():
        if not isinstance(lane, dict):
            errors.append(f"{name}: invalid lane")
            continue
        pins = string_list(lane.get("available_skill_pins"), f"{name} pins", errors)
        if any(x not in active for x in pins):
            errors.append(f"{name}: unavailable pin")
    lite = lanes.get("lite", {})
    standard = lanes.get("standard", {})
    critical = lanes.get("critical", {})
    if (
        any(
            lite.get(x)
            for x in ("orchestrator_role", "boards", "ledger", "subagents_permitted")
        )
        or lite.get("max_specialists") != 0
    ):
        errors.append("lite lane invariant failed")
    if (
        standard.get("orchestrator_role")
        or not standard.get("focused_qa_required")
        or standard.get("max_specialists") != 1
    ):
        errors.append("standard lane invariant failed")
    if (
        not critical.get("orchestrator_role")
        or critical.get("max_specialists") != 3
        or set(critical.get("gate_owners", [])) != {"ownership", "security", "qa"}
        or set(critical.get("evidence_required", [])) != EVIDENCE
        or critical.get("evidence_not_applicable") != []
    ):
        errors.append("critical lane invariant failed")
    if (
        variants.get("schema_version") != "observed-variants-v3"
        or variants.get("hash_algorithm") != "git-tree-sha1"
        or variants.get("owner_confirmed_overlays") != []
    ):
        errors.append("variant metadata is invalid")
    listed = set()
    for item in (
        variants.get("variants", [])
        if isinstance(variants.get("variants"), list)
        else []
    ):
        if not exact_keys(
            item,
            {"skill_id", "skill_path", "overlay_intent", "tree_hashes"},
            "variant",
            errors,
        ):
            continue
        listed.add(item.get("skill_id"))
        if (
            item.get("overlay_intent") != "unresolved_fork"
            or set(item.get("tree_hashes", {})) != REPOSITORIES
            or not all(
                isinstance(v, str) and TREE.fullmatch(v)
                for v in item.get("tree_hashes", {}).values()
            )
        ):
            errors.append("invalid unresolved fork")
    if listed != FORKS:
        errors.append("observed variant coverage is invalid")
    names = set()
    for item in (
        scenarios.get("scenarios", [])
        if isinstance(scenarios.get("scenarios"), list)
        else []
    ):
        if not isinstance(item, dict):
            errors.append("invalid routing scenario")
            continue
        names.add(item.get("name"))
        parent = route(item.get("primary_route"), "scenario parent", errors)
        children = item.get("child_routes")
        if (
            item.get("lane") not in lanes
            or not isinstance(item.get("minimum_agents"), int)
            or item["minimum_agents"] < 1
            or not isinstance(children, list)
            or not set(item.get("evidence_required", [])).isdisjoint(
                set(item.get("evidence_not_applicable", []))
            )
            or set(item.get("evidence_required", []))
            | set(item.get("evidence_not_applicable", []))
            != EVIDENCE
        ):
            errors.append("routing scenario partition is invalid")
            continue
        for child in children:
            child = route(child, "scenario child", errors)
            if (
                parent
                and child
                and (
                    child["model"] == "Sol"
                    or child["effort"] == "ultra"
                    or MODEL[child["model"]] >= MODEL[parent["model"]]
                    or EFFORT[child["effort"]] >= EFFORT[parent["effort"]]
                )
            ):
                errors.append("routing child must be strictly lower and never Ultra")
        if parent and parent["model"] == "Luna" and children:
            errors.append("Luna/low cannot delegate")
    if names != SCENARIOS:
        errors.append("routing scenario coverage is incomplete")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    errors = validate(args.root)
    if errors:
        print("Catalog validation failed:\n" + "\n".join("- " + x for x in errors))
        return 1
    print("Catalog validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
