"""Query building: sysparm construction, encoding, pagination, canned filters."""

from __future__ import annotations

import argparse
import re

import pytest

import servicenow_client as snow
from conftest import SYS_ID, record


def test_table_params_minimal():
    assert snow._table_params() == {"sysparm_exclude_reference_link": "true"}


def test_display_mode_mapping():
    assert snow._table_params(display="value")["sysparm_display_value"] == "false"
    assert snow._table_params(display="display")["sysparm_display_value"] == "true"
    assert snow._table_params(display="both")["sysparm_display_value"] == "all"


def test_display_mode_invalid():
    with pytest.raises(ValueError, match="display"):
        snow._table_params(display="fancy")


def test_limit_hard_capped():
    assert snow._table_params(limit=999999)["sysparm_limit"] == "1000"


def test_encoded_query_is_url_encoded(transport):
    transport.queue_json([])
    snow.query_records("incident", query="active=true^short_descriptionLIKEvpn down")
    url = transport.urls()[0]
    assert "sysparm_query=active%3Dtrue%5Eshort_descriptionLIKEvpn+down" in url


def test_defaults_fields_and_reference_link_exclusion(transport):
    transport.queue_json([])
    snow.query_records("incident")
    url = transport.urls()[0]
    assert "sysparm_exclude_reference_link=true" in url
    assert "number%2Csys_id%2Cshort_description" in url  # curated default fields


def test_pagination_metadata_with_total(transport):
    transport.queue_json([record(), record()], headers={"x-total-count": "10"})
    result = snow.query_records("incident", limit=2)
    assert result["count"] == 2 and result["total"] == 10
    assert result["has_more"] is True and result["next_offset"] == 2


def test_pagination_metadata_complete_set(transport):
    transport.queue_json([record(), record()], headers={"x-total-count": "2"})
    result = snow.query_records("incident", limit=25)
    assert result["has_more"] is False and result["next_offset"] is None


def test_pagination_without_total_uses_full_page_heuristic(transport):
    transport.queue_json([record()])
    result = snow.query_records("incident", limit=1)
    assert result["total"] is None and result["has_more"] is True


def test_fetch_all_walks_offsets_and_skips_recount(transport):
    transport.queue_json([record(), record()], headers={"x-total-count": "5"})
    transport.queue_json([record(), record()])
    transport.queue_json([record()])
    result = snow.query_records("incident", limit=2, fetch_all=True)
    assert result["count"] == 5 and result["truncated"] is False
    urls = transport.urls()
    assert len(urls) == 3
    assert "sysparm_offset" not in urls[0] and "sysparm_no_count" not in urls[0]
    assert "sysparm_offset=2" in urls[1] and "sysparm_no_count=true" in urls[1]
    assert "sysparm_offset=4" in urls[2]


def test_fetch_all_hard_cap_sets_truncated(transport):
    transport.queue_json([record(), record()])
    result = snow.query_records("incident", limit=2, fetch_all=True, max_records=2)
    assert result["count"] == 2
    assert result["truncated"] is True and result["has_more"] is True
    assert len(transport.requests) == 1


def test_fetch_all_clamps_oversized_limit(transport):
    # sysparm_limit is capped at 1000 on the wire; the walk must page with the
    # effective limit instead of stopping after one "short" 1000-row page.
    transport.queue_json([record()] * 1000)
    transport.queue_json([record()] * 5)
    result = snow.query_records("incident", limit=2000, fetch_all=True, max_records=3000)
    assert result["count"] == 1005 and result["truncated"] is False
    urls = transport.urls()
    assert "sysparm_limit=1000" in urls[0]
    assert "sysparm_offset=1000" in urls[1]


def test_fetch_all_short_final_page_overshoot_sets_truncated(transport):
    # Rows dropped by the max_records cap must be reported even when the final
    # page was short.
    transport.queue_json([record()] * 10)
    transport.queue_json([record()] * 4)
    result = snow.query_records("incident", limit=10, fetch_all=True, max_records=12)
    assert result["count"] == 12 and result["truncated"] is True


def test_limit_clamp_keeps_has_more_heuristic_honest(transport):
    transport.queue_json([record()] * 1000)
    result = snow.query_records("incident", limit=2000)
    assert result["has_more"] is True and result["next_offset"] == 1000


# -- canned filters ----------------------------------------------------------


def _ns(**overrides):
    defaults = dict(
        query=None, active=False, group=None, assigned_to=None, unassigned=False,
        opened_last=None, updated_last=None, order_by=None, desc=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_compose_query_all_flags_and_join_order():
    q = snow._compose_query(
        _ns(query="priority=1", active=True, group="Network Ops",
            assigned_to="jdoe", unassigned=True, opened_last="7d",
            order_by="sys_updated_on", desc=True)
    )
    parts = q.split("^")
    assert parts[0] == "priority=1"
    assert "active=true" in parts
    assert "assignment_group.name=Network Ops" in parts
    assert "assigned_to.user_name=jdoe" in parts
    assert "assigned_toISEMPTY" in parts
    assert any(part.startswith("opened_at>=") for part in parts)
    assert parts[-1] == "ORDERBYDESCsys_updated_on"


def test_compose_query_empty_is_none():
    assert snow._compose_query(_ns()) is None


def test_updated_last_uses_sys_updated_on():
    q = snow._compose_query(_ns(updated_last="12h"))
    assert q.startswith("sys_updated_on>=")


def test_ref_term_variants():
    assert snow._ref_term("assigned_to", SYS_ID) == f"assigned_to={SYS_ID}"
    assert (
        snow._ref_term("assigned_to", "j.doe@example.com")
        == "assigned_to.email=j.doe@example.com"
    )
    assert snow._ref_term("assigned_to", "Jane Doe") == "assigned_to.name=Jane Doe"
    assert snow._ref_term("assigned_to", "jdoe") == "assigned_to.user_name=jdoe"
    assert (
        snow._ref_term("assignment_group", "Network Ops")
        == "assignment_group.name=Network Ops"
    )


def test_since_formats_absolute_utc_bound():
    stamp = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
    for spec in ("7d", "12h", "30m"):
        assert stamp.match(snow._since(spec)), spec


def test_since_rejects_garbage():
    for bad in ("7x", "d", "", "1.5d", "7 d"):
        with pytest.raises(ValueError):
            snow._since(bad)
