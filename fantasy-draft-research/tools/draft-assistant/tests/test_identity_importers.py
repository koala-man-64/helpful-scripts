from __future__ import annotations

from pathlib import Path

import pytest

from fantasy_draft_assistant.identity import identity_key, normalize_position
from fantasy_draft_assistant.importers import load_league, load_players


FIXTURES = Path(__file__).parent / "fixtures"


def test_league_distinguishes_active_and_maximum_teams(league):
    assert league.active_teams == 8
    assert league.maximum_teams == 10
    assert len(league.draft_slots) == 8
    assert league.keepers[0].overall_pick == 49


def test_identity_normalizes_suffixes_but_keeps_exact_team_position():
    assert identity_key("Marvin Test Jr.", "det", "D/ST") == ("marvin test", "DET", "DEF")
    assert normalize_position("WR") != normalize_position("RB")
    assert normalize_position("RB, WR") == "RB/WR"


def test_duplicate_normalized_identity_fails_closed(tmp_path):
    source = (FIXTURES / "players.csv").read_text(encoding="utf-8")
    duplicate = source + source.splitlines()[1] + "\n"
    path = tmp_path / "players.csv"
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_players(path)


def test_invalid_active_team_count_fails(tmp_path):
    value = (FIXTURES / "league.json").read_text(encoding="utf-8").replace('"active_teams": 8', '"active_teams": 11')
    path = tmp_path / "league.json"
    path.write_text(value, encoding="utf-8")
    with pytest.raises(ValueError, match="maximum_teams"):
        load_league(path)
