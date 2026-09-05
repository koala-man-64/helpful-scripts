"""Render disabled candidate locks without installing configuration."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from typing import Any

from catalog_lib import canonical_hash, load_document, write_json
from render_consumer_lock import render
from validate_catalog import REPOSITORIES, parse_repository_mappings, validate

SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
LANES = ("lite", "standard", "critical")
CURATED_REPOSITORY_PATHS = (
    "codex-workflow/tools/catalog_lib.py",
    "codex-workflow/schemas/consumer-lock-v2.schema.json",
    "codex-workflow/tools/render_candidate_bundle.py",
    "codex-workflow/tools/render_consumer_lock.py",
    "codex-workflow/tools/validate_catalog.py",
    "codex-workflow/tools/validate_consumer_candidate.py",
    "codex-workflow/tools/context_selection.py",
    "codex-workflow/tools/output_projection.py",
    "codex-workflow/benchmark/manifest.py",
    "codex-workflow/benchmark/harness.py",
    "codex-workflow/benchmark/artifacts.py",
    "codex-workflow/benchmark/app_server_capture.py",
    "codex-workflow/benchmark/pricing.py",
    "codex-workflow/benchmark/fixtures.py",
    "codex-workflow/benchmark/runner.py",
    "codex-workflow/benchmark/validators.py",
    "codex-workflow/benchmark/semantic_evidence.py",
    "codex-workflow/benchmark/semantic_git.py",
    "codex-workflow/benchmark/semantic_local.py",
    "codex-workflow/benchmark/semantic_research.py",
    "codex-workflow/benchmark/semantic_validation.py",
    "codex-workflow/benchmark/evidence-receipt-v1.schema.json",
    "codex-token-usage-audit/codex_token_usage_audit.py",
    "codex-token-usage-audit/codex_equivalent_pricing.py",
)


def sha256_file(path: Path) -> str:
    """Return the content digest used for emitted lock files."""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_digests(root: Path) -> dict[str, str]:
    """Return the canonical catalog and disabled-candidate package digests."""
    return {
        "catalog_digest": canonical_hash(root / "catalog"),
        "bundle_digest": candidate_source_digest(root),
    }


def semantic_validator_pins(root: Path) -> dict[str, str]:
    """Bind the exact semantic source bytes using the evaluator's digest recipe."""
    digest = hashlib.sha256()
    for name in ("semantic_evidence.py", "semantic_git.py", "semantic_local.py",
                 "semantic_research.py", "semantic_validation.py"):
        raw = (root / "benchmark" / name).read_bytes()
        digest.update(name.encode() + len(raw).to_bytes(8, "big") + raw)
    return {
        "semantic-validators": "sha256:" + digest.hexdigest(),
        "validator:deterministic-semantic-v1": sha256_file(root / "benchmark/semantic_validation.py"),
    }


def candidate_source_digest(root: Path) -> str:
    """Hash the disabled package and its fixed curated implementation inputs."""
    candidate_root = root / "candidates"
    candidate_paths = sorted(
        path
        for path in candidate_root.rglob("*")
        if path.is_file() and "outputs" not in path.relative_to(candidate_root).parts
        and "__pycache__" not in path.relative_to(candidate_root).parts
    )
    paths = [(path, f"candidates/{path.relative_to(candidate_root).as_posix()}") for path in candidate_paths]
    for relative in CURATED_REPOSITORY_PATHS:
        path = root.parent / relative
        if not path.is_file():
            raise ValueError(f"curated candidate tooling is absent: {relative}")
        paths.append((path, relative))
    for path in sorted((root / "benchmark" / "task_inputs").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            paths.append((path, path.relative_to(root.parent).as_posix()))
    digest = hashlib.sha256()
    for path, relative_name in sorted(paths, key=lambda item: item[1]):
        relative = relative_name.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def render_candidate_bundle(
    root: Path,
    output: Path,
    repository_roots: dict[str, Path],
    release_digest: str,
    tested_commit: str,
    replace_generated: bool = False,
) -> dict[str, Any]:
    """Render all five-repository, three-lane disabled candidate locks."""
    if not SHA256.fullmatch(release_digest):
        raise ValueError("release_digest must be a sha256 digest")
    observation = load_document(root / "candidates" / "central-policy-observation.json")
    if observation.get("release_digest") != release_digest:
        raise ValueError("release digest differs from the pinned central policy observation")
    if not COMMIT.fullmatch(tested_commit):
        raise ValueError("tested_commit must be an exact 40-character commit SHA")
    if set(repository_roots) != REPOSITORIES:
        raise ValueError("candidate rendering requires explicit mappings for all repositories")
    errors = validate(root, repository_roots, strict_origin=True)
    if errors:
        raise ValueError("catalog is invalid: " + "; ".join(errors))
    expected_outputs = {
        Path("candidate-bundle.json"),
        *(Path("locks") / repository / f"{lane}.json" for repository in REPOSITORIES for lane in LANES),
    }
    if output.exists() and any(output.iterdir()):
        existing = {path.relative_to(output) for path in output.rglob("*") if path.is_file()}
        if not replace_generated or existing - expected_outputs:
            raise ValueError("output directory must be empty or contain only replaceable generated files")
    output.mkdir(parents=True, exist_ok=True)

    inventory = load_document(root / "catalog" / "origin-inventory.yaml")
    origins = {
        item["id"]: item["origin_sha"] for item in inventory["repositories"]
    }
    digests = candidate_digests(root)
    bindings = {**digests, "release_digest": release_digest}
    repositories: list[dict[str, Any]] = []
    for repository in sorted(REPOSITORIES):
        locks: dict[str, dict[str, str]] = {}
        for lane in LANES:
            relative = Path("locks") / repository / f"{lane}.json"
            destination = output / relative
            if not destination.resolve().is_relative_to(output.resolve()):
                raise ValueError("candidate output escapes the requested output directory")
            write_json(
                destination,
                render(
                    root, repository, lane, repository_roots,
                    strict_origin=True, validate_catalog=False,
                ),
            )
            locks[lane] = {
                "path": relative.as_posix(),
                "sha256": sha256_file(destination),
                **bindings,
            }
        repositories.append({"repository": repository, "locks": locks})
    bundle = {
        "schema_version": "candidate-bundle-v1",
        "package": "codex-workflow",
        "installation": "disabled_not_installed",
        "activation": False,
        "readiness": False,
        "evidence": {
            "benchmark": {"status": "missing"},
            "run": {"status": "missing"},
            "acceptance": {"status": "missing"},
        },
        "canonical_origins": origins,
        "tested_commit": tested_commit,
        "tested_commit_scope": "helpful-scripts-catalog-base",
        "preflight_receipts": [],
        "readiness_receipts": [],
        "validator_pins": semantic_validator_pins(root),
        **bindings,
        "repositories": repositories,
    }
    validate_candidate_bundle(root, output, bundle)
    write_json(output / "candidate-bundle.json", bundle)
    return bundle


def validate_candidate_bundle(root: Path, output: Path, value: Any) -> None:
    """Fail closed unless every candidate lane lock has immutable bindings."""
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "package", "installation", "activation", "readiness",
        "evidence", "canonical_origins", "tested_commit", "tested_commit_scope", "preflight_receipts",
        "readiness_receipts", "catalog_digest", "bundle_digest", "release_digest",
        "repositories", "validator_pins",
    }:
        raise ValueError("candidate bundle fields are invalid")
    if (
        value["schema_version"] != "candidate-bundle-v1"
        or value["package"] != "codex-workflow"
        or value["installation"] != "disabled_not_installed"
        or value["activation"] is not False
        or value["readiness"] is not False
        or not COMMIT.fullmatch(value["tested_commit"])
        or value["tested_commit_scope"] != "helpful-scripts-catalog-base"
    ):
        raise ValueError("candidate bundle activation contract is invalid")
    if not all(SHA256.fullmatch(value[name]) for name in (
        "catalog_digest", "bundle_digest", "release_digest"
    )):
        raise ValueError("candidate bundle digests are invalid")
    if value["catalog_digest"] != canonical_hash(root / "catalog") or value[
        "bundle_digest"
    ] != candidate_source_digest(root):
        raise ValueError("candidate bundle source digests do not match")
    if value["validator_pins"] != semantic_validator_pins(root):
        raise ValueError("candidate semantic validator pins do not match source")
    observation = load_document(root / "candidates" / "central-policy-observation.json")
    if observation.get("release_digest") != value["release_digest"]:
        raise ValueError("release digest differs from the pinned central policy observation")
    if value["evidence"] != {
        "benchmark": {"status": "missing"},
        "run": {"status": "missing"},
        "acceptance": {"status": "missing"},
    } or value["preflight_receipts"] != [] or value["readiness_receipts"] != []:
        raise ValueError("candidate bundle cannot self-attest readiness")
    if not isinstance(value["canonical_origins"], dict) or set(value["canonical_origins"]) != REPOSITORIES:
        raise ValueError("candidate bundle origin coverage is invalid")
    if not isinstance(value["repositories"], list) or len(value["repositories"]) != len(REPOSITORIES):
        raise ValueError("candidate bundle repository coverage is invalid")
    expected_bindings = {
        name: value[name] for name in ("catalog_digest", "bundle_digest", "release_digest")
    }
    listed: set[str] = set()
    for repository in value["repositories"]:
        if not isinstance(repository, dict) or set(repository) != {"repository", "locks"}:
            raise ValueError("candidate repository entry is invalid")
        name, locks = repository["repository"], repository["locks"]
        listed.add(name)
        if name not in REPOSITORIES or not isinstance(locks, dict) or set(locks) != set(LANES):
            raise ValueError("candidate lane coverage is invalid")
        for lane, lock in locks.items():
            if not isinstance(lock, dict) or set(lock) != {"path", "sha256", *expected_bindings}:
                raise ValueError("candidate lock receipt is invalid")
            if lock.get("path") != f"locks/{name}/{lane}.json" or not SHA256.fullmatch(lock.get("sha256", "")):
                raise ValueError("candidate lock path or digest is invalid")
            if any(lock[key] != digest for key, digest in expected_bindings.items()):
                raise ValueError("candidate lock binding digest is invalid")
            if sha256_file(output / lock["path"]) != lock["sha256"]:
                raise ValueError("candidate lock content digest does not match")
    if listed != REPOSITORIES:
        raise ValueError("candidate bundle repository entries are duplicated or missing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-digest", required=True)
    parser.add_argument("--tested-commit", required=True)
    parser.add_argument("--replace-generated", action="store_true")
    parser.add_argument("--repo", action="append", default=[], metavar="ID=PATH")
    args = parser.parse_args()
    try:
        roots = parse_repository_mappings(args.repo, require_complete=True)
        render_candidate_bundle(
            args.root, args.output, roots, args.release_digest, args.tested_commit,
            args.replace_generated,
        )
    except ValueError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
