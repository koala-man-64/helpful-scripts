"""Fail-closed pre-click safety evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Collection, Protocol, Sequence

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
MAXIMUM_OBSERVATION_AGE_SECONDS = 5
MAXIMUM_FUTURE_CLOCK_SKEW_SECONDS = 2


class PostPickObservation(Protocol):
    """Structural subset required from a platform-neutral draft observation."""

    platform: object
    room_fingerprint: str
    current_team: str
    overall_pick: int
    roster_count: int
    captured_at: datetime
    roster_player_ids: tuple[str, ...]
    unavailable_player_ids: tuple[str, ...]
    authentication_challenge: bool
    modal_ambiguity: bool
    reconnecting: bool
    control_interrupted: bool
    autodraft_off: bool
    phase: str
    control_status: str
    last_pick_player_id: str | None
    last_pick_position: str | None
    last_pick_overall: int | None
    room_advanced: bool | None
    last_pick_provenance: str | None
    last_pick_timer_expired: bool | None


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
    require_visible_queue_match: bool = True,
    now: datetime | None = None,
) -> SafetyDecision:
    """Return every detected reason a pick must halt; missing evidence is unsafe."""

    reasons: list[str] = []
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not _observation_is_fresh(observed.captured_at, timestamp):
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
    if _normalized_status(getattr(observed, "phase", None)) != "in_progress":
        reasons.append("draft_phase_not_in_progress")
    if _normalized_status(getattr(observed, "control_status", None)) != "ready":
        reasons.append("draft_control_not_ready")
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
    else:
        selected_row = rows[0]
        if selected_row.player_id != player.player_id:
            reasons.append("player_row_id_mismatch")
        if selected_row.ambiguous:
            reasons.append("player_row_identity_ambiguous")
        if not selected_row.available:
            reasons.append("player_not_visibly_available")
        if not selected_row.has_draft_control:
            reasons.append("draft_control_not_on_player_row")

    acceptable = set(acceptable_queue_ids or recommendation.top_three)
    visible_queue = observed.queue_player_ids
    if require_visible_queue_match:
        if not visible_queue:
            reasons.append("queue_empty")
        if len(set(visible_queue)) != len(visible_queue):
            reasons.append("queue_contains_duplicates")
        if any(candidate not in acceptable or candidate in state.unavailable for candidate in visible_queue):
            reasons.append("queue_contains_unacceptable_player")
        if tuple(visible_queue) != tuple(state.queue):
            reasons.append("queue_state_mismatch")
    queue_by_id = {candidate.player_id: candidate for candidate in (queue_players or ())}
    queue_evidence_ids = visible_queue if require_visible_queue_match else tuple(queue_by_id)
    for candidate_id in queue_evidence_ids:
        queued_player = queue_by_id.get(candidate_id)
        if queued_player is None:
            reasons.append("missing_queue_identity_evidence")
            continue
        if candidate_id not in acceptable or candidate_id in state.unavailable:
            reasons.append("queue_contains_unacceptable_player")
        if queued_player.ambiguous:
            reasons.append("queued_player_identity_ambiguous")
        queued_rows = matching_rows(observed, queued_player)
        if len(queued_rows) != 1:
            reasons.append("queued_player_row_not_unique")
        else:
            queued_row = queued_rows[0]
            if queued_row.player_id != queued_player.player_id:
                reasons.append("queued_player_row_id_mismatch")
            if queued_row.ambiguous:
                reasons.append("queued_player_row_identity_ambiguous")
            if not queued_row.available:
                reasons.append("queued_player_not_visibly_available")
            if not queued_row.has_draft_control:
                reasons.append("queued_player_missing_draft_control")

    return SafetyDecision.halt(*reasons) if reasons else SafetyDecision.allow()


def evaluate_post_pick_verification(
    *,
    observed: PostPickObservation,
    expected_platform: object,
    expected_room_fingerprint: str,
    expected_player_id: str,
    expected_position: str,
    expected_pick: int,
    expected_roster_count: int,
    expected_next_team: str | None,
    now: datetime | None = None,
) -> SafetyDecision:
    """Pure, fail-closed evaluation of evidence captured after one submission.

    Callers must turn any halt decision into manager takeover.  This function
    intentionally has no event-store or browser side effects and never grants a
    retry of the submitted action.
    """

    reasons: list[str] = []
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    captured_at = getattr(observed, "captured_at", None)
    if not _observation_is_fresh(captured_at, timestamp):
        reasons.append("verification_observation_not_fresh")

    if _platform_text(getattr(observed, "platform", None)) != _platform_text(expected_platform):
        reasons.append("platform_mismatch")
    if (
        not expected_room_fingerprint
        or getattr(observed, "room_fingerprint", None) != expected_room_fingerprint
    ):
        reasons.append("room_fingerprint_mismatch")

    if getattr(observed, "last_pick_player_id", None) != expected_player_id:
        reasons.append("last_pick_player_mismatch")
    if not _positions_match(getattr(observed, "last_pick_position", None), expected_position):
        reasons.append("last_pick_position_mismatch")
    if getattr(observed, "last_pick_overall", None) != expected_pick:
        reasons.append("last_pick_overall_mismatch")
    if getattr(observed, "last_pick_provenance", None) != "manager-approved-chrome":
        reasons.append("last_pick_provenance_mismatch")
    if getattr(observed, "last_pick_timer_expired", None) is not False:
        reasons.append("last_pick_timer_expiry_ambiguous")
    phase = _normalized_status(getattr(observed, "phase", None))
    if getattr(observed, "room_advanced", None) is not True:
        reasons.append("room_did_not_advance")
    if expected_next_team is None:
        if phase not in {"complete", "completed"}:
            reasons.append("terminal_draft_not_complete")
        if getattr(observed, "overall_pick", None) not in {expected_pick, expected_pick + 1}:
            reasons.append("terminal_pick_position_mismatch")
    else:
        if getattr(observed, "overall_pick", None) != expected_pick + 1:
            reasons.append("room_did_not_advance")
        if getattr(observed, "current_team", None) != expected_next_team:
            reasons.append("next_team_mismatch")

    if getattr(observed, "roster_count", None) != expected_roster_count + 1:
        reasons.append("roster_count_did_not_advance")
    roster_ids = set(getattr(observed, "roster_player_ids", ()) or ())
    if expected_player_id not in roster_ids:
        reasons.append("selected_player_missing_from_roster")
    unavailable_ids = set(getattr(observed, "unavailable_player_ids", ()) or ())
    if expected_player_id not in unavailable_ids:
        reasons.append("selected_player_missing_from_unavailable")

    # `is not False` is deliberate: absent/None status is ambiguous and must
    # halt just like an explicitly active interruption.
    for field_name, reason in (
        ("authentication_challenge", "authentication_challenge"),
        ("modal_ambiguity", "modal_ambiguity"),
        ("reconnecting", "reconnecting"),
        ("control_interrupted", "control_interrupted"),
    ):
        if getattr(observed, field_name, None) is not False:
            reasons.append(reason)

    if getattr(observed, "autodraft_off", None) is not True:
        reasons.append("autodraft_not_off")
    if phase not in {"in_progress", "complete", "completed"}:
        reasons.append("post_pick_phase_not_manual")
    control_status = _normalized_status(getattr(observed, "control_status", None))
    if control_status not in {"ready", "complete", "completed"}:
        reasons.append("post_pick_control_not_ready")

    return SafetyDecision.halt(*reasons) if reasons else SafetyDecision.allow()


def evaluate_platform_autodraft_observation(
    *,
    observed: PostPickObservation,
    expected_platform: object,
    expected_room_fingerprint: str,
    expected_pick: int,
    expected_roster_count: int,
    expected_next_team: str | None,
    now: datetime | None = None,
) -> SafetyDecision:
    """Verify that the timer/platform, rather than the approved executor, made the pick."""

    reasons: list[str] = []
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not _observation_is_fresh(getattr(observed, "captured_at", None), timestamp):
        reasons.append("autodraft_observation_not_fresh")
    if _platform_text(getattr(observed, "platform", None)) != _platform_text(expected_platform):
        reasons.append("platform_mismatch")
    if getattr(observed, "room_fingerprint", None) != expected_room_fingerprint:
        reasons.append("room_fingerprint_mismatch")
    player_id = getattr(observed, "last_pick_player_id", None)
    if not isinstance(player_id, str) or not player_id.strip():
        reasons.append("autodraft_player_missing")
    if getattr(observed, "last_pick_overall", None) != expected_pick:
        reasons.append("last_pick_overall_mismatch")
    if getattr(observed, "room_advanced", None) is not True:
        reasons.append("room_did_not_advance")
    phase = _normalized_status(getattr(observed, "phase", None))
    if phase not in {"auto_drafted", "in_progress"}:
        reasons.append("phase_not_platform_autodraft")
    if getattr(observed, "last_pick_provenance", None) != "platform-autodraft":
        reasons.append("last_pick_provenance_mismatch")
    if getattr(observed, "last_pick_timer_expired", None) is not True:
        reasons.append("timer_expiry_not_confirmed")
    if getattr(observed, "roster_count", None) != expected_roster_count + 1:
        reasons.append("roster_count_did_not_advance")
    roster_ids = set(getattr(observed, "roster_player_ids", ()) or ())
    unavailable_ids = set(getattr(observed, "unavailable_player_ids", ()) or ())
    if isinstance(player_id, str):
        if player_id not in roster_ids:
            reasons.append("autodrafted_player_missing_from_roster")
        if player_id not in unavailable_ids:
            reasons.append("autodrafted_player_missing_from_unavailable")
    if expected_next_team is not None:
        if getattr(observed, "overall_pick", None) != expected_pick + 1:
            reasons.append("room_did_not_advance")
        if getattr(observed, "current_team", None) != expected_next_team:
            reasons.append("next_team_mismatch")
    for field_name, reason in (
        ("authentication_challenge", "authentication_challenge"),
        ("modal_ambiguity", "modal_ambiguity"),
        ("reconnecting", "reconnecting"),
        ("control_interrupted", "control_interrupted"),
    ):
        if getattr(observed, field_name, None) is not False:
            reasons.append(reason)
    if _normalized_status(getattr(observed, "control_status", None)) != "ready":
        reasons.append("post_pick_control_not_ready")
    return SafetyDecision.halt(*reasons) if reasons else SafetyDecision.allow()


def _observation_is_fresh(captured_at: object, now: datetime) -> bool:
    if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
        return False
    age = (now - captured_at.astimezone(timezone.utc)).total_seconds()
    return -MAXIMUM_FUTURE_CLOCK_SKEW_SECONDS <= age <= MAXIMUM_OBSERVATION_AGE_SECONDS


def _platform_text(value: object) -> str | None:
    candidate = getattr(value, "value", value)
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    return candidate.strip().casefold()


def _normalized_status(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def _positions_match(observed_position: object, expected_position: object) -> bool:
    if not isinstance(observed_position, str) or not isinstance(expected_position, str):
        return False
    if not observed_position.strip() or not expected_position.strip():
        return False
    return normalize_position(observed_position) == normalize_position(expected_position)


# Concise alias for callers that do not need the more explicit name.
evaluate_safety = evaluate_pick_safety
