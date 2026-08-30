"""Command-line interface for offline draft state and recommendation control."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any, Sequence
from uuid import uuid4

from .importers import load_league, load_players
from .models import (
    DraftState,
    LeagueConfig,
    ObservedYahooState,
    PlayerSnapshot,
    RecommendationEnvelope,
    datetime_text,
    parse_datetime,
)
from .optimizer import board_hash, config_hash, team_for_pick
from .recommendation import recommend
from .reducer import replay, state_hash, state_to_dict
from .safety import evaluate_pick_safety
from .storage import EventStore, EventStoreError
from fantasy_draft_assistant_provisioning import provision_event_store


CONFIG_FILE = "league.json"
PLAYERS_FILE = "players.json"
DATABASE_FILE = "draft.sqlite"
RECOMMENDATION_FILE = "latest-recommendation.json"
READINESS_FILE = "rehearsal-readiness.json"
SOURCE_MANIFEST_FILE = "source-manifest.json"


class CommandExit(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"command exited with status {code}")
        self.code = code


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def _runtime(run: Path) -> tuple[LeagueConfig, list[PlayerSnapshot], EventStore, DraftState]:
    config = LeagueConfig.from_dict(_read_json(run / CONFIG_FILE))
    players_value = json.loads((run / PLAYERS_FILE).read_text(encoding="utf-8"))
    if not isinstance(players_value, list):
        raise ValueError("players.json must contain a list")
    players = [PlayerSnapshot.from_dict(item) for item in players_value]
    store = EventStore(run / DATABASE_FILE)
    state = replay(store.events())
    return config, players, store, state


def _player(players: Sequence[PlayerSnapshot], player_id: str) -> PlayerSnapshot:
    matches = [player for player in players if player.player_id == player_id]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one player with ID {player_id!r}")
    return matches[0]


def _envelope_to_dict(value: RecommendationEnvelope) -> dict[str, Any]:
    return {
        "top_three": list(value.top_three),
        "component_ordering": list(value.component_ordering),
        "exclusions": {key: list(reasons) for key, reasons in value.exclusions.items()},
        "input_freshness": dict(value.input_freshness),
        "state_hash": value.state_hash,
        "config_hash": value.config_hash,
        "board_hash": value.board_hash,
        "expires_at": datetime_text(value.expires_at),
    }


def _envelope_from_dict(value: dict[str, Any]) -> RecommendationEnvelope:
    return RecommendationEnvelope(
        top_three=tuple(value["top_three"]),
        component_ordering=tuple(value["component_ordering"]),
        exclusions={key: tuple(reasons) for key, reasons in value["exclusions"].items()},
        input_freshness=dict(value["input_freshness"]),
        state_hash=value["state_hash"],
        config_hash=value["config_hash"],
        board_hash=value["board_hash"],
        expires_at=parse_datetime(value["expires_at"], "expires_at"),
    )


def _append(store: EventStore, event_type: str, payload: dict[str, Any], key: str | None = None) -> None:
    store.append(event_type, payload, idempotency_key=key or str(uuid4()))


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def command_init(args: argparse.Namespace) -> None:
    run = args.run.resolve()
    if any((run / name).exists() for name in (CONFIG_FILE, PLAYERS_FILE, DATABASE_FILE)):
        raise ValueError(f"run already initialized: {run}")
    config = load_league(args.league)
    players = load_players(args.players)
    player_ids = {player.player_id for player in players}
    missing_keepers = [keeper.player_id for keeper in config.keepers if keeper.player_id not in player_ids]
    if missing_keepers:
        raise ValueError(f"keepers missing from player board: {', '.join(missing_keepers)}")
    run.mkdir(parents=True, exist_ok=True)
    _write_json(run / CONFIG_FILE, config.to_dict())
    _write_json(run / PLAYERS_FILE, [player.to_dict() for player in players])
    _write_json(
        run / SOURCE_MANIFEST_FILE,
        {
            "board_hash": board_hash(players),
            "network_access_after_freeze": False,
            "sources": sorted(
                {
                    (player.source, player.source_family, datetime_text(player.checked_at))
                    for player in players
                }
            ),
        },
    )
    provision_event_store(run / DATABASE_FILE)
    store = EventStore(run / DATABASE_FILE)
    _append(
        store,
        "initialized",
        {
            "current_pick": 1,
            "current_team": team_for_pick(config, 1),
            "rosters": {team: [] for team in config.draft_slots},
            "unavailable": [keeper.player_id for keeper in config.keepers],
            "queue": [],
        },
        "initialized-v1",
    )
    _print({"run": str(run), "status": "initialized", "network_access": False})


def _readiness(run: Path) -> tuple[bool, list[str]]:
    path = run / READINESS_FILE
    if not path.exists():
        return False, ["missing_rehearsal_readiness"]
    value = _read_json(path)
    requirements = {
        "full_mock_passed": True,
        "zero_wrong_duplicate_ambiguous_actions": True,
        "takeover_witnessed": True,
        "queue_fallback_witnessed": True,
        "no_external_calls_after_room_open": True,
    }
    reasons = [f"readiness_{key}_not_met" for key, expected in requirements.items() if value.get(key) is not expected]
    p99 = value.get("recommendation_p99_ms")
    if not isinstance(p99, (int, float)) or isinstance(p99, bool) or p99 > 500:
        reasons.append("readiness_p99_over_500ms")
    return not reasons, reasons


def command_doctor(args: argparse.Namespace) -> None:
    config, players, store, state = _runtime(args.run)
    now = datetime.now(timezone.utc)
    stale = [
        player.player_id
        for player in players
        if (now - player.checked_at).total_seconds() / 3600 > config.mandatory_freshness_hours
    ]
    ambiguous = [player.player_id for player in players if player.ambiguous]
    timings: list[float] = []
    for _ in range(100):
        started = time.perf_counter()
        recommend(config, players, state, now=now)
        timings.append((time.perf_counter() - started) * 1000)
    p99_ms = sorted(timings)[98]
    manifest = _read_json(args.run / SOURCE_MANIFEST_FILE)
    manifest_valid = (
        manifest.get("board_hash") == board_hash(players)
        and manifest.get("network_access_after_freeze") is False
    )
    ready, readiness_reasons = _readiness(args.run)
    result = {
        "ok": not stale and not ambiguous and len(players) >= 3 and manifest_valid,
        "active_teams": config.active_teams,
        "maximum_teams": config.maximum_teams,
        "draft_slots": len(config.draft_slots),
        "keeper_picks": {keeper.player_id: keeper.overall_pick for keeper in config.keepers},
        "events": len(store.events()),
        "state_hash": state_hash(state),
        "config_hash": config_hash(config),
        "board_hash": board_hash(players),
        "stale_players": stale,
        "ambiguous_players": ambiguous,
        "recommendation_p99_ms": round(p99_ms, 3),
        "performance_gate_passed": p99_ms <= 500,
        "source_manifest_valid": manifest_valid,
        "real_automatic_ready": ready and not stale and not ambiguous and manifest_valid and p99_ms <= 500,
        "real_automatic_blockers": readiness_reasons,
    }
    _print(result)
    if not result["ok"]:
        raise CommandExit(2)


def command_status(args: argparse.Namespace) -> None:
    config, players, store, state = _runtime(args.run)
    _print(
        {
            "state": state_to_dict(state),
            "event_count": len(store.events()),
            "state_hash": state_hash(state),
            "config_hash": config_hash(config),
            "board_hash": board_hash(players),
        }
    )


def command_observe_pick(args: argparse.Namespace) -> None:
    config, players, store, state = _runtime(args.run)
    _player(players, args.player_id)
    team = team_for_pick(config, args.overall)
    keeper = next(
        (
            configured
            for configured in config.keepers
            if configured.overall_pick == args.overall
            and configured.player_id == args.player_id
            and configured.team == team
        ),
        None,
    )
    next_pick = args.overall + 1
    next_team = team_for_pick(config, next_pick) if next_pick <= config.active_teams * config.rounds else None
    _append(
        store,
        "pick_observed",
        {
            "overall_pick": args.overall,
            "player_id": args.player_id,
            "team": team,
            "next_team": next_team,
            "keeper": keeper is not None,
        },
        f"observed-{args.overall}-{args.player_id}",
    )
    command_status(args)


def command_recommend(args: argparse.Namespace) -> None:
    config, players, store, state = _runtime(args.run)
    if not state.reconciled or not state.room_fingerprint:
        raise ValueError("recommendation requires a freshly reconciled Yahoo room state")
    envelope, candidates, pair = recommend(config, players, state, top=args.top)
    queue_ids = list(envelope.top_three)
    _append(
        store,
        "queue_set",
        {"player_ids": queue_ids},
        f"queue-{state_hash(state)}-{'-'.join(queue_ids)}",
    )
    # Queue state changes the state hash, so recompute from the persisted state.
    state = replay(store.events())
    envelope, candidates, pair = recommend(config, players, state, top=args.top)
    value = {"recommendation": _envelope_to_dict(envelope), "candidates": candidates, "pair": pair}
    _write_json(args.run / RECOMMENDATION_FILE, value)
    _print(value)


def command_queue(args: argparse.Namespace) -> None:
    _, players, _, state = _runtime(args.run)
    by_id = {player.player_id: player for player in players}
    _print(
        {
            "queue": [
                {"player_id": player_id, "name": by_id[player_id].name, "position": by_id[player_id].position}
                for player_id in state.queue
                if player_id in by_id
            ]
        }
    )


def command_arm(args: argparse.Namespace) -> None:
    config, players, store, state = _runtime(args.run)
    if not state.reconciled or state.room_fingerprint != args.room_fingerprint:
        raise ValueError("arming requires a reconciled state for this room fingerprint")
    if args.mode == "real":
        if not state.real_draft_acknowledged or state.room_fingerprint != args.room_fingerprint:
            raise ValueError("real mode requires a separate acknowledge-real command for this room fingerprint")
        ready, reasons = _readiness(args.run)
        if not ready:
            raise ValueError(f"real automatic mode is rehearsal-blocked: {', '.join(reasons)}")
        now = datetime.now(timezone.utc)
        stale = [
            player.player_id
            for player in players
            if (now - player.checked_at).total_seconds() / 3600 > config.mandatory_freshness_hours
        ]
        ambiguous = [player.player_id for player in players if player.ambiguous]
        if stale or ambiguous or len(players) < 3:
            raise ValueError(
                "real automatic mode failed current doctor gates: "
                f"stale={stale}, ambiguous={ambiguous}, players={len(players)}"
            )
        manifest = _read_json(args.run / SOURCE_MANIFEST_FILE)
        if manifest.get("board_hash") != board_hash(players) or manifest.get("network_access_after_freeze") is not False:
            raise ValueError("real automatic mode failed the frozen source-manifest gate")
        timings: list[float] = []
        for _ in range(100):
            started = time.perf_counter()
            recommend(config, players, state, now=now)
            timings.append((time.perf_counter() - started) * 1000)
        if sorted(timings)[98] > 500:
            raise ValueError("real automatic mode failed the current 500 ms p99 performance gate")
    _append(
        store,
        "armed",
        {
            "mode": args.mode,
            "room_fingerprint": args.room_fingerprint,
        },
    )
    command_status(args)


def command_acknowledge_real(args: argparse.Namespace) -> None:
    _, _, store, state = _runtime(args.run)
    if state.control_state.value != "disarmed" or state.outstanding_intent_id is not None:
        raise ValueError("real acknowledgement requires a disarmed run with no outstanding intent")
    _append(
        store,
        "real_draft_acknowledged",
        {"room_fingerprint": args.room_fingerprint},
    )
    command_status(args)


def command_disarm(args: argparse.Namespace) -> None:
    _, _, store, _ = _runtime(args.run)
    _append(store, "disarmed", {"reason": args.reason})
    command_status(args)


def command_reconcile(args: argparse.Namespace) -> None:
    config, _, store, state = _runtime(args.run)
    observed = ObservedYahooState.from_dict(_read_json(args.observed_state))
    if state.outstanding_intent_id:
        raise ValueError("cannot reconcile while a pick intent is outstanding")
    age = (datetime.now(timezone.utc) - observed.captured_at).total_seconds()
    if age < -2 or age > 5:
        raise ValueError("reconciliation requires visible state captured within the last five seconds")
    expected_team = team_for_pick(config, observed.overall_pick)
    if observed.current_team != expected_team:
        raise ValueError("observed current team does not match the configured snake order")
    if state.room_fingerprint and observed.room_fingerprint != state.room_fingerprint:
        raise ValueError("observed room fingerprint does not match the armed or acknowledged room")
    expected_roster = tuple(state.rosters.get(config.our_team, ()))
    if observed.roster_count != len(expected_roster) or set(observed.roster_player_ids) != set(expected_roster):
        raise ValueError("observed roster does not match replayed state")
    if set(observed.unavailable_player_ids) != set(state.unavailable):
        raise ValueError("observed unavailable set does not match replayed state")
    _append(
        store,
        "reconciled",
        {
            "current_pick": observed.overall_pick,
            "current_team": observed.current_team,
            "room_fingerprint": observed.room_fingerprint,
        },
    )
    command_status(args)


def _latest_recommendation(run: Path) -> tuple[RecommendationEnvelope, dict[str, Any]]:
    value = _read_json(run / RECOMMENDATION_FILE)
    return _envelope_from_dict(value["recommendation"]), value


def command_issue_intent(args: argparse.Namespace) -> None:
    config, players, store, state = _runtime(args.run)
    observed = ObservedYahooState.from_dict(_read_json(args.observed_state))
    envelope, _ = _latest_recommendation(args.run)
    player = _player(players, envelope.top_three[0])
    decision = evaluate_pick_safety(
        state=state,
        observed=observed,
        recommendation=envelope,
        player=player,
        current_state_hash=state_hash(state),
        current_config_hash=config_hash(config),
        current_board_hash=board_hash(players),
        acceptable_queue_ids=envelope.top_three,
        queue_players=[_player(players, player_id) for player_id in envelope.top_three],
    )
    if not decision.allowed:
        _print({"safety": decision.outcome, "reasons": list(decision.reasons)})
        raise CommandExit(3)
    intent_id = str(uuid4())
    payload = {
        "intent_id": intent_id,
        "player_id": player.player_id,
        "player_name": player.name,
        "nfl_team": player.nfl_team,
        "position": player.position,
        "expected_pick": state.current_pick,
        "expected_team": state.current_team,
        "expected_roster_count": state.roster_count(state.current_team or ""),
        "room_fingerprint": observed.room_fingerprint,
        "state_hash": envelope.state_hash,
        "config_hash": envelope.config_hash,
        "board_hash": envelope.board_hash,
        "expires_at": datetime_text(envelope.expires_at),
    }
    _append(store, "intent_issued", payload, f"intent-{intent_id}")
    _print({"safety": "allow", "intent": payload, "instruction": "click_exact_row_once_then_mark_submitted"})


def _intent_payload(store: EventStore, intent_id: str) -> dict[str, Any]:
    for event in reversed(store.events()):
        if event.event_type == "intent_issued" and event.payload.get("intent_id") == intent_id:
            return dict(event.payload)
    raise ValueError(f"unknown intent: {intent_id}")


def command_mark_submitted(args: argparse.Namespace) -> None:
    _, _, store, state = _runtime(args.run)
    if state.outstanding_intent_id != args.intent_id:
        raise ValueError("intent is not the outstanding intent")
    _append(store, "intent_submitted", {"intent_id": args.intent_id}, f"submitted-{args.intent_id}")
    _print({"intent_id": args.intent_id, "status": "submitted", "retry_allowed": False})


def command_verify_pick(args: argparse.Namespace) -> None:
    config, _, store, state = _runtime(args.run)
    if state.outstanding_intent_id != args.intent_id:
        raise ValueError("intent is not the outstanding intent")
    intent = _intent_payload(store, args.intent_id)
    observed = ObservedYahooState.from_dict(_read_json(args.observed_state))
    failures: list[str] = []
    if observed.last_pick_player_id != intent["player_id"]:
        failures.append("last_pick_player_mismatch")
    if observed.last_pick_position != intent["position"]:
        failures.append("last_pick_position_mismatch")
    if observed.last_pick_overall != intent["expected_pick"]:
        failures.append("last_pick_overall_mismatch")
    if observed.room_advanced is not True or observed.overall_pick != intent["expected_pick"] + 1:
        failures.append("room_did_not_advance")
    if observed.roster_count != intent["expected_roster_count"] + 1:
        failures.append("roster_count_did_not_advance")
    if failures:
        _append(
            store,
            "verification_failed_takeover",
            {"intent_id": args.intent_id, "reason": ",".join(failures)},
            f"verification-failed-{args.intent_id}",
        )
        _print({"verified": False, "failures": failures, "automatic_entry": "disarmed"})
        raise CommandExit(4)
    next_pick = intent["expected_pick"] + 1
    next_team = team_for_pick(config, next_pick) if next_pick <= config.active_teams * config.rounds else None
    _append(
        store,
        "pick_verified_and_observed",
        {
            "intent_id": args.intent_id,
            "overall_pick": intent["expected_pick"],
            "player_id": intent["player_id"],
            "team": intent["expected_team"],
            "next_team": next_team,
        },
        f"verified-observed-{intent['expected_pick']}-{intent['player_id']}",
    )
    _print({"verified": True, "intent_id": args.intent_id, "next_pick": next_pick})


def command_replay(args: argparse.Namespace) -> None:
    _, _, store, _ = _runtime(args.run)
    events = store.events(to_sequence=args.to_event)
    state = replay(events)
    _print({"events": len(events), "state": state_to_dict(state), "state_hash": state_hash(state)})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="draft-assistant")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    init.add_argument("--league", type=Path, required=True)
    init.add_argument("--players", type=Path, required=True)
    init.add_argument("--run", type=Path, required=True)
    init.set_defaults(handler=command_init)

    for name, handler in (("doctor", command_doctor), ("status", command_status), ("queue", command_queue)):
        command = commands.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
        command.add_argument("--json", action="store_true", dest="json_output")
        command.set_defaults(handler=handler)

    observe = commands.add_parser("observe-pick")
    observe.add_argument("--run", type=Path, required=True)
    observe.add_argument("--overall", type=int, required=True)
    observe.add_argument("--player-id", required=True)
    observe.set_defaults(handler=command_observe_pick)

    recommendation = commands.add_parser("recommend")
    recommendation.add_argument("--run", type=Path, required=True)
    recommendation.add_argument("--top", type=int, default=3)
    recommendation.add_argument("--json", action="store_true", dest="json_output")
    recommendation.set_defaults(handler=command_recommend)

    arm = commands.add_parser("arm")
    arm.add_argument("--run", type=Path, required=True)
    arm.add_argument("--mode", choices=("mock", "real"), required=True)
    arm.add_argument("--room-fingerprint", required=True)
    arm.set_defaults(handler=command_arm)

    acknowledge = commands.add_parser("acknowledge-real")
    acknowledge.add_argument("--run", type=Path, required=True)
    acknowledge.add_argument("--room-fingerprint", required=True)
    acknowledge.set_defaults(handler=command_acknowledge_real)

    disarm = commands.add_parser("disarm")
    disarm.add_argument("--run", type=Path, required=True)
    disarm.add_argument("--reason", required=True)
    disarm.set_defaults(handler=command_disarm)

    for name, handler in (("issue-intent", command_issue_intent), ("reconcile", command_reconcile)):
        command = commands.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
        command.add_argument("--observed-state", type=Path, required=True)
        command.set_defaults(handler=handler)

    submitted = commands.add_parser("mark-submitted")
    submitted.add_argument("--run", type=Path, required=True)
    submitted.add_argument("--intent-id", required=True)
    submitted.set_defaults(handler=command_mark_submitted)

    verified = commands.add_parser("verify-pick")
    verified.add_argument("--run", type=Path, required=True)
    verified.add_argument("--intent-id", required=True)
    verified.add_argument("--observed-state", type=Path, required=True)
    verified.set_defaults(handler=command_verify_pick)

    replay_command = commands.add_parser("replay")
    replay_command.add_argument("--run", type=Path, required=True)
    replay_command.add_argument("--to-event", type=int)
    replay_command.add_argument("--json", action="store_true", dest="json_output")
    replay_command.set_defaults(handler=command_replay)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
        return 0
    except CommandExit as exc:
        return exc.code
    except (OSError, ValueError, EventStoreError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
