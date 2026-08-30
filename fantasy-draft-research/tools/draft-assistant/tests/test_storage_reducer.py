from __future__ import annotations

import sqlite3

import pytest

from fantasy_draft_assistant.reducer import InvalidEventError, replay, state_hash
from fantasy_draft_assistant.storage import CorruptEventLogError, EventConflictError, EventStore, EventStoreError
from fantasy_draft_assistant_provisioning import provision_event_store


def _create_store(path):
    provision_event_store(path)
    return EventStore(path)


def test_runtime_store_does_not_bootstrap_missing_schema(tmp_path):
    with pytest.raises(EventStoreError, match="not provisioned"):
        EventStore(tmp_path / "missing.sqlite")


def _submitted_intent_store(path):
    store = _create_store(path)
    store.append(
        "initialized",
        {"current_pick": 1, "current_team": "team-1", "rosters": {"team-1": []}, "unavailable": [], "queue": ["alpha-rb"]},
        idempotency_key="init",
    )
    store.append(
        "reconciled",
        {"current_pick": 1, "current_team": "team-1", "room_fingerprint": "mock"},
        idempotency_key="reconciled",
    )
    store.append("armed", {"mode": "mock", "room_fingerprint": "mock"}, idempotency_key="armed")
    store.append("intent_issued", {"intent_id": "intent-1"}, idempotency_key="issued")
    return store


def test_verification_requires_submitted_intent(tmp_path):
    store = _submitted_intent_store(tmp_path / "draft.sqlite")
    store.append(
        "pick_verified_and_observed",
        {"intent_id": "intent-1", "overall_pick": 1, "player_id": "alpha-rb", "team": "team-1", "next_team": "team-2"},
        idempotency_key="verify",
    )
    with pytest.raises(InvalidEventError, match="submitted"):
        replay(store.events())


def test_verified_pick_is_one_atomic_replay_transition(tmp_path):
    store = _submitted_intent_store(tmp_path / "draft.sqlite")
    store.append("intent_submitted", {"intent_id": "intent-1"}, idempotency_key="submitted")
    before = len(store.events())
    store.append(
        "pick_verified_and_observed",
        {"intent_id": "intent-1", "overall_pick": 1, "player_id": "alpha-rb", "team": "team-1", "next_team": "team-2"},
        idempotency_key="verified-observed",
    )
    assert len(store.events()) == before + 1
    state = replay(store.events())
    assert state.current_pick == 2
    assert state.last_verified_pick == 1
    assert state.outstanding_intent_id is None
    assert state.reconciled is False


def test_failed_verification_is_one_atomic_takeover_transition(tmp_path):
    store = _submitted_intent_store(tmp_path / "draft.sqlite")
    store.append("intent_submitted", {"intent_id": "intent-1"}, idempotency_key="submitted")
    before = len(store.events())
    store.append(
        "verification_failed_takeover",
        {"intent_id": "intent-1", "reason": "mismatch"},
        idempotency_key="failed",
    )
    assert len(store.events()) == before + 1
    state = replay(store.events())
    assert state.control_state.value == "takeover"
    assert state.outstanding_intent_id is None


def test_sqlite_idempotency_and_replay(tmp_path):
    store = _create_store(tmp_path / "draft.sqlite")
    initialized = store.append(
        "initialized",
        {"current_pick": 1, "current_team": "team-1", "rosters": {"team-1": []}, "unavailable": [], "queue": []},
        idempotency_key="init",
    )
    repeated = store.append(
        "initialized",
        {"current_pick": 1, "current_team": "team-1", "rosters": {"team-1": []}, "unavailable": [], "queue": []},
        idempotency_key="init",
    )
    assert repeated.event_hash == initialized.event_hash
    state = replay(store.events())
    assert state.current_pick == 1
    assert state_hash(state) == state_hash(replay(store.events()))


def test_reused_idempotency_key_with_other_payload_is_rejected(tmp_path):
    store = _create_store(tmp_path / "draft.sqlite")
    store.append("disarmed", {"reason": "one"}, idempotency_key="same")
    with pytest.raises(EventConflictError):
        store.append("disarmed", {"reason": "two"}, idempotency_key="same")


def test_hash_chain_corruption_is_detected(tmp_path):
    path = tmp_path / "draft.sqlite"
    store = _create_store(path)
    store.append("disarmed", {"reason": "safe"}, idempotency_key="one")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE draft_events SET payload_json = ? WHERE sequence = 1", ('{"reason":"tampered"}',))
    with pytest.raises(CorruptEventLogError):
        store.events()


def test_pick_replay_rejects_duplicate_player(tmp_path):
    store = _create_store(tmp_path / "draft.sqlite")
    store.append(
        "initialized",
        {"current_pick": 1, "current_team": "team-1", "rosters": {"team-1": []}, "unavailable": [], "queue": []},
        idempotency_key="init",
    )
    store.append(
        "pick_observed",
        {"overall_pick": 1, "player_id": "alpha-rb", "team": "team-1", "next_team": "team-2"},
        idempotency_key="pick-1",
    )
    state = replay(store.events())
    assert state.current_pick == 2
    assert "alpha-rb" in state.unavailable
    assert state.reconciled is False


def test_configured_keeper_can_advance_its_occupied_pick(tmp_path):
    store = _create_store(tmp_path / "draft.sqlite")
    store.append(
        "initialized",
        {
            "current_pick": 49,
            "current_team": "team-1",
            "rosters": {"team-1": []},
            "unavailable": ["treveyon-henderson"],
            "queue": [],
        },
        idempotency_key="init",
    )
    store.append(
        "pick_observed",
        {
            "overall_pick": 49,
            "player_id": "treveyon-henderson",
            "team": "team-1",
            "next_team": "team-8",
            "keeper": True,
        },
        idempotency_key="keeper-49",
    )
    state = replay(store.events())
    assert state.current_pick == 50
    assert state.rosters["team-1"] == ("treveyon-henderson",)
