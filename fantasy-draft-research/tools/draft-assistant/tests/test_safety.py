from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fantasy_draft_assistant.models import (
    ArmMode,
    ControlState,
    DraftState,
    ObservedYahooRow,
    ObservedYahooState,
    RecommendationEnvelope,
)
from fantasy_draft_assistant.safety import (
    evaluate_pick_safety,
    evaluate_post_pick_verification,
    matching_rows,
)


ROOM_FINGERPRINT = "room-fp:" + "a" * 64
OTHER_ROOM_FINGERPRINT = "room-fp:" + "b" * 64


def _context(league, players):
    player = players[0]
    state = DraftState(
        current_pick=1,
        current_team=league.our_team,
        rosters={league.our_team: ()},
        queue=(player.player_id, players[1].player_id, players[2].player_id),
        control_state=ControlState.ARMED,
        armed_mode=ArmMode.MOCK,
        room_fingerprint=ROOM_FINGERPRINT,
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
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=10),
    )
    observed = ObservedYahooState(
        room_fingerprint=ROOM_FINGERPRINT,
        your_turn=True,
        current_team=league.our_team,
        overall_pick=1,
        clock_seconds=45,
        roster_count=0,
        rows=tuple(
            ObservedYahooRow(
                name=candidate.name,
                nfl_team=candidate.nfl_team,
                position=candidate.position,
                available=True,
                has_draft_control=True,
                player_id=candidate.player_id,
                ambiguous=False,
            )
            for candidate in players[:3]
        ),
        autodraft_off=True,
        captured_at=datetime.now(timezone.utc),
        queue_player_ids=state.queue,
        unavailable_player_ids=tuple(state.unavailable),
        roster_player_ids=(),
        authentication_challenge=False,
        modal_ambiguity=False,
        reconnecting=False,
        control_interrupted=False,
        phase="in_progress",
        control_status="ready",
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


def test_visible_player_requires_exact_id_and_unambiguous_row(league, players):
    player, state, envelope, observed = _context(league, players)
    unsafe_row = replace(observed.rows[0], player_id="wrong-player-id", ambiguous=True)

    decision = evaluate_pick_safety(
        state=state,
        observed=replace(observed, rows=(unsafe_row, *observed.rows[1:])),
        recommendation=envelope,
        player=player,
        current_state_hash="state-hash",
        current_config_hash="config-hash",
        current_board_hash="board-hash",
        queue_players=players[:3],
    )

    assert {"player_row_id_mismatch", "player_row_identity_ambiguous"}.issubset(
        decision.reasons
    )


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


def _post_pick_observation(player, *, captured_at, **overrides):
    values = {
        "platform": "yahoo",
        "room_fingerprint": ROOM_FINGERPRINT,
        "current_team": "team-2",
        "overall_pick": 2,
        "roster_count": 1,
        "captured_at": captured_at,
        "roster_player_ids": (player.player_id,),
        "unavailable_player_ids": (player.player_id,),
        "authentication_challenge": False,
        "modal_ambiguity": False,
        "reconnecting": False,
        "control_interrupted": False,
        "last_pick_player_id": player.player_id,
        "last_pick_position": player.position,
        "last_pick_overall": 1,
        "room_advanced": True,
        "last_pick_provenance": "manager-approved-chrome",
        "last_pick_timer_expired": False,
        "autodraft_off": True,
        "phase": "in_progress",
        "control_status": "ready",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _post_pick_decision(player, observed, now):
    return evaluate_post_pick_verification(
        observed=observed,
        expected_platform="yahoo",
        expected_room_fingerprint=ROOM_FINGERPRINT,
        expected_player_id=player.player_id,
        expected_position=player.position,
        expected_pick=1,
        expected_roster_count=0,
        expected_next_team="team-2",
        now=now,
    )


def test_complete_post_pick_evidence_verifies(players):
    now = datetime.now(timezone.utc)
    player = players[0]

    decision = _post_pick_decision(
        player,
        _post_pick_observation(player, captured_at=now),
        now,
    )

    assert decision.allowed


def test_post_pick_verification_requires_fresh_same_platform_and_room(players):
    now = datetime.now(timezone.utc)
    player = players[0]
    observed = _post_pick_observation(
        player,
        captured_at=now - timedelta(seconds=6),
        platform="espn",
        room_fingerprint=OTHER_ROOM_FINGERPRINT,
    )

    decision = _post_pick_decision(player, observed, now)

    assert {
        "verification_observation_not_fresh",
        "platform_mismatch",
        "room_fingerprint_mismatch",
    }.issubset(decision.reasons)


def test_post_pick_verification_requires_player_in_roster_and_unavailable(players):
    now = datetime.now(timezone.utc)
    player = players[0]
    observed = _post_pick_observation(
        player,
        captured_at=now,
        roster_player_ids=(),
        unavailable_player_ids=(),
    )

    decision = _post_pick_decision(player, observed, now)

    assert {
        "selected_player_missing_from_roster",
        "selected_player_missing_from_unavailable",
    }.issubset(decision.reasons)


def test_post_pick_verification_requires_exact_pick_identity_and_advancement(players):
    now = datetime.now(timezone.utc)
    player = players[0]
    observed = _post_pick_observation(
        player,
        captured_at=now,
        overall_pick=1,
        roster_count=0,
        last_pick_player_id="other-player",
        last_pick_position="WR" if player.position != "WR" else "RB",
        last_pick_overall=2,
        room_advanced=False,
    )

    decision = _post_pick_decision(player, observed, now)

    assert {
        "last_pick_player_mismatch",
        "last_pick_position_mismatch",
        "last_pick_overall_mismatch",
        "room_did_not_advance",
        "roster_count_did_not_advance",
    }.issubset(decision.reasons)


def test_post_pick_verification_rejects_any_control_ambiguity(players):
    now = datetime.now(timezone.utc)
    player = players[0]
    observed = _post_pick_observation(
        player,
        captured_at=now,
        authentication_challenge=True,
        modal_ambiguity=True,
        reconnecting=True,
        control_interrupted=True,
    )

    decision = _post_pick_decision(player, observed, now)

    assert {
        "authentication_challenge",
        "modal_ambiguity",
        "reconnecting",
        "control_interrupted",
    }.issubset(decision.reasons)


def test_post_pick_verification_requires_manual_provenance_and_unexpired_timer(players):
    now = datetime.now(timezone.utc)
    player = players[0]
    observed = _post_pick_observation(
        player,
        captured_at=now,
        last_pick_provenance="platform-autodraft",
        last_pick_timer_expired=True,
        autodraft_off=False,
        phase="auto_drafted",
        control_status="ambiguous",
    )

    decision = _post_pick_decision(player, observed, now)

    assert {
        "last_pick_provenance_mismatch",
        "last_pick_timer_expiry_ambiguous",
        "autodraft_not_off",
        "post_pick_phase_not_manual",
        "post_pick_control_not_ready",
    }.issubset(decision.reasons)


def test_post_pick_verification_treats_missing_evidence_as_unsafe(players):
    now = datetime.now(timezone.utc)
    player = players[0]
    observed = SimpleNamespace(captured_at=now)

    decision = _post_pick_decision(player, observed, now)

    assert not decision.allowed
    assert {
        "platform_mismatch",
        "room_fingerprint_mismatch",
        "room_did_not_advance",
        "next_team_mismatch",
        "selected_player_missing_from_roster",
        "selected_player_missing_from_unavailable",
        "last_pick_provenance_mismatch",
        "last_pick_timer_expiry_ambiguous",
        "authentication_challenge",
        "modal_ambiguity",
        "reconnecting",
        "control_interrupted",
        "autodraft_not_off",
        "post_pick_phase_not_manual",
        "post_pick_control_not_ready",
    }.issubset(decision.reasons)
