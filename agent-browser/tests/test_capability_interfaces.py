"""Public CLI/MCP entry points preserve capability options and permission boundaries."""
import json

import pytest

from agent_browser_core import cli, common, evidence, mcp_server, policy, session


def cli_request(monkeypatch, argv):
    sent = []
    monkeypatch.setattr(cli, "_session_for", lambda *args, **kwargs: (None, {}))
    monkeypatch.setattr(cli.core_session, "_request", lambda session, command, payload, timeout:
                        sent.append((command, payload)) or {"ok": True})
    assert cli.main(argv) == 0
    assert len(sent) == 1
    return sent[0]


@pytest.mark.parametrize("argv", [
    ["--trace", "click", "e1"],
    ["click", "e1", "--trace"],
    ["--trace", "commit", "click", "e1"],
    ["commit", "--trace", "click", "e1"],
    ["commit", "click", "e1", "--trace"],
])
def test_trace_survives_cli_and_commit_parser(monkeypatch, argv):
    command, payload = cli_request(monkeypatch, argv)
    assert command == ("commit" if "commit" in argv else "click")
    assert payload["trace"] is True
    if command == "commit":
        assert payload["operation"] == "click"


def test_cli_table_options_reach_daemon(monkeypatch):
    command, payload = cli_request(monkeypatch, [
        "table", "#results", "--next", "#next", "--max-pages", "4", "--max-rows", "80",
        "--column", "Name", "--column", "ID", "--key", "ID", "--key", "Region", "--export", "csv", "--trace",
    ])
    assert command == "table"
    assert {key: payload[key] for key in ("target", "next", "max_pages", "max_rows", "columns", "keys", "export_format", "trace")} == {
        "target": "#results", "next": "#next", "max_pages": 4, "max_rows": 80, "columns": ["Name", "ID"],
        "keys": ["ID", "Region"], "export_format": "csv", "trace": True,
    }


@pytest.mark.parametrize("argv", [["status", "--trace"], ["--trace", "start"], ["commit", "--trace", "journal", "--clear"]])
def test_trace_on_control_commands_is_rejected_before_contact(monkeypatch, capsys, argv):
    monkeypatch.setattr(cli, "_session_for", lambda *args, **kwargs: pytest.fail("must reject before contacting a daemon"))
    assert cli.main(argv) == 2
    assert json.loads(capsys.readouterr().err)["error"]["class"] == "usage"


def test_mcp_table_supports_semantic_pagination_and_export(monkeypatch):
    sent = []
    monkeypatch.setattr(mcp_server.client, "invoke", lambda command, payload, **kwargs:
                        sent.append((command, payload)) or {"ok": True})
    result = mcp_server.browser_read("table", "reader", target={"role": "table"}, next={"role": "button", "name": "Next"},
                                     max_pages=4, max_rows=80, columns=["Name"], keys=["ID"], export_format="json", trace=True)
    assert result["ok"]
    assert sent[0][0] == "table"
    assert sent[0][1]["next"] == {"role": "button", "name": "Next"}
    assert sent[0][1]["export_format"] == "json"
    assert sent[0][1]["trace"] is True


def test_mcp_rejects_table_options_for_other_reads_before_invocation(monkeypatch):
    monkeypatch.setattr(mcp_server.client, "invoke", lambda *args, **kwargs: pytest.fail("must reject before invoking"))
    result = mcp_server.browser_read("journal", "reader", next="#next")
    assert result["error"]["class"] == "usage"


@pytest.mark.parametrize("operation", ["state", "journal"])
def test_mcp_rejects_control_trace_before_invocation(monkeypatch, operation):
    monkeypatch.setattr(mcp_server.client, "invoke", lambda *args, **kwargs: pytest.fail("must reject before invoking"))
    assert mcp_server.browser_read(operation, "reader", trace=True)["error"]["class"] == "usage"
    assert mcp_server.browser_commit(operation, "writer", trace=True)["error"]["class"] == "usage"


def test_mcp_trace_must_be_a_boolean(monkeypatch):
    monkeypatch.setattr(mcp_server.client, "invoke", lambda *args, **kwargs: pytest.fail("must reject before invoking"))
    assert mcp_server.browser_click("e1", "writer", trace="yes")["error"]["class"] == "validation"


def test_mcp_new_capabilities_refuse_protocol_two_daemon_before_sending(monkeypatch):
    paths = common._paths("default")
    paths.dir.mkdir(parents=True, exist_ok=True)
    paths.lock.touch()
    monkeypatch.setattr(common, "_lock_held", lambda path: True)
    monkeypatch.setattr(session, "_read_session", lambda path: {"port": 1, "token": "synthetic", "protocol_version": 2})
    monkeypatch.setattr(session, "_request", lambda *args, **kwargs: pytest.fail("must not send unsupported options"))
    reply = mcp_server.browser_read("table", "reader", next="#next", export_format="csv", trace=True)
    assert reply["error"]["class"] == "protocol_mismatch"


def test_mcp_form_accepts_success_checks_and_trace(monkeypatch):
    sent = []
    monkeypatch.setattr(mcp_server.client, "invoke", lambda command, payload, **kwargs:
                        sent.append((command, payload)) or {"ok": True})
    result = mcp_server.browser_fill_form([], "writer", expect=["checked:e2"], trace=True)
    assert result["ok"]
    assert sent[0][1]["expect"] == ["checked:e2"] and sent[0][1]["trace"]


def test_pagination_requires_action_permission():
    assert not policy.authorize({"permission": "read"}, "table", {})
    with pytest.raises(ValueError) as exc:
        policy.authorize({"permission": "read"}, "table", {"next": "#next"})
    assert exc.value.error_class == "guarded"
    assert policy.authorize({"permission": "act"}, "table", {"next": "#next"})


def test_receipt_keeps_diagnostics_and_artifact_links(tmp_path):
    journal = evidence.Journal(tmp_path)
    journal.start("a" * 32, "writer", "click", {}, "https://example.com/")
    details = {"diagnostics": {"events": [{"kind": "console", "text": "sanitized"}], "truncated": False, "dropped": 0},
               "trace_file": "outputs/trace.zip", "export_file": "outputs/table.csv", "executed": True,
               "pages": 4, "row_count": 80, "complete": False, "truncated": True, "stop_reason": "row_limit"}
    journal.finish("a" * 32, "completed", details)
    row = journal.rows("writer", "a" * 32)[0]
    outcome = row["outcome"]
    if isinstance(outcome, str):
        outcome = json.loads(outcome)
    assert outcome == details


def test_receipt_arguments_omit_expected_values_even_before_validation(tmp_path):
    journal = evidence.Journal(tmp_path)
    journal.start("b" * 32, "writer", "click", {"expect": ["value:e5=sensitive-value", "persisted:#priority=sensitive-value"]}, "https://example.com/")
    rows = journal.rows("writer", "b" * 32)
    assert "sensitive-value" not in json.dumps(rows)


@pytest.mark.parametrize("failed", [False, True])
def test_api_receipt_records_redacted_request_timing(make_daemon, failed):
    from test_api_extension import Response, request_context
    daemon, context, page = make_daemon()
    page.url = "http://x/app"
    request_context(context, Exception("Authorization: Bearer private") if failed else Response(status=201))
    if failed:
        with pytest.raises(RuntimeError) as exc:
            daemon.dispatch("api", {"url": "/items?token=private"}, 1)
        command_id = exc.value.details["command_id"]
    else:
        result = daemon.dispatch("api", {"url": "/items?token=private"}, 1)
        command_id = result["command_id"]
    outcome = json.loads(daemon.journal.rows("manual", command_id)[0]["outcome"])
    event = outcome["diagnostics"]["events"][0]
    assert event["method"] == "GET" and event["url"] == "http://x/items"
    assert event["duration_ms"] >= 0
    assert "private" not in json.dumps(outcome)
    if failed:
        assert event["failure"] == "request_failed"
    else:
        assert event["status"] == 201
