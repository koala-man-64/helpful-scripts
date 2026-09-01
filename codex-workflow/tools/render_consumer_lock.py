"""Render a repository-specific lock without changing global configuration."""

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
    active = set(load_document(root / "catalog" / "active-surface.yaml")["active_skill_ids"])
    statuses = {row["id"]: row["status"] for row in decisions["decisions"]}
    available = {entry["id"]: entry for entry in manifest["skills"] if repository in entry["supported_repositories"] and entry["id"] in active and statuses[entry["id"]] == "active"}
    if not available:
        raise ValueError(f"unknown repository: {repository}")
    selected = []
    for skill_id in surface["lanes"][lane]["selected_skill_ids"]:
        entry = available.get(skill_id)
        if entry is None: raise ValueError(f"{lane} requires unavailable active skill: {skill_id}")
        selected.append({key: entry[key] for key in ("id", "source", "content_hash", "version")})
    return {"schema_version": "consumer-lock-v1", "repository": repository, "lane": lane, "skills": selected, "overlays": [], "central_denials": decisions["central_denials"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--lane", choices=("lite", "standard", "critical"), default="standard")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_json(args.output, render(args.root, args.repository, args.lane))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
