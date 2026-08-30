"""Fail-closed player identity normalization."""

from __future__ import annotations

import re
import unicodedata


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}


def normalize_name(value: str) -> str:
    """Return a comparison key without weakening exact position/team checks."""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_value.casefold())
    if words and words[-1] in _SUFFIXES:
        words.pop()
    if not words:
        raise ValueError("player name must contain letters or digits")
    return " ".join(words)


def normalize_team(value: str) -> str:
    team = re.sub(r"[^A-Za-z]", "", value).upper()
    if not 2 <= len(team) <= 4:
        raise ValueError(f"invalid NFL team code: {value!r}")
    return team


def normalize_position(value: str) -> str:
    raw = value.strip().upper()
    if raw in {"DST", "D/ST"}:
        return "DEF"
    parts = [part for part in re.split(r"[/,|\s]+", raw) if part]
    if not parts or any(part not in _POSITIONS for part in parts):
        raise ValueError(f"unsupported exact position: {value!r}")
    return "/".join(dict.fromkeys(parts))


def identity_key(name: str, nfl_team: str, position: str) -> tuple[str, str, str]:
    return normalize_name(name), normalize_team(nfl_team), normalize_position(position)
