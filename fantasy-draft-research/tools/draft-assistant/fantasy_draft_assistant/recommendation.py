"""Pure, hash-bound turn recommendations and sequential snake-turn plans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from .models import (
    DraftState,
    JsonValue,
    LeagueConfig,
    ObservedDraftState,
    PlayerSnapshot,
    CompiledPlayer,
    RecommendationEnvelope,
)
from .optimizer import (
    COMPONENT_ORDERING,
    Candidate,
    board_hash,
    config_hash,
    pair_fallback_branches,
    rank_candidates,
    recommend_pair,
    team_for_pick,
)
from .reducer import state_hash


DraftPlayer = PlayerSnapshot | CompiledPlayer


@dataclass(frozen=True, slots=True)
class TurnRecommendation:
    envelope: RecommendationEnvelope
    primary: str
    fallbacks: tuple[str, ...]
    candidates: tuple[Candidate, ...]
    pair_plan: tuple[str, str] | None
    fallback_branches: Mapping[str, str]
    explanations: Mapping[str, tuple[str, ...]]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "recommendation": {
                "top_three": list(self.envelope.top_three),
                "component_ordering": list(self.envelope.component_ordering),
                "exclusions": {
                    player_id: list(reasons)
                    for player_id, reasons in self.envelope.exclusions.items()
                },
                "input_freshness": dict(self.envelope.input_freshness),
                "state_hash": self.envelope.state_hash,
                "config_hash": self.envelope.config_hash,
                "board_hash": self.envelope.board_hash,
                "expires_at": self.envelope.expires_at.isoformat(),
            },
            "primary": self.primary,
            "fallbacks": list(self.fallbacks),
            "candidates": [item.to_dict() for item in self.candidates],
            "pair_plan": list(self.pair_plan) if self.pair_plan else None,
            "fallback_branches": dict(self.fallback_branches),
            "explanations": {
                player_id: list(reasons)
                for player_id, reasons in self.explanations.items()
            },
        }


def _is_consecutive_snake_turn(config: LeagueConfig, state: DraftState) -> bool:
    next_pick = state.current_pick + 1
    maximum_pick = config.active_teams * config.rounds
    if next_pick > maximum_pick:
        return False
    if any(
        keeper.team == config.our_team and keeper.overall_pick == next_pick
        for keeper in config.keepers
    ):
        return False
    return (
        team_for_pick(config, state.current_pick)
        == team_for_pick(config, next_pick)
        == config.our_team
    )


def recommend_turn(
    config: LeagueConfig,
    players: Iterable[DraftPlayer],
    state: DraftState,
    *,
    top: int = 3,
    now: datetime | None = None,
    observed: ObservedDraftState | None = None,
) -> TurnRecommendation:
    """Calculate a recommendation without mutating queue or draft state."""

    if not 1 <= top <= 3:
        raise ValueError("top must be between one and three")
    timestamp = now or datetime.now(timezone.utc)
    player_list = list(players)
    ranked, excluded = rank_candidates(
        player_list,
        config,
        state,
        now=timestamp,
        observed=observed,
    )
    if not ranked:
        raise ValueError("no eligible candidates remain")

    chosen = tuple(ranked[:top])
    pair_ids: tuple[str, str] | None = None
    branches: dict[str, str] = {}
    if _is_consecutive_snake_turn(config, state):
        pair = recommend_pair(
            ranked,
            config,
            state,
            now=timestamp,
            observed=observed,
            all_players=player_list,
        )
        if pair is not None:
            pair_ids = (pair[0].player.player_id, pair[1].player.player_id)
        branches = pair_fallback_branches(
            ranked,
            config,
            state,
            branch_count=min(3, len(ranked)),
            now=timestamp,
            observed=observed,
            all_players=player_list,
        )

    explanations = {
        candidate.player.player_id: candidate.explanations
        for candidate in ranked
    }
    explanations.update(
        {
            player_id: tuple(f"excluded by hard rule: {reason}" for reason in reasons)
            for player_id, reasons in excluded.items()
        }
    )
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
        expires_at=timestamp + timedelta(seconds=15),
    )
    return TurnRecommendation(
        envelope=envelope,
        primary=chosen[0].player.player_id,
        fallbacks=tuple(item.player.player_id for item in chosen[1:]),
        candidates=chosen,
        pair_plan=pair_ids,
        fallback_branches=branches,
        explanations=explanations,
    )


def recommend(
    config: LeagueConfig,
    players: Iterable[DraftPlayer],
    state: DraftState,
    *,
    top: int = 3,
    now: datetime | None = None,
    observed: ObservedDraftState | None = None,
) -> tuple[RecommendationEnvelope, list[dict[str, Any]], tuple[str, str] | None]:
    """Backward-compatible tuple wrapper around :func:`recommend_turn`."""

    result = recommend_turn(
        config,
        players,
        state,
        top=top,
        now=now,
        observed=observed,
    )
    return (
        result.envelope,
        [item.to_dict() for item in result.candidates],
        result.pair_plan,
    )
