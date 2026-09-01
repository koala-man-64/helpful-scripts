from __future__ import annotations

import pytest

from mcp_chatbot import bench


def test_list_models_delegates_to_filtered_server(monkeypatch) -> None:
    expected = {"models": [{"id": "allowed", "owned_by": None}]}
    monkeypatch.setattr(bench.server, "list_models", lambda: expected)

    assert bench.list_models() == expected


def test_chat_delegates_only_stateless_bounded_arguments(monkeypatch) -> None:
    recorded = {}

    def fake_chat(**kwargs):
        recorded.update(kwargs)
        return {"reply": "ok"}

    monkeypatch.setattr(bench.server, "chat", fake_chat)

    assert bench.chat(
        prompt="bounded question",
        model="allowed",
        mode="chat",
        system="be concise",
        reasoning_effort="low",
        temperature=0.2,
        max_output_tokens=123,
    ) == {"reply": "ok"}
    assert recorded == {
        "prompt": "bounded question",
        "model": "allowed",
        "mode": "chat",
        "system": "be concise",
        "reasoning_effort": "low",
        "temperature": 0.2,
        "max_output_tokens": 123,
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"prompt": ""}, "prompt must contain"),
        ({"prompt": "x" * (bench.MAX_PROMPT_CHARS + 1)}, "prompt must contain"),
        (
            {"prompt": "ok", "system": "x" * (bench.MAX_SYSTEM_CHARS + 1)},
            "system must not exceed",
        ),
        ({"prompt": "ok", "mode": "agents"}, "mode must be"),
        ({"prompt": "ok", "max_output_tokens": 0}, "max_output_tokens"),
        (
            {"prompt": "ok", "max_output_tokens": bench.MAX_OUTPUT_TOKENS + 1},
            "max_output_tokens",
        ),
    ],
)
def test_chat_rejects_out_of_scope_or_unbounded_requests(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        bench.chat(**kwargs)
