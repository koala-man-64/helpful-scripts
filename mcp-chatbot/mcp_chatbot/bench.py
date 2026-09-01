#!/usr/bin/env python3
"""Least-privilege MCP surface for explicit Foundry model consultation.

Unlike the full mcp-chatbot server, this entry point is stateless and exposes
no local-file, document-store, conversation, deletion, or Foundry-agent tools.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from mcp_chatbot import server

DEFAULT_MAX_OUTPUT_TOKENS = 4_096
MAX_OUTPUT_TOKENS = 16_384
MAX_PROMPT_CHARS = 100_000
MAX_SYSTEM_CHARS = 20_000

mcp = FastMCP("foundry-model-bench")


@mcp.tool()
def list_models() -> dict[str, list[dict[str, Any]]]:
    """List only deployment IDs authorized by the configured allowlist."""
    return server.list_models()


@mcp.tool()
def chat(
    prompt: str,
    model: str | None = None,
    mode: Literal["responses", "chat"] = "responses",
    system: str | None = None,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """Ask one bounded, stateless question of an allowlisted deployment.

    This bench intentionally has no conversation persistence, attachments,
    retrieval collections, local-file access, mutation tools, or agents mode.
    """
    if not prompt or len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(
            f"prompt must contain 1 through {MAX_PROMPT_CHARS} characters."
        )
    if system is not None and len(system) > MAX_SYSTEM_CHARS:
        raise ValueError(f"system must not exceed {MAX_SYSTEM_CHARS} characters.")
    if mode not in {"responses", "chat"}:
        raise ValueError("mode must be 'responses' or 'chat'.")
    if not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
        raise ValueError(
            f"max_output_tokens must be between 1 and {MAX_OUTPUT_TOKENS}."
        )

    return server.chat(
        prompt=prompt,
        model=model,
        mode=mode,
        system=system,
        reasoning_effort=reasoning_effort,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
