"""Fail-closed validation for the advisory catalog and its runnable locks."""
from __future__ import annotations
import argparse
import re
from pathlib import Path
from catalog_lib import canonical_hash, load_document

HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
EVIDENCE = ["source", "ci", "release", "deployment", "runtime", "user_path"]

def validate(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        manifest = load_document(root / "catalog" / "skills.yaml")
        surface = load_document(root / "catalog" / "active-surface.yaml")
        decisions = load_document(root / "catalog" / "skill-decisions.yaml")
        variants = load_document(root / "catalog" / "observed-variants.yaml")
        scenarios = load_document(root / "catalog" / "routing-scenarios.yaml")
    except ValueError as error: return [str(error)]
    if manifest.get("$schema") != "../schemas/skill-manifest-v1.schema.json": errors.append("invalid manifest schema reference")
    skills = manifest.get("skills")
    if not isinstance(skills, list): return errors + ["skills must be a list"]
    ids = [item.get("id") for item in skills if isinstance(item, dict)]
    if len(ids) != len(set(ids)) or None in ids: errors.append("skill IDs must be unique and nonempty")
    required = {"id","owner","source","content_hash","version","supported_repositories","risk_tier","external_state_behavior","conflicts","supersession","review_date","overlay_intent"}
    for item in skills:
        if not isinstance(item, dict) or set(item) - required: errors.append(f"{item.get('id','<unknown>')}: unknown field"); continue
        if required - set(item): errors.append(f"{item['id']}: missing required fields"); continue
        if item["risk_tier"] not in {"lite","standard","critical"} or item["external_state_behavior"] not in {"advisory","read_only","mutation_requires_hook"}: errors.append(f"{item['id']}: invalid enum")
        if len(item["supported_repositories"]) != len(set(item["supported_repositories"])): errors.append(f"{item['id']}: duplicate repositories")
        if not HASH.match(item["content_hash"]) or not COMMIT.match(item["source"].get("commit", "")): errors.append(f"{item['id']}: invalid hash or commit")
        path = root.parent / item["source"].get("path", "")
        if not path.is_dir(): errors.append(f"{item['id']}: missing source")
        elif canonical_hash(path) != item["content_hash"]: errors.append(f"{item['id']}: content hash drift")
    decision_rows = decisions.get("decisions", [])
    decision_ids = [row.get("id") for row in decision_rows if isinstance(row, dict)]
    if len(decision_ids) != len(set(decision_ids)) or set(ids) - set(decision_ids): errors.append("decision matrix must cover every catalog skill uniquely")
    for row in decision_rows:
        if set(row) != {"id","status","disposition","supersession"}: errors.append(f"{row.get('id','<unknown>')}: invalid decision fields"); continue
        if row["status"] not in {"active","on_demand"} or row["disposition"] not in {"keep","merge","deprecate"}: errors.append(f"{row['id']}: invalid decision enum")
        if row["disposition"] in {"merge","deprecate"} and not row["supersession"]: errors.append(f"{row['id']}: supersession required")
    active = surface.get("active_skill_ids", [])
    if not isinstance(active, list) or len(active) != len(set(active)) or not 10 <= len(active) <= 15: errors.append("active operating surface must contain 10-15 unique IDs")
    if set(active) - set(ids): errors.append("active surface selects unknown skill")
    status = {row["id"]: row["status"] for row in decision_rows if isinstance(row, dict) and "id" in row}
    if any(status.get(skill_id) != "active" for skill_id in active): errors.append("active surface includes non-active disposition")
    lanes = surface.get("lanes", {})
    if set(lanes) != {"lite","standard","critical"}: errors.append("lanes must be lite, standard, critical")
    lite, standard, critical = (lanes.get(name, {}) for name in ("lite","standard","critical"))
    if lite.get("owner") == "delivery-orchestrator-agent" or lite.get("boards_or_ledger") or lite.get("subagents_permitted") or lite.get("max_specialists") != 0: errors.append("lite lane invariant failed")
    if standard.get("owner") != "delivery-engineer-agent" or not standard.get("focused_qa_required") or standard.get("orchestrator_required") or standard.get("max_specialists") != 1: errors.append("standard lane invariant failed")
    if critical.get("owner") != "delivery-orchestrator-agent" or not all(critical.get(key) for key in ("orchestrator_required","ownership_gate","security_gate","qa_gate","human_protected_approval")) or critical.get("max_specialists") != 3 or critical.get("independent_evidence_states") != EVIDENCE: errors.append("critical lane invariant failed")
    for lane in lanes.values():
        if set(lane.get("selected_skill_ids", [])) - set(active): errors.append("lane has unknown or inactive selection")
    for variant in variants.get("variants", []):
        if variant.get("overlay_intent") not in {"intentional_overlay","unresolved_fork"} or len(set(variant.get("tree_hashes", {}).values())) < 2: errors.append("invalid observed variant")
    names = set()
    for scenario in scenarios.get("scenarios", []):
        if scenario.get("name") in names: errors.append("duplicate routing scenario")
        names.add(scenario.get("name"))
        lane = scenario.get("lane")
        if lane not in lanes or scenario.get("agents", 0) < 1 or scenario.get("specialists", -1) > lanes[lane].get("max_specialists", -1): errors.append(f"invalid routing scenario: {scenario.get('name')}")
        if scenario.get("protected_gates") != "human" or any(item not in EVIDENCE for item in scenario.get("evidence", [])): errors.append(f"unsafe routing scenario: {scenario.get('name')}")
    if names != {"narrow local fix","standard feature","cross-repo contract change","CI incident","production/IaC change"}: errors.append("routing scenario coverage incomplete")
    return errors

def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("root", type=Path); args = parser.parse_args()
    errors = validate(args.root)
    if errors: print("Catalog validation failed:\n" + "\n".join(f"- {item}" for item in errors)); return 1
    print("Catalog validation passed."); return 0
if __name__ == "__main__": raise SystemExit(main())
