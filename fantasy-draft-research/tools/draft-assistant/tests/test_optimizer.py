from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from fantasy_draft_assistant.models import DraftState
from fantasy_draft_assistant.optimizer import rank_candidates, team_for_pick
from fantasy_draft_assistant.recommendation import recommend


def test_slot_one_snake_and_keeper_sequence(league):
    expected = [1, 16, 17, 32, 33, 48, 49, 64, 65, 80, 81, 96, 97, 112, 113, 128]
    actual = [pick for pick in range(1, 129) if team_for_pick(league, pick) == league.our_team]
    assert actual == expected
    assert league.keepers[0].overall_pick == 49


def test_keeper_is_never_recommended(league, players):
    state = DraftState(current_pick=1, current_team=league.our_team)
    envelope, _, _ = recommend(league, players, state, now=datetime.now(timezone.utc))
    assert "treveyon-henderson" not in envelope.top_three
    assert "keeper" in envelope.exclusions["treveyon-henderson"]


def test_recommendation_is_deterministic(league, players):
    state = DraftState(current_pick=1, current_team=league.our_team)
    now = datetime.now(timezone.utc)
    first = recommend(league, players, state, now=now)[0]
    second = recommend(league, players, state, now=now)[0]
    assert first.top_three == second.top_three
    assert first.state_hash == second.state_hash


def test_paired_turn_returns_joint_pair(league, players):
    state = DraftState(current_pick=16, current_team=league.our_team)
    _, _, pair = recommend(league, players, state, now=datetime.now(timezone.utc))
    assert pair is not None
    assert len(set(pair)) == 2


def test_unavailable_and_ambiguous_are_excluded(league, players):
    ambiguous = replace(players[0], ambiguous=True)
    state = DraftState(unavailable=frozenset({players[1].player_id}))
    ranked, excluded = rank_candidates([ambiguous, players[1]], league, state)
    assert ranked == []
    assert "ambiguous_identity" in excluded[ambiguous.player_id]
    assert "unavailable" in excluded[players[1].player_id]
