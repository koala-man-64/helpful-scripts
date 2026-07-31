from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_chatbot import store


def _record(name: str = "conv-a", mode: str = "chat", model: str = "m1") -> dict[str, Any]:
    return store.new_record(name, mode, model, "sys")


def test_save_then_load_round_trips_record(env) -> None:
    record = _record()
    record["messages"].append({"role": "user", "content": "hi"})
    store.save(record)
    assert store.load("conv-a") == record


def test_save_is_atomic_no_tmp_left_behind(env) -> None:
    store.save(_record())
    assert sorted(p.name for p in env.iterdir()) == ["conv-a.json"]
    json.loads((env / "conv-a.json").read_text(encoding="utf-8"))


def test_load_unknown_name_returns_none(env) -> None:
    assert store.load("nope") is None


def test_list_conversations_sorted_newest_first(env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "utc_now", lambda: "2026-01-01T00:00:00Z")
    store.save(_record("older"))
    monkeypatch.setattr(store, "utc_now", lambda: "2026-01-01T00:00:01Z")
    store.save(_record("newer", mode="responses", model="m2"))
    summaries = store.list_all()
    assert [s["name"] for s in summaries] == ["newer", "older"]
    assert summaries[0] == {
        "name": "newer",
        "mode": "responses",
        "model": "m2",
        "message_count": 0,
        "updated_at": "2026-01-01T00:00:01Z",
    }


def test_list_all_empty_when_dir_missing(env) -> None:
    assert store.list_all() == []


def test_delete_removes_file_and_returns_record(env) -> None:
    store.save(_record())
    record = store.delete("conv-a")
    assert record["name"] == "conv-a"
    assert store.load("conv-a") is None


def test_delete_unknown_name_raises(env) -> None:
    with pytest.raises(ValueError, match="nope"):
        store.delete("nope")


@pytest.mark.parametrize(
    "name", ["../x", "a/b", "a\\b", "", "x" * 65, ".hidden", "a:b", "abc\n", "abc\ndef"]
)
def test_invalid_names_rejected(env, name: str) -> None:
    with pytest.raises(ValueError, match="Invalid conversation name"):
        store.validate_name(name)


def test_valid_names_accepted(env) -> None:
    for name in ("a", "conv-a", "A.b_c-9", "x" * 64):
        assert store.validate_name(name) == name


def test_corrupt_json_raises_clear_error(env) -> None:
    env.mkdir(parents=True)
    path = env / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="bad.json"):
        store.load("bad")


def test_case_aliased_file_rejected_not_silently_continued(env) -> None:
    # Simulates Windows NTFS case-insensitivity: the file on disk belongs to
    # a conversation whose stored name differs from the requested one.
    env.mkdir(parents=True)
    record = store.new_record("Alpha", "chat", "m1", None)
    (env / "alpha.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="case-insensitive"):
        store.load("alpha")


def test_list_all_skips_foreign_files_and_reports_corrupt_ones(env) -> None:
    store.save(_record("good"))
    (env / "My Notes.json").write_text("{}", encoding="utf-8")  # foreign: invalid stem
    (env / "broken.json").write_text("{not json", encoding="utf-8")
    summaries = store.list_all()
    by_name = {s["name"]: s for s in summaries}
    assert set(by_name) == {"good", "broken"}
    assert by_name["good"]["mode"] == "chat"
    assert "unreadable" in by_name["broken"]["error"]


def test_delete_corrupt_file_still_deletes(env) -> None:
    env.mkdir(parents=True)
    (env / "broken.json").write_text("{not json", encoding="utf-8")
    assert store.delete("broken") is None
    assert not (env / "broken.json").exists()


def test_save_failure_mid_write_preserves_existing_file(env, monkeypatch) -> None:
    record = _record()
    store.save(record)
    original = (env / "conv-a.json").read_text(encoding="utf-8")

    def broken_replace(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store.os, "replace", broken_replace)
    record["messages"].append({"role": "user", "content": "lost turn"})
    with pytest.raises(OSError):
        store.save(record)
    # The previously committed file is untouched and still parses.
    assert (env / "conv-a.json").read_text(encoding="utf-8") == original
    json.loads(original)
