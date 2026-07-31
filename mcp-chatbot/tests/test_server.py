from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakeOpenAI, make_api_error

from mcp_chatbot import server, store

PNG_BYTES = b"\x89PNG\r\n\x1a\nfakepngdata"


def test_single_shot_responses_is_default_and_persists_nothing(env, fake_openai) -> None:
    result = server.chat(prompt="hi")
    assert result == {
        "reply": "fake reply",
        "conversation": None,
        "mode": "responses",
        "model": "test-deployment",
    }
    assert len(fake_openai.responses_calls) == 1
    call = fake_openai.responses_calls[0]
    assert call["model"] == "test-deployment"
    assert "previous_response_id" not in call
    assert not env.exists()  # store never touched


def test_per_call_model_overrides_env_default(env, fake_openai) -> None:
    server.chat(prompt="hi", model="my-deployment")
    assert fake_openai.responses_calls[0]["model"] == "my-deployment"


def test_missing_model_everywhere_raises(env, fake_openai, monkeypatch) -> None:
    monkeypatch.delenv("FOUNDRY_DEFAULT_DEPLOYMENT")
    with pytest.raises(RuntimeError, match="FOUNDRY_DEFAULT_DEPLOYMENT"):
        server.chat(prompt="hi")


def test_missing_api_key_raises_named_error(env, monkeypatch) -> None:
    monkeypatch.delenv("FOUNDRY_API_KEY")
    with pytest.raises(RuntimeError, match="FOUNDRY_API_KEY"):
        server.chat(prompt="hi")


def test_params_passed_only_when_provided(env, fake_openai) -> None:
    server.chat(prompt="hi")
    bare = fake_openai.responses_calls[0]
    for key in ("reasoning", "temperature", "max_output_tokens", "instructions"):
        assert key not in bare
    server.chat(
        prompt="hi", system="s", reasoning_effort="high", temperature=0.2, max_output_tokens=99
    )
    full = fake_openai.responses_calls[1]
    assert full["reasoning"] == {"effort": "high"}
    assert full["temperature"] == 0.2
    assert full["max_output_tokens"] == 99
    assert full["instructions"] == "s"


def test_responses_conversation_chains_previous_response_id(env, fake_openai) -> None:
    server.chat(prompt="one", conversation="c", system="be nice")
    server.chat(prompt="two", conversation="c")
    first, second = fake_openai.responses_calls
    assert "previous_response_id" not in first
    assert second["previous_response_id"] == "resp_1"
    # Instructions are re-sent every turn (not inherited across the chain).
    assert first["instructions"] == "be nice"
    assert second["instructions"] == "be nice"
    record = store.load("c")
    assert record["last_response_id"] == "resp_2"
    assert [m["role"] for m in record["messages"]] == ["user", "assistant", "user", "assistant"]


def test_responses_expired_chain_rebuilds_from_transcript(env, fake_openai) -> None:
    server.chat(prompt="one", conversation="c")
    fake_openai.responses_failures.append(make_api_error(404, "response not found"))
    result = server.chat(prompt="two", conversation="c")
    assert result["reply"] == "fake reply"
    assert len(fake_openai.responses_calls) == 3  # turn 1, failed chained call, rebuilt retry
    retry = fake_openai.responses_calls[2]
    assert "previous_response_id" not in retry
    contents = [(m["role"], m["content"]) for m in retry["input"]]
    assert contents == [("user", "one"), ("assistant", "fake reply"), ("user", "two")]


def test_responses_non_404_error_surfaced_verbatim(env, fake_openai) -> None:
    fake_openai.responses_failures.append(make_api_error(400, "unsupported parameter"))
    with pytest.raises(RuntimeError, match=r"Azure request failed \(400\)"):
        server.chat(prompt="hi", temperature=0.5)


def test_chat_mode_replays_full_history(env, fake_openai) -> None:
    for prompt in ("one", "two", "three"):
        server.chat(prompt=prompt, conversation="c", mode="chat", system="s")
    final = fake_openai.chat_calls[2]["messages"]
    assert final[0] == {"role": "system", "content": "s"}
    assert [(m["role"], m["content"]) for m in final[1:]] == [
        ("user", "one"),
        ("assistant", "fake reply"),
        ("user", "two"),
        ("assistant", "fake reply"),
        ("user", "three"),
    ]


def test_chat_mode_uses_max_completion_tokens(env, fake_openai) -> None:
    server.chat(prompt="hi", mode="chat", max_output_tokens=50, reasoning_effort="low")
    call = fake_openai.chat_calls[0]
    assert call["max_completion_tokens"] == 50
    assert "max_tokens" not in call
    assert "max_output_tokens" not in call
    assert call["reasoning_effort"] == "low"


def test_image_attachment_shape_per_mode(env, fake_openai, tmp_path: Path) -> None:
    png = tmp_path / "a.png"
    png.write_bytes(PNG_BYTES)
    server.chat(prompt="look", attachments=[str(png)])
    parts = fake_openai.responses_calls[0]["input"][0]["content"]
    assert parts[1]["type"] == "input_image"
    assert isinstance(parts[1]["image_url"], str)
    server.chat(prompt="look", mode="chat", attachments=[str(png)])
    parts = fake_openai.chat_calls[0]["messages"][-1]["content"]
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_transcript_stores_paths_not_data_urls(env, fake_openai, tmp_path: Path) -> None:
    png = tmp_path / "a.png"
    png.write_bytes(PNG_BYTES)
    server.chat(prompt="look", conversation="c", mode="chat", attachments=[str(png)])
    raw = (env / "c.json").read_text(encoding="utf-8")
    assert "base64" not in raw
    record = json.loads(raw)
    assert record["messages"][0]["content"] == "look"
    assert record["messages"][0]["attachments"] == [{"path": str(png.resolve()), "kind": "image"}]
    # Replay re-encodes the image from disk.
    server.chat(prompt="again", conversation="c", mode="chat")
    replayed = fake_openai.chat_calls[1]["messages"][0]["content"]
    assert replayed[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_text_attachment_inlined_into_prompt(env, fake_openai, tmp_path: Path) -> None:
    f = tmp_path / "ctx.txt"
    f.write_text("context here", encoding="utf-8")
    server.chat(prompt="use this", attachments=[str(f)])
    sent = fake_openai.responses_calls[0]["input"][0]["content"]
    assert "--- file: ctx.txt ---" in sent
    assert "context here" in sent


def test_unknown_attachment_path_raises(env, fake_openai) -> None:
    with pytest.raises(ValueError, match="Attachment not found"):
        server.chat(prompt="hi", attachments=["missing.txt"])


def test_agents_first_call_creates_agent_and_conversation_then_reuses(env, fake_agents) -> None:
    project, aoai = fake_agents
    expected_name = server._agent_name("c")
    server.chat(prompt="one", conversation="c", mode="agents", system="s")
    server.chat(prompt="two", conversation="c", mode="agents")
    assert len(project.create_version_calls) == 1
    created = project.create_version_calls[0]
    assert created["agent_name"] == expected_name
    assert created["definition"].model == "test-deployment"
    assert created["definition"].instructions == "s"
    assert aoai.conversations_created == 1
    for call in aoai.responses_calls:
        assert call["conversation"] == "conv_1"
        assert call["extra_body"] == {
            "agent_reference": {"name": expected_name, "type": "agent_reference"}
        }
    record = store.load("c")
    assert record["agent_name"] == expected_name
    assert record["remote_conversation_id"] == "conv_1"


def test_agents_single_shot_uses_no_remote_conversation(env, fake_agents) -> None:
    project, aoai = fake_agents
    server.chat(prompt="hi", mode="agents")
    assert project.create_version_calls[0]["agent_name"] == "mcp-chatbot-oneshot"
    assert aoai.conversations_created == 0
    assert "conversation" not in aoai.responses_calls[0]
    assert not env.exists()


@pytest.mark.parametrize(
    "kwargs",
    [{"reasoning_effort": "high"}, {"temperature": 0.5}, {"max_output_tokens": 10}],
)
def test_agents_rejects_sampling_params(env, fake_agents, kwargs: dict) -> None:
    with pytest.raises(ValueError, match="agents mode does not accept"):
        server.chat(prompt="hi", mode="agents", **kwargs)


def test_credential_constructed_only_for_agents_mode(env, fake_openai, monkeypatch) -> None:
    constructed: list[int] = []
    aoai = FakeOpenAI()

    class FakeCred:
        def __init__(self) -> None:
            constructed.append(1)

    class FakeProjectClient:
        def __init__(self, endpoint: str, credential: object) -> None:
            self.agents = SimpleNamespace(create_version=lambda **kw: SimpleNamespace())

        def get_openai_client(self) -> FakeOpenAI:
            return aoai

    monkeypatch.setattr(server, "DefaultAzureCredential", FakeCred)
    monkeypatch.setattr(server, "AIProjectClient", FakeProjectClient)
    server.chat(prompt="hi")
    assert constructed == []
    server.chat(prompt="hi", mode="agents")
    assert len(constructed) == 1


def test_mode_conflict_on_existing_conversation_raises(env, fake_openai) -> None:
    server.chat(prompt="one", conversation="c", mode="chat")
    with pytest.raises(ValueError, match="cannot switch"):
        server.chat(prompt="two", conversation="c", mode="responses")


def test_system_conflict_on_existing_conversation_raises(env, fake_openai) -> None:
    server.chat(prompt="one", conversation="c", system="a")
    with pytest.raises(ValueError, match="different system"):
        server.chat(prompt="two", conversation="c", system="b")
    # Omitting system continues with the stored one.
    server.chat(prompt="two", conversation="c")
    assert fake_openai.responses_calls[-1]["instructions"] == "a"


def test_agents_model_conflict_raises(env, fake_agents) -> None:
    server.chat(prompt="one", conversation="c", mode="agents")
    with pytest.raises(ValueError, match="fixed at agent creation"):
        server.chat(prompt="two", conversation="c", mode="agents", model="other-deployment")


def test_agents_default_model_drift_does_not_corrupt_record(env, fake_agents, monkeypatch) -> None:
    server.chat(prompt="one", conversation="c", mode="agents", model="gpt-4o")
    assert store.load("c")["model"] == "gpt-4o"
    # Env default changes after the agent is created; continuing without an
    # explicit model must keep reporting the agent's true (fixed) model.
    monkeypatch.setenv("FOUNDRY_DEFAULT_DEPLOYMENT", "other-deployment")
    result = server.chat(prompt="two", conversation="c", mode="agents")
    assert result["model"] == "gpt-4o"
    assert store.load("c")["model"] == "gpt-4o"
    # And the agent's actual model is still accepted explicitly.
    server.chat(prompt="three", conversation="c", mode="agents", model="gpt-4o")


def test_agent_names_are_foundry_legal_and_collision_free(env) -> None:
    import re as _re

    for name in ("My.Conv_x-", "a" * 64, "x", "1.2.3"):
        agent = server._agent_name(name)
        assert len(agent) <= 63
        assert _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9]", agent), agent
    assert server._agent_name("a.b") != server._agent_name("a-b")
    assert server._agent_name(None) == "mcp-chatbot-oneshot"


def test_replay_with_missing_image_gives_clear_error(env, fake_openai, tmp_path: Path) -> None:
    png = tmp_path / "shot.png"
    png.write_bytes(PNG_BYTES)
    server.chat(prompt="look", conversation="c", mode="chat", attachments=[str(png)])
    png.unlink()
    with pytest.raises(ValueError, match="could not be read"):
        server.chat(prompt="again", conversation="c", mode="chat")


def test_empty_reply_raises_explanatory_error(env, fake_openai) -> None:
    fake_openai.reply = ""
    fake_openai.finish_reason = "length"
    with pytest.raises(RuntimeError, match="empty reply"):
        server.chat(prompt="hi", mode="chat")
    with pytest.raises(RuntimeError, match="empty reply"):
        server.chat(prompt="hi")


def test_get_conversation_returns_record_and_unknown_raises(env, fake_openai) -> None:
    server.chat(prompt="one", conversation="c")
    record = server.get_conversation("c")
    assert record["name"] == "c"
    with pytest.raises(ValueError, match="nope"):
        server.get_conversation("nope")


def test_list_conversations_tool(env, fake_openai) -> None:
    assert server.list_conversations() == []
    server.chat(prompt="one", conversation="c")
    assert [s["name"] for s in server.list_conversations()] == ["c"]


def test_delete_agents_conversation_returns_portal_note(env, fake_agents) -> None:
    server.chat(prompt="one", conversation="c", mode="agents")
    result = server.delete_conversation("c")
    assert result["deleted"] == "c"
    assert "Foundry portal" in result["note"]
    assert store.load("c") is None


def test_delete_plain_conversation_has_no_note(env, fake_openai) -> None:
    server.chat(prompt="one", conversation="c")
    result = server.delete_conversation("c")
    assert result == {"deleted": "c"}
