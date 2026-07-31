#!/usr/bin/env python3
"""MCP server exposing a chatbot backed by Azure AI Foundry.

Tools:
  chat(...)                 - single-shot or persistent-conversation chat
  list_conversations()      - stored conversation summaries
  get_conversation(name)    - full stored record
  delete_conversation(name) - remove the local record

Modes per call: "responses" (default, Responses API), "chat" (Chat
Completions), "agents" (Foundry agents via azure-ai-projects). Responses and
chat authenticate with FOUNDRY_API_KEY; agents mode uses Entra ID
(DefaultAzureCredential, e.g. ``az login``).

Expected .env / environment keys (none read until first needed, so the server
starts with zero configuration):
  FOUNDRY_OPENAI_BASE_URL=https://your-resource.services.ai.azure.com/openai/v1/
  FOUNDRY_API_KEY=...
  FOUNDRY_PROJECT_ENDPOINT=https://your-resource.services.ai.azure.com/api/projects/your-project
  FOUNDRY_DEFAULT_DEPLOYMENT=optional-deployment-name
  FOUNDRY_TIMEOUT_SECONDS=optional
  MCP_CHATBOT_DATA_DIR=optional
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from openai import APIStatusError, OpenAI

from mcp_chatbot import attachments as attach
from mcp_chatbot import store

MODES = ("responses", "chat", "agents")

mcp = FastMCP("mcp-chatbot")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@lru_cache(maxsize=None)
def _openai_client() -> OpenAI:
    kwargs: dict[str, Any] = {
        "base_url": require_env("FOUNDRY_OPENAI_BASE_URL"),
        "api_key": require_env("FOUNDRY_API_KEY"),
    }
    timeout = os.getenv("FOUNDRY_TIMEOUT_SECONDS")
    if timeout:
        kwargs["timeout"] = float(timeout)
    return OpenAI(**kwargs)


@lru_cache(maxsize=None)
def _agents_clients() -> tuple[AIProjectClient, Any]:
    project = AIProjectClient(
        endpoint=require_env("FOUNDRY_PROJECT_ENDPOINT"),
        credential=DefaultAzureCredential(),
    )
    return project, project.get_openai_client()


def _azure_error(exc: APIStatusError) -> RuntimeError:
    return RuntimeError(f"Azure request failed ({exc.status_code}): {exc}")


def _replay_content(message: dict[str, Any], shape: str) -> str | list[dict[str, Any]]:
    """Rebuild API content for a stored message, re-encoding images from disk."""
    images = [a["path"] for a in message.get("attachments", []) if a["kind"] == "image"]
    return attach.assemble_content(message["content"], images, shape)


def _history_as_input(record: dict[str, Any], shape: str) -> list[dict[str, Any]]:
    return [
        {"role": m["role"], "content": _replay_content(m, shape)}
        for m in record["messages"]
    ]


def _check_reply(reply: str | None, detail: str | None) -> str:
    if not reply:
        raise RuntimeError(
            "Model returned an empty reply"
            + (f" ({detail})" if detail else "")
            + ". A reasoning model may have spent the whole token budget on hidden "
            "reasoning; raise max_output_tokens or lower reasoning_effort."
        )
    return reply


def _run_responses(
    record: dict[str, Any] | None,
    prompt: str,
    model: str,
    system: str | None,
    reasoning_effort: str | None,
    temperature: float | None,
    max_output_tokens: int | None,
    attachment_paths: list[str] | None,
) -> tuple[str, str, list[dict[str, str]]]:
    client = _openai_client()
    content, text, meta = attach.build_user_content(prompt, attachment_paths, "responses")
    kwargs: dict[str, Any] = {"model": model, "input": [{"role": "user", "content": content}]}
    if system:
        # Instructions are not inherited across previous_response_id; send every turn.
        kwargs["instructions"] = system
    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_output_tokens is not None:
        kwargs["max_output_tokens"] = max_output_tokens
    if record and record.get("last_response_id"):
        kwargs["previous_response_id"] = record["last_response_id"]
    try:
        response = client.responses.create(**kwargs)
    except APIStatusError as exc:
        if "previous_response_id" in kwargs and exc.status_code == 404:
            # Stored response expired (30-day retention) or is otherwise gone:
            # rebuild the whole conversation from the local transcript.
            kwargs.pop("previous_response_id")
            kwargs["input"] = _history_as_input(record, "responses") + kwargs["input"]
            try:
                response = client.responses.create(**kwargs)
            except APIStatusError as retry_exc:
                raise _azure_error(retry_exc) from retry_exc
        else:
            raise _azure_error(exc) from exc
    detail = getattr(getattr(response, "incomplete_details", None), "reason", None)
    reply = _check_reply(getattr(response, "output_text", None), detail)
    if record is not None:
        record["last_response_id"] = response.id
    return reply, text, meta


def _run_chat(
    record: dict[str, Any] | None,
    prompt: str,
    model: str,
    system: str | None,
    reasoning_effort: str | None,
    temperature: float | None,
    max_output_tokens: int | None,
    attachment_paths: list[str] | None,
) -> tuple[str, str, list[dict[str, str]]]:
    client = _openai_client()
    content, text, meta = attach.build_user_content(prompt, attachment_paths, "chat")
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    if record:
        messages.extend(_history_as_input(record, "chat"))
    messages.append({"role": "user", "content": content})
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_output_tokens is not None:
        # max_completion_tokens works for both reasoning and non-reasoning
        # deployments; max_tokens is rejected by reasoning models.
        kwargs["max_completion_tokens"] = max_output_tokens
    try:
        completion = client.chat.completions.create(**kwargs)
    except APIStatusError as exc:
        raise _azure_error(exc) from exc
    choice = completion.choices[0]
    reply = _check_reply(
        choice.message.content, f"finish_reason={getattr(choice, 'finish_reason', None)}"
    )
    return reply, text, meta


def _agent_name(conversation: str | None) -> str:
    """Derive a Foundry-legal agent name (alnum ends, hyphens inside, <=63 chars).

    Conversation names allow '.', '_' and mixed case, which Foundry agent names
    do not; a short hash keeps distinct conversations from colliding after
    sanitization (colliding names would silently share one remote agent).
    """
    if conversation is None:
        return "mcp-chatbot-oneshot"
    digest = hashlib.sha1(conversation.encode("utf-8")).hexdigest()[:8]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", conversation).strip("-").lower() or "conv"
    return f"mcp-chatbot-{slug[:40].rstrip('-')}-{digest}"


def _run_agents(
    record: dict[str, Any] | None,
    conversation: str | None,
    prompt: str,
    model: str,
    system: str | None,
    attachment_paths: list[str] | None,
) -> tuple[str, str, list[dict[str, str]]]:
    project, agents_openai = _agents_clients()
    content, text, meta = attach.build_user_content(prompt, attachment_paths, "responses")
    if record is None or not record.get("agent_name"):
        agent_name = _agent_name(conversation)
        project.agents.create_version(
            agent_name=agent_name,
            definition=PromptAgentDefinition(model=model, instructions=system),
        )
        if record is not None:
            remote = agents_openai.conversations.create()
            record["agent_name"] = agent_name
            record["remote_conversation_id"] = remote.id
    else:
        agent_name = record["agent_name"]
    kwargs: dict[str, Any] = {
        "input": [{"role": "user", "content": content}],
        "extra_body": {"agent_reference": {"name": agent_name, "type": "agent_reference"}},
    }
    if record is not None:
        kwargs["conversation"] = record["remote_conversation_id"]
    try:
        response = agents_openai.responses.create(**kwargs)
    except APIStatusError as exc:
        raise _azure_error(exc) from exc
    reply = _check_reply(getattr(response, "output_text", None), None)
    return reply, text, meta


@mcp.tool()
def chat(
    prompt: str,
    conversation: str | None = None,
    mode: Literal["responses", "chat", "agents"] = "responses",
    model: str | None = None,
    system: str | None = None,
    reasoning_effort: str | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    attachments: list[str] | None = None,
) -> dict[str, Any]:
    """Send a prompt to an Azure AI Foundry model deployment.

    Omit `conversation` for a single-shot question (nothing persisted); pass a
    name to start or continue a persistent conversation. `mode` picks the API:
    "responses" (default), "chat" (Chat Completions), or "agents" (Foundry
    agents; requires Entra ID auth). `model` is the deployment name (falls back
    to FOUNDRY_DEFAULT_DEPLOYMENT). `attachments` are local file paths: UTF-8
    text files are inlined into the prompt, png/jpg/jpeg/gif/webp images are
    sent as vision input. `reasoning_effort` (e.g. low/medium/high) applies to
    reasoning deployments only and is passed through verbatim.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}; expected one of {MODES}.")
    resolved_model = model or os.getenv("FOUNDRY_DEFAULT_DEPLOYMENT")
    if not resolved_model:
        raise RuntimeError(
            "No model given and FOUNDRY_DEFAULT_DEPLOYMENT is not set; pass `model` "
            "or set the environment variable to a deployment name."
        )
    if mode == "agents" and any(
        v is not None for v in (reasoning_effort, temperature, max_output_tokens)
    ):
        raise ValueError(
            "agents mode does not accept reasoning_effort, temperature, or "
            "max_output_tokens; the agent definition is the source of truth."
        )

    record: dict[str, Any] | None = None
    if conversation is not None:
        store.validate_name(conversation)
        record = store.load(conversation)
        if record is None:
            record = store.new_record(conversation, mode, resolved_model, system)
        else:
            if record["mode"] != mode:
                raise ValueError(
                    f"Conversation {conversation!r} uses mode {record['mode']!r}; "
                    f"cannot switch to {mode!r}."
                )
            if system is not None and system != record["system"]:
                raise ValueError(
                    f"Conversation {conversation!r} was created with a different system "
                    "prompt; start a new conversation to change it."
                )
            if mode == "agents" and model is not None and model != record["model"]:
                raise ValueError(
                    f"Conversation {conversation!r} uses agent model {record['model']!r}, "
                    "which is fixed at agent creation; start a new conversation to change it."
                )
            if mode == "agents" and record.get("agent_name"):
                # The agent's model is fixed at creation; keep reporting/storing
                # it rather than letting a changed env default drift the record.
                resolved_model = record["model"]
    effective_system = record["system"] if record is not None else system

    if mode == "responses":
        reply, text, meta = _run_responses(
            record, prompt, resolved_model, effective_system, reasoning_effort,
            temperature, max_output_tokens, attachments,
        )
    elif mode == "chat":
        reply, text, meta = _run_chat(
            record, prompt, resolved_model, effective_system, reasoning_effort,
            temperature, max_output_tokens, attachments,
        )
    else:
        reply, text, meta = _run_agents(
            record, conversation, prompt, resolved_model, effective_system, attachments,
        )

    if record is not None:
        user_message: dict[str, Any] = {"role": "user", "content": text}
        if meta:
            user_message["attachments"] = meta
        record["messages"].append(user_message)
        record["messages"].append({"role": "assistant", "content": reply})
        record["model"] = resolved_model
        store.save(record)

    return {
        "reply": reply,
        "conversation": conversation,
        "mode": mode,
        "model": resolved_model,
    }


@mcp.tool()
def list_conversations() -> list[dict[str, Any]]:
    """List saved conversations with name, mode, model, message count, and last update."""
    return store.list_all()


@mcp.tool()
def get_conversation(name: str) -> dict[str, Any]:
    """Return the full stored record of a saved conversation."""
    record = store.load(name)
    if record is None:
        raise ValueError(f"No conversation named {name!r}.")
    return record


@mcp.tool()
def delete_conversation(name: str) -> dict[str, Any]:
    """Delete a saved conversation from local storage."""
    record = store.delete(name)
    result: dict[str, Any] = {"deleted": name}
    if record is not None and record["mode"] == "agents" and record.get("agent_name"):
        result["note"] = (
            f"Remote agent {record['agent_name']!r} and its conversation were not "
            "deleted; remove them in the Foundry portal if desired."
        )
    return result


def main() -> int:
    # stdout carries the MCP JSON-RPC stream; all logging must go to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    # MCP clients launch servers from an arbitrary cwd, so load the repo-root
    # .env by explicit path; client-provided env vars win (override=False).
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
