"""Small, explicit research adapters and the canonical snapshot contract."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import io
import json
import re
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.request import urlopen

from .identity import normalize_name, normalize_position, normalize_team
from .models import (
    AcquisitionMethod,
    DEFAULT_FRESHNESS_HOURS,
    DraftPlatform,
    JsonValue,
    PlayerEvidence,
    SignalRole,
    SourceArtifact,
    datetime_text,
    parse_datetime,
)
from .scoring import normalize_scoring_format


BORIS_URLS = {
    "standard": "https://s3-us-west-1.amazonaws.com/fftiers/out/weekly-ALL.csv",
    "half-ppr": "https://s3-us-west-1.amazonaws.com/fftiers/out/weekly-ALL-HALF-PPR.csv",
    "ppr": "https://s3-us-west-1.amazonaws.com/fftiers/out/weekly-ALL-PPR.csv",
}
SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
SLEEPER_DRAFT_URL = "https://api.sleeper.app/v1/draft/{draft_id}"
SLEEPER_DRAFT_PICKS_URL = "https://api.sleeper.app/v1/draft/{draft_id}/picks"


FRESHNESS_HOURS = DEFAULT_FRESHNESS_HOURS


class ReadableResponse(Protocol):
    headers: Any

    def read(self) -> bytes: ...

    def close(self) -> None: ...


OpenTransport = Callable[[str], ReadableResponse]
IdentityResolver = Callable[[str, str], tuple[str, str] | None]


@dataclass(frozen=True, slots=True)
class ResearchBundle:
    artifacts: tuple[SourceArtifact, ...]
    evidence: tuple[PlayerEvidence, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported research bundle schema version")
        artifact_keys = {(item.checksum, item.signal_role) for item in self.artifacts}
        if len(artifact_keys) != len(self.artifacts):
            raise ValueError("research bundle contains duplicate artifact role/checksum pairs")
        for item in self.evidence:
            if (item.artifact_checksum, item.signal_role) not in artifact_keys:
                raise ValueError(
                    f"evidence for {item.player_id!r} does not reference a same-role artifact"
                )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResearchBundle":
        allowed = {"schema_version", "artifacts", "evidence"}
        unknown = set(value).difference(allowed)
        if unknown:
            raise ValueError(f"research bundle contains unknown fields: {', '.join(sorted(unknown))}")
        artifacts = value.get("artifacts")
        evidence = value.get("evidence")
        if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)):
            raise ValueError("research bundle artifacts must be a list")
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
            raise ValueError("research bundle evidence must be a list")
        return cls(
            schema_version=value.get("schema_version", 1),
            artifacts=tuple(SourceArtifact.from_dict(item) for item in artifacts),
            evidence=tuple(PlayerEvidence.from_dict(item) for item in evidence),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "evidence": [item.to_dict() for item in self.evidence],
        }


_SLEEPER_DRAFT_SETTINGS = frozenset(
    {
        "teams",
        "rounds",
        "pick_timer",
        "reversal_round",
        "slots_qb",
        "slots_rb",
        "slots_wr",
        "slots_te",
        "slots_flex",
        "slots_bn",
        "slots_k",
        "slots_def",
        "slots_wr_rb",
        "slots_rec_flex",
        "slots_super_flex",
    }
)


def _sleeper_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _sleeper_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _sleeper_roster_id(value: Any, field_name: str) -> int:
    # The documented picks example encodes roster_id as a decimal string,
    # while slot_to_roster_id uses JSON numbers.
    if isinstance(value, str) and value.isdecimal():
        value = int(value)
    return _sleeper_positive_int(value, field_name)


def _sleeper_optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer or null")
    return value


@dataclass(frozen=True, slots=True)
class SleeperDraftRoom:
    """Allowlisted read-only draft metadata from Sleeper's documented API."""

    draft_id: str
    status: str
    draft_type: str
    season: str
    sport: str
    start_time: int | None
    last_picked: int | None
    settings: Mapping[str, JsonValue]
    slot_to_roster_id: Mapping[str, int]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "draft_id": self.draft_id,
            "status": self.status,
            "draft_type": self.draft_type,
            "season": self.season,
            "sport": self.sport,
            "start_time": self.start_time,
            "last_picked": self.last_picked,
            "settings": dict(self.settings),
            "slot_to_roster_id": dict(self.slot_to_roster_id),
        }


@dataclass(frozen=True, slots=True)
class SleeperDraftPick:
    """Sanitized pick identity; manager/user identifiers are intentionally omitted."""

    player_id: str
    pick_no: int
    round: int
    draft_slot: int
    roster_id: int
    name: str | None = None
    nfl_team: str | None = None
    position: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "player_id": self.player_id,
            "pick_no": self.pick_no,
            "round": self.round,
            "draft_slot": self.draft_slot,
            "roster_id": self.roster_id,
            "name": self.name,
            "nfl_team": self.nfl_team,
            "position": self.position,
        }


@dataclass(frozen=True, slots=True)
class SleeperDraftSnapshot:
    room: SleeperDraftRoom
    picks: tuple[SleeperDraftPick, ...]
    retrieved_at: datetime
    adapter_version: str = "sleeper-draft-read-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "retrieved_at", parse_datetime(self.retrieved_at, "retrieved_at"))
        pick_numbers = [pick.pick_no for pick in self.picks]
        if len(pick_numbers) != len(set(pick_numbers)):
            raise ValueError("Sleeper draft picks must have unique pick_no values")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "adapter_version": self.adapter_version,
            "retrieved_at": datetime_text(self.retrieved_at),
            "room": self.room.to_dict(),
            "picks": [pick.to_dict() for pick in self.picks],
        }


def _sleeper_draft_id(value: str) -> str:
    draft_id = _sleeper_text(value, "draft_id")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", draft_id):
        raise ValueError("draft_id contains unsupported characters")
    return draft_id


def _json_value(payload: bytes | str, label: str) -> Any:
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid {label} JSON: {error}") from error


def parse_sleeper_draft_room(payload: bytes | str, *, expected_draft_id: str) -> SleeperDraftRoom:
    value = _json_value(payload, "Sleeper draft")
    if not isinstance(value, Mapping):
        raise ValueError("Sleeper draft response must be an object")
    draft_id = _sleeper_text(value.get("draft_id"), "Sleeper draft_id")
    if draft_id != expected_draft_id:
        raise ValueError("Sleeper draft response does not match the requested draft_id")
    raw_settings = value.get("settings", {})
    if not isinstance(raw_settings, Mapping):
        raise ValueError("Sleeper draft settings must be an object")
    settings: dict[str, JsonValue] = {}
    for key in sorted(_SLEEPER_DRAFT_SETTINGS.intersection(raw_settings)):
        item = raw_settings[key]
        if item is None or isinstance(item, (bool, int, float, str)):
            settings[key] = item
        else:
            raise ValueError(f"Sleeper draft setting {key!r} must be a JSON scalar")
    raw_slots = value.get("slot_to_roster_id", {})
    if not isinstance(raw_slots, Mapping):
        raise ValueError("Sleeper slot_to_roster_id must be an object")
    slots = {
        _sleeper_text(str(slot), "Sleeper draft slot"): _sleeper_positive_int(
            roster_id, f"Sleeper roster ID for slot {slot}"
        )
        for slot, roster_id in raw_slots.items()
    }
    return SleeperDraftRoom(
        draft_id=draft_id,
        status=_sleeper_text(value.get("status"), "Sleeper draft status"),
        draft_type=_sleeper_text(value.get("type"), "Sleeper draft type"),
        season=_sleeper_text(str(value.get("season", "")), "Sleeper draft season"),
        sport=_sleeper_text(value.get("sport"), "Sleeper draft sport"),
        start_time=_sleeper_optional_int(value.get("start_time"), "Sleeper start_time"),
        last_picked=_sleeper_optional_int(value.get("last_picked"), "Sleeper last_picked"),
        settings=settings,
        slot_to_roster_id=slots,
    )


def parse_sleeper_draft_picks(payload: bytes | str) -> tuple[SleeperDraftPick, ...]:
    value = _json_value(payload, "Sleeper draft picks")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("Sleeper draft picks response must be a list")
    picks: list[SleeperDraftPick] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ValueError(f"Sleeper draft pick {index} must be an object")
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError(f"Sleeper draft pick {index} metadata must be an object")
        first = metadata.get("first_name")
        last = metadata.get("last_name")
        name_parts = [part.strip() for part in (first, last) if isinstance(part, str) and part.strip()]
        team = metadata.get("team")
        position = metadata.get("position")
        normalized_team = None
        normalized_position = None
        if isinstance(team, str) and team.strip():
            try:
                normalized_team = normalize_team(team)
            except ValueError:
                normalized_team = None
        if isinstance(position, str) and position.strip():
            try:
                normalized_position = normalize_position(position)
            except ValueError:
                normalized_position = None
        picks.append(
            SleeperDraftPick(
                player_id=_sleeper_text(raw.get("player_id"), f"Sleeper pick {index} player_id"),
                pick_no=_sleeper_positive_int(raw.get("pick_no"), f"Sleeper pick {index} pick_no"),
                round=_sleeper_positive_int(raw.get("round"), f"Sleeper pick {index} round"),
                draft_slot=_sleeper_positive_int(
                    raw.get("draft_slot"), f"Sleeper pick {index} draft_slot"
                ),
                roster_id=_sleeper_roster_id(
                    raw.get("roster_id"), f"Sleeper pick {index} roster_id"
                ),
                name=" ".join(name_parts) or None,
                nfl_team=normalized_team,
                position=normalized_position,
            )
        )
    return tuple(sorted(picks, key=lambda pick: pick.pick_no))


def bundle_from_json(text: str) -> ResearchBundle:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid research snapshot JSON: {error}") from error
    if not isinstance(value, Mapping):
        raise ValueError("research snapshot must be a JSON object")
    return ResearchBundle.from_dict(value)


def bundle_to_json(bundle: ResearchBundle) -> str:
    return json.dumps(bundle.to_dict(), indent=2, sort_keys=True) + "\n"


def _read_transport(opener: OpenTransport, url: str) -> tuple[bytes, Any]:
    response = opener(url)
    try:
        payload = response.read()
        if not isinstance(payload, bytes):
            raise ValueError("research transport must return bytes")
        return payload, getattr(response, "headers", {})
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _header(headers: Any, name: str) -> str | None:
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        return str(value) if value is not None else None
    if isinstance(headers, Mapping):
        for key, value in headers.items():
            if str(key).casefold() == name.casefold():
                return str(value)
    return None


def _published_at(headers: Any) -> datetime | None:
    value = _header(headers, "Last-Modified")
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _checksum(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _slug(value: str) -> str:
    result = "-".join(re.findall(r"[a-z0-9]+", normalize_name(value)))
    if not result:
        raise ValueError("cannot derive a stable player ID")
    return result


def parse_boris_tiers(
    payload: bytes | str,
    artifact: SourceArtifact,
    *,
    identity_resolver: IdentityResolver | None = None,
) -> tuple[PlayerEvidence, ...]:
    """Parse the documented Boris Top-200 CSV without retaining raw rows."""

    if artifact.signal_role is not SignalRole.TIER:
        raise ValueError("Boris artifact must have the tier signal role")
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload
    reader = csv.DictReader(io.StringIO(text, newline=""))
    required = {"Rank", "Player.Name", "Tier", "Position"}
    missing = required.difference(reader.fieldnames or ())
    if missing:
        raise ValueError(f"Boris CSV missing columns: {', '.join(sorted(missing))}")
    evidence: list[PlayerEvidence] = []
    seen_ids: set[str] = set()
    for line, row in enumerate(reader, start=2):
        try:
            name = row["Player.Name"].strip()
            position = normalize_position(row["Position"])
            tier = int(row["Tier"])
            if tier <= 0:
                raise ValueError("tier must be positive")
            resolved = identity_resolver(name, position) if identity_resolver else None
            if resolved is None:
                player_id = f"boris:{_slug(name)}:{position.casefold()}"
                team = None
                ambiguous = True
            else:
                player_id, raw_team = resolved
                team = normalize_team(raw_team)
                ambiguous = False
            if player_id in seen_ids:
                raise ValueError(f"duplicate player identity {player_id!r}")
            seen_ids.add(player_id)
            evidence.append(
                PlayerEvidence(
                    player_id=player_id,
                    name=name,
                    nfl_team=team,
                    position=position,
                    signal_role=SignalRole.TIER,
                    artifact_checksum=artifact.checksum,
                    tier=tier,
                    ambiguous=ambiguous,
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid Boris CSV line {line}: {error}") from error
    if not evidence:
        raise ValueError("Boris CSV contains no players")
    return tuple(evidence)


def fetch_boris_tiers(
    scoring_format: str = "standard",
    *,
    opener: OpenTransport = urlopen,
    now: datetime | None = None,
    identity_resolver: IdentityResolver | None = None,
) -> ResearchBundle:
    """Fetch one public Boris tier file through an injectable read transport."""

    normalized = normalize_scoring_format(scoring_format)
    url = BORIS_URLS[normalized]
    payload, headers = _read_transport(opener, url)
    retrieved_at = parse_datetime(now or datetime.now(timezone.utc), "now")
    artifact = SourceArtifact(
        source="boris-chen",
        upstream_family="fantasypros-consensus",
        signal_role=SignalRole.TIER,
        scoring_context=normalized,
        acquisition_method=AcquisitionMethod.DIRECT,
        published_at=_published_at(headers),
        retrieved_at=retrieved_at,
        checksum=_checksum(payload),
        freshness_hours=FRESHNESS_HOURS[SignalRole.TIER],
        safe_provenance={"adapter": "boris-csv-v1", "public_url": url},
        mandatory=False,
    )
    return ResearchBundle((artifact,), parse_boris_tiers(payload, artifact, identity_resolver=identity_resolver))


def parse_sleeper_players(
    payload: bytes | str,
    identity_artifact: SourceArtifact,
    status_artifact: SourceArtifact,
) -> tuple[PlayerEvidence, ...]:
    """Parse Sleeper's documented keyed NFL player map."""

    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid Sleeper player JSON: {error}") from error
    if not isinstance(value, Mapping):
        raise ValueError("Sleeper player response must be an object keyed by player ID")
    evidence: list[PlayerEvidence] = []
    for player_id in sorted(value):
        row = value[player_id]
        if not isinstance(row, Mapping):
            continue
        position = row.get("position")
        team = row.get("team")
        name = row.get("full_name")
        if not name:
            name = " ".join(str(part).strip() for part in (row.get("first_name"), row.get("last_name")) if part)
        if not name or not position or not team:
            continue
        try:
            normalized_position = normalize_position(str(position))
            normalized_team = normalize_team(str(team))
        except ValueError:
            # The v1 compiler intentionally ignores IDP and non-player records.
            continue
        if normalized_position not in {"QB", "RB", "WR", "TE", "K", "DEF"}:
            continue
        evidence.append(
            PlayerEvidence(
                player_id=str(player_id),
                name=str(name),
                nfl_team=normalized_team,
                position=normalized_position,
                signal_role=SignalRole.IDENTITY,
                artifact_checksum=identity_artifact.checksum,
            )
        )
        status = row.get("injury_status") or row.get("status") or ("active" if row.get("active") is True else "unknown")
        evidence.append(
            PlayerEvidence(
                player_id=str(player_id),
                name=str(name),
                nfl_team=normalized_team,
                position=normalized_position,
                signal_role=SignalRole.STATUS,
                artifact_checksum=status_artifact.checksum,
                status=str(status).strip().casefold(),
            )
        )
    if not evidence:
        raise ValueError("Sleeper player response contains no supported active-player identities")
    return tuple(evidence)


def fetch_sleeper_players(
    *,
    opener: OpenTransport = urlopen,
    now: datetime | None = None,
) -> ResearchBundle:
    """Fetch the documented read-only Sleeper player map through a test seam."""

    payload, _ = _read_transport(opener, SLEEPER_PLAYERS_URL)
    retrieved_at = parse_datetime(now or datetime.now(timezone.utc), "now")
    digest = _checksum(payload)
    common: dict[str, Any] = {
        "source": "sleeper-api",
        "upstream_family": "sleeper",
        "scoring_context": None,
        "acquisition_method": AcquisitionMethod.DIRECT,
        "published_at": None,
        "retrieved_at": retrieved_at,
        "checksum": digest,
        "safe_provenance": {"adapter": "sleeper-players-v1", "public_url": SLEEPER_PLAYERS_URL},
        "mandatory": False,
    }
    identity_artifact = SourceArtifact(
        signal_role=SignalRole.IDENTITY,
        freshness_hours=FRESHNESS_HOURS[SignalRole.IDENTITY],
        **common,
    )
    status_artifact = SourceArtifact(
        signal_role=SignalRole.STATUS,
        freshness_hours=FRESHNESS_HOURS[SignalRole.STATUS],
        **common,
    )
    return ResearchBundle(
        (identity_artifact, status_artifact),
        parse_sleeper_players(payload, identity_artifact, status_artifact),
    )


def fetch_sleeper_draft_room(
    draft_id: str,
    *,
    opener: OpenTransport = urlopen,
) -> SleeperDraftRoom:
    """GET documented Sleeper draft metadata; this adapter has no write surface."""

    validated_id = _sleeper_draft_id(draft_id)
    payload, _ = _read_transport(opener, SLEEPER_DRAFT_URL.format(draft_id=validated_id))
    return parse_sleeper_draft_room(payload, expected_draft_id=validated_id)


def fetch_sleeper_draft_picks(
    draft_id: str,
    *,
    opener: OpenTransport = urlopen,
) -> tuple[SleeperDraftPick, ...]:
    """GET documented Sleeper draft picks; queue/pick writes remain Chrome-owned."""

    validated_id = _sleeper_draft_id(draft_id)
    payload, _ = _read_transport(opener, SLEEPER_DRAFT_PICKS_URL.format(draft_id=validated_id))
    return parse_sleeper_draft_picks(payload)


def fetch_sleeper_draft(
    draft_id: str,
    *,
    opener: OpenTransport = urlopen,
    now: datetime | None = None,
) -> SleeperDraftSnapshot:
    """Capture one deterministic, sanitized read-only Sleeper draft snapshot."""

    validated_id = _sleeper_draft_id(draft_id)
    room = fetch_sleeper_draft_room(validated_id, opener=opener)
    picks = fetch_sleeper_draft_picks(validated_id, opener=opener)
    return SleeperDraftSnapshot(
        room=room,
        picks=picks,
        retrieved_at=parse_datetime(now or datetime.now(timezone.utc), "now"),
    )


def merge_bundles(*bundles: ResearchBundle) -> ResearchBundle:
    artifacts: dict[tuple[str, SignalRole], SourceArtifact] = {}
    evidence: dict[tuple[str, SignalRole, str, str], PlayerEvidence] = {}
    for bundle in bundles:
        for artifact in bundle.artifacts:
            key = (artifact.checksum, artifact.signal_role)
            previous = artifacts.get(key)
            if previous is not None and previous != artifact:
                raise ValueError(
                    "conflicting artifact metadata for checksum/role "
                    f"{artifact.checksum}/{artifact.signal_role.value}"
                )
            artifacts[key] = artifact
        for item in bundle.evidence:
            key = (item.player_id, item.signal_role, item.artifact_checksum, item.platform.value if item.platform else "")
            previous = evidence.get(key)
            if previous is not None and previous != item:
                raise ValueError(
                    f"conflicting evidence for {item.player_id!r}/{item.signal_role.value}"
                )
            evidence[key] = item
    return ResearchBundle(
        tuple(sorted(artifacts.values(), key=lambda item: (item.signal_role.value, item.source, item.checksum))),
        tuple(sorted(evidence.values(), key=lambda item: (item.player_id, item.signal_role.value, item.artifact_checksum))),
    )
