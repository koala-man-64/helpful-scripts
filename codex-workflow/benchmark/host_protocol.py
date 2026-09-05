"""Pinned, optional full-schema validation for retained app-server payloads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


PINNED_VERSION = "0.153.4"
PINNED_SOURCE_SHA256 = "e8284c5cb8157554a3dd1e035aadbd4325aea501af56887e9c2e12eb1b9b9448"
PINNED_PROJECTION_SHA256 = "318ff66a5e42ec2cd285155d782928bc3722f5b78761425b83b64bacb3d37418"
SCHEMA_NAMES = frozenset(
    {
        "ThreadStartResponse",
        "ThreadStartParams",
        "ReviewStartResponse",
        "ReviewStartParams",
        "TurnStartedNotification",
        "TurnCompletedNotification",
        "ItemCompletedNotification",
    }
)
PROJECTION_PATH = (
    Path(__file__).with_name("protocol") / "host-events-0.153.4.schema.json"
)


def validate_host_payload(schema_name: str, value: object) -> None:
    """Validate one provider payload against the sealed Draft 7 projection.

    ``jsonschema`` is intentionally imported only here so standard catalog and CLI
    operations do not require the optional host-evidence dependency.
    """
    if schema_name not in SCHEMA_NAMES:
        raise ValueError(f"unsupported host payload schema: {schema_name!r}")
    validator_type = _draft7_validator()
    projection = _load_projection()
    selected_schema = {
        "$schema": projection["$schema"],
        "definitions": projection["definitions"],
        "$ref": f"#/definitions/v2/{schema_name}",
    }
    validator_type.check_schema(selected_schema)
    errors = sorted(
        validator_type(selected_schema).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise ValueError(f"{schema_name} payload is invalid at {location}: {error.message}")


def _draft7_validator() -> Any:
    try:
        from jsonschema import Draft7Validator
    except ImportError as error:
        raise ValueError(
            "optional host-evidence tooling requires jsonschema; install "
            "requirements-host-evidence.txt"
        ) from error
    return Draft7Validator


def _load_projection() -> dict[str, Any]:
    raw = PROJECTION_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PINNED_PROJECTION_SHA256:
        raise ValueError("host payload projection does not match its pinned digest")
    try:
        projection = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("host payload projection is invalid JSON") from error
    if not isinstance(projection, dict) or projection.get("$schema") != (
        "http://json-schema.org/draft-07/schema#"
    ):
        raise ValueError("host payload projection is not a Draft 7 schema")
    source = projection.get("x_host_evidence_source")
    if not isinstance(source, Mapping) or source.get("version") != PINNED_VERSION or (
        source.get("sha256") != PINNED_SOURCE_SHA256
    ):
        raise ValueError("host payload projection does not bind the provider source pin")
    if source.get("entrypoints") != sorted(SCHEMA_NAMES, key=_entrypoint_order):
        raise ValueError("host payload projection entrypoints differ from the provider pin")
    definitions = projection.get("definitions")
    if not isinstance(definitions, dict) or not isinstance(definitions.get("v2"), dict):
        raise ValueError("host payload projection has no v2 definitions namespace")
    for name in SCHEMA_NAMES:
        if name not in definitions["v2"]:
            raise ValueError(f"host payload projection is missing {name}")
    for reference in _references(projection):
        _resolve_local_pointer(projection, reference)
    return projection


def _entrypoint_order(name: str) -> int:
    return (
        "ThreadStartResponse",
        "ThreadStartParams",
        "ReviewStartResponse",
        "ReviewStartParams",
        "TurnStartedNotification",
        "TurnCompletedNotification",
        "ItemCompletedNotification",
    ).index(name)


def _references(value: object) -> Iterable[str]:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/"):
                raise ValueError("host payload projection contains a remote $ref")
            yield reference
        for nested in value.values():
            yield from _references(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _references(nested)


def _resolve_local_pointer(document: Mapping[str, Any], reference: str) -> None:
    node: object = document
    for part in reference[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, Mapping) or key not in node:
            raise ValueError(f"host payload projection has an unresolved $ref: {reference}")
        node = node[key]
