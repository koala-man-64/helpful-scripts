"""Persistence, integrity, and search tests for mcp_chatbot.docstore (offline)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from mcp_chatbot import docstore, store


@pytest.fixture
def collections(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MCP_CHATBOT_DATA_DIR", str(tmp_path))
    return tmp_path / "collections"


def _staged(
    document: str = "notes.txt",
    path: str = "C:/docs/notes.txt",
    texts: tuple[str, ...] = ("alpha", "beta"),
    dim: int = 4,
    hot: int = 0,
    sha: str = "sha-1",
) -> dict[str, Any]:
    """Staged doc whose chunk i embeds as a one-hot at dim index (hot + i) % dim.

    Magnitude 2.0 so tests prove commit normalizes rows to unit length.
    """
    chunks = [{"text": t, "index": i, "start": 0, "end": len(t)} for i, t in enumerate(texts)]
    vectors = [
        [2.0 if j == (hot + i) % dim else 0.0 for j in range(dim)] for i in range(len(texts))
    ]
    return {
        "document": document,
        "path": path,
        "content_sha256": sha,
        "chunks": chunks,
        "vectors": vectors,
    }


def test_commit_and_load_round_trip(collections: Path) -> None:
    docstore.commit_documents("c1", "embed-model", [_staged()])
    record, vectors = docstore.load("c1")
    assert record["name"] == "c1"
    assert record["embedding_model"] == "embed-model"
    assert record["dimension"] == 4
    assert [c["text"] for c in record["chunks"]] == ["alpha", "beta"]
    assert vectors.shape == (2, 4)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)  # normalized at commit
    assert sorted(p.name for p in collections.iterdir()) == ["c1.npz"]  # no .tmp debris


def test_load_missing_returns_none(collections: Path) -> None:
    assert docstore.load("nope") is None


def test_invalid_collection_name_rejected(collections: Path) -> None:
    with pytest.raises(ValueError, match="Invalid collection name"):
        docstore.load("../escape")


def test_corrupt_file_names_delete_collection(collections: Path) -> None:
    collections.mkdir(parents=True)
    (collections / "bad.npz").write_bytes(b"garbage")
    with pytest.raises(RuntimeError, match="delete_collection"):
        docstore.load("bad")


def test_row_count_mismatch_detected(collections: Path) -> None:
    docstore.commit_documents("c1", "m", [_staged()])
    record, _vectors = docstore.load("c1")
    # Hand-tamper: three vector rows but two chunk entries.
    meta = np.frombuffer(json.dumps(record).encode("utf-8"), dtype=np.uint8)
    with open(collections / "c1.npz", "wb") as fh:
        np.savez(fh, vectors=np.zeros((3, 4), dtype=np.float32), meta=meta)
    with pytest.raises(RuntimeError, match="does not match chunk metadata"):
        docstore.load("c1")


def test_unknown_version_refused(collections: Path) -> None:
    docstore.commit_documents("c1", "m", [_staged()])
    record, vectors = docstore.load("c1")
    record["version"] = 99
    meta = np.frombuffer(json.dumps(record).encode("utf-8"), dtype=np.uint8)
    with open(collections / "c1.npz", "wb") as fh:
        np.savez(fh, vectors=vectors, meta=meta)
    with pytest.raises(RuntimeError, match="unsupported version"):
        docstore.load("c1")


def test_case_aliased_file_rejected(collections: Path) -> None:
    # Simulates Windows NTFS case-insensitivity: the file on disk belongs to a
    # collection whose stored name differs from the requested one.
    docstore.commit_documents("Alpha", "m", [_staged()])
    data = (collections / "Alpha.npz").read_bytes()
    (collections / "Alpha.npz").unlink()
    (collections / "alpha.npz").write_bytes(data)
    with pytest.raises(ValueError, match="case-insensitive"):
        docstore.load("alpha")


def test_upsert_replaces_document_rows(collections: Path) -> None:
    docstore.commit_documents("c1", "m", [_staged(texts=("one", "two"))])
    docstore.commit_documents("c1", "m", [_staged(texts=("three",), sha="sha-2")])
    record, vectors = docstore.load("c1")
    assert [c["text"] for c in record["chunks"]] == ["three"]
    assert vectors.shape == (1, 4)
    assert len(record["documents"]) == 1
    assert record["documents"][0]["content_sha256"] == "sha-2"


def test_commit_model_pin_conflict(collections: Path) -> None:
    docstore.commit_documents("c1", "embed-a", [_staged()])
    with pytest.raises(RuntimeError, match="pinned to embedding model"):
        docstore.commit_documents("c1", "embed-b", [_staged(sha="sha-2")])


def test_commit_dimension_drift_rejected_and_store_untouched(collections: Path) -> None:
    docstore.commit_documents("c1", "m", [_staged()])
    with pytest.raises(RuntimeError, match="dimensions"):
        docstore.commit_documents("c1", "m", [_staged(dim=8, sha="sha-2")])
    record, vectors = docstore.load("c1")
    assert record["dimension"] == 4
    assert vectors.shape == (2, 4)


def test_commit_rejects_mismatched_vector_count(collections: Path) -> None:
    bad = _staged()
    bad["vectors"] = bad["vectors"][:1]
    with pytest.raises(RuntimeError, match="does not match chunk count"):
        docstore.commit_documents("c1", "m", [bad])
    assert docstore.load("c1") is None  # nothing created


def test_commit_rejects_inconsistent_staged_dimensions(collections: Path) -> None:
    with pytest.raises(RuntimeError, match="inconsistent dimensions"):
        docstore.commit_documents(
            "c1", "m", [_staged(), _staged("b.txt", "C:/docs/b.txt", dim=8, sha="s2")]
        )
    assert docstore.load("c1") is None


def test_remove_document_compacts_and_keeps_alignment(collections: Path) -> None:
    docstore.commit_documents(
        "c1",
        "m",
        [
            _staged("a.txt", "C:/docs/a.txt", texts=("alpha", "beta"), hot=0),
            _staged("b.txt", "C:/docs/b.txt", texts=("gamma",), hot=2, sha="sha-b"),
        ],
    )
    result = docstore.remove_document("c1", "a.txt")
    assert result["deleted"] == "a.txt"
    assert result["remaining_documents"] == 1
    record, vectors = docstore.load("c1")
    assert [c["text"] for c in record["chunks"]] == ["gamma"]
    # The surviving row is still gamma's committed vector (one-hot at index 2),
    # proving row/chunk alignment survives compaction.
    hits = docstore.top_k(record, vectors, [0.0, 0.0, 1.0, 0.0], k=1)
    assert hits[0] == {
        "document": "b.txt",
        "path": "C:/docs/b.txt",
        "chunk_index": 0,
        "score": 1.0,
        "text": "gamma",
    }


def test_remove_by_path_unknown_and_ambiguous(collections: Path) -> None:
    docstore.commit_documents(
        "c1",
        "m",
        [
            _staged("notes.txt", "C:/a/notes.txt"),
            _staged("notes.txt", "C:/b/notes.txt", sha="sha-b"),
        ],
    )
    with pytest.raises(ValueError, match="ambiguous"):
        docstore.remove_document("c1", "notes.txt")
    docstore.remove_document("c1", "C:/b/notes.txt")
    record, _vectors = docstore.load("c1")
    assert [d["path"] for d in record["documents"]] == ["C:/a/notes.txt"]
    with pytest.raises(ValueError, match="No document"):
        docstore.remove_document("c1", "gone.txt")


@pytest.mark.skipif(
    os.path.normcase("A") == "A", reason="requires case-insensitive path semantics"
)
def test_remove_by_path_ignores_windows_case(collections: Path) -> None:
    # A case-variant path must still match once the source file is gone
    # (resolve() cannot canonicalize casing for nonexistent files).
    docstore.commit_documents("c1", "m", [_staged("notes.txt", "C:/docs/notes.txt")])
    docstore.remove_document("c1", "C:/DOCS/NOTES.TXT")
    record, _vectors = docstore.load("c1")
    assert record["documents"] == []


def test_remove_from_missing_collection(collections: Path) -> None:
    with pytest.raises(ValueError, match="No collection named"):
        docstore.remove_document("nope", "a.txt")


def test_remove_last_document_keeps_pin(collections: Path) -> None:
    docstore.commit_documents("c1", "m", [_staged()])
    docstore.remove_document("c1", "notes.txt")
    record, vectors = docstore.load("c1")
    assert record["documents"] == []
    assert record["chunks"] == []
    assert record["dimension"] == 4
    assert record["embedding_model"] == "m"
    assert vectors.shape == (0, 4)
    assert docstore.top_k(record, vectors, [1.0, 0.0, 0.0, 0.0], k=3) == []


def test_top_k_ordering_k_bounds_and_min_score(collections: Path) -> None:
    docstore.commit_documents(
        "c1", "m", [_staged("a.txt", "C:/docs/a.txt", texts=("alpha", "beta", "gamma"), hot=0)]
    )
    record, vectors = docstore.load("c1")
    hits = docstore.top_k(record, vectors, [1.0, 0.5, 0.0, 0.0], k=2)
    assert [h["text"] for h in hits] == ["alpha", "beta"]
    assert hits[0]["score"] > hits[1]["score"] > 0
    assert len(docstore.top_k(record, vectors, [1.0, 0.0, 0.0, 0.0], k=50)) == 3
    strong = docstore.top_k(record, vectors, [1.0, 0.5, 0.0, 0.0], k=5, min_score=0.5)
    assert [h["text"] for h in strong] == ["alpha"]


def test_top_k_deterministic_tie_break(collections: Path) -> None:
    docstore.commit_documents(
        "c1",
        "m",
        [
            _staged("b.txt", "C:/docs/b.txt", texts=("same",), hot=0, sha="s1"),
            _staged("a.txt", "C:/docs/a.txt", texts=("same",), hot=0, sha="s2"),
        ],
    )
    record, vectors = docstore.load("c1")
    hits = docstore.top_k(record, vectors, [1.0, 0.0, 0.0, 0.0], k=2)
    # Equal scores: ordered by document name, then chunk index - never by row order.
    assert [h["document"] for h in hits] == ["a.txt", "b.txt"]


def test_query_dimension_mismatch_raises(collections: Path) -> None:
    docstore.commit_documents("c1", "m", [_staged()])
    record, vectors = docstore.load("c1")
    with pytest.raises(RuntimeError, match="dimensions"):
        docstore.top_k(record, vectors, [1.0, 0.0], k=1)


def test_delete_collection_and_corrupt_tolerance(collections: Path) -> None:
    docstore.commit_documents("c1", "m", [_staged()])
    docstore.delete("c1")
    assert docstore.load("c1") is None
    with pytest.raises(ValueError, match="No collection named"):
        docstore.delete("c1")
    (collections / "bad.npz").write_bytes(b"garbage")
    docstore.delete("bad")
    assert not (collections / "bad.npz").exists()


def test_list_all_sorted_with_error_entries(
    collections: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "utc_now", lambda: "2026-01-01T00:00:00Z")
    docstore.commit_documents("older", "m", [_staged()])
    monkeypatch.setattr(store, "utc_now", lambda: "2026-01-01T00:00:01Z")
    docstore.commit_documents("newer", "m", [_staged(sha="sha-2")])
    (collections / "bad.npz").write_bytes(b"garbage")
    (collections / "My Notes.npz").write_bytes(b"foreign")  # invalid stem: skipped
    summaries = docstore.list_all()
    assert [s["name"] for s in summaries[:2]] == ["newer", "older"]
    assert summaries[0] == {
        "name": "newer",
        "embedding_model": "m",
        "dimension": 4,
        "document_count": 1,
        "chunk_count": 2,
        "updated_at": "2026-01-01T00:00:01Z",
    }
    errors = [s for s in summaries if "error" in s]
    assert [e["name"] for e in errors] == ["bad"]
    assert "unreadable" in errors[0]["error"]


def test_list_all_empty_when_dir_missing(collections: Path) -> None:
    assert docstore.list_all() == []


def test_save_failure_preserves_committed_file(
    collections: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docstore.commit_documents("c1", "m", [_staged()])
    original = (collections / "c1.npz").read_bytes()

    def broken_replace(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(docstore.os, "replace", broken_replace)
    with pytest.raises(OSError):
        docstore.commit_documents("c1", "m", [_staged(texts=("new",), sha="sha-2")])
    # The previously committed file is untouched and still loads.
    assert (collections / "c1.npz").read_bytes() == original
