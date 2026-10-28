"""Optional SDK validation, including a real stdio JSON-RPC handshake (no browser)."""
import asyncio
import inspect
import json
import os
import sys

import pytest

from agent_browser_core import common, mcp_server

pytest.importorskip("mcp", reason="Install the optional mcp extra to validate the SDK surface")


def test_mcp_lifecycle_annotations_describe_browser_mutation():
    tools = asyncio.run(mcp_server.make_server().list_tools())
    annotations = {tool.name: tool.annotations for tool in tools}
    for name, destructive in (
        ("browser_goto", True),
        ("browser_start", False),
        ("browser_stop", True),
        ("browser_needs_human", False),
    ):
        assert annotations[name].readOnlyHint is False, name
        assert annotations[name].destructiveHint is destructive, name
    for name in ("browser_snapshot", "browser_read", "browser_api", "browser_recipe"):
        assert annotations[name].readOnlyHint is True, name
        assert annotations[name].destructiveHint is False, name


def test_mcp_schema_and_complete_result_budget(monkeypatch):
    monkeypatch.setattr(mcp_server.client, "invoke", lambda *a, **kw: {"value": "😀\\\"" * 15000})

    async def run():
        server = mcp_server.make_server()
        tools = await server.list_tools()
        assert len(tools) == 18
        commit = next(t for t in tools if t.name == "browser_commit")
        assert "api" in commit.inputSchema["properties"]["operation"]["enum"]
        assert "state" in commit.inputSchema["properties"]["operation"]["enum"]
        handoff = next(t for t in tools if t.name == "browser_needs_human")
        assert "resolved" not in handoff.inputSchema["properties"]
        assert commit.annotations.readOnlyHint is False
        reply = await server.call_tool("browser_snapshot", {"session_id": "gate"})
        assert len(reply.model_dump_json().encode("utf-8")) + 128 <= common.STDOUT_BUDGET
        assert json.loads(reply.content[0].text) == reply.structuredContent
        assert reply.structuredContent["contract_version"] == 1
        assert reply.structuredContent["truncated"]
        output_id = reply.structuredContent["output_id"]
        chunks, offset = [], 0
        while offset is not None:
            page = await server.call_tool("browser_output", {"session_id": "gate", "output_id": output_id, "offset": offset})
            assert len(page.model_dump_json().encode("utf-8")) + 128 <= common.STDOUT_BUDGET
            chunks.append(page.structuredContent["chunk"])
            offset = page.structuredContent["next_offset"]
        assert json.loads("".join(chunks))["value"] == "😀\\\"" * 15000
        error = await server.call_tool("browser_commit", {"operation": "invalid", "session_id": "gate", "text": "x" * 50000})
        assert len(error.model_dump_json().encode()) < common.STDOUT_BUDGET
        assert error.structuredContent["error"]["class"] == "validation"
    asyncio.run(run())


def test_mcp_manual_handoff_resolution_uses_commit_dispatch(monkeypatch):
    called = []
    monkeypatch.setattr(mcp_server.client, "invoke", lambda *a, **kw: called.append((a, kw)) or {})
    mcp_server.browser_commit("state", "gate", handoff_id="handoff", resolve=True)
    args, kwargs = called[0]
    assert args[0] == "state" and args[1]["resolve"] is True
    assert kwargs["committing"] is True


def test_mcp_can_inspect_and_page_receipts_when_quota_is_full(make_daemon, monkeypatch):
    daemon, ctx, page = make_daemon()
    monkeypatch.setenv("AGENT_BROWSER_HOME", str(daemon.paths.root))
    monkeypatch.setattr(common, "_load_config", lambda: {"evidence_max_bytes": 65536})
    for index in range(25):
        command_id = f"{index:032x}"
        daemon.journal.start(command_id, "manual", "read", {}, "http://x/")
        daemon.journal.finish(command_id, "completed", {"expectations": [{"kind": "text", "passed": True}] * 2000})
    daemon.journal.quota = 65536
    monkeypatch.setattr(mcp_server.client, "invoke", lambda command, args, **kw: daemon.dispatch(command, args, 1))
    async def run():
        server = mcp_server.make_server()
        reply = await server.call_tool("browser_read", {"operation": "journal", "session_id": "manual"})
        data = reply.structuredContent
        assert not data.get("truncated") and data["next_offset"] is not None
        summary = next(row for row in data["commands"] if row.get("output_id"))
        page = await server.call_tool("browser_output", {"session_id": "manual", "output_id": summary["output_id"]})
        assert page.structuredContent["next_offset"] is not None
        assert len(page.model_dump_json().encode()) < common.STDOUT_BUDGET
    asyncio.run(run())
    assert not daemon.paths.outputs.exists()


def _tool(name, arguments):
    async def run():
        reply = await mcp_server.make_server().call_tool(name, arguments)
        assert reply.isError
        assert json.loads(reply.content[0].text) == reply.structuredContent
        assert len(reply.model_dump_json().encode("utf-8")) + 128 <= common.STDOUT_BUDGET
        return reply.structuredContent
    return asyncio.run(run())


def _raises(exc):
    def invoke(*args, **kwargs):
        raise exc
    return invoke


def test_mcp_error_reply_carries_hint_http_status_and_details(monkeypatch):
    details = {"command_id": "a" * 32, "executed": False}
    monkeypatch.setattr(mcp_server.client, "invoke", _raises(common._tag(ValueError("stale"), "stale_ref", hint="Run: snapshot", details=details)))
    data = _tool("browser_click", {"target": "e1", "session_id": "g"})
    assert data["error"] == {"class": "stale_ref", "http_status": None, "message": "stale", "hint": "Run: snapshot", "details": details}


def test_mcp_unexpected_exception_is_daemon_class_not_validation_and_leaks_nothing(monkeypatch):
    monkeypatch.setattr(mcp_server.client, "invoke", _raises(KeyError("C:/secret/path")))
    data = _tool("browser_click", {"target": "e1", "session_id": "g", "profile": "../evil"})
    assert data["error"]["class"] == "daemon" and data["error"]["details"] == {"exception": "KeyError"}
    assert data["error"]["hint"] == common._hint("inspect_receipt") and "secret" not in json.dumps(data)
    monkeypatch.setattr(mcp_server.client, "invoke", _raises(OSError("C:/secret")))
    data = _tool("browser_click", {"target": "e1", "session_id": "g"})
    assert data["error"]["class"] == "io" and "secret" not in json.dumps(data)
    monkeypatch.setattr(mcp_server.client, "invoke", _raises(common._tag(OSError("gone"), "not_running", details={"executed": False})))
    data = _tool("browser_click", {"target": "e1", "session_id": "g"})
    assert data["error"]["class"] == "not_running" and data["error"]["details"] == {"executed": False}


def test_mcp_bad_arguments_stay_validation_with_a_hint():
    for name, arguments in (("browser_commit", {"operation": "invalid", "session_id": "g", "text": "x" * 50000}),
                            ("browser_nonexistent", {"session_id": "g"})):
        error = _tool(name, arguments)["error"]
        assert error == {"class": "validation", "http_status": None, "message": "Invalid tool arguments. Inspect the typed tool schema.",
                         "hint": common._hint("tool_arguments")}


def test_mcp_unstorable_reply_keeps_receipt_pointer(monkeypatch):
    from agent_browser_core import cli
    command_id = "b" * 32
    monkeypatch.setattr(mcp_server.client, "invoke", lambda *a, **kw: {"command_id": command_id, "executed": True, "value": "x" * 30000})
    monkeypatch.setattr(cli.evidence, "require_space", _raises(common._tag(RuntimeError("full"), "journal_full")))
    error = _tool("browser_commit", {"operation": "click", "target": "e1", "session_id": "g", "profile": "work"})["error"]
    assert error["class"] == "journal_full" and error["hint"] == common._hint("inspect_receipt", "work")
    assert error["details"] == {"command_id": command_id, "executed": True, "receipt": "state --command " + command_id}


def test_mcp_reply_that_only_overflows_the_tool_budget_keeps_receipt_pointer(monkeypatch):
    from agent_browser_core import cli
    command_id = "c" * 32
    # Fits the 24,000-byte socket budget, so only the 7,000-byte tool envelope has to store it.
    monkeypatch.setattr(mcp_server.client, "invoke", lambda *a, **kw: {"command_id": command_id, "executed": True, "value": "x" * 10000})
    monkeypatch.setattr(cli.evidence, "require_space", _raises(OSError("disk")))
    error = _tool("browser_click", {"target": "e1", "session_id": "g"})["error"]
    assert error["class"] == "output_storage"
    assert error["details"] == {"command_id": command_id, "executed": True, "receipt": "state --command " + command_id}


def test_real_stdio_handshake_and_error_contract():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "agent_browser_core.mcp_server"], env=dict(os.environ))
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert len(tools.tools) == 18
                reply = await session.call_tool("browser_read", {"operation": "state", "session_id": "gate"})
                assert reply.isError
                assert reply.structuredContent["contract_version"] == 1
                assert reply.structuredContent["error"]["class"] == "not_running"
    asyncio.run(run())


def _recorder(monkeypatch):
    calls = []

    def invoke(command, arguments, **kwargs):
        calls.append((command, arguments, kwargs))
        return {"ok": True}
    monkeypatch.setattr(mcp_server.client, "invoke", invoke)
    return calls


def _cli_sent(monkeypatch, argv):
    """The command, payload and timeout the CLI sends the daemon for argv."""
    from agent_browser_core import cli
    sent = []
    monkeypatch.setattr(cli, "_session_for", lambda args, verb, autostart=False: (None, {}))
    monkeypatch.setattr(cli.core_session, "_request", lambda session, cmd, payload, timeout: sent.append((cmd, payload, timeout)) or {"ok": True})
    assert cli.main(argv) == 0
    return sent[0]


# Keys the CLI sends that MCP leaves out on purpose: the client adds the first two, MCP has its own snapshot
# budget, enter submits and stays off MCP, and MCP picks its own screenshot path.
CLI_ONLY_KEYS = {"_session_id", "_cwd", "max_bytes", "double", "right", "enter", "path"}
# Accepting a dialog or discarding unsaved edits: on MCP only browser_commit and browser_stop take them.
DIALOG_KEYS = {"accept_dialog", "dialog_text", "discard_changes"}


@pytest.mark.parametrize("argv, tool, kwargs", [
    (["goto", "https://x/", "--wait", "domcontentloaded", "--tab", "2"], "browser_goto", {"url": "https://x/", "wait": "domcontentloaded", "tab": 2}),
    (["goto", "https://x/", "--new-tab"], "browser_goto", {"url": "https://x/", "new_tab": True}),
    (["snapshot", "--ref", "e5", "--find", "Save", "--tab", "T2"], "browser_snapshot", {"ref": "e5", "find": "Save", "tab": "T2"}),
    (["click", "e1", "--tab", "t2"], "browser_click", {"target": "e1", "tab": "t2"}),
    (["fill", "e1", "hello", "--tab", "1"], "browser_fill", {"target": "e1", "text": "hello", "tab": 1}),
    (["wait", "--ref", "e3", "--selector", "#a", "--tab", "2"], "browser_wait", {"ref": "e3", "selector": "#a", "tab": 2}),
    (["eval", "el.id", "--ref", "e2", "--frame", "gsft_main"], "browser_eval", {"js": "el.id", "ref": "e2", "frame": "gsft_main"}),
    (["text", "--max-chars", "500", "--tab", "t3"], "browser_read", {"operation": "text", "max_chars": 500, "tab": "t3"}),
    (["tab", "t2", "--close"], "browser_action", {"operation": "tab", "index": "t2", "close": True}),
    (["back", "--tab", "2"], "browser_action", {"operation": "back", "tab": 2}),
    (["screenshot", "--full-page", "--tab", "2"], "browser_screenshot", {"full_page": True, "tab": 2}),
    (["api", "/items", "--tab", "2"], "browser_api", {"url": "/items", "tab": 2}),
    (["run", "sn/add-work-note", "--number", "INC0010001", "--text", "note", "--tab", "2"], "browser_recipe", {"number": "INC0010001", "text": "note", "tab": 2}),
])
def test_mcp_payload_carries_every_cli_key_with_the_cli_value(argv, tool, kwargs, monkeypatch):
    command, cli_payload, _ = _cli_sent(monkeypatch, argv)
    calls = _recorder(monkeypatch)
    assert "error" not in getattr(mcp_server, tool)(session_id="s", **kwargs)
    sent_command, payload, _ = calls[0]
    assert sent_command == command
    expected = {k: v for k, v in cli_payload.items() if k not in CLI_ONLY_KEYS | DIALOG_KEYS}
    assert {k: payload[k] for k in expected if k in payload} == expected
    assert payload["max_bytes"] == mcp_server.MAX_BYTES
    assert not DIALOG_KEYS & set(payload)


@pytest.mark.parametrize("argv, tool, kwargs", [
    (["commit", "click", "e1", "--accept-dialog", "--dialog-text", "ok", "--tab", "2"], "browser_commit",
     {"operation": "click", "target": "e1", "accept_dialog": True, "dialog_text": "ok", "tab": 2}),
    (["commit", "fill", "e1", "draft", "--discard-changes"], "browser_commit", {"operation": "fill", "target": "e1", "text": "draft", "discard_changes": True}),
    (["stop", "--discard-changes"], "browser_stop", {"discard_changes": True}),
    (["stop"], "browser_stop", {}),
])
def test_mcp_commit_and_stop_carry_the_cli_dialog_keys(argv, tool, kwargs, monkeypatch):
    from agent_browser_core import cli
    monkeypatch.setattr(cli.core_session, "_liveness", lambda paths: ("running", {"pid": 1}))
    command, cli_payload, _ = _cli_sent(monkeypatch, argv)
    calls = _recorder(monkeypatch)
    assert "error" not in getattr(mcp_server, tool)(session_id="s", **kwargs)
    sent_command, payload, sent_kwargs = calls[0]
    if command == "commit":
        command = cli_payload.pop("operation")
        assert sent_kwargs["committing"] is True
    assert sent_command == command
    expected = {k: v for k, v in cli_payload.items() if k not in CLI_ONLY_KEYS}
    assert DIALOG_KEYS & set(expected) and {k: payload.get(k) for k in expected} == expected


def test_mcp_dialog_answers_are_validated_like_the_cli(monkeypatch):
    calls = _recorder(monkeypatch)
    assert mcp_server.browser_commit("click", "s", target="e1", dialog_text="ok")["error"]["class"] == "usage"
    # The CLI's api, run, journal and state verbs have no dialog flags, so MCP refuses them there too.
    for operation, kwargs in (("api", {"url": "/items", "method": "POST"}), ("run", {"recipe": "sn/add-work-note", "number": "INC0010001", "text": "n"}),
                              ("journal", {"clear": True}), ("state", {"handoff_id": "h", "resolve": True})):
        assert mcp_server.browser_commit(operation, "s", accept_dialog=True, **kwargs)["error"]["class"] == "usage", operation
        assert mcp_server.browser_commit(operation, "s", discard_changes=True, **kwargs)["error"]["class"] == "usage", operation
    assert not calls


def test_mcp_tab_and_index_are_sent_only_when_given(monkeypatch):
    calls = _recorder(monkeypatch)
    mcp_server.browser_action("tab", "s")
    mcp_server.browser_action("back", "s")
    mcp_server.browser_click("e1", "s")
    assert all("index" not in payload and "tab" not in payload for _, payload, _ in calls)
    calls.clear()
    error = mcp_server.browser_action("tab", "s", index="t1", tab="t2")["error"]
    assert error["class"] == "usage" and not calls
    error = mcp_server.browser_click("e1", "s", tab=0)["error"]
    assert error["class"] == "validation" and error["hint"] == common._hint("tab_index") and not calls


def test_mcp_tab_operation_takes_stable_ids_and_never_sends_index_zero(make_daemon, monkeypatch):
    daemon, ctx, page = make_daemon()
    second = ctx.new_page()
    second.url = "http://x/second.html"
    daemon._track_page(second)
    monkeypatch.setattr(mcp_server.client, "invoke", lambda command, args, **kw: daemon.dispatch(command, args, 1))

    async def run():
        server = mcp_server.make_server()
        missing = (await server.call_tool("browser_action", {"operation": "tab", "session_id": "manual"})).structuredContent
        assert missing["error"]["class"] == "usage" and missing["error"]["hint"] == common._hint("tab_index")
        tab_id = daemon._tab_id(second)
        switched = (await server.call_tool("browser_action", {"operation": "tab", "session_id": "manual", "index": tab_id})).structuredContent
        assert "error" not in switched and switched["tab_id"] == tab_id and switched["url"] == "http://x/second.html"
        first = (await server.call_tool("browser_snapshot", {"session_id": "manual", "tab": 1})).structuredContent
        assert "error" not in first and first["url"] == page.url
    asyncio.run(run())


def test_mcp_schema_exposes_the_cli_parameters(monkeypatch):
    tools = {tool.name: tool.inputSchema["properties"] for tool in asyncio.run(mcp_server.make_server().list_tools())}
    assert len(tools) == 18
    page_tools = {"browser_goto", "browser_snapshot", "browser_click", "browser_fill", "browser_fill_form", "browser_action", "browser_read",
                  "browser_wait", "browser_screenshot", "browser_api", "browser_eval", "browser_commit", "browser_recipe"}
    for name in page_tools:
        assert {s.get("type") for s in tools[name]["tab"]["anyOf"]} == {"integer", "string", "null"}, name
        assert tools[name]["tab"]["default"] is None, name
    index = tools["browser_action"]["index"]
    assert {s.get("type") for s in index["anyOf"]} == {"integer", "string", "null"} and index["default"] is None
    assert {"ref", "selector"} <= set(tools["browser_wait"])
    assert "ref" in tools["browser_snapshot"] and {"ref", "frame"} <= set(tools["browser_eval"])
    assert "max_chars" in tools["browser_read"] and "content_type" in tools["browser_commit"]
    assert tools["browser_goto"]["wait"]["enum"] == ["load", "domcontentloaded"] and tools["browser_goto"]["new_tab"]["default"] is False
    # enter submits, so it stays commit-only; the snapshot budget is MCP's own.
    assert all("enter" not in props and "max_bytes" not in props for props in tools.values())
    # Accepting a dialog or discarding unsaved edits happens only through browser_commit and browser_stop.
    assert DIALOG_KEYS <= set(tools["browser_commit"]) and DIALOG_KEYS & set(tools["browser_stop"]) == {"discard_changes"}
    assert all(not DIALOG_KEYS & set(props) for name, props in tools.items() if name not in {"browser_commit", "browser_stop"})
    assert list(inspect.signature(mcp_server.browser_commit).parameters)[-4:] == ["tab", "accept_dialog", "dialog_text", "discard_changes"]
    # A JSON integer tab stays an integer and a tab id stays a string through the SDK's argument validation.
    calls = _recorder(monkeypatch)

    async def run():
        server = mcp_server.make_server()
        await server.call_tool("browser_snapshot", {"session_id": "s", "tab": 2})
        await server.call_tool("browser_snapshot", {"session_id": "s", "tab": "t2"})
    asyncio.run(run())
    assert [payload["tab"] for _, payload, _ in calls] == [2, "t2"]


@pytest.mark.parametrize("value", [-1, -0.5, float("inf"), float("-inf"), float("nan"), common.MAX_TIMEOUT + 1])
def test_mcp_timeout_out_of_range_is_validation_like_the_cli(value, monkeypatch, capsys):
    from agent_browser_core import cli
    assert cli.main(["goto", "https://x/", f"--timeout={value!r}"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["class"] == "validation"
    calls = _recorder(monkeypatch)
    for result in (mcp_server.browser_goto("https://x/", "s", timeout=value), mcp_server.browser_wait("s", text="Saved", timeout=value)):
        assert result["error"]["class"] == "validation", result
    assert not calls


def test_mcp_wait_is_capped_like_the_cli_and_cannot_block_the_stdio_server(monkeypatch, capsys):
    from agent_browser_core import cli
    assert cli.main(["wait", "--text", "Saved", "--timeout", "101"]) == 2
    cli_error = json.loads(capsys.readouterr().err)["error"]
    calls = _recorder(monkeypatch)
    error = _tool("browser_wait", {"session_id": "s", "text": "Saved", "timeout": common.MAX_TIMEOUT})["error"]
    assert error["class"] == cli_error["class"] == "validation"
    assert error["hint"] == cli_error["hint"] == common._hint("wait_cap")
    assert _tool("browser_goto", {"url": "https://x/", "session_id": "s", "timeout": -1})["error"]["class"] == "validation"
    assert not calls
    mcp_server.browser_wait("s", text="Saved", timeout=common.WAIT_CAP)
    mcp_server.browser_goto("https://x/", "s", timeout=0)
    mcp_server.browser_goto("https://x/", "s", timeout=common.MAX_TIMEOUT)
    assert [kw["timeout"] for _, _, kw in calls] == [common.WAIT_CAP, 0.0, common.MAX_TIMEOUT]


def _servicenow_like_page():
    """A classic ServiceNow incident form: about 30 KB of snapshot, a related list, and the form in the gsft_main iframe."""
    rows = ["- generic [active] [ref=e1]:", "  - banner [ref=e2]:", '    - link "ServiceNow Home Page" [ref=e3] [cursor=pointer]:',
            "      - /url: /now/nav/ui/home", '    - button "All" [ref=e4]', '    - button "Favorites" [ref=e5]', '    - searchbox "Search" [ref=e6]',
            "  - iframe [ref=e7]:", "    - generic [active] [ref=f1e1]:", '      - heading "Incident INC0010001" [level=2] [ref=f1e2]']
    ref = 2
    for section in ("Caller", "Category", "Subcategory", "Service", "Configuration item", "Short description", "Description", "Assignment group"):
        for index in range(12):
            ref += 1
            rows.append(f'      - button "{section} lookup {index}: open the \\"{section}\\" reference picker" [ref=f1e{ref}]')
    rows.append('      - table "Related incidents" [ref=f1e900]:')
    for index in range(250):
        rows.append(f'        - row "INC00{index:05d} Email outage in building {index} New 3 - Moderate" [ref=f1e{1000 + index}]')
    text = "\n".join(rows) + "\n"
    assert 25000 < len(text.encode("utf-8")) < 40000
    return text


def test_mcp_page_read_fits_the_reply_budget_and_keeps_url_and_title(make_daemon, monkeypatch):
    url = "https://dev12345.service-now.com/nav_to.do?uri=%2Fincident.do%3Fsys_id%3D9d385017c611228701d22104cc95c371%26sysparm_view%3Dess"
    title = "INC0010001 | Incident | ServiceNow - Email outage reported by several users in building 12 after the weekend change"
    daemon, ctx, page = make_daemon(snapshot_text=_servicenow_like_page(), page_url=url, page_title=title)
    monkeypatch.setattr(mcp_server.client, "invoke", lambda command, args, **kw: daemon.dispatch(command, args, 1))

    async def run():
        server = mcp_server.make_server()
        for name, arguments in (("browser_goto", {"url": url}), ("browser_snapshot", {}), ("browser_action", {"operation": "reload"}),
                                ("browser_wait", {"url_contains": "incident.do"})):
            reply = await server.call_tool(name, {"session_id": "manual", **arguments})
            data = reply.structuredContent
            assert "error" not in data and "output_id" not in data, (name, data)
            assert data["url"] == common.redact_url(url) and data["title"] == title, name
            assert data["snapshot"] and data["truncated"] is True and data["untrusted"] == common.UNTRUSTED_KEYS, name
            assert len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1 <= mcp_server.REPLY_BUDGET, name
            assert len(reply.model_dump_json().encode("utf-8")) + 128 <= common.STDOUT_BUDGET, name
    asyncio.run(run())


def test_overflow_stub_keeps_url_title_and_their_untrusted_marker(monkeypatch):
    big = {"ok": True, "url": "https://x/app", "title": "Record 7", "untrusted": common.UNTRUSTED_KEYS, "snapshot": ["- row [ref=e1]"] * 4000}
    monkeypatch.setattr(mcp_server.client, "invoke", lambda *a, **kw: big)

    async def run():
        return (await mcp_server.make_server().call_tool("browser_snapshot", {"session_id": "s"})).structuredContent
    data = asyncio.run(run())
    assert data["truncated"] and data["output_id"]
    assert data["url"] == "https://x/app" and data["title"] == "Record 7" and data["untrusted"] == common.UNTRUSTED_KEYS
