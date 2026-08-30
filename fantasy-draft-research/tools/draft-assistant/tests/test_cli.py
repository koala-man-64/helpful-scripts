from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fantasy_draft_assistant.cli import main


FIXTURES = Path(__file__).parent / "fixtures"


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
    observed["captured_at"] = datetime.now(timezone.utc).isoformat()
    observed_path = tmp_path / "observed.json"
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    assert main(["reconcile", "--run", str(run), "--observed-state", str(observed_path)]) == 0
    assert main(["recommend", "--run", str(run), "--top", "3", "--json"]) == 0
    assert main(["queue", "--run", str(run), "--json"]) == 0
    assert main(["replay", "--run", str(run), "--json"]) == 0


def test_real_arm_is_rehearsal_blocked(tmp_path):
    run = tmp_path / "run"
    assert main(["init", "--league", str(FIXTURES / "league.json"), "--players", str(FIXTURES / "players.csv"), "--run", str(run)]) == 0
    assert main(["arm", "--run", str(run), "--mode", "real", "--room-fingerprint", "safe-alias"]) == 2


def test_real_acknowledgement_is_a_separate_transition(tmp_path):
    run = tmp_path / "run"
    assert main(["init", "--league", str(FIXTURES / "league.json"), "--players", str(FIXTURES / "players.csv"), "--run", str(run)]) == 0
    (run / "rehearsal-readiness.json").write_text(
        json.dumps(
            {
                "full_mock_passed": True,
                "zero_wrong_duplicate_ambiguous_actions": True,
                "takeover_witnessed": True,
                "queue_fallback_witnessed": True,
                "no_external_calls_after_room_open": True,
                "recommendation_p99_ms": 100,
            }
        ),
        encoding="utf-8",
    )
    assert main(["arm", "--run", str(run), "--mode", "real", "--room-fingerprint", "safe-alias"]) == 2
    observed = json.loads((FIXTURES / "yahoo_state.json").read_text(encoding="utf-8"))
    observed["room_fingerprint"] = "safe-alias"
    observed["captured_at"] = datetime.now(timezone.utc).isoformat()
    observed_path = tmp_path / "real-observed.json"
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    assert main(["reconcile", "--run", str(run), "--observed-state", str(observed_path)]) == 0
    assert main(["acknowledge-real", "--run", str(run), "--room-fingerprint", "safe-alias"]) == 0
    assert main(["arm", "--run", str(run), "--mode", "real", "--room-fingerprint", "safe-alias"]) == 0


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


def _initialize_armed_mock(tmp_path, capsys):
    run = tmp_path / "run"
    assert main(["init", "--league", str(FIXTURES / "league.json"), "--players", str(FIXTURES / "players.csv"), "--run", str(run)]) == 0
    capsys.readouterr()
    observed_path = tmp_path / "observed.json"
    observed = json.loads((FIXTURES / "yahoo_state.json").read_text(encoding="utf-8"))
    observed["captured_at"] = datetime.now(timezone.utc).isoformat()
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    assert main(["reconcile", "--run", str(run), "--observed-state", str(observed_path)]) == 0
    assert main(["arm", "--run", str(run), "--mode", "mock", "--room-fingerprint", "mock-room"]) == 0
    assert main(["recommend", "--run", str(run), "--top", "3", "--json"]) == 0
    capsys.readouterr()
    recommendation = json.loads((run / "latest-recommendation.json").read_text(encoding="utf-8"))
    observed["queue_player_ids"] = recommendation["recommendation"]["top_three"]
    observed["rows"] = [
        {
            "name": candidate["name"],
            "nfl_team": candidate["nfl_team"],
            "position": candidate["position"],
            "available": True,
            "has_draft_control": True,
        }
        for candidate in recommendation["candidates"]
    ]
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    capsys.readouterr()
    return run, observed_path, observed


def test_mock_intent_submit_and_four_signal_verification(tmp_path, capsys):
    run, observed_path, observed = _initialize_armed_mock(tmp_path, capsys)
    assert main(["issue-intent", "--run", str(run), "--observed-state", str(observed_path)]) == 0
    issued = json.loads(capsys.readouterr().out)
    intent = issued["intent"]
    assert issued["safety"] == "allow"
    assert main(["mark-submitted", "--run", str(run), "--intent-id", intent["intent_id"]]) == 0
    capsys.readouterr()

    observed.update(
        {
            "your_turn": False,
            "overall_pick": 2,
            "current_team": "team-2",
            "roster_count": 1,
            "rows": [],
            "last_pick_player_id": intent["player_id"],
            "last_pick_position": intent["position"],
            "last_pick_overall": 1,
            "room_advanced": True,
        }
    )
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    assert main(["verify-pick", "--run", str(run), "--intent-id", intent["intent_id"], "--observed-state", str(observed_path)]) == 0
    capsys.readouterr()
    assert main(["status", "--run", str(run), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"]["current_pick"] == 2
    assert status["state"]["last_verified_pick"] == 1
    assert intent["player_id"] in status["state"]["unavailable"]
    assert status["state"]["outstanding_intent_id"] is None


def test_failed_verification_disarms_and_requires_takeover(tmp_path, capsys):
    run, observed_path, observed = _initialize_armed_mock(tmp_path, capsys)
    assert main(["issue-intent", "--run", str(run), "--observed-state", str(observed_path)]) == 0
    intent = json.loads(capsys.readouterr().out)["intent"]
    assert main(["mark-submitted", "--run", str(run), "--intent-id", intent["intent_id"]]) == 0
    capsys.readouterr()
    observed.update(
        {
            "overall_pick": 2,
            "current_team": "team-2",
            "roster_count": 0,
            "last_pick_player_id": "wrong-player",
            "last_pick_position": "WR",
            "last_pick_overall": 1,
            "room_advanced": True,
        }
    )
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    assert main(["verify-pick", "--run", str(run), "--intent-id", intent["intent_id"], "--observed-state", str(observed_path)]) == 4
    capsys.readouterr()
    assert main(["status", "--run", str(run), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"]["control_state"] == "takeover"
    assert status["state"]["outstanding_intent_id"] is None
