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

FULL_EXPECTED_TOOLS = {
    "chat",
    "list_models",
    "list_conversations",
    "get_conversation",
    "delete_conversation",
    "upload_documents",
    "search_documents",
    "list_collections",
    "get_collection",
    "delete_document",
    "delete_collection",
}
BENCH_EXPECTED_TOOLS = {"chat", "list_models"}


async def _listed_tools(module: str) -> set[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("FOUNDRY_") and k != "MCP_CHATBOT_DATA_DIR"
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", module], env=env
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return {tool.name for tool in (await session.list_tools()).tools}


async def check() -> int:
    surfaces = {
        "full": ("mcp_chatbot.server", FULL_EXPECTED_TOOLS),
        "bench": ("mcp_chatbot.bench", BENCH_EXPECTED_TOOLS),
    }
    for name, (module, expected) in surfaces.items():
        tools = await _listed_tools(module)
        if tools != expected:
            print(
                f"SMOKE FAIL ({name}): expected {sorted(expected)}; got {sorted(tools)}",
                file=sys.stderr,
            )
            return 1
    print(
        f"SMOKE OK: full={len(FULL_EXPECTED_TOOLS)} tools, "
        f"bench={len(BENCH_EXPECTED_TOOLS)} tools"
    )
    return 0


def main() -> int:
    return asyncio.run(check())


if __name__ == "__main__":
    raise SystemExit(main())
