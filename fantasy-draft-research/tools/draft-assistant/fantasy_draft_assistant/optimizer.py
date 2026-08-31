"""Deterministic league-aware ranking, roster legality, and wait-risk logic."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence

from .compiler import derive_replacement_levels
from .models import (
    CompiledPlayer,
    DraftPlatform,
    DraftState,
    LeagueConfig,
    ObservedDraftState,
    PlayerSnapshot,
)


DraftPlayer = PlayerSnapshot | CompiledPlayer


COMPONENT_ORDERING = (
    "hard_exclusions",
    "vbd_band",
    "independent_tier",
    "wait_risk_band",
    "lineup_fit_band",
    "risk_band",
    "target_avoid_tiebreaker",
    "raw_vbd",
    "same_position_tier_drop",
    "active_platform_adp",
    "player_id",
)


@dataclass(frozen=True, slots=True)
class Candidate:
    player: DraftPlayer
    ordering: tuple[Any, ...]
    components: Mapping[str, Any]
    explanations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player.player_id,
            "name": self.player.name,
            "nfl_team": self.player.nfl_team,
            "position": self.player.position,
            "ordering": {name: self.components[name] for name in COMPONENT_ORDERING[1:]},
            "components": dict(self.components),
            "explanations": list(self.explanations),
            "evidence_refs": list(getattr(self.player, "evidence_refs", ())),
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


def board_hash(players: Iterable[DraftPlayer]) -> str:
    rows: list[dict[str, Any]] = []
    for player in sorted(players, key=lambda item: item.player_id):
        row = player.to_dict()
        row.pop("compiled_at", None)
        rows.append(row)
    return stable_hash(rows)


def team_for_pick(config: LeagueConfig, overall_pick: int) -> str:
    if not 1 <= overall_pick <= config.active_teams * config.rounds:
        raise ValueError("overall pick is outside the configured draft")
    round_number, offset = divmod(overall_pick - 1, config.active_teams)
    slots = config.draft_slots if round_number % 2 == 0 else tuple(reversed(config.draft_slots))
    return slots[offset]


def _tokens(position: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part for part in position.split("/") if part))


def _primary_position(player: DraftPlayer) -> str:
    return _tokens(player.position)[0]


def _required_slots(config: LeagueConfig) -> tuple[str, ...]:
    slots: list[str] = []
    for slot, count in sorted(config.roster.items()):
        if slot == "BENCH":
            continue
        slots.extend([slot] * count)
    return tuple(slots)


def _slot_accepts(slot: str, player: DraftPlayer, config: LeagueConfig) -> bool:
    positions = set(_tokens(player.position))
    if slot == "FLEX":
        return bool(positions.intersection(config.flex_positions))
    return bool(positions.intersection(_tokens(slot)))


def _maximum_required_matches(players: Sequence[DraftPlayer], config: LeagueConfig) -> int:
    slots = list(enumerate(_required_slots(config)))
    slots.sort(
        key=lambda item: (
            sum(_slot_accepts(item[1], player, config) for player in players),
            item[1] == "FLEX",
            item[1],
            item[0],
        )
    )
    matched_player_for_slot: dict[int, int] = {}

    def assign(player_index: int, visited: set[int]) -> bool:
        player = players[player_index]
        for slot_index, slot in slots:
            if slot_index in visited or not _slot_accepts(slot, player, config):
                continue
            visited.add(slot_index)
            previous = matched_player_for_slot.get(slot_index)
            if previous is None or assign(previous, visited):
                matched_player_for_slot[slot_index] = player_index
                return True
        return False

    player_order = sorted(
        range(len(players)),
        key=lambda index: (
            sum(_slot_accepts(slot, players[index], config) for _, slot in slots),
            players[index].player_id,
        ),
    )
    for player_index in player_order:
        assign(player_index, set())
    return len(matched_player_for_slot)


def required_unfilled_slots(players: Sequence[DraftPlayer], config: LeagueConfig) -> int:
    return len(_required_slots(config)) - _maximum_required_matches(players, config)


def remaining_selectable_picks(config: LeagueConfig, state: DraftState) -> int:
    keeper_picks = {
        keeper.overall_pick
        for keeper in config.keepers
        if keeper.team == config.our_team and keeper.overall_pick >= state.current_pick
    }
    return sum(
        1
        for overall in range(state.current_pick, config.active_teams * config.rounds + 1)
        if team_for_pick(config, overall) == config.our_team and overall not in keeper_picks
    )


def _committed_roster(
    players_by_id: Mapping[str, DraftPlayer],
    config: LeagueConfig,
    state: DraftState,
) -> list[DraftPlayer]:
    ids = list(state.rosters.get(config.our_team, ()))
    ids.extend(keeper.player_id for keeper in config.keepers if keeper.team == config.our_team)
    result: list[DraftPlayer] = []
    seen: set[str] = set()
    for player_id in ids:
        if player_id not in seen and player_id in players_by_id:
            seen.add(player_id)
            result.append(players_by_id[player_id])
    return result


def roster_completion_possible(
    candidate: DraftPlayer,
    players: Sequence[DraftPlayer],
    config: LeagueConfig,
    state: DraftState,
    *,
    now: datetime | None = None,
) -> bool:
    players_by_id = {item.player_id: item for item in players}
    roster = _committed_roster(players_by_id, config, state)
    if len(roster) >= sum(config.roster.values()):
        return False
    roster.append(candidate)
    remaining_picks = max(0, remaining_selectable_picks(config, state) - 1)
    required_after_pick = required_unfilled_slots(roster, config)
    if required_after_pick > remaining_picks:
        return False
    if required_after_pick != remaining_picks:
        return True
    timestamp = now or datetime.now(timezone.utc)
    future_pool = [
        player
        for player in players
        if player.player_id != candidate.player_id
        and not _base_exclusion_reasons(player, config, state, now=timestamp)
    ]
    return required_unfilled_slots([*roster, *future_pool], config) == 0


def _status_band(player: DraftPlayer) -> int:
    if isinstance(player, CompiledPlayer):
        return player.status_band
    status = player.status.casefold()
    if status in {"active", "available"}:
        return 0
    if status in {"questionable", "unknown"}:
        return 1
    if status in {"doubtful", "pup", "nfi"}:
        return 2
    return 3


def _risk_band(player: DraftPlayer) -> int:
    if isinstance(player, CompiledPlayer):
        return max(player.risk_band, player.status_band)
    return max(0, min(3, int(round(player.risk))))


def _preference(player: DraftPlayer) -> int:
    return player.personal_preference if isinstance(player, CompiledPlayer) else 0


def _active_adp(player: DraftPlayer, platform: DraftPlatform) -> float:
    return player.active_adp(platform) if isinstance(player, CompiledPlayer) else player.adp


def _base_exclusion_reasons(
    player: DraftPlayer,
    config: LeagueConfig,
    state: DraftState,
    *,
    now: datetime,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if player.player_id in state.unavailable:
        reasons.append("unavailable")
    if any(keeper.player_id == player.player_id for keeper in config.keepers):
        reasons.append("keeper")
    if _status_band(player) >= 3:
        reasons.append(f"status:{player.status}")
    if _risk_band(player) >= 3:
        reasons.append("risk:exclude")
    if player.ambiguous:
        reasons.append("ambiguous_identity")
    age_hours = (now - player.checked_at.astimezone(timezone.utc)).total_seconds() / 3600
    if age_hours < 0:
        reasons.append("future_timestamp")
    elif age_hours > config.mandatory_freshness_hours:
        reasons.append("stale_mandatory_input")
    return tuple(dict.fromkeys(reasons))


def _next_manager_pick(config: LeagueConfig, state: DraftState) -> int | None:
    keeper_picks = {keeper.overall_pick for keeper in config.keepers if keeper.team == config.our_team}
    for overall in range(state.current_pick + 1, config.active_teams * config.rounds + 1):
        if team_for_pick(config, overall) == config.our_team and overall not in keeper_picks:
            return overall
    return None


def wait_risk_band(
    player: DraftPlayer,
    candidates: Sequence[DraftPlayer],
    config: LeagueConfig,
    state: DraftState,
    *,
    platform: DraftPlatform,
    observed: ObservedDraftState | None = None,
    now: datetime | None = None,
) -> int:
    """Classify no-return risk from ADP, supply, turn distance, and demand."""

    next_pick = _next_manager_pick(config, state)
    if next_pick is None:
        return 0
    position = _primary_position(player)
    tier = player.independent_tier if isinstance(player, CompiledPlayer) else player.tier
    timestamp = now or datetime.now(timezone.utc)
    same_tier_supply = sum(
        1
        for item in candidates
        if _primary_position(item) == position
        and (item.independent_tier if isinstance(item, CompiledPlayer) else item.tier) == tier
        and item.player_id != player.player_id
        and not _base_exclusion_reasons(item, config, state, now=timestamp)
    )
    observed_demand = int(observed.positional_demand.get(position, 0)) if observed else 0
    adp = _active_adp(player, platform)
    distance = next_pick - state.current_pick
    legacy_pressure = player.wait_risk if isinstance(player, PlayerSnapshot) else 0.0
    if (
        (math.isfinite(adp) and adp <= next_pick)
        or same_tier_supply <= observed_demand
        or legacy_pressure >= 2
    ):
        return 0
    if (
        (math.isfinite(adp) and adp <= next_pick + distance)
        or same_tier_supply <= observed_demand + 2
        or legacy_pressure >= 1
    ):
        return 1
    return 2


def _lineup_fit_band(
    candidate: DraftPlayer,
    players: Sequence[DraftPlayer],
    config: LeagueConfig,
    state: DraftState,
) -> int:
    roster = _committed_roster({item.player_id: item for item in players}, config, state)
    before = required_unfilled_slots(roster, config)
    after = required_unfilled_slots([*roster, candidate], config)
    if after < before:
        return 0
    return 1 if before == 0 else 2


def _tier_drops(players: Sequence[DraftPlayer], vbd: Mapping[str, float]) -> dict[str, float]:
    result: dict[str, float] = {}
    by_position: dict[str, list[DraftPlayer]] = {}
    for player in players:
        by_position.setdefault(_primary_position(player), []).append(player)
    for values in by_position.values():
        values.sort(
            key=lambda item: (
                item.independent_tier if isinstance(item, CompiledPlayer) else item.tier,
                -vbd[item.player_id],
                item.player_id,
            )
        )
        for player in values:
            if isinstance(player, CompiledPlayer):
                result[player.player_id] = player.same_position_tier_drop
                continue
            tier = player.tier
            next_tier = next((item for item in values if item.tier > tier), None)
            next_value = vbd[next_tier.player_id] if next_tier else 0.0
            result[player.player_id] = max(0.0, vbd[player.player_id] - next_value)
    return result


def exclusion_reasons(
    player: DraftPlayer,
    config: LeagueConfig,
    state: DraftState,
    *,
    now: datetime | None = None,
    players: Sequence[DraftPlayer] | None = None,
) -> tuple[str, ...]:
    now = now or datetime.now(timezone.utc)
    reasons = list(_base_exclusion_reasons(player, config, state, now=now))
    if players is not None and not roster_completion_possible(
        player, players, config, state, now=now
    ):
        roster_size = len(_committed_roster({item.player_id: item for item in players}, config, state))
        reasons.append("roster_full" if roster_size >= sum(config.roster.values()) else "roster_completion_impossible")
    return tuple(dict.fromkeys(reasons))


def candidate_order(player: DraftPlayer) -> tuple[Any, ...]:
    """Compatibility helper for callers with an already-compiled player."""

    if isinstance(player, CompiledPlayer):
        return (
            -player.vbd_band,
            player.independent_tier,
            1,
            1,
            player.risk_band,
            player.personal_preference,
            -player.vbd,
            -player.same_position_tier_drop,
            player.adp,
            player.player_id,
        )
    return (
        -math.floor(player.projection / 10.0),
        player.tier,
        max(0, 2 - int(round(player.wait_risk))),
        1,
        _risk_band(player),
        0,
        -player.projection,
        -player.scarcity,
        player.adp,
        player.player_id,
    )


def rank_candidates(
    players: Iterable[DraftPlayer],
    config: LeagueConfig,
    state: DraftState,
    *,
    now: datetime | None = None,
    observed: ObservedDraftState | None = None,
    active_platform: DraftPlatform | str | None = None,
) -> tuple[list[Candidate], dict[str, tuple[str, ...]]]:
    player_list = list(players)
    if not player_list:
        return [], {}
    timestamp = now or datetime.now(timezone.utc)
    if active_platform is None:
        platform = observed.platform if observed else (state.platform or DraftPlatform.YAHOO)
    else:
        platform = active_platform
    platform = platform if isinstance(platform, DraftPlatform) else DraftPlatform(platform)

    baseline_pool = [
        player
        for player in player_list
        if not _base_exclusion_reasons(player, config, state, now=timestamp)
    ]
    baselines = derive_replacement_levels(baseline_pool, config) if baseline_pool else {}
    vbd = {
        player.player_id: (
            player.vbd if isinstance(player, CompiledPlayer)
            else player.projection - baselines.get(_primary_position(player), 0.0)
        )
        for player in player_list
    }
    drops = _tier_drops(baseline_pool, vbd)
    ranked: list[Candidate] = []
    excluded: dict[str, tuple[str, ...]] = {}
    for player in player_list:
        reasons = exclusion_reasons(player, config, state, now=timestamp, players=player_list)
        if reasons:
            excluded[player.player_id] = reasons
            continue
        raw_vbd = vbd[player.player_id]
        vbd_band = math.floor(raw_vbd / 10.0)
        tier = player.independent_tier if isinstance(player, CompiledPlayer) else player.tier
        wait_band = wait_risk_band(
            player,
            player_list,
            config,
            state,
            platform=platform,
            observed=observed,
            now=timestamp,
        )
        fit_band = _lineup_fit_band(player, player_list, config, state)
        risk_band = _risk_band(player)
        preference = _preference(player)
        tier_drop = drops.get(player.player_id, 0.0)
        adp = _active_adp(player, platform)
        ordering = (
            -vbd_band,
            tier,
            wait_band,
            fit_band,
            risk_band,
            preference,
            -raw_vbd,
            -tier_drop,
            adp,
            player.player_id,
        )
        components = {
            "hard_exclusions": (),
            "vbd_band": vbd_band,
            "independent_tier": tier,
            "wait_risk_band": wait_band,
            "lineup_fit_band": fit_band,
            "risk_band": risk_band,
            "target_avoid_tiebreaker": preference,
            "raw_vbd": round(raw_vbd, 6),
            "same_position_tier_drop": round(tier_drop, 6),
            "active_platform_adp": adp if math.isfinite(adp) else None,
            "player_id": player.player_id,
        }
        explanations = (
            f"VBD {raw_vbd:.1f} is in the {vbd_band * 10}-point band",
            f"independent tier {tier}",
            f"wait-risk band {wait_band} from next-pick distance, {platform.value} ADP, tier supply, and demand",
            f"lineup-fit band {fit_band}",
            f"risk band {risk_band}; close-call preference {preference}",
            f"same-position tier drop {tier_drop:.1f}",
        )
        ranked.append(Candidate(player, ordering, components, explanations))
    ranked.sort(key=lambda item: item.ordering)
    return ranked, excluded


def _state_after_pick(state: DraftState, config: LeagueConfig, player_id: str) -> DraftState:
    rosters = dict(state.rosters)
    rosters[config.our_team] = (*rosters.get(config.our_team, ()), player_id)
    next_pick = min(state.current_pick + 1, config.active_teams * config.rounds)
    return replace(
        state,
        current_pick=next_pick,
        current_team=team_for_pick(config, next_pick),
        rosters=rosters,
        unavailable=frozenset({*state.unavailable, player_id}),
    )


def recommend_pair(
    candidates: list[Candidate],
    config: LeagueConfig,
    state: DraftState,
    *,
    now: datetime | None = None,
    observed: ObservedDraftState | None = None,
    all_players: Sequence[DraftPlayer] | None = None,
) -> tuple[Candidate, Candidate] | None:
    """Simulate the first pick, then rerank the second against that roster."""

    if len(candidates) < 2 or remaining_selectable_picks(config, state) < 2:
        return None
    first = candidates[0]
    simulated = _state_after_pick(state, config, first.player.player_id)
    pool = all_players or [item.player for item in candidates]
    # Keep the simulated first player in the context pool so roster matching can
    # resolve its position; the unavailable set excludes it as a candidate.
    reranked, _ = rank_candidates(pool, config, simulated, now=now, observed=observed)
    return (first, reranked[0]) if reranked else None


def pair_fallback_branches(
    candidates: list[Candidate],
    config: LeagueConfig,
    state: DraftState,
    *,
    branch_count: int = 3,
    now: datetime | None = None,
    observed: ObservedDraftState | None = None,
    all_players: Sequence[DraftPlayer] | None = None,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for first in candidates[:branch_count]:
        simulated = _state_after_pick(state, config, first.player.player_id)
        pool = all_players or [item.player for item in candidates]
        reranked, _ = rank_candidates(pool, config, simulated, now=now, observed=observed)
        if reranked:
            result[first.player.player_id] = reranked[0].player.player_id
    return result
