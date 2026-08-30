"""Recommendation envelope construction."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .models import DraftState, LeagueConfig, PlayerSnapshot, RecommendationEnvelope
from .optimizer import (
    COMPONENT_ORDERING,
    board_hash,
    config_hash,
    rank_candidates,
    recommend_pair,
    team_for_pick,
)
from .reducer import state_hash


def recommend(
    config: LeagueConfig,
    players: Iterable[PlayerSnapshot],
    state: DraftState,
    *,
    top: int = 3,
    now: datetime | None = None,
) -> tuple[RecommendationEnvelope, list[dict[str, Any]], tuple[str, str] | None]:
    if not 1 <= top <= 3:
        raise ValueError("top must be between one and three")
    now = now or datetime.now(timezone.utc)
    player_list = list(players)
    ranked, excluded = rank_candidates(player_list, config, state, now=now)
    if not ranked:
        raise ValueError("no eligible candidates remain")

    chosen = ranked[:top]
    pair_ids: tuple[str, str] | None = None
    next_pick = state.current_pick + 1
    maximum_pick = config.active_teams * config.rounds
    if next_pick <= maximum_pick:
        current_team = team_for_pick(config, state.current_pick)
        following_team = team_for_pick(config, next_pick)
        if current_team == following_team == config.our_team:
            pair = recommend_pair(ranked, config, state)
            if pair is not None:
                pair_ids = (pair[0].player.player_id, pair[1].player.player_id)
                remainder = [item for item in ranked if item.player.player_id not in pair_ids]
                chosen = [pair[0], pair[1], *remainder][:top]

    envelope = RecommendationEnvelope(
        top_three=tuple(item.player.player_id for item in chosen),
        component_ordering=COMPONENT_ORDERING,
        exclusions=excluded,
        input_freshness={
            player.player_id: player.checked_at.astimezone(timezone.utc).isoformat()
            for player in player_list
        },
        state_hash=state_hash(state),
        config_hash=config_hash(config),
        board_hash=board_hash(player_list),
        expires_at=now + timedelta(seconds=15),
    )
    return envelope, [item.to_dict() for item in chosen], pair_ids
