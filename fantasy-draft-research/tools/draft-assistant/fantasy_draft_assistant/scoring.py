"""Deterministic fantasy scoring presets and projection scoring.

The compiler works with canonical stat names.  Source-specific importers are
responsible for translating their fields before calling :func:`score_stats`.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Mapping


_BASE_RULES: dict[str, float] = {
    "passing_yards": 0.04,
    "passing_touchdowns": 4.0,
    "passing_interceptions": -2.0,
    "rushing_yards": 0.1,
    "rushing_touchdowns": 6.0,
    "receiving_yards": 0.1,
    "receiving_touchdowns": 6.0,
    "receptions": 0.0,
    "two_point_conversions": 2.0,
    "fumbles_lost": -2.0,
    "extra_points_made": 1.0,
    "field_goals_0_39": 3.0,
    "field_goals_40_49": 4.0,
    "field_goals_50_plus": 5.0,
    "defense_sacks": 1.0,
    "defense_interceptions": 2.0,
    "defense_fumbles_recovered": 2.0,
    "defense_safeties": 2.0,
    "defense_blocked_kicks": 2.0,
    "defense_touchdowns": 6.0,
    "return_touchdowns": 6.0,
}


SCORING_PRESETS: dict[str, dict[str, float]] = {
    "standard": dict(_BASE_RULES),
    "half-ppr": {**_BASE_RULES, "receptions": 0.5},
    "ppr": {**_BASE_RULES, "receptions": 1.0},
}


_FORMAT_ALIASES = {
    "standard": "standard",
    "non-ppr": "standard",
    "non_ppr": "standard",
    "zero-ppr": "standard",
    "0-ppr": "standard",
    "half": "half-ppr",
    "half-ppr": "half-ppr",
    "half_ppr": "half-ppr",
    "0.5-ppr": "half-ppr",
    "ppr": "ppr",
    "full-ppr": "ppr",
    "full_ppr": "ppr",
}


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def normalize_scoring_format(value: str) -> str:
    """Return the canonical v1 preset name."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("scoring_format must be a non-empty string")
    normalized = value.strip().casefold().replace(" ", "-")
    try:
        return _FORMAT_ALIASES[normalized]
    except KeyError as error:
        supported = ", ".join(SCORING_PRESETS)
        raise ValueError(f"unsupported scoring format {value!r}; expected one of {supported}") from error


def scoring_rules(
    scoring_format: str,
    overrides: Mapping[str, int | float] | None = None,
) -> dict[str, float]:
    """Resolve a preset plus explicit, finite per-stat overrides."""

    resolved = dict(SCORING_PRESETS[normalize_scoring_format(scoring_format)])
    for raw_name, raw_value in (overrides or {}).items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("scoring override names must be non-empty strings")
        resolved[raw_name.strip()] = _finite(raw_value, f"scoring_overrides.{raw_name}")
    return resolved


def scoring_context(
    scoring_format: str,
    overrides: Mapping[str, int | float] | None = None,
) -> str:
    """Return a stable scoring-context identifier suitable for a manifest."""

    normalized = normalize_scoring_format(scoring_format)
    rules = scoring_rules(normalized, overrides)
    serialized = json.dumps(rules, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{normalized}:sha256:{digest}"


def scoring_context_matches(
    supplied_context: str | None,
    scoring_format: str,
    overrides: Mapping[str, int | float] | None = None,
) -> bool:
    """Return whether pre-scored points are compatible with the league.

    A bare preset name is accepted only when the league has no overrides.  A
    league with overrides requires the exact hash-bound context.
    """

    if supplied_context is None:
        return False
    supplied = supplied_context.strip().casefold()
    exact = scoring_context(scoring_format, overrides).casefold()
    if supplied == exact:
        return True
    if overrides:
        return False
    try:
        return normalize_scoring_format(supplied) == normalize_scoring_format(scoring_format)
    except ValueError:
        return False


def score_stats(
    projected_stats: Mapping[str, int | float],
    rules: Mapping[str, int | float],
) -> float:
    """Score canonical raw projections with deterministic arithmetic."""

    unknown = sorted(set(projected_stats).difference(rules))
    if unknown:
        raise ValueError("unsupported projected stats: " + ", ".join(unknown))
    if not projected_stats:
        raise ValueError("projected_stats must contain at least one recognized scoring stat")

    total = 0.0
    for stat_name in sorted(projected_stats):
        stat_value = _finite(projected_stats[stat_name], f"projected_stats.{stat_name}")
        multiplier = _finite(rules[stat_name], f"scoring_rules.{stat_name}")
        total += stat_value * multiplier
    return round(total, 6)


def score_projection(
    projected_stats: Mapping[str, int | float],
    scoring_format: str,
    overrides: Mapping[str, int | float] | None = None,
) -> float:
    return score_stats(projected_stats, scoring_rules(scoring_format, overrides))
