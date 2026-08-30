from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fantasy_draft_assistant.importers import load_league, load_players


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def league():
    return load_league(FIXTURES / "league.json")


@pytest.fixture
def players():
    result = load_players(FIXTURES / "players.csv")
    fresh = datetime.now(timezone.utc) - timedelta(minutes=1)
    return [
        type(player)(**{**player.to_dict(), "checked_at": fresh})
        for player in result
    ]
