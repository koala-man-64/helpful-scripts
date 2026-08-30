"""Typed domain models for the local fantasy draft assistant.

The models intentionally accept only sanitized, visible Yahoo state.  Browser
credentials and session material have no representation in this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Mapping, Sequence


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: str | datetime, field_name: str) -> datetime:
    if not isinstance(value, (str, datetime)):
        raise ValueError(f"{field_name} must be an ISO-8601 datetime")
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def datetime_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_player_name(value: str) -> str:
    """Normalize visible identity without discarding suffixes such as Jr/III."""

    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def normalize_team(value: str) -> str:
    return value.strip().upper()


def normalize_position(value: str) -> str:
    # Preserve multi-position identity while normalizing separators.
    parts = [part for part in re.split(r"[/,|\s]+", value.strip().upper()) if part]
    return "/".join(parts)


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError(f"{field_name} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class Keeper:
    player_id: str
    overall_pick: int
    team: str

    def __post_init__(self) -> None:
        _required_text(self.player_id, "keeper.player_id")
        _positive_int(self.overall_pick, "keeper.overall_pick")
        _required_text(self.team, "keeper.team")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Keeper":
        return cls(
            player_id=_required_text(value.get("player_id"), "keeper.player_id"),
            overall_pick=_positive_int(value.get("overall_pick"), "keeper.overall_pick"),
            team=_required_text(value.get("team"), "keeper.team"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {"player_id": self.player_id, "overall_pick": self.overall_pick, "team": self.team}


@dataclass(frozen=True, slots=True)
class LeagueConfig:
    active_teams: int
    maximum_teams: int
    draft_slots: tuple[str, ...]
    rounds: int
    draft_position: int
    pick_clock_seconds: int
    our_team: str
    roster: Mapping[str, int]
    flex_positions: tuple[str, ...]
    keepers: tuple[Keeper, ...]
    scoring_format: str
    mandatory_freshness_hours: float

    def __post_init__(self) -> None:
        _positive_int(self.active_teams, "active_teams")
        _positive_int(self.maximum_teams, "maximum_teams")
        if self.active_teams > self.maximum_teams:
            raise ValueError("active_teams cannot exceed maximum_teams")
        if len(self.draft_slots) != self.active_teams:
            raise ValueError("draft_slots must contain exactly active_teams entries")
        if len(set(self.draft_slots)) != len(self.draft_slots):
            raise ValueError("draft_slots must be unique")
        if not 1 <= self.draft_position <= self.active_teams:
            raise ValueError("draft_position must be within the active draft slots")
        _positive_int(self.rounds, "rounds")
        _positive_int(self.pick_clock_seconds, "pick_clock_seconds")
        _required_text(self.our_team, "our_team")
        if self.our_team not in self.draft_slots:
            raise ValueError("our_team must appear in draft_slots")
        if not self.roster:
            raise ValueError("roster must contain at least one position")
        for position, count in self.roster.items():
            _required_text(position, "roster position")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("roster counts must be non-negative integers")
        if not any(self.roster.values()):
            raise ValueError("roster must contain at least one slot")
        if not self.flex_positions:
            raise ValueError("flex_positions must not be empty")
        _required_text(self.scoring_format, "scoring_format")
        if _finite_number(self.mandatory_freshness_hours, "mandatory_freshness_hours") <= 0:
            raise ValueError("mandatory_freshness_hours must be positive")
        keeper_picks = [keeper.overall_pick for keeper in self.keepers]
        if len(set(keeper_picks)) != len(keeper_picks):
            raise ValueError("keeper overall picks must be unique")
        maximum_pick = self.active_teams * self.rounds
        if any(pick > maximum_pick for pick in keeper_picks):
            raise ValueError("keeper overall_pick exceeds the draft length")

    @property
    def maximum_capacity(self) -> int:
        return self.maximum_teams

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LeagueConfig":
        roster = value.get("roster")
        if not isinstance(roster, Mapping):
            raise ValueError("roster must be an object")
        draft_slots = value.get("draft_slots")
        flex_positions = value.get("flex_positions")
        keepers = value.get("keepers", [])
        if not isinstance(draft_slots, Sequence) or isinstance(draft_slots, (str, bytes)):
            raise ValueError("draft_slots must be a list")
        if not isinstance(flex_positions, Sequence) or isinstance(flex_positions, (str, bytes)):
            raise ValueError("flex_positions must be a list")
        if not isinstance(keepers, Sequence) or isinstance(keepers, (str, bytes)):
            raise ValueError("keepers must be a list")
        return cls(
            active_teams=_positive_int(value.get("active_teams"), "active_teams"),
            maximum_teams=_positive_int(value.get("maximum_teams"), "maximum_teams"),
            draft_slots=tuple(_required_text(item, "draft_slots item") for item in draft_slots),
            rounds=_positive_int(value.get("rounds"), "rounds"),
            draft_position=_positive_int(value.get("draft_position"), "draft_position"),
            pick_clock_seconds=_positive_int(value.get("pick_clock_seconds"), "pick_clock_seconds"),
            our_team=_required_text(value.get("our_team"), "our_team"),
            roster={normalize_position(str(key)): count for key, count in roster.items()},
            flex_positions=tuple(normalize_position(_required_text(item, "flex_positions item")) for item in flex_positions),
            keepers=tuple(Keeper.from_dict(item) for item in keepers),
            scoring_format=_required_text(value.get("scoring_format"), "scoring_format"),
            mandatory_freshness_hours=_finite_number(
                value.get("mandatory_freshness_hours"), "mandatory_freshness_hours"
            ),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "active_teams": self.active_teams,
            "maximum_teams": self.maximum_teams,
            "draft_slots": list(self.draft_slots),
            "rounds": self.rounds,
            "draft_position": self.draft_position,
            "pick_clock_seconds": self.pick_clock_seconds,
            "our_team": self.our_team,
            "roster": dict(self.roster),
            "flex_positions": list(self.flex_positions),
            "keepers": [keeper.to_dict() for keeper in self.keepers],
            "scoring_format": self.scoring_format,
            "mandatory_freshness_hours": self.mandatory_freshness_hours,
        }


@dataclass(frozen=True, slots=True)
class PlayerSnapshot:
    player_id: str
    name: str
    nfl_team: str
    position: str
    projection: float
    tier: int
    adp: float
    league_fit: float
    scarcity: float
    wait_risk: float
    roster_utility: float
    risk: float
    source: str
    source_family: str
    checked_at: datetime
    status: str
    ambiguous: bool = False

    def __post_init__(self) -> None:
        for field_name in ("player_id", "name", "nfl_team", "position", "source", "source_family", "status"):
            _required_text(getattr(self, field_name), field_name)
        _positive_int(self.tier, "tier")
        for field_name in ("projection", "adp", "league_fit", "scarcity", "wait_risk", "roster_utility", "risk"):
            _finite_number(getattr(self, field_name), field_name)
        parse_datetime(self.checked_at, "checked_at")
        if not isinstance(self.ambiguous, bool):
            raise ValueError("ambiguous must be boolean")

    @property
    def normalized_name(self) -> str:
        return normalize_player_name(self.name)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PlayerSnapshot":
        return cls(
            player_id=_required_text(value.get("player_id"), "player_id"),
            name=_required_text(value.get("name"), "name"),
            nfl_team=normalize_team(_required_text(value.get("nfl_team"), "nfl_team")),
            position=normalize_position(_required_text(value.get("position"), "position")),
            projection=_finite_number(value.get("projection"), "projection"),
            tier=_positive_int(value.get("tier"), "tier"),
            adp=_finite_number(value.get("adp"), "adp"),
            league_fit=_finite_number(value.get("league_fit"), "league_fit"),
            scarcity=_finite_number(value.get("scarcity"), "scarcity"),
            wait_risk=_finite_number(value.get("wait_risk"), "wait_risk"),
            roster_utility=_finite_number(value.get("roster_utility"), "roster_utility"),
            risk=_finite_number(value.get("risk"), "risk"),
            source=_required_text(value.get("source"), "source"),
            source_family=_required_text(value.get("source_family"), "source_family"),
            checked_at=parse_datetime(value.get("checked_at"), "checked_at"),
            status=_required_text(value.get("status"), "status"),
            ambiguous=value.get("ambiguous", False),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "nfl_team": self.nfl_team,
            "position": self.position,
            "projection": self.projection,
            "tier": self.tier,
            "adp": self.adp,
            "league_fit": self.league_fit,
            "scarcity": self.scarcity,
            "wait_risk": self.wait_risk,
            "roster_utility": self.roster_utility,
            "risk": self.risk,
            "source": self.source,
            "source_family": self.source_family,
            "checked_at": datetime_text(self.checked_at),
            "status": self.status,
            "ambiguous": self.ambiguous,
        }


class ArmMode(str, Enum):
    MOCK = "mock"
    REAL = "real"


class ControlState(str, Enum):
    DISARMED = "disarmed"
    ARMED = "armed"
    TAKEOVER = "takeover"


class IntentStatus(str, Enum):
    ISSUED = "issued"
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class DraftEvent:
    sequence: int
    idempotency_key: str
    timestamp: datetime
    version: int
    event_type: str
    payload: Mapping[str, JsonValue]
    previous_event_hash: str
    event_hash: str


@dataclass(frozen=True, slots=True)
class DraftState:
    current_pick: int = 1
    current_team: str | None = None
    rosters: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    unavailable: frozenset[str] = field(default_factory=frozenset)
    queue: tuple[str, ...] = ()
    control_state: ControlState = ControlState.DISARMED
    armed_mode: ArmMode | None = None
    room_fingerprint: str | None = None
    real_draft_acknowledged: bool = False
    last_verified_pick: int | None = None
    outstanding_intent_id: str | None = None
    outstanding_intent_status: IntentStatus | None = None
    reconciled: bool = False
    halt_reason: str | None = None

    def roster_count(self, team: str) -> int:
        return len(self.rosters.get(team, ()))


@dataclass(frozen=True, slots=True)
class RecommendationEnvelope:
    top_three: tuple[str, ...]
    component_ordering: tuple[str, ...]
    exclusions: Mapping[str, tuple[str, ...]]
    input_freshness: Mapping[str, str]
    state_hash: str
    config_hash: str
    board_hash: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if not 1 <= len(self.top_three) <= 3 or len(set(self.top_three)) != len(self.top_three):
            raise ValueError("top_three must contain one to three unique player IDs")
        parse_datetime(self.expires_at, "expires_at")


_FORBIDDEN_VISIBLE_KEYS = {
    "account_id", "auth", "authorization", "captcha", "cookie", "cookies", "credential",
    "local_storage", "mfa", "password", "private_url", "session", "session_id", "token",
}


def _reject_private_keys(value: Mapping[str, Any]) -> None:
    for key, nested in value.items():
        normalized = key.casefold().replace("-", "_")
        if normalized in _FORBIDDEN_VISIBLE_KEYS or any(
            token in normalized for token in ("password", "credential", "cookie", "token", "authorization")
        ):
            raise ValueError(f"observed state contains forbidden field: {key}")
        if isinstance(nested, Mapping):
            _reject_private_keys(nested)
        elif isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
            for item in nested:
                if isinstance(item, Mapping):
                    _reject_private_keys(item)


@dataclass(frozen=True, slots=True)
class ObservedYahooRow:
    name: str
    nfl_team: str
    position: str
    available: bool
    has_draft_control: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservedYahooRow":
        _reject_private_keys(value)
        allowed = {"name", "nfl_team", "position", "available", "has_draft_control"}
        unknown = set(value).difference(allowed)
        if unknown:
            raise ValueError(f"row contains unknown fields: {', '.join(sorted(unknown))}")
        return cls(
            name=_required_text(value.get("name"), "row.name"),
            nfl_team=normalize_team(_required_text(value.get("nfl_team"), "row.nfl_team")),
            position=normalize_position(_required_text(value.get("position"), "row.position")),
            available=value.get("available") is True,
            has_draft_control=value.get("has_draft_control") is True,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "nfl_team": self.nfl_team,
            "position": self.position,
            "available": self.available,
            "has_draft_control": self.has_draft_control,
        }


@dataclass(frozen=True, slots=True)
class ObservedYahooState:
    room_fingerprint: str
    your_turn: bool
    current_team: str
    overall_pick: int
    clock_seconds: int
    roster_count: int
    rows: tuple[ObservedYahooRow, ...]
    autodraft_off: bool
    captured_at: datetime
    queue_player_ids: tuple[str, ...] = ()
    unavailable_player_ids: tuple[str, ...] = ()
    roster_player_ids: tuple[str, ...] = ()
    authentication_challenge: bool = False
    modal_ambiguity: bool = False
    reconnecting: bool = False
    control_interrupted: bool = False
    state_hash: str | None = None
    config_hash: str | None = None
    board_hash: str | None = None
    last_pick_player_id: str | None = None
    last_pick_position: str | None = None
    last_pick_overall: int | None = None
    room_advanced: bool | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservedYahooState":
        _reject_private_keys(value)
        allowed = {
            "room_fingerprint", "your_turn", "current_team", "overall_pick", "clock_seconds",
            "roster_count", "rows", "autodraft_off", "captured_at", "queue_player_ids", "queue",
            "unavailable_player_ids", "roster_player_ids", "authentication_challenge",
            "modal_ambiguity", "reconnecting", "control_interrupted", "state_hash", "config_hash",
            "board_hash", "last_pick_player_id", "last_pick_position", "last_pick_overall",
            "room_advanced",
        }
        unknown = set(value).difference(allowed)
        if unknown:
            raise ValueError(f"observed state contains unknown fields: {', '.join(sorted(unknown))}")
        rows = value.get("rows", [])
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise ValueError("rows must be a list")
        queue = value.get("queue_player_ids", value.get("queue", []))
        if not isinstance(queue, Sequence) or isinstance(queue, (str, bytes)):
            raise ValueError("queue_player_ids must be a list")
        unavailable = value.get("unavailable_player_ids", [])
        roster_players = value.get("roster_player_ids", [])
        if not isinstance(unavailable, Sequence) or isinstance(unavailable, (str, bytes)):
            raise ValueError("unavailable_player_ids must be a list")
        if not isinstance(roster_players, Sequence) or isinstance(roster_players, (str, bytes)):
            raise ValueError("roster_player_ids must be a list")
        roster_count = value.get("roster_count")
        if isinstance(roster_count, bool) or not isinstance(roster_count, int) or roster_count < 0:
            raise ValueError("roster_count must be a non-negative integer")
        clock_seconds = value.get("clock_seconds")
        if isinstance(clock_seconds, bool) or not isinstance(clock_seconds, int) or clock_seconds < 0:
            raise ValueError("clock_seconds must be a non-negative integer")
        return cls(
            room_fingerprint=_required_text(value.get("room_fingerprint"), "room_fingerprint"),
            your_turn=value.get("your_turn") is True,
            current_team=_required_text(value.get("current_team"), "current_team"),
            overall_pick=_positive_int(value.get("overall_pick"), "overall_pick"),
            clock_seconds=clock_seconds,
            roster_count=roster_count,
            rows=tuple(ObservedYahooRow.from_dict(row) for row in rows),
            autodraft_off=value.get("autodraft_off") is True,
            captured_at=parse_datetime(value.get("captured_at"), "captured_at"),
            queue_player_ids=tuple(_required_text(item, "queue player ID") for item in queue),
            unavailable_player_ids=tuple(_required_text(item, "unavailable player ID") for item in unavailable),
            roster_player_ids=tuple(_required_text(item, "roster player ID") for item in roster_players),
            authentication_challenge=value.get("authentication_challenge", False) is True,
            modal_ambiguity=value.get("modal_ambiguity", False) is True,
            reconnecting=value.get("reconnecting", False) is True,
            control_interrupted=value.get("control_interrupted", False) is True,
            state_hash=value.get("state_hash"),
            config_hash=value.get("config_hash"),
            board_hash=value.get("board_hash"),
            last_pick_player_id=value.get("last_pick_player_id"),
            last_pick_position=value.get("last_pick_position"),
            last_pick_overall=value.get("last_pick_overall"),
            room_advanced=value.get("room_advanced"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "room_fingerprint": self.room_fingerprint,
            "your_turn": self.your_turn,
            "current_team": self.current_team,
            "overall_pick": self.overall_pick,
            "clock_seconds": self.clock_seconds,
            "roster_count": self.roster_count,
            "rows": [row.to_dict() for row in self.rows],
            "autodraft_off": self.autodraft_off,
            "captured_at": datetime_text(self.captured_at),
            "queue_player_ids": list(self.queue_player_ids),
            "unavailable_player_ids": list(self.unavailable_player_ids),
            "roster_player_ids": list(self.roster_player_ids),
            "authentication_challenge": self.authentication_challenge,
            "modal_ambiguity": self.modal_ambiguity,
            "reconnecting": self.reconnecting,
            "control_interrupted": self.control_interrupted,
            "state_hash": self.state_hash,
            "config_hash": self.config_hash,
            "board_hash": self.board_hash,
            "last_pick_player_id": self.last_pick_player_id,
            "last_pick_position": self.last_pick_position,
            "last_pick_overall": self.last_pick_overall,
            "room_advanced": self.room_advanced,
        }


@dataclass(frozen=True, slots=True)
class PickIntent:
    intent_id: str
    player_id: str
    player_name: str
    nfl_team: str
    position: str
    expected_pick: int
    expected_team: str
    expected_roster_count: int
    room_fingerprint: str
    state_hash: str
    config_hash: str
    board_hash: str
    expires_at: datetime
    status: IntentStatus = IntentStatus.ISSUED


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()

    @property
    def outcome(self) -> str:
        return "allow" if self.allowed else "halt"

    @classmethod
    def allow(cls) -> "SafetyDecision":
        return cls(True, ())

    @classmethod
    def halt(cls, *reasons: str) -> "SafetyDecision":
        if not reasons:
            raise ValueError("halt requires at least one reason")
        return cls(False, tuple(dict.fromkeys(reasons)))
