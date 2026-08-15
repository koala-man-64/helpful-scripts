"""Write-safety guardrails: read-only mode, dry-run, delete gates, denylist, truncation."""

from __future__ import annotations

import pytest

import servicenow_client as snow
from conftest import SYS_ID, record


def test_read_only_refuses_update_with_zero_http(transport, env):
    env.setenv("SERVICENOW_READ_ONLY", "true")
    with pytest.raises(RuntimeError, match="SERVICENOW_READ_ONLY") as excinfo:
        snow.update_record("INC0010023", {"state": "6"})
    assert excinfo.value.error_class == "guarded"
    assert transport.requests == []


def test_read_only_refuses_every_write_verb(transport, env):
    env.setenv("SERVICENOW_READ_ONLY", "true")
    attempts = [
        lambda: snow.create_record("incident", {"short_description": "x"}),
        lambda: snow.add_comment("INC0010023", "x"),
        lambda: snow.add_work_note("INC0010023", "x"),
        lambda: snow.resolve_record("INC0010023", code="c", notes="n"),
        lambda: snow.close_record("SCTASK0000042"),
        lambda: snow.assign_record("INC0010023", to="jdoe"),
        lambda: snow.attach_file(SYS_ID, "x.txt", table="incident"),
        lambda: snow.delete_record(SYS_ID, table="incident", force=True),
    ]
    for attempt in attempts:
        with pytest.raises(RuntimeError, match="READ_ONLY|ALLOW_DELETE"):
            attempt()
    assert transport.requests == []


def test_read_only_still_allows_reads(transport, env):
    env.setenv("SERVICENOW_READ_ONLY", "true")
    transport.queue_json([record()])
    assert snow.get_record("INC0010023")["short_description"] == "VPN down"


def test_choke_point_also_guards_direct_client_use(transport, env):
    # Defense in depth: even raw client calls cannot write in read-only mode.
    env.setenv("SERVICENOW_READ_ONLY", "true")
    with pytest.raises(RuntimeError, match="READ_ONLY"):
        snow._client().request("PATCH", "/api/now/table/incident/x", json_body={})
    assert transport.requests == []


def test_dry_run_update_previews_exact_request(transport):
    transport.queue_json([{"sys_id": SYS_ID}])
    result = snow.update_record("INC0010023", {"state": "6"}, dry_run=True)
    assert result["dry_run"] is True and result["method"] == "PATCH"
    assert SYS_ID in result["url"]
    assert result["payload"] == {"state": "6"}
    assert len(transport.requests) == 1  # only the number->sys_id resolution GET


def test_dry_run_create_sends_nothing(transport):
    result = snow.create_record("incident", {"short_description": "x"}, dry_run=True)
    assert result["dry_run"] is True and result["method"] == "POST"
    assert transport.requests == []


def test_delete_needs_force_flag(transport):
    with pytest.raises(ValueError, match="--force") as excinfo:
        snow.delete_record(SYS_ID, table="incident")
    assert excinfo.value.error_class == "guarded"
    assert transport.requests == []


def test_delete_needs_env_gate(transport):
    with pytest.raises(RuntimeError, match="SERVICENOW_ALLOW_DELETE") as excinfo:
        snow.delete_record(SYS_ID, table="incident", force=True)
    assert excinfo.value.error_class == "guarded"
    assert transport.requests == []


def test_delete_with_both_gates(transport, env):
    env.setenv("SERVICENOW_ALLOW_DELETE", "true")
    transport.queue(204, b"")
    result = snow.delete_record(SYS_ID, table="incident", force=True)
    assert result == {"deleted": True, "table": "incident", "sys_id": SYS_ID}
    assert transport.requests[0]["method"] == "DELETE"


def test_platform_table_create_denied(transport):
    with pytest.raises(ValueError, match="platform") as excinfo:
        snow.create_record("sys_properties", {"name": "x", "value": "y"})
    assert excinfo.value.error_class == "guarded"
    assert transport.requests == []


def test_platform_table_update_denied(transport):
    with pytest.raises(ValueError, match="platform"):
        snow.update_record(SYS_ID, {"script": "x"}, table="sys_script_include")
    assert transport.requests == []


def test_group_membership_and_role_tables_denied(transport):
    # Writing these grants privileges just as surely as sys_user_has_role does.
    for table in ("sys_user_grmember", "sys_group_has_role"):
        with pytest.raises(ValueError, match="platform"):
            snow.create_record(table, {"user": "x", "group": "y"})
    assert transport.requests == []


def test_platform_tables_still_readable(transport):
    transport.queue_json([])
    snow.query_records("sys_properties", query="name=glide.foo")
    assert len(transport.requests) == 1


def test_truncation_marker_math():
    out = snow._truncate_strings({"d": "x" * 25}, 10)
    assert out["d"] == "x" * 10 + "...[truncated 15 chars]"


def test_truncation_boundary_untouched():
    assert snow._truncate_strings("x" * 10, 10) == "x" * 10


def test_truncation_recurses_into_lists():
    out = snow._truncate_strings([{"a": "y" * 12}], 5)
    assert out[0]["a"].startswith("yyyyy...[truncated")


def test_env_flag_variants(env):
    for value in ("1", "true", "YES", "On"):
        env.setenv("SERVICENOW_READ_ONLY", value)
        assert snow._read_only(), value
    env.setenv("SERVICENOW_READ_ONLY", "false")
    assert not snow._read_only()
