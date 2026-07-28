"""Bounded redacted runtime diagnostics."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .utils import redact_text


MAX_LOG_BYTES = 1_000_000
BACKUP_COUNT = 4


def write_log(data_dir: Path, event: str, **fields: Any) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "runtime.jsonl"
    try:
        _rotate(path)
        safe: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": redact_text(event),
        }
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, (bool, int, float)):
                safe[key] = value
            else:
                safe[key] = redact_text(str(value))
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(safe, sort_keys=True, ensure_ascii=True) + "\n")
    except OSError:
        return


def _rotate(path: Path) -> None:
    if not path.exists() or path.stat().st_size < MAX_LOG_BYTES:
        return
    oldest = path.with_suffix(path.suffix + f".{BACKUP_COUNT}")
    if oldest.exists():
        oldest.unlink()
    for index in range(BACKUP_COUNT - 1, 0, -1):
        source = path.with_suffix(path.suffix + f".{index}")
        if source.exists():
            os.replace(source, path.with_suffix(path.suffix + f".{index + 1}"))
    os.replace(path, path.with_suffix(path.suffix + ".1"))
