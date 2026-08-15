"""High-level operations: reads, lookups, writes, transitions, attachments."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest

import servicenow_client as snow
from conftest import BASE, SYS_ID, record


def _queue_resolution(transport):
    transport.queue_json([{"sys_id": SYS_ID, "number": "INC0010023"}])


# -- get ---------------------------------------------------------------------


def test_get_by_number_normalizes(transport):
    transport.queue_json([record()])
    result = snow.get_record("INC0010023")
    url = transport.urls()[0]
    assert "/api/now/table/incident" in url
    assert "sysparm_query=number%3DINC0010023" in url
    assert "sysparm_display_value=all" in url
    assert result["url"].endswith(f"/incident.do?sys_id={SYS_ID}")
    assert result["state"] == {"value": "2", "display": "In Progress"}
    assert result["opened_at"]["value"] == "2026-08-01T10:00:00Z"
    assert result["short_description"] == "VPN down"  # value==display collapses
    assert list(result)[:2] == ["sys_id", "number"]  # identity fields lead
    assert list(result)[-1] == "url"


def test_get_by_sys_id_hits_direct_endpoint(transport):
    transport.queue_json(record())
    snow.get_record(SYS_ID, table="incident")
    assert f"/api/now/table/incident/{SYS_ID}" in transport.urls()[0]


def test_get_number_not_found(transport):
    transport.queue_json([])
    with pytest.raises(ValueError) as excinfo:
        snow.get_record("INC0099999")
    assert excinfo.value.error_class == "not_found"


def test_get_number_ambiguous(transport):
    transport.queue_json([record(), record()])
    with pytest.raises(ValueError, match="multiple"):
        snow.get_record("INC0010023")


def test_get_field_subset_keeps_identity(transport):
    transport.queue_json([record()])
    snow.get_record("INC0010023", fields=("state",))
    assert "sysparm_fields=state%2Cnumber%2Csys_id" in transport.urls()[0]


# -- notes / history ---------------------------------------------------------


def test_notes_query_shape_and_mapping(transport):
    _queue_resolution(transport)
    transport.queue_json(
        [
            {
                "sys_created_on": "2026-08-02 09:00:00",
                "sys_created_by": "jdoe",
                "element": "work_notes",
                "value": "checked firewall",
            }
        ]
    )
    notes = snow.get_notes("INC0010023")
    journal_url = transport.urls()[1]
    assert "/api/now/table/sys_journal_field" in journal_url
    expected = (
        "sysparm_query=nameINincident%2Ctask%5Eelement_id%3D" + SYS_ID
        + "%5EelementINcomments%2Cwork_notes%5EORDERBYDESCsys_created_on"
    )
    assert expected in journal_url
    assert notes == [
        {
            "when": "2026-08-02T09:00:00Z",
            "by": "jdoe",
            "type": "work_notes",
            "text": "checked firewall",
        }
    ]


def test_notes_type_filter(transport):
    _queue_resolution(transport)
    transport.queue_json([])
    snow.get_notes("INC0010023", note_type="comments")
    assert "elementINcomments%5EORDERBYDESC" in transport.urls()[1]


def test_notes_rejects_bad_type():
    with pytest.raises(ValueError, match="note type"):
        snow.get_notes("INC0010023", note_type="wrong")


def test_notes_vit_defaults_to_work_notes_only(transport):
    transport.queue_json([{"sys_id": SYS_ID}])
    transport.queue_json([])
    snow.get_notes("VIT0000042")
    assert "elementINwork_notes%5E" in transport.urls()[1]


def test_notes_403_names_the_acl_fix(transport):
    _queue_resolution(transport)
    transport.queue_error(403, "Insufficient rights")
    with pytest.raises(RuntimeError, match="read ACL"):
        snow.get_notes("INC0010023")


def test_history_query_shape_and_mapping(transport):
    _queue_resolution(transport)
    transport.queue_json(
        [
            {
                "sys_created_on": "2026-08-02 10:00:00",
                "user": "jdoe",
                "fieldname": "state",
                "oldvalue": "1",
                "newvalue": "2",
            }
        ]
    )
    rows = snow.get_history("INC0010023")
    url = transport.urls()[1]
    assert "/api/now/table/sys_audit" in url
    assert "tablename%3Dincident%5Edocumentkey%3D" + SYS_ID in url
    assert rows[0] == {
        "when": "2026-08-02T10:00:00Z",
        "by": "jdoe",
        "field": "state",
        "old": "1",
        "new": "2",
    }


def test_history_field_filter(transport):
    _queue_resolution(transport)
    transport.queue_json([])
    snow.get_history("INC0010023", field_name="state")
    assert "%5Efieldname%3Dstate%5E" in transport.urls()[1]


def test_history_403_names_sys_audit(transport):
    _queue_resolution(transport)
    transport.queue_error(403, "no")
    with pytest.raises(RuntimeError, match="sys_audit"):
        snow.get_history("INC0010023")


# -- lookups / introspection -------------------------------------------------


def test_whoami(transport):
    transport.queue_json(
        [
            {
                "sys_id": {"value": SYS_ID, "display_value": SYS_ID},
                "user_name": {"value": "unit", "display_value": "unit"},
            }
        ]
    )
    result = snow.whoami()
    assert "sysparm_query=user_name%3Dunit" in transport.urls()[0]
    assert result["instance"] == BASE


def test_whoami_invisible_user(transport):
    transport.queue_json([])
    with pytest.raises(RuntimeError, match="sys_user"):
        snow.whoami()


def test_choices_child_rows_replace_task_rows(transport):
    transport.queue_json(
        [
            {"name": "task", "value": "6", "label": "Resolved (task)"},
            {"name": "incident", "value": "6", "label": "Resolved"},
            {"name": "task", "value": "7", "label": "Closed (task)"},
        ]
    )
    rows = snow.get_choices("incident", "state")
    url = transport.urls()[0]
    assert "nameINincident%2Ctask" in url
    assert "%5Elanguage%3Den" in url
    by_value = {row["value"]: row for row in rows}
    assert by_value["6"] == {"value": "6", "label": "Resolved", "table": "incident"}
    # ServiceNow resolves choice lists child-first, wholesale: when incident
    # defines its own set, task-only values must not leak in.
    assert "7" not in by_value


def test_choices_fall_back_to_task_rows(transport):
    transport.queue_json(
        [
            {"name": "task", "value": "1", "label": "1 - Critical"},
            {"name": "task", "value": "2", "label": "2 - High"},
        ]
    )
    rows = snow.get_choices("incident", "priority")
    assert [row["value"] for row in rows] == ["1", "2"]


def test_schema_unions_task_parent_and_prefers_table(transport):
    transport.queue_json(
        [
            {"name": "task", "element": "description", "column_label": "Description",
             "internal_type": "string", "max_length": "4000", "mandatory": "false",
             "reference.name": ""},
            {"name": "incident", "element": "close_code", "column_label": "Close code",
             "internal_type": "string", "max_length": "40", "mandatory": "false",
             "reference.name": ""},
            {"name": "incident", "element": "description",
             "column_label": "Description (incident)", "internal_type": "string",
             "max_length": "4000", "mandatory": "false", "reference.name": ""},
        ]
    )
    fields = {entry["field"]: entry for entry in snow.get_schema("incident")}
    assert "nameINincident%2Ctask" in transport.urls()[0]
    assert fields["description"]["label"] == "Description (incident)"
    assert "close_code" in fields


def test_sla_query_shape(transport):
    _queue_resolution(transport)
    transport.queue_json([])
    snow.get_sla("INC0010023")
    url = transport.urls()[1]
    assert "/api/now/table/task_sla" in url
    assert "task%3D" + SYS_ID in url
    assert "sla.name" in urllib.parse.unquote(url)


def test_approvals_query_covers_both_link_styles(transport):
    _queue_resolution(transport)
    transport.queue_json([])
    snow.list_approvals("CHG0000123")
    url = transport.urls()[1]
    assert "/api/now/table/sysapproval_approver" in url
    assert "sysapproval%3D" + SYS_ID + "%5EORdocument_id%3D" + SYS_ID in url


def test_lookup_user_query_and_dedupe(transport):
    transport.queue_json(
        [{"sys_id": "b" * 32, "user_name": "jdoe"}, {"sys_id": "b" * 32, "user_name": "jdoe"}]
    )
    rows = snow.lookup_user("jdoe")
    assert len(rows) == 1
    assert "user_name%3Djdoe%5EORemail%3Djdoe%5EORnameLIKEjdoe" in transport.urls()[0]


def test_lookup_group_uses_like(transport):
    transport.queue_json([])
    snow.lookup_group("Network")
    assert "nameLIKENetwork" in transport.urls()[0]


def test_count(transport):
    transport.queue_json({"stats": {"count": "42"}})
    result = snow.count_records("incident", query="active=true")
    assert result["count"] == 42
    url = transport.urls()[0]
    assert "/api/now/stats/incident" in url and "sysparm_count=true" in url


def test_count_malformed_shape(transport):
    transport.queue_json({"weird": True})
    with pytest.raises(RuntimeError, match="Aggregate"):
        snow.count_records("incident")


def test_list_attachments(transport):
    _queue_resolution(transport)
    transport.queue_json(
        [{"sys_id": "d" * 32, "file_name": "log.txt", "size_bytes": "123",
          "content_type": "text/plain"}]
    )
    rows = snow.list_record_attachments("INC0010023")
    url = transport.urls()[1]
    assert "/api/now/attachment?" in url
    assert "table_name%3Dincident%5Etable_sys_id%3D" + SYS_ID in url
    assert rows[0]["file_name"] == "log.txt"


# -- writes ------------------------------------------------------------------


def test_create_posts_payload_and_requests_display_both(transport):
    transport.queue_json(record())
    result = snow.create_record(
        "incident", {"short_description": "VPN down", "urgency": "1"}
    )
    req = transport.requests[0]
    assert req["method"] == "POST"
    assert json.loads(req["body"]) == {"short_description": "VPN down", "urgency": "1"}
    assert "sysparm_display_value=all" in req["url"]
    assert "urgency" in urllib.parse.unquote(req["url"])  # payload keys echoed back
    assert result["number"] == "INC0010023"


def test_create_requires_fields(transport):
    with pytest.raises(ValueError, match="No fields"):
        snow.create_record("incident", {})
    assert transport.requests == []


def test_create_correlation_dedupe_returns_existing(transport):
    transport.queue_json(
        [record(correlation_id={"value": "key-1", "display_value": "key-1"})]
    )
    result = snow.create_record(
        "incident", {"short_description": "x"}, correlation_id="key-1"
    )
    assert result["deduplicated"] is True
    assert len(transport.requests) == 1  # the lookup; no POST happened
    assert "correlation_id%3Dkey-1" in transport.urls()[0]
    # correlation_id must be in the requested fields so the hit can be verified
    assert "correlation_id" in urllib.parse.unquote(transport.urls()[0])


def test_create_correlation_dedupe_ignores_unverified_hit(transport):
    # On a table without a correlation_id column the query term is invalid and
    # can match every row; an unrelated record must not be claimed as the dupe.
    transport.queue_json([record()])  # hit that does NOT carry the key
    transport.queue_json(record())  # the real create
    result = snow.create_record(
        "incident", {"short_description": "x"}, correlation_id="key-1"
    )
    assert "deduplicated" not in result
    assert transport.requests[1]["method"] == "POST"


def test_create_correlation_miss_stamps_id_into_payload(transport):
    transport.queue_json([])
    transport.queue_json(record())
    snow.create_record("incident", {"short_description": "x"}, correlation_id="key-1")
    body = json.loads(transport.requests[1]["body"])
    assert body["correlation_id"] == "key-1"


def test_create_input_display_sets_sysparm(transport):
    transport.queue_json(record())
    snow.create_record("incident", {"short_description": "x"}, input_display=True)
    assert "sysparm_input_display_value=true" in transport.urls()[0]


def test_writes_default_to_raw_values(transport):
    transport.queue_json(record())
    snow.create_record("incident", {"short_description": "x"})
    assert "sysparm_input_display_value" not in transport.urls()[0]


def test_update_input_display_sets_sysparm(transport):
    transport.queue_json(record())
    snow.update_record(SYS_ID, {"state": "In Progress"}, table="incident", input_display=True)
    assert "sysparm_input_display_value=true" in transport.urls()[-1]


def test_update_by_number_resolves_then_patches(transport):
    _queue_resolution(transport)
    transport.queue_json(record(state={"value": "6", "display_value": "Resolved"}))
    snow.update_record("INC0010023", {"state": "6"})
    req = transport.requests[1]
    assert req["method"] == "PATCH"
    assert f"/api/now/table/incident/{SYS_ID}" in req["url"]
    assert json.loads(req["body"]) == {"state": "6"}


def test_comment_patches_comments_field(transport):
    _queue_resolution(transport)
    transport.queue_json(record())
    snow.add_comment("INC0010023", "customer visible")
    assert json.loads(transport.requests[1]["body"]) == {"comments": "customer visible"}


def test_work_note_patches_work_notes_field(transport):
    _queue_resolution(transport)
    transport.queue_json(record())
    snow.add_work_note("INC0010023", "internal")
    assert json.loads(transport.requests[1]["body"]) == {"work_notes": "internal"}


def test_comment_on_vit_refused(transport):
    with pytest.raises(ValueError, match="comments"):
        snow.add_comment("VIT0000042", "hello")
    assert transport.requests == []


def test_empty_note_refused(transport):
    with pytest.raises(ValueError, match="empty"):
        snow.add_work_note("INC0010023", "   ")
    assert transport.requests == []


# -- transitions -------------------------------------------------------------


def test_resolve_incident_sends_state_and_close_fields(transport):
    _queue_resolution(transport)
    transport.queue_json(
        record(
            state={"value": "6", "display_value": "Resolved"},
            close_code={"value": "Solved (Permanently)",
                        "display_value": "Solved (Permanently)"},
            close_notes={"value": "fixed", "display_value": "fixed"},
        )
    )
    snow.resolve_record("INC0010023", code="Solved (Permanently)", notes="fixed")
    body = json.loads(transport.requests[1]["body"])
    assert body == {
        "state": "6",
        "close_code": "Solved (Permanently)",
        "close_notes": "fixed",
    }


def test_resolve_requires_code_and_notes(transport):
    with pytest.raises(ValueError, match="--code"):
        snow.resolve_record("INC0010023", notes="x")
    with pytest.raises(ValueError, match="--notes"):
        snow.resolve_record("INC0010023", code="x")
    assert transport.requests == []


def test_resolve_on_unregistered_table_points_at_update(transport):
    with pytest.raises(ValueError, match="use `update`"):
        snow.resolve_record(SYS_ID, table="u_custom")
    assert transport.requests == []


def test_close_sc_task(transport):
    transport.queue_json([{"sys_id": SYS_ID}])
    transport.queue_json(record())
    snow.close_record("SCTASK0000042")
    assert json.loads(transport.requests[1]["body"]) == {"state": "3"}


def test_close_code_on_table_without_close_code_field(transport):
    with pytest.raises(ValueError, match="close-code"):
        snow.close_record("SCTASK0000042", code="Done")
    assert transport.requests == []


def test_close_vit_points_at_choices(transport):
    with pytest.raises(ValueError, match="choices"):
        snow.close_record("VIT0000042")
    assert transport.requests == []


# -- assignment --------------------------------------------------------------


def test_assign_resolves_user_and_group_names(transport):
    transport.queue_json([{"sys_id": "b" * 32}])  # user lookup
    transport.queue_json([{"sys_id": "c" * 32}])  # group lookup
    transport.queue_json([{"sys_id": SYS_ID}])  # record number resolution
    transport.queue_json(record())  # PATCH response
    snow.assign_record("INC0010023", to="jdoe", group="Network Ops")
    body = json.loads(transport.requests[3]["body"])
    assert body == {"assigned_to": "b" * 32, "assignment_group": "c" * 32}
    assert "sysparm_query=name%3DNetwork+Ops" in transport.urls()[1]


def test_assign_ambiguous_user(transport):
    transport.queue_json([{"sys_id": "b" * 32}, {"sys_id": "c" * 32}])
    with pytest.raises(ValueError, match="multiple users"):
        snow.assign_record("INC0010023", to="j")


def test_assign_unknown_group(transport):
    transport.queue_json([])
    with pytest.raises(ValueError) as excinfo:
        snow.assign_record("INC0010023", group="Nope")
    assert excinfo.value.error_class == "not_found"


def test_assign_needs_a_target(transport):
    with pytest.raises(ValueError, match="--to"):
        snow.assign_record("INC0010023")
    assert transport.requests == []


# -- write verification ------------------------------------------------------


def test_write_verification_reports_dropped_field(transport, capsys):
    _queue_resolution(transport)
    transport.queue_json(record())  # response still shows state "2"
    result = snow.update_record("INC0010023", {"state": "6"})
    assert result["fields_not_applied"] == [
        {"field": "state", "requested": "6", "actual": "2"}
    ]
    assert "not applied" in capsys.readouterr().err


def test_write_verification_accepts_display_value_match(transport):
    _queue_resolution(transport)
    transport.queue_json(record())  # display side says "In Progress"
    result = snow.update_record("INC0010023", {"state": "In Progress"})
    assert "fields_not_applied" not in result


def test_write_verification_normalizes_booleans(transport):
    # The Table API echoes booleans as "true"/"false"; a successful boolean
    # write must not be reported as blocked.
    _queue_resolution(transport)
    transport.queue_json(record(active={"value": "false", "display_value": "false"}))
    result = snow.update_record("INC0010023", {"active": False})
    assert "fields_not_applied" not in result


def test_write_verification_normalizes_null_as_cleared(transport):
    _queue_resolution(transport)
    transport.queue_json(record(assigned_to={"value": "", "display_value": ""}))
    result = snow.update_record("INC0010023", {"assigned_to": None})
    assert "fields_not_applied" not in result


def test_journal_fields_never_flagged(transport):
    # ServiceNow accepts journal writes but never echoes them in the body.
    _queue_resolution(transport)
    transport.queue_json(record())
    result = snow.add_comment("INC0010023", "hello")
    assert "fields_not_applied" not in result


# -- attachments -------------------------------------------------------------


def test_attach_uploads_binary_with_guessed_type(transport, tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("hello")
    _queue_resolution(transport)
    transport.queue_json(
        {"sys_id": "d" * 32, "file_name": "notes.txt", "size_bytes": "5",
         "content_type": "text/plain"}
    )
    result = snow.attach_file("INC0010023", source)
    req = transport.requests[1]
    assert req["method"] == "POST"
    assert "/api/now/attachment/file" in req["url"]
    assert "table_name=incident" in req["url"] and "file_name=notes.txt" in req["url"]
    assert req["body"] == b"hello"
    assert req["headers"]["Content-type"] == "text/plain"
    assert result["file_name"] == "notes.txt"


def test_attach_missing_file(transport):
    with pytest.raises(ValueError, match="No such file"):
        snow.attach_file(SYS_ID, "does-not-exist.txt", table="incident")


def test_attach_size_cap(transport, tmp_path, env):
    env.setenv("SERVICENOW_MAX_ATTACHMENT_MB", "0.000001")  # ~1 byte
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 100)
    with pytest.raises(ValueError, match="cap"):
        snow.attach_file(SYS_ID, big, table="incident")


def test_download_writes_file(transport, tmp_path):
    transport.queue_json({"file_name": "log.txt"})
    transport.queue_raw(200, b"data", content_type="text/plain")
    result = snow.download_file("d" * 32, out_dir=tmp_path)
    saved = Path(result["saved_to"])
    assert saved.read_bytes() == b"data" and saved.name == "log.txt"
    assert "/api/now/attachment/" + "d" * 32 + "/file" in transport.urls()[1]


def test_download_never_overwrites(transport, tmp_path):
    (tmp_path / "log.txt").write_text("old")
    transport.queue_json({"file_name": "log.txt"})
    transport.queue_raw(200, b"new", content_type="text/plain")
    result = snow.download_file("d" * 32, out_dir=tmp_path)
    assert result["file_name"] == "log (1).txt"
    assert (tmp_path / "log.txt").read_text() == "old"


def test_download_validates_sys_id():
    with pytest.raises(ValueError, match="sys_id"):
        snow.download_file("not-a-sysid")


def test_download_sanitizes_traversal_file_name(transport, tmp_path):
    # file_name is remote-user-controlled; a hostile name must not escape --out.
    transport.queue_json({"file_name": "..\\..\\evil.bat"})
    transport.queue_raw(200, b"data", content_type="text/plain")
    saved = Path(snow.download_file("d" * 32, out_dir=tmp_path)["saved_to"])
    assert saved.parent == tmp_path and saved.name == "evil.bat"


def test_download_sanitizes_absolute_file_name(transport, tmp_path):
    transport.queue_json({"file_name": "C:/Windows/Temp/evil.exe"})
    transport.queue_raw(200, b"data", content_type="text/plain")
    saved = Path(snow.download_file("d" * 32, out_dir=tmp_path)["saved_to"])
    assert saved.parent == tmp_path and saved.name == "evil.exe"


def test_download_strips_windows_invalid_chars(transport, tmp_path):
    transport.queue_json({"file_name": 'we:ird*na"me?.txt'})
    transport.queue_raw(200, b"data", content_type="text/plain")
    saved = Path(snow.download_file("d" * 32, out_dir=tmp_path)["saved_to"])
    assert saved.name == "we_ird_na_me_.txt"


def test_download_falls_back_when_name_unusable(transport, tmp_path):
    transport.queue_json({"file_name": ".."})
    transport.queue_raw(200, b"data", content_type="text/plain")
    saved = Path(snow.download_file("d" * 32, out_dir=tmp_path)["saved_to"])
    assert saved.parent == tmp_path and saved.name == "d" * 32
