"""Typed domain models for the local fantasy draft assistant.

The models intentionally accept only sanitized, visible Yahoo state.  Browser
credentials and session material have no representation in this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, IntEnum
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


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
    """Use the package's single canonical comparison key for player names."""

    from .identity import normalize_name

    return normalize_name(value)


def normalize_team(value: str) -> str:
    from .identity import normalize_team as normalize_identity_team

    return normalize_identity_team(value)


def normalize_position(value: str) -> str:
    # Preserve multi-position identity while normalizing separators.
    raw = value.strip().upper()
    if raw in {"D/ST", "DST"}:
        return "DEF"
    parts = [part for part in re.split(r"[/,|\s]+", raw) if part]
    return "/".join(parts)


_SUPPORTED_PLAYER_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DEF"})
_SUPPORTED_MULTI_POSITIONS = frozenset({"RB", "WR", "TE"})


def normalize_player_position(value: str) -> str:
    """Normalize a v1 player position without admitting IDP/superflex loopholes."""

    normalized = normalize_position(value)
    parts = normalized.split("/")
    if any(part not in _SUPPORTED_PLAYER_POSITIONS for part in parts):
        raise ValueError(f"unsupported player position: {value!r}")
    if len(parts) > 1 and not set(parts).issubset(_SUPPORTED_MULTI_POSITIONS):
        raise ValueError(f"unsupported multi-position player: {value!r}")
    return normalized


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


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


def _required_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value


def validate_room_fingerprint(value: Any) -> str:
    fingerprint = _required_text(value, "room_fingerprint").casefold()
    if not re.fullmatch(r"room-fp:[0-9a-f]{64}", fingerprint):
        raise ValueError("room_fingerprint must be room-fp: followed by a SHA-256 digest")
    return fingerprint


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
    scoring_overrides: Mapping[str, float] = field(default_factory=dict)
    draft_type: str = "snake"
    league_type: str = "redraft"

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
        if self.draft_slots[self.draft_position - 1] != self.our_team:
            raise ValueError("draft_position must identify our_team in draft_slots")
        normalized_roster: dict[str, int] = {}
        allowed_slots = {"QB", "RB", "WR", "TE", "FLEX", "K", "DEF", "BENCH"}
        for raw_position, count in self.roster.items():
            position = normalize_position(_required_text(str(raw_position), "roster position"))
            if position in {"DL", "LB", "DB", "IDP", "SUPERFLEX"}:
                raise ValueError("superflex and IDP roster slots are outside the v1 league scope")
            if position not in allowed_slots:
                raise ValueError(f"unsupported v1 roster slot: {raw_position!r}")
            if position in normalized_roster:
                raise ValueError(f"duplicate normalized roster slot: {position}")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("roster counts must be non-negative integers")
            normalized_roster[position] = count
        object.__setattr__(self, "roster", MappingProxyType(normalized_roster))
        if not normalized_roster:
            raise ValueError("roster must contain at least one position")
        if not any(normalized_roster.values()):
            raise ValueError("roster must contain at least one slot")
        if sum(normalized_roster.values()) != self.rounds:
            raise ValueError("rounds must equal the number of configured roster slots")
        normalized_flex = tuple(
            normalize_player_position(_required_text(item, "flex position"))
            for item in self.flex_positions
        )
        if normalized_roster.get("FLEX", 0) > 0 and not normalized_flex:
            raise ValueError("flex_positions must not be empty when FLEX slots are configured")
        if len(set(normalized_flex)) != len(normalized_flex):
            raise ValueError("flex_positions must be unique")
        if any("/" in position or position not in {"RB", "WR", "TE"} for position in normalized_flex):
            raise ValueError("superflex and special-team flex eligibility are outside the v1 scope")
        object.__setattr__(self, "flex_positions", normalized_flex)
        _required_text(self.scoring_format, "scoring_format")
        from .scoring import normalize_scoring_format, scoring_rules

        normalized_scoring_format = normalize_scoring_format(self.scoring_format)
        scoring_rules(normalized_scoring_format, self.scoring_overrides)
        object.__setattr__(self, "scoring_format", normalized_scoring_format)
        if self.draft_type.strip().casefold() != "snake":
            raise ValueError("auction drafts are outside the v1 league scope")
        if self.league_type.strip().casefold() != "redraft":
            raise ValueError("dynasty and best-ball leagues are outside the v1 league scope")
        object.__setattr__(self, "draft_type", "snake")
        object.__setattr__(self, "league_type", "redraft")
        if any(position in {"DL", "LB", "DB", "IDP", "SUPERFLEX"} for position in self.roster):
            raise ValueError("superflex and IDP roster slots are outside the v1 league scope")
        object.__setattr__(
            self,
            "scoring_overrides",
            MappingProxyType({str(key): float(value) for key, value in self.scoring_overrides.items()}),
        )
        if _finite_number(self.mandatory_freshness_hours, "mandatory_freshness_hours") <= 0:
            raise ValueError("mandatory_freshness_hours must be positive")
        keeper_picks = [keeper.overall_pick for keeper in self.keepers]
        if len(set(keeper_picks)) != len(keeper_picks):
            raise ValueError("keeper overall picks must be unique")
        keeper_players = [keeper.player_id for keeper in self.keepers]
        if len(set(keeper_players)) != len(keeper_players):
            raise ValueError("keeper player IDs must be unique")
        maximum_pick = self.active_teams * self.rounds
        if any(pick > maximum_pick for pick in keeper_picks):
            raise ValueError("keeper overall_pick exceeds the draft length")
        for keeper in self.keepers:
            if keeper.team not in self.draft_slots:
                raise ValueError("keeper team must appear in draft_slots")
            round_number, offset = divmod(keeper.overall_pick - 1, self.active_teams)
            slots = self.draft_slots if round_number % 2 == 0 else tuple(reversed(self.draft_slots))
            if slots[offset] != keeper.team:
                raise ValueError("keeper overall_pick must belong to keeper team")

    @property
    def maximum_capacity(self) -> int:
        return self.maximum_teams

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LeagueConfig":
        if "best_ball" in value:
            _required_bool(value["best_ball"], "best_ball")
        if value.get("best_ball") is True:
            raise ValueError("best-ball leagues are outside the v1 league scope")
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
            scoring_overrides={
                _required_text(str(key), "scoring override name"): _finite_number(
                    item, f"scoring_overrides.{key}"
                )
                for key, item in _mapping(value.get("scoring_overrides", {}), "scoring_overrides").items()
            },
            draft_type=_required_text(value.get("draft_type", "snake"), "draft_type"),
            league_type=_required_text(value.get("league_type", "redraft"), "league_type"),
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
            "scoring_overrides": dict(self.scoring_overrides),
            "draft_type": self.draft_type,
            "league_type": self.league_type,
        }

    @property
    def resolved_scoring_rules(self) -> dict[str, float]:
        from .scoring import scoring_rules

        return scoring_rules(self.scoring_format, self.scoring_overrides)

    @property
    def scoring_context(self) -> str:
        from .scoring import scoring_context

        return scoring_context(self.scoring_format, self.scoring_overrides)


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
        object.__setattr__(self, "position", normalize_player_position(self.position))
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
            position=normalize_player_position(_required_text(value.get("position"), "position")),
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


class SignalRole(str, Enum):
    PROJECTION = "projection"
    RANKING = "ranking"
    TIER = "tier"
    ADP = "adp"
    IDENTITY = "identity"
    STATUS = "status"
    NEWS = "news"
    RISK = "risk"
    ROOM_OBSERVATION = "room_observation"


class AcquisitionMethod(str, Enum):
    DIRECT = "direct"
    CHROME_SNAPSHOT = "chrome_snapshot"
    MANUAL_SNAPSHOT = "manual_snapshot"


class DraftPlatform(str, Enum):
    YAHOO = "yahoo"
    ESPN = "espn"
    SLEEPER = "sleeper"


class PersonalPreference(IntEnum):
    TARGET = -1
    NEUTRAL = 0
    AVOID = 1


DEFAULT_FRESHNESS_HOURS: Mapping[SignalRole, float] = MappingProxyType(
    {
        SignalRole.ROOM_OBSERVATION: 5.0 / 3600.0,
        SignalRole.STATUS: 6.0,
        SignalRole.NEWS: 6.0,
        SignalRole.IDENTITY: 24.0,
        SignalRole.ADP: 24.0,
        SignalRole.PROJECTION: 72.0,
        SignalRole.RANKING: 72.0,
        SignalRole.TIER: 72.0,
        SignalRole.RISK: 72.0,
    }
)


_RISK_BANDS = {"low": 0, "medium": 1, "high": 2, "exclude": 3}


def _band(value: Any, field_name: str) -> int:
    if isinstance(value, str):
        try:
            return _RISK_BANDS[value.strip().casefold()]
        except KeyError as error:
            raise ValueError(f"{field_name} must be low, medium, high, or exclude") from error
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 3:
        raise ValueError(f"{field_name} must be an integer from zero through three")
    return value


def _enum(enum_type: type[Enum], value: Any, field_name: str) -> Any:
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as error:
        choices = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field_name} must be one of: {choices}") from error


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    """A sanitized, checksummed research artifact and its decision role."""

    source: str
    upstream_family: str
    signal_role: SignalRole
    scoring_context: str | None
    acquisition_method: AcquisitionMethod
    published_at: datetime | None
    retrieved_at: datetime
    checksum: str
    freshness_hours: float
    safe_provenance: Mapping[str, JsonValue] = field(default_factory=dict)
    mandatory: bool = False

    def __post_init__(self) -> None:
        _required_text(self.source, "artifact.source")
        _required_text(self.upstream_family, "artifact.upstream_family")
        if self.scoring_context is not None:
            _required_text(self.scoring_context, "artifact.scoring_context")
        object.__setattr__(self, "signal_role", _enum(SignalRole, self.signal_role, "artifact.signal_role"))
        object.__setattr__(
            self,
            "acquisition_method",
            _enum(AcquisitionMethod, self.acquisition_method, "artifact.acquisition_method"),
        )
        retrieved_at = parse_datetime(self.retrieved_at, "artifact.retrieved_at")
        object.__setattr__(self, "retrieved_at", retrieved_at)
        if self.published_at is not None:
            published_at = parse_datetime(self.published_at, "artifact.published_at")
            object.__setattr__(self, "published_at", published_at)
            if published_at > retrieved_at:
                raise ValueError("artifact.published_at cannot be after retrieved_at")
        checksum = _required_text(self.checksum, "artifact.checksum").casefold()
        if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", checksum):
            raise ValueError("artifact.checksum must be a SHA-256 digest")
        object.__setattr__(self, "checksum", checksum.removeprefix("sha256:"))
        if _finite_number(self.freshness_hours, "artifact.freshness_hours") <= 0:
            raise ValueError("artifact.freshness_hours must be positive")
        if not isinstance(self.safe_provenance, Mapping):
            raise ValueError("artifact.safe_provenance must be an object")
        _reject_private_keys(self.safe_provenance)
        object.__setattr__(self, "safe_provenance", MappingProxyType(dict(self.safe_provenance)))
        if not isinstance(self.mandatory, bool):
            raise ValueError("artifact.mandatory must be boolean")

    @property
    def artifact_id(self) -> str:
        return f"{self.source}:{self.signal_role.value}:{self.checksum[:16]}"

    @property
    def freshness_at(self) -> datetime:
        return self.published_at or self.retrieved_at

    @property
    def freshness_limit_hours(self) -> float:
        return min(self.freshness_hours, DEFAULT_FRESHNESS_HOURS[self.signal_role])

    def is_fresh(self, now: datetime) -> bool:
        timestamp = parse_datetime(now, "now")
        age = (timestamp - self.freshness_at).total_seconds()
        return -2 <= age <= self.freshness_limit_hours * 3600

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceArtifact":
        _reject_private_keys(value)
        allowed = {
            "source", "upstream_family", "signal_role", "scoring_context", "acquisition_method",
            "published_at", "retrieved_at", "checksum", "freshness_hours", "safe_provenance",
            "mandatory",
        }
        unknown = set(value).difference(allowed)
        if unknown:
            raise ValueError(f"artifact contains unknown fields: {', '.join(sorted(unknown))}")
        return cls(
            source=_required_text(value.get("source"), "artifact.source"),
            upstream_family=_required_text(value.get("upstream_family"), "artifact.upstream_family"),
            signal_role=_enum(SignalRole, value.get("signal_role"), "artifact.signal_role"),
            scoring_context=value.get("scoring_context"),
            acquisition_method=_enum(
                AcquisitionMethod, value.get("acquisition_method"), "artifact.acquisition_method"
            ),
            published_at=(
                parse_datetime(value["published_at"], "artifact.published_at")
                if value.get("published_at") is not None
                else None
            ),
            retrieved_at=parse_datetime(value.get("retrieved_at"), "artifact.retrieved_at"),
            checksum=_required_text(value.get("checksum"), "artifact.checksum"),
            freshness_hours=_finite_number(value.get("freshness_hours"), "artifact.freshness_hours"),
            safe_provenance=_mapping(value.get("safe_provenance", {}), "artifact.safe_provenance"),
            mandatory=_required_bool(value.get("mandatory", False), "artifact.mandatory"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "source": self.source,
            "upstream_family": self.upstream_family,
            "signal_role": self.signal_role.value,
            "scoring_context": self.scoring_context,
            "acquisition_method": self.acquisition_method.value,
            "published_at": datetime_text(self.published_at) if self.published_at else None,
            "retrieved_at": datetime_text(self.retrieved_at),
            "checksum": self.checksum,
            "freshness_hours": self.freshness_hours,
            "safe_provenance": dict(self.safe_provenance),
            "mandatory": self.mandatory,
        }


@dataclass(frozen=True, slots=True)
class PlayerEvidence:
    """One typed player signal linked to a checksummed artifact."""

    player_id: str
    name: str
    nfl_team: str | None
    position: str
    signal_role: SignalRole
    artifact_checksum: str
    projection_points: float | None = None
    projected_stats: Mapping[str, float] = field(default_factory=dict)
    tier: int | None = None
    adp: float | None = None
    platform: DraftPlatform | None = None
    status: str | None = None
    news: str | None = None
    risk_band: int | None = None
    ambiguous: bool = False

    def __post_init__(self) -> None:
        _required_text(self.player_id, "evidence.player_id")
        _required_text(self.name, "evidence.name")
        if self.nfl_team is not None:
            object.__setattr__(self, "nfl_team", normalize_team(_required_text(self.nfl_team, "evidence.nfl_team")))
        object.__setattr__(
            self,
            "position",
            normalize_player_position(_required_text(self.position, "evidence.position")),
        )
        role = _enum(SignalRole, self.signal_role, "evidence.signal_role")
        object.__setattr__(self, "signal_role", role)
        checksum = _required_text(self.artifact_checksum, "evidence.artifact_checksum").casefold()
        if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", checksum):
            raise ValueError("evidence.artifact_checksum must be a SHA-256 digest")
        object.__setattr__(self, "artifact_checksum", checksum.removeprefix("sha256:"))
        if self.projection_points is not None:
            object.__setattr__(
                self,
                "projection_points",
                _finite_number(self.projection_points, "evidence.projection_points"),
            )
        stats = {
            _required_text(str(key), "projected stat name"): _finite_number(
                raw, f"evidence.projected_stats.{key}"
            )
            for key, raw in _mapping(self.projected_stats, "evidence.projected_stats").items()
        }
        object.__setattr__(self, "projected_stats", MappingProxyType(stats))
        if self.tier is not None:
            _positive_int(self.tier, "evidence.tier")
        if self.adp is not None:
            object.__setattr__(self, "adp", _finite_number(self.adp, "evidence.adp"))
            if self.adp <= 0:
                raise ValueError("evidence.adp must be positive")
        if self.platform is not None:
            object.__setattr__(self, "platform", _enum(DraftPlatform, self.platform, "evidence.platform"))
        if self.status is not None:
            object.__setattr__(self, "status", _required_text(self.status, "evidence.status"))
        if self.news is not None:
            object.__setattr__(self, "news", _required_text(self.news, "evidence.news"))
        if self.risk_band is not None:
            object.__setattr__(self, "risk_band", _band(self.risk_band, "evidence.risk_band"))
        if not isinstance(self.ambiguous, bool):
            raise ValueError("evidence.ambiguous must be boolean")

        if role is SignalRole.PROJECTION:
            if bool(stats) == (self.projection_points is not None):
                raise ValueError("projection evidence requires exactly one of projected_stats or projection_points")
        elif role is SignalRole.TIER and self.tier is None:
            raise ValueError("tier evidence requires tier")
        elif role is SignalRole.ADP and (self.adp is None or self.platform is None):
            raise ValueError("ADP evidence requires adp and platform")
        elif role is SignalRole.STATUS and not self.status:
            raise ValueError("status evidence requires status")
        elif role is SignalRole.NEWS and not self.news:
            raise ValueError("news evidence requires news")
        elif role is SignalRole.RISK and self.risk_band is None:
            raise ValueError("risk evidence requires risk_band")

    @property
    def normalized_identity(self) -> tuple[str, str, str]:
        return self.normalized_name, self.nfl_team or "", self.position

    @property
    def normalized_name(self) -> str:
        return normalize_player_name(self.name)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PlayerEvidence":
        _reject_private_keys(value)
        allowed = {
            "player_id", "name", "nfl_team", "position", "signal_role", "evidence_type",
            "artifact_checksum", "projection_points", "projected_stats", "tier", "adp",
            "platform", "status", "news", "risk_band", "ambiguous",
        }
        unknown = set(value).difference(allowed)
        if unknown:
            raise ValueError(f"player evidence contains unknown fields: {', '.join(sorted(unknown))}")
        role = value.get("signal_role", value.get("evidence_type"))
        return cls(
            player_id=_required_text(value.get("player_id"), "evidence.player_id"),
            name=_required_text(value.get("name"), "evidence.name"),
            nfl_team=value.get("nfl_team"),
            position=_required_text(value.get("position"), "evidence.position"),
            signal_role=_enum(SignalRole, role, "evidence.signal_role"),
            artifact_checksum=_required_text(value.get("artifact_checksum"), "evidence.artifact_checksum"),
            projection_points=(
                _finite_number(value["projection_points"], "evidence.projection_points")
                if value.get("projection_points") is not None
                else None
            ),
            projected_stats=_mapping(value.get("projected_stats", {}), "evidence.projected_stats"),
            tier=value.get("tier"),
            adp=value.get("adp"),
            platform=value.get("platform"),
            status=value.get("status"),
            news=value.get("news"),
            risk_band=value.get("risk_band"),
            ambiguous=_required_bool(value.get("ambiguous", False), "evidence.ambiguous"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "nfl_team": self.nfl_team,
            "position": self.position,
            "signal_role": self.signal_role.value,
            "artifact_checksum": self.artifact_checksum,
            "projection_points": self.projection_points,
            "projected_stats": dict(self.projected_stats),
            "tier": self.tier,
            "adp": self.adp,
            "platform": self.platform.value if self.platform else None,
            "status": self.status,
            "news": self.news,
            "risk_band": self.risk_band,
            "ambiguous": self.ambiguous,
        }


@dataclass(frozen=True, slots=True)
class CompiledPlayer:
    """Frozen league-scored player record used by the live optimizer."""

    player_id: str
    name: str
    nfl_team: str
    position: str
    projected_points: float
    replacement_baseline: float
    vbd: float
    independent_tier: int
    platform_adps: Mapping[str, float]
    status: str = "active"
    status_band: int = 0
    risk_band: int = 0
    personal_preference: int = PersonalPreference.NEUTRAL
    same_position_tier_drop: float = 0.0
    evidence_refs: tuple[str, ...] = ()
    compiled_at: datetime = field(default_factory=utc_now)
    ambiguous: bool = False

    def __post_init__(self) -> None:
        for field_name in ("player_id", "name", "nfl_team", "position", "status"):
            _required_text(getattr(self, field_name), f"compiled_player.{field_name}")
        object.__setattr__(self, "nfl_team", normalize_team(self.nfl_team))
        object.__setattr__(self, "position", normalize_player_position(self.position))
        projected = _finite_number(self.projected_points, "compiled_player.projected_points")
        baseline = _finite_number(self.replacement_baseline, "compiled_player.replacement_baseline")
        vbd = _finite_number(self.vbd, "compiled_player.vbd")
        if not math.isclose(projected - baseline, vbd, abs_tol=1e-4):
            raise ValueError("compiled_player.vbd must equal projected_points minus replacement_baseline")
        _positive_int(self.independent_tier, "compiled_player.independent_tier")
        adps: dict[str, float] = {}
        for platform, raw_adp in _mapping(self.platform_adps, "compiled_player.platform_adps").items():
            key = _enum(DraftPlatform, platform, "compiled_player platform").value
            value = _finite_number(raw_adp, f"compiled_player.platform_adps.{platform}")
            if value <= 0:
                raise ValueError("compiled player ADP values must be positive")
            adps[key] = value
        object.__setattr__(self, "platform_adps", MappingProxyType(adps))
        object.__setattr__(self, "status_band", _band(self.status_band, "compiled_player.status_band"))
        object.__setattr__(self, "risk_band", _band(self.risk_band, "compiled_player.risk_band"))
        preference = int(self.personal_preference)
        if preference not in {-1, 0, 1}:
            raise ValueError("compiled_player.personal_preference must be target, neutral, or avoid")
        object.__setattr__(self, "personal_preference", preference)
        object.__setattr__(
            self,
            "same_position_tier_drop",
            _finite_number(self.same_position_tier_drop, "compiled_player.same_position_tier_drop"),
        )
        refs = tuple(_required_text(item, "compiled_player evidence reference") for item in self.evidence_refs)
        if len(refs) != len(set(refs)):
            raise ValueError("compiled_player.evidence_refs must be unique")
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "compiled_at", parse_datetime(self.compiled_at, "compiled_player.compiled_at"))
        if not isinstance(self.ambiguous, bool):
            raise ValueError("compiled_player.ambiguous must be boolean")

    @property
    def normalized_name(self) -> str:
        return normalize_player_name(self.name)

    @property
    def projection(self) -> float:
        return self.projected_points

    @property
    def tier(self) -> int:
        return self.independent_tier

    @property
    def adp(self) -> float:
        return min(self.platform_adps.values(), default=float("inf"))

    @property
    def checked_at(self) -> datetime:
        return self.compiled_at

    @property
    def source(self) -> str:
        return "compiled-board"

    @property
    def source_family(self) -> str:
        return "compiled-board"

    @property
    def risk(self) -> float:
        return float(self.risk_band)

    @property
    def vbd_band(self) -> int:
        return math.floor(self.vbd / 10.0)

    def active_adp(self, platform: DraftPlatform | str) -> float:
        key = _enum(DraftPlatform, platform, "platform").value
        return self.platform_adps.get(key, float("inf"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CompiledPlayer":
        return cls(
            player_id=_required_text(value.get("player_id"), "compiled_player.player_id"),
            name=_required_text(value.get("name"), "compiled_player.name"),
            nfl_team=_required_text(value.get("nfl_team"), "compiled_player.nfl_team"),
            position=_required_text(value.get("position"), "compiled_player.position"),
            projected_points=_finite_number(value.get("projected_points"), "compiled_player.projected_points"),
            replacement_baseline=_finite_number(
                value.get("replacement_baseline"), "compiled_player.replacement_baseline"
            ),
            vbd=_finite_number(value.get("vbd"), "compiled_player.vbd"),
            independent_tier=_positive_int(value.get("independent_tier"), "compiled_player.independent_tier"),
            platform_adps=_mapping(value.get("platform_adps", {}), "compiled_player.platform_adps"),
            status=value.get("status", "active"),
            status_band=value.get("status_band", 0),
            risk_band=value.get("risk_band", 0),
            personal_preference=value.get("personal_preference", 0),
            same_position_tier_drop=value.get("same_position_tier_drop", 0.0),
            evidence_refs=tuple(value.get("evidence_refs", ())),
            compiled_at=parse_datetime(value.get("compiled_at"), "compiled_player.compiled_at"),
            ambiguous=_required_bool(value.get("ambiguous", False), "compiled_player.ambiguous"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "nfl_team": self.nfl_team,
            "position": self.position,
            "projected_points": self.projected_points,
            "replacement_baseline": self.replacement_baseline,
            "vbd": self.vbd,
            "vbd_band": self.vbd_band,
            "independent_tier": self.independent_tier,
            "platform_adps": dict(self.platform_adps),
            "status": self.status,
            "status_band": self.status_band,
            "risk_band": self.risk_band,
            "personal_preference": self.personal_preference,
            "same_position_tier_drop": self.same_position_tier_drop,
            "evidence_refs": list(self.evidence_refs),
            "compiled_at": datetime_text(self.compiled_at),
            "ambiguous": self.ambiguous,
        }


@dataclass(frozen=True, slots=True)
class BoardRevision:
    revision: int
    frozen_at: datetime
    board_hash: str
    parent_board_hash: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _positive_int(self.revision, "board_revision.revision")
        object.__setattr__(self, "frozen_at", parse_datetime(self.frozen_at, "board_revision.frozen_at"))
        _required_text(self.board_hash, "board_revision.board_hash")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BoardRevision":
        return cls(
            revision=_positive_int(value.get("revision"), "board_revision.revision"),
            frozen_at=parse_datetime(value.get("frozen_at"), "board_revision.frozen_at"),
            board_hash=_required_text(value.get("board_hash"), "board_revision.board_hash"),
            parent_board_hash=value.get("parent_board_hash"),
            reason=value.get("reason"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "revision": self.revision,
            "frozen_at": datetime_text(self.frozen_at),
            "board_hash": self.board_hash,
            "parent_board_hash": self.parent_board_hash,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class BoardManifest:
    schema_hash: str
    config_hash: str
    board_hash: str
    selected_source_families: Mapping[str, str]
    omissions: tuple[str, ...]
    conflicts: tuple[str, ...]
    artifact_checksums: Mapping[str, str]
    frozen_at: datetime
    artifact_freshness: Mapping[str, Mapping[str, JsonValue]] = field(default_factory=dict)
    revision: int = 1
    parent_board_hash: str | None = None
    revision_history: tuple[BoardRevision, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("schema_hash", "config_hash", "board_hash"):
            _required_text(getattr(self, field_name), f"board_manifest.{field_name}")
        object.__setattr__(self, "frozen_at", parse_datetime(self.frozen_at, "board_manifest.frozen_at"))
        _positive_int(self.revision, "board_manifest.revision")
        for key, value in self.selected_source_families.items():
            _required_text(str(key), "selected source role")
            _required_text(value, "selected source family")
        for key, value in self.artifact_checksums.items():
            _required_text(str(key), "artifact checksum key")
            _required_text(value, "artifact checksum")
        freshness: dict[str, Mapping[str, JsonValue]] = {}
        for artifact_id, raw_audit in self.artifact_freshness.items():
            _required_text(str(artifact_id), "artifact freshness key")
            audit = _mapping(raw_audit, f"artifact_freshness.{artifact_id}")
            required = {
                "signal_role", "published_at", "retrieved_at", "freshness_at",
                "freshness_limit_hours", "mandatory", "fresh_at_freeze",
            }
            missing = required.difference(audit)
            if missing:
                raise ValueError(
                    f"artifact_freshness.{artifact_id} is missing: {', '.join(sorted(missing))}"
                )
            _enum(SignalRole, audit["signal_role"], f"artifact_freshness.{artifact_id}.signal_role")
            if audit["published_at"] is not None:
                parse_datetime(
                    audit["published_at"], f"artifact_freshness.{artifact_id}.published_at"
                )
            parse_datetime(
                audit["retrieved_at"], f"artifact_freshness.{artifact_id}.retrieved_at"
            )
            parse_datetime(audit["freshness_at"], f"artifact_freshness.{artifact_id}.freshness_at")
            if _finite_number(
                audit["freshness_limit_hours"],
                f"artifact_freshness.{artifact_id}.freshness_limit_hours",
            ) <= 0:
                raise ValueError("artifact freshness limit must be positive")
            if not isinstance(audit["mandatory"], bool) or not isinstance(
                audit["fresh_at_freeze"], bool
            ):
                raise ValueError("artifact freshness flags must be boolean")
            freshness[str(artifact_id)] = MappingProxyType(dict(audit))
        if self.revision_history and self.revision_history[-1].revision != self.revision:
            raise ValueError("board manifest revision history must end at the current revision")
        object.__setattr__(
            self,
            "selected_source_families",
            MappingProxyType(dict(self.selected_source_families)),
        )
        object.__setattr__(self, "omissions", tuple(self.omissions))
        object.__setattr__(self, "conflicts", tuple(self.conflicts))
        object.__setattr__(self, "artifact_checksums", MappingProxyType(dict(self.artifact_checksums)))
        object.__setattr__(self, "artifact_freshness", MappingProxyType(freshness))
        object.__setattr__(self, "revision_history", tuple(self.revision_history))

    @property
    def freeze_history(self) -> tuple[BoardRevision, ...]:
        return self.revision_history

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BoardManifest":
        history = value.get("revision_history", value.get("freeze_history", []))
        if not isinstance(history, Sequence) or isinstance(history, (str, bytes)):
            raise ValueError("board_manifest.revision_history must be a list")
        return cls(
            schema_hash=_required_text(value.get("schema_hash"), "board_manifest.schema_hash"),
            config_hash=_required_text(value.get("config_hash"), "board_manifest.config_hash"),
            board_hash=_required_text(value.get("board_hash"), "board_manifest.board_hash"),
            selected_source_families=_mapping(
                value.get("selected_source_families", {}), "board_manifest.selected_source_families"
            ),
            omissions=tuple(value.get("omissions", ())),
            conflicts=tuple(value.get("conflicts", ())),
            artifact_checksums=_mapping(
                value.get("artifact_checksums", {}), "board_manifest.artifact_checksums"
            ),
            frozen_at=parse_datetime(value.get("frozen_at"), "board_manifest.frozen_at"),
            artifact_freshness=_mapping(
                value.get("artifact_freshness", {}), "board_manifest.artifact_freshness"
            ),
            revision=value.get("revision", 1),
            parent_board_hash=value.get("parent_board_hash"),
            revision_history=tuple(BoardRevision.from_dict(item) for item in history),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_hash": self.schema_hash,
            "config_hash": self.config_hash,
            "board_hash": self.board_hash,
            "selected_source_families": dict(self.selected_source_families),
            "omissions": list(self.omissions),
            "conflicts": list(self.conflicts),
            "artifact_checksums": dict(self.artifact_checksums),
            "frozen_at": datetime_text(self.frozen_at),
            "artifact_freshness": {
                artifact_id: dict(audit)
                for artifact_id, audit in self.artifact_freshness.items()
            },
            "revision": self.revision,
            "parent_board_hash": self.parent_board_hash,
            "revision_history": [item.to_dict() for item in self.revision_history],
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
    approved_queue: tuple[str, ...] = ()
    control_state: ControlState = ControlState.DISARMED
    armed_mode: ArmMode | None = None
    room_fingerprint: str | None = None
    real_draft_acknowledged: bool = False
    last_verified_pick: int | None = None
    outstanding_intent_id: str | None = None
    outstanding_intent_status: IntentStatus | None = None
    outstanding_player_id: str | None = None
    outstanding_expected_pick: int | None = None
    outstanding_expected_team: str | None = None
    outstanding_expires_at: datetime | None = None
    reconciled: bool = False
    halt_reason: str | None = None
    platform: DraftPlatform | None = None
    adapter_version: str | None = None
    config_hash: str | None = None
    board_hash: str | None = None

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
        object.__setattr__(self, "exclusions", MappingProxyType(dict(self.exclusions)))
        object.__setattr__(self, "input_freshness", MappingProxyType(dict(self.input_freshness)))


_FORBIDDEN_VISIBLE_KEYS = {
    "account_id", "auth", "authorization", "captcha", "cookie", "cookies", "credential",
    "dom", "html", "local_storage", "mfa", "page_html", "password", "private_url", "raw_dom",
    "session", "session_id", "token",
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
                elif isinstance(item, str):
                    _reject_private_string(item, key)
        elif isinstance(nested, str):
            _reject_private_string(nested, key)


def _reject_private_string(value: str, field_name: str) -> None:
    lowered = value.casefold()
    if "bearer " in lowered or re.fullmatch(r"[a-z0-9_-]{12,}\.[a-z0-9_-]{12,}\.[a-z0-9_-]{12,}", lowered):
        raise ValueError(f"{field_name} contains credential-like material")
    if "://" not in value:
        return
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} contains a private or signed URL")


@dataclass(frozen=True, slots=True)
class ObservedDraftPlayer:
    name: str
    nfl_team: str
    position: str
    available: bool
    has_draft_control: bool
    player_id: str | None = None
    ambiguous: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "position", normalize_player_position(self.position))
        _required_bool(self.available, "row.available")
        _required_bool(self.has_draft_control, "row.has_draft_control")
        _required_bool(self.ambiguous, "row.ambiguous")
        if self.player_id is not None:
            object.__setattr__(self, "player_id", _required_text(self.player_id, "row.player_id"))
        elif not self.ambiguous:
            raise ValueError("row.player_id may be null only when row.ambiguous is true")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservedDraftPlayer":
        _reject_private_keys(value)
        allowed = {
            "name", "nfl_team", "position", "available", "has_draft_control", "player_id", "ambiguous"
        }
        unknown = set(value).difference(allowed)
        if unknown:
            raise ValueError(f"row contains unknown fields: {', '.join(sorted(unknown))}")
        required = {"name", "nfl_team", "position", "available", "has_draft_control", "player_id", "ambiguous"}
        missing = required.difference(value)
        if missing:
            raise ValueError("row is missing required fields: " + ", ".join(sorted(missing)))
        return cls(
            name=_required_text(value.get("name"), "row.name"),
            nfl_team=normalize_team(_required_text(value.get("nfl_team"), "row.nfl_team")),
            position=normalize_player_position(_required_text(value.get("position"), "row.position")),
            available=_required_bool(value.get("available"), "row.available"),
            has_draft_control=_required_bool(
                value.get("has_draft_control"), "row.has_draft_control"
            ),
            player_id=(
                _required_text(value.get("player_id"), "row.player_id")
                if value.get("player_id") is not None
                else None
            ),
            ambiguous=_required_bool(value["ambiguous"], "row.ambiguous"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "nfl_team": self.nfl_team,
            "position": self.position,
            "available": self.available,
            "has_draft_control": self.has_draft_control,
            "player_id": self.player_id,
            "ambiguous": self.ambiguous,
        }


@dataclass(frozen=True, slots=True)
class ObservedCompletedPick:
    overall_pick: int
    player_id: str
    team: str
    provenance: str = "external"

    def __post_init__(self) -> None:
        _positive_int(self.overall_pick, "completed_pick.overall_pick")
        _required_text(self.player_id, "completed_pick.player_id")
        _required_text(self.team, "completed_pick.team")
        if self.provenance not in {"external", "keeper", "manager", "platform-autodraft"}:
            raise ValueError("completed_pick.provenance is unsupported")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservedCompletedPick":
        if not isinstance(value, Mapping):
            raise ValueError("completed_picks entries must be objects")
        _reject_private_keys(value)
        unknown = set(value).difference({"overall_pick", "player_id", "team", "provenance"})
        if unknown:
            raise ValueError(
                f"completed pick contains unknown fields: {', '.join(sorted(unknown))}"
            )
        return cls(
            overall_pick=_positive_int(value.get("overall_pick"), "completed_pick.overall_pick"),
            player_id=_required_text(value.get("player_id"), "completed_pick.player_id"),
            team=_required_text(value.get("team"), "completed_pick.team"),
            provenance=_required_text(value.get("provenance", "external"), "completed_pick.provenance"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "overall_pick": self.overall_pick,
            "player_id": self.player_id,
            "team": self.team,
            "provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class ObservedDraftState:
    room_fingerprint: str
    your_turn: bool
    current_team: str
    overall_pick: int
    clock_seconds: int
    roster_count: int
    rows: tuple[ObservedDraftPlayer, ...]
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
    last_pick_provenance: str | None = None
    last_pick_timer_expired: bool | None = None
    platform: DraftPlatform = DraftPlatform.YAHOO
    adapter_version: str = "1"
    phase: str = "in_progress"
    control_status: str = "ready"
    positional_demand: Mapping[str, int] = field(default_factory=dict)
    completed_picks: tuple[ObservedCompletedPick, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "room_fingerprint", validate_room_fingerprint(self.room_fingerprint))
        object.__setattr__(self, "platform", _enum(DraftPlatform, self.platform, "platform"))
        _required_text(self.current_team, "current_team")
        _positive_int(self.overall_pick, "overall_pick")
        for field_name in ("clock_seconds", "roster_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if not isinstance(self.rows, tuple) or not all(
            isinstance(item, ObservedDraftPlayer) for item in self.rows
        ):
            raise ValueError("rows must be a tuple of ObservedDraftPlayer values")
        if not isinstance(self.completed_picks, tuple) or not all(
            isinstance(item, ObservedCompletedPick) for item in self.completed_picks
        ):
            raise ValueError("completed_picks must be a tuple of ObservedCompletedPick values")
        for field_name in (
            "your_turn",
            "autodraft_off",
            "authentication_challenge",
            "modal_ambiguity",
            "reconnecting",
            "control_interrupted",
        ):
            _required_bool(getattr(self, field_name), field_name)
        if self.room_advanced is not None:
            _required_bool(self.room_advanced, "room_advanced")
        if self.last_pick_timer_expired is not None:
            _required_bool(self.last_pick_timer_expired, "last_pick_timer_expired")
        if self.last_pick_overall is not None:
            _positive_int(self.last_pick_overall, "last_pick_overall")
        for field_name in (
            "state_hash",
            "config_hash",
            "board_hash",
            "last_pick_player_id",
            "last_pick_position",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _required_text(value, field_name)
        if self.last_pick_provenance is not None and self.last_pick_provenance not in {
            "manager-approved-chrome",
            "platform-autodraft",
            "external",
        }:
            raise ValueError("last_pick_provenance is unsupported")
        _required_text(self.adapter_version, "adapter_version")
        _required_text(self.phase, "phase")
        _required_text(self.control_status, "control_status")
        object.__setattr__(self, "captured_at", parse_datetime(self.captured_at, "captured_at"))
        for field_name, values in (
            ("queue_player_ids", self.queue_player_ids),
            ("unavailable_player_ids", self.unavailable_player_ids),
            ("roster_player_ids", self.roster_player_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicate IDs")
        roster_ids = set(self.roster_player_ids)
        unavailable_ids = set(self.unavailable_player_ids)
        if self.roster_count != len(roster_ids):
            raise ValueError("roster_count must equal the number of roster_player_ids")
        if not roster_ids.issubset(unavailable_ids):
            raise ValueError("roster_player_ids must be included in unavailable_player_ids")
        if set(self.queue_player_ids).intersection(unavailable_ids):
            raise ValueError("queue_player_ids must not contain unavailable players")
        demand: dict[str, int] = {}
        for position, raw_count in self.positional_demand.items():
            normalized = normalize_player_position(
                _required_text(str(position), "positional_demand position")
            )
            if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
                raise ValueError("positional_demand counts must be non-negative integers")
            demand[normalized] = raw_count
        object.__setattr__(self, "positional_demand", MappingProxyType(demand))
        completed_overalls = [item.overall_pick for item in self.completed_picks]
        completed_players = [item.player_id for item in self.completed_picks]
        if len(completed_overalls) != len(set(completed_overalls)):
            raise ValueError("completed_picks overall picks must be unique")
        if len(completed_players) != len(set(completed_players)):
            raise ValueError("completed_picks player IDs must be unique")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservedDraftState":
        _reject_private_keys(value)
        allowed = {
            "room_fingerprint", "your_turn", "current_team", "overall_pick", "clock_seconds",
            "roster_count", "rows", "visible_players", "autodraft_off", "captured_at", "queue_player_ids", "queue",
            "unavailable_player_ids", "roster_player_ids", "authentication_challenge",
            "modal_ambiguity", "reconnecting", "control_interrupted", "state_hash", "config_hash",
            "board_hash", "last_pick_player_id", "last_pick_position", "last_pick_overall",
            "room_advanced", "last_pick_evidence", "platform", "adapter_version", "phase",
            "control_status", "positional_demand", "last_pick_provenance",
            "last_pick_timer_expired",
            "completed_picks",
        }
        unknown = set(value).difference(allowed)
        if unknown:
            raise ValueError(f"observed state contains unknown fields: {', '.join(sorted(unknown))}")
        required_control_evidence = {
            "authentication_challenge",
            "modal_ambiguity",
            "reconnecting",
            "control_interrupted",
        }
        missing_control_evidence = required_control_evidence.difference(value)
        if missing_control_evidence:
            raise ValueError(
                "observed state is missing required control evidence: "
                + ", ".join(sorted(missing_control_evidence))
            )
        required_state = {
            "platform",
            "adapter_version",
            "phase",
            "control_status",
            "queue_player_ids",
            "unavailable_player_ids",
            "roster_player_ids",
            "rows",
        }
        missing_state = required_state.difference(value)
        if missing_state:
            raise ValueError(
                "observed state is missing required fields: " + ", ".join(sorted(missing_state))
            )
        rows = value.get("rows", value.get("visible_players", []))
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
        positional_demand = value.get("positional_demand", {})
        if not isinstance(positional_demand, Mapping):
            raise ValueError("positional_demand must be an object")
        completed_picks = value.get("completed_picks", [])
        if not isinstance(completed_picks, Sequence) or isinstance(completed_picks, (str, bytes)):
            raise ValueError("completed_picks must be a list")
        last_pick = value.get("last_pick_evidence", {})
        if not isinstance(last_pick, Mapping):
            raise ValueError("last_pick_evidence must be an object")
        _reject_private_keys(last_pick)
        allowed_last_pick = {
            "player_id", "position", "overall_pick", "room_advanced", "provenance",
            "timer_expired",
        }
        unknown_last_pick = set(last_pick).difference(allowed_last_pick)
        if unknown_last_pick:
            raise ValueError(
                f"last_pick_evidence contains unknown fields: {', '.join(sorted(unknown_last_pick))}"
            )
        duplicate_last_pick_fields = {
            "last_pick_player_id": "player_id",
            "last_pick_position": "position",
            "last_pick_overall": "overall_pick",
            "room_advanced": "room_advanced",
            "last_pick_provenance": "provenance",
            "last_pick_timer_expired": "timer_expired",
        }
        for outer_name, nested_name in duplicate_last_pick_fields.items():
            if (
                outer_name in value
                and nested_name in last_pick
                and value[outer_name] != last_pick[nested_name]
            ):
                raise ValueError(
                    f"{outer_name} contradicts last_pick_evidence.{nested_name}"
                )
        if "room_advanced" in value:
            room_advanced = value["room_advanced"]
        else:
            room_advanced = last_pick.get("room_advanced")
        if room_advanced is not None:
            room_advanced = _required_bool(room_advanced, "room_advanced")
        last_pick_timer_expired = value.get(
            "last_pick_timer_expired",
            last_pick.get("timer_expired"),
        )
        if last_pick_timer_expired is not None:
            last_pick_timer_expired = _required_bool(
                last_pick_timer_expired,
                "last_pick_timer_expired",
            )
        roster_count = value.get("roster_count")
        if isinstance(roster_count, bool) or not isinstance(roster_count, int) or roster_count < 0:
            raise ValueError("roster_count must be a non-negative integer")
        clock_seconds = value.get("clock_seconds")
        if isinstance(clock_seconds, bool) or not isinstance(clock_seconds, int) or clock_seconds < 0:
            raise ValueError("clock_seconds must be a non-negative integer")
        return cls(
            room_fingerprint=_required_text(value.get("room_fingerprint"), "room_fingerprint"),
            your_turn=_required_bool(value.get("your_turn"), "your_turn"),
            current_team=_required_text(value.get("current_team"), "current_team"),
            overall_pick=_positive_int(value.get("overall_pick"), "overall_pick"),
            clock_seconds=clock_seconds,
            roster_count=roster_count,
            rows=tuple(ObservedDraftPlayer.from_dict(row) for row in rows),
            autodraft_off=_required_bool(value.get("autodraft_off"), "autodraft_off"),
            captured_at=parse_datetime(value.get("captured_at"), "captured_at"),
            queue_player_ids=tuple(_required_text(item, "queue player ID") for item in queue),
            unavailable_player_ids=tuple(_required_text(item, "unavailable player ID") for item in unavailable),
            roster_player_ids=tuple(_required_text(item, "roster player ID") for item in roster_players),
            authentication_challenge=_required_bool(
                value["authentication_challenge"], "authentication_challenge"
            ),
            modal_ambiguity=_required_bool(value["modal_ambiguity"], "modal_ambiguity"),
            reconnecting=_required_bool(value["reconnecting"], "reconnecting"),
            control_interrupted=_required_bool(
                value["control_interrupted"], "control_interrupted"
            ),
            state_hash=value.get("state_hash"),
            config_hash=value.get("config_hash"),
            board_hash=value.get("board_hash"),
            last_pick_player_id=value.get("last_pick_player_id", last_pick.get("player_id")),
            last_pick_position=value.get("last_pick_position", last_pick.get("position")),
            last_pick_overall=value.get("last_pick_overall", last_pick.get("overall_pick")),
            room_advanced=room_advanced,
            last_pick_provenance=value.get(
                "last_pick_provenance",
                last_pick.get("provenance"),
            ),
            last_pick_timer_expired=last_pick_timer_expired,
            platform=_enum(DraftPlatform, value["platform"], "platform"),
            adapter_version=_required_text(value["adapter_version"], "adapter_version"),
            phase=_required_text(value["phase"], "phase"),
            control_status=_required_text(value["control_status"], "control_status"),
            positional_demand=positional_demand,
            completed_picks=tuple(ObservedCompletedPick.from_dict(item) for item in completed_picks),
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
            "last_pick_provenance": self.last_pick_provenance,
            "last_pick_timer_expired": self.last_pick_timer_expired,
            "platform": self.platform.value,
            "adapter_version": self.adapter_version,
            "phase": self.phase,
            "control_status": self.control_status,
            "positional_demand": dict(self.positional_demand),
            "completed_picks": [item.to_dict() for item in self.completed_picks],
        }

    @property
    def visible_players(self) -> tuple[ObservedDraftPlayer, ...]:
        return self.rows

    @property
    def last_pick_evidence(self) -> dict[str, JsonValue]:
        return {
            "player_id": self.last_pick_player_id,
            "position": self.last_pick_position,
            "overall_pick": self.last_pick_overall,
            "room_advanced": self.room_advanced,
            "provenance": self.last_pick_provenance,
            "timer_expired": self.last_pick_timer_expired,
        }


# Compatibility names for the existing Yahoo-only CLI and safety surface.
ObservedYahooRow = ObservedDraftPlayer
ObservedYahooState = ObservedDraftState


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
