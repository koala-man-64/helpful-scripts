"""Command-line interface for offline draft state and recommendation control."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Sequence
from uuid import uuid4

from .compiler import (
    compact_cheat_sheet,
    compile_board,
    compiled_board_csv,
    compiled_board_dict,
)
from .importers import load_compiled_board, load_league, load_players, load_research_bundle
from .models import (
    BoardManifest,
    CompiledPlayer,
    DraftState,
    DraftPlatform,
    LeagueConfig,
    ObservedDraftState,
    PlayerSnapshot,
    RecommendationEnvelope,
    datetime_text,
    parse_datetime,
)
from .optimizer import board_hash, config_hash, team_for_pick
from .platforms import (
    ChromeOperation,
    ControlSnapshot,
    get_platform_mapping,
    validate_control_snapshot,
)
from .research import (
    ResearchBundle,
    fetch_boris_tiers,
    fetch_sleeper_players,
    merge_bundles,
)
from .recommendation import recommend, recommend_turn
from .reducer import replay, state_hash, state_to_dict
from .safety import (
    evaluate_pick_safety,
    evaluate_platform_autodraft_observation,
    evaluate_post_pick_verification,
)
from .storage import EventStore, EventStoreError
from fantasy_draft_assistant_provisioning import provision_event_store


CONFIG_FILE = "league.json"
PLAYERS_FILE = "players.json"
DATABASE_FILE = "draft.sqlite"
RECOMMENDATION_FILE = "latest-recommendation.json"
READINESS_FILE = "rehearsal-readiness.json"
SOURCE_MANIFEST_FILE = "source-manifest.json"
COMPILED_BOARD_FILE = "board.json"
COMPILED_BOARD_CSV_FILE = "board.csv"
COMPILED_MANIFEST_FILE = "manifest.json"
CHEAT_SHEET_FILE = "cheat-sheet.txt"


class CommandExit(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"command exited with status {code}")
        self.code = code


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def _read_json_source(source: str | Path) -> dict[str, Any]:
    """Read one sanitized JSON object from a path or standard input.

    Chrome workflows pass ``-`` so transient room observations do not need to
    be written to disk.  File input remains available for offline fixtures and
    rehearsals.
    """

    if str(source) != "-":
        return _read_json(Path(source))
    try:
        value = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read valid JSON from standard input: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object on standard input")
    return value


def _read_observed_state(
    source: str | Path,
    *,
    required_operations: Sequence[ChromeOperation],
    write_rehearsal_passed: bool = False,
) -> tuple[ObservedDraftState, dict[str, str]]:
    """Validate state plus sanitized semantic-control evidence at the CLI boundary."""

    value = _read_json_source(source)
    snapshots_value = value.pop("control_snapshots", None)
    if not isinstance(snapshots_value, list):
        raise ValueError("observed-state envelope requires a control_snapshots list")
    observed = ObservedDraftState.from_dict(value)
    get_platform_mapping(observed.platform.value, observed.adapter_version)

    snapshots: dict[ChromeOperation, tuple[ControlSnapshot, dict[str, Any]]] = {}
    for raw_snapshot in snapshots_value:
        if not isinstance(raw_snapshot, dict):
            raise ValueError("control_snapshots entries must be objects")
        snapshot = ControlSnapshot.from_dict(raw_snapshot)
        if snapshot.operation in snapshots:
            raise ValueError(f"duplicate {snapshot.operation.value} control snapshot")
        snapshots[snapshot.operation] = (snapshot, raw_snapshot)

    hashes: dict[str, str] = {}
    for operation in required_operations:
        entry = snapshots.get(operation)
        if entry is None:
            raise ValueError(f"missing {operation.value} semantic-control snapshot")
        snapshot, raw_snapshot = entry
        validation = validate_control_snapshot(
            snapshot,
            write_rehearsal_passed=write_rehearsal_passed,
            expected_platform=observed.platform.value,
            expected_mapping_version=observed.adapter_version,
        )
        if not validation.allowed:
            raise ValueError(
                f"{operation.value} semantic-control validation failed: "
                + "; ".join(validation.reasons)
            )
        encoded = json.dumps(raw_snapshot, sort_keys=True, separators=(",", ":"))
        hashes[operation.value] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return observed, hashes


DraftPlayer = PlayerSnapshot | CompiledPlayer


def _runtime(run: Path) -> tuple[LeagueConfig, list[DraftPlayer], EventStore, DraftState]:
    config = LeagueConfig.from_dict(_read_json(run / CONFIG_FILE))
    players_value = json.loads((run / PLAYERS_FILE).read_text(encoding="utf-8"))
    if not isinstance(players_value, list):
        raise ValueError("players.json must contain a list")
    players: list[DraftPlayer] = []
    for item in players_value:
        if not isinstance(item, dict):
            raise ValueError("players.json entries must be objects")
        players.append(
            CompiledPlayer.from_dict(item)
            if "projected_points" in item
            else PlayerSnapshot.from_dict(item)
        )
    store = EventStore(run / DATABASE_FILE)
    state = replay(store.events())
    if state.config_hash is not None and state.config_hash != config_hash(config):
        raise ValueError("runtime league configuration differs from the initialized config hash")
    if state.board_hash is not None and state.board_hash != board_hash(players):
        raise ValueError("runtime player board differs from the initialized board hash")
    return config, players, store, state


def _player(players: Sequence[DraftPlayer], player_id: str) -> DraftPlayer:
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


def _observation_hash(observed: ObservedDraftState) -> str:
    """Hash decision-relevant visible state while allowing the clock to tick."""

    value = observed.to_dict()
    value.pop("captured_at", None)
    value.pop("clock_seconds", None)
    rows = value.get("rows", [])
    if isinstance(rows, list):
        value["rows"] = sorted(
            rows,
            key=lambda row: (
                str(row.get("player_id") or ""),
                str(row.get("name") or ""),
                str(row.get("nfl_team") or ""),
                str(row.get("position") or ""),
            ),
        )
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _pre_submit_binding_hash(observed: ObservedDraftState) -> str:
    """Bind approval to every decision input except the intentionally replaced queue."""

    value = observed.to_dict()
    value.pop("captured_at", None)
    value.pop("clock_seconds", None)
    value.pop("queue_player_ids", None)
    rows = value.get("rows", [])
    if isinstance(rows, list):
        value["rows"] = sorted(
            rows,
            key=lambda row: (
                str(row.get("player_id") or ""),
                str(row.get("name") or ""),
                str(row.get("nfl_team") or ""),
                str(row.get("position") or ""),
            ),
        )
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _append(store: EventStore, event_type: str, payload: dict[str, Any], key: str | None = None) -> None:
    store.append(event_type, payload, idempotency_key=key or str(uuid4()))


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def command_init(args: argparse.Namespace) -> None:
    run = args.run.resolve()
    if any((run / name).exists() for name in (CONFIG_FILE, PLAYERS_FILE, DATABASE_FILE)):
        raise ValueError(f"run already initialized: {run}")
    config = load_league(args.league)
    compiled_manifest: BoardManifest | None = None
    if args.players.suffix.casefold() == ".json":
        players, compiled_manifest = load_compiled_board(args.players)
        if compiled_manifest.config_hash != config_hash(config):
            raise ValueError("compiled board config hash does not match the league configuration")
        if compiled_manifest.board_hash != board_hash(players):
            raise ValueError("compiled board hash does not match its player records")
    else:
        players = load_players(args.players)
    player_ids = {player.player_id for player in players}
    missing_keepers = [keeper.player_id for keeper in config.keepers if keeper.player_id not in player_ids]
    if missing_keepers:
        raise ValueError(f"keepers missing from player board: {', '.join(missing_keepers)}")
    run.mkdir(parents=True, exist_ok=True)
    _write_json(run / CONFIG_FILE, config.to_dict())
    _write_json(run / PLAYERS_FILE, [player.to_dict() for player in players])
    if compiled_manifest is not None:
        source_manifest = compiled_manifest.to_dict()
        source_manifest.update(
            {
                "board_kind": "compiled",
                "network_access_after_freeze": False,
            }
        )
    else:
        source_manifest = {
            "board_hash": board_hash(players),
            "config_hash": config_hash(config),
            "board_kind": "legacy",
            "network_access_after_freeze": False,
            "sources": sorted(
                {
                    (player.source, player.source_family, datetime_text(player.checked_at))
                    for player in players
                }
            ),
        }
    _write_json(run / SOURCE_MANIFEST_FILE, source_manifest)
    provision_event_store(run / DATABASE_FILE)
    store = EventStore(run / DATABASE_FILE)
    initial_rosters = {team: [] for team in config.draft_slots}
    for keeper in sorted(config.keepers, key=lambda item: item.overall_pick):
        initial_rosters[keeper.team].append(keeper.player_id)
    _append(
        store,
        "initialized",
        {
            "current_pick": 1,
            "current_team": team_for_pick(config, 1),
            "rosters": initial_rosters,
            "unavailable": [keeper.player_id for keeper in config.keepers],
            "queue": [],
            "config_hash": config_hash(config),
            "board_hash": board_hash(players),
        },
        "initialized-v1",
    )
    _print(
        {
            "run": str(run),
            "status": "initialized",
            "board_kind": source_manifest["board_kind"],
            "network_access": False,
        }
    )


def command_research_refresh(args: argparse.Namespace) -> None:
    """Fetch only the two direct, documented v1 research adapters."""

    bundles: list[ResearchBundle] = []
    for source in args.source:
        if source == "boris":
            bundles.append(fetch_boris_tiers(args.scoring_format))
        elif source == "sleeper":
            bundles.append(fetch_sleeper_players())
        else:  # argparse constrains this; retain a fail-closed programmatic path.
            raise ValueError(f"unsupported direct research source: {source}")
    bundle = merge_bundles(*bundles)
    _write_json(args.output, bundle.to_dict())
    _print(
        {
            "output": str(args.output.resolve()),
            "artifacts": len(bundle.artifacts),
            "evidence": len(bundle.evidence),
            "sources": list(args.source),
            "raw_payloads_retained": False,
        }
    )


def command_research_import(args: argparse.Namespace) -> None:
    """Validate and merge sanitized Chrome/manual research snapshots."""

    imported = ResearchBundle.from_dict(_read_json_source(args.snapshot))
    existing = [load_research_bundle(path) for path in args.merge]
    bundle = merge_bundles(*existing, imported)
    _write_json(args.output, bundle.to_dict())
    _print(
        {
            "output": str(args.output.resolve()),
            "artifacts": len(bundle.artifacts),
            "evidence": len(bundle.evidence),
            "private_browser_state_retained": False,
        }
    )


def _optional_mapping(path: Path | None, label: str) -> dict[str, Any] | None:
    if path is None:
        return None
    value = _read_json(path)
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} keys must be strings")
    return value


def command_compile_board(args: argparse.Namespace) -> None:
    """Freeze one league-specific board without mutating an earlier revision."""

    config = load_league(args.league)
    bundle = load_research_bundle(args.research)
    parent_manifest: BoardManifest | None = None
    if args.parent_board is not None:
        _, parent_manifest = load_compiled_board(args.parent_board)
    selected_families = _optional_mapping(args.source_families, "source family selection")
    preferences = _optional_mapping(args.preferences, "personal preferences")
    players, manifest = compile_board(
        config,
        bundle,
        selected_source_families=selected_families,
        personal_preferences=preferences,
        parent_manifest=parent_manifest,
        revision_reason=args.revision_reason,
    )

    output = args.output.resolve()
    targets = (
        output / COMPILED_BOARD_FILE,
        output / COMPILED_BOARD_CSV_FILE,
        output / COMPILED_MANIFEST_FILE,
        output / CHEAT_SHEET_FILE,
    )
    existing = [str(path) for path in targets if path.exists()]
    if existing:
        raise ValueError(
            "compiled board output is immutable; choose a new directory for a revision: "
            + ", ".join(existing)
        )
    _write_json(output / COMPILED_BOARD_FILE, compiled_board_dict(players, manifest))
    _write_text(output / COMPILED_BOARD_CSV_FILE, compiled_board_csv(players))
    _write_json(output / COMPILED_MANIFEST_FILE, manifest.to_dict())
    _write_text(output / CHEAT_SHEET_FILE, compact_cheat_sheet(players))
    _print(
        {
            "output": str(output),
            "players": len(players),
            "board_hash": manifest.board_hash,
            "revision": manifest.revision,
            "parent_board_hash": manifest.parent_board_hash,
            "omissions": list(manifest.omissions),
            "conflicts": list(manifest.conflicts),
        }
    )


def _readiness(
    run: Path,
    platform: DraftPlatform | str,
    adapter_version: str | None = None,
) -> tuple[bool, list[str]]:
    platform_value = platform.value if isinstance(platform, DraftPlatform) else str(platform).strip().casefold()
    if platform_value not in {item.value for item in DraftPlatform}:
        return False, ["unknown_platform"]
    expected_version = get_platform_mapping(platform_value, adapter_version).version
    path = run / READINESS_FILE
    if not path.exists():
        return False, [f"{platform_value}_missing_rehearsal_readiness"]
    document = _read_json(path)
    platforms = document.get("platforms")
    if not isinstance(platforms, dict) or not isinstance(platforms.get(platform_value), dict):
        return False, [f"{platform_value}_missing_rehearsal_readiness"]
    value = platforms[platform_value]
    requirements = {
        "timed_mock_passed": True,
        "zero_wrong_duplicate_ambiguous_actions": True,
        "takeover_witnessed": True,
        "queue_fallback_witnessed": True,
        "no_external_calls_after_room_open": True,
        "timer_expiry_classified_as_autodraft": True,
    }
    reasons = [
        f"{platform_value}_readiness_{key}_not_met"
        for key, expected in requirements.items()
        if value.get(key) is not expected
    ]
    if value.get("mapping_version") != expected_version:
        reasons.append(f"{platform_value}_readiness_mapping_version_mismatch")
    try:
        witnessed_at = parse_datetime(value.get("witnessed_at"), "witnessed_at")
        if witnessed_at > datetime.now(timezone.utc) + timedelta(seconds=2):
            reasons.append(f"{platform_value}_readiness_witnessed_at_in_future")
    except ValueError:
        reasons.append(f"{platform_value}_readiness_witnessed_at_invalid")
    evidence_reference = value.get("evidence_reference")
    if (
        not isinstance(evidence_reference, str)
        or not evidence_reference.strip()
        or "://" in evidence_reference
    ):
        reasons.append(f"{platform_value}_readiness_evidence_reference_invalid")
    p99 = value.get("recommendation_p99_ms")
    if (
        not isinstance(p99, (int, float))
        or isinstance(p99, bool)
        or not math.isfinite(float(p99))
        or p99 < 0
        or p99 > 500
    ):
        reasons.append(f"{platform_value}_readiness_p99_over_500ms")
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
        and manifest.get("config_hash") == config_hash(config)
        and manifest.get("network_access_after_freeze") is False
    )
    platform_readiness: dict[str, dict[str, Any]] = {}
    for platform in DraftPlatform:
        write_ready, blockers = _readiness(
            args.run,
            platform,
            state.adapter_version if state.platform is platform else None,
        )
        platform_readiness[platform.value] = {
            "write_ready": write_ready,
            "blockers": blockers,
        }
    current_platform = state.platform.value if state.platform else None
    if current_platform:
        ready = platform_readiness[current_platform]["write_ready"]
        readiness_reasons = list(platform_readiness[current_platform]["blockers"])
    else:
        ready = False
        readiness_reasons = ["room_platform_not_reconciled"]
    automatic_blockers = list(readiness_reasons)
    if stale:
        automatic_blockers.append("mandatory_player_evidence_stale")
    if ambiguous:
        automatic_blockers.append("ambiguous_player_identity")
    if not manifest_valid:
        automatic_blockers.append("source_manifest_invalid")
    if p99_ms > 500:
        automatic_blockers.append("recommendation_p99_over_500ms")
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
        "real_automatic_blockers": automatic_blockers,
        "platform_write_readiness": platform_readiness,
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
            "submission_provenance": args.provenance,
        },
        f"observed-{args.overall}-{args.player_id}",
    )
    command_status(args)


def command_recommend(args: argparse.Namespace) -> None:
    config, players, store, state = _runtime(args.run)
    if not state.reconciled or not state.room_fingerprint:
        raise ValueError("recommendation requires a freshly reconciled draft-room state")
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
        if state.platform is None:
            raise ValueError("real mode requires a reconciled platform")
        ready, reasons = _readiness(args.run, state.platform, state.adapter_version)
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


def command_cancel_intent(args: argparse.Namespace) -> None:
    """Cancel an issued intent; submitted intents deliberately fail closed."""

    _, _, store, state = _runtime(args.run)
    if state.outstanding_intent_id != args.intent_id:
        raise ValueError("intent is not the outstanding intent")
    _append(
        store,
        "intent_cancelled",
        {"intent_id": args.intent_id, "reason": args.reason},
        f"cancelled-{args.intent_id}",
    )
    _print({"intent_id": args.intent_id, "status": "cancelled", "retry_allowed": False})


def _reconcile_observation(
    config: LeagueConfig,
    players: Sequence[DraftPlayer],
    store: EventStore,
    state: DraftState,
    observed: ObservedDraftState,
    control_snapshot_hash: str,
) -> DraftState:
    if state.outstanding_intent_id:
        raise ValueError("cannot reconcile while a pick intent is outstanding")
    age = (datetime.now(timezone.utc) - observed.captured_at).total_seconds()
    if age < -2 or age > 5:
        raise ValueError("reconciliation requires visible state captured within the last five seconds")
    if observed.config_hash != config_hash(config):
        raise ValueError("observed league settings/order/keepers/scoring hash does not match the run")
    if observed.board_hash != board_hash(players):
        raise ValueError("observed frozen board hash does not match the run")
    expected_team = team_for_pick(config, observed.overall_pick)
    if observed.current_team != expected_team:
        raise ValueError("observed current team does not match the configured snake order")
    if state.room_fingerprint and observed.room_fingerprint != state.room_fingerprint:
        raise ValueError("observed room fingerprint does not match the armed or acknowledged room")
    if state.platform is not None and observed.platform is not state.platform:
        raise ValueError("observed platform does not match the reconciled platform")
    if state.adapter_version is not None and observed.adapter_version != state.adapter_version:
        raise ValueError("observed adapter version does not match the reconciled adapter")
    get_platform_mapping(observed.platform.value, observed.adapter_version)
    if observed.phase.casefold() != "in_progress" or observed.control_status.casefold() != "ready":
        raise ValueError("reconciliation requires an in-progress room with ready controls")
    if not observed.autodraft_off:
        raise ValueError("reconciliation for an actionable run requires autodraft to be off")
    completed_overalls = [pick.overall_pick for pick in observed.completed_picks]
    expected_overalls = list(range(state.current_pick, observed.overall_pick))
    if completed_overalls != expected_overalls:
        raise ValueError("completed_picks must cover every intervening pick in order")
    player_ids = {player.player_id for player in players}
    completed_payload: list[dict[str, Any]] = []
    maximum_pick = config.active_teams * config.rounds
    for pick in observed.completed_picks:
        if pick.player_id not in player_ids:
            raise ValueError(f"completed pick references unknown player: {pick.player_id}")
        expected_pick_team = team_for_pick(config, pick.overall_pick)
        if pick.team != expected_pick_team:
            raise ValueError("completed pick team does not match the configured snake order")
        configured_keeper = next(
            (
                keeper
                for keeper in config.keepers
                if keeper.overall_pick == pick.overall_pick
                and keeper.player_id == pick.player_id
                and keeper.team == pick.team
            ),
            None,
        )
        next_pick = pick.overall_pick + 1
        completed_payload.append(
            {
                "overall_pick": pick.overall_pick,
                "player_id": pick.player_id,
                "team": pick.team,
                "keeper": configured_keeper is not None,
                "next_team": team_for_pick(config, next_pick) if next_pick <= maximum_pick else None,
                "provenance": pick.provenance,
            }
        )
    if len(set(observed.queue_player_ids)) != len(observed.queue_player_ids):
        raise ValueError("observed queue contains duplicate player IDs")
    if any(player_id in observed.unavailable_player_ids for player_id in observed.queue_player_ids):
        raise ValueError("observed queue contains an unavailable player")
    if (
        observed.authentication_challenge
        or observed.modal_ambiguity
        or observed.reconnecting
        or observed.control_interrupted
    ):
        raise ValueError("cannot reconcile an interrupted or ambiguous browser state")
    _append(
        store,
        "reconciled",
        {
            "current_pick": observed.overall_pick,
            "current_team": observed.current_team,
            "room_fingerprint": observed.room_fingerprint,
            "platform": observed.platform.value,
            "adapter_version": observed.adapter_version,
            "queue": list(observed.queue_player_ids),
            "our_team": config.our_team,
            "our_roster": list(observed.roster_player_ids),
            "unavailable": list(observed.unavailable_player_ids),
            "completed_picks": completed_payload,
            "config_hash": observed.config_hash,
            "board_hash": observed.board_hash,
            "control_snapshot_hash": control_snapshot_hash,
        },
    )
    return replay(store.events())


def command_reconcile(args: argparse.Namespace) -> None:
    config, players, store, state = _runtime(args.run)
    observed, control_hashes = _read_observed_state(
        args.observed_state,
        required_operations=(ChromeOperation.OBSERVE,),
    )
    _reconcile_observation(
        config,
        players,
        store,
        state,
        observed,
        control_hashes[ChromeOperation.OBSERVE.value],
    )
    command_status(args)


def command_turn(args: argparse.Namespace) -> None:
    config, players, store, state = _runtime(args.run)
    observed, control_hashes = _read_observed_state(
        args.observed_state,
        required_operations=(ChromeOperation.OBSERVE,),
    )
    if observed.phase.casefold() != "in_progress" or observed.control_status.casefold() != "ready":
        raise ValueError("turn requires an in-progress room with ready controls")
    state = _reconcile_observation(
        config,
        players,
        store,
        state,
        observed,
        control_hashes[ChromeOperation.OBSERVE.value],
    )
    result = recommend_turn(config, players, state, top=3, observed=observed)
    value = result.to_dict()
    value.update({
        "observation_hash": _observation_hash(observed),
        "platform": observed.platform.value,
        "adapter_version": observed.adapter_version,
        "room_fingerprint": observed.room_fingerprint,
        "overall_pick": observed.overall_pick,
        "baseline_queue": list(observed.queue_player_ids),
        "control_snapshot_hashes": control_hashes,
    })
    _write_json(args.run / RECOMMENDATION_FILE, value)
    _print(value)


def _latest_recommendation(run: Path) -> tuple[RecommendationEnvelope, dict[str, Any]]:
    value = _read_json(run / RECOMMENDATION_FILE)
    return _envelope_from_dict(value["recommendation"]), value


def command_approve_pick(args: argparse.Namespace) -> None:
    config, players, store, state = _runtime(args.run)
    observed, control_hashes = _read_observed_state(
        args.observed_state,
        required_operations=(ChromeOperation.OBSERVE,),
    )
    envelope, recommendation_value = _latest_recommendation(args.run)
    queue_ids = tuple(args.queue)
    if not 1 <= len(queue_ids) <= 3:
        raise ValueError("approval queue must contain one to three player IDs")
    if len(set(queue_ids)) != len(queue_ids):
        raise ValueError("approval queue must not contain duplicate player IDs")
    if queue_ids[0] != args.player_id:
        raise ValueError("approved player must be first in the exact queue order")
    if any(player_id not in envelope.top_three for player_id in queue_ids):
        raise ValueError("every approved queue player must come from the current top three")
    if recommendation_value.get("observation_hash") != _observation_hash(observed):
        _print({"safety": "halt", "reasons": ["observation_changed_since_recommendation"]})
        raise CommandExit(3)
    for field_name, actual in (
        ("platform", observed.platform.value),
        ("adapter_version", observed.adapter_version),
        ("room_fingerprint", observed.room_fingerprint),
        ("overall_pick", observed.overall_pick),
    ):
        if recommendation_value.get(field_name) != actual:
            _print({"safety": "halt", "reasons": [f"{field_name}_changed_since_recommendation"]})
            raise CommandExit(3)
    if state.platform is not observed.platform or state.adapter_version != observed.adapter_version:
        _print({"safety": "halt", "reasons": ["reconciled_platform_or_adapter_mismatch"]})
        raise CommandExit(3)
    player = _player(players, args.player_id)
    queue_players = [_player(players, player_id) for player_id in queue_ids]
    decision = evaluate_pick_safety(
        state=state,
        observed=observed,
        recommendation=envelope,
        player=player,
        current_state_hash=state_hash(state),
        current_config_hash=config_hash(config),
        current_board_hash=board_hash(players),
        acceptable_queue_ids=queue_ids,
        queue_players=queue_players,
        require_visible_queue_match=False,
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
        "platform": observed.platform.value,
        "adapter_version": observed.adapter_version,
        "room_fingerprint": observed.room_fingerprint,
        "approval_observation_hash": _observation_hash(observed),
        "pre_submit_binding_hash": _pre_submit_binding_hash(observed),
        "approval_control_snapshot_hash": control_hashes[ChromeOperation.OBSERVE.value],
        "baseline_queue": list(observed.queue_player_ids),
        "approved_queue": list(queue_ids),
        "recommendation_top_three": list(envelope.top_three),
        "state_hash": envelope.state_hash,
        "config_hash": envelope.config_hash,
        "board_hash": envelope.board_hash,
        "expires_at": datetime_text(envelope.expires_at),
    }
    _append(store, "intent_issued", payload, f"intent-{intent_id}")
    _print(
        {
            "safety": "allow",
            "intent": payload,
            "instruction": (
                "reobserve_bound_state_replace_queue_verify_exact_order_"
                "click_once_submit_once_then_verify"
            ),
        }
    )


def _intent_event(store: EventStore, intent_id: str):
    for event in reversed(store.events()):
        if event.event_type == "intent_issued" and event.payload.get("intent_id") == intent_id:
            return event
    raise ValueError(f"unknown intent: {intent_id}")


def _intent_payload(store: EventStore, intent_id: str) -> dict[str, Any]:
    return dict(_intent_event(store, intent_id).payload)


def command_mark_submitted(args: argparse.Namespace) -> None:
    config, players, store, state = _runtime(args.run)
    if state.outstanding_intent_id != args.intent_id:
        raise ValueError("intent is not the outstanding intent")
    intent_event = _intent_event(store, args.intent_id)
    intent = dict(intent_event.payload)
    write_rehearsal_passed = state.armed_mode is not None and state.armed_mode.value == "mock"
    if state.armed_mode is not None and state.armed_mode.value == "real":
        if state.platform is None:
            raise ValueError("submitted intent has no reconciled platform")
        write_rehearsal_passed = _readiness(
            args.run,
            state.platform,
            state.adapter_version,
        )[0]
    try:
        observed, control_hashes = _read_observed_state(
            args.observed_state,
            required_operations=(ChromeOperation.QUEUE, ChromeOperation.PICK),
            write_rehearsal_passed=write_rehearsal_passed,
        )
    except ValueError:
        reason = "pre_submit_control_evidence_invalid"
        _append(
            store,
            "pre_submit_failed_takeover",
            {"intent_id": args.intent_id, "reason": reason},
            f"pre-submit-failed-{args.intent_id}",
        )
        _print(
            {
                "intent_id": args.intent_id,
                "status": "approval_voided",
                "safety": "halt",
                "reasons": [reason],
                "automatic_entry": "takeover",
                "retry_allowed": False,
            }
        )
        raise CommandExit(3)
    pre_intent_state = replay(store.events(to_sequence=intent_event.sequence - 1))
    approved_queue = tuple(intent["approved_queue"])
    safety_state = replace(pre_intent_state, queue=approved_queue)
    original_envelope, recommendation_value = _latest_recommendation(args.run)
    now = datetime.now(timezone.utc)
    recomputed_envelope, _, _ = recommend(
        config,
        players,
        pre_intent_state,
        top=3,
        now=now,
        observed=observed,
    )
    reasons: list[str] = []
    if _pre_submit_binding_hash(observed) != intent.get("pre_submit_binding_hash"):
        reasons.append("observation_changed_after_approval")
    if tuple(observed.queue_player_ids) != approved_queue:
        reasons.append("approved_queue_not_observed_in_exact_order")
    if tuple(intent.get("recommendation_top_three", ())) != original_envelope.top_three:
        reasons.append("stored_recommendation_changed")
    if recomputed_envelope.top_three != original_envelope.top_three:
        reasons.append("recommendation_changed_after_approval")
    if recommendation_value.get("platform") != observed.platform.value:
        reasons.append("platform_changed_after_approval")
    if recommendation_value.get("adapter_version") != observed.adapter_version:
        reasons.append("adapter_version_changed_after_approval")
    decision = evaluate_pick_safety(
        state=safety_state,
        observed=observed,
        recommendation=original_envelope,
        player=_player(players, intent["player_id"]),
        current_state_hash=state_hash(pre_intent_state),
        current_config_hash=config_hash(config),
        current_board_hash=board_hash(players),
        acceptable_queue_ids=approved_queue,
        queue_players=[_player(players, player_id) for player_id in approved_queue],
        require_visible_queue_match=True,
        now=now,
    )
    reasons.extend(reason for reason in decision.reasons if reason not in reasons)
    if reasons:
        _append(
            store,
            "pre_submit_failed_takeover",
            {"intent_id": args.intent_id, "reason": ",".join(reasons)},
            f"pre-submit-failed-{args.intent_id}",
        )
        _print(
            {
                "intent_id": args.intent_id,
                "status": "approval_voided",
                "safety": "halt",
                "reasons": reasons,
                "automatic_entry": "takeover",
                "retry_allowed": False,
            }
        )
        raise CommandExit(3)
    _append(
        store,
        "intent_submitted",
        {
            "intent_id": args.intent_id,
            "submission_provenance": "manager_approved_chrome_attempt_unverified",
            "room_fingerprint": observed.room_fingerprint,
            "platform": observed.platform.value,
            "adapter_version": observed.adapter_version,
            "overall_pick": observed.overall_pick,
            "current_team": observed.current_team,
            "clock_seconds": observed.clock_seconds,
            "observed_at": datetime_text(observed.captured_at),
            "observed_queue": list(observed.queue_player_ids),
            "roster_player_ids": list(observed.roster_player_ids),
            "unavailable_player_ids": list(observed.unavailable_player_ids),
            "authentication_challenge": observed.authentication_challenge,
            "modal_ambiguity": observed.modal_ambiguity,
            "reconnecting": observed.reconnecting,
            "control_interrupted": observed.control_interrupted,
            "autodraft_off": observed.autodraft_off,
            "phase": observed.phase,
            "control_status": observed.control_status,
            "recommendation_unchanged": True,
            "control_snapshot_hashes": control_hashes,
        },
        f"submitted-{args.intent_id}",
    )
    _print(
        {
            "intent_id": args.intent_id,
            "status": "submission_attempt_recorded",
            "submission_provenance": "manager_approved_chrome_attempt_unverified",
            "control_snapshot_hashes": control_hashes,
            "retry_allowed": False,
        }
    )


def command_verify_pick(args: argparse.Namespace) -> None:
    config, players, store, state = _runtime(args.run)
    if state.outstanding_intent_id != args.intent_id:
        raise ValueError("intent is not the outstanding intent")
    intent = _intent_payload(store, args.intent_id)
    observed, control_hashes = _read_observed_state(
        args.observed_state,
        required_operations=(ChromeOperation.VERIFY,),
    )
    next_pick = intent["expected_pick"] + 1
    next_team = (
        team_for_pick(config, next_pick)
        if next_pick <= config.active_teams * config.rounds
        else None
    )
    if state.outstanding_intent_status is not None and state.outstanding_intent_status.value == "issued":
        decision = evaluate_platform_autodraft_observation(
            observed=observed,
            expected_platform=intent.get("platform", state.platform),
            expected_room_fingerprint=intent["room_fingerprint"],
            expected_pick=intent["expected_pick"],
            expected_roster_count=intent["expected_roster_count"],
            expected_next_team=next_team,
        )
        if not decision.allowed:
            failures = list(decision.reasons)
            _append(
                store,
                "autodraft_verification_failed_takeover",
                {"intent_id": args.intent_id, "reason": ",".join(failures)},
                f"autodraft-verification-failed-{args.intent_id}",
            )
            _print(
                {
                    "verified": False,
                    "codex_pick": False,
                    "automatic_entry": "takeover",
                    "failures": failures,
                    "retry_allowed": False,
                }
            )
            raise CommandExit(4)
        autodrafted_player = _player(players, observed.last_pick_player_id or "")
        _append(
            store,
            "platform_autodraft_observed",
            {
                "intent_id": args.intent_id,
                "overall_pick": intent["expected_pick"],
                "player_id": autodrafted_player.player_id,
                "team": intent["expected_team"],
                "next_team": next_team,
                "room_fingerprint": observed.room_fingerprint,
                "platform": observed.platform.value,
                "adapter_version": observed.adapter_version,
                "observed_overall_pick": observed.overall_pick,
                "observed_current_team": observed.current_team,
                "roster_player_ids": list(observed.roster_player_ids),
                "unavailable_player_ids": list(observed.unavailable_player_ids),
                "observed_queue": list(observed.queue_player_ids),
                "verification_observation_hash": _observation_hash(observed),
                "verification_control_snapshot_hash": control_hashes[ChromeOperation.VERIFY.value],
                "autodraft_off": observed.autodraft_off,
                "phase": observed.phase,
                "control_status": observed.control_status,
                "last_pick_provenance": observed.last_pick_provenance,
                "last_pick_timer_expired": observed.last_pick_timer_expired,
                "authentication_challenge": observed.authentication_challenge,
                "modal_ambiguity": observed.modal_ambiguity,
                "reconnecting": observed.reconnecting,
                "control_interrupted": observed.control_interrupted,
            },
            f"platform-autodraft-{intent['expected_pick']}-{autodrafted_player.player_id}",
        )
        _print(
            {
                "verified": True,
                "codex_pick": False,
                "submission_provenance": "platform-autodraft",
                "player_id": autodrafted_player.player_id,
                "automatic_entry": "takeover",
                "retry_allowed": False,
            }
        )
        return
    decision = evaluate_post_pick_verification(
        observed=observed,
        expected_platform=intent.get("platform", state.platform),
        expected_room_fingerprint=intent["room_fingerprint"],
        expected_player_id=intent["player_id"],
        expected_position=intent["position"],
        expected_pick=intent["expected_pick"],
        expected_roster_count=intent["expected_roster_count"],
        expected_next_team=next_team,
    )
    if not decision.allowed:
        failures = list(decision.reasons)
        _append(
            store,
            "verification_failed_takeover",
            {"intent_id": args.intent_id, "reason": ",".join(failures)},
            f"verification-failed-{args.intent_id}",
        )
        _print(
            {
                "verified": False,
                "failures": failures,
                "automatic_entry": "takeover",
                "retry_allowed": False,
                "control_snapshot_hash": control_hashes[ChromeOperation.VERIFY.value],
            }
        )
        raise CommandExit(4)
    _append(
        store,
        "pick_verified_and_observed",
        {
            "intent_id": args.intent_id,
            "overall_pick": intent["expected_pick"],
            "player_id": intent["player_id"],
            "team": intent["expected_team"],
            "next_team": next_team,
            "room_fingerprint": observed.room_fingerprint,
            "platform": observed.platform.value,
            "adapter_version": observed.adapter_version,
            "observed_overall_pick": observed.overall_pick,
            "observed_current_team": observed.current_team,
            "roster_player_ids": list(observed.roster_player_ids),
            "unavailable_player_ids": list(observed.unavailable_player_ids),
            "verification_observation_hash": _observation_hash(observed),
            "verification_control_snapshot_hash": control_hashes[ChromeOperation.VERIFY.value],
            "authentication_challenge": observed.authentication_challenge,
            "modal_ambiguity": observed.modal_ambiguity,
            "reconnecting": observed.reconnecting,
            "control_interrupted": observed.control_interrupted,
            "autodraft_off": observed.autodraft_off,
            "phase": observed.phase,
            "control_status": observed.control_status,
            "confirmed_submission_provenance": "manager_approved_chrome_transaction",
            "last_pick_provenance": observed.last_pick_provenance,
            "last_pick_timer_expired": observed.last_pick_timer_expired,
        },
        f"verified-observed-{intent['expected_pick']}-{intent['player_id']}",
    )
    _print(
        {
            "verified": True,
            "intent_id": args.intent_id,
            "next_pick": next_pick,
            "control_snapshot_hash": control_hashes[ChromeOperation.VERIFY.value],
            "submission_provenance": "manager_approved_chrome_transaction",
        }
    )


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

    refresh = commands.add_parser("research-refresh")
    refresh.add_argument(
        "--source",
        choices=("boris", "sleeper"),
        nargs="+",
        default=("boris", "sleeper"),
    )
    refresh.add_argument(
        "--scoring-format",
        choices=("standard", "half-ppr", "ppr"),
        default="standard",
    )
    refresh.add_argument("--output", type=Path, required=True)
    refresh.set_defaults(handler=command_research_refresh)

    research_import = commands.add_parser("research-import")
    research_import.add_argument("--snapshot", required=True)
    research_import.add_argument("--merge", type=Path, action="append", default=[])
    research_import.add_argument("--output", type=Path, required=True)
    research_import.set_defaults(handler=command_research_import)

    compilation = commands.add_parser("compile-board")
    compilation.add_argument("--league", type=Path, required=True)
    compilation.add_argument("--research", type=Path, required=True)
    compilation.add_argument("--output", type=Path, required=True)
    compilation.add_argument("--parent-board", type=Path)
    compilation.add_argument("--revision-reason")
    compilation.add_argument("--source-families", type=Path)
    compilation.add_argument("--preferences", type=Path)
    compilation.set_defaults(handler=command_compile_board)

    for name, handler in (("doctor", command_doctor), ("status", command_status), ("queue", command_queue)):
        command = commands.add_parser(name)
        command.add_argument("--run", type=Path, required=True)
        command.add_argument("--json", action="store_true", dest="json_output")
        command.set_defaults(handler=handler)

    observe = commands.add_parser("observe-pick")
    observe.add_argument("--run", type=Path, required=True)
    observe.add_argument("--overall", type=int, required=True)
    observe.add_argument("--player-id", required=True)
    observe.add_argument(
        "--provenance",
        choices=("manager", "platform-autodraft", "external"),
        default="external",
    )
    observe.set_defaults(handler=command_observe_pick)

    recommendation = commands.add_parser("recommend")
    recommendation.add_argument("--run", type=Path, required=True)
    recommendation.add_argument("--top", type=int, default=3)
    recommendation.add_argument("--json", action="store_true", dest="json_output")
    recommendation.set_defaults(handler=command_recommend)

    turn = commands.add_parser("turn")
    turn.add_argument("--run", type=Path, required=True)
    turn.add_argument("--observed-state", required=True)
    turn.add_argument("--json", action="store_true", dest="json_output")
    turn.set_defaults(handler=command_turn)

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

    reconcile_command = commands.add_parser("reconcile")
    reconcile_command.add_argument("--run", type=Path, required=True)
    reconcile_command.add_argument("--observed-state", required=True)
    reconcile_command.set_defaults(handler=command_reconcile)

    approval = commands.add_parser("approve-pick")
    approval.add_argument("--run", type=Path, required=True)
    approval.add_argument("--observed-state", required=True)
    approval.add_argument("--player-id", required=True)
    approval.add_argument("--queue", nargs="+", required=True)
    approval.set_defaults(handler=command_approve_pick)

    submitted = commands.add_parser("mark-submitted")
    submitted.add_argument("--run", type=Path, required=True)
    submitted.add_argument("--intent-id", required=True)
    submitted.add_argument("--observed-state", required=True)
    submitted.set_defaults(handler=command_mark_submitted)

    cancelled = commands.add_parser("cancel-intent")
    cancelled.add_argument("--run", type=Path, required=True)
    cancelled.add_argument("--intent-id", required=True)
    cancelled.add_argument("--reason", required=True)
    cancelled.set_defaults(handler=command_cancel_intent)

    verified = commands.add_parser("verify-pick")
    verified.add_argument("--run", type=Path, required=True)
    verified.add_argument("--intent-id", required=True)
    verified.add_argument("--observed-state", required=True)
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
