"""Create the sealed provider-schema projection used by host-evidence tooling.

The input is the Codex app-server 0.153.4 protocol export with SHA-256
e8284c5cb8157554a3dd1e035aadbd4325aea501af56887e9c2e12eb1b9b9448.  The
projection keeps the host request and event entrypoints and every transitive local
``$ref`` target, preserving their original ``definitions/v2`` namespace.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any, Iterable, Mapping


PINNED_VERSION = "0.153.4"
PINNED_SOURCE_SHA256 = "e8284c5cb8157554a3dd1e035aadbd4325aea501af56887e9c2e12eb1b9b9448"
ENTRYPOINTS = (
    "ThreadStartResponse",
    "ThreadStartParams",
    "ReviewStartResponse",
    "ReviewStartParams",
    "TurnStartedNotification",
    "TurnCompletedNotification",
    "ItemCompletedNotification",
)


def _decode_pointer(pointer: str) -> list[str]:
    if not pointer.startswith("#/"):
        raise ValueError(f"provider schema must use local JSON pointers, got {pointer!r}")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[2:].split("/")]


def _at_pointer(document: Mapping[str, Any], pointer: str) -> Any:
    node: Any = document
    for part in _decode_pointer(pointer):
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"provider schema reference does not resolve: {pointer}")
        node = node[part]
    return node


def _put_pointer(document: dict[str, Any], pointer: str, value: Any) -> None:
    parts = _decode_pointer(pointer)
    node = document
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            raise ValueError(f"cannot preserve provider schema namespace for {pointer}")
    node[parts[-1]] = copy.deepcopy(value)


def _references(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/"):
                raise ValueError("provider projection rejects non-local $ref values")
            yield reference
        for nested in value.values():
            yield from _references(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _references(nested)


def extract_projection(source: Path) -> dict[str, Any]:
    """Load one pinned export and retain only the entrypoint reference closure."""
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PINNED_SOURCE_SHA256:
        raise ValueError("provider schema source does not match the 0.153.4 pin")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("provider schema source is invalid JSON") from error
    if not isinstance(document, dict) or document.get("$schema") != (
        "http://json-schema.org/draft-07/schema#"
    ):
        raise ValueError("provider schema source is not a Draft 7 document")

    projection: dict[str, Any] = {
        "$schema": document["$schema"],
        "title": "Codex app-server 0.153.4 host payload projection",
        "x_host_evidence_source": {
            "version": PINNED_VERSION,
            "sha256": PINNED_SOURCE_SHA256,
            "entrypoints": list(ENTRYPOINTS),
            "recipe": "transitive local $ref closure; no validation keywords removed",
        },
        "definitions": {},
    }
    pending = deque(f"#/definitions/v2/{name}" for name in ENTRYPOINTS)
    retained: set[str] = set()
    while pending:
        pointer = pending.popleft()
        if pointer in retained:
            continue
        value = _at_pointer(document, pointer)
        _put_pointer(projection, pointer, value)
        retained.add(pointer)
        pending.extend(reference for reference in _references(value) if reference not in retained)
    return projection


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    projection = extract_projection(arguments.source)
    encoded = json.dumps(projection, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(encoded, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
