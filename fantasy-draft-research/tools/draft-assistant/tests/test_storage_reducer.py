from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from fantasy_draft_assistant.models import datetime_text
from fantasy_draft_assistant.reducer import InvalidEventError, replay, state_hash
from fantasy_draft_assistant.storage import (
    CorruptEventLogError,
    EventConflictError,
    EventStore,
    EventStoreError,
)
from fantasy_draft_assistant_provisioning import provision_event_store


BASE_TIME = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
ROOM_FINGERPRINT = "room-fp:" + "a" * 64
CONFIG_HASH = "config-sha256"
BOARD_HASH = "board-sha256"
BASELINE_QUEUE = ("alpha-rb", "bravo-wr", "charlie-rb")


def _create_store(path):
    provision_event_store(path)
    return EventStore(path)


def _initialized_payload(*, rosters=None, unavailable=(), queue=BASELINE_QUEUE):
    return {
        "current_pick": 1,
        "current_team": "team-1",
        "rosters": rosters or {"team-1": [], "team-2": []},
        "unavailable": list(unavailable),
        "queue": list(queue),
        "config_hash": CONFIG_HASH,
        "board_hash": BOARD_HASH,
    }


def _reconciled_payload():
    return {
        "current_pick": 1,
        "current_team": "team-1",
        "room_fingerprint": ROOM_FINGERPRINT,
        "platform": "yahoo",
        "adapter_version": "1",
        "queue": list(BASELINE_QUEUE),
        "our_team": "team-1",
        "our_roster": [],
        "unavailable": [],
        "completed_picks": [],
        "config_hash": CONFIG_HASH,
        "board_hash": BOARD_HASH,
        "control_snapshot_hash": "observe-control-sha256",
    }


def _intent_payload():
    return {
        "intent_id": "intent-1",
        "player_id": "alpha-rb",
        "player_name": "Alpha Runner",
        "nfl_team": "AAA",
        "position": "RB",
        "expected_pick": 1,
        "expected_team": "team-1",
        "expected_roster_count": 0,
        "platform": "yahoo",
        "adapter_version": "1",
        "room_fingerprint": ROOM_FINGERPRINT,
        "approval_observation_hash": "approval-observation-sha256",
        "approval_control_snapshot_hash": "approval-control-sha256",
        "pre_submit_binding_hash": "pre-submit-binding-sha256",
        "baseline_queue": list(BASELINE_QUEUE),
        "approved_queue": list(BASELINE_QUEUE),
        "recommendation_top_three": list(BASELINE_QUEUE),
        "state_hash": "state-sha256",
        "config_hash": CONFIG_HASH,
        "board_hash": BOARD_HASH,
        "expires_at": datetime_text(BASE_TIME + timedelta(seconds=13)),
    }


def _submission_payload():
    return {
        "intent_id": "intent-1",
        "submission_provenance": "manager_approved_chrome_attempt_unverified",
        "room_fingerprint": ROOM_FINGERPRINT,
        "platform": "yahoo",
        "adapter_version": "1",
        "overall_pick": 1,
        "current_team": "team-1",
        "clock_seconds": 45,
        "observed_at": datetime_text(BASE_TIME + timedelta(seconds=4)),
        "observed_queue": list(BASELINE_QUEUE),
        "roster_player_ids": [],
        "unavailable_player_ids": [],
        "authentication_challenge": False,
        "modal_ambiguity": False,
        "reconnecting": False,
        "control_interrupted": False,
        "autodraft_off": True,
        "phase": "in_progress",
        "control_status": "ready",
        "recommendation_unchanged": True,
        "control_snapshot_hashes": {
            "queue": "queue-control-sha256",
            "pick": "pick-control-sha256",
        },
    }


def _verification_payload():
    return {
        "intent_id": "intent-1",
        "overall_pick": 1,
        "player_id": "alpha-rb",
        "team": "team-1",
        "next_team": "team-2",
        "room_fingerprint": ROOM_FINGERPRINT,
        "platform": "yahoo",
        "adapter_version": "1",
        "observed_overall_pick": 2,
        "observed_current_team": "team-2",
        "roster_player_ids": ["alpha-rb"],
        "unavailable_player_ids": ["alpha-rb"],
        "verification_observation_hash": "verification-observation-sha256",
        "verification_control_snapshot_hash": "verification-control-sha256",
        "authentication_challenge": False,
        "modal_ambiguity": False,
        "reconnecting": False,
        "control_interrupted": False,
        "autodraft_off": True,
        "phase": "in_progress",
        "control_status": "ready",
        "confirmed_submission_provenance": "manager_approved_chrome_transaction",
        "last_pick_provenance": "manager-approved-chrome",
        "last_pick_timer_expired": False,
    }


def _issued_intent_store(path):
    store = _create_store(path)
    store.append(
        "initialized",
        _initialized_payload(),
        idempotency_key="init",
        timestamp=BASE_TIME,
    )
    store.append(
        "reconciled",
        _reconciled_payload(),
        idempotency_key="reconciled",
        timestamp=BASE_TIME + timedelta(seconds=1),
    )
    store.append(
        "armed",
        {"mode": "mock", "room_fingerprint": ROOM_FINGERPRINT},
        idempotency_key="armed",
        timestamp=BASE_TIME + timedelta(seconds=2),
    )
    store.append(
        "intent_issued",
        _intent_payload(),
        idempotency_key="issued",
        timestamp=BASE_TIME + timedelta(seconds=3),
    )
    return store


def _submit_intent(store, *, idempotency_key="submitted"):
    return store.append(
        "intent_submitted",
        _submission_payload(),
        idempotency_key=idempotency_key,
        timestamp=BASE_TIME + timedelta(seconds=4),
    )


def test_runtime_store_does_not_bootstrap_missing_schema(tmp_path):
    with pytest.raises(EventStoreError, match="not provisioned"):
        EventStore(tmp_path / "missing.sqlite")


def test_verification_requires_submitted_intent(tmp_path):
    store = _issued_intent_store(tmp_path / "draft.sqlite")

    with pytest.raises(InvalidEventError, match="submitted"):
        store.append(
            "pick_verified_and_observed",
            _verification_payload(),
            idempotency_key="verify",
            timestamp=BASE_TIME + timedelta(seconds=4),
        )


def test_invalid_transition_is_not_persisted_or_reserved(tmp_path):
    store = _issued_intent_store(tmp_path / "draft.sqlite")
    before = store.events()

    with pytest.raises(InvalidEventError, match="submitted"):
        store.append(
            "pick_verified_and_observed",
            _verification_payload(),
            idempotency_key="transition-key",
            timestamp=BASE_TIME + timedelta(seconds=4),
        )

    assert store.events() == before
    submitted = _submit_intent(store, idempotency_key="transition-key")
    assert submitted.sequence == len(before) + 1
    assert replay(store.events()).outstanding_intent_status.value == "submitted"


def test_issued_intent_can_be_cancelled(tmp_path):
    store = _issued_intent_store(tmp_path / "draft.sqlite")
    store.append(
        "intent_cancelled",
        {"intent_id": "intent-1", "reason": "manager changed the pick"},
        idempotency_key="cancelled",
        timestamp=BASE_TIME + timedelta(seconds=4),
    )

    state = replay(store.events())
    assert state.outstanding_intent_id is None
    assert state.outstanding_intent_status is None
    assert state.approved_queue == ()


def test_submitted_intent_cannot_be_cancelled(tmp_path):
    store = _issued_intent_store(tmp_path / "draft.sqlite")
    _submit_intent(store)
    before = store.events()

    with pytest.raises(InvalidEventError, match="issued"):
        store.append(
            "intent_cancelled",
            {"intent_id": "intent-1", "reason": "too late"},
            idempotency_key="cancelled",
            timestamp=BASE_TIME + timedelta(seconds=5),
        )

    assert store.events() == before


def test_verified_pick_is_one_atomic_replay_transition(tmp_path):
    store = _issued_intent_store(tmp_path / "draft.sqlite")
    _submit_intent(store)
    before = len(store.events())
    store.append(
        "pick_verified_and_observed",
        _verification_payload(),
        idempotency_key="verified-observed",
        timestamp=BASE_TIME + timedelta(seconds=5),
    )

    assert len(store.events()) == before + 1
    state = replay(store.events())
    assert state.current_pick == 2
    assert state.current_team == "team-2"
    assert state.last_verified_pick == 1
    assert state.rosters["team-1"] == ("alpha-rb",)
    assert state.unavailable == frozenset({"alpha-rb"})
    assert state.queue == ("bravo-wr", "charlie-rb")
    assert state.outstanding_intent_id is None
    assert state.outstanding_intent_status is None
    assert state.reconciled is False


def test_failed_verification_is_one_atomic_takeover_transition(tmp_path):
    store = _issued_intent_store(tmp_path / "draft.sqlite")
    _submit_intent(store)
    before = len(store.events())
    store.append(
        "verification_failed_takeover",
        {"intent_id": "intent-1", "reason": "mismatch"},
        idempotency_key="failed",
        timestamp=BASE_TIME + timedelta(seconds=5),
    )

    assert len(store.events()) == before + 1
    state = replay(store.events())
    assert state.control_state.value == "takeover"
    assert state.outstanding_intent_id is None
    assert state.reconciled is False


def test_sqlite_idempotency_and_replay(tmp_path):
    store = _create_store(tmp_path / "draft.sqlite")
    payload = _initialized_payload(queue=())
    initialized = store.append(
        "initialized", payload, idempotency_key="init", timestamp=BASE_TIME
    )
    repeated = store.append(
        "initialized",
        payload,
        idempotency_key="init",
        timestamp=BASE_TIME + timedelta(seconds=10),
    )
    assert repeated.event_hash == initialized.event_hash
    state = replay(store.events())
    assert state.current_pick == 1
    assert state_hash(state) == state_hash(replay(store.events()))


def test_reused_idempotency_key_with_other_payload_is_rejected(tmp_path):
    store = _create_store(tmp_path / "draft.sqlite")
    store.append(
        "disarmed",
        {"reason": "one"},
        idempotency_key="same",
        timestamp=BASE_TIME,
    )
    with pytest.raises(EventConflictError):
        store.append(
            "disarmed",
            {"reason": "two"},
            idempotency_key="same",
            timestamp=BASE_TIME + timedelta(seconds=1),
        )


def test_hash_chain_corruption_is_detected(tmp_path):
    path = tmp_path / "draft.sqlite"
    store = _create_store(path)
    store.append(
        "disarmed",
        {"reason": "safe"},
        idempotency_key="one",
        timestamp=BASE_TIME,
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE draft_events SET payload_json = ? WHERE sequence = 1",
            ('{"reason":"tampered"}',),
        )
    with pytest.raises(CorruptEventLogError):
        store.events()


def test_pick_replay_records_external_player_once(tmp_path):
    store = _create_store(tmp_path / "draft.sqlite")
    store.append(
        "initialized",
        _initialized_payload(queue=()),
        idempotency_key="init",
        timestamp=BASE_TIME,
    )
    store.append(
        "pick_observed",
        {
            "overall_pick": 1,
            "player_id": "alpha-rb",
            "team": "team-1",
            "next_team": "team-2",
            "keeper": False,
            "submission_provenance": "external",
        },
        idempotency_key="pick-1",
        timestamp=BASE_TIME + timedelta(seconds=1),
    )
    state = replay(store.events())
    assert state.current_pick == 2
    assert state.current_team == "team-2"
    assert state.rosters["team-1"] == ("alpha-rb",)
    assert "alpha-rb" in state.unavailable
    assert state.reconciled is False


def test_configured_keeper_can_advance_its_occupied_pick(tmp_path):
    store = _create_store(tmp_path / "draft.sqlite")
    keeper_id = "treveyon-henderson"
    store.append(
        "initialized",
        _initialized_payload(
            rosters={"team-1": [keeper_id], "team-2": []},
            unavailable=(keeper_id,),
            queue=(),
        ),
        idempotency_key="init",
        timestamp=BASE_TIME,
    )
    store.append(
        "pick_observed",
        {
            "overall_pick": 1,
            "player_id": keeper_id,
            "team": "team-1",
            "next_team": "team-2",
            "keeper": True,
            "submission_provenance": "external",
        },
        idempotency_key="keeper-1",
        timestamp=BASE_TIME + timedelta(seconds=1),
    )
    state = replay(store.events())
    assert state.current_pick == 2
    assert state.rosters["team-1"] == (keeper_id,)
    assert state.unavailable == frozenset({keeper_id})
