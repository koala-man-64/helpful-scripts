"""Optional SDK validation, including a real stdio JSON-RPC handshake (no browser)."""
import asyncio
import json
import os
import sys

import pytest

from agent_browser_core import common, mcp_server

pytest.importorskip("mcp", reason="Install the optional mcp extra to validate the SDK surface")


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
