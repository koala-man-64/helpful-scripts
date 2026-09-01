"""Dependency-free helpers for the JSON-compatible YAML workflow catalog."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

SUPPORTED_SCHEMA_KEYS = {
    "$schema",
    "title",
    "type",
    "const",
    "enum",
    "allOf",
    "additionalProperties",
    "required",
    "properties",
    "items",
    "contains",
    "minContains",
    "maxContains",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minLength",
    "pattern",
    "minimum",
}


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


def _schema_definition_errors(schema: Any, path: str = "$schema") -> list[str]:
    if not isinstance(schema, dict):
        return [f"{path}: schema must be an object"]
    errors = [
        f"{path}: unsupported schema keyword {key}"
        for key in schema
        if key not in SUPPORTED_SCHEMA_KEYS
    ]
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            errors.append(f"{path}.properties: must be an object")
        else:
            for key, child in properties.items():
                errors.extend(
                    _schema_definition_errors(child, f"{path}.properties.{key}")
                )
    if "items" in schema:
        errors.extend(_schema_definition_errors(schema["items"], f"{path}.items"))
    if "contains" in schema:
        errors.extend(_schema_definition_errors(schema["contains"], f"{path}.contains"))
    all_of = schema.get("allOf")
    if all_of is not None:
        if not isinstance(all_of, list) or not all_of:
            errors.append(f"{path}.allOf: must be a non-empty array")
        else:
            for index, child in enumerate(all_of):
                errors.extend(
                    _schema_definition_errors(child, f"{path}.allOf[{index}]")
                )
    for key in ("minContains", "maxContains"):
        if key in schema and (
            not isinstance(schema[key], int)
            or isinstance(schema[key], bool)
            or schema[key] < 0
        ):
            errors.append(f"{path}.{key}: must be a non-negative integer")
    return errors


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    path: str = "$",
    *,
    _definition_checked: bool = False,
) -> list[str]:
    """Small fail-closed JSON-schema subset used by the checked-in catalog."""
    errors = [] if _definition_checked else _schema_definition_errors(schema)
    if not isinstance(schema, dict):
        return errors or [f"{path}: schema must be an object"]
    for child in schema.get("allOf", []):
        errors.extend(validate_schema(value, child, path, _definition_checked=True))
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: invalid enum")
    expected = schema.get("type")
    types = expected if isinstance(expected, list) else [expected]
    type_ok = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "null": value is None,
    }
    if expected and not any(type_ok.get(item, False) for item in types):
        return [f"{path}: wrong type"]
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing {key}")
        if schema.get("additionalProperties") is False:
            errors.extend(
                f"{path}: unknown field {key}" for key in value if key not in properties
            )
        for key, child in properties.items():
            if key in value and isinstance(child, dict):
                errors.extend(
                    validate_schema(
                        value[key], child, f"{path}.{key}", _definition_checked=True
                    )
                )
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get(
            "maxItems", len(value)
        ):
            errors.append(f"{path}: invalid item count")
        if schema.get("uniqueItems") and len(
            {json.dumps(item, sort_keys=True) for item in value}
        ) != len(value):
            errors.append(f"{path}: duplicate item")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(
                    validate_schema(
                        item,
                        item_schema,
                        f"{path}[{index}]",
                        _definition_checked=True,
                    )
                )
        contains = schema.get("contains")
        if isinstance(contains, dict):
            matches = sum(
                not validate_schema(
                    item,
                    contains,
                    f"{path}[{index}]",
                    _definition_checked=True,
                )
                for index, item in enumerate(value)
            )
            minimum = schema.get("minContains", 1)
            maximum = schema.get("maxContains", len(value))
            if (
                isinstance(minimum, int)
                and isinstance(maximum, int)
                and not (minimum <= matches <= maximum)
            ):
                errors.append(f"{path}: invalid contains count")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: too short")
        if schema.get("pattern") and not re.fullmatch(schema["pattern"], value):
            errors.append(f"{path}: pattern mismatch")
    return errors
