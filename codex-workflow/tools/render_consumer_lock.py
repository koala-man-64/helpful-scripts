"""Render an advisory consumer lock; never install configuration."""

from __future__ import annotations

import argparse
from pathlib import Path

from catalog_lib import load_document, write_json
from validate_catalog import validate


def render(root: Path, repository: str, lane: str) -> dict[str, object]:
    errors = validate(root)
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
    pins = surface["lanes"][lane]["available_skill_pins"]
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
    return {
        "schema_version": "consumer-lock-v2",
        "repository": repository,
        "lane": lane,
        "available_skill_pins": available,
        "task_participants": surface["lanes"][lane]["task_participants"],
        "unresolved_forks": [
            {"id": x["skill_id"], "runnable": False, "blocking": True}
            for x in variants["variants"]
        ],
        "central_denials": decisions["central_denials"],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--repository", required=True)
    p.add_argument(
        "--lane", choices=("lite", "standard", "critical"), default="standard"
    )
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    write_json(a.output, render(a.root, a.repository, a.lane))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
