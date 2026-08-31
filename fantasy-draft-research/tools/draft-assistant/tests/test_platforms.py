from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from fantasy_draft_assistant.models import ObservedDraftState
from fantasy_draft_assistant.platforms import (
    CURRENT_MAPPING_VERSION,
    ChromeOperation,
    ControlCardinality,
    ControlSnapshot,
    DiscoveredControl,
    LocatorKind,
    SemanticLocator,
    get_platform_mapping,
    validate_control_snapshot,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _match(locator: SemanticLocator, *, enabled: bool = True) -> DiscoveredControl:
    if locator.kind is LocatorKind.ACCESSIBLE_ROLE:
        return DiscoveredControl(
            locator_kind=locator.kind,
            role=locator.role,
            accessible_name=" ".join(locator.name_tokens),
            visible=True,
            enabled=enabled,
        )
    return DiscoveredControl(
        locator_kind=locator.kind,
        attribute_name=locator.attribute_name,
        attribute_value=" ".join(locator.value_tokens),
        visible=True,
        enabled=enabled,
    )


def _valid_snapshot(platform: str, operation: ChromeOperation) -> ControlSnapshot:
    mapping = get_platform_mapping(platform)
    controls: dict[str, tuple[DiscoveredControl, ...]] = {}
    for contract in mapping.controls_for(operation):
        if contract.cardinality is ControlCardinality.ABSENT:
            controls[contract.semantic_name] = ()
        else:
            controls[contract.semantic_name] = (_match(contract.locators[0]),)
    return ControlSnapshot(
        platform=platform,
        mapping_version=mapping.version,
        operation=operation,
        controls=controls,
    )


@pytest.mark.parametrize("platform", ["yahoo", "espn", "sleeper"])
def test_platform_contracts_are_versioned_semantic_and_write_gated(platform: str) -> None:
    mapping = get_platform_mapping(platform)

    assert mapping.version == CURRENT_MAPPING_VERSION[platform]
    assert mapping.mapping_status == "contract_only"
    assert mapping.writes_enabled_by_default is False
    assert mapping.timed_mock_rehearsal_required is True
    assert {item.semantic_name for item in mapping.controls_for(ChromeOperation.QUEUE)} >= {
        "queue_region",
        "queue_player_action",
        "ordered_queue_entries",
    }
    assert {item.semantic_name for item in mapping.controls_for(ChromeOperation.PICK)} >= {
        "pick_player_action",
        "submit_pick_action",
    }
    assert {item.semantic_name for item in mapping.controls_for(ChromeOperation.VERIFY)} >= {
        "last_pick_evidence",
        "roster_entries",
        "unavailable_player_evidence",
    }

    serialized = json.dumps(mapping.to_dict()).casefold()
    for forbidden in ("css_selector", "xpath", "nth-child", "dom_path", "private_state"):
        assert forbidden not in serialized


@pytest.mark.parametrize("platform", ["yahoo", "espn", "sleeper"])
def test_valid_semantic_observation_contract_passes(platform: str) -> None:
    decision = validate_control_snapshot(_valid_snapshot(platform, ChromeOperation.OBSERVE))

    assert decision.allowed is True
    assert decision.reasons == ()


def test_write_controls_require_rehearsal_and_unique_enabled_actions() -> None:
    snapshot = _valid_snapshot("espn", ChromeOperation.PICK)

    blocked = validate_control_snapshot(snapshot)
    assert blocked.allowed is False
    assert "timed mock rehearsal" in " ".join(blocked.reasons)

    allowed = validate_control_snapshot(snapshot, write_rehearsal_passed=True)
    assert allowed.allowed is True

    controls = dict(snapshot.controls)
    controls["submit_pick_action"] = controls["submit_pick_action"] * 2
    duplicated = replace(snapshot, controls=controls)
    rejected = validate_control_snapshot(duplicated, write_rehearsal_passed=True)
    assert rejected.allowed is False
    assert "submit_pick_action: expected exactly one match, found 2" in rejected.reasons

    controls = dict(snapshot.controls)
    controls["pick_player_action"] = (replace(controls["pick_player_action"][0], enabled=False),)
    disabled = replace(snapshot, controls=controls)
    rejected = validate_control_snapshot(disabled, write_rehearsal_passed=True)
    assert rejected.allowed is False
    assert "pick_player_action[0]: action is disabled" in rejected.reasons


def test_missing_ambiguous_and_reconnect_controls_fail_closed() -> None:
    snapshot = _valid_snapshot("yahoo", ChromeOperation.OBSERVE)

    controls = dict(snapshot.controls)
    controls.pop("clock")
    missing = validate_control_snapshot(replace(snapshot, controls=controls))
    assert missing.allowed is False
    assert "clock: control check is missing" in missing.reasons

    controls = dict(snapshot.controls)
    controls["room_marker"] = (replace(controls["room_marker"][0], ambiguous=True),)
    ambiguous = validate_control_snapshot(replace(snapshot, controls=controls))
    assert ambiguous.allowed is False
    assert "room_marker[0]: match is ambiguous" in ambiguous.reasons

    mapping = get_platform_mapping("yahoo")
    reconnect_contract = next(item for item in mapping.controls if item.semantic_name == "reconnect_status")
    controls = dict(snapshot.controls)
    controls["reconnect_status"] = (_match(reconnect_contract.locators[0]),)
    reconnect = validate_control_snapshot(replace(snapshot, controls=controls))
    assert reconnect.allowed is False
    assert "reconnect_status: blocking surface is present" in reconnect.reasons


def test_platform_and_mapping_version_mismatches_fail_closed() -> None:
    snapshot = _valid_snapshot("sleeper", ChromeOperation.OBSERVE)

    wrong_platform = validate_control_snapshot(snapshot, expected_platform="espn")
    assert wrong_platform.allowed is False
    assert "does not match expected platform espn" in " ".join(wrong_platform.reasons)

    wrong_expected_version = validate_control_snapshot(snapshot, expected_mapping_version="2")
    assert wrong_expected_version.allowed is False
    assert "does not match expected version 2" in " ".join(wrong_expected_version.reasons)

    unsupported_version = replace(snapshot, mapping_version="2")
    unsupported = validate_control_snapshot(unsupported_version)
    assert unsupported.allowed is False
    assert "unsupported mapping version for sleeper: 2" in unsupported.reasons


def test_raw_dom_and_private_state_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="forbidden or unknown fields"):
        ControlSnapshot.from_dict(
            {
                "platform": "yahoo",
                "mapping_version": "1",
                "operation": "observe",
                "controls": {
                    "room_marker": [
                        {
                            "locator_kind": "accessible_role",
                            "role": "heading",
                            "accessible_name": "Live Draft",
                            "visible": True,
                            "enabled": True,
                            "css_selector": "main div:nth-child(2)",
                        }
                    ]
                },
            }
        )

    with pytest.raises(ValueError, match="stable-attribute locators require value_tokens"):
        SemanticLocator(LocatorKind.STABLE_ATTRIBUTE, attribute_name="data-testid")


@pytest.mark.parametrize(
    ("fixture_name", "platform"),
    [
        ("espn_state.json", "espn"),
        ("sleeper_state.json", "sleeper"),
        ("ambiguous_player_state.json", "espn"),
        ("missing_controls_state.json", "sleeper"),
        ("reconnect_state.json", "yahoo"),
        ("timer_expiry_state.json", "espn"),
        ("queue_mismatch_state.json", "yahoo"),
        ("platform_auto_draft_state.json", "espn"),
    ],
)
def test_sanitized_observation_fixtures_parse(fixture_name: str, platform: str) -> None:
    raw = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))

    observed = ObservedDraftState.from_dict(raw)

    assert observed.platform.value == platform
    assert observed.adapter_version == "1"
    lowered = json.dumps(raw).casefold()
    for forbidden in ("cookie", "authorization", "private_url", "local_storage", "session_id"):
        assert forbidden not in lowered


def test_observation_fixtures_encode_requested_failure_states() -> None:
    ambiguous = ObservedDraftState.from_dict(
        json.loads((FIXTURES / "ambiguous_player_state.json").read_text(encoding="utf-8"))
    )
    missing = ObservedDraftState.from_dict(
        json.loads((FIXTURES / "missing_controls_state.json").read_text(encoding="utf-8"))
    )
    reconnect = ObservedDraftState.from_dict(
        json.loads((FIXTURES / "reconnect_state.json").read_text(encoding="utf-8"))
    )
    expired = ObservedDraftState.from_dict(
        json.loads((FIXTURES / "timer_expiry_state.json").read_text(encoding="utf-8"))
    )
    mismatch = ObservedDraftState.from_dict(
        json.loads((FIXTURES / "queue_mismatch_state.json").read_text(encoding="utf-8"))
    )
    auto = ObservedDraftState.from_dict(
        json.loads((FIXTURES / "platform_auto_draft_state.json").read_text(encoding="utf-8"))
    )

    assert any(row.ambiguous for row in ambiguous.rows)
    assert missing.control_interrupted and missing.control_status == "missing_pick_control"
    assert reconnect.reconnecting and reconnect.control_interrupted
    assert expired.clock_seconds == 0 and expired.phase == "pick_expired"
    assert mismatch.control_interrupted and mismatch.control_status == "queue_mismatch"
    assert auto.phase == "auto_drafted"
    assert auto.last_pick_player_id in auto.roster_player_ids
    assert auto.last_pick_player_id in auto.unavailable_player_ids
