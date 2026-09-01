"""Render an advisory consumer lock; never install configuration."""

from __future__ import annotations

import argparse
from pathlib import Path

from catalog_lib import load_document, validate_schema, write_json
from validate_catalog import validate


def render(
    root: Path,
    repository: str,
    lane: str,
    repository_roots: dict[str, Path] | None = None,
    strict_origin: bool = False,
) -> dict[str, object]:
    errors = validate(root, repository_roots, strict_origin)
    if errors:
        raise ValueError("catalog is invalid: " + "; ".join(errors))
    manifest = load_document(root / "catalog" / "skills.yaml")
    surface = load_document(root / "catalog" / "active-surface.yaml")
    decisions = load_document(root / "catalog" / "skill-decisions.yaml")
    variants = load_document(root / "catalog" / "observed-variants.yaml")
    if repository not in {
        "asset-allocation-contracts",
        "asset-allocation-control-plane",
        "asset-allocation-jobs",
        "asset-allocation-runtime-common",
        "asset-allocation-ui",
    }:
        raise ValueError(f"unknown repository: {repository}")
    if lane not in surface["lanes"]:
        raise ValueError(f"unknown lane: {lane}")
    statuses = {x["id"]: x["status"] for x in decisions["decisions"]}
    entries = {x["id"]: x for x in manifest["skills"]}
    pins = surface["active_skill_ids"]
    available = []
    for skill_id in pins:
        entry = entries.get(skill_id)
        if (
            entry is None
            or not entry["runnable"]
            or entry["canonical_state"] != "verified_nondivergent"
            or statuses.get(skill_id) != "active"
        ):
            raise ValueError(f"{lane} requires unavailable canonical skill: {skill_id}")
        available.append(
            {
                key: entry[key]
                for key in (
                    "id",
                    "source",
                    "content_hash",
                    "content_hash_algorithm",
                    "version",
                )
            }
        )
    lock = {
        "schema_version": "consumer-lock-v2",
        "repository": repository,
        "lane": lane,
        "active_surface_skill_pins": available,
        "lane_required_skill_ids": surface["lanes"][lane]["lane_required_skill_ids"],
        "task_participant_skill_ids": surface["lanes"][lane][
            "task_participant_skill_ids"
        ],
        "unresolved_forks": [
            {"id": x["skill_id"], "runnable": False, "selection_blocked": True}
            for x in variants["variants"]
        ],
        "central_denials": decisions["central_denials"],
        "record_authority": surface["record_authority"],
        "selected_routing_policy": surface["lanes"][lane]["primary_route"],
        "lane_execution_plan": _lane_execution_plan(surface["lanes"][lane]),
    }
    schema = load_document(root / "schemas" / "consumer-lock-v2.schema.json")
    schema_errors = validate_schema(lock, schema, "consumer-lock")
    if schema_errors:
        raise ValueError("rendered lock violates schema: " + "; ".join(schema_errors))
    _validate_execution_plan(lock["lane_execution_plan"], lane)
    return lock


def _lane_execution_plan(lane: dict[str, object]) -> dict[str, object]:
    """Render the executable roster separately from available catalog pins."""
    route = lane["primary_route"]
    name = route["model"].lower()
    children, minimum, maximum, orchestrator, gates = [], 0, 0, False, {}
    if name == "terra":
        children = [
            {"model": "Luna", "effort": "low", "role": "focused_qa", "required": True},
            {
                "model": "Luna",
                "effort": "low",
                "role": "necessary_specialist",
                "required": False,
            },
        ]
        minimum, maximum = 1, 2
    elif name == "sol":
        children = [
            {
                "model": "Terra",
                "effort": "medium",
                "role": "bounded_specialist",
                "required": True,
            }
        ]
        minimum, maximum, orchestrator, gates = 1, 3, True, lane["gate_owners"]
    return {
        "owner": route,
        "children": children,
        "minimum_children": minimum,
        "maximum_children": maximum,
        "orchestrator": orchestrator,
        "gate_owners": gates,
    }


def _validate_execution_plan(plan: dict[str, object], lane: str) -> None:
    owner, children = plan["owner"], plan["children"]
    expected = {
        "lite": ("Luna", "low", 0, 0, False),
        "standard": ("Terra", "medium", 1, 2, False),
        "critical": ("Sol", "high", 1, 3, True),
    }[lane]
    if (
        owner["model"],
        owner["effort"],
        plan["minimum_children"],
        plan["maximum_children"],
        plan["orchestrator"],
    ) != expected:
        raise ValueError(f"invalid {lane} execution plan")
    if lane == "lite" and (children or plan["gate_owners"]):
        raise ValueError("lite execution plan cannot contain children or gates")
    if lane == "standard" and (
        children
        != [
            {"model": "Luna", "effort": "low", "role": "focused_qa", "required": True},
            {
                "model": "Luna",
                "effort": "low",
                "role": "necessary_specialist",
                "required": False,
            },
        ]
        or plan["gate_owners"]
    ):
        raise ValueError(
            "standard execution plan requires focused QA then optional specialist"
        )
    if lane == "critical" and (
        children
        != [
            {
                "model": "Terra",
                "effort": "medium",
                "role": "bounded_specialist",
                "required": True,
            }
        ]
        or plan["gate_owners"]
        != {
            "ownership": {"role": "orchestrator_owner", "human_protected": False},
            "security": {"role": "security_specialist", "human_protected": True},
            "qa": {"role": "qa_specialist", "human_protected": False},
        }
    ):
        raise ValueError(
            "critical execution plan requires Terra specialists and all gates"
        )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--repository", required=True)
    p.add_argument(
        "--lane", choices=("lite", "standard", "critical"), default="standard"
    )
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--repo", action="append", default=[], metavar="ID=PATH")
    p.add_argument("--strict-origin", action="store_true")
    a = p.parse_args()
    mappings = {}
    for value in a.repo:
        if "=" not in value:
            p.error("--repo must be ID=PATH")
        name, path = value.split("=", 1)
        mappings[name] = Path(path)
    write_json(
        a.output,
        render(a.root, a.repository, a.lane, mappings or None, a.strict_origin),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
