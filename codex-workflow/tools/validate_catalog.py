"""Fail-closed, stdlib-only validation for the advisory workflow catalog."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

from catalog_lib import (
    canonical_hash,
    canonical_git_hash,
    load_document,
    validate_schema,
)

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
STATES = {"verified_nondivergent", "unresolved_fork", "conflict_blocked", "deprecated"}
SCENARIOS = {
    "narrow local fix",
    "standard feature",
    "cross-repo contract change",
    "CI incident",
    "production/IaC change",
}
PROJECTS_ROOT = Path.home() / "Projects"


def parse_repository_mappings(
    values: list[str], *, require_complete: bool = False
) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"--repo must use ID=PATH: {item}")
        name, value = item.split("=", 1)
        if name not in REPOSITORIES or not value:
            raise ValueError(f"--repo has unknown ID or empty path: {item}")
        if name in roots:
            raise ValueError(f"--repo mapping is duplicated: {name}")
        roots[name] = Path(value)
    if require_complete and set(roots) != REPOSITORIES:
        missing = ", ".join(sorted(REPOSITORIES - set(roots))) or "none"
        extra = ", ".join(sorted(set(roots) - REPOSITORIES)) or "none"
        raise ValueError(
            "--strict-origin requires exactly one mapping for every repository; "
            f"missing: {missing}; unexpected: {extra}"
        )
    return roots


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


def validate_candidate_sources(root: Path, errors: list[str]) -> None:
    """Validate disabled candidate sources without treating them as active pins."""
    try:
        bundle = load_document(root / "candidates" / "bundle.json")
    except (OSError, ValueError) as error:
        errors.append(f"candidate bundle: {error}")
        return
    if not exact_keys(
        bundle,
        {"schema_version", "package", "installation", "activation", "sources"},
        "candidate bundle",
        errors,
    ):
        return
    if (
        bundle.get("schema_version") != "candidate-source-bundle-v1"
        or bundle.get("package") != "codex-workflow"
        or bundle.get("installation") != "disabled_not_installed"
        or bundle.get("activation") is not False
    ):
        errors.append("candidate bundle installation contract is invalid")
    sources = bundle.get("sources")
    expected_ids = {
        "compact-global-instructions",
        "managed-subagent-task-contract",
        "instruction-profiles",
        "selection-policy",
        "candidate-browser-evidence",
        "candidate-git-hygiene",
    }
    if not isinstance(sources, list) or len(sources) != len(expected_ids):
        errors.append("candidate source coverage is invalid")
        return
    seen = set()
    candidate_root = root / "candidates"
    for source in sources:
        if not exact_keys(source, {"id", "path", "content_hash"}, "candidate source", errors):
            continue
        source_id, path, digest = source.get("id"), source.get("path"), source.get("content_hash")
        seen.add(source_id)
        if (
            not isinstance(source_id, str)
            or not isinstance(path, str)
            or not path.startswith(("sources/", "skills/"))
            or "/../" in f"/{path}"
            or not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        ):
            errors.append("candidate source metadata is invalid")
            continue
        source_path = candidate_root / path
        if not source_path.resolve().is_relative_to(candidate_root.resolve()):
            errors.append("candidate source escapes the owned source root")
            continue
        if not source_path.is_file() and not source_path.is_dir():
            errors.append(f"candidate source is absent: {path}")
            continue
        actual = (
            "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest()
            if source_path.is_file()
            else canonical_hash(source_path)
        )
        if digest != actual:
            errors.append(f"candidate source content hash does not match: {source_id}")
    if seen != expected_ids:
        errors.append("candidate source IDs are invalid")
    try:
        policy = load_document(candidate_root / "sources" / "selection-policy.json")
    except (OSError, ValueError) as error:
        errors.append(f"candidate selection policy: {error}")
        return
    if not exact_keys(
        policy, {"schema_version", "skills", "language_references", "reuse"},
        "candidate selection policy", errors,
    ):
        return
    if policy.get("schema_version") != "candidate-selection-policy-v1" or policy.get(
        "reuse"
    ) != "retained_identical_applicable_context_only_no_automatic_cache":
        errors.append("candidate selection policy metadata is invalid")
    expected_skills = {
        "candidate-browser-evidence": (
            "skills/browser-evidence/SKILL.md", "rendered_or_interactive_state_material"
        ),
        "candidate-git-hygiene": (
            "skills/git-hygiene-candidate/SKILL.md", "explicit_git_hygiene_or_cleanup"
        ),
    }
    skills = policy.get("skills")
    if not isinstance(skills, list) or len(skills) != len(expected_skills):
        errors.append("candidate skill trigger coverage is invalid")
    else:
        for item in skills:
            if not exact_keys(item, {"id", "path", "trigger", "references"}, "candidate skill trigger", errors):
                continue
            expected = expected_skills.get(item.get("id"))
            if (
                expected is None
                or (item.get("path"), item.get("trigger")) != expected
                or item.get("references") != []
                or not (candidate_root / expected[0]).is_file()
            ):
                errors.append("candidate skill trigger or reference is invalid")
    expected_languages = {"csharp", "python", "sql"}
    references = policy.get("language_references")
    if not isinstance(references, list) or {item.get("language") for item in references if isinstance(item, dict)} != expected_languages:
        errors.append("candidate language reference coverage is invalid")
    else:
        for item in references:
            if not exact_keys(item, {"language", "path", "select_when"}, "candidate language reference", errors):
                continue
            path, selections = item.get("path"), item.get("select_when")
            if (
                not isinstance(path, str)
                or not path.startswith("references/")
                or not (candidate_root / path).resolve().is_relative_to(candidate_root.resolve())
                or not (candidate_root / path).is_file()
                or not isinstance(selections, list)
                or not selections
                or not all(isinstance(selection, str) and selection for selection in selections)
            ):
                errors.append("candidate language reference is invalid")


def validate(
    root: Path,
    repository_roots: dict[str, Path] | None = None,
    strict_origin: bool = False,
) -> list[str]:
    errors = []
    # Explicit roots make validation portable; the historical Projects fallback
    # is retained only for local compatibility.
    default_repository_roots = {name: PROJECTS_ROOT / name for name in REPOSITORIES}
    supplied_repository_roots = repository_roots or {}
    if strict_origin and set(supplied_repository_roots) != REPOSITORIES:
        errors.append(
            "strict origin validation requires explicit mappings for exactly five repositories"
        )
        repository_roots = supplied_repository_roots
    else:
        repository_roots = (
            supplied_repository_roots
            if strict_origin
            else {**default_repository_roots, **supplied_repository_roots}
        )
    inventory = doc(root, "origin-inventory.yaml", errors)
    manifest = doc(root, "skills.yaml", errors)
    try:
        schema = load_document(root / "schemas" / "skill-manifest-v2.schema.json")
        errors.extend(validate_schema(manifest, schema, "skills.yaml"))
    except (OSError, ValueError) as error:
        errors.append(f"manifest schema: {error}")
    decisions = doc(root, "skill-decisions.yaml", errors)
    surface = doc(root, "active-surface.yaml", errors)
    variants = doc(root, "observed-variants.yaml", errors)
    scenarios = doc(root, "routing-scenarios.yaml", errors)
    validate_candidate_sources(root, errors)
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
            continue
        checkout = repository_roots.get(item["id"])
        if checkout is None or not checkout.is_dir():
            errors.append(f"{item['id']}: repository mapping is missing or invalid")
            continue
        if strict_origin:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(checkout),
                    "merge-base",
                    "--is-ancestor",
                    item["origin_sha"],
                    "origin/main",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            if result.returncode:
                errors.append(
                    f"{item['id']}: inventory origin SHA is not reachable from fetched origin/main"
                )
        result = subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "cat-file",
                "-e",
                f"{item['origin_sha']}:{item['skill_root']}",
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode:
            errors.append(f"{item['id']}: inventory skill_root is absent at origin SHA")
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
        if not isinstance(source, dict):
            errors.append(f"{item.get('id', 'unknown')}: source must be an object")
            continue
        if (
            not isinstance(item.get("id"), str)
            or not ID.fullmatch(item["id"])
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
            or item.get("canonical_state") not in STATES
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
        if source.get("repository") in REPOSITORIES and isinstance(
            source.get("path"), str
        ):
            source_repo = repository_roots.get(source["repository"])
            if source_repo is None or not source_repo.is_dir():
                errors.append(
                    f"{item.get('id')}: repository mapping is missing or invalid"
                )
                continue
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(source_repo),
                    "rev-parse",
                    f"{source['commit']}:{source['path']}",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            expected = f"git-tree-sha1:{result.stdout.strip()}"
            if result.returncode or item.get("content_hash") != expected:
                errors.append(
                    f"{item.get('id')}: origin source tree hash does not match"
                )
            inventory_row = next(
                (
                    x
                    for x in repos
                    if isinstance(x, dict) and x.get("id") == source["repository"]
                ),
                None,
            )
            if inventory_row and source.get("commit") != inventory_row.get(
                "origin_sha"
            ):
                errors.append(
                    f"{item.get('id')}: source commit does not equal inventory SHA"
                )
    if len(ids) != len(set(ids)) or set(ids) != set(shared) | {"workflow-router"}:
        errors.append(
            "manifest must cover the exact 52 inventory IDs plus workflow-router"
        )
    router = next(
        (x for x in skills if isinstance(x, dict) and x.get("id") == "workflow-router"),
        {},
    )
    router_source = router.get("source") if isinstance(router, dict) else None
    router_path = (
        router_source.get("path", "") if isinstance(router_source, dict) else ""
    )
    path = root.parent / router_path
    if not path.is_dir():
        errors.append("workflow-router source path does not exist")
    else:
        router_commit = (
            str(router_source.get("commit", ""))
            if isinstance(router_source, dict)
            else ""
        )
        try:
            committed_hash = canonical_git_hash(root.parent, router_commit, router_path)
        except ValueError as error:
            errors.append(f"workflow-router claimed source is invalid: {error}")
        else:
            if router.get("content_hash") != committed_hash:
                errors.append(
                    "workflow-router claimed commit content hash does not match"
                )
        if strict_origin:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root.parent),
                    "merge-base",
                    "--is-ancestor",
                    router_commit,
                    "origin/main",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            if result.returncode:
                errors.append(
                    "workflow-router claimed commit is not reachable from fetched origin/main"
                )
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
    if any(
        next(
            (x for x in skills if isinstance(x, dict) and x.get("id") == skill_id), {}
        ).get("canonical_state")
        != "unresolved_fork"
        or next(
            (x for x in skills if isinstance(x, dict) and x.get("id") == skill_id), {}
        ).get("runnable")
        for skill_id in FORKS
    ):
        errors.append("unresolved forks must remain non-runnable")
    strict_skill = next(
        (
            x
            for x in skills
            if isinstance(x, dict)
            and x.get("id") == "strict-branch-and-merge-discipline"
        ),
        {},
    )
    if (
        strict_skill.get("canonical_state") != "conflict_blocked"
        or strict_skill.get("runnable")
        or "force_with_lease_grant" not in strict_skill.get("conflicts", [])
    ):
        errors.append("strict branch discipline must remain conflict blocked")
    gateway = next(
        (x for x in skills if isinstance(x, dict) and x.get("id") == "gateway-agent"),
        {},
    )
    if (
        gateway.get("canonical_state") != "deprecated"
        or gateway.get("runnable")
        or status.get("gateway-agent") != "deprecated"
    ):
        errors.append("gateway-agent must remain deprecated and non-runnable")
    if decisions.get("record_authority") != {
        "mutation_evidence": "central_hooks",
        "tracked_delivery": "azure_boards_when_applicable",
        "coordination_ledger": "none_by_default",
        "jsonl": "regulated_audit_only_when_explicitly_required",
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
        pins = string_list(lane.get("lane_required_skill_ids"), f"{name} pins", errors)
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
        or standard.get("primary_route", {}).get("model") != "Terra"
        or standard.get("primary_route", {}).get("effort") != "medium"
        or standard.get("minimum_agents") != 2
        or standard.get("max_specialists") != 1
    ):
        errors.append("standard lane invariant failed")
    if (
        not critical.get("orchestrator_role")
        or critical.get("max_specialists") != 3
        or set(critical.get("gate_owners", {})) != {"ownership", "security", "qa"}
        or critical.get("primary_route", {}).get("model") != "Sol"
        or critical.get("primary_route", {}).get("effort") != "high"
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
    parser.add_argument("--repo", action="append", default=[], metavar="ID=PATH")
    parser.add_argument("--strict-origin", action="store_true")
    args = parser.parse_args()
    try:
        roots = parse_repository_mappings(
            args.repo, require_complete=args.strict_origin
        )
    except ValueError as error:
        parser.error(str(error))
    errors = validate(args.root, roots or None, args.strict_origin)
    if errors:
        print("Catalog validation failed:\n" + "\n".join("- " + x for x in errors))
        return 1
    print("Catalog validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
