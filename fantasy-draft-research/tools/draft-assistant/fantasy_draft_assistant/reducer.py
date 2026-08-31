"""Pure, deterministic reduction of draft events into current state."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from .models import (
    ArmMode,
    ControlState,
    DraftEvent,
    DraftPlatform,
    DraftState,
    IntentStatus,
    parse_datetime,
    validate_room_fingerprint,
)


class InvalidEventError(ValueError):
    pass


def reduce_event(state: DraftState, event: DraftEvent) -> DraftState:
    payload = event.payload
    kind = event.event_type
    if event.version != 1:
        raise InvalidEventError(f"unsupported event version: {event.version}")

    if kind == "initialized":
        _payload_shape(
            payload,
            kind,
            {"current_pick", "current_team", "rosters", "unavailable", "queue", "config_hash", "board_hash"},
        )
        if event.sequence != 1:
            raise InvalidEventError("initialized must be the first event")
        rosters = _rosters(payload.get("rosters"))
        unavailable = frozenset(_ids(payload.get("unavailable"), "unavailable"))
        rostered = {player for players in rosters.values() for player in players}
        if not rostered.issubset(unavailable):
            raise InvalidEventError("initialized roster players must already be unavailable")
        queue = _ids(payload.get("queue"), "queue")
        if len(set(queue)) != len(queue) or any(player in unavailable for player in queue):
            raise InvalidEventError("initialized queue contains duplicate or unavailable players")
        current_pick = _positive(payload.get("current_pick"), "current_pick")
        if current_pick != 1:
            raise InvalidEventError("initialized current_pick must be 1")
        current_team = _text(payload.get("current_team"), "current_team")
        if current_team not in rosters:
            raise InvalidEventError("initialized current_team must appear in rosters")
        _text(payload.get("config_hash"), "config_hash")
        _text(payload.get("board_hash"), "board_hash")
        return DraftState(
            current_pick=current_pick,
            current_team=current_team,
            rosters=rosters,
            unavailable=unavailable,
            queue=queue,
            config_hash=_text(payload.get("config_hash"), "config_hash"),
            board_hash=_text(payload.get("board_hash"), "board_hash"),
        )

    if kind == "pick_observed":
        _payload_shape(
            payload,
            kind,
            {"overall_pick", "player_id", "team", "next_team", "keeper", "submission_provenance"},
            required={"overall_pick", "player_id", "team", "next_team", "keeper", "submission_provenance"},
        )
        if state.outstanding_intent_id is not None:
            raise InvalidEventError("pick_observed cannot bypass an outstanding intent")
        overall = _positive(payload.get("overall_pick"), "overall_pick")
        if overall != state.current_pick:
            raise InvalidEventError(f"pick_observed expected overall {state.current_pick}, found {overall}")
        player_id = _text(payload.get("player_id"), "player_id")
        keeper = _boolean(payload.get("keeper", False), "keeper")
        if _text(payload.get("submission_provenance"), "submission_provenance") not in {
            "manager", "platform-autodraft", "external"
        }:
            raise InvalidEventError("pick_observed submission provenance is unsupported")
        team = _text(payload.get("team"), "team")
        if team != state.current_team or team not in state.rosters:
            raise InvalidEventError("pick_observed team must match the current draft team")
        rosters = {name: tuple(players) for name, players in state.rosters.items()}
        if keeper:
            if player_id not in state.unavailable or player_id not in rosters[team]:
                raise InvalidEventError("keeper pick must reference its precommitted roster player")
        elif player_id in state.unavailable:
            raise InvalidEventError(f"player {player_id!r} is already unavailable")
        else:
            rosters[team] = rosters.get(team, ()) + (player_id,)
        return replace(
            state,
            current_pick=overall + 1,
            current_team=_optional_text(payload.get("next_team")),
            rosters=rosters,
            unavailable=state.unavailable | {player_id},
            queue=tuple(candidate for candidate in state.queue if candidate != player_id),
            reconciled=False,
        )

    if kind == "armed":
        _payload_shape(payload, kind, {"mode", "room_fingerprint"})
        mode = ArmMode(_text(payload.get("mode"), "mode"))
        fingerprint = _room(payload.get("room_fingerprint"))
        if not state.reconciled:
            raise InvalidEventError("arming requires a reconciled visible room state")
        if state.control_state is not ControlState.DISARMED or state.outstanding_intent_id is not None:
            raise InvalidEventError("arming requires a disarmed run with no outstanding intent")
        if state.platform is None or state.adapter_version is None:
            raise InvalidEventError("arming requires a supported reconciled platform mapping")
        if state.room_fingerprint != fingerprint:
            raise InvalidEventError("armed room fingerprint must match the reconciled room")
        acknowledged = state.real_draft_acknowledged
        if mode is ArmMode.REAL and (not acknowledged or state.room_fingerprint != fingerprint):
            raise InvalidEventError("real mode requires a separate acknowledgement for this room fingerprint")
        return replace(
            state,
            control_state=ControlState.ARMED,
            armed_mode=mode,
            room_fingerprint=fingerprint,
            real_draft_acknowledged=acknowledged if mode is ArmMode.REAL else False,
            halt_reason=None,
        )

    if kind == "real_draft_acknowledged":
        _payload_shape(payload, kind, {"room_fingerprint"})
        if state.control_state is not ControlState.DISARMED or state.outstanding_intent_id is not None:
            raise InvalidEventError("real acknowledgement requires a disarmed run with no outstanding intent")
        fingerprint = _room(payload.get("room_fingerprint"))
        if not state.reconciled or state.room_fingerprint != fingerprint:
            raise InvalidEventError("real acknowledgement must match the reconciled room")
        return replace(
            state,
            real_draft_acknowledged=True,
        )

    if kind == "disarmed":
        _payload_shape(payload, kind, {"reason"})
        if state.outstanding_intent_id is not None:
            raise InvalidEventError("cancel an issued intent or verify a submitted intent before disarming")
        return replace(
            state,
            control_state=ControlState.DISARMED,
            armed_mode=None,
            room_fingerprint=None,
            platform=None,
            adapter_version=None,
            approved_queue=(),
            real_draft_acknowledged=False,
            halt_reason=_text(payload.get("reason"), "reason"),
        )

    if kind == "takeover_required":
        _payload_shape(payload, kind, {"reason"})
        if state.outstanding_intent_id is not None:
            raise InvalidEventError("use the intent-specific takeover transition while an intent exists")
        return replace(
            state,
            control_state=ControlState.TAKEOVER,
            armed_mode=None,
            real_draft_acknowledged=False,
            halt_reason=_text(payload.get("reason"), "reason"),
        )

    if kind == "intent_issued":
        _payload_shape(
            payload,
            kind,
            {
                "intent_id", "player_id", "player_name", "nfl_team", "position",
                "expected_pick", "expected_team", "expected_roster_count", "platform",
                "adapter_version", "room_fingerprint", "approval_observation_hash",
                "approval_control_snapshot_hash", "pre_submit_binding_hash", "baseline_queue",
                "approved_queue", "recommendation_top_three", "state_hash", "config_hash",
                "board_hash", "expires_at",
            },
        )
        intent_id = _text(payload.get("intent_id"), "intent_id")
        if state.control_state is not ControlState.ARMED or state.armed_mode is None:
            raise InvalidEventError("issuing an intent requires an armed run")
        if not state.reconciled or not state.room_fingerprint:
            raise InvalidEventError("issuing an intent requires a reconciled room")
        if state.outstanding_intent_id is not None:
            raise InvalidEventError("cannot issue an intent while another intent is outstanding")
        if _room(payload.get("room_fingerprint")) != state.room_fingerprint:
            raise InvalidEventError("intent room fingerprint must match the reconciled room")
        expected_pick = _positive(payload.get("expected_pick"), "expected_pick")
        if expected_pick != state.current_pick:
            raise InvalidEventError("intent pick must match the reconciled current pick")
        expected_team = _text(payload.get("expected_team"), "expected_team")
        if expected_team != state.current_team:
            raise InvalidEventError("intent team must match the reconciled current team")
        platform = DraftPlatform(_text(payload.get("platform"), "platform"))
        if platform is not state.platform:
            raise InvalidEventError("intent platform must match the reconciled platform")
        if _text(payload.get("adapter_version"), "adapter_version") != state.adapter_version:
            raise InvalidEventError("intent adapter version must match the reconciled adapter")
        player_id = _text(payload.get("player_id"), "player_id")
        if _nonnegative(payload.get("expected_roster_count"), "expected_roster_count") != state.roster_count(expected_team):
            raise InvalidEventError("intent roster count must match the reconciled roster")
        for field_name in (
            "player_name",
            "nfl_team",
            "position",
            "approval_observation_hash",
            "approval_control_snapshot_hash",
            "pre_submit_binding_hash",
            "state_hash",
            "config_hash",
            "board_hash",
        ):
            _text(payload.get(field_name), field_name)
        baseline_queue = _ids(payload.get("baseline_queue"), "baseline_queue")
        if baseline_queue != state.queue:
            raise InvalidEventError("intent baseline queue must match the observed platform queue")
        approved_queue = _ids(payload.get("approved_queue"), "approved_queue")
        if not 1 <= len(approved_queue) <= 3 or approved_queue[0] != player_id:
            raise InvalidEventError("approved queue must contain one to three players led by the approved player")
        if len(set(approved_queue)) != len(approved_queue) or any(
            item in state.unavailable for item in approved_queue
        ):
            raise InvalidEventError("approved queue contains duplicate or unavailable players")
        top_three = _ids(payload.get("recommendation_top_three"), "recommendation_top_three")
        if not 1 <= len(top_three) <= 3 or len(set(top_three)) != len(top_three):
            raise InvalidEventError("recommendation_top_three must contain one to three unique players")
        if any(player not in top_three for player in approved_queue):
            raise InvalidEventError("approved queue must be a subset of recommendation_top_three")
        expires_at = parse_datetime(_text(payload.get("expires_at"), "expires_at"), "expires_at")
        if not event.timestamp < expires_at <= event.timestamp + timedelta(seconds=15):
            raise InvalidEventError("intent expiry must be in the next 15 seconds")
        return replace(
            state,
            approved_queue=approved_queue,
            outstanding_intent_id=intent_id,
            outstanding_intent_status=IntentStatus.ISSUED,
            outstanding_player_id=player_id,
            outstanding_expected_pick=expected_pick,
            outstanding_expected_team=expected_team,
            outstanding_expires_at=expires_at,
        )

    if kind == "intent_submitted":
        _payload_shape(
            payload,
            kind,
            {
                "intent_id", "submission_provenance", "room_fingerprint", "platform",
                "adapter_version", "overall_pick", "current_team", "clock_seconds",
                "observed_at", "observed_queue", "roster_player_ids",
                "unavailable_player_ids", "authentication_challenge", "modal_ambiguity",
                "reconnecting", "control_interrupted", "autodraft_off", "phase",
                "control_status", "recommendation_unchanged", "control_snapshot_hashes",
            },
        )
        _require_intent(state, payload, IntentStatus.ISSUED)
        if state.control_state is not ControlState.ARMED or not state.reconciled:
            raise InvalidEventError("submitting an intent requires an armed reconciled state")
        if state.outstanding_expires_at is None or event.timestamp >= state.outstanding_expires_at:
            raise InvalidEventError("intent expired before submission")
        if _text(payload.get("submission_provenance"), "submission_provenance") != "manager_approved_chrome_attempt_unverified":
            raise InvalidEventError("submission provenance is invalid")
        if _room(payload.get("room_fingerprint")) != state.room_fingerprint:
            raise InvalidEventError("submitted room fingerprint does not match")
        if DraftPlatform(_text(payload.get("platform"), "platform")) is not state.platform:
            raise InvalidEventError("submitted platform does not match")
        if _text(payload.get("adapter_version"), "adapter_version") != state.adapter_version:
            raise InvalidEventError("submitted adapter version does not match")
        if _positive(payload.get("overall_pick"), "overall_pick") != state.current_pick:
            raise InvalidEventError("submitted pick does not match")
        if _text(payload.get("current_team"), "current_team") != state.current_team:
            raise InvalidEventError("submitted team does not match")
        clock_seconds = _nonnegative(payload.get("clock_seconds"), "clock_seconds")
        if clock_seconds < 20:
            raise InvalidEventError("submission requires at least 20 seconds remaining")
        observed_at = parse_datetime(_text(payload.get("observed_at"), "observed_at"), "observed_at")
        observation_age = (event.timestamp - observed_at).total_seconds()
        if observation_age < -2 or observation_age > 5:
            raise InvalidEventError("submission observation is not fresh")
        if _ids(payload.get("observed_queue"), "observed_queue") != state.approved_queue:
            raise InvalidEventError("submitted queue does not match the approved queue")
        roster_ids = frozenset(_ids(payload.get("roster_player_ids"), "roster_player_ids"))
        if roster_ids != frozenset(state.rosters.get(state.current_team or "", ())):
            raise InvalidEventError("submitted roster evidence does not match state")
        unavailable_ids = frozenset(
            _ids(payload.get("unavailable_player_ids"), "unavailable_player_ids")
        )
        if unavailable_ids != state.unavailable:
            raise InvalidEventError("submitted unavailable evidence does not match state")
        for field_name in (
            "authentication_challenge",
            "modal_ambiguity",
            "reconnecting",
            "control_interrupted",
        ):
            if _boolean(payload.get(field_name), field_name):
                raise InvalidEventError(f"submission blocked by {field_name}")
        if not _boolean(payload.get("autodraft_off"), "autodraft_off"):
            raise InvalidEventError("submission requires autodraft to be off")
        if _text(payload.get("phase"), "phase").casefold() != "in_progress":
            raise InvalidEventError("submission requires an in-progress draft")
        if _text(payload.get("control_status"), "control_status").casefold() != "ready":
            raise InvalidEventError("submission requires ready controls")
        if not _boolean(payload.get("recommendation_unchanged"), "recommendation_unchanged"):
            raise InvalidEventError("submission requires an unchanged recommendation")
        control_hashes = payload.get("control_snapshot_hashes")
        if not isinstance(control_hashes, Mapping):
            raise InvalidEventError("control_snapshot_hashes must be an object")
        if set(control_hashes) != {"queue", "pick"}:
            raise InvalidEventError("queue and pick control snapshot hashes are required")
        for operation, digest in control_hashes.items():
            _text(digest, f"control_snapshot_hashes.{operation}")
        return replace(
            state,
            queue=state.approved_queue,
            outstanding_intent_status=IntentStatus.SUBMITTED,
        )

    if kind == "intent_cancelled":
        _payload_shape(payload, kind, {"intent_id", "reason"})
        _require_intent(state, payload, IntentStatus.ISSUED)
        _text(payload.get("reason"), "reason")
        return replace(
            state,
            approved_queue=(),
            outstanding_intent_id=None,
            outstanding_intent_status=None,
            outstanding_player_id=None,
            outstanding_expected_pick=None,
            outstanding_expected_team=None,
            outstanding_expires_at=None,
        )

    if kind == "pick_verified_and_observed":
        _payload_shape(
            payload,
            kind,
            {
                "intent_id", "overall_pick", "player_id", "team", "next_team",
                "room_fingerprint", "platform", "adapter_version", "observed_overall_pick",
                "observed_current_team", "roster_player_ids", "unavailable_player_ids",
                "verification_observation_hash", "verification_control_snapshot_hash",
                "authentication_challenge", "modal_ambiguity", "reconnecting",
                "control_interrupted", "autodraft_off", "phase", "control_status",
                "confirmed_submission_provenance",
                "last_pick_provenance", "last_pick_timer_expired",
            },
        )
        _require_intent(state, payload, IntentStatus.SUBMITTED)
        overall = _positive(payload.get("overall_pick"), "overall_pick")
        if overall != state.current_pick:
            raise InvalidEventError(f"verified pick expected overall {state.current_pick}, found {overall}")
        player_id = _text(payload.get("player_id"), "player_id")
        if player_id != state.outstanding_player_id:
            raise InvalidEventError("verified player does not match the approved intent")
        if player_id in state.unavailable:
            raise InvalidEventError(f"player {player_id!r} is already unavailable")
        team = _text(payload.get("team"), "team")
        if team != state.outstanding_expected_team or team != state.current_team:
            raise InvalidEventError("verified team does not match the approved intent")
        if _room(payload.get("room_fingerprint")) != state.room_fingerprint:
            raise InvalidEventError("verified room fingerprint does not match")
        if DraftPlatform(_text(payload.get("platform"), "platform")) is not state.platform:
            raise InvalidEventError("verified platform does not match")
        if _text(payload.get("adapter_version"), "adapter_version") != state.adapter_version:
            raise InvalidEventError("verified adapter version does not match")
        next_team = _optional_text(payload.get("next_team"))
        observed_overall = _positive(payload.get("observed_overall_pick"), "observed_overall_pick")
        observed_team = _text(payload.get("observed_current_team"), "observed_current_team")
        phase = _text(payload.get("phase"), "phase").casefold()
        control_status = _text(payload.get("control_status"), "control_status").casefold()
        if next_team is None:
            if phase not in {"complete", "completed"} or observed_overall not in {overall, overall + 1}:
                raise InvalidEventError("terminal verification did not prove draft completion")
        elif observed_overall != overall + 1 or observed_team != next_team:
            raise InvalidEventError("verified room did not advance to the expected next team")
        if not _boolean(payload.get("autodraft_off"), "autodraft_off"):
            raise InvalidEventError("verified pick cannot be attributed while autodraft is enabled")
        for field_name in (
            "authentication_challenge",
            "modal_ambiguity",
            "reconnecting",
            "control_interrupted",
        ):
            if _boolean(payload.get(field_name), field_name):
                raise InvalidEventError(f"verified pick blocked by {field_name}")
        if phase not in {"in_progress", "complete", "completed"}:
            raise InvalidEventError("verified phase is not a manual-pick phase")
        if control_status not in {"ready", "complete", "completed"}:
            raise InvalidEventError("verified controls are ambiguous")
        _text(payload.get("verification_observation_hash"), "verification_observation_hash")
        _text(payload.get("verification_control_snapshot_hash"), "verification_control_snapshot_hash")
        if _text(
            payload.get("confirmed_submission_provenance"),
            "confirmed_submission_provenance",
        ) != "manager_approved_chrome_transaction":
            raise InvalidEventError("confirmed submission provenance is invalid")
        if _text(payload.get("last_pick_provenance"), "last_pick_provenance") != "manager-approved-chrome":
            raise InvalidEventError("verified last-pick provenance is invalid")
        if _boolean(payload.get("last_pick_timer_expired"), "last_pick_timer_expired"):
            raise InvalidEventError("verified manager pick cannot be timer-expired")
        expected_roster = frozenset(state.rosters.get(team, ())) | {player_id}
        if frozenset(_ids(payload.get("roster_player_ids"), "roster_player_ids")) != expected_roster:
            raise InvalidEventError("verified roster evidence does not match the selected player")
        expected_unavailable = state.unavailable | {player_id}
        if frozenset(
            _ids(payload.get("unavailable_player_ids"), "unavailable_player_ids")
        ) != expected_unavailable:
            raise InvalidEventError("verified unavailable evidence does not match the selected player")
        rosters = {name: tuple(players) for name, players in state.rosters.items()}
        rosters[team] = rosters.get(team, ()) + (player_id,)
        return replace(
            state,
            current_pick=overall + 1,
            current_team=next_team,
            rosters=rosters,
            unavailable=state.unavailable | {player_id},
            queue=tuple(candidate for candidate in state.queue if candidate != player_id),
            approved_queue=(),
            outstanding_intent_id=None,
            outstanding_intent_status=None,
            outstanding_player_id=None,
            outstanding_expected_pick=None,
            outstanding_expected_team=None,
            outstanding_expires_at=None,
            last_verified_pick=overall,
            reconciled=False,
        )

    if kind == "verification_failed_takeover":
        _payload_shape(payload, kind, {"intent_id", "reason"})
        _require_intent(state, payload, IntentStatus.SUBMITTED)
        return replace(
            state,
            control_state=ControlState.TAKEOVER,
            armed_mode=None,
            real_draft_acknowledged=False,
            outstanding_intent_id=None,
            outstanding_intent_status=None,
            outstanding_player_id=None,
            outstanding_expected_pick=None,
            outstanding_expected_team=None,
            outstanding_expires_at=None,
            approved_queue=(),
            reconciled=False,
            halt_reason=_text(payload.get("reason"), "reason"),
        )

    if kind == "autodraft_verification_failed_takeover":
        _payload_shape(payload, kind, {"intent_id", "reason"})
        _require_intent(state, payload, IntentStatus.ISSUED)
        return replace(
            state,
            control_state=ControlState.TAKEOVER,
            armed_mode=None,
            real_draft_acknowledged=False,
            outstanding_intent_id=None,
            outstanding_intent_status=None,
            outstanding_player_id=None,
            outstanding_expected_pick=None,
            outstanding_expected_team=None,
            outstanding_expires_at=None,
            approved_queue=(),
            reconciled=False,
            halt_reason=_text(payload.get("reason"), "reason"),
        )

    if kind == "pre_submit_failed_takeover":
        _payload_shape(payload, kind, {"intent_id", "reason"})
        _require_intent(state, payload, IntentStatus.ISSUED)
        return replace(
            state,
            control_state=ControlState.TAKEOVER,
            armed_mode=None,
            real_draft_acknowledged=False,
            outstanding_intent_id=None,
            outstanding_intent_status=None,
            outstanding_player_id=None,
            outstanding_expected_pick=None,
            outstanding_expected_team=None,
            outstanding_expires_at=None,
            approved_queue=(),
            reconciled=False,
            halt_reason=_text(payload.get("reason"), "reason"),
        )

    if kind == "platform_autodraft_observed":
        _payload_shape(
            payload,
            kind,
            {
                "intent_id", "overall_pick", "player_id", "team", "next_team",
                "room_fingerprint", "platform", "adapter_version", "observed_overall_pick",
                "observed_current_team", "roster_player_ids", "unavailable_player_ids",
                "observed_queue", "verification_observation_hash",
                "verification_control_snapshot_hash", "autodraft_off", "phase",
                "control_status", "authentication_challenge", "modal_ambiguity",
                "reconnecting", "control_interrupted",
                "last_pick_provenance", "last_pick_timer_expired",
            },
        )
        _require_intent(state, payload, IntentStatus.ISSUED)
        overall = _positive(payload.get("overall_pick"), "overall_pick")
        if overall != state.current_pick:
            raise InvalidEventError("platform autodraft pick does not match current pick")
        player_id = _text(payload.get("player_id"), "player_id")
        if player_id in state.unavailable:
            raise InvalidEventError("platform autodraft player is already unavailable")
        team = _text(payload.get("team"), "team")
        if team != state.current_team:
            raise InvalidEventError("platform autodraft team does not match current team")
        if _room(payload.get("room_fingerprint")) != state.room_fingerprint:
            raise InvalidEventError("platform autodraft room does not match")
        if DraftPlatform(_text(payload.get("platform"), "platform")) is not state.platform:
            raise InvalidEventError("platform autodraft platform does not match")
        if _text(payload.get("adapter_version"), "adapter_version") != state.adapter_version:
            raise InvalidEventError("platform autodraft adapter does not match")
        next_team = _optional_text(payload.get("next_team"))
        if _positive(payload.get("observed_overall_pick"), "observed_overall_pick") != overall + 1:
            raise InvalidEventError("platform autodraft room did not advance")
        if next_team is not None and _text(payload.get("observed_current_team"), "observed_current_team") != next_team:
            raise InvalidEventError("platform autodraft next team does not match")
        _boolean(payload.get("autodraft_off"), "autodraft_off")
        if _text(payload.get("phase"), "phase").casefold() not in {"auto_drafted", "in_progress"}:
            raise InvalidEventError("platform autodraft phase is missing")
        if _text(payload.get("last_pick_provenance"), "last_pick_provenance") != "platform-autodraft":
            raise InvalidEventError("platform autodraft provenance is missing")
        if not _boolean(payload.get("last_pick_timer_expired"), "last_pick_timer_expired"):
            raise InvalidEventError("platform autodraft timer expiry is not confirmed")
        if _text(payload.get("control_status"), "control_status").casefold() != "ready":
            raise InvalidEventError("platform autodraft controls are ambiguous")
        for field_name in (
            "authentication_challenge",
            "modal_ambiguity",
            "reconnecting",
            "control_interrupted",
        ):
            if _boolean(payload.get(field_name), field_name):
                raise InvalidEventError(f"platform autodraft blocked by {field_name}")
        _text(payload.get("verification_observation_hash"), "verification_observation_hash")
        _text(payload.get("verification_control_snapshot_hash"), "verification_control_snapshot_hash")
        expected_roster = frozenset(state.rosters.get(team, ())) | {player_id}
        if frozenset(_ids(payload.get("roster_player_ids"), "roster_player_ids")) != expected_roster:
            raise InvalidEventError("platform autodraft roster evidence does not match")
        expected_unavailable = state.unavailable | {player_id}
        if frozenset(
            _ids(payload.get("unavailable_player_ids"), "unavailable_player_ids")
        ) != expected_unavailable:
            raise InvalidEventError("platform autodraft unavailable evidence does not match")
        observed_queue = _ids(payload.get("observed_queue"), "observed_queue")
        if len(set(observed_queue)) != len(observed_queue) or any(
            item in expected_unavailable for item in observed_queue
        ):
            raise InvalidEventError("platform autodraft queue evidence is invalid")
        rosters = {name: tuple(players) for name, players in state.rosters.items()}
        rosters[team] = rosters[team] + (player_id,)
        return replace(
            state,
            current_pick=overall + 1,
            current_team=next_team,
            rosters=rosters,
            unavailable=frozenset(expected_unavailable),
            queue=observed_queue,
            control_state=ControlState.TAKEOVER,
            armed_mode=None,
            real_draft_acknowledged=False,
            outstanding_intent_id=None,
            outstanding_intent_status=None,
            outstanding_player_id=None,
            outstanding_expected_pick=None,
            outstanding_expected_team=None,
            outstanding_expires_at=None,
            approved_queue=(),
            reconciled=False,
            halt_reason="platform_autodraft",
        )

    if kind == "reconciled":
        _payload_shape(
            payload,
            kind,
            {
                "current_pick", "current_team", "room_fingerprint", "platform",
                "adapter_version", "queue", "our_team", "our_roster", "unavailable",
                "completed_picks", "config_hash", "board_hash", "control_snapshot_hash",
            },
        )
        if state.outstanding_intent_id is not None:
            raise InvalidEventError("cannot reconcile with an outstanding intent")
        observed_pick = _positive(payload.get("current_pick"), "current_pick")
        if observed_pick < state.current_pick:
            raise InvalidEventError("reconciliation cannot move the draft backward")
        room_fingerprint = _room(payload.get("room_fingerprint"))
        platform = DraftPlatform(_text(payload.get("platform"), "platform"))
        adapter_version = _text(payload.get("adapter_version"), "adapter_version")
        _text(payload.get("config_hash"), "config_hash")
        _text(payload.get("board_hash"), "board_hash")
        _text(payload.get("control_snapshot_hash"), "control_snapshot_hash")
        if payload.get("config_hash") != state.config_hash:
            raise InvalidEventError("reconciled config hash changed")
        if payload.get("board_hash") != state.board_hash:
            raise InvalidEventError("reconciled board hash changed")
        if state.room_fingerprint and room_fingerprint != state.room_fingerprint:
            raise InvalidEventError("reconciled room fingerprint changed")
        if state.platform is not None and platform is not state.platform:
            raise InvalidEventError("reconciled platform changed")
        if state.adapter_version is not None and adapter_version != state.adapter_version:
            raise InvalidEventError("reconciled adapter version changed")

        completed = payload.get("completed_picks")
        if not isinstance(completed, Sequence) or isinstance(completed, (str, bytes)):
            raise InvalidEventError("completed_picks must be a list")
        expected_overalls = list(range(state.current_pick, observed_pick))
        if len(completed) != len(expected_overalls):
            raise InvalidEventError("completed_picks must cover every intervening pick exactly once")
        current_pick = state.current_pick
        current_team = state.current_team
        rosters = {team: tuple(players) for team, players in state.rosters.items()}
        unavailable = set(state.unavailable)
        queue_after_picks = state.queue
        for raw_pick, expected_overall in zip(completed, expected_overalls, strict=True):
            if not isinstance(raw_pick, Mapping):
                raise InvalidEventError("completed_picks entries must be objects")
            allowed_pick_keys = {
                "overall_pick", "player_id", "team", "keeper", "next_team", "provenance"
            }
            if set(raw_pick).difference(allowed_pick_keys):
                raise InvalidEventError("completed pick contains unknown fields")
            overall = _positive(raw_pick.get("overall_pick"), "completed_pick.overall_pick")
            if overall != expected_overall or overall != current_pick:
                raise InvalidEventError("completed picks are not contiguous")
            team = _text(raw_pick.get("team"), "completed_pick.team")
            if team != current_team or team not in rosters:
                raise InvalidEventError("completed pick team does not match draft order")
            player_id = _text(raw_pick.get("player_id"), "completed_pick.player_id")
            keeper = _boolean(raw_pick.get("keeper", False), "completed_pick.keeper")
            if keeper:
                if player_id not in unavailable or player_id not in rosters[team]:
                    raise InvalidEventError("completed keeper is not precommitted to its roster")
            else:
                if player_id in unavailable:
                    raise InvalidEventError("completed pick player is already unavailable")
                rosters[team] = rosters[team] + (player_id,)
                unavailable.add(player_id)
            _text(raw_pick.get("provenance"), "completed_pick.provenance")
            current_pick = overall + 1
            current_team = _optional_text(raw_pick.get("next_team"))
            queue_after_picks = tuple(item for item in queue_after_picks if item != player_id)

        observed_team = _text(payload.get("current_team"), "current_team")
        if current_pick != observed_pick or current_team != observed_team:
            raise InvalidEventError("completed pick ledger does not reach the observed turn")
        our_team = _text(payload.get("our_team"), "our_team")
        if our_team not in rosters:
            raise InvalidEventError("our_team is not present in rosters")
        observed_roster = frozenset(_ids(payload.get("our_roster"), "our_roster"))
        if observed_roster != frozenset(rosters[our_team]):
            raise InvalidEventError("observed roster does not match the completed pick ledger")
        observed_unavailable = frozenset(
            _ids(payload.get("unavailable"), "unavailable")
        )
        if observed_unavailable != frozenset(unavailable):
            raise InvalidEventError("observed unavailable set does not match the completed pick ledger")
        queue = _ids(payload.get("queue"), "queue")
        if len(set(queue)) != len(queue) or any(item in unavailable for item in queue):
            raise InvalidEventError("reconciled queue contains duplicate or unavailable players")
        return replace(
            state,
            current_pick=observed_pick,
            current_team=observed_team,
            rosters=rosters,
            unavailable=frozenset(unavailable),
            room_fingerprint=room_fingerprint,
            platform=platform,
            adapter_version=adapter_version,
            queue=queue,
            approved_queue=(),
            reconciled=True,
            halt_reason=None,
        )

    raise InvalidEventError(f"unsupported event type: {kind}")


def replay(events: Iterable[DraftEvent], initial_state: DraftState | None = None) -> DraftState:
    state = initial_state or DraftState()
    for event in events:
        state = reduce_event(state, event)
    return state


def state_to_dict(state: DraftState) -> dict[str, Any]:
    return {
        "current_pick": state.current_pick,
        "current_team": state.current_team,
        "rosters": {team: list(players) for team, players in sorted(state.rosters.items())},
        "unavailable": sorted(state.unavailable),
        "queue": list(state.queue),
        "approved_queue": list(state.approved_queue),
        "control_state": state.control_state.value,
        "armed_mode": state.armed_mode.value if state.armed_mode else None,
        "room_fingerprint": state.room_fingerprint,
        "platform": state.platform.value if state.platform else None,
        "adapter_version": state.adapter_version,
        "config_hash": state.config_hash,
        "board_hash": state.board_hash,
        "real_draft_acknowledged": state.real_draft_acknowledged,
        "last_verified_pick": state.last_verified_pick,
        "outstanding_intent_id": state.outstanding_intent_id,
        "outstanding_intent_status": (
            state.outstanding_intent_status.value if state.outstanding_intent_status else None
        ),
        "outstanding_player_id": state.outstanding_player_id,
        "outstanding_expected_pick": state.outstanding_expected_pick,
        "outstanding_expected_team": state.outstanding_expected_team,
        "outstanding_expires_at": (
            state.outstanding_expires_at.isoformat() if state.outstanding_expires_at else None
        ),
        "reconciled": state.reconciled,
        "halt_reason": state.halt_reason,
    }


def state_hash(state: DraftState) -> str:
    encoded = json.dumps(state_to_dict(state), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidEventError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    return None if value is None else _text(value, "text")


def _positive(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidEventError(f"{field_name} must be a positive integer")
    return value


def _nonnegative(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidEventError(f"{field_name} must be a non-negative integer")
    return value


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidEventError(f"{field_name} must be boolean")
    return value


def _room(value: Any) -> str:
    try:
        return validate_room_fingerprint(value)
    except ValueError as error:
        raise InvalidEventError(str(error)) from error


def _ids(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise InvalidEventError(f"{field_name} must be a list")
    return tuple(_text(item, f"{field_name} item") for item in value)


def _rosters(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping) or not value:
        raise InvalidEventError("rosters must be a non-empty object")
    result: dict[str, tuple[str, ...]] = {}
    all_players: list[str] = []
    for raw_team, raw_players in value.items():
        team = _text(raw_team, "rosters team")
        if team in result:
            raise InvalidEventError("rosters contains duplicate teams")
        players = _ids(raw_players, f"rosters.{team}")
        if len(set(players)) != len(players):
            raise InvalidEventError(f"rosters.{team} contains duplicate players")
        result[team] = players
        all_players.extend(players)
    if len(all_players) != len(set(all_players)):
        raise InvalidEventError("a player cannot appear on multiple rosters")
    return result


def _payload_shape(
    payload: Any,
    event_type: str,
    allowed: set[str],
    *,
    required: set[str] | None = None,
) -> None:
    if not isinstance(payload, Mapping):
        raise InvalidEventError(f"{event_type} payload must be an object")
    unknown = set(payload).difference(allowed)
    if unknown:
        raise InvalidEventError(
            f"{event_type} payload contains unknown fields: {', '.join(sorted(unknown))}"
        )
    missing = (required or allowed).difference(payload)
    if missing:
        raise InvalidEventError(
            f"{event_type} payload is missing fields: {', '.join(sorted(missing))}"
        )


def _require_intent(
    state: DraftState,
    payload: Any,
    required_status: IntentStatus | None = None,
) -> None:
    intent_id = _text(payload.get("intent_id"), "intent_id")
    if intent_id != state.outstanding_intent_id:
        raise InvalidEventError("event does not match the outstanding intent")
    if required_status is not None and state.outstanding_intent_status is not required_status:
        raise InvalidEventError(f"intent must be {required_status.value} before this transition")
