from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fantasy_draft_assistant.models import (
    ArmMode,
    ControlState,
    DraftState,
    ObservedYahooRow,
    ObservedYahooState,
    RecommendationEnvelope,
)
from fantasy_draft_assistant.safety import evaluate_pick_safety, matching_rows


def _context(league, players):
    player = players[0]
    state = DraftState(
        current_pick=1,
        current_team=league.our_team,
        rosters={league.our_team: ()},
        queue=(player.player_id, players[1].player_id, players[2].player_id),
        control_state=ControlState.ARMED,
        armed_mode=ArmMode.MOCK,
        room_fingerprint="mock-room",
        reconciled=True,
    )
    envelope = RecommendationEnvelope(
        top_three=state.queue,
        component_ordering=("tier",),
        exclusions={},
        input_freshness={},
        state_hash="state-hash",
        config_hash="config-hash",
        board_hash="board-hash",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    observed = ObservedYahooState(
        room_fingerprint="mock-room",
        your_turn=True,
        current_team=league.our_team,
        overall_pick=1,
        clock_seconds=45,
        roster_count=0,
        rows=tuple(
            ObservedYahooRow(candidate.name, candidate.nfl_team, candidate.position, True, True)
            for candidate in players[:3]
        ),
        autodraft_off=True,
        captured_at=datetime.now(timezone.utc),
        queue_player_ids=state.queue,
        unavailable_player_ids=tuple(state.unavailable),
        roster_player_ids=(),
    )
    return player, state, envelope, observed


def test_complete_evidence_allows_intent(league, players):
    player, state, envelope, observed = _context(league, players)
    decision = evaluate_pick_safety(
        state=state,
        observed=observed,
        recommendation=envelope,
        player=player,
        current_state_hash="state-hash",
        current_config_hash="config-hash",
        current_board_hash="board-hash",
        queue_players=players[:3],
    )
    assert decision.allowed


def test_low_clock_and_reconnect_halt(league, players):
    player, state, envelope, observed = _context(league, players)
    observed = replace(observed, clock_seconds=19, reconnecting=True)
    decision = evaluate_pick_safety(
        state=state,
        observed=observed,
        recommendation=envelope,
        player=player,
        current_state_hash="state-hash",
        current_config_hash="config-hash",
        current_board_hash="board-hash",
        queue_players=players[:3],
    )
    assert not decision.allowed
    assert {"clock_below_minimum", "reconnecting"}.issubset(decision.reasons)


def test_exact_position_prevents_rb_wr_substring_regression(league, players):
    player, _, _, observed = _context(league, players)
    wrong = replace(observed.rows[0], position="WR")
    assert matching_rows(replace(observed, rows=(wrong,)), player) == ()


def test_duplicate_visible_rows_halt(league, players):
    player, state, envelope, observed = _context(league, players)
    observed = replace(observed, rows=(observed.rows[0], observed.rows[0]))
    decision = evaluate_pick_safety(
        state=state,
        observed=observed,
        recommendation=envelope,
        player=player,
        current_state_hash="state-hash",
        current_config_hash="config-hash",
        current_board_hash="board-hash",
        queue_players=players[:3],
    )
    assert "player_row_not_unique" in decision.reasons


def test_every_queued_player_requires_unique_visible_evidence(league, players):
    player, state, envelope, observed = _context(league, players)
    observed = replace(observed, rows=(observed.rows[0],))
    decision = evaluate_pick_safety(
        state=state,
        observed=observed,
        recommendation=envelope,
        player=player,
        current_state_hash="state-hash",
        current_config_hash="config-hash",
        current_board_hash="board-hash",
        queue_players=players[:3],
    )
    assert "queued_player_row_not_unique" in decision.reasons


def test_stale_observation_halts(league, players):
    player, state, envelope, observed = _context(league, players)
    observed = replace(observed, captured_at=datetime.now(timezone.utc) - timedelta(seconds=6))
    decision = evaluate_pick_safety(
        state=state,
        observed=observed,
        recommendation=envelope,
        player=player,
        current_state_hash="state-hash",
        current_config_hash="config-hash",
        current_board_hash="board-hash",
        queue_players=players[:3],
    )
    assert "observed_state_not_fresh" in decision.reasons
