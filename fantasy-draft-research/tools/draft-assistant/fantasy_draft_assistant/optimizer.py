"""Deterministic and explainable candidate ordering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import itertools
import json
from typing import Any, Iterable

from .models import DraftState, LeagueConfig, PlayerSnapshot


COMPONENT_ORDERING = (
    "hard_exclusions",
    "tier",
    "league_fit",
    "scarcity",
    "wait_risk",
    "roster_utility",
    "risk",
    "projection",
    "adp",
    "player_id",
)


@dataclass(frozen=True, slots=True)
class Candidate:
    player: PlayerSnapshot
    ordering: tuple[Any, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player.player_id,
            "name": self.player.name,
            "nfl_team": self.player.nfl_team,
            "position": self.player.position,
            "ordering": dict(zip(COMPONENT_ORDERING[1:], self.ordering, strict=True)),
            "source": self.player.source,
            "source_family": self.player.source_family,
            "checked_at": self.player.checked_at.isoformat(),
        }


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"cannot serialize {type(value).__name__}")


def stable_hash(value: Any) -> str:
    serialized = json.dumps(value, default=_json_default, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def config_hash(config: LeagueConfig) -> str:
    return stable_hash(config.to_dict())


def board_hash(players: Iterable[PlayerSnapshot]) -> str:
    return stable_hash([player.to_dict() for player in sorted(players, key=lambda item: item.player_id)])


def team_for_pick(config: LeagueConfig, overall_pick: int) -> str:
    if not 1 <= overall_pick <= config.active_teams * config.rounds:
        raise ValueError("overall pick is outside the configured draft")
    round_number, offset = divmod(overall_pick - 1, config.active_teams)
    slots = config.draft_slots if round_number % 2 == 0 else tuple(reversed(config.draft_slots))
    return slots[offset]


def exclusion_reasons(
    player: PlayerSnapshot,
    config: LeagueConfig,
    state: DraftState,
    *,
    now: datetime | None = None,
) -> tuple[str, ...]:
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    if player.player_id in state.unavailable:
        reasons.append("unavailable")
    if any(keeper.player_id == player.player_id for keeper in config.keepers):
        reasons.append("keeper")
    if player.status not in {"available", "active"}:
        reasons.append(f"status:{player.status}")
    if player.ambiguous:
        reasons.append("ambiguous_identity")
    age_hours = (now - player.checked_at.astimezone(timezone.utc)).total_seconds() / 3600
    if age_hours < 0:
        reasons.append("future_timestamp")
    elif age_hours > config.mandatory_freshness_hours:
        reasons.append("stale_mandatory_input")
    return tuple(reasons)


def candidate_order(player: PlayerSnapshot) -> tuple[Any, ...]:
    return (
        player.tier,
        -player.league_fit,
        -player.scarcity,
        -player.wait_risk,
        -player.roster_utility,
        player.risk,
        -player.projection,
        player.adp,
        player.player_id,
    )


def rank_candidates(
    players: Iterable[PlayerSnapshot],
    config: LeagueConfig,
    state: DraftState,
    *,
    now: datetime | None = None,
) -> tuple[list[Candidate], dict[str, tuple[str, ...]]]:
    ranked: list[Candidate] = []
    excluded: dict[str, tuple[str, ...]] = {}
    for player in players:
        reasons = exclusion_reasons(player, config, state, now=now)
        if reasons:
            excluded[player.player_id] = reasons
            continue
        ranked.append(Candidate(player, candidate_order(player)))
    ranked.sort(key=lambda item: item.ordering)
    return ranked, excluded


def recommend_pair(
    candidates: list[Candidate],
    config: LeagueConfig,
    state: DraftState,
) -> tuple[Candidate, Candidate] | None:
    """Choose a paired turn deterministically, favoring close value plus position diversity."""
    roster_limit = sum(config.roster.values())
    current_roster_size = state.roster_count(config.our_team)
    if len(candidates) < 2 or roster_limit - current_roster_size < 2:
        return None
    pool = candidates[: min(12, len(candidates))]

    def pair_order(pair: tuple[Candidate, Candidate]) -> tuple[Any, ...]:
        first, second = pair
        position_penalty = int(first.player.position == second.player.position)
        return (
            max(first.player.tier, second.player.tier),
            first.player.tier + second.player.tier,
            position_penalty,
            first.ordering,
            second.ordering,
        )

    return min(itertools.combinations(pool, 2), key=pair_order)
