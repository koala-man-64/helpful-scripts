"""Fail-closed pre-click safety evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Collection, Sequence

from .models import (
    ArmMode,
    ControlState,
    DraftState,
    ObservedYahooRow,
    ObservedYahooState,
    PlayerSnapshot,
    RecommendationEnvelope,
    SafetyDecision,
    normalize_player_name,
    normalize_position,
    normalize_team,
)


MINIMUM_CLICK_CLOCK_SECONDS = 20


def matching_rows(observed: ObservedYahooState, player: PlayerSnapshot) -> tuple[ObservedYahooRow, ...]:
    """Match all three visible identity components; position is never substring-matched."""

    return tuple(
        row
        for row in observed.rows
        if normalize_player_name(row.name) == player.normalized_name
        and normalize_team(row.nfl_team) == normalize_team(player.nfl_team)
        and normalize_position(row.position) == normalize_position(player.position)
    )


def evaluate_pick_safety(
    *,
    state: DraftState,
    observed: ObservedYahooState,
    recommendation: RecommendationEnvelope,
    player: PlayerSnapshot,
    current_state_hash: str | None = None,
    current_config_hash: str | None = None,
    current_board_hash: str | None = None,
    acceptable_queue_ids: Collection[str] | None = None,
    queue_players: Sequence[PlayerSnapshot] | None = None,
    now: datetime | None = None,
) -> SafetyDecision:
    """Return every detected reason a pick must halt; missing evidence is unsafe."""

    reasons: list[str] = []
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    observation_age = (timestamp - observed.captured_at.astimezone(timezone.utc)).total_seconds()
    if observation_age < -2 or observation_age > 5:
        reasons.append("observed_state_not_fresh")

    if state.control_state is not ControlState.ARMED or state.armed_mode is None:
        reasons.append("not_armed")
    if state.armed_mode is ArmMode.REAL and not state.real_draft_acknowledged:
        reasons.append("real_draft_not_acknowledged")
    if not state.reconciled:
        reasons.append("state_not_reconciled")
    if not state.room_fingerprint or observed.room_fingerprint != state.room_fingerprint:
        reasons.append("room_fingerprint_mismatch")
    if not observed.your_turn:
        reasons.append("not_your_turn")
    if observed.overall_pick != state.current_pick:
        reasons.append("overall_pick_mismatch")
    if not state.current_team or observed.current_team != state.current_team:
        reasons.append("current_team_mismatch")
    expected_roster_count = state.roster_count(observed.current_team)
    if observed.roster_count != expected_roster_count:
        reasons.append("roster_count_mismatch")
    if set(observed.roster_player_ids) != set(state.rosters.get(observed.current_team, ())):
        reasons.append("roster_players_mismatch")
    if set(observed.unavailable_player_ids) != set(state.unavailable):
        reasons.append("unavailable_set_mismatch")
    if observed.clock_seconds < MINIMUM_CLICK_CLOCK_SECONDS:
        reasons.append("clock_below_minimum")
    if observed.authentication_challenge:
        reasons.append("authentication_challenge")
    if observed.modal_ambiguity:
        reasons.append("modal_ambiguity")
    if observed.reconnecting:
        reasons.append("reconnecting")
    if observed.control_interrupted:
        reasons.append("control_interrupted")
    if not observed.autodraft_off:
        reasons.append("autodraft_not_off")
    if state.outstanding_intent_id is not None:
        reasons.append("outstanding_intent")

    if timestamp >= recommendation.expires_at.astimezone(timezone.utc):
        reasons.append("recommendation_expired")
    supplied_hashes = (current_state_hash, current_config_hash, current_board_hash)
    expected_hashes = (recommendation.state_hash, recommendation.config_hash, recommendation.board_hash)
    observed_hashes = (observed.state_hash, observed.config_hash, observed.board_hash)
    for label, supplied, expected, visible in zip(
        ("state", "config", "board"), supplied_hashes, expected_hashes, observed_hashes, strict=True
    ):
        actual = supplied if supplied is not None else visible
        if actual is None:
            reasons.append(f"missing_{label}_hash")
        elif actual != expected:
            reasons.append(f"stale_{label}_hash")

    if player.player_id not in recommendation.top_three:
        reasons.append("player_not_recommended")
    if player.player_id in state.unavailable:
        reasons.append("player_unavailable_in_state")
    if player.ambiguous:
        reasons.append("player_identity_ambiguous")
    rows = matching_rows(observed, player)
    if len(rows) != 1:
        reasons.append("player_row_not_unique")
    elif not rows[0].available:
        reasons.append("player_not_visibly_available")
    elif not rows[0].has_draft_control:
        reasons.append("draft_control_not_on_player_row")

    acceptable = set(acceptable_queue_ids or recommendation.top_three)
    visible_queue = observed.queue_player_ids
    if not visible_queue:
        reasons.append("queue_empty")
    if len(set(visible_queue)) != len(visible_queue):
        reasons.append("queue_contains_duplicates")
    if any(candidate not in acceptable or candidate in state.unavailable for candidate in visible_queue):
        reasons.append("queue_contains_unacceptable_player")
    if tuple(visible_queue) != tuple(state.queue):
        reasons.append("queue_state_mismatch")
    queue_by_id = {candidate.player_id: candidate for candidate in (queue_players or ())}
    for candidate_id in visible_queue:
        queued_player = queue_by_id.get(candidate_id)
        if queued_player is None:
            reasons.append("missing_queue_identity_evidence")
            continue
        queued_rows = matching_rows(observed, queued_player)
        if len(queued_rows) != 1:
            reasons.append("queued_player_row_not_unique")
        elif not queued_rows[0].available:
            reasons.append("queued_player_not_visibly_available")
        elif not queued_rows[0].has_draft_control:
            reasons.append("queued_player_missing_draft_control")

    return SafetyDecision.halt(*reasons) if reasons else SafetyDecision.allow()


# Concise alias for callers that do not need the more explicit name.
evaluate_safety = evaluate_pick_safety
