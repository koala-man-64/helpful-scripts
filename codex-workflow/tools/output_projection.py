"""Opt-in projections for owned process call sites; raw evidence stays expandable.

The targets are character-based estimates, never measured model tokens. Critical
records may exceed the soft target rather than hiding a failure or causal chain.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

FAILURE = re.compile(
    r"\b(?:fail(?:ed|ure|ures)?|fatal|critical|error|exception|traceback|"
    r"caused by|direct cause|during handling|assert(?:ion)?error)\b", re.I
)
SEVERITY = re.compile(r"\b(TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\b", re.I)
STAMP = re.compile(r"\b\d{4}-\d\d-\d\d[T ][\d:.]+(?:Z|[+-]\d\d:\d\d)?")
CODE = re.compile(r"\b(?:[A-Z]{1,8}\d{3,8}|[A-Z][A-Za-z]*(?:Error|Exception))\b")
LOCATION = re.compile(r'(?:File "[^"\r\n]{1,240}", line \d+|(?<!\S)[^\s]{1,240}:\d+(?::\d+)?)')


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(0) if match else None


def _metadata(value: Any, limit: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    if not isinstance(value, str):
        return None, True
    return value[:limit], len(value) > limit


def _record(raw: bytes, path: Path, offset: int, number: int, chain: bool) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
    structured: dict[str, Any] = {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            structured = parsed
    except (ValueError, RecursionError):
        pass
    message = str(structured.get("message", text))
    critical = bool(FAILURE.search(text)) or chain or str(structured.get("severity", "")).lower() in {
        "error", "fatal", "critical"
    }
    # Search the whole record before clipping: a 153k-character line may hide a
    # failure in the middle. Capture diagnostic windows as well as its prefix.
    windows = []
    for match in FAILURE.finditer(text):
        start, end = max(0, match.start() - 60), min(len(text), match.end() + 160)
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], end)
        else:
            windows.append((start, end))
    metadata = {}
    clipped = []
    for key, pattern, limit in (("timestamp", STAMP, 64), ("severity", SEVERITY, 20), ("code", CODE, 96), ("location", LOCATION, 240)):
        value, shortened = _metadata(structured.get(key, _match(pattern, text)), limit)
        metadata[key] = value
        if shortened:
            clipped.append(key)
    return {
        **metadata,
        "message_prefix": message[:240],
        "metadata_clipped_or_non_scalar": clipped,
        "hash": sha256(raw),
        "raw_record": {"path": str(path.resolve()), "line": number, "byte_offset": offset, "byte_length": len(raw)},
        "failure_or_cause": critical,
        "diagnostic_windows": [text[start:end] for start, end in windows],
        "characters": len(text),
        "bytes": len(raw),
        "truncated": len(message) > 240,
        "decode_replacement": text.encode("utf-8") != raw.rstrip(b"\r\n"),
    }


def project_file(path: Path, *, exit_status: int, target_tokens: int | None = None) -> dict[str, Any]:
    """Scan every raw record; retain all detected failures and chained tracebacks.

    A record reference is byte-addressable, with a content hash checked by
    expand_record. No raw output is modified, deleted, or treated as instructions.
    """
    if isinstance(exit_status, bool) or not isinstance(exit_status, int):
        raise ValueError("exit_status must be the observed integer process status")
    target = target_tokens if target_tokens is not None else (4000 if exit_status else 2000)
    if isinstance(target, bool) or not isinstance(target, int) or target < 1:
        raise ValueError("target_tokens must be a positive integer soft estimate")
    records = []
    offset = 0
    chain = False
    observed_digest = hashlib.sha256()
    before = path.stat()
    with path.open("rb") as source:
        for number, raw in enumerate(source, 1):
            observed_digest.update(raw)
            line = raw.decode("utf-8", errors="replace")
            if "Traceback (most recent call last)" in line or re.search(r"\b[A-Za-z.]+(?:Error|Exception):", line):
                chain = True
            record = _record(raw, path, offset, number, chain)
            records.append(record)
            offset += len(raw)
            # An empty line ends an individual traceback, while Python's
            # subsequent causal marker and next traceback are independently kept.
            if chain and not line.strip():
                chain = False
    after = path.stat()
    source_changed = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or offset != after.st_size
    required = {index for index, row in enumerate(records) if row["failure_or_cause"]}
    # Nonzero status is authoritative even when the log uses no recognized words.
    if records:
        required.add(0)
        required.add(len(records) - 1)
    selected = set(required)
    used = sum(len(json.dumps(records[index], ensure_ascii=False)) for index in selected)
    for index, row in enumerate(records):
        size = len(json.dumps(row, ensure_ascii=False))
        if index not in selected and used + size <= target * 4:
            selected.add(index)
            used += size
    projection = {
        "schema_version": "owned-output-projection/v1",
        "exit_status": exit_status,
        "process_succeeded": exit_status == 0,
        "raw_file": {"path": str(path.resolve()), "hash": "sha256:" + observed_digest.hexdigest(), "hash_basis": "bytes observed during scan", "bytes": offset, "source_changed_during_scan": source_changed},
        "records": [records[index] for index in sorted(selected)],
        "record_count": len(records),
        "omitted_records": len(records) - len(selected),
        "failure_records": sum(row["failure_or_cause"] for row in records),
        "budget": {"target_tokens_estimate": target, "basis": "serialized Unicode characters / 4; not measured tokens", "soft": True},
        "expansion": "Use expand_record(reference) to read a complete hash-verified record; the raw file contains every omitted record.",
    }
    projection["budget"]["measured_boundary"] = "json.dumps(projection, ensure_ascii=False), including these metrics"
    # Self-inclusive size converges as soon as the decimal digit lengths settle.
    for _ in range(10):
        rendered = json.dumps(projection, ensure_ascii=False)
        metrics = {"projected_characters": len(rendered), "projected_utf8_bytes": len(rendered.encode("utf-8")), "tokens_estimate": (len(rendered) + 3) // 4, "target_exceeded": len(rendered) > target * 4}
        if all(projection["budget"].get(key) == value for key, value in metrics.items()):
            break
        projection["budget"].update(metrics)
    return projection


def expand_record(record: dict[str, Any]) -> bytes:
    reference = record["raw_record"]
    with Path(reference["path"]).open("rb") as source:
        source.seek(reference["byte_offset"])
        raw = source.read(reference["byte_length"])
    if sha256(raw) != record["hash"]:
        raise ValueError("raw record changed; re-project current evidence")
    return raw


def run_process(command: list[str], *, cwd: Path, raw_dir: Path,
                failure_target_tokens: int = 4000, routine_target_tokens: int = 2000,
                input_bytes: bytes | None = None) -> dict[str, Any]:
    """Run an explicitly supplied argv, never a shell; preserve status and bytes."""
    if not command or not all(isinstance(arg, str) for arg in command):
        raise ValueError("command must be a nonempty argv list")
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / (uuid.uuid4().hex + ".log")
    with path.open("xb") as output:
        result = subprocess.run(command, cwd=cwd, input=input_bytes, stdout=output, stderr=subprocess.STDOUT, check=False)
    return project_file(path, exit_status=result.returncode, target_tokens=failure_target_tokens if result.returncode else routine_target_tokens)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("an explicit command argv is required after --")
    projection = run_process(command, cwd=args.cwd, raw_dir=args.raw_dir)
    print(json.dumps(projection, ensure_ascii=False))
    return projection["exit_status"]


if __name__ == "__main__":
    raise SystemExit(main())
