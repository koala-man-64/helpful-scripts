"""Validated offline imports for league and player snapshots."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .identity import identity_key, normalize_position, normalize_team
from .models import LeagueConfig, PlayerSnapshot


PLAYER_COLUMNS = {
    "player_id",
    "name",
    "nfl_team",
    "position",
    "projection",
    "tier",
    "adp",
    "league_fit",
    "scarcity",
    "wait_risk",
    "roster_utility",
    "risk",
    "source",
    "source_family",
    "checked_at",
    "status",
    "ambiguous",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid checked_at timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("checked_at must include a timezone")
    parsed = parsed.astimezone(timezone.utc)
    if parsed > datetime.now(timezone.utc):
        raise ValueError(f"checked_at cannot be in the future: {value!r}")
    return parsed


def load_league(path: Path) -> LeagueConfig:
    return LeagueConfig.from_dict(_read_json(path))


def load_players(path: Path) -> list[PlayerSnapshot]:
    try:
        stream = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise ValueError(f"cannot read player CSV {path}: {exc}") from exc

    players: list[PlayerSnapshot] = []
    ids: set[str] = set()
    identities: set[tuple[str, str, str]] = set()
    with stream:
        reader = csv.DictReader(stream)
        missing = PLAYER_COLUMNS.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"player CSV missing columns: {', '.join(sorted(missing))}")
        for line, row in enumerate(reader, start=2):
            try:
                player_id = row["player_id"].strip()
                if not player_id or player_id in ids:
                    raise ValueError(f"duplicate or empty player_id: {player_id!r}")
                key = identity_key(row["name"], row["nfl_team"], row["position"])
                if key in identities:
                    raise ValueError(f"duplicate normalized identity: {key}")
                checked_at = parse_timestamp(row["checked_at"])
                player = PlayerSnapshot(
                    player_id=player_id,
                    name=row["name"].strip(),
                    nfl_team=normalize_team(row["nfl_team"]),
                    position=normalize_position(row["position"]),
                    projection=float(row["projection"]),
                    tier=int(row["tier"]),
                    adp=float(row["adp"]),
                    league_fit=float(row["league_fit"]),
                    scarcity=float(row["scarcity"]),
                    wait_risk=float(row["wait_risk"]),
                    roster_utility=float(row["roster_utility"]),
                    risk=float(row["risk"]),
                    source=row["source"].strip(),
                    source_family=row["source_family"].strip(),
                    checked_at=checked_at,
                    status=row["status"].strip().lower(),
                    ambiguous=row["ambiguous"].strip().lower() in {"1", "true", "yes"},
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid player CSV line {line}: {exc}") from exc
            ids.add(player_id)
            identities.add(key)
            players.append(player)

    if not players:
        raise ValueError("player CSV must contain at least one player")
    return players
