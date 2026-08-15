"""CLI end-to-end through main(): stdout JSON, stderr error envelopes, exit codes."""

from __future__ import annotations

import json
import urllib.parse

import servicenow_client as snow
from conftest import SYS_ID, record


def test_get_success_prints_json_and_exits_zero(transport, capsys):
    transport.queue_json([record()])
    assert snow.main(["get", "INC0010023"]) == 0
    out, err = capsys.readouterr()
    parsed = json.loads(out)
    assert parsed["number"] == "INC0010023"
    assert err == ""


def test_value_error_exits_2_with_error_envelope(transport, capsys):
    assert snow.main(["get", "XYZ0001"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    payload = json.loads(err)
    assert payload["error"]["class"] == "usage"
    assert "Known number prefixes" in payload["error"]["message"]


def test_runtime_error_exits_1_with_class_and_status(transport, capsys):
    transport.queue_error(401, "User Not Authenticated")
    assert snow.main(["get", "INC0010023"]) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["class"] == "auth"
    assert payload["error"]["http_status"] == 401


def test_field_overrides_data(transport, capsys):
    assert (
        snow.main(
            [
                "update", SYS_ID, "--table", "incident",
                "--data", '{"state": "2", "priority": "3"}',
                "--field", "state=6", "--dry-run",
            ]
        )
        == 0
    )
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["payload"] == {"state": "6", "priority": "3"}
    assert transport.requests == []  # sys_id given, dry-run: nothing sent


def test_bad_data_json_exits_2(transport, capsys):
    assert snow.main(["create", "incident", "--data", "{oops"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["class"] == "usage"


def test_tables_needs_no_config(monkeypatch, capsys):
    # `tables` must work with zero env - it is pure registry data.
    monkeypatch.setattr(snow, "load_dotenv", lambda *args, **kwargs: False)
    for name in ("SERVICENOW_INSTANCE", "SERVICENOW_USERNAME", "SERVICENOW_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    snow._client.cache_clear()
    assert snow.main(["tables"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert "incident" in parsed and "sn_vul_vulnerable_item" in parsed


def test_missing_config_reports_config_class(monkeypatch, capsys):
    monkeypatch.setattr(snow, "load_dotenv", lambda *args, **kwargs: False)
    for name in ("SERVICENOW_INSTANCE", "SERVICENOW_USERNAME", "SERVICENOW_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    snow._client.cache_clear()
    assert snow.main(["whoami"]) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["class"] == "config"
    assert "SERVICENOW_INSTANCE" in payload["error"]["message"]
    snow._client.cache_clear()


def test_output_table_renders_columns(transport, capsys):
    transport.queue_json([record()], headers={"x-total-count": "1"})
    assert snow.main(["query", "incident", "--output", "table"]) == 0
    out = capsys.readouterr().out
    assert "number" in out and "INC0010023" in out
    assert not out.lstrip().startswith("{")


def test_list_alias_works(transport, capsys):
    transport.queue_json([])
    assert snow.main(["list", "incident"]) == 0


def test_long_fields_truncated_by_default_flag(transport, capsys):
    transport.queue_json(
        [record(description={"value": "y" * 50, "display_value": "y" * 50})]
    )
    assert snow.main(["get", "INC0010023", "--max-field-chars", "10"]) == 0
    assert "[truncated 40 chars]" in capsys.readouterr().out


def test_truncation_disabled_with_zero(transport, capsys):
    transport.queue_json(
        [record(description={"value": "y" * 50, "display_value": "y" * 50})]
    )
    assert snow.main(["get", "INC0010023", "--max-field-chars", "0"]) == 0
    assert "truncated" not in capsys.readouterr().out


def test_comment_flow_through_cli(transport, capsys):
    transport.queue_json([{"sys_id": SYS_ID}])
    transport.queue_json(record())
    assert snow.main(["comment", "INC0010023", "customer visible"]) == 0
    assert json.loads(transport.requests[1]["body"]) == {
        "comments": "customer visible"
    }


def test_read_only_guard_maps_to_exit_1_guarded(transport, env, capsys):
    env.setenv("SERVICENOW_READ_ONLY", "true")
    assert snow.main(["work-note", "INC0010023", "x"]) == 1
    assert json.loads(capsys.readouterr().err)["error"]["class"] == "guarded"


def test_delete_without_force_exits_2(transport, capsys):
    assert snow.main(["delete", SYS_ID, "--table", "incident"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["class"] == "guarded"


def test_query_canned_flags_compose(transport, capsys):
    transport.queue_json([])
    assert (
        snow.main(
            ["query", "incident", "--active", "--group", "Network Ops",
             "--opened-last", "7d", "--limit", "10"]
        )
        == 0
    )
    url = transport.urls()[0]
    encoded = url.split("sysparm_query=")[1].split("&")[0]
    q = urllib.parse.unquote_plus(encoded)
    assert q.startswith("active=true^assignment_group.name=Network Ops^opened_at>=")
    assert "sysparm_limit=10" in url


def test_input_display_flag_reaches_the_wire(transport, capsys):
    transport.queue_json(record())
    assert (
        snow.main(
            ["create", "incident", "--field", "short_description=x", "--input-display"]
        )
        == 0
    )
    assert "sysparm_input_display_value=true" in transport.urls()[0]


def test_dry_run_resolve_previews_payload(transport, capsys):
    transport.queue_json([{"sys_id": SYS_ID}])
    assert (
        snow.main(
            ["resolve", "INC0010023", "--code", "Solved", "--notes", "done", "--dry-run"]
        )
        == 0
    )
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["dry_run"] is True
    assert parsed["payload"] == {
        "state": "6", "close_code": "Solved", "close_notes": "done"
    }
