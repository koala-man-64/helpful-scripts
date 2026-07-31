#!/usr/bin/env python3
"""MCP server exposing a chatbot backed by Azure AI Foundry.

Tools:
  chat(...)                 - single-shot or persistent-conversation chat;
                              pass `collection` for retrieval-augmented answers
  list_conversations()      - stored conversation summaries
  get_conversation(name)    - full stored record
  delete_conversation(name) - remove the local record
  upload_documents(...)     - chunk + embed local files into a named collection
  search_documents(...)     - cosine top-k over a collection
  list_collections()        - stored collection summaries
  get_collection(name)      - collection metadata (chunk texts omitted)
  delete_document(...)      - remove one document from a collection
  delete_collection(name)   - remove a whole collection

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
  FOUNDRY_EMBEDDING_DEPLOYMENT=embedding-deployment-name (document tools)
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
from mcp_chatbot import docstore, documents, store

MODES = ("responses", "chat", "agents")
EMBED_BATCH_SIZE = 64
DEFAULT_TOP_K = 5

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


def _resolved_embedding_model(model: str | None) -> str:
    resolved = model or os.getenv("FOUNDRY_EMBEDDING_DEPLOYMENT")
    if not resolved:
        raise RuntimeError(
            "No embedding model given and FOUNDRY_EMBEDDING_DEPLOYMENT is not set; "
            "pass `embedding_model` or set the environment variable to an embedding "
            "deployment name."
        )
    return resolved


def _embed_texts(texts: list[str], model: str) -> list[list[float]]:
    """Embed texts in bounded batches; vectors come back in input order."""
    client = _openai_client()
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        try:
            response = client.embeddings.create(model=model, input=batch)
        except APIStatusError as exc:
            raise _azure_error(exc) from exc
        vectors.extend(d.embedding for d in sorted(response.data, key=lambda d: d.index))
    return vectors


def _retrieve(
    collection: str, query: str, top_k: int, min_score: float | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load a collection and return (record, best-matching chunks) for a query.

    The query is always embedded with the collection's pinned deployment - an
    env-default change after creation must never mix vector spaces.
    """
    loaded = docstore.load(collection)
    if loaded is None:
        raise ValueError(f"No collection named {collection!r}; use upload_documents first.")
    record, vectors = loaded
    query_vector = _embed_texts([query], record["embedding_model"])[0]
    return record, docstore.top_k(record, vectors, query_vector, top_k, min_score)


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
    collection: str | None = None,
) -> dict[str, Any]:
    """Send a prompt to an Azure AI Foundry model deployment.

    Omit `conversation` for a single-shot question (nothing persisted); pass a
    name to start or continue a persistent conversation. `mode` picks the API:
    "responses" (default), "chat" (Chat Completions), or "agents" (Foundry
    agents; requires Entra ID auth). `model` is the deployment name (falls back
    to FOUNDRY_DEFAULT_DEPLOYMENT). `attachments` are local file paths: UTF-8
    text files are inlined into the prompt, png/jpg/jpeg/gif/webp images are
    sent as vision input. `reasoning_effort` (e.g. low/medium/high) applies to
    reasoning deployments only and is passed through verbatim. Pass
    `collection` to retrieve the most relevant stored document chunks (see
    upload_documents) and inline them into the prompt.
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

    retrieved: list[dict[str, Any]] | None = None
    if collection is not None:
        _, hits = _retrieve(collection, prompt, DEFAULT_TOP_K)
        retrieved = [
            {"document": h["document"], "chunk_index": h["chunk_index"], "score": h["score"]}
            for h in hits
        ]
        if hits:
            # Augmenting the prompt text (rather than the API payload) means the
            # retrieved context lands in the stored transcript too, so history
            # replay and the responses-mode 404 rebuild resend exactly what the
            # model originally saw.
            context = "\n\n".join(
                f"--- retrieved: {h['document']} (chunk {h['chunk_index']}, "
                f"score {h['score']}) ---\n{h['text']}\n--- end retrieved ---"
                for h in hits
            )
            prompt = f"{prompt}\n\n{context}"

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
        if retrieved is not None:
            user_message["retrieval"] = {"collection": collection, "chunks": retrieved}
        record["messages"].append(user_message)
        record["messages"].append({"role": "assistant", "content": reply})
        record["model"] = resolved_model
        store.save(record)

    result: dict[str, Any] = {
        "reply": reply,
        "conversation": conversation,
        "mode": mode,
        "model": resolved_model,
    }
    if retrieved is not None:
        result["collection"] = collection
        result["retrieved"] = retrieved
    return result


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


@mcp.tool()
def upload_documents(
    paths: list[str],
    collection: str = "default",
    embedding_model: str | None = None,
) -> dict[str, Any]:
    """Extract, chunk, embed, and store local documents in a named persistent collection.

    Accepts UTF-8 text/code files, .pdf (text layer), and .docx, max 15 MB
    each. The collection is created on first use and pins its embedding
    deployment (`embedding_model` or FOUNDRY_EMBEDDING_DEPLOYMENT); later
    uploads must use the same deployment. Re-uploading a file replaces its
    previous chunks; files whose content is unchanged are skipped. One bad
    file never aborts the batch - check per-file `status` in the result.
    """
    if not paths:
        raise ValueError("paths must contain at least one file path.")
    snapshot = docstore.load(collection)  # also validates the collection name
    if snapshot is not None:
        pinned = snapshot[0]["embedding_model"]
        if embedding_model is not None and embedding_model != pinned:
            raise ValueError(
                f"Collection {collection!r} uses embedding model {pinned!r}, which is "
                "fixed at collection creation; use a new collection to change it."
            )
        model = pinned
    else:
        model = _resolved_embedding_model(embedding_model)
    # Fail fast on client misconfiguration: a missing FOUNDRY_OPENAI_BASE_URL or
    # FOUNDRY_API_KEY is an environment problem for the whole call and must not
    # be demoted to per-file "failed" statuses by the loop below.
    _openai_client()
    existing_sha = {
        d["path"]: d["content_sha256"] for d in (snapshot[0]["documents"] if snapshot else [])
    }

    staged: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    counts = {"indexed": 0, "replaced": 0, "unchanged": 0, "failed": 0}
    seen: set[str] = set()
    for raw in paths:
        try:
            path, text, digest = documents.extract_text(raw)
        except (ValueError, RuntimeError) as exc:
            counts["failed"] += 1
            files.append({"path": raw, "status": "failed", "error": str(exc)})
            continue
        key = str(path)
        if key in seen:
            files.append({"path": key, "status": "duplicate"})
            continue
        seen.add(key)
        if existing_sha.get(key) == digest:
            counts["unchanged"] += 1
            files.append({"path": key, "status": "unchanged"})
            continue
        chunks = documents.chunk_text(text)
        try:
            vectors = _embed_texts([c["text"] for c in chunks], model)
        except RuntimeError as exc:
            counts["failed"] += 1
            files.append({"path": key, "status": "failed", "error": str(exc)})
            continue
        status = "replaced" if key in existing_sha else "indexed"
        counts[status] += 1
        staged.append(
            {
                "document": path.name,
                "path": key,
                "content_sha256": digest,
                "chunks": chunks,
                "vectors": vectors,
            }
        )
        files.append({"path": key, "status": status, "chunks": len(chunks)})

    result: dict[str, Any] = {"collection": collection, "embedding_model": model}
    if staged:
        committed = docstore.commit_documents(collection, model, staged)
        result["dimension"] = committed["dimension"]
    else:
        # All failed/unchanged: never create an empty collection.
        result["dimension"] = snapshot[0]["dimension"] if snapshot else None
    result.update(counts)
    result["files"] = files
    return result


@mcp.tool()
def search_documents(
    query: str,
    collection: str = "default",
    top_k: int = 5,
    min_score: float | None = None,
) -> dict[str, Any]:
    """Semantic search over a stored document collection.

    Embeds the query with the collection's pinned embedding deployment and
    returns the `top_k` most similar chunks by cosine similarity, best first.
    `min_score` (0-1) drops weak matches.
    """
    if not 1 <= top_k <= 50:
        raise ValueError(f"top_k must be between 1 and 50, got {top_k}.")
    record, hits = _retrieve(collection, query, top_k, min_score)
    return {
        "collection": collection,
        "embedding_model": record["embedding_model"],
        "results": hits,
    }


@mcp.tool()
def list_collections() -> list[dict[str, Any]]:
    """List document collections with embedding model, document/chunk counts, and last update."""
    return docstore.list_all()


@mcp.tool()
def get_collection(name: str) -> dict[str, Any]:
    """Return a collection's metadata and per-document details (chunk texts omitted)."""
    loaded = docstore.load(name)
    if loaded is None:
        raise ValueError(f"No collection named {name!r}.")
    record, _vectors = loaded
    summary = {k: v for k, v in record.items() if k != "chunks"}
    summary["chunk_count"] = len(record["chunks"])
    return summary


@mcp.tool()
def delete_document(document: str, collection: str = "default") -> dict[str, Any]:
    """Remove one document (its chunks and vectors) from a collection.

    `document` is the stored document name (e.g. "notes.pdf") or its full
    path; pass the path when two stored documents share a name.
    """
    return docstore.remove_document(collection, document)


@mcp.tool()
def delete_collection(name: str) -> dict[str, Any]:
    """Delete an entire document collection from local storage."""
    try:
        loaded = docstore.load(name)
    except RuntimeError:
        loaded = None  # corrupt file: deletion must still be possible
    docstore.delete(name)
    return {"deleted": name, "documents": len(loaded[0]["documents"]) if loaded else None}


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
