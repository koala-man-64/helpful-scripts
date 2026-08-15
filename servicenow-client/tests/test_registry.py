"""Record-type registry invariants and the generic-table fallback."""

from __future__ import annotations

import json

import servicenow_client as snow


def test_all_first_class_tables_registered():
    assert set(snow.TABLES) == {
        "incident",
        "change_request",
        "change_task",
        "sc_request",
        "sc_req_item",
        "sc_task",
        "problem",
        "sn_vul_vulnerable_item",
    }


def test_prefix_map_complete():
    assert snow.PREFIXES == {
        "INC": "incident",
        "CHG": "change_request",
        "CTASK": "change_task",
        "REQ": "sc_request",
        "RITM": "sc_req_item",
        "SCTASK": "sc_task",
        "PRB": "problem",
        "VIT": "sn_vul_vulnerable_item",
    }


def test_aliases_resolve_case_insensitively():
    assert snow.resolve_table("vit") == "sn_vul_vulnerable_item"
    assert snow.resolve_table("RITM") == "sc_req_item"
    assert snow.resolve_table("change") == "change_request"
    assert snow.resolve_table("Incident") == "incident"
    # Unknown names pass through untouched - any table is addressable.
    assert snow.resolve_table("u_custom_table") == "u_custom_table"


def test_generic_fallback_config():
    cfg = snow.config_for("u_custom_table")
    assert cfg.table == "u_custom_table"
    assert cfg.journal_fields == ("comments", "work_notes")
    assert cfg.transitions == {} and cfg.default_fields == ()


def test_incident_transitions():
    cfg = snow.TABLES["incident"]
    assert cfg.transitions["resolve"] == {"state": "6"}
    assert cfg.transitions["close"] == {"state": "7"}
    assert cfg.transition_required["resolve"] == ("code", "notes")
    assert cfg.close_code_field == "close_code"


def test_sc_request_uses_request_state():
    cfg = snow.TABLES["sc_request"]
    assert cfg.state_field == "request_state"
    assert cfg.transitions["close"] == {"request_state": "closed_complete"}


def test_vit_is_work_notes_only_with_plugin_hint_and_no_transitions():
    cfg = snow.TABLES["sn_vul_vulnerable_item"]
    assert cfg.journal_fields == ("work_notes",)
    assert cfg.plugin and "Vulnerability Response" in cfg.plugin
    # VR state values are release-dependent; transitions are deliberately empty.
    assert cfg.transitions == {}


def test_registry_dump_is_json_serializable():
    dumped = json.dumps(snow.registry())
    assert "sn_vul_vulnerable_item" in dumped and "INC" in dumped


def test_every_default_field_list_carries_identity_fields():
    for cfg in snow.TABLES.values():
        assert "number" in cfg.default_fields, cfg.table
        assert "sys_id" in cfg.default_fields, cfg.table
