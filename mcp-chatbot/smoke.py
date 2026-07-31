#!/usr/bin/env python3
"""MCP smoke test: spawn the server over stdio and verify its tool list.

Runs with all FOUNDRY_* configuration stripped from the environment to prove
the server boots with zero configuration (env vars are only read lazily at
first use). Exits 0 on success.

Usage: python smoke.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {"chat", "list_conversations", "get_conversation", "delete_conversation"}


async def check() -> int:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("FOUNDRY_") and k != "MCP_CHATBOT_DATA_DIR"
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_chatbot.server"], env=env
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
    missing = EXPECTED_TOOLS - tools
    if missing:
        print(f"SMOKE FAIL: missing tools {sorted(missing)}; got {sorted(tools)}", file=sys.stderr)
        return 1
    print(f"SMOKE OK: {len(EXPECTED_TOOLS)} tools")
    return 0


def main() -> int:
    return asyncio.run(check())


if __name__ == "__main__":
    raise SystemExit(main())
