"""Shared fixtures: isolated environment plus recording fakes for the Azure clients.

The test seam is the two client factory functions in server.py; fakes are
installed by monkeypatching those, so no network or credentials are ever
touched and the whole suite runs offline.
"""

from __future__ import annotations

import hashlib
import math
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from mcp_chatbot import server


class FakeOpenAI:
    """Recording fake for the OpenAI client surface the server uses."""

    def __init__(self) -> None:
        self.responses_calls: list[dict[str, Any]] = []
        self.chat_calls: list[dict[str, Any]] = []
        self.conversations_created = 0
        self.responses_failures: list[Exception] = []
        self.reply = "fake reply"
        self.finish_reason = "stop"
        self.embeddings_calls: list[dict[str, Any]] = []
        self.embeddings_failures: list[Exception] = []
        self.embedding_map: dict[str, list[float]] = {}
        self.embedding_dimensions = 8
        self.model_entries = [
            SimpleNamespace(id="test-deployment", owned_by="unit-test")
        ]
        self.models_failures: list[Exception] = []
        self.models_list_calls = 0
        self.responses = SimpleNamespace(create=self._responses_create)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat_create))
        self.conversations = SimpleNamespace(create=self._conversations_create)
        self.embeddings = SimpleNamespace(create=self._embeddings_create)
        self.models = SimpleNamespace(list=self._models_list)

    def _responses_create(self, **kwargs: Any) -> SimpleNamespace:
        self.responses_calls.append(kwargs)
        if self.responses_failures:
            raise self.responses_failures.pop(0)
        return SimpleNamespace(
            id=f"resp_{len(self.responses_calls)}",
            output_text=self.reply,
            incomplete_details=None,
        )

    def _chat_create(self, **kwargs: Any) -> SimpleNamespace:
        self.chat_calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.reply),
                    finish_reason=self.finish_reason,
                )
            ]
        )

    def _conversations_create(self, **kwargs: Any) -> SimpleNamespace:
        self.conversations_created += 1
        return SimpleNamespace(id=f"conv_{self.conversations_created}")

    def _embeddings_create(self, **kwargs: Any) -> SimpleNamespace:
        self.embeddings_calls.append(kwargs)
        if self.embeddings_failures:
            raise self.embeddings_failures.pop(0)
        data = [
            SimpleNamespace(embedding=self.vector_for(text), index=i)
            for i, text in enumerate(kwargs["input"])
        ]
        # Real API response order is unspecified; .index, not position, is the
        # contract. Returning reversed data keeps the server's sort load-bearing.
        data.reverse()
        return SimpleNamespace(data=data)

    def _models_list(self) -> list[SimpleNamespace]:
        self.models_list_calls += 1
        if self.models_failures:
            raise self.models_failures.pop(0)
        return self.model_entries

    def vector_for(self, text: str) -> list[float]:
        """Deterministic unit vector per text: identical text always embeds to the
        identical vector, so an exact-text query scores 1.0 against its own chunk.
        embedding_map overrides individual texts for predictable ranking tests."""
        if text in self.embedding_map:
            return self.embedding_map[text]
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [b - 127.5 for b in digest[: self.embedding_dimensions]]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]


class FakeProject:
    """Recording fake for the AIProjectClient agents surface."""

    def __init__(self) -> None:
        self.create_version_calls: list[dict[str, Any]] = []
        self.agents = SimpleNamespace(create_version=self._create_version)

    def _create_version(self, **kwargs: Any) -> SimpleNamespace:
        self.create_version_calls.append(kwargs)
        return SimpleNamespace(name=kwargs.get("agent_name"))


def make_api_error(status_code: int, message: str = "boom") -> openai.APIStatusError:
    request = httpx.Request("POST", "https://unit.test")
    response = httpx.Response(status_code, request=request)
    return openai.APIStatusError(message, response=response, body=None)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any):
    """Isolated Foundry env vars and a temp conversation store."""
    data_dir = tmp_path / "conversations"
    monkeypatch.setenv("FOUNDRY_OPENAI_BASE_URL", "https://unit.test/openai/v1/")
    monkeypatch.setenv("FOUNDRY_API_KEY", "unit-test-key")
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://unit.test/api/projects/unit")
    monkeypatch.setenv("FOUNDRY_DEFAULT_DEPLOYMENT", "test-deployment")
    monkeypatch.setenv("FOUNDRY_EMBEDDING_DEPLOYMENT", "test-embedding")
    monkeypatch.setenv("MCP_CHATBOT_DATA_DIR", str(data_dir))
    monkeypatch.delenv("FOUNDRY_ALLOWED_DEPLOYMENTS_JSON", raising=False)
    monkeypatch.delenv("FOUNDRY_TIMEOUT_SECONDS", raising=False)
    # Clearing at setup is what isolates tests; at teardown the factories may
    # still be monkeypatched fakes without a cache_clear attribute.
    server._openai_client.cache_clear()
    server._agents_clients.cache_clear()
    return data_dir


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> FakeOpenAI:
    fake = FakeOpenAI()
    monkeypatch.setattr(server, "_openai_client", lambda: fake)
    return fake


@pytest.fixture
def fake_agents(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeProject, FakeOpenAI]:
    project = FakeProject()
    aoai = FakeOpenAI()
    monkeypatch.setattr(server, "_agents_clients", lambda: (project, aoai))
    return project, aoai
