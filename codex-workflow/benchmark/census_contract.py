"""Offline validation for the pinned benchmark-host-census-v1 contract copy."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


SOURCE_COMMIT = "fde0edd62aff25fa466eebaefe2becaf1ca19423"
BUILD_ID = 21298
MANIFEST_SHA256 = "d0b9c38639d7b2dc630b063f7c9c1a375e9e5a081b2aa0fdd5eb2d082a264789"
PAYLOAD_SHA256 = "98e6c920cac1a0b826ded12115ccc7ca9c778a9418e9c4566202b91f2ed0f337"
SCHEMA_SHA256 = "28854a8f019646b3cd51294c5b634a0fdddc375cd354c7b71b72edf009334302"
PUBLICATION_SHA256 = "596f896e5839bc2afffd9b5c95c129ce06edf60fce3bf33340d74d64ae0c6b91"
CONTRACT_ROOT = Path(__file__).with_name("contracts") / "benchmark-host-census-v1"
_UTC_DATE_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)


def strict_json(raw: bytes) -> Any:
    """Decode one contract JSON document without permissive JSON extensions."""
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_nonfinite,
            parse_float=_finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("contract JSON is malformed") from error


def validate_census(value: object) -> None:
    """Validate a record against all locally retained publication and schema pins."""
    schema = _load_retained_schema()
    validator_type, format_checker = _draft202012_validator()
    validator_type.check_schema(schema)
    errors = sorted(
        validator_type(schema, format_checker=format_checker).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise ValueError(f"census payload is invalid at {location}: {error.message}")


def _draft202012_validator() -> tuple[Any, Any]:
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as error:
        raise ValueError(
            "optional census-evidence tooling requires jsonschema; install "
            "requirements-host-evidence.txt"
        ) from error
    checker = FormatChecker()

    @checker.checks("date-time")
    def strict_utc_date_time(value: object) -> bool:
        if not isinstance(value, str) or not _UTC_DATE_TIME.fullmatch(value):
            return False
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)

    return Draft202012Validator, checker


def _load_retained_schema() -> dict[str, Any]:
    files = _retained_files(CONTRACT_ROOT)
    manifest_raw = files.get("manifest.json")
    publication_raw = files.get("publication.json")
    if manifest_raw is None or publication_raw is None:
        raise ValueError("retained census contract is missing manifest or publication")
    if _digest(manifest_raw) != MANIFEST_SHA256 or _digest(publication_raw) != PUBLICATION_SHA256:
        raise ValueError("retained census contract pin digest differs")
    manifest, publication = strict_json(manifest_raw), strict_json(publication_raw)
    if not isinstance(manifest, dict) or not isinstance(publication, dict):
        raise ValueError("retained census manifest and publication must be objects")
    if (
        manifest.get("schema_version") != "benchmark-host-census-v1"
        or manifest.get("payload_digest") != f"sha256:{PAYLOAD_SHA256}"
        or publication.get("source_commit") != SOURCE_COMMIT
        or publication.get("build_id") != BUILD_ID
        or publication.get("manifest_digest") != f"sha256:{MANIFEST_SHA256}"
        or publication.get("payload_digest") != f"sha256:{PAYLOAD_SHA256}"
    ):
        raise ValueError("retained census publication source, build, or pin differs")
    inventory = _inventory(manifest)
    expected = {"manifest.json", "publication.json", *inventory}
    if set(files) != expected:
        raise ValueError("retained census contract has missing or unexpected files")
    payload = json.dumps(manifest["files"], indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if _digest(payload) != PAYLOAD_SHA256:
        raise ValueError("retained census manifest payload digest differs")
    for path, entry in inventory.items():
        raw = files[path]
        if len(raw) != entry["byte_length"] or _digest(raw) != entry["digest"].removeprefix("sha256:"):
            raise ValueError(f"retained census inventory does not seal {path}")
    schema_raw = files.get("benchmark-host-census-v1.schema.json")
    if schema_raw is None or _digest(schema_raw) != SCHEMA_SHA256:
        raise ValueError("retained census schema digest differs")
    schema = strict_json(schema_raw)
    if not isinstance(schema, dict):
        raise ValueError("retained census schema must be an object")
    _assert_local_refs(schema)
    return schema


def _retained_files(root: Path) -> dict[str, bytes]:
    _assert_safe_root(root)
    if not root.is_dir():
        raise ValueError("retained census contract root is unavailable or unsafe")
    files: dict[str, bytes] = {}

    def walk(directory: Path, prefix: PurePosixPath = PurePosixPath()) -> None:
        for child in directory.iterdir():
            relative = prefix / child.name
            if _is_reparse(child):
                raise ValueError(f"retained census contract contains a reparse point: {relative}")
            if child.is_dir():
                walk(child, relative)
            elif child.is_file():
                files[str(relative)] = child.read_bytes()
            else:
                raise ValueError(f"retained census contract has an unsafe entry: {relative}")

    walk(root)
    return files


def _assert_safe_root(root: Path) -> None:
    absolute = root.absolute()
    for ancestor in (absolute, *absolute.parents):
        if _is_reparse(ancestor):
            raise ValueError("retained census contract root is unavailable or unsafe")
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise ValueError("retained census contract root is unavailable or unsafe") from error
    if os.path.normcase(str(absolute)) != os.path.normcase(str(resolved)):
        raise ValueError("retained census contract root resolves outside its retained path")


def _is_reparse(path: Path) -> bool:
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _inventory(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("retained census manifest has no file inventory")
    inventory: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"byte_length", "digest", "path"}:
            raise ValueError("retained census manifest entry has an invalid shape")
        path = _safe_path(entry["path"])
        if path in inventory:
            raise ValueError(f"retained census manifest duplicates {path}")
        if (
            isinstance(entry["byte_length"], bool)
            or not isinstance(entry["byte_length"], int)
            or entry["byte_length"] < 0
            or not isinstance(entry["digest"], str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", entry["digest"])
        ):
            raise ValueError("retained census manifest entry has invalid fields")
        inventory[path] = entry
    return inventory


def _safe_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("retained census inventory path is invalid")
    if "\\" in value or ":" in value or any(
        part in {"", ".", ".."} for part in value.split("/")
    ):
        raise ValueError(f"retained census inventory has unsafe path {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError(f"retained census inventory has unsafe path {value!r}")
    return value


def _assert_local_refs(value: object) -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if reference is not None and (
            not isinstance(reference, str) or not reference.startswith("#")
        ):
            raise ValueError("retained census schema contains a remote reference")
        for nested in value.values():
            _assert_local_refs(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_local_refs(nested)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite JSON value: {value}")
    return number


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
