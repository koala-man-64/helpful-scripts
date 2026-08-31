"""Traceable research compilation into a frozen league-specific board."""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import io
import json
from typing import Any, Iterable, Mapping, Sequence

from .models import (
    BoardManifest,
    BoardRevision,
    CompiledPlayer,
    DraftPlatform,
    JsonValue,
    LeagueConfig,
    PersonalPreference,
    PlayerEvidence,
    PlayerSnapshot,
    SignalRole,
    SourceArtifact,
    parse_datetime,
)
from .research import ResearchBundle
from .scoring import score_projection, scoring_context_matches


BOARD_SCHEMA_VERSION = "fantasy-draft-compiled-board-v1"
BOARD_SCHEMA_HASH = hashlib.sha256(BOARD_SCHEMA_VERSION.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _Projected:
    player_id: str
    name: str
    nfl_team: str
    position: str
    projected_points: float
    tier: int
    platform_adps: Mapping[str, float]
    status: str
    status_band: int
    risk_band: int
    preference: int
    evidence_refs: tuple[str, ...]
    ambiguous: bool


def _stable_hash(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _position_tokens(position: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part for part in position.split("/") if part))


def _primary_position(player: Any) -> str:
    return _position_tokens(str(player.position))[0]


def _projected_points(player: Any) -> float:
    if hasattr(player, "projected_points"):
        return float(player.projected_points)
    return float(player.projection)


def allocate_flex_demand(
    players: Iterable[Any],
    config: LeagueConfig,
) -> dict[str, int]:
    """Allocate league-wide flex starters to the best remaining eligible players."""

    supported = {"QB", "RB", "WR", "TE", "K", "DEF"}
    direct = {
        position: config.active_teams * int(config.roster.get(position, 0))
        for position in supported
    }
    flex_count = config.active_teams * int(config.roster.get("FLEX", 0))
    by_position: dict[str, list[Any]] = {position: [] for position in supported}
    for player in players:
        primary = _primary_position(player)
        if primary in by_position:
            by_position[primary].append(player)
    for values in by_position.values():
        values.sort(key=lambda item: (-_projected_points(item), str(item.player_id)))

    pool: list[tuple[float, str, str]] = []
    for position in sorted(set(config.flex_positions)):
        if position not in by_position:
            continue
        for player in by_position[position][direct[position] :]:
            pool.append((-_projected_points(player), str(player.player_id), position))
    pool.sort()
    for _, _, position in pool[:flex_count]:
        direct[position] += 1
    return direct


def derive_replacement_levels(
    players: Iterable[Any],
    config: LeagueConfig,
) -> dict[str, float]:
    """Derive positional baselines from starters plus deterministic flex demand."""

    player_list = list(players)
    demand = allocate_flex_demand(player_list, config)
    by_position: dict[str, list[Any]] = {}
    for player in player_list:
        by_position.setdefault(_primary_position(player), []).append(player)
    levels: dict[str, float] = {}
    for position, values in by_position.items():
        values.sort(key=lambda item: (-_projected_points(item), str(item.player_id)))
        required = demand.get(position, 0)
        if not values:
            continue
        # Replacement is the first player outside league-wide starter demand.
        # If the supplied board is shallower than demand, clamp to its last
        # player; a position with no configured demand uses its top player.
        index = min(len(values) - 1, max(0, required))
        levels[position] = _projected_points(values[index])
    return levels


def _status_band(status: str | None) -> int:
    normalized = (status or "unknown").strip().casefold()
    if normalized in {"active", "available", "healthy", "probable", "full"}:
        return 0
    if normalized in {"questionable", "limited", "unknown", "day-to-day"}:
        return 1
    if normalized in {"doubtful", "pup", "nfi", "suspended-pending"}:
        return 2
    if normalized in {"out", "ir", "inactive", "suspended", "retired"}:
        return 3
    return 1


def _artifact_for(
    item: PlayerEvidence,
    artifacts: Sequence[SourceArtifact],
) -> SourceArtifact:
    matches = [
        artifact
        for artifact in artifacts
        if artifact.checksum == item.artifact_checksum and artifact.signal_role is item.signal_role
    ]
    if len(matches) != 1:
        raise ValueError(
            f"evidence for {item.player_id!r} does not resolve to exactly one same-role artifact"
        )
    return matches[0]


def _role_key(item: PlayerEvidence) -> str:
    if item.signal_role is SignalRole.ADP:
        if item.platform is None:
            raise ValueError("ADP evidence is missing its platform")
        return f"adp:{item.platform.value}"
    return item.signal_role.value


def _normalize_selected_families(value: Mapping[Any, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_key, family in (value or {}).items():
        key = raw_key.value if isinstance(raw_key, SignalRole) else str(raw_key).strip().casefold()
        if not key or not isinstance(family, str) or not family.strip():
            raise ValueError("selected source family keys and values must be non-empty")
        result[key] = family.strip()
    return result


def _is_discovery_only(artifact: SourceArtifact) -> bool:
    source = artifact.source.casefold()
    return (
        "reddit" in source
        or "nbc" in source
        or artifact.safe_provenance.get("discovery_only") is True
    )


def _is_official_status_authority(artifact: SourceArtifact) -> bool:
    authority = artifact.safe_provenance.get("status_authority")
    if isinstance(authority, str) and authority.casefold() == "official":
        return True
    return artifact.upstream_family.casefold() in {
        "nfl-official",
        "official-team",
        "official-team-reporting",
        "nfl-team-official",
    }


def _evidence_value(item: PlayerEvidence) -> Any:
    if item.signal_role is SignalRole.PROJECTION:
        return item.projection_points if item.projection_points is not None else dict(item.projected_stats)
    if item.signal_role is SignalRole.TIER:
        return item.tier
    if item.signal_role is SignalRole.ADP:
        return item.adp
    if item.signal_role is SignalRole.STATUS:
        return item.status
    if item.signal_role is SignalRole.NEWS:
        return item.news
    if item.signal_role is SignalRole.RISK:
        return item.risk_band
    return item.normalized_identity


def _evidence_context_compatible(
    item: PlayerEvidence,
    artifact: SourceArtifact,
    config: LeagueConfig,
) -> bool:
    context = (artifact.scoring_context or "").strip().casefold()
    if context == "context-neutral":
        return True
    if item.signal_role in {SignalRole.TIER, SignalRole.RANKING}:
        return scoring_context_matches(
            artifact.scoring_context,
            config.scoring_format,
            config.scoring_overrides,
        )
    if item.signal_role is SignalRole.PROJECTION and item.projection_points is not None:
        return scoring_context_matches(
            artifact.scoring_context,
            config.scoring_format,
            config.scoring_overrides,
        )
    return True


def _canonicalize_bundle(bundle: ResearchBundle) -> tuple[ResearchBundle, list[str]]:
    """Join platform/source IDs through exact normalized name, team, position.

    Team-less tier rows (notably Boris CSV) are joined only when name+position
    resolves to exactly one full identity.  Ambiguity remains fail-closed.
    """

    by_full_identity: dict[tuple[str, str, str], list[PlayerEvidence]] = {}
    full_keys_by_partial: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
    identity_keys_by_partial: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
    for item in bundle.evidence:
        if not item.nfl_team:
            continue
        key = (item.normalized_name, item.nfl_team, item.position)
        by_full_identity.setdefault(key, []).append(item)
        partial = (item.normalized_name, item.position)
        full_keys_by_partial.setdefault(partial, set()).add(key)
        if item.signal_role is SignalRole.IDENTITY:
            identity_keys_by_partial.setdefault(partial, set()).add(key)

    canonical_by_full: dict[tuple[str, str, str], str] = {}
    conflicts: list[str] = []
    for key, items in by_full_identity.items():
        projection_ids = sorted(
            {item.player_id for item in items if item.signal_role is SignalRole.PROJECTION}
        )
        if len(projection_ids) > 1:
            conflicts.append(f"duplicate_projection_identity:{'|'.join(key)}")
        canonical_by_full[key] = projection_ids[0] if projection_ids else min(item.player_id for item in items)

    authoritative_by_partial = {
        partial: next(iter(keys))
        for partial, keys in identity_keys_by_partial.items()
        if len(keys) == 1
    }
    for partial, authoritative_key in authoritative_by_partial.items():
        keys = full_keys_by_partial[partial]
        if len(keys) <= 1:
            continue
        projection_ids = sorted(
            {
                item.player_id
                for key in keys
                for item in by_full_identity[key]
                if item.signal_role is SignalRole.PROJECTION
            }
        )
        if len(projection_ids) == 1:
            canonical_by_full[authoritative_key] = projection_ids[0]
        conflicts.append(
            "identity_team_conflict:"
            f"{'|'.join(partial)}:authoritative={authoritative_key[1]}:observed="
            + ",".join(sorted(key[1] for key in keys))
        )

    canonical: list[PlayerEvidence] = []
    for item in bundle.evidence:
        partial = (item.normalized_name, item.position)
        authoritative_key = authoritative_by_partial.get(partial)
        if item.nfl_team:
            key = (item.normalized_name, item.nfl_team, item.position)
            if authoritative_key is not None:
                canonical.append(
                    replace(
                        item,
                        player_id=canonical_by_full[authoritative_key],
                        nfl_team=authoritative_key[1],
                    )
                )
            else:
                canonical.append(replace(item, player_id=canonical_by_full[key]))
            continue
        candidates = (
            {authoritative_key}
            if authoritative_key is not None
            else full_keys_by_partial.get(partial, set())
        )
        if len(candidates) == 1:
            key = next(iter(candidates))
            canonical.append(
                replace(
                    item,
                    player_id=canonical_by_full[key],
                    nfl_team=key[1],
                    ambiguous=False,
                )
            )
        else:
            canonical.append(item)
            if len(candidates) > 1:
                conflicts.append(f"ambiguous_partial_identity:{item.player_id}")
    return ResearchBundle(bundle.artifacts, tuple(canonical), bundle.schema_version), conflicts


def _select_evidence(
    bundle: ResearchBundle,
    *,
    now: datetime,
    config: LeagueConfig,
    selected_families: Mapping[Any, str] | None,
) -> tuple[
    list[tuple[PlayerEvidence, SourceArtifact]],
    dict[str, str],
    list[str],
    list[str],
]:
    omissions: list[str] = []
    conflicts: list[str] = []
    fresh_artifacts: list[SourceArtifact] = []
    for artifact in bundle.artifacts:
        if artifact.is_fresh(now):
            fresh_artifacts.append(artifact)
        elif artifact.mandatory:
            raise ValueError(
                f"mandatory {artifact.signal_role.value} artifact {artifact.source!r} is stale"
            )
        else:
            omissions.append(f"stale_optional_artifact:{artifact.artifact_id}")

    attached: list[tuple[PlayerEvidence, SourceArtifact]] = []
    for item in bundle.evidence:
        artifact = _artifact_for(item, bundle.artifacts)
        if artifact not in fresh_artifacts:
            continue
        if _is_discovery_only(artifact) and item.signal_role in {
            SignalRole.PROJECTION,
            SignalRole.TIER,
            SignalRole.ADP,
            SignalRole.STATUS,
            SignalRole.RISK,
        }:
            omissions.append(f"discovery_only_not_decision_input:{artifact.artifact_id}")
            continue
        if item.signal_role is SignalRole.STATUS and not _is_official_status_authority(artifact):
            omissions.append(f"non_authoritative_status_omitted:{artifact.artifact_id}")
            continue
        if not _evidence_context_compatible(item, artifact, config):
            conflicts.append(f"scoring_context_mismatch:{item.player_id}:{artifact.artifact_id}")
            continue
        attached.append((item, artifact))

    requested = _normalize_selected_families(selected_families)
    families_by_role: dict[str, dict[str, datetime]] = {}
    for item, artifact in attached:
        key = _role_key(item)
        family_times = families_by_role.setdefault(key, {})
        family_times[artifact.upstream_family] = max(
            family_times.get(artifact.upstream_family, datetime.min.replace(tzinfo=timezone.utc)),
            artifact.freshness_at,
        )

    selected: dict[str, str] = {}
    for key, family_times in sorted(families_by_role.items()):
        if key in requested:
            if requested[key] not in family_times:
                raise ValueError(
                    f"selected source family {requested[key]!r} has no fresh evidence for {key}"
                )
            selected[key] = requested[key]
        else:
            selected[key] = sorted(
                family_times,
                key=lambda family: (-family_times[family].timestamp(), family),
            )[0]
        if len(family_times) > 1:
            conflicts.append(
                f"source_family_conflict:{key}:selected={selected[key]}:omitted="
                + ",".join(sorted(set(family_times).difference({selected[key]})))
            )

    chosen = [
        (item, artifact)
        for item, artifact in attached
        if artifact.upstream_family == selected[_role_key(item)]
    ]

    # Within one upstream family, retain only the newest signal rather than
    # counting mirrors/presentations as independent votes.
    deduped: dict[tuple[str, str], tuple[PlayerEvidence, SourceArtifact]] = {}
    for item, artifact in sorted(
        chosen,
        key=lambda pair: (
            pair[0].player_id,
            _role_key(pair[0]),
            pair[1].freshness_at,
            pair[1].checksum,
        ),
    ):
        key = (item.player_id, _role_key(item))
        previous = deduped.get(key)
        if previous is not None and _evidence_value(previous[0]) != _evidence_value(item):
            conflicts.append(
                f"same_family_conflict:{item.player_id}:{_role_key(item)}:selected={artifact.artifact_id}"
            )
        deduped[key] = (item, artifact)
    return list(deduped.values()), selected, omissions, conflicts


def _same_position_tier_drops(players: Sequence[_Projected], baselines: Mapping[str, float]) -> dict[str, float]:
    result: dict[str, float] = {}
    by_position: dict[str, list[_Projected]] = {}
    for player in players:
        by_position.setdefault(_primary_position(player), []).append(player)
    for position, values in by_position.items():
        values.sort(key=lambda item: (item.tier, -item.projected_points, item.player_id))
        baseline = baselines.get(position, 0.0)
        for player in values:
            next_tier = next((item for item in values if item.tier > player.tier), None)
            comparison = next_tier.projected_points if next_tier else baseline
            result[player.player_id] = max(0.0, round(player.projected_points - comparison, 6))
    return result


def compile_board(
    config: LeagueConfig,
    bundle: ResearchBundle,
    *,
    now: datetime | None = None,
    selected_source_families: Mapping[Any, str] | None = None,
    personal_preferences: Mapping[str, int | str] | None = None,
    parent_manifest: BoardManifest | None = None,
    revision_reason: str | None = None,
) -> tuple[list[CompiledPlayer], BoardManifest]:
    """Compile a deterministic frozen board with traceable source selection."""

    frozen_at = parse_datetime(now or datetime.now(timezone.utc), "now")
    config_digest = _stable_hash(config.to_dict())
    if parent_manifest is None and revision_reason is not None:
        raise ValueError("revision_reason requires a parent board manifest")
    if parent_manifest is not None:
        if parent_manifest.schema_hash != BOARD_SCHEMA_HASH:
            raise ValueError("parent board schema hash does not match this compiler")
        if parent_manifest.config_hash != config_digest:
            raise ValueError("parent board config hash does not match the league configuration")
        if frozen_at <= parent_manifest.frozen_at:
            raise ValueError("board revision must be frozen after its parent")
    canonical_bundle, identity_conflicts = _canonicalize_bundle(bundle)
    selected_evidence, selected, omissions, conflicts = _select_evidence(
        canonical_bundle,
        now=frozen_at,
        config=config,
        selected_families=selected_source_families,
    )
    conflicts.extend(identity_conflicts)
    by_player: dict[str, dict[str, tuple[PlayerEvidence, SourceArtifact]]] = {}
    for item, artifact in selected_evidence:
        by_player.setdefault(item.player_id, {})[_role_key(item)] = (item, artifact)

    preferences: dict[str, int] = {}
    for player_id, raw in (personal_preferences or {}).items():
        if isinstance(raw, str):
            try:
                value = PersonalPreference[raw.strip().upper()].value
            except KeyError as error:
                raise ValueError(f"invalid personal preference for {player_id!r}") from error
        else:
            if isinstance(raw, bool):
                raise ValueError(f"invalid personal preference for {player_id!r}")
            value = int(raw)
        if value not in {-1, 0, 1}:
            raise ValueError(f"personal preference for {player_id!r} must be target, neutral, or avoid")
        preferences[player_id] = value
    unknown_preferences = sorted(set(preferences).difference(by_player))
    if unknown_preferences:
        raise ValueError(
            "personal preferences reference unknown player IDs: " + ", ".join(unknown_preferences)
        )

    projected: list[_Projected] = []
    for player_id in sorted(by_player):
        signals = by_player[player_id]
        projection_entry = signals.get(SignalRole.PROJECTION.value)
        tier_entry = signals.get(SignalRole.TIER.value)
        if projection_entry is None:
            omissions.append(f"player_missing_projection:{player_id}")
            continue
        if tier_entry is None:
            omissions.append(f"player_missing_independent_tier:{player_id}")
            continue
        projection, projection_artifact = projection_entry
        tier_evidence, _ = tier_entry
        if projection.projected_stats:
            points = score_projection(
                projection.projected_stats,
                config.scoring_format,
                config.scoring_overrides,
            )
        else:
            assert projection.projection_points is not None
            points = projection.projection_points

        identity_candidates = [entry[0] for entry in signals.values()]
        identity = projection
        identity_entry = signals.get(SignalRole.IDENTITY.value)
        if identity_entry is not None:
            identity = identity_entry[0]
        names = {item.normalized_name for item in identity_candidates}
        teams = {item.nfl_team for item in identity_candidates if item.nfl_team}
        positions = {item.position for item in identity_candidates}
        ambiguous = any(item.ambiguous for item in identity_candidates)
        if len(names) > 1 or len(teams) > 1 or len(positions) > 1:
            conflicts.append(f"identity_conflict:{player_id}")
            ambiguous = True
        if not identity.nfl_team:
            omissions.append(f"player_missing_team_identity:{player_id}")
            continue

        adps = {
            key.split(":", 1)[1]: entry[0].adp
            for key, entry in signals.items()
            if key.startswith("adp:") and entry[0].adp is not None
        }
        status_entry = signals.get(SignalRole.STATUS.value)
        status = status_entry[0].status if status_entry else "unknown"
        risk_entry = signals.get(SignalRole.RISK.value)
        risk_band = risk_entry[0].risk_band if risk_entry and risk_entry[0].risk_band is not None else 0
        refs = tuple(sorted({artifact.artifact_id for _, artifact in signals.values()}))
        projected.append(
            _Projected(
                player_id=player_id,
                name=identity.name,
                nfl_team=identity.nfl_team,
                position=identity.position,
                projected_points=points,
                tier=int(tier_evidence.tier),
                platform_adps={key: float(value) for key, value in adps.items()},
                status=status or "unknown",
                status_band=_status_band(status),
                risk_band=risk_band,
                preference=preferences.get(player_id, PersonalPreference.NEUTRAL.value),
                evidence_refs=refs,
                ambiguous=ambiguous,
            )
        )

    if not projected:
        raise ValueError("no players have compatible fresh projection and independent-tier evidence")
    eligible_projected = [
        item
        for item in projected
        if not item.ambiguous and item.status_band < 3 and item.risk_band < 3
    ]
    if not eligible_projected:
        raise ValueError("no draftable players remain after status, risk, and identity exclusions")
    baselines = derive_replacement_levels(eligible_projected, config)
    tier_drops = _same_position_tier_drops(eligible_projected, baselines)
    compiled = [
        CompiledPlayer(
            player_id=item.player_id,
            name=item.name,
            nfl_team=item.nfl_team,
            position=item.position,
            projected_points=item.projected_points,
            replacement_baseline=baselines.get(_primary_position(item), 0.0),
            vbd=round(item.projected_points - baselines.get(_primary_position(item), 0.0), 6),
            independent_tier=item.tier,
            platform_adps=item.platform_adps,
            status=item.status,
            status_band=item.status_band,
            risk_band=item.risk_band,
            personal_preference=item.preference,
            same_position_tier_drop=tier_drops.get(item.player_id, 0.0),
            evidence_refs=item.evidence_refs,
            compiled_at=frozen_at,
            ambiguous=item.ambiguous,
        )
        for item in projected
    ]
    compiled.sort(key=lambda item: item.player_id)
    board_payload = []
    for item in compiled:
        row = item.to_dict()
        row.pop("compiled_at", None)
        board_payload.append(row)
    board_digest = _stable_hash(board_payload)
    revision = parent_manifest.revision + 1 if parent_manifest else 1
    parent_hash = parent_manifest.board_hash if parent_manifest else None
    material_news = any(
        item.signal_role is SignalRole.NEWS
        and artifact.freshness_at > parent_manifest.frozen_at
        for item, artifact in selected_evidence
    ) if parent_manifest else False
    reason = revision_reason or ("material_news" if material_news else None)
    if parent_manifest:
        history = parent_manifest.revision_history or (
            BoardRevision(
                revision=parent_manifest.revision,
                frozen_at=parent_manifest.frozen_at,
                board_hash=parent_manifest.board_hash,
                parent_board_hash=parent_manifest.parent_board_hash,
                reason="imported_parent",
            ),
        )
    else:
        history = ()
    current_revision = BoardRevision(
        revision=revision,
        frozen_at=frozen_at,
        board_hash=board_digest,
        parent_board_hash=parent_hash,
        reason=reason,
    )
    history = (*history, current_revision)
    artifact_checksums = {
        artifact.artifact_id: artifact.checksum
        for artifact in sorted(bundle.artifacts, key=lambda item: item.artifact_id)
    }
    artifact_freshness = {
        artifact.artifact_id: {
            "signal_role": artifact.signal_role.value,
            "published_at": (
                artifact.published_at.isoformat() if artifact.published_at is not None else None
            ),
            "retrieved_at": artifact.retrieved_at.isoformat(),
            "freshness_at": artifact.freshness_at.isoformat(),
            "freshness_limit_hours": artifact.freshness_limit_hours,
            "mandatory": artifact.mandatory,
            "fresh_at_freeze": artifact.is_fresh(frozen_at),
        }
        for artifact in sorted(bundle.artifacts, key=lambda item: item.artifact_id)
    }
    manifest = BoardManifest(
        schema_hash=BOARD_SCHEMA_HASH,
        config_hash=config_digest,
        board_hash=board_digest,
        selected_source_families=selected,
        omissions=tuple(sorted(set(omissions))),
        conflicts=tuple(sorted(set(conflicts))),
        artifact_checksums=artifact_checksums,
        frozen_at=frozen_at,
        artifact_freshness=artifact_freshness,
        revision=revision,
        parent_board_hash=parent_hash,
        revision_history=history,
    )
    return compiled, manifest


def compiled_board_dict(
    players: Iterable[CompiledPlayer],
    manifest: BoardManifest,
) -> dict[str, JsonValue]:
    return {
        "players": [item.to_dict() for item in sorted(players, key=lambda player: player.player_id)],
        "manifest": manifest.to_dict(),
    }


def compiled_board_json(players: Iterable[CompiledPlayer], manifest: BoardManifest) -> str:
    return json.dumps(compiled_board_dict(players, manifest), indent=2, sort_keys=True) + "\n"


def compiled_board_csv(players: Iterable[CompiledPlayer]) -> str:
    stream = io.StringIO(newline="")
    columns = (
        "player_id", "name", "nfl_team", "position", "projected_points",
        "replacement_baseline", "vbd", "vbd_band", "independent_tier",
        "yahoo_adp", "espn_adp", "sleeper_adp", "status", "status_band",
        "risk_band", "personal_preference", "same_position_tier_drop", "evidence_refs",
    )
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for player in sorted(players, key=lambda item: (-item.vbd_band, item.independent_tier, item.player_id)):
        writer.writerow(
            {
                "player_id": player.player_id,
                "name": player.name,
                "nfl_team": player.nfl_team,
                "position": player.position,
                "projected_points": player.projected_points,
                "replacement_baseline": player.replacement_baseline,
                "vbd": player.vbd,
                "vbd_band": player.vbd_band,
                "independent_tier": player.independent_tier,
                "yahoo_adp": player.platform_adps.get(DraftPlatform.YAHOO.value, ""),
                "espn_adp": player.platform_adps.get(DraftPlatform.ESPN.value, ""),
                "sleeper_adp": player.platform_adps.get(DraftPlatform.SLEEPER.value, ""),
                "status": player.status,
                "status_band": player.status_band,
                "risk_band": player.risk_band,
                "personal_preference": player.personal_preference,
                "same_position_tier_drop": player.same_position_tier_drop,
                "evidence_refs": "|".join(player.evidence_refs),
            }
        )
    return stream.getvalue()


def compact_cheat_sheet(players: Iterable[CompiledPlayer], limit: int = 60) -> str:
    if limit <= 0:
        raise ValueError("cheat-sheet limit must be positive")
    ordered = sorted(
        players,
        key=lambda item: (-item.vbd_band, item.independent_tier, -item.vbd, item.player_id),
    )[:limit]
    lines = ["#  Player                         Pos  VBD  Tier  ADP"]
    for index, player in enumerate(ordered, start=1):
        adp = player.adp
        adp_text = "-" if adp == float("inf") else f"{adp:.1f}"
        lines.append(
            f"{index:>2} {player.name[:29]:<29} {player.position:<4} {player.vbd:>5.1f} "
            f"{player.independent_tier:>4} {adp_text:>5}"
        )
    return "\n".join(lines) + "\n"
