"""Identifier handling: number-prefix detection, sys_id heuristics, resolution."""

from __future__ import annotations

import pytest

import servicenow_client as snow
from conftest import SYS_ID


@pytest.mark.parametrize(
    ("number", "table"),
    [
        ("INC0010023", "incident"),
        ("CHG0000123", "change_request"),
        ("CTASK0000042", "change_task"),
        ("REQ0000042", "sc_request"),
        ("RITM0000042", "sc_req_item"),
        ("SCTASK0000042", "sc_task"),
        ("PRB0000042", "problem"),
        ("VIT0000042", "sn_vul_vulnerable_item"),
    ],
)
def test_prefix_detection(number, table):
    assert snow.detect_table(number) == (table, False)


def test_prefix_detection_is_case_insensitive():
    assert snow.detect_table("inc0010023") == ("incident", False)


def test_sys_id_requires_table():
    with pytest.raises(ValueError, match="--table") as excinfo:
        snow.detect_table(SYS_ID)
    assert excinfo.value.error_class == "usage"


def test_sys_id_with_table_alias():
    assert snow.detect_table(SYS_ID, "vit") == ("sn_vul_vulnerable_item", True)


def test_explicit_table_overrides_prefix():
    assert snow.detect_table("INC0010023", "problem") == ("problem", False)


def test_unknown_prefix_lists_known_ones():
    with pytest.raises(ValueError) as excinfo:
        snow.detect_table("XYZ0001")
    assert "INC" in str(excinfo.value) and "VIT" in str(excinfo.value)


def test_not_quite_sys_ids_are_rejected():
    with pytest.raises(ValueError):
        snow.detect_table("a" * 31)  # one char short of a sys_id, no prefix
    with pytest.raises(ValueError):
        snow.detect_table("A" * 32)  # uppercase hex is not a sys_id, no prefix


def test_resolve_sys_id_by_number(transport):
    transport.queue_json([{"sys_id": "b" * 32, "number": "INC0010023"}])
    assert snow._resolve_sys_id(snow._client(), "incident", "INC0010023") == "b" * 32
    url = transport.urls()[0]
    assert "sysparm_query=number%3DINC0010023" in url
    assert "sysparm_limit=2" in url  # 2 rows is enough to prove ambiguity


def test_resolve_sys_id_passes_through_sys_ids(transport):
    assert snow._resolve_sys_id(snow._client(), "incident", SYS_ID) == SYS_ID
    assert transport.requests == []


def test_resolve_sys_id_no_match(transport):
    transport.queue_json([])
    with pytest.raises(ValueError) as excinfo:
        snow._resolve_sys_id(snow._client(), "incident", "INC0099999")
    assert excinfo.value.error_class == "not_found"


def test_resolve_sys_id_ambiguous_number(transport):
    transport.queue_json([{"sys_id": "b" * 32}, {"sys_id": "c" * 32}])
    with pytest.raises(ValueError, match="multiple"):
        snow._resolve_sys_id(snow._client(), "incident", "INC0010023")
