"""Versioned, browser-driver-free contracts for Chrome draft workflows.

The package never discovers or clicks DOM nodes.  A user-controlled Chrome
workflow reports sanitized semantic matches, and this module validates that
report before an observation, queue write, pick write, or verification can be
trusted.  Mappings are deliberately contract-only until a timed mock rehearsal
has exercised the corresponding platform write path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence


class ChromeOperation(str, Enum):
    OBSERVE = "observe"
    QUEUE = "queue"
    PICK = "pick"
    VERIFY = "verify"


class LocatorKind(str, Enum):
    ACCESSIBLE_ROLE = "accessible_role"
    STABLE_ATTRIBUTE = "stable_attribute"


class ControlCardinality(str, Enum):
    EXACTLY_ONE = "exactly_one"
    ONE_OR_MORE = "one_or_more"
    ABSENT = "absent"


_WRITE_OPERATIONS = frozenset({ChromeOperation.QUEUE, ChromeOperation.PICK})
_SUPPORTED_PLATFORMS = frozenset({"yahoo", "espn", "sleeper"})
_STABLE_ATTRIBUTES = frozenset({"aria-label", "data-testid", "data-test", "data-qa", "name", "role"})
_FORBIDDEN_TEXT = (
    "css selector",
    "css_selector",
    "xpath",
    "nth-child",
    "nth_child",
    "dom path",
    "dom_path",
    "shadowroot",
    "shadow_root",
    "private state",
    "private_state",
)


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    result = value.strip()
    lowered = result.casefold()
    if any(forbidden in lowered for forbidden in _FORBIDDEN_TEXT):
        raise ValueError(f"{field_name} contains a forbidden DOM/private-state assumption")
    return result


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field_name} must be a list")
    result = tuple(_required_text(item, f"{field_name} item") for item in value)
    if not result:
        raise ValueError(f"{field_name} must not be empty")
    return result


def _enum(enum_type: type[Enum], value: Any, field_name: str) -> Any:
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as error:
        choices = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field_name} must be one of: {choices}") from error


@dataclass(frozen=True, slots=True)
class SemanticLocator:
    """A selector-independent hint that a Chrome workflow may resolve."""

    kind: LocatorKind
    role: str | None = None
    name_tokens: tuple[str, ...] = ()
    attribute_name: str | None = None
    value_tokens: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum(LocatorKind, self.kind, "locator.kind"))
        if self.kind is LocatorKind.ACCESSIBLE_ROLE:
            role = _required_text(self.role, "locator.role")
            tokens = tuple(_required_text(item, "locator.name_tokens item") for item in self.name_tokens)
            if not tokens:
                raise ValueError("accessible-role locators require name_tokens")
            if self.attribute_name is not None or self.value_tokens:
                raise ValueError("accessible-role locators cannot contain attribute fields")
            object.__setattr__(self, "role", role.casefold())
            object.__setattr__(self, "name_tokens", tuple(item.casefold() for item in tokens))
            return

        attribute_name = _required_text(self.attribute_name, "locator.attribute_name").casefold()
        if attribute_name not in _STABLE_ATTRIBUTES:
            raise ValueError(f"unsupported or unstable locator attribute: {attribute_name}")
        tokens = tuple(_required_text(item, "locator.value_tokens item") for item in self.value_tokens)
        if not tokens:
            raise ValueError("stable-attribute locators require value_tokens")
        if self.role is not None or self.name_tokens:
            raise ValueError("stable-attribute locators cannot contain accessible-role fields")
        object.__setattr__(self, "attribute_name", attribute_name)
        object.__setattr__(self, "value_tokens", tuple(item.casefold() for item in tokens))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "role": self.role,
            "name_tokens": list(self.name_tokens),
            "attribute_name": self.attribute_name,
            "value_tokens": list(self.value_tokens),
        }


@dataclass(frozen=True, slots=True)
class ControlContract:
    semantic_name: str
    operations: tuple[ChromeOperation, ...]
    cardinality: ControlCardinality
    locators: tuple[SemanticLocator, ...]
    actionable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "semantic_name", _required_text(self.semantic_name, "control.semantic_name"))
        operations = tuple(_enum(ChromeOperation, item, "control.operations item") for item in self.operations)
        if not operations:
            raise ValueError("control.operations must not be empty")
        if len(set(operations)) != len(operations):
            raise ValueError("control.operations must not contain duplicates")
        object.__setattr__(self, "operations", operations)
        object.__setattr__(
            self,
            "cardinality",
            _enum(ControlCardinality, self.cardinality, "control.cardinality"),
        )
        if not self.locators:
            raise ValueError("control.locators must not be empty")
        if self.cardinality is ControlCardinality.ABSENT and self.actionable:
            raise ValueError("absence sentinels cannot be actionable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_name": self.semantic_name,
            "operations": [item.value for item in self.operations],
            "cardinality": self.cardinality.value,
            "locators": [item.to_dict() for item in self.locators],
            "actionable": self.actionable,
        }


@dataclass(frozen=True, slots=True)
class PlatformMapping:
    platform: str
    version: str
    controls: tuple[ControlContract, ...]
    mapping_status: str = "contract_only"
    writes_enabled_by_default: bool = False
    timed_mock_rehearsal_required: bool = True
    evidence_note: str = "Semantic contract only; live queue and pick behavior is unverified."

    def __post_init__(self) -> None:
        platform = _required_text(self.platform, "mapping.platform").casefold()
        if platform not in _SUPPORTED_PLATFORMS:
            raise ValueError(f"unsupported platform: {platform}")
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "version", _required_text(self.version, "mapping.version"))
        object.__setattr__(self, "mapping_status", _required_text(self.mapping_status, "mapping.mapping_status"))
        object.__setattr__(self, "evidence_note", _required_text(self.evidence_note, "mapping.evidence_note"))
        names = [item.semantic_name for item in self.controls]
        if len(names) != len(set(names)):
            raise ValueError("mapping control semantic names must be unique")
        if self.writes_enabled_by_default:
            raise ValueError("platform writes cannot be enabled by a static mapping")
        if not self.timed_mock_rehearsal_required:
            raise ValueError("platform mappings must require a timed mock rehearsal")

    def controls_for(self, operation: ChromeOperation | str) -> tuple[ControlContract, ...]:
        resolved = _enum(ChromeOperation, operation, "operation")
        return tuple(item for item in self.controls if resolved in item.operations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "version": self.version,
            "mapping_status": self.mapping_status,
            "writes_enabled_by_default": self.writes_enabled_by_default,
            "timed_mock_rehearsal_required": self.timed_mock_rehearsal_required,
            "evidence_note": self.evidence_note,
            "controls": [item.to_dict() for item in self.controls],
        }


@dataclass(frozen=True, slots=True)
class DiscoveredControl:
    locator_kind: LocatorKind
    visible: bool
    enabled: bool
    ambiguous: bool = False
    role: str | None = None
    accessible_name: str | None = None
    attribute_name: str | None = None
    attribute_value: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "locator_kind", _enum(LocatorKind, self.locator_kind, "match.locator_kind"))
        for field_name in ("visible", "enabled", "ambiguous"):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"match.{field_name} must be boolean")

        if self.locator_kind is LocatorKind.ACCESSIBLE_ROLE:
            object.__setattr__(self, "role", _required_text(self.role, "match.role").casefold())
            object.__setattr__(
                self,
                "accessible_name",
                _required_text(self.accessible_name, "match.accessible_name"),
            )
            if self.attribute_name is not None or self.attribute_value is not None:
                raise ValueError("accessible-role matches cannot contain attribute fields")
            return

        attribute_name = _required_text(self.attribute_name, "match.attribute_name").casefold()
        if attribute_name not in _STABLE_ATTRIBUTES:
            raise ValueError(f"unsupported or unstable match attribute: {attribute_name}")
        object.__setattr__(self, "attribute_name", attribute_name)
        object.__setattr__(
            self,
            "attribute_value",
            _required_text(self.attribute_value, "match.attribute_value"),
        )
        if self.role is not None or self.accessible_name is not None:
            raise ValueError("stable-attribute matches cannot contain accessible-role fields")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DiscoveredControl":
        if not isinstance(value, Mapping):
            raise ValueError("control match must be an object")
        allowed = {
            "locator_kind",
            "visible",
            "enabled",
            "ambiguous",
            "role",
            "accessible_name",
            "attribute_name",
            "attribute_value",
        }
        unknown = set(value).difference(allowed)
        if unknown:
            raise ValueError(f"control match contains forbidden or unknown fields: {', '.join(sorted(unknown))}")
        required = {"locator_kind", "visible", "enabled", "ambiguous"}
        missing = required.difference(value)
        if missing:
            raise ValueError("control match is missing required fields: " + ", ".join(sorted(missing)))
        return cls(
            locator_kind=_enum(LocatorKind, value.get("locator_kind"), "match.locator_kind"),
            visible=value.get("visible"),
            enabled=value.get("enabled"),
            ambiguous=value["ambiguous"],
            role=value.get("role"),
            accessible_name=value.get("accessible_name"),
            attribute_name=value.get("attribute_name"),
            attribute_value=value.get("attribute_value"),
        )


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    platform: str
    mapping_version: str
    operation: ChromeOperation
    controls: Mapping[str, tuple[DiscoveredControl, ...]]

    def __post_init__(self) -> None:
        platform = _required_text(self.platform, "snapshot.platform").casefold()
        if platform not in _SUPPORTED_PLATFORMS:
            raise ValueError(f"unsupported platform: {platform}")
        object.__setattr__(self, "platform", platform)
        object.__setattr__(
            self,
            "mapping_version",
            _required_text(self.mapping_version, "snapshot.mapping_version"),
        )
        object.__setattr__(self, "operation", _enum(ChromeOperation, self.operation, "snapshot.operation"))
        if not isinstance(self.controls, Mapping):
            raise ValueError("snapshot.controls must be an object")
        normalized: dict[str, tuple[DiscoveredControl, ...]] = {}
        for raw_name, raw_matches in self.controls.items():
            name = _required_text(raw_name, "snapshot control name")
            if not isinstance(raw_matches, Sequence) or isinstance(raw_matches, (str, bytes)):
                raise ValueError(f"snapshot.controls.{name} must be a list")
            matches = tuple(
                item if isinstance(item, DiscoveredControl) else DiscoveredControl.from_dict(item)
                for item in raw_matches
            )
            normalized[name] = matches
        object.__setattr__(self, "controls", MappingProxyType(normalized))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ControlSnapshot":
        if not isinstance(value, Mapping):
            raise ValueError("control snapshot must be an object")
        allowed = {"platform", "mapping_version", "operation", "controls"}
        unknown = set(value).difference(allowed)
        if unknown:
            raise ValueError(f"control snapshot contains forbidden or unknown fields: {', '.join(sorted(unknown))}")
        controls = value.get("controls")
        if not isinstance(controls, Mapping):
            raise ValueError("snapshot.controls must be an object")
        return cls(
            platform=_required_text(value.get("platform"), "snapshot.platform"),
            mapping_version=_required_text(value.get("mapping_version"), "snapshot.mapping_version"),
            operation=_enum(ChromeOperation, value.get("operation"), "snapshot.operation"),
            controls=controls,
        )


@dataclass(frozen=True, slots=True)
class ControlValidation:
    allowed: bool
    reasons: tuple[str, ...] = ()

    @property
    def outcome(self) -> str:
        return "allow" if self.allowed else "halt"


def _accessible(role: str, *name_tokens: str) -> SemanticLocator:
    return SemanticLocator(LocatorKind.ACCESSIBLE_ROLE, role=role, name_tokens=tuple(name_tokens))


def _control(
    semantic_name: str,
    operations: tuple[ChromeOperation, ...],
    cardinality: ControlCardinality,
    *locators: SemanticLocator,
    actionable: bool = False,
) -> ControlContract:
    return ControlContract(semantic_name, operations, cardinality, tuple(locators), actionable)


_ALL = tuple(ChromeOperation)
_OBSERVE_WRITE_VERIFY = (
    ChromeOperation.OBSERVE,
    ChromeOperation.QUEUE,
    ChromeOperation.PICK,
    ChromeOperation.VERIFY,
)


def _mapping(
    platform: str,
    *,
    room_names: tuple[str, ...],
    pick_names: tuple[str, ...],
    team_names: tuple[str, ...],
    clock_names: tuple[str, ...],
    roster_names: tuple[str, ...],
    player_names: tuple[str, ...],
    queue_names: tuple[str, ...],
    queue_action_names: tuple[str, ...],
    pick_action_names: tuple[str, ...],
    submit_names: tuple[str, ...],
    last_pick_names: tuple[str, ...],
    evidence_note: str,
) -> PlatformMapping:
    controls = (
        _control("room_marker", _OBSERVE_WRITE_VERIFY, ControlCardinality.EXACTLY_ONE, _accessible("heading", *room_names)),
        _control("phase_indicator", _OBSERVE_WRITE_VERIFY, ControlCardinality.EXACTLY_ONE, _accessible("status", "draft")),
        _control("pick_indicator", _OBSERVE_WRITE_VERIFY, ControlCardinality.EXACTLY_ONE, _accessible("status", *pick_names)),
        _control("active_team", _OBSERVE_WRITE_VERIFY, ControlCardinality.EXACTLY_ONE, _accessible("status", *team_names)),
        _control("clock", _OBSERVE_WRITE_VERIFY, ControlCardinality.EXACTLY_ONE, _accessible("timer", *clock_names)),
        _control("roster_region", _OBSERVE_WRITE_VERIFY, ControlCardinality.EXACTLY_ONE, _accessible("region", *roster_names)),
        _control("available_players_region", _OBSERVE_WRITE_VERIFY, ControlCardinality.EXACTLY_ONE, _accessible("region", *player_names)),
        _control("queue_region", _OBSERVE_WRITE_VERIFY, ControlCardinality.EXACTLY_ONE, _accessible("region", *queue_names)),
        _control("visible_player_rows", (ChromeOperation.OBSERVE, ChromeOperation.QUEUE, ChromeOperation.PICK), ControlCardinality.ONE_OR_MORE, _accessible("row", "player")),
        _control("control_status", _OBSERVE_WRITE_VERIFY, ControlCardinality.EXACTLY_ONE, _accessible("status", "draft", "control")),
        _control("queue_player_action", (ChromeOperation.QUEUE,), ControlCardinality.ONE_OR_MORE, _accessible("button", *queue_action_names), actionable=True),
        _control("ordered_queue_entries", (ChromeOperation.QUEUE,), ControlCardinality.ONE_OR_MORE, _accessible("listitem", "queue")),
        _control("pick_player_action", (ChromeOperation.PICK,), ControlCardinality.EXACTLY_ONE, _accessible("button", *pick_action_names), actionable=True),
        _control("submit_pick_action", (ChromeOperation.PICK,), ControlCardinality.EXACTLY_ONE, _accessible("button", *submit_names), actionable=True),
        _control("last_pick_evidence", (ChromeOperation.VERIFY,), ControlCardinality.EXACTLY_ONE, _accessible("region", *last_pick_names)),
        _control("roster_entries", (ChromeOperation.VERIFY,), ControlCardinality.ONE_OR_MORE, _accessible("listitem", "roster")),
        _control("unavailable_player_evidence", (ChromeOperation.VERIFY,), ControlCardinality.EXACTLY_ONE, _accessible("status", "unavailable", "player")),
        _control("authentication_challenge", _ALL, ControlCardinality.ABSENT, _accessible("dialog", "sign", "in")),
        _control("modal_ambiguity", _ALL, ControlCardinality.ABSENT, _accessible("alertdialog", "ambiguous", "draft")),
        _control("reconnect_status", _ALL, ControlCardinality.ABSENT, _accessible("status", "reconnect")),
    )
    return PlatformMapping(platform=platform, version="1", controls=controls, evidence_note=evidence_note)


_MAPPINGS = {
    ("yahoo", "1"): _mapping(
        "yahoo",
        room_names=("live", "draft"),
        pick_names=("pick",),
        team_names=("on", "clock"),
        clock_names=("time", "remaining"),
        roster_names=("my", "team"),
        player_names=("players",),
        queue_names=("draft", "queue"),
        queue_action_names=("add", "queue"),
        pick_action_names=("draft",),
        submit_names=("draft", "player"),
        last_pick_names=("draft", "results"),
        evidence_note="Contract-only Yahoo semantic labels; live queue and pick writes require a successful timed mock rehearsal.",
    ),
    ("espn", "1"): _mapping(
        "espn",
        room_names=("live", "draft"),
        pick_names=("pick",),
        team_names=("on", "clock"),
        clock_names=("time", "remaining"),
        roster_names=("my", "team"),
        player_names=("players",),
        queue_names=("player", "queue"),
        queue_action_names=("add", "queue"),
        pick_action_names=("draft",),
        submit_names=("draft", "player"),
        last_pick_names=("draft", "results"),
        evidence_note="Contract-only ESPN semantic labels; live queue and pick writes require a successful timed mock rehearsal.",
    ),
    ("sleeper", "1"): _mapping(
        "sleeper",
        room_names=("draftboard",),
        pick_names=("pick",),
        team_names=("on", "clock"),
        clock_names=("pick", "timer"),
        roster_names=("roster",),
        player_names=("player", "pool"),
        queue_names=("queue",),
        queue_action_names=("queue",),
        pick_action_names=("draft",),
        submit_names=("confirm",),
        last_pick_names=("draftboard",),
        evidence_note=(
            "Sleeper Queue/Roster/Chat, Auto-pick, ordered queue entries, next-pick, Remove, and pre-draft Set Player "
            "surfaces were observed; live click, timer, and queue-consumption behavior remains unverified and rehearsal-gated."
        ),
    ),
}


PLATFORM_MAPPINGS: Mapping[tuple[str, str], PlatformMapping] = MappingProxyType(_MAPPINGS)
CURRENT_MAPPING_VERSION: Mapping[str, str] = MappingProxyType({platform: "1" for platform in _SUPPORTED_PLATFORMS})


def get_platform_mapping(platform: str, version: str | None = None) -> PlatformMapping:
    normalized = _required_text(platform, "platform").casefold()
    if normalized not in _SUPPORTED_PLATFORMS:
        raise ValueError(f"unsupported platform: {normalized}")
    resolved_version = version or CURRENT_MAPPING_VERSION[normalized]
    try:
        return PLATFORM_MAPPINGS[(normalized, resolved_version)]
    except KeyError as error:
        raise ValueError(f"unsupported mapping version for {normalized}: {resolved_version}") from error


def _matches_locator(match: DiscoveredControl, locator: SemanticLocator) -> bool:
    if match.locator_kind is not locator.kind:
        return False
    if locator.kind is LocatorKind.ACCESSIBLE_ROLE:
        if match.role != locator.role or match.accessible_name is None:
            return False
        value = match.accessible_name.casefold()
        return all(token in value for token in locator.name_tokens)
    if match.attribute_name != locator.attribute_name or match.attribute_value is None:
        return False
    value = match.attribute_value.casefold()
    return all(token in value for token in locator.value_tokens)


def validate_control_snapshot(
    snapshot: ControlSnapshot | Mapping[str, Any],
    *,
    write_rehearsal_passed: bool = False,
    expected_platform: str | None = None,
    expected_mapping_version: str | None = None,
) -> ControlValidation:
    """Validate one sanitized Chrome transaction boundary without side effects.

    Queue and pick snapshots remain blocked unless the caller supplies separate
    evidence that the platform's non-consequential timed mock rehearsal passed.
    This flag does not persist or activate anything.
    """

    observed = snapshot if isinstance(snapshot, ControlSnapshot) else ControlSnapshot.from_dict(snapshot)
    reasons: list[str] = []
    if expected_platform is not None:
        normalized_expected_platform = _required_text(expected_platform, "expected_platform").casefold()
        if observed.platform != normalized_expected_platform:
            reasons.append(
                f"snapshot platform {observed.platform} does not match expected platform {normalized_expected_platform}"
            )
    if expected_mapping_version is not None:
        normalized_expected_version = _required_text(
            expected_mapping_version,
            "expected_mapping_version",
        )
        if observed.mapping_version != normalized_expected_version:
            reasons.append(
                "snapshot mapping version "
                f"{observed.mapping_version} does not match expected version {normalized_expected_version}"
            )
    try:
        mapping = get_platform_mapping(observed.platform, observed.mapping_version)
    except ValueError as error:
        reasons.append(str(error))
        return ControlValidation(False, tuple(dict.fromkeys(reasons)))

    if observed.platform != mapping.platform:
        reasons.append("snapshot platform does not match the selected mapping")
    if observed.mapping_version != mapping.version:
        reasons.append("snapshot mapping version does not match the selected mapping")
    if observed.operation in _WRITE_OPERATIONS and not write_rehearsal_passed:
        reasons.append(f"{mapping.platform} write path requires a completed timed mock rehearsal")

    contracts = mapping.controls_for(observed.operation)
    known_names = {item.semantic_name for item in mapping.controls}
    unknown_names = set(observed.controls).difference(known_names)
    if unknown_names:
        reasons.append(f"unexpected semantic controls: {', '.join(sorted(unknown_names))}")

    for contract in contracts:
        if contract.semantic_name not in observed.controls:
            reasons.append(f"{contract.semantic_name}: control check is missing")
            continue
        matches = observed.controls[contract.semantic_name]
        count = len(matches)
        if contract.cardinality is ControlCardinality.EXACTLY_ONE and count != 1:
            reasons.append(f"{contract.semantic_name}: expected exactly one match, found {count}")
            continue
        if contract.cardinality is ControlCardinality.ONE_OR_MORE and count < 1:
            reasons.append(f"{contract.semantic_name}: expected one or more matches, found 0")
            continue
        if contract.cardinality is ControlCardinality.ABSENT:
            if count:
                reasons.append(f"{contract.semantic_name}: blocking surface is present")
            continue

        for index, match in enumerate(matches):
            prefix = f"{contract.semantic_name}[{index}]"
            if match.ambiguous:
                reasons.append(f"{prefix}: match is ambiguous")
            if not match.visible:
                reasons.append(f"{prefix}: match is not visible")
            if contract.actionable and not match.enabled:
                reasons.append(f"{prefix}: action is disabled")
            if not any(_matches_locator(match, locator) for locator in contract.locators):
                reasons.append(f"{prefix}: match does not satisfy the semantic locator contract")

    return ControlValidation(not reasons, tuple(dict.fromkeys(reasons)))


__all__ = [
    "CURRENT_MAPPING_VERSION",
    "PLATFORM_MAPPINGS",
    "ChromeOperation",
    "ControlCardinality",
    "ControlContract",
    "ControlSnapshot",
    "ControlValidation",
    "DiscoveredControl",
    "LocatorKind",
    "PlatformMapping",
    "SemanticLocator",
    "get_platform_mapping",
    "validate_control_snapshot",
]
