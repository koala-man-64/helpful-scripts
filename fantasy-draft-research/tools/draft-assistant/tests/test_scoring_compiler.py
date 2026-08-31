from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from fantasy_draft_assistant.compiler import compiled_board_dict, compile_board, derive_replacement_levels
from fantasy_draft_assistant.importers import load_compiled_board
from fantasy_draft_assistant.models import (
    AcquisitionMethod,
    LeagueConfig,
    DraftState,
    PlayerEvidence,
    SignalRole,
    SourceArtifact,
)
from fantasy_draft_assistant.research import ResearchBundle, merge_bundles
from fantasy_draft_assistant.optimizer import board_hash
from fantasy_draft_assistant.recommendation import recommend_turn
from fantasy_draft_assistant.scoring import (
    score_projection,
    scoring_context,
    scoring_context_matches,
)


NOW = datetime(2026, 8, 31, 18, tzinfo=timezone.utc)


def _config(**updates):
    values = {
        "active_teams": 2,
        "maximum_teams": 2,
        "draft_slots": ("ours", "other"),
        "rounds": 4,
        "draft_position": 1,
        "pick_clock_seconds": 60,
        "our_team": "ours",
        "roster": {"RB": 1, "WR": 1, "FLEX": 1, "BENCH": 1},
        "flex_positions": ("RB", "WR", "TE"),
        "keepers": (),
        "scoring_format": "standard",
        "mandatory_freshness_hours": 72,
    }
    values.update(updates)
    return LeagueConfig(**values)


def _artifact(
    label: str,
    role: SignalRole,
    *,
    family: str,
    retrieved_at: datetime = NOW,
    freshness_hours: float = 72,
    mandatory: bool = False,
    context: str | None = None,
    provenance=None,
):
    return SourceArtifact(
        source=label,
        upstream_family=family,
        signal_role=role,
        scoring_context=context,
        acquisition_method=AcquisitionMethod.MANUAL_SNAPSHOT,
        published_at=retrieved_at,
        retrieved_at=retrieved_at,
        checksum=hashlib.sha256(label.encode()).hexdigest(),
        freshness_hours=freshness_hours,
        safe_provenance=provenance or {"adapter": "test"},
        mandatory=mandatory,
    )


def _bundle(*, stale_adp: bool = False, projection_mandatory: bool = True):
    projection = _artifact(
        "projections",
        SignalRole.PROJECTION,
        family="projection-family",
        mandatory=projection_mandatory,
    )
    tier = _artifact(
        "tiers",
        SignalRole.TIER,
        family="fantasypros-consensus",
        context="standard",
    )
    adp_time = NOW - timedelta(hours=25) if stale_adp else NOW
    yahoo = _artifact(
        "yahoo-adp",
        SignalRole.ADP,
        family="yahoo",
        retrieved_at=adp_time,
        freshness_hours=24,
    )
    specs = [
        ("rb-1", "Runner One", "DET", "RB", 30.0, 1, 1.0),
        ("rb-2", "Runner Two", "GB", "RB", 25.0, 2, 5.0),
        ("rb-3", "Runner Three", "CHI", "RB", 19.0, 3, 9.0),
        ("rb-4", "Runner Four", "MIN", "RB", 10.0, 4, 14.0),
        ("wr-1", "Receiver One", "DAL", "WR", 28.0, 1, 2.0),
        ("wr-2", "Receiver Two", "SEA", "WR", 24.0, 2, 6.0),
        ("wr-3", "Receiver Three", "LAR", "WR", 17.0, 3, 10.0),
        ("wr-4", "Receiver Four", "NYG", "WR", 9.0, 4, 15.0),
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
                    artifact_checksum=yahoo.checksum,
                    platform="yahoo",
                    adp=adp,
                ),
            ]
        )
    return ResearchBundle((projection, tier, yahoo), tuple(evidence))


def test_scoring_presets_and_overrides_are_explicit():
    stats = {"receiving_yards": 100, "receptions": 10}
    assert score_projection(stats, "standard") == 10
    assert score_projection(stats, "half-ppr") == 15
    assert score_projection(stats, "ppr") == 20
    assert score_projection(stats, "ppr", {"receptions": 2}) == 30
    context = scoring_context("ppr", {"receptions": 2})
    assert scoring_context_matches(context, "ppr", {"receptions": 2})
    assert not scoring_context_matches("ppr", "ppr", {"receptions": 2})


def test_v1_league_scope_rejects_auction_dynasty_best_ball_superflex_and_idp():
    with pytest.raises(ValueError, match="auction"):
        _config(draft_type="auction")
    with pytest.raises(ValueError, match="dynasty"):
        _config(league_type="dynasty")
    value = _config().to_dict()
    value["best_ball"] = True
    with pytest.raises(ValueError, match="best-ball"):
        LeagueConfig.from_dict(value)
    with pytest.raises(ValueError, match="superflex"):
        _config(flex_positions=("QB", "RB", "WR", "TE"))
    with pytest.raises(ValueError, match="IDP"):
        _config(roster={"RB": 1, "LB": 1, "BENCH": 2})


def test_flex_allocation_uses_first_player_outside_final_starter_demand():
    config = _config()
    players, _ = compile_board(config, _bundle(), now=NOW)
    levels = derive_replacement_levels(players, config)
    # Two direct starters per position plus the best two flex players (RB3 and
    # WR3) leaves RB4/WR4 as the first players outside total starter demand.
    assert levels == {"RB": 10.0, "WR": 9.0}
    by_id = {player.player_id: player for player in players}
    assert by_id["rb-1"].replacement_baseline == 10.0
    assert by_id["rb-1"].vbd == 20.0
    assert by_id["wr-1"].replacement_baseline == 9.0
    assert by_id["wr-1"].vbd == 19.0


def test_optional_stale_evidence_is_omitted_not_neutralized():
    players, manifest = compile_board(_config(), _bundle(stale_adp=True), now=NOW)
    assert all(player.platform_adps == {} for player in players)
    assert any(item.startswith("stale_optional_artifact:yahoo-adp") for item in manifest.omissions)
    stale_id = next(
        artifact_id
        for artifact_id, audit in manifest.artifact_freshness.items()
        if audit["signal_role"] == "adp"
    )
    assert stale_id in manifest.artifact_checksums
    assert manifest.artifact_freshness[stale_id]["fresh_at_freeze"] is False
    assert manifest.artifact_freshness[stale_id]["freshness_limit_hours"] == 24


def test_role_default_freshness_is_a_cap_not_a_source_controlled_extension():
    status = _artifact(
        "old-status",
        SignalRole.STATUS,
        family="nfl-official",
        retrieved_at=NOW - timedelta(hours=7),
        freshness_hours=72,
        provenance={"status_authority": "official"},
    )
    assert status.freshness_limit_hours == 6
    assert not status.is_fresh(NOW)


def test_platform_adps_remain_three_independent_timing_signals():
    base = _bundle()
    espn = _artifact("espn-adp", SignalRole.ADP, family="espn")
    sleeper = _artifact("sleeper-adp", SignalRole.ADP, family="sleeper")
    extra = []
    projections = {
        item.player_id: item
        for item in base.evidence
        if item.signal_role is SignalRole.PROJECTION
    }
    for player_id, item in projections.items():
        extra.extend(
            [
                PlayerEvidence(
                    player_id, item.name, item.nfl_team, item.position, SignalRole.ADP,
                    espn.checksum, adp=20, platform="espn",
                ),
                PlayerEvidence(
                    player_id, item.name, item.nfl_team, item.position, SignalRole.ADP,
                    sleeper.checksum, adp=30, platform="sleeper",
                ),
            ]
        )
    players, manifest = compile_board(
        _config(),
        ResearchBundle((*base.artifacts, espn, sleeper), (*base.evidence, *extra)),
        now=NOW,
    )
    assert all(set(player.platform_adps) == {"yahoo", "espn", "sleeper"} for player in players)
    assert manifest.selected_source_families["adp:yahoo"] == "yahoo"
    assert manifest.selected_source_families["adp:espn"] == "espn"
    assert manifest.selected_source_families["adp:sleeper"] == "sleeper"


def test_stale_mandatory_artifact_blocks_compilation():
    bundle = _bundle()
    projection = bundle.artifacts[0]
    stale = SourceArtifact(
        source=projection.source,
        upstream_family=projection.upstream_family,
        signal_role=projection.signal_role,
        scoring_context=projection.scoring_context,
        acquisition_method=projection.acquisition_method,
        published_at=NOW - timedelta(hours=73),
        retrieved_at=NOW - timedelta(hours=73),
        checksum=projection.checksum,
        freshness_hours=72,
        safe_provenance=projection.safe_provenance,
        mandatory=True,
    )
    with pytest.raises(ValueError, match="mandatory projection artifact.*stale"):
        compile_board(_config(), ResearchBundle((stale, *bundle.artifacts[1:]), bundle.evidence), now=NOW)


def test_pre_scored_projection_requires_exact_override_context():
    config = _config(scoring_format="ppr", scoring_overrides={"receptions": 1.25})
    projection = _artifact(
        "pre-scored",
        SignalRole.PROJECTION,
        family="projection-family",
        mandatory=True,
        context="ppr",
    )
    tier = _artifact("single-tier", SignalRole.TIER, family="fantasypros-consensus")
    evidence = (
        PlayerEvidence(
            "player", "Player One", "DET", "RB", SignalRole.PROJECTION,
            projection.checksum, projection_points=100,
        ),
        PlayerEvidence(
            "player", "Player One", "DET", "RB", SignalRole.TIER,
            tier.checksum, tier=1,
        ),
    )
    with pytest.raises(ValueError, match="no players have compatible"):
        compile_board(config, ResearchBundle((projection, tier), evidence), now=NOW)


def test_same_family_tier_mirror_is_counted_once_and_conflict_recorded():
    bundle = _bundle()
    old_tier = _artifact(
        "old-tier-mirror",
        SignalRole.TIER,
        family="fantasypros-consensus",
        retrieved_at=NOW - timedelta(hours=1),
        context="standard",
    )
    duplicate = PlayerEvidence(
        "rb-1", "Runner One", "DET", "RB", SignalRole.TIER,
        old_tier.checksum, tier=9,
    )
    players, manifest = compile_board(
        _config(),
        ResearchBundle((*bundle.artifacts, old_tier), (*bundle.evidence, duplicate)),
        now=NOW,
    )
    rb = next(player for player in players if player.player_id == "rb-1")
    assert rb.independent_tier == 1
    assert manifest.selected_source_families["tier"] == "fantasypros-consensus"
    assert any(item.startswith("same_family_conflict:rb-1:tier") for item in manifest.conflicts)


def test_teamless_boris_tier_joins_only_to_unique_projection_identity():
    base = _bundle()
    projection_artifact, _, adp_artifact = base.artifacts
    projections_and_adp = tuple(
        item for item in base.evidence if item.signal_role is not SignalRole.TIER
    )
    boris = _artifact(
        "boris-tier",
        SignalRole.TIER,
        family="fantasypros-consensus",
        context="standard",
    )
    tiers = []
    for item in base.evidence:
        if item.signal_role is SignalRole.TIER:
            tiers.append(
                PlayerEvidence(
                    player_id=f"boris:{item.player_id}",
                    name=item.name,
                    nfl_team=None,
                    position=item.position,
                    signal_role=SignalRole.TIER,
                    artifact_checksum=boris.checksum,
                    tier=item.tier,
                    ambiguous=True,
                )
            )
    players, _ = compile_board(
        _config(),
        ResearchBundle((projection_artifact, adp_artifact, boris), (*projections_and_adp, *tiers)),
        now=NOW,
    )
    assert {player.player_id for player in players} == {
        "rb-1", "rb-2", "rb-3", "rb-4", "wr-1", "wr-2", "wr-3", "wr-4"
    }
    assert all(not player.ambiguous for player in players)


def test_only_official_team_or_nfl_artifact_controls_material_status():
    base = _bundle()
    sleeper_status = _artifact("sleeper-status", SignalRole.STATUS, family="sleeper")
    sleeper_item = PlayerEvidence(
        "rb-1", "Runner One", "DET", "RB", SignalRole.STATUS,
        sleeper_status.checksum, status="out",
    )
    players, manifest = compile_board(
        _config(),
        ResearchBundle((*base.artifacts, sleeper_status), (*base.evidence, sleeper_item)),
        now=NOW,
    )
    rb = next(player for player in players if player.player_id == "rb-1")
    assert rb.status == "unknown"
    assert any(item.startswith("non_authoritative_status_omitted") for item in manifest.omissions)

    official_status = _artifact(
        "official-status",
        SignalRole.STATUS,
        family="nfl-official",
        provenance={"adapter": "manual", "status_authority": "official"},
    )
    official_item = PlayerEvidence(
        "rb-1", "Runner One", "DET", "RB", SignalRole.STATUS,
        official_status.checksum, status="out",
    )
    players, _ = compile_board(
        _config(),
        ResearchBundle((*base.artifacts, official_status), (*base.evidence, official_item)),
        now=NOW,
    )
    rb = next(player for player in players if player.player_id == "rb-1")
    assert rb.status == "out"
    assert rb.status_band == 3


def test_material_news_creates_parent_linked_revision():
    config = _config()
    players, parent = compile_board(config, _bundle(), now=NOW)
    news_time = NOW + timedelta(hours=1)
    news = _artifact(
        "official-news",
        SignalRole.NEWS,
        family="nfl-official",
        retrieved_at=news_time,
        freshness_hours=6,
    )
    item = PlayerEvidence(
        "rb-1", "Runner One", "DET", "RB", SignalRole.NEWS,
        news.checksum, news="Cleared to play",
    )
    revised_bundle = ResearchBundle((*_bundle().artifacts, news), (*_bundle().evidence, item))
    revised, manifest = compile_board(
        config,
        revised_bundle,
        now=news_time,
        parent_manifest=parent,
    )
    assert players and revised
    assert manifest.revision == 2
    assert manifest.parent_board_hash == parent.board_hash
    assert manifest.revision_history[-1].reason == "material_news"


def test_compiled_board_round_trip_hash_and_recommendation_contract(tmp_path):
    config = _config()
    players, manifest = compile_board(config, _bundle(), now=NOW)
    assert board_hash(players) == manifest.board_hash
    path = tmp_path / "board.json"
    import json

    path.write_text(json.dumps(compiled_board_dict(players, manifest)), encoding="utf-8")
    restored, restored_manifest = load_compiled_board(path)
    assert board_hash(restored) == restored_manifest.board_hash == manifest.board_hash
    result = recommend_turn(
        config,
        restored,
        DraftState(current_pick=1, current_team="ours"),
        now=NOW,
    )
    assert result.primary in {player.player_id for player in restored}
    assert result.envelope.board_hash == manifest.board_hash


def test_raw_projection_rejects_unknown_stat_names():
    with pytest.raises(ValueError, match="unsupported projected stats: recieving_yards"):
        score_projection({"recieving_yards": 100}, "standard")


def test_research_evidence_requires_a_same_role_artifact():
    projection = _artifact(
        "projection-only",
        SignalRole.PROJECTION,
        family="projection-family",
        mandatory=True,
    )
    tier = PlayerEvidence(
        "player", "Player", "DET", "RB", SignalRole.TIER, projection.checksum, tier=1
    )
    with pytest.raises(ValueError, match="same-role artifact"):
        ResearchBundle((projection,), (tier,))


def test_merge_rejects_metadata_mutation_under_the_same_checksum_and_role():
    artifact = _artifact("tier", SignalRole.TIER, family="family", context="standard")
    mutated = replace(artifact, source="mutated-source")
    with pytest.raises(ValueError, match="conflicting artifact metadata"):
        merge_bundles(ResearchBundle((artifact,), ()), ResearchBundle((mutated,), ()))


def test_incompatible_newer_tier_family_cannot_hide_a_compatible_family():
    base = _bundle()
    incompatible = _artifact(
        "newer-ppr-tiers", SignalRole.TIER, family="newer-family", context="ppr"
    )
    extra = tuple(
        replace(item, artifact_checksum=incompatible.checksum)
        for item in base.evidence
        if item.signal_role is SignalRole.TIER
    )
    players, compiled_manifest = compile_board(
        _config(),
        ResearchBundle((*base.artifacts, incompatible), (*base.evidence, *extra)),
        now=NOW,
    )
    assert players
    assert compiled_manifest.selected_source_families["tier"] == "fantasypros-consensus"
    assert any("newer-ppr-tiers" in conflict for conflict in compiled_manifest.conflicts)


def test_tier_scoring_context_must_match_the_league():
    with pytest.raises(ValueError, match="compatible fresh projection and independent-tier"):
        compile_board(_config(scoring_format="ppr"), _bundle(), now=NOW)


def test_board_revision_requires_same_config_and_strictly_later_freeze():
    _, parent = compile_board(_config(), _bundle(), now=NOW)
    with pytest.raises(ValueError, match="frozen after its parent"):
        compile_board(_config(), _bundle(), now=NOW, parent_manifest=parent)
    with pytest.raises(ValueError, match="config hash"):
        compile_board(
            _config(scoring_format="ppr"),
            _bundle(),
            now=NOW + timedelta(minutes=1),
            parent_manifest=parent,
        )
    with pytest.raises(ValueError, match="revision_reason requires"):
        compile_board(_config(), _bundle(), now=NOW, revision_reason="manual-news")


def test_excluded_high_projection_does_not_distort_replacement_baseline():
    base = _bundle()
    status_artifact = _artifact(
        "official-status",
        SignalRole.STATUS,
        family="nfl-official",
        provenance={"status_authority": "official"},
    )
    status = PlayerEvidence(
        "rb-1",
        "Runner One",
        "DET",
        "RB",
        SignalRole.STATUS,
        status_artifact.checksum,
        status="out",
    )
    config = _config(
        active_teams=1,
        maximum_teams=1,
        draft_slots=("ours",),
        rounds=2,
        roster={"RB": 1, "BENCH": 1},
    )
    players, _ = compile_board(
        config,
        ResearchBundle((*base.artifacts, status_artifact), (*base.evidence, status)),
        now=NOW,
    )
    by_id = {player.player_id: player for player in players}
    assert by_id["rb-1"].status_band == 3
    assert by_id["rb-2"].replacement_baseline == 19.0


def test_current_identity_team_controls_a_unique_traded_player_join():
    base = _bundle()
    identity_artifact = _artifact(
        "current-identities",
        SignalRole.IDENTITY,
        family="sleeper",
        freshness_hours=24,
    )
    identity = PlayerEvidence(
        "sleeper-rb-1",
        "Runner One",
        "HOU",
        "RB",
        SignalRole.IDENTITY,
        identity_artifact.checksum,
    )
    players, compiled_manifest = compile_board(
        _config(),
        ResearchBundle((*base.artifacts, identity_artifact), (*base.evidence, identity)),
        now=NOW,
    )
    player = next(item for item in players if item.player_id == "rb-1")
    assert player.nfl_team == "HOU"
    assert not player.ambiguous
    assert any(
        item.startswith("identity_team_conflict:") for item in compiled_manifest.conflicts
    )
