"""MCP transport regressions with the real SDK and an offline daemon seam."""
import asyncio
import json
import threading

import pytest

from agent_browser_core import common, mcp_server

pytest.importorskip("mcp", reason="Install the optional mcp extra to validate the SDK surface")


@pytest.mark.parametrize(("value", "max_chars"), [
    ("x" * 10000, None),
    ('\\"\n' * 6000, None),
    ("😀" * 20000, None),
    ("x" * 35000, 0),
], ids=["ascii", "escaped", "unicode", "unlimited"])
def test_mcp_text_reply_budget_preserves_complete_overflow(monkeypatch, value, max_chars):
    def invoke(command, arguments, **kwargs):
        assert command == "text" and arguments["max_chars"] == max_chars
        return {"text": value, "chars": len(value), "truncated": False, "untrusted": ["text"]}

    monkeypatch.setattr(mcp_server.client, "invoke", invoke)

    def checked(reply):
        compact = json.dumps(reply.structuredContent, ensure_ascii=False, separators=(",", ":"))
        assert reply.content[0].text == compact
        assert len(compact.encode("utf-8")) + 1 <= mcp_server.REPLY_BUDGET
        assert len(reply.model_dump_json().encode("utf-8")) + 128 <= common.STDOUT_BUDGET
        assert not reply.isError
        return reply.structuredContent

    async def run():
        server = mcp_server.make_server()
        data = checked(await server.call_tool("browser_read", {"operation": "text", "session_id": "text-budget", "max_chars": max_chars}))
        assert data["truncated"] and "text" not in data
        parts, offset = [], 0
        while offset is not None:
            chunk = checked(await server.call_tool("browser_output", {
                "session_id": "text-budget", "output_id": data["output_id"], "offset": offset,
            }))
            parts.append(chunk["chunk"])
            offset = chunk["next_offset"]
        stored = json.loads("".join(parts))
        assert stored["text"] == value and stored["chars"] == len(value)
        assert stored["untrusted"] == ["text"]

    asyncio.run(run())


def test_mcp_pending_wait_allows_another_session_to_read(monkeypatch):
    started, release = threading.Event(), threading.Event()
    order = []
    monkeypatch.setattr(mcp_server.client.session, "_require_running", lambda *args: {})

    def request(state, command, arguments, timeout):
        if command == "wait":
            started.set()
            return {"pending": True, "status": "pending", "command_id": "a" * 32}
        if command == "poll":
            assert release.wait(3), "another MCP session could not run while this wait was pending"
            order.append("wait_completed")
            return {"pending": False, "status": "completed", "command_id": "a" * 32}
        assert command == "snapshot" and arguments["_session_id"] == "reader"
        order.append("snapshot")
        release.set()
        return {"ok": True}

    monkeypatch.setattr(mcp_server.client.session, "_request", request)

    async def run():
        server = mcp_server.make_server()
        waiting = asyncio.create_task(server.call_tool("browser_wait", {"session_id": "waiter", "text": "Ready"}))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            reading = await server.call_tool("browser_snapshot", {"session_id": "reader"})
            result = await waiting
            assert not reading.isError and not result.isError
            assert order == ["snapshot", "wait_completed"]
        finally:
            release.set()
            await waiting

    asyncio.run(run())


def test_cancelled_mcp_write_keeps_one_operation_and_readable_receipt(monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    command_id, writes = "b" * 32, []
    receipt = {"command_id": command_id, "status": "pending", "executed": True}
    monkeypatch.setattr(mcp_server.client.session, "_require_running", lambda *args: {})

    def request(state, command, arguments, timeout):
        if command == "commit":
            writes.append(arguments["operation"])
            started.set()
            return {**receipt, "pending": True}
        if command == "poll":
            assert release.wait(3), "MCP cancellation did not return while the write was pending"
            receipt["status"] = "completed"
            finished.set()
            return {**receipt, "pending": False}
        assert command == "state" and arguments["command_id"] == command_id
        return dict(receipt)

    monkeypatch.setattr(mcp_server.client.session, "_request", request)

    async def run():
        server = mcp_server.make_server()
        writing = asyncio.create_task(server.call_tool("browser_commit", {
            "operation": "click", "target": "e1", "session_id": "writer",
        }))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            writing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await writing
            state = await server.call_tool("browser_read", {"operation": "state", "session_id": "writer", "command_id": command_id})
            assert state.structuredContent == {**receipt, "contract_version": common.CONTRACT_VERSION}
            assert state.structuredContent["status"] == "pending"
            release.set()
            assert await asyncio.to_thread(finished.wait, 3)
            state = await server.call_tool("browser_read", {"operation": "state", "session_id": "writer", "command_id": command_id})
            assert state.structuredContent["status"] == "completed"
            assert state.structuredContent["command_id"] == command_id
            assert writes == ["click"]
        finally:
            release.set()

    asyncio.run(run())
