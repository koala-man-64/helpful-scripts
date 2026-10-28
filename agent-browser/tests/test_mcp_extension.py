"""Optional SDK validation, including a real stdio JSON-RPC handshake (no browser)."""
import asyncio
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
