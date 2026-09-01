"""Dependency-free helpers for the JSON-compatible YAML workflow catalog."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def load_document(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} must be JSON-compatible YAML: {error}") from error


def canonical_hash(path: Path) -> str:
    if not path.is_dir():
        raise ValueError(f"source directory does not exist: {path}")
    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    for item in files:
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = item.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


def catalog_root(path: Path) -> Path:
    return path if (path / "catalog").is_dir() else path.parent


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
