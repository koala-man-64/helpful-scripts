from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakeOpenAI, make_api_error

from mcp_chatbot import documents, server, store

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


# --- document tools ---


def _write_doc(tmp_path: Path, name: str = "notes.txt", text: str = "alpha facts") -> str:
    f = tmp_path / name
    f.write_text(text, encoding="utf-8")
    return str(f)


def test_upload_documents_creates_persistent_collection(env, fake_openai, tmp_path: Path) -> None:
    result = server.upload_documents([_write_doc(tmp_path)])
    assert result["collection"] == "default"
    assert result["embedding_model"] == "test-embedding"
    assert result["dimension"] == 8
    assert result["indexed"] == 1
    assert result["failed"] == 0
    assert result["files"][0]["status"] == "indexed"
    assert result["files"][0]["chunks"] == 1
    assert (env / "collections" / "default.npz").is_file()


def test_upload_batches_embedding_calls(env, fake_openai, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(server, "EMBED_BATCH_SIZE", 2)
    server.upload_documents([_write_doc(tmp_path, text="sentence. " * 400)])
    assert len(fake_openai.embeddings_calls) >= 2
    assert all(len(c["input"]) <= 2 for c in fake_openai.embeddings_calls)
    assert all(c["model"] == "test-embedding" for c in fake_openai.embeddings_calls)


def test_upload_missing_embedding_deployment_raises(
    env, fake_openai, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("FOUNDRY_EMBEDDING_DEPLOYMENT")
    with pytest.raises(RuntimeError, match="FOUNDRY_EMBEDDING_DEPLOYMENT"):
        server.upload_documents([_write_doc(tmp_path)])


def test_upload_missing_base_url_raises_not_per_file(env, tmp_path: Path, monkeypatch) -> None:
    # Client misconfiguration is a whole-call RuntimeError, never a per-file status.
    monkeypatch.delenv("FOUNDRY_OPENAI_BASE_URL")
    with pytest.raises(RuntimeError, match="FOUNDRY_OPENAI_BASE_URL"):
        server.upload_documents([_write_doc(tmp_path)])


def test_upload_locked_file_isolated_from_batch(
    env, fake_openai, tmp_path: Path, monkeypatch
) -> None:
    locked = _write_doc(tmp_path, "locked.txt", "cannot read me")
    good = _write_doc(tmp_path, "good.txt", "fine text")
    real_read_bytes = Path.read_bytes

    def flaky_read_bytes(self: Path) -> bytes:
        if self.name == "locked.txt":
            raise PermissionError("locked by another process")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", flaky_read_bytes)
    result = server.upload_documents([locked, good])
    assert result["failed"] == 1
    assert result["indexed"] == 1
    assert [f["status"] for f in result["files"]] == ["failed", "indexed"]
    assert "could not be read" in result["files"][0]["error"]


def test_multi_chunk_upload_keeps_vectors_aligned(
    env, fake_openai, tmp_path: Path, monkeypatch
) -> None:
    # Guards the .index sort in _embed_texts: with the fake returning batches in
    # reversed order, any misalignment pairs a chunk with another chunk's vector
    # and the exact-text query below stops scoring 1.0 against its own text.
    monkeypatch.setattr(server, "EMBED_BATCH_SIZE", 2)
    text = "\n\n".join(f"topic-{i} " * 200 for i in range(3))
    server.upload_documents([_write_doc(tmp_path, "big.txt", text)])
    chunks = documents.chunk_text(text)
    assert len(chunks) >= 3
    target = chunks[1]["text"]
    hit = server.search_documents(target, top_k=1)["results"][0]
    assert hit["score"] == 1.0
    assert hit["text"] == target


def test_upload_partial_failure_isolates_bad_file(env, fake_openai, tmp_path: Path) -> None:
    good = _write_doc(tmp_path, "good.txt", "useful text")
    result = server.upload_documents([good, str(tmp_path / "missing.txt")])
    assert result["indexed"] == 1
    assert result["failed"] == 1
    assert [f["status"] for f in result["files"]] == ["indexed", "failed"]
    assert "Document not found" in result["files"][1]["error"]


def test_upload_all_failed_creates_no_collection(env, fake_openai, tmp_path: Path) -> None:
    result = server.upload_documents([str(tmp_path / "missing.txt")])
    assert result["failed"] == 1
    assert result["dimension"] is None
    assert not (env / "collections").exists()
    with pytest.raises(ValueError, match="No collection named"):
        server.search_documents("q")


def test_upload_unchanged_file_skips_embedding(env, fake_openai, tmp_path: Path) -> None:
    doc = _write_doc(tmp_path)
    server.upload_documents([doc])
    calls = len(fake_openai.embeddings_calls)
    result = server.upload_documents([doc])
    assert result["unchanged"] == 1
    assert result["indexed"] == 0
    assert len(fake_openai.embeddings_calls) == calls  # no new API spend


def test_upload_changed_file_replaces_chunks(env, fake_openai, tmp_path: Path) -> None:
    doc = _write_doc(tmp_path, text="first version")
    server.upload_documents([doc])
    Path(doc).write_text("second version", encoding="utf-8")
    result = server.upload_documents([doc])
    assert result["replaced"] == 1
    hits = server.search_documents("second version")["results"]
    assert [h["text"] for h in hits] == ["second version"]  # old chunk is gone


def test_upload_duplicate_path_reported_once(env, fake_openai, tmp_path: Path) -> None:
    doc = _write_doc(tmp_path)
    result = server.upload_documents([doc, doc])
    assert result["indexed"] == 1
    assert [f["status"] for f in result["files"]] == ["indexed", "duplicate"]


def test_upload_conflicting_model_rejected(env, fake_openai, tmp_path: Path) -> None:
    server.upload_documents([_write_doc(tmp_path)])
    with pytest.raises(ValueError, match="fixed at collection creation"):
        server.upload_documents([_write_doc(tmp_path, "b.txt")], embedding_model="other-embed")


def test_env_drift_after_creation_keeps_pinned_model(
    env, fake_openai, tmp_path: Path, monkeypatch
) -> None:
    server.upload_documents([_write_doc(tmp_path)])
    # Env default changes after the collection is created; uploads and searches
    # must keep embedding with the pinned deployment, never the drifted one.
    monkeypatch.setenv("FOUNDRY_EMBEDDING_DEPLOYMENT", "drifted-model")
    server.upload_documents([_write_doc(tmp_path, "b.txt", "more text")])
    server.search_documents("anything")
    assert all(c["model"] == "test-embedding" for c in fake_openai.embeddings_calls)


def test_upload_azure_error_surfaced_per_file(env, fake_openai, tmp_path: Path) -> None:
    fake_openai.embeddings_failures.append(make_api_error(400, "bad request"))
    bad = _write_doc(tmp_path, "bad.txt", "poisoned text")
    good = _write_doc(tmp_path, "ok.txt", "fine text")
    result = server.upload_documents([bad, good])
    assert result["failed"] == 1
    assert result["indexed"] == 1
    assert "Azure request failed (400)" in result["files"][0]["error"]


def test_search_exact_text_scores_one(env, fake_openai, tmp_path: Path) -> None:
    server.upload_documents([_write_doc(tmp_path, text="alpha facts")])
    result = server.search_documents("alpha facts")
    assert result["embedding_model"] == "test-embedding"
    assert result["results"][0]["score"] == 1.0
    assert result["results"][0]["text"] == "alpha facts"
    assert result["results"][0]["document"] == "notes.txt"


def test_search_ranking_with_keyword_vectors(env, fake_openai, tmp_path: Path) -> None:
    e1 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    e2 = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    fake_openai.embedding_map = {
        "dogs bark": e1,
        "cats meow": e2,
        "about dogs": [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    }
    server.upload_documents(
        [_write_doc(tmp_path, "dogs.txt", "dogs bark"), _write_doc(tmp_path, "cats.txt", "cats meow")]
    )
    hits = server.search_documents("about dogs", top_k=2)["results"]
    assert [h["document"] for h in hits] == ["dogs.txt", "cats.txt"]
    assert hits[0]["score"] > hits[1]["score"]


def test_search_top_k_bounds_and_missing_collection(env, fake_openai) -> None:
    with pytest.raises(ValueError, match="top_k"):
        server.search_documents("q", top_k=0)
    with pytest.raises(ValueError, match="No collection named"):
        server.search_documents("q", collection="nope")


def test_chat_with_collection_injects_context_and_persists(
    env, fake_openai, tmp_path: Path
) -> None:
    server.upload_documents([_write_doc(tmp_path, text="alpha facts")])
    result = server.chat(prompt="what are the alpha facts?", conversation="c", collection="default")
    assert result["collection"] == "default"
    assert [r["document"] for r in result["retrieved"]] == ["notes.txt"]
    sent = fake_openai.responses_calls[-1]["input"][0]["content"]
    assert sent.startswith("what are the alpha facts?")
    assert "--- retrieved: notes.txt (chunk 0" in sent
    assert "alpha facts" in sent
    # The augmented text is what the transcript stores, so replay resends it.
    record = store.load("c")
    assert "--- retrieved: notes.txt" in record["messages"][0]["content"]
    assert record["messages"][0]["retrieval"]["collection"] == "default"


def test_chat_with_missing_collection_raises_before_model_call(env, fake_openai) -> None:
    with pytest.raises(ValueError, match="No collection named"):
        server.chat(prompt="hi", collection="nope")
    assert fake_openai.responses_calls == []


def test_chat_with_empty_collection_proceeds_without_context(
    env, fake_openai, tmp_path: Path
) -> None:
    server.upload_documents([_write_doc(tmp_path)])
    server.delete_document("notes.txt")
    result = server.chat(prompt="hi", collection="default")
    assert result["retrieved"] == []
    assert fake_openai.responses_calls[-1]["input"][0]["content"] == "hi"


def test_chat_agents_mode_with_collection(env, fake_agents, fake_openai, tmp_path: Path) -> None:
    # Retrieval embeds via the key-authenticated OpenAI client even in agents
    # mode; the augmented prompt must reach the remote agent conversation.
    server.upload_documents([_write_doc(tmp_path, text="alpha facts")])
    project, aoai = fake_agents
    result = server.chat(
        prompt="what are the alpha facts?", conversation="c", mode="agents", collection="default"
    )
    assert [r["document"] for r in result["retrieved"]] == ["notes.txt"]
    sent = aoai.responses_calls[0]["input"][0]["content"]
    assert "--- retrieved: notes.txt (chunk 0" in sent
    assert store.load("c")["messages"][0]["retrieval"]["collection"] == "default"


def test_delete_collection_recovers_from_corrupt_file(env) -> None:
    # The corruption error tells users to run delete_collection; that advice
    # must work even though every read path raises on the corrupt file.
    coll_dir = env / "collections"
    coll_dir.mkdir(parents=True)
    (coll_dir / "bad.npz").write_bytes(b"garbage")
    with pytest.raises(RuntimeError, match="delete_collection"):
        server.get_collection("bad")
    assert server.delete_collection("bad") == {"deleted": "bad", "documents": None}
    assert not (coll_dir / "bad.npz").exists()


def test_get_collection_and_list_collections_tools(env, fake_openai, tmp_path: Path) -> None:
    server.upload_documents([_write_doc(tmp_path)], collection="kb")
    summary = server.get_collection("kb")
    assert summary["name"] == "kb"
    assert summary["chunk_count"] == 1
    assert "chunks" not in summary
    assert [c["name"] for c in server.list_collections()] == ["kb"]
    with pytest.raises(ValueError, match="nope"):
        server.get_collection("nope")


def test_delete_document_and_delete_collection_tools(env, fake_openai, tmp_path: Path) -> None:
    server.upload_documents(
        [_write_doc(tmp_path, "a.txt", "text a"), _write_doc(tmp_path, "b.txt", "text b")]
    )
    result = server.delete_document("a.txt")
    assert result["deleted"] == "a.txt"
    assert result["remaining_documents"] == 1
    result = server.delete_collection("default")
    assert result == {"deleted": "default", "documents": 1}
    with pytest.raises(ValueError, match="No collection named"):
        server.delete_collection("default")
