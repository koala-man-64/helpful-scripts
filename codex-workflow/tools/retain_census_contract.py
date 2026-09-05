"""Verify and retain the approved benchmark-host-census-v1 CI artifact offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


ARCHIVE_SHA256 = "00afd4cd9924f097cf4addfccd2d058b6711dcb2bb8ecd147075e5bbd5909e8c"
PREFIX = "benchmark-host-census-v1/"
SOURCE_COMMIT = "fde0edd62aff25fa466eebaefe2becaf1ca19423"
BUILD_ID = 21298
MANIFEST_SHA256 = "d0b9c38639d7b2dc630b063f7c9c1a375e9e5a081b2aa0fdd5eb2d082a264789"
PAYLOAD_SHA256 = "98e6c920cac1a0b826ded12115ccc7ca9c778a9418e9c4566202b91f2ed0f337"
SCHEMA_SHA256 = "28854a8f019646b3cd51294c5b634a0fdddc375cd354c7b71b72edf009334302"
PUBLICATION_SHA256 = "596f896e5839bc2afffd9b5c95c129ce06edf60fce3bf33340d74d64ae0c6b91"


def strict_json(raw: bytes) -> Any:
    """Decode UTF-8 JSON while rejecting duplicate keys and non-finite values."""
    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_nonfinite
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("contract JSON is malformed") from error


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("contract inventory path must be a non-empty string")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or "\\" in value
        or ":" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"unsafe contract inventory path: {value!r}")
    return value


def _manifest_files(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("contract manifest must contain a non-empty files array")
    indexed: dict[str, dict[str, Any]] = {}
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"byte_length", "digest", "path"}:
            raise ValueError("contract manifest file entry has an invalid shape")
        path = _safe_relative_path(entry["path"])
        if path in indexed:
            raise ValueError(f"duplicate contract inventory path: {path}")
        if (
            isinstance(entry["byte_length"], bool)
            or not isinstance(entry["byte_length"], int)
            or entry["byte_length"] < 0
            or not isinstance(entry["digest"], str)
            or not entry["digest"].startswith("sha256:")
            or len(entry["digest"]) != 71
        ):
            raise ValueError("contract manifest file entry has invalid byte or digest fields")
        indexed[path] = entry
    return indexed


def _validate_bundle(entries: Mapping[str, bytes]) -> dict[str, dict[str, Any]]:
    manifest_raw = entries.get("manifest.json")
    publication_raw = entries.get("publication.json")
    if manifest_raw is None or publication_raw is None:
        raise ValueError("contract archive is missing manifest or publication data")
    if _digest(manifest_raw) != MANIFEST_SHA256 or _digest(publication_raw) != PUBLICATION_SHA256:
        raise ValueError("contract archive pin digest differs from the approved publication")
    manifest = strict_json(manifest_raw)
    publication = strict_json(publication_raw)
    if not isinstance(manifest, dict) or not isinstance(publication, dict):
        raise ValueError("contract manifest and publication must be objects")
    if (
        manifest.get("schema_version") != "benchmark-host-census-v1"
        or manifest.get("payload_digest") != f"sha256:{PAYLOAD_SHA256}"
        or publication.get("source_commit") != SOURCE_COMMIT
        or publication.get("build_id") != BUILD_ID
        or publication.get("manifest_digest") != f"sha256:{MANIFEST_SHA256}"
        or publication.get("payload_digest") != f"sha256:{PAYLOAD_SHA256}"
    ):
        raise ValueError("contract publication source, build, or manifest pin differs")
    files = _manifest_files(manifest)
    expected = {"manifest.json", "publication.json", *files}
    if set(entries) != expected:
        raise ValueError("contract archive contains missing or unexpected files")
    payload = json.dumps(manifest["files"], indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if _digest(payload) != PAYLOAD_SHA256:
        raise ValueError("contract manifest payload digest differs from the approved pin")
    for path, entry in files.items():
        raw = entries[path]
        if len(raw) != entry["byte_length"] or _digest(raw) != entry["digest"].removeprefix("sha256:"):
            raise ValueError(f"contract inventory does not seal {path}")
    schema = entries.get("benchmark-host-census-v1.schema.json")
    if schema is None or _digest(schema) != SCHEMA_SHA256:
        raise ValueError("contract schema digest differs from the approved pin")
    return files


def retain_contract(archive: Path, output: Path) -> None:
    """Verify exactly one approved ZIP then retain its root payload atomically."""
    if _digest(archive.read_bytes()) != ARCHIVE_SHA256:
        raise ValueError("contract ZIP does not match the approved artifact digest")
    if output.exists():
        raise ValueError("contract output already exists; refusing to merge retained bytes")
    with zipfile.ZipFile(archive) as bundle:
        contents: dict[str, bytes] = {}
        for info in bundle.infolist():
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("contract ZIP contains a directory or symlink entry")
            if not info.filename.startswith(PREFIX):
                raise ValueError("contract ZIP contains an unexpected prefix")
            relative = _safe_relative_path(info.filename.removeprefix(PREFIX))
            if relative in contents:
                raise ValueError(f"contract ZIP contains a duplicate entry: {relative}")
            contents[relative] = bundle.read(info)
    _validate_bundle(contents)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        staged = Path(temporary) / output.name
        for relative, raw in contents.items():
            destination = staged / PurePosixPath(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        os.replace(staged, output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    retain_contract(arguments.archive, arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
