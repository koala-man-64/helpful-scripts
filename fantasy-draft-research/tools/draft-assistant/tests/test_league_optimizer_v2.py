from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from fantasy_draft_assistant.models import (
    CompiledPlayer,
    DraftPlatform,
    DraftState,
    Keeper,
    LeagueConfig,
    ObservedDraftState,
    PlayerSnapshot,
)
from fantasy_draft_assistant.optimizer import rank_candidates
from fantasy_draft_assistant.recommendation import recommend_turn


NOW = datetime(2026, 8, 31, 18, tzinfo=timezone.utc)
ROOM = "room-fp:" + "a" * 64


def _config(*, rounds=3, roster=None, keepers=(), active_teams=1):
    slots = tuple(f"team-{index}" for index in range(1, active_teams + 1))
    return LeagueConfig(
        active_teams=active_teams,
        maximum_teams=active_teams,
        draft_slots=slots,
        rounds=rounds,
        draft_position=1,
        pick_clock_seconds=60,
        our_team="team-1",
        roster=roster or {"RB": 1, "K": 1, "DEF": 1},
        flex_positions=("RB", "WR", "TE"),
        keepers=keepers,
        scoring_format="standard",
        mandatory_freshness_hours=72,
    )


def _snapshot(player_id, position, projection, tier=1, adp=10, status="active"):
    return PlayerSnapshot(
        player_id=player_id,
        name=player_id.replace("-", " ").title(),
        nfl_team="DET",
        position=position,
        projection=projection,
        tier=tier,
        adp=adp,
        league_fit=0,
        scarcity=0,
        wait_risk=0,
        roster_utility=0,
        risk=0,
        source="synthetic",
        source_family="synthetic",
        checked_at=NOW,
        status=status,
    )


def _compiled(
    player_id,
    position,
    *,
    vbd,
    tier=1,
    yahoo=10,
    espn=10,
    risk=0,
):
    return CompiledPlayer(
        player_id=player_id,
        name=player_id.replace("-", " ").title(),
        nfl_team="DET",
        position=position,
        projected_points=100 + vbd,
        replacement_baseline=100,
        vbd=vbd,
        independent_tier=tier,
        platform_adps={"yahoo": yahoo, "espn": espn},
        risk_band=risk,
        compiled_at=NOW,
    )


def test_kicker_and_defense_endgame_excludes_impossible_extra_skill_pick():
    config = _config()
    players = [
        _snapshot("rb-one", "RB", 30),
        _snapshot("rb-two", "RB", 20),
        _snapshot("k-one", "K", 10),
        _snapshot("def-one", "DEF", 9),
    ]
    state = DraftState(
        current_pick=2,
        current_team="team-1",
        rosters={"team-1": ("rb-one",)},
        unavailable=frozenset({"rb-one"}),
    )
    ranked, excluded = rank_candidates(players, config, state, now=NOW)
    assert {item.player.player_id for item in ranked} == {"k-one", "def-one"}
    assert "roster_completion_impossible" in excluded["rb-two"]


def test_keeper_is_committed_to_roster_matching_before_live_picks():
    keeper = Keeper("rb-keeper", 1, "team-1")
    config = _config(rounds=2, roster={"RB": 1, "K": 1}, keepers=(keeper,))
    players = [
        _snapshot("rb-keeper", "RB", 30, status="keeper"),
        _snapshot("rb-extra", "RB", 20),
        _snapshot("k-one", "K", 10),
    ]
    state = DraftState(current_pick=2, current_team="team-1")
    ranked, excluded = rank_candidates(players, config, state, now=NOW)
    assert [item.player.player_id for item in ranked] == ["k-one"]
    assert "keeper" in excluded["rb-keeper"]
    assert "roster_completion_impossible" in excluded["rb-extra"]


def test_material_vbd_band_precedes_tier_and_wait_risk():
    config = _config(roster={"RB": 1, "BENCH": 2})
    high_band = _compiled("high-band", "RB", vbd=20, tier=5, yahoo=100)
    lower_band = _compiled("lower-band", "RB", vbd=19, tier=1, yahoo=1)
    ranked, _ = rank_candidates([lower_band, high_band], config, DraftState(), now=NOW)
    assert [item.player.player_id for item in ranked] == ["high-band", "lower-band"]


def test_independent_tier_resolves_players_inside_same_ten_point_band():
    config = _config(roster={"RB": 1, "BENCH": 2})
    more_vbd = _compiled("more-vbd", "RB", vbd=29, tier=2)
    better_tier = _compiled("better-tier", "RB", vbd=21, tier=1)
    ranked, _ = rank_candidates([more_vbd, better_tier], config, DraftState(), now=NOW)
    assert [item.player.player_id for item in ranked] == ["better-tier", "more-vbd"]


def test_active_platform_adp_is_a_late_deterministic_tiebreaker():
    config = _config(roster={"RB": 1, "BENCH": 2})
    alpha = _compiled("alpha", "RB", vbd=20, yahoo=3, espn=30)
    bravo = _compiled("bravo", "RB", vbd=20, yahoo=30, espn=3)
    yahoo, _ = rank_candidates(
        [alpha, bravo], config, DraftState(platform=DraftPlatform.YAHOO), now=NOW
    )
    espn, _ = rank_candidates(
        [alpha, bravo], config, DraftState(platform=DraftPlatform.ESPN), now=NOW
    )
    assert yahoo[0].player.player_id == "alpha"
    assert espn[0].player.player_id == "bravo"


def test_observed_position_demand_contributes_to_wait_risk_explanation():
    config = _config(rounds=4, roster={"RB": 1, "BENCH": 3}, active_teams=4)
    players = [
        _compiled("rb-one", "RB", vbd=20, yahoo=100),
        _compiled("rb-two", "RB", vbd=20, yahoo=100),
        _compiled("rb-three", "RB", vbd=20, yahoo=100),
    ]
    observed = ObservedDraftState(
        room_fingerprint=ROOM,
        your_turn=True,
        current_team="team-1",
        overall_pick=1,
        clock_seconds=40,
        roster_count=0,
        rows=(),
        autodraft_off=True,
        captured_at=NOW,
        platform=DraftPlatform.YAHOO,
        positional_demand={"RB": 3},
    )
    ranked, _ = rank_candidates(players, config, DraftState(current_pick=1), now=NOW, observed=observed)
    assert ranked[0].components["wait_risk_band"] == 0
    assert any("tier supply, and demand" in text for text in ranked[0].explanations)


def test_snake_pair_reranks_second_pick_against_first_pick_roster():
    config = _config(rounds=2, roster={"RB": 1, "WR": 1})
    rb_primary = _compiled("rb-primary", "RB", vbd=30, tier=1)
    rb_fallback = _compiled("rb-fallback", "RB", vbd=20, tier=1)
    wr = _compiled("wr-primary", "WR", vbd=19, tier=2)
    state = DraftState(current_pick=1, current_team="team-1")
    result = recommend_turn(config, [rb_primary, rb_fallback, wr], state, now=NOW)
    assert result.primary == "rb-primary"
    assert result.pair_plan == ("rb-primary", "wr-primary")
    assert result.fallback_branches["wr-primary"] == "rb-primary"
    assert set(result.explanations) == {"rb-primary", "rb-fallback", "wr-primary"}
    assert state.rosters == {}
    assert state.unavailable == frozenset()


def test_league_config_rejects_inconsistent_draft_roster_keeper_and_flex_metadata():
    base = _config(active_teams=2)
    with pytest.raises(ValueError, match="draft_position must identify our_team"):
        replace(base, draft_position=2)
    with pytest.raises(ValueError, match="rounds must equal"):
        replace(base, rounds=4)
    with pytest.raises(ValueError, match="keeper overall_pick must belong"):
        replace(base, keepers=(Keeper("wrong-owner", 2, "team-1"),))
    with pytest.raises(ValueError, match="keeper team must appear"):
        replace(base, keepers=(Keeper("unknown-team", 1, "not-a-team"),))
    with pytest.raises(ValueError, match="keeper player IDs must be unique"):
        replace(
            base,
            keepers=(Keeper("duplicate", 1, "team-1"), Keeper("duplicate", 4, "team-1")),
        )
    with pytest.raises(ValueError, match="unsupported multi-position"):
        replace(base, flex_positions=("QB/RB",))


def test_player_models_reject_idp_and_superflex_multi_positions():
    with pytest.raises(ValueError, match="unsupported player position"):
        _snapshot("linebacker", "LB", 10)
    with pytest.raises(ValueError, match="unsupported multi-position"):
        _compiled("hybrid", "QB/RB", vbd=10)


def test_keeper_occupying_corner_pick_suppresses_two_player_pair_plan():
    keeper = Keeper("keeper-rb", 5, "team-1")
    config = _config(
        rounds=4,
        roster={"RB": 1, "WR": 1, "K": 1, "BENCH": 1},
        keepers=(keeper,),
        active_teams=2,
    )
    players = [
        _compiled("keeper-rb", "RB", vbd=30),
        _compiled("wr-one", "WR", vbd=25),
        _compiled("k-one", "K", vbd=15),
        _compiled("bench-rb", "RB", vbd=10),
    ]
    result = recommend_turn(
        config,
        players,
        DraftState(current_pick=4, current_team="team-1"),
        now=NOW,
    )
    assert result.pair_plan is None
    assert result.fallback_branches == {}


def test_endgame_feasibility_requires_actual_remaining_position_supply():
    config = _config(rounds=2, roster={"RB": 1, "K": 1})
    players = [
        _snapshot("rb-one", "RB", 20),
        _snapshot("rb-two", "RB", 10),
    ]
    ranked, excluded = rank_candidates(
        players,
        config,
        DraftState(current_pick=1, current_team="team-1"),
        now=NOW,
    )
    assert ranked == []
    assert all("roster_completion_impossible" in reasons for reasons in excluded.values())


def test_wait_risk_supply_ignores_hard_excluded_same_tier_peers():
    config = _config(rounds=4, roster={"RB": 1, "BENCH": 3}, active_teams=4)
    target = _compiled("target", "RB", vbd=20, yahoo=100)
    excluded_peers = [
        replace(_compiled(f"ambiguous-{index}", "RB", vbd=20, yahoo=100), ambiguous=True)
        for index in range(3)
    ]
    ranked, excluded = rank_candidates(
        [target, *excluded_peers],
        config,
        DraftState(current_pick=1, current_team="team-1"),
        now=NOW,
    )
    assert ranked[0].components["wait_risk_band"] == 0
    assert set(excluded) == {item.player_id for item in excluded_peers}
