"""Pure, deterministic reduction of draft events into current state."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Any, Iterable

from .models import ArmMode, ControlState, DraftEvent, DraftState, IntentStatus


class InvalidEventError(ValueError):
    pass


def reduce_event(state: DraftState, event: DraftEvent) -> DraftState:
    payload = event.payload
    kind = event.event_type

    if kind == "initialized":
        rosters = {str(team): tuple(str(player) for player in players) for team, players in dict(payload.get("rosters", {})).items()}
        unavailable = frozenset(str(player) for player in payload.get("unavailable", []))
        return DraftState(
            current_pick=_positive(payload.get("current_pick", 1), "current_pick"),
            current_team=_optional_text(payload.get("current_team")),
            rosters=rosters,
            unavailable=unavailable,
            queue=tuple(str(player) for player in payload.get("queue", [])),
        )

    if kind == "pick_observed":
        overall = _positive(payload.get("overall_pick"), "overall_pick")
        if overall != state.current_pick:
            raise InvalidEventError(f"pick_observed expected overall {state.current_pick}, found {overall}")
        player_id = _text(payload.get("player_id"), "player_id")
        keeper = payload.get("keeper", False) is True
        if player_id in state.unavailable and not keeper:
            raise InvalidEventError(f"player {player_id!r} is already unavailable")
        team = _text(payload.get("team"), "team")
        rosters = {name: tuple(players) for name, players in state.rosters.items()}
        if player_id not in rosters.get(team, ()):
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

    if kind == "queue_set":
        queue = tuple(str(item) for item in payload.get("player_ids", payload.get("queue", [])))
        if len(set(queue)) != len(queue) or any(item in state.unavailable for item in queue):
            raise InvalidEventError("queue contains duplicate or unavailable players")
        return replace(state, queue=queue)

    if kind == "armed":
        mode = ArmMode(_text(payload.get("mode"), "mode"))
        fingerprint = _text(payload.get("room_fingerprint"), "room_fingerprint")
        if not state.reconciled:
            raise InvalidEventError("arming requires a reconciled visible room state")
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
        if state.control_state is not ControlState.DISARMED or state.outstanding_intent_id is not None:
            raise InvalidEventError("real acknowledgement requires a disarmed run with no outstanding intent")
        fingerprint = _text(payload.get("room_fingerprint"), "room_fingerprint")
        if not state.reconciled or state.room_fingerprint != fingerprint:
            raise InvalidEventError("real acknowledgement must match the reconciled room")
        return replace(
            state,
            real_draft_acknowledged=True,
        )

    if kind == "disarmed":
        return replace(
            state,
            control_state=ControlState.DISARMED,
            armed_mode=None,
            room_fingerprint=None,
            real_draft_acknowledged=False,
            halt_reason=_text(payload.get("reason"), "reason"),
        )

    if kind == "takeover_required":
        return replace(
            state,
            control_state=ControlState.TAKEOVER,
            armed_mode=None,
            real_draft_acknowledged=False,
            halt_reason=_text(payload.get("reason"), "reason"),
        )

    if kind == "intent_issued":
        intent_id = _text(payload.get("intent_id"), "intent_id")
        if state.outstanding_intent_id is not None:
            raise InvalidEventError("cannot issue an intent while another intent is outstanding")
        return replace(
            state,
            outstanding_intent_id=intent_id,
            outstanding_intent_status=IntentStatus.ISSUED,
        )

    if kind == "intent_submitted":
        _require_intent(state, payload, IntentStatus.ISSUED)
        return replace(state, outstanding_intent_status=IntentStatus.SUBMITTED)

    if kind in {"intent_cancelled", "pick_verified"}:
        required = IntentStatus.SUBMITTED if kind == "pick_verified" else None
        _require_intent(state, payload, required)
        verified_pick = state.last_verified_pick
        if kind == "pick_verified":
            verified_pick = _positive(payload.get("overall_pick"), "overall_pick")
        return replace(
            state,
            outstanding_intent_id=None,
            outstanding_intent_status=None,
            last_verified_pick=verified_pick,
        )

    if kind == "pick_verified_and_observed":
        _require_intent(state, payload, IntentStatus.SUBMITTED)
        overall = _positive(payload.get("overall_pick"), "overall_pick")
        if overall != state.current_pick:
            raise InvalidEventError(f"verified pick expected overall {state.current_pick}, found {overall}")
        player_id = _text(payload.get("player_id"), "player_id")
        if player_id in state.unavailable:
            raise InvalidEventError(f"player {player_id!r} is already unavailable")
        team = _text(payload.get("team"), "team")
        rosters = {name: tuple(players) for name, players in state.rosters.items()}
        rosters[team] = rosters.get(team, ()) + (player_id,)
        return replace(
            state,
            current_pick=overall + 1,
            current_team=_optional_text(payload.get("next_team")),
            rosters=rosters,
            unavailable=state.unavailable | {player_id},
            queue=tuple(candidate for candidate in state.queue if candidate != player_id),
            outstanding_intent_id=None,
            outstanding_intent_status=None,
            last_verified_pick=overall,
            reconciled=False,
        )

    if kind == "verification_failed_takeover":
        _require_intent(state, payload, IntentStatus.SUBMITTED)
        return replace(
            state,
            control_state=ControlState.TAKEOVER,
            armed_mode=None,
            real_draft_acknowledged=False,
            outstanding_intent_id=None,
            outstanding_intent_status=None,
            reconciled=False,
            halt_reason=_text(payload.get("reason"), "reason"),
        )

    if kind == "reconciled":
        if state.outstanding_intent_id is not None:
            raise InvalidEventError("cannot reconcile with an outstanding intent")
        observed_pick = _positive(payload.get("current_pick"), "current_pick")
        return replace(
            state,
            current_pick=observed_pick,
            current_team=_text(payload.get("current_team"), "current_team"),
            room_fingerprint=_text(payload.get("room_fingerprint"), "room_fingerprint"),
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
        "control_state": state.control_state.value,
        "armed_mode": state.armed_mode.value if state.armed_mode else None,
        "room_fingerprint": state.room_fingerprint,
        "real_draft_acknowledged": state.real_draft_acknowledged,
        "last_verified_pick": state.last_verified_pick,
        "outstanding_intent_id": state.outstanding_intent_id,
        "outstanding_intent_status": (
            state.outstanding_intent_status.value if state.outstanding_intent_status else None
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
