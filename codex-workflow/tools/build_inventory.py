"""Create a deterministic inventory of concrete skills below a source directory."""

from __future__ import annotations

import argparse
from pathlib import Path

from catalog_lib import canonical_hash, write_json


def build_inventory(source: Path) -> dict[str, object]:
    skills = []
    ids = set()
    for entrypoint in sorted(source.rglob("SKILL.md")):
        folder = entrypoint.parent
        if folder.name in ids:
            raise ValueError(f"inventory ID collision: {folder.name}")
        ids.add(folder.name)
        skills.append({"id": folder.name, "source_path": folder.as_posix(), "content_hash": canonical_hash(folder)})
    return {"inventory_version": 1, "source": source.as_posix(), "skills": skills}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_json(args.output, build_inventory(args.source))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
