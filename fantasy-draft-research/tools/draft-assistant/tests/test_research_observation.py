from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from fantasy_draft_assistant.models import (
    CompiledPlayer,
    DraftPlatform,
    ObservedDraftState,
    ObservedYahooState,
    PlayerEvidence,
    SignalRole,
    SourceArtifact,
)
from fantasy_draft_assistant.importers import load_players
from fantasy_draft_assistant.research import (
    ResearchBundle,
    bundle_from_json,
    bundle_to_json,
    fetch_boris_tiers,
    fetch_sleeper_draft,
    fetch_sleeper_players,
)


NOW = datetime(2026, 8, 31, 18, tzinfo=timezone.utc)
ROOM = "room-fp:" + "a" * 64


class _Response:
    def __init__(self, payload: bytes, headers=None):
        self.payload = payload
        self.headers = headers or {}
        self.closed = False

    def read(self):
        return self.payload

    def close(self):
        self.closed = True


def test_boris_adapter_uses_injected_transport_and_canonical_identity_resolver():
    response = _Response(
        b"Rank,Player.Name,Tier,Position,Best.Rank,Worst.Rank,Avg.Rank,Std.Dev\n"
        b"1,Example Runner,2,RB,1,4,2.5,1.0\n",
        {"Last-Modified": "Mon, 31 Aug 2026 17:00:00 GMT"},
    )
    seen = []

    def open_fake(url):
        seen.append(url)
        return response

    bundle = fetch_boris_tiers(
        "standard",
        opener=open_fake,
        now=NOW,
        identity_resolver=lambda name, position: ("runner-id", "DET"),
    )
    assert seen == ["https://s3-us-west-1.amazonaws.com/fftiers/out/weekly-ALL.csv"]
    assert response.closed
    assert bundle.artifacts[0].upstream_family == "fantasypros-consensus"
    assert bundle.evidence[0].player_id == "runner-id"
    assert bundle.evidence[0].tier == 2
    assert not bundle.evidence[0].ambiguous


def test_sleeper_adapter_is_read_only_and_preserves_platform_player_ids():
    payload = json.dumps(
        {
            "123": {
                "full_name": "Example Receiver",
                "team": "DET",
                "position": "WR",
                "active": True,
                "status": "Active",
            },
            "idp": {
                "full_name": "Example Linebacker",
                "team": "DET",
                "position": "LB",
                "active": True,
            },
        }
    ).encode()
    response = _Response(payload)
    bundle = fetch_sleeper_players(opener=lambda _: response, now=NOW)
    assert response.closed
    assert {item.signal_role for item in bundle.evidence} == {SignalRole.IDENTITY, SignalRole.STATUS}
    assert {item.player_id for item in bundle.evidence} == {"123"}
    assert all(item.player_id != "idp" for item in bundle.evidence)


def test_sleeper_draft_adapter_reads_documented_endpoints_and_sanitizes_output():
    room_payload = json.dumps(
        {
            "draft_id": "draft_123",
            "status": "drafting",
            "type": "snake",
            "season": "2026",
            "sport": "nfl",
            "start_time": 1_788_000_000_000,
            "last_picked": 2,
            "settings": {"teams": 2, "rounds": 4, "pick_timer": 60, "private": "drop"},
            "slot_to_roster_id": {"1": 11, "2": 12},
            "league_id": "not-retained",
            "creators": ["user-id"],
            "metadata": {"private": "not-retained"},
        }
    ).encode()
    picks_payload = json.dumps(
        [
            {
                "player_id": "player-2",
                "pick_no": 2,
                "round": 1,
                "draft_slot": 2,
                "roster_id": "12",
                "picked_by": "user-id",
                "metadata": {
                    "first_name": "Player",
                    "last_name": "Two",
                    "team": "DET",
                    "position": "WR",
                    "secret": "drop",
                },
            },
            {
                "player_id": "player-1",
                "pick_no": 1,
                "round": 1,
                "draft_slot": 1,
                "roster_id": 11,
                "metadata": {
                    "first_name": "Player",
                    "last_name": "One",
                    "team": "GB",
                    "position": "RB",
                },
            },
        ]
    ).encode()
    responses = []
    seen = []

    def open_fake(url):
        seen.append(url)
        response = _Response(picks_payload if url.endswith("/picks") else room_payload)
        responses.append(response)
        return response

    snapshot = fetch_sleeper_draft("draft_123", opener=open_fake, now=NOW)
    assert seen == [
        "https://api.sleeper.app/v1/draft/draft_123",
        "https://api.sleeper.app/v1/draft/draft_123/picks",
    ]
    assert all(response.closed for response in responses)
    assert [pick.player_id for pick in snapshot.picks] == ["player-1", "player-2"]
    sanitized = snapshot.to_dict()
    assert sanitized["room"]["settings"] == {"pick_timer": 60, "rounds": 4, "teams": 2}
    assert "league_id" not in sanitized["room"]
    assert "picked_by" not in sanitized["picks"][0]
    assert sanitized["picks"][1]["name"] == "Player Two"


def test_sleeper_draft_adapter_rejects_url_shaping_draft_ids_before_transport():
    called = False

    def should_not_open(_):
        nonlocal called
        called = True
        raise AssertionError("transport should not be called")

    with pytest.raises(ValueError, match="unsupported characters"):
        fetch_sleeper_draft("../private?token=x", opener=should_not_open, now=NOW)
    assert not called


def test_canonical_research_bundle_round_trip_is_lossless():
    response = _Response(
        b"Rank,Player.Name,Tier,Position\n1,Example Runner,1,RB\n",
        {"Last-Modified": "Mon, 31 Aug 2026 17:00:00 GMT"},
    )
    bundle = fetch_boris_tiers("standard", opener=lambda _: response, now=NOW)
    restored = bundle_from_json(bundle_to_json(bundle))
    assert restored == bundle
    assert isinstance(restored, ResearchBundle)


@pytest.mark.parametrize("platform", ["yahoo", "espn", "sleeper"])
def test_platform_neutral_observation_round_trip(platform):
    value = {
        "platform": platform,
        "adapter_version": "2026.08.31",
        "room_fingerprint": ROOM,
        "phase": "in_progress",
        "your_turn": True,
        "current_team": "ours",
        "overall_pick": 9,
        "clock_seconds": 42,
        "roster_count": 1,
        "roster_player_ids": ["one"],
        "unavailable_player_ids": ["one", "other"],
        "queue_player_ids": ["two", "three"],
        "rows": [
            {
                "player_id": "two",
                "name": "Player Two",
                "nfl_team": "DET",
                "position": "RB",
                "available": True,
                "has_draft_control": True,
                "ambiguous": False,
            }
        ],
        "autodraft_off": True,
        "captured_at": NOW.isoformat(),
        "authentication_challenge": False,
        "modal_ambiguity": False,
        "reconnecting": False,
        "control_interrupted": False,
        "control_status": "ready",
        "positional_demand": {"RB": 2},
        "last_pick_evidence": {
            "player_id": "other",
            "position": "WR",
            "overall_pick": 8,
            "room_advanced": True,
        },
    }
    observed = ObservedDraftState.from_dict(value)
    assert observed.platform is DraftPlatform(platform)
    assert observed.visible_players[0].player_id == "two"
    assert observed.last_pick_evidence["overall_pick"] == 8
    assert ObservedDraftState.from_dict(observed.to_dict()) == observed


def test_yahoo_compatibility_alias_uses_explicit_platform_contract():
    value = {
        "platform": "yahoo",
        "adapter_version": "1",
        "phase": "in_progress",
        "control_status": "ready",
        "room_fingerprint": ROOM,
        "your_turn": False,
        "current_team": "other",
        "overall_pick": 2,
        "clock_seconds": 20,
        "roster_count": 0,
        "rows": [],
        "queue_player_ids": [],
        "unavailable_player_ids": [],
        "roster_player_ids": [],
        "autodraft_off": True,
        "captured_at": NOW.isoformat(),
        "authentication_challenge": False,
        "modal_ambiguity": False,
        "reconnecting": False,
        "control_interrupted": False,
    }
    assert ObservedYahooState.from_dict(value).platform is DraftPlatform.YAHOO


@pytest.mark.parametrize(
    "missing_field",
    [
        "authentication_challenge",
        "modal_ambiguity",
        "reconnecting",
        "control_interrupted",
    ],
)
def test_observation_json_requires_explicit_control_evidence(missing_field):
    value = {
        "platform": "yahoo",
        "adapter_version": "1",
        "phase": "in_progress",
        "control_status": "ready",
        "room_fingerprint": "room",
        "your_turn": True,
        "current_team": "ours",
        "overall_pick": 1,
        "clock_seconds": 30,
        "roster_count": 0,
        "rows": [],
        "queue_player_ids": [],
        "unavailable_player_ids": [],
        "roster_player_ids": [],
        "autodraft_off": True,
        "captured_at": NOW.isoformat(),
        "authentication_challenge": False,
        "modal_ambiguity": False,
        "reconnecting": False,
        "control_interrupted": False,
    }
    del value[missing_field]

    with pytest.raises(ValueError, match="missing required control evidence"):
        ObservedDraftState.from_dict(value)


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("your_turn", "true"),
        ("autodraft_off", 1),
        ("authentication_challenge", "false"),
        ("modal_ambiguity", 0),
        ("reconnecting", None),
        ("control_interrupted", "no"),
        ("room_advanced", "true"),
    ],
)
def test_observation_json_rejects_non_boolean_control_values(field_name, bad_value):
    value = {
        "platform": "yahoo",
        "adapter_version": "1",
        "phase": "in_progress",
        "control_status": "ready",
        "room_fingerprint": "room",
        "your_turn": True,
        "current_team": "ours",
        "overall_pick": 1,
        "clock_seconds": 30,
        "roster_count": 0,
        "rows": [],
        "queue_player_ids": [],
        "unavailable_player_ids": [],
        "roster_player_ids": [],
        "autodraft_off": True,
        "captured_at": NOW.isoformat(),
        "authentication_challenge": False,
        "modal_ambiguity": False,
        "reconnecting": False,
        "control_interrupted": False,
    }
    value[field_name] = bad_value

    with pytest.raises(ValueError, match="must be boolean"):
        ObservedDraftState.from_dict(value)


@pytest.mark.parametrize("field_name", ["available", "has_draft_control", "ambiguous"])
def test_observed_player_json_rejects_non_boolean_fields(field_name):
    value = {
        "player_id": "player",
        "name": "Player",
        "nfl_team": "DET",
        "position": "RB",
        "available": True,
        "has_draft_control": True,
        "ambiguous": False,
    }
    value[field_name] = "false"
    observed = {
        "platform": "yahoo",
        "adapter_version": "1",
        "phase": "in_progress",
        "control_status": "ready",
        "room_fingerprint": "room",
        "your_turn": True,
        "current_team": "ours",
        "overall_pick": 1,
        "clock_seconds": 30,
        "roster_count": 0,
        "rows": [value],
        "queue_player_ids": [],
        "unavailable_player_ids": [],
        "roster_player_ids": [],
        "autodraft_off": True,
        "captured_at": NOW.isoformat(),
        "authentication_challenge": False,
        "modal_ambiguity": False,
        "reconnecting": False,
        "control_interrupted": False,
    }

    with pytest.raises(ValueError, match="must be boolean"):
        ObservedDraftState.from_dict(observed)


def test_observation_and_provenance_reject_private_or_raw_browser_fields():
    with pytest.raises(ValueError, match="forbidden field"):
        ObservedDraftState.from_dict({"cookie": "secret"})
    value = {
        "platform": "yahoo",
        "room_fingerprint": "room",
        "your_turn": True,
        "current_team": "ours",
        "overall_pick": 1,
        "clock_seconds": 30,
        "roster_count": 0,
        "rows": [],
        "autodraft_off": True,
        "captured_at": NOW.isoformat(),
        "raw_dom": "not allowed",
    }
    with pytest.raises(ValueError, match="forbidden field"):
        ObservedDraftState.from_dict(value)
    artifact = {
        "source": "manual",
        "upstream_family": "manual",
        "signal_role": "tier",
        "scoring_context": "standard",
        "acquisition_method": "manual_snapshot",
        "published_at": NOW.isoformat(),
        "retrieved_at": NOW.isoformat(),
        "checksum": "a" * 64,
        "freshness_hours": 72,
        "safe_provenance": {"access_token": "secret"},
    }
    with pytest.raises(ValueError, match="forbidden field"):
        SourceArtifact.from_dict(artifact)


def test_canonical_json_boolean_fields_reject_string_coercion():
    artifact = SourceArtifact(
        source="manual",
        upstream_family="manual",
        signal_role=SignalRole.TIER,
        scoring_context="standard",
        acquisition_method="manual_snapshot",
        published_at=NOW,
        retrieved_at=NOW,
        checksum="a" * 64,
        freshness_hours=72,
    ).to_dict()
    artifact["mandatory"] = "true"
    with pytest.raises(ValueError, match="artifact.mandatory must be boolean"):
        SourceArtifact.from_dict(artifact)

    evidence = {
        "player_id": "player",
        "name": "Player",
        "nfl_team": "DET",
        "position": "RB",
        "signal_role": "tier",
        "artifact_checksum": "a" * 64,
        "tier": 1,
        "ambiguous": "false",
    }
    with pytest.raises(ValueError, match="evidence.ambiguous must be boolean"):
        PlayerEvidence.from_dict(evidence)

    compiled = CompiledPlayer(
        player_id="player",
        name="Player",
        nfl_team="DET",
        position="RB",
        projected_points=20,
        replacement_baseline=10,
        vbd=10,
        independent_tier=1,
        platform_adps={},
        compiled_at=NOW,
    ).to_dict()
    compiled["ambiguous"] = "false"
    with pytest.raises(ValueError, match="compiled_player.ambiguous must be boolean"):
        CompiledPlayer.from_dict(compiled)


def test_legacy_csv_ambiguous_column_is_strict(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "players.csv"
    text = fixture.read_text(encoding="utf-8")
    invalid = tmp_path / "players.csv"
    invalid.write_text(text.replace("false", "maybe", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="ambiguous must be true/false"):
        load_players(invalid)
