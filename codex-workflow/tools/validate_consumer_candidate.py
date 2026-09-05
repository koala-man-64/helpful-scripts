"""Read-only validation of one consumer's three advisory lane locks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from catalog_lib import load_document
from render_candidate_bundle import LANES, sha256_file, validate_candidate_bundle
from render_consumer_lock import render
from validate_catalog import REPOSITORIES


def validate_consumer(root: Path, bundle_dir: Path, repository: str, locks_dir: Path) -> dict:
    """Verify source/release bindings and exact shared-renderer output, without installing."""
    if repository not in REPOSITORIES:
        raise ValueError(f"unknown consumer repository: {repository}")
    bundle = load_document(bundle_dir / "candidate-bundle.json")
    validate_candidate_bundle(root, bundle_dir, bundle)
    entry = next(item for item in bundle["repositories"] if item["repository"] == repository)
    verified = {}
    for lane in LANES:
        path = locks_dir / f"{lane}.json"
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError(f"consumer lock must be a regular file: {lane}")
        actual = load_document(path)
        # render() runs the shared schema and exact execution-plan validators.
        # Catalog provenance is already bound by the verified candidate source;
        # consumer owners independently run their repository-specific checks.
        expected = render(root, repository, lane, validate_catalog=False)
        if actual != expected or sha256_file(path) != entry["locks"][lane]["sha256"]:
            raise ValueError(f"consumer lock differs from pinned {lane} candidate")
        verified[lane] = {"path": str(path.resolve()), "digest": sha256_file(path)}
    return {
        "schema_version": "consumer-candidate-validation-v1",
        "repository": repository,
        "catalog_digest": bundle["catalog_digest"],
        "bundle_digest": bundle["bundle_digest"],
        "release_digest": bundle["release_digest"],
        "locks": verified,
        "shared_validation": "passed",
        "repository_validation": "not_observed",
        "runtime_consumption": "unverified",
        "readiness": False,
        "activation": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--repository", choices=sorted(REPOSITORIES), required=True)
    parser.add_argument("--locks", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = validate_consumer(args.root, args.bundle, args.repository, args.locks)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
