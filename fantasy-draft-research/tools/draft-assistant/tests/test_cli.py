from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from fantasy_draft_assistant.cli import main
from fantasy_draft_assistant.importers import load_compiled_board, load_league, load_players
from fantasy_draft_assistant.models import AcquisitionMethod, PlayerEvidence, SignalRole, SourceArtifact
from fantasy_draft_assistant.optimizer import board_hash, config_hash
from fantasy_draft_assistant.platforms import (
    ChromeOperation,
    ControlCardinality,
    LocatorKind,
    get_platform_mapping,
)
from fantasy_draft_assistant.research import ResearchBundle


FIXTURES = Path(__file__).parent / "fixtures"
ROOM_FINGERPRINT = "room-fp:" + ("a" * 64)
OFFLINE_ROOM_FINGERPRINT = "room-fp:" + ("b" * 64)


def _control_snapshot(
    platform: str,
    adapter_version: str,
    operation: ChromeOperation,
) -> dict[str, object]:
    """Build sanitized semantic-control evidence from the versioned mapping."""

    mapping = get_platform_mapping(platform, adapter_version)
    controls: dict[str, list[dict[str, object]]] = {}
    for contract in mapping.controls_for(operation):
        if contract.cardinality is ControlCardinality.ABSENT:
            controls[contract.semantic_name] = []
            continue
        locator = contract.locators[0]
        match: dict[str, object] = {
            "locator_kind": locator.kind.value,
            "visible": True,
            "enabled": True,
            "ambiguous": False,
        }
        if locator.kind is LocatorKind.ACCESSIBLE_ROLE:
            match.update(
                {
                    "role": locator.role,
                    "accessible_name": " ".join(locator.name_tokens),
                }
            )
        else:
            match.update(
                {
                    "attribute_name": locator.attribute_name,
                    "attribute_value": " ".join(locator.value_tokens),
                }
            )
        controls[contract.semantic_name] = [match]
    return {
        "platform": platform,
        "mapping_version": adapter_version,
        "operation": operation.value,
        "controls": controls,
    }


def _set_control_snapshots(
    observed: dict[str, object],
    *operations: ChromeOperation,
) -> None:
    platform = str(observed["platform"])
    adapter_version = str(observed["adapter_version"])
    observed["control_snapshots"] = [
        _control_snapshot(platform, adapter_version, operation)
        for operation in operations
    ]


def _write_observed(
    path: Path,
    observed: dict[str, object],
    *operations: ChromeOperation,
) -> None:
    observed["captured_at"] = datetime.now(timezone.utc).isoformat()
    _set_control_snapshots(observed, *operations)
    path.write_text(json.dumps(observed), encoding="utf-8")


def _bind_run_hashes(observed: dict[str, object]) -> None:
    observed["config_hash"] = config_hash(load_league(FIXTURES / "league.json"))
    observed["board_hash"] = board_hash(load_players(FIXTURES / "players.csv"))


def test_cli_init_doctor_recommend_replay(tmp_path, capsys):
    run = tmp_path / "run"
    assert main(["init", "--league", str(FIXTURES / "league.json"), "--players", str(FIXTURES / "players.csv"), "--run", str(run)]) == 0
    capsys.readouterr()
    assert main(["doctor", "--run", str(run), "--json"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["ok"] is True
    assert doctor["real_automatic_ready"] is False
    # The readiness file is intentionally absent: code readiness is not live-use proof.
    assert not (run / "rehearsal-readiness.json").exists()
    observed = json.loads((FIXTURES / "yahoo_state.json").read_text(encoding="utf-8"))
    _bind_run_hashes(observed)
    observed_path = tmp_path / "observed.json"
    _write_observed(observed_path, observed, ChromeOperation.OBSERVE)
    assert main(["reconcile", "--run", str(run), "--observed-state", str(observed_path)]) == 0
    assert main(["recommend", "--run", str(run), "--top", "3", "--json"]) == 0
    assert main(["queue", "--run", str(run), "--json"]) == 0
    assert main(["replay", "--run", str(run), "--json"]) == 0


def test_recommendation_is_pure_and_does_not_claim_queue_change(tmp_path, capsys):
    run = tmp_path / "run"
    assert main(["init", "--league", str(FIXTURES / "league.json"), "--players", str(FIXTURES / "players.csv"), "--run", str(run)]) == 0
    capsys.readouterr()
    observed = json.loads((FIXTURES / "yahoo_state.json").read_text(encoding="utf-8"))
    _bind_run_hashes(observed)
    observed_path = tmp_path / "observed.json"
    _write_observed(observed_path, observed, ChromeOperation.OBSERVE)
    assert main(["reconcile", "--run", str(run), "--observed-state", str(observed_path)]) == 0
    capsys.readouterr()
    assert main(["status", "--run", str(run), "--json"]) == 0
    before = json.loads(capsys.readouterr().out)

    assert main(["recommend", "--run", str(run), "--json"]) == 0
    capsys.readouterr()
    assert main(["status", "--run", str(run), "--json"]) == 0
    after = json.loads(capsys.readouterr().out)

    assert after["event_count"] == before["event_count"]
    assert after["state_hash"] == before["state_hash"]
    assert after["state"]["queue"] == []


def test_turn_accepts_sanitized_observation_over_stdin(tmp_path, capsys, monkeypatch):
    run = tmp_path / "run"
    assert main(["init", "--league", str(FIXTURES / "league.json"), "--players", str(FIXTURES / "players.csv"), "--run", str(run)]) == 0
    capsys.readouterr()
    observed = json.loads((FIXTURES / "yahoo_state.json").read_text(encoding="utf-8"))
    _bind_run_hashes(observed)
    observed["captured_at"] = datetime.now(timezone.utc).isoformat()
    _set_control_snapshots(observed, ChromeOperation.OBSERVE)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(observed)))

    assert main(["turn", "--run", str(run), "--observed-state", "-", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["platform"] == "yahoo"
    assert result["primary"] in result["recommendation"]["top_three"]
    assert result["observation_hash"]


def test_real_arm_is_rehearsal_blocked(tmp_path):
    run = tmp_path / "run"
    assert main(["init", "--league", str(FIXTURES / "league.json"), "--players", str(FIXTURES / "players.csv"), "--run", str(run)]) == 0
    assert main(["arm", "--run", str(run), "--mode", "real", "--room-fingerprint", ROOM_FINGERPRINT]) == 2


def test_real_acknowledgement_is_a_separate_transition(tmp_path):
    run = tmp_path / "run"
    assert main(["init", "--league", str(FIXTURES / "league.json"), "--players", str(FIXTURES / "players.csv"), "--run", str(run)]) == 0
    (run / "rehearsal-readiness.json").write_text(
        json.dumps(
            {
                "platforms": {
                    "yahoo": {
                        "timed_mock_passed": True,
                        "zero_wrong_duplicate_ambiguous_actions": True,
                        "takeover_witnessed": True,
                        "queue_fallback_witnessed": True,
                        "no_external_calls_after_room_open": True,
                        "timer_expiry_classified_as_autodraft": True,
                        "recommendation_p99_ms": 100,
                        "mapping_version": "1",
                        "witnessed_at": datetime.now(timezone.utc).isoformat(),
                        "evidence_reference": "synthetic/yahoo-timed-mock.json",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert main(["arm", "--run", str(run), "--mode", "real", "--room-fingerprint", ROOM_FINGERPRINT]) == 2
    observed = json.loads((FIXTURES / "yahoo_state.json").read_text(encoding="utf-8"))
    _bind_run_hashes(observed)
    observed["room_fingerprint"] = ROOM_FINGERPRINT
    observed_path = tmp_path / "real-observed.json"
    _write_observed(observed_path, observed, ChromeOperation.OBSERVE)
    assert main(["reconcile", "--run", str(run), "--observed-state", str(observed_path)]) == 0
    assert main(["acknowledge-real", "--run", str(run), "--room-fingerprint", ROOM_FINGERPRINT]) == 0
    assert main(["arm", "--run", str(run), "--mode", "real", "--room-fingerprint", ROOM_FINGERPRINT]) == 0


def test_observed_state_rejects_private_fields(tmp_path):
    run = tmp_path / "run"
    assert main(["init", "--league", str(FIXTURES / "league.json"), "--players", str(FIXTURES / "players.csv"), "--run", str(run)]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"token": "forbidden"}), encoding="utf-8")
    assert main(["reconcile", "--run", str(run), "--observed-state", str(bad)]) == 2


def test_observed_state_rejects_unknown_nested_private_fields(tmp_path):
    run = tmp_path / "run"
    assert main(["init", "--league", str(FIXTURES / "league.json"), "--players", str(FIXTURES / "players.csv"), "--run", str(run)]) == 0
    value = json.loads((FIXTURES / "yahoo_state.json").read_text(encoding="utf-8"))
    value["captured_at"] = datetime.now(timezone.utc).isoformat()
    value["rows"][0]["metadata"] = {"access_token": "forbidden"}
    path = tmp_path / "nested-private.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert main(["reconcile", "--run", str(run), "--observed-state", str(path)]) == 2


def test_cli_returns_actionable_error_for_missing_store(tmp_path, capsys):
    run = tmp_path / "run"
    assert main(["init", "--league", str(FIXTURES / "league.json"), "--players", str(FIXTURES / "players.csv"), "--run", str(run)]) == 0
    missing = tmp_path / "missing-store"
    missing.mkdir()
    for name in ("league.json", "players.json", "source-manifest.json"):
        (missing / name).write_text((run / name).read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["status", "--run", str(missing), "--json"]) == 2
    assert "not provisioned" in capsys.readouterr().err


def _all_visible_rows() -> list[dict[str, object]]:
    with (FIXTURES / "players.csv").open(encoding="utf-8", newline="") as stream:
        return [
            {
                "player_id": row["player_id"],
                "name": row["name"],
                "nfl_team": row["nfl_team"],
                "position": row["position"],
                "available": row["status"] != "keeper",
                "has_draft_control": row["status"] != "keeper",
                "ambiguous": False,
            }
            for row in csv.DictReader(stream)
            if row["status"] != "keeper"
        ]


def _initialize_armed_mock(tmp_path, capsys):
    run = tmp_path / "run"
    assert main(["init", "--league", str(FIXTURES / "league.json"), "--players", str(FIXTURES / "players.csv"), "--run", str(run)]) == 0
    capsys.readouterr()
    observed_path = tmp_path / "observed.json"
    observed = json.loads((FIXTURES / "yahoo_state.json").read_text(encoding="utf-8"))
    _bind_run_hashes(observed)
    observed["rows"] = _all_visible_rows()
    _write_observed(observed_path, observed, ChromeOperation.OBSERVE)
    assert main(["reconcile", "--run", str(run), "--observed-state", str(observed_path)]) == 0
    assert main(["arm", "--run", str(run), "--mode", "mock", "--room-fingerprint", ROOM_FINGERPRINT]) == 0
    _write_observed(observed_path, observed, ChromeOperation.OBSERVE)
    assert main(["turn", "--run", str(run), "--observed-state", str(observed_path), "--json"]) == 0
    capsys.readouterr()
    recommendation = json.loads((run / "latest-recommendation.json").read_text(encoding="utf-8"))
    _write_observed(observed_path, observed, ChromeOperation.OBSERVE)
    capsys.readouterr()
    return run, observed_path, observed, recommendation


def _approve_current_primary(run, observed_path, recommendation, capsys):
    top_three = recommendation["recommendation"]["top_three"]
    assert main(
        [
            "approve-pick", "--run", str(run), "--observed-state", str(observed_path),
            "--player-id", top_three[0], "--queue", *top_three,
        ]
    ) == 0
    approved = json.loads(capsys.readouterr().out)
    return approved["intent"]


def _mark_current_intent_submitted(run, observed_path, observed, intent, capsys):
    observed["queue_player_ids"] = list(intent["approved_queue"])
    _write_observed(
        observed_path,
        observed,
        ChromeOperation.QUEUE,
        ChromeOperation.PICK,
    )
    assert main(
        [
            "mark-submitted", "--run", str(run), "--intent-id", intent["intent_id"],
            "--observed-state", str(observed_path),
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "submission_attempt_recorded"
    assert result["submission_provenance"] == "manager_approved_chrome_attempt_unverified"


def test_issued_intent_can_be_cancelled(tmp_path, capsys):
    run, observed_path, _, recommendation = _initialize_armed_mock(tmp_path, capsys)
    intent = _approve_current_primary(run, observed_path, recommendation, capsys)

    assert main(
        [
            "cancel-intent", "--run", str(run), "--intent-id", intent["intent_id"],
            "--reason", "manager changed the approved branch",
        ]
    ) == 0
    cancelled = json.loads(capsys.readouterr().out)
    assert cancelled["status"] == "cancelled"
    assert cancelled["retry_allowed"] is False
    assert main(["status", "--run", str(run), "--json"]) == 0
    state = json.loads(capsys.readouterr().out)["state"]
    assert state["outstanding_intent_id"] is None


def test_submitted_intent_cannot_be_cancelled(tmp_path, capsys):
    run, observed_path, observed, recommendation = _initialize_armed_mock(tmp_path, capsys)
    intent = _approve_current_primary(run, observed_path, recommendation, capsys)
    _mark_current_intent_submitted(run, observed_path, observed, intent, capsys)

    assert main(
        [
            "cancel-intent", "--run", str(run), "--intent-id", intent["intent_id"],
            "--reason", "too late",
        ]
    ) == 2
    assert main(["status", "--run", str(run), "--json"]) == 0
    state = json.loads(capsys.readouterr().out)["state"]
    assert state["outstanding_intent_status"] == "submitted"


def test_mock_intent_submit_and_four_signal_verification(tmp_path, capsys):
    run, observed_path, observed, recommendation = _initialize_armed_mock(tmp_path, capsys)
    intent = _approve_current_primary(run, observed_path, recommendation, capsys)
    _mark_current_intent_submitted(run, observed_path, observed, intent, capsys)

    observed.update(
        {
            "your_turn": False,
            "overall_pick": 2,
            "current_team": "team-2",
            "roster_count": 2,
            "rows": [],
            "queue_player_ids": [],
            "roster_player_ids": ["treveyon-henderson", intent["player_id"]],
            "unavailable_player_ids": ["treveyon-henderson", intent["player_id"]],
            "last_pick_player_id": intent["player_id"],
            "last_pick_position": intent["position"],
            "last_pick_overall": 1,
            "room_advanced": True,
            "last_pick_provenance": "manager-approved-chrome",
            "last_pick_timer_expired": False,
        }
    )
    _write_observed(observed_path, observed, ChromeOperation.VERIFY)
    assert main(["verify-pick", "--run", str(run), "--intent-id", intent["intent_id"], "--observed-state", str(observed_path)]) == 0
    capsys.readouterr()
    assert main(["status", "--run", str(run), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"]["current_pick"] == 2
    assert status["state"]["last_verified_pick"] == 1
    assert intent["player_id"] in status["state"]["unavailable"]
    assert status["state"]["outstanding_intent_id"] is None


def test_failed_verification_disarms_and_requires_takeover(tmp_path, capsys):
    run, observed_path, observed, recommendation = _initialize_armed_mock(tmp_path, capsys)
    intent = _approve_current_primary(run, observed_path, recommendation, capsys)
    _mark_current_intent_submitted(run, observed_path, observed, intent, capsys)
    observed.update(
        {
            "overall_pick": 2,
            "current_team": "team-2",
            "roster_count": 1,
            "queue_player_ids": [],
            "last_pick_player_id": "wrong-player",
            "last_pick_position": "WR",
            "last_pick_overall": 1,
            "room_advanced": True,
            "last_pick_provenance": "manager-approved-chrome",
            "last_pick_timer_expired": False,
        }
    )
    _write_observed(observed_path, observed, ChromeOperation.VERIFY)
    assert main(["verify-pick", "--run", str(run), "--intent-id", intent["intent_id"], "--observed-state", str(observed_path)]) == 4
    capsys.readouterr()
    assert main(["status", "--run", str(run), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"]["control_state"] == "takeover"
    assert status["state"]["outstanding_intent_id"] is None


def test_full_offline_research_compile_turn_approval_and_verification(
    tmp_path, capsys, monkeypatch
):
    now = datetime.now(timezone.utc)
    league_value = {
        "active_teams": 2,
        "maximum_teams": 2,
        "draft_slots": ["ours", "other"],
        "rounds": 4,
        "draft_position": 1,
        "pick_clock_seconds": 60,
        "our_team": "ours",
        "roster": {"RB": 1, "WR": 1, "FLEX": 1, "BENCH": 1},
        "flex_positions": ["RB", "WR", "TE"],
        "keepers": [],
        "scoring_format": "standard",
        "mandatory_freshness_hours": 72,
    }
    league_path = tmp_path / "league.json"
    league_path.write_text(json.dumps(league_value), encoding="utf-8")
    projection = SourceArtifact(
        source="synthetic-projections",
        upstream_family="synthetic-projection-family",
        signal_role=SignalRole.PROJECTION,
        scoring_context="standard",
        acquisition_method=AcquisitionMethod.MANUAL_SNAPSHOT,
        published_at=now,
        retrieved_at=now,
        checksum=hashlib.sha256(b"synthetic-projections").hexdigest(),
        freshness_hours=72,
        safe_provenance={"adapter": "synthetic-test-v1"},
        mandatory=True,
    )
    tier = SourceArtifact(
        source="synthetic-consensus-tiers",
        upstream_family="fantasypros-consensus",
        signal_role=SignalRole.TIER,
        scoring_context="standard",
        acquisition_method=AcquisitionMethod.MANUAL_SNAPSHOT,
        published_at=now,
        retrieved_at=now,
        checksum=hashlib.sha256(b"synthetic-consensus-tiers").hexdigest(),
        freshness_hours=72,
        safe_provenance={"adapter": "synthetic-test-v1"},
    )
    yahoo_adp = SourceArtifact(
        source="synthetic-yahoo-adp",
        upstream_family="yahoo",
        signal_role=SignalRole.ADP,
        scoring_context=None,
        acquisition_method=AcquisitionMethod.CHROME_SNAPSHOT,
        published_at=now,
        retrieved_at=now,
        checksum=hashlib.sha256(b"synthetic-yahoo-adp").hexdigest(),
        freshness_hours=24,
        safe_provenance={"adapter": "sanitized-chrome-test-v1"},
    )
    specs = [
        ("rb-1", "Runner One", "DET", "RB", 30.0, 1, 1.0),
        ("rb-2", "Runner Two", "GB", "RB", 25.0, 2, 4.0),
        ("rb-3", "Runner Three", "CHI", "RB", 19.0, 3, 7.0),
        ("rb-4", "Runner Four", "MIN", "RB", 10.0, 4, 10.0),
        ("wr-1", "Receiver One", "DAL", "WR", 28.0, 1, 2.0),
        ("wr-2", "Receiver Two", "SEA", "WR", 24.0, 2, 5.0),
        ("wr-3", "Receiver Three", "LAR", "WR", 17.0, 3, 8.0),
        ("wr-4", "Receiver Four", "NYG", "WR", 9.0, 4, 11.0),
    ]
    evidence = []
    for player_id, name, team, position, points, player_tier, adp in specs:
        evidence.extend(
            [
                PlayerEvidence(
                    player_id=player_id,
                    name=name,
                    nfl_team=team,
                    position=position,
                    signal_role=SignalRole.PROJECTION,
                    artifact_checksum=projection.checksum,
                    projected_stats={"receiving_yards": points * 10},
                ),
                PlayerEvidence(
                    player_id=player_id,
                    name=name,
                    nfl_team=team,
                    position=position,
                    signal_role=SignalRole.TIER,
                    artifact_checksum=tier.checksum,
                    tier=player_tier,
                ),
                PlayerEvidence(
                    player_id=player_id,
                    name=name,
                    nfl_team=team,
                    position=position,
                    signal_role=SignalRole.ADP,
                    artifact_checksum=yahoo_adp.checksum,
                    platform="yahoo",
                    adp=adp,
                ),
            ]
        )
    bundle = ResearchBundle((projection, tier, yahoo_adp), tuple(evidence))
    research_path = tmp_path / "research.json"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(bundle.to_dict())))
    assert main(
        ["research-import", "--snapshot", "-", "--output", str(research_path)]
    ) == 0
    capsys.readouterr()

    board_dir = tmp_path / "board-v1"
    assert main(
        [
            "compile-board", "--league", str(league_path), "--research", str(research_path),
            "--output", str(board_dir),
        ]
    ) == 0
    capsys.readouterr()
    assert {path.name for path in board_dir.iterdir()} == {
        "board.json", "board.csv", "manifest.json", "cheat-sheet.txt"
    }
    compiled_players, _ = load_compiled_board(board_dir / "board.json")

    run = tmp_path / "run"
    assert main(
        [
            "init", "--league", str(league_path), "--players", str(board_dir / "board.json"),
            "--run", str(run),
        ]
    ) == 0
    capsys.readouterr()
    config = load_league(league_path)
    observed = {
        "platform": "yahoo",
        "adapter_version": "1",
        "room_fingerprint": OFFLINE_ROOM_FINGERPRINT,
        "phase": "in_progress",
        "control_status": "ready",
        "your_turn": True,
        "overall_pick": 1,
        "current_team": "ours",
        "clock_seconds": 55,
        "roster_count": 0,
        "autodraft_off": True,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "unavailable_player_ids": [],
        "roster_player_ids": [],
        "queue_player_ids": [],
        "rows": [
            {
                "player_id": player.player_id,
                "name": player.name,
                "nfl_team": player.nfl_team,
                "position": player.position,
                "available": True,
                "has_draft_control": True,
                "ambiguous": False,
            }
            for player in compiled_players
        ],
        "authentication_challenge": False,
        "modal_ambiguity": False,
        "reconnecting": False,
        "control_interrupted": False,
        "config_hash": config_hash(config),
        "board_hash": board_hash(compiled_players),
    }
    observed_path = tmp_path / "observed.json"
    _write_observed(observed_path, observed, ChromeOperation.OBSERVE)
    assert main(
        ["reconcile", "--run", str(run), "--observed-state", str(observed_path)]
    ) == 0
    capsys.readouterr()
    assert main(
        ["arm", "--run", str(run), "--mode", "mock", "--room-fingerprint", OFFLINE_ROOM_FINGERPRINT]
    ) == 0
    capsys.readouterr()
    observed["captured_at"] = datetime.now(timezone.utc).isoformat()
    _set_control_snapshots(observed, ChromeOperation.OBSERVE)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(observed)))
    assert main(["turn", "--run", str(run), "--observed-state", "-", "--json"]) == 0
    turn = json.loads(capsys.readouterr().out)
    observed["captured_at"] = datetime.now(timezone.utc).isoformat()
    _set_control_snapshots(observed, ChromeOperation.OBSERVE)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(observed)))
    assert main(
        [
            "approve-pick", "--run", str(run), "--observed-state", "-",
            "--player-id", turn["primary"], "--queue", *turn["recommendation"]["top_three"],
        ]
    ) == 0
    intent = json.loads(capsys.readouterr().out)["intent"]
    _mark_current_intent_submitted(run, observed_path, observed, intent, capsys)
    observed.update(
        {
            "your_turn": False,
            "overall_pick": 2,
            "current_team": "other",
            "clock_seconds": 55,
                "roster_count": 1,
                "rows": [],
                "queue_player_ids": [
                    player_id
                    for player_id in observed["queue_player_ids"]
                    if player_id != intent["player_id"]
                ],
                "roster_player_ids": [intent["player_id"]],
            "unavailable_player_ids": [intent["player_id"]],
            "last_pick_player_id": intent["player_id"],
            "last_pick_position": intent["position"],
            "last_pick_overall": 1,
            "room_advanced": True,
            "last_pick_provenance": "manager-approved-chrome",
            "last_pick_timer_expired": False,
        }
    )
    _write_observed(observed_path, observed, ChromeOperation.VERIFY)
    assert main(
        [
            "verify-pick", "--run", str(run), "--intent-id", intent["intent_id"],
            "--observed-state", str(observed_path),
        ]
    ) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["verified"] is True
