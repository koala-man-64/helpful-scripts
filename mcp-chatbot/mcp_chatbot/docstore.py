"""Document-collection persistence for the local RAG index.

Each collection is one ``.npz`` file at ``<collections_dir>/<name>.npz``
holding an L2-normalized float32 vector matrix plus a JSON metadata record
(documents, chunk texts, and the pinned embedding model). A single file keeps
vectors and metadata atomic by construction: one os.replace commits both or
neither. Writes are serialized behind a lock because FastMCP may run sync
tools on multiple worker threads.

Row invariant: ``record["chunks"][i]`` describes ``vectors[i]``; it is
maintained by construction on every mutation and verified on every load.
numpy stays inside this module - callers see plain dicts and lists, plus the
vector matrix as an opaque handle passed back into top_k().
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import numpy as np

from mcp_chatbot import store

_LOCK = threading.Lock()


def collections_dir() -> Path:
    override = os.getenv("MCP_CHATBOT_DATA_DIR")
    base = Path(override) if override else Path.home() / ".mcp-chatbot"
    return base / "collections"


def _path_for(name: str) -> Path:
    return collections_dir() / f"{store.validate_name(name, kind='collection')}.npz"


def new_collection(name: str, embedding_model: str) -> dict[str, Any]:
    now = store.utc_now()
    return {
        "version": 1,
        "name": name,
        "embedding_model": embedding_model,
        "dimension": None,
        "created_at": now,
        "updated_at": now,
        "documents": [],
        "chunks": [],
    }


def load(name: str) -> tuple[dict[str, Any], Any] | None:
    """Return (record, vector matrix), or None if the collection does not exist."""
    with _LOCK:
        return _load_unlocked(name)


def _load_unlocked(name: str) -> tuple[dict[str, Any], Any] | None:
    path = _path_for(name)
    if not path.exists():
        return None

    def corrupt(why: object) -> RuntimeError:
        return RuntimeError(
            f"Collection file {path} is corrupt ({why}); use delete_collection"
            f"({name!r}) and re-upload the documents."
        )

    try:
        with np.load(path, allow_pickle=False) as npz:
            vectors = npz["vectors"]
            meta_raw = npz["meta"]
    except Exception as exc:  # bad zip, missing keys, truncated file
        raise corrupt(exc) from exc
    try:
        record = json.loads(bytes(meta_raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise corrupt(exc) from exc
    if not isinstance(record, dict):
        raise corrupt("metadata is not a JSON object")
    if record.get("version") != 1:
        raise corrupt(f"unsupported version {record.get('version')!r}")
    chunks = record.get("chunks")
    if (
        not isinstance(chunks, list)
        or vectors.ndim != 2
        or vectors.shape[0] != len(chunks)
        or vectors.shape[1] != record.get("dimension")
    ):
        raise corrupt("vector matrix does not match chunk metadata")
    if record.get("name") != name:
        # On case-insensitive filesystems (Windows) 'Alpha' and 'alpha' resolve
        # to the same file; refuse to silently open a different collection.
        raise ValueError(
            f"Collection file for {name!r} belongs to {record.get('name')!r} (names "
            "are case-insensitive on this filesystem); use the exact stored name."
        )
    return record, vectors


def _save_unlocked(record: dict[str, Any], vectors: Any) -> None:
    path = _path_for(record["name"])
    record["updated_at"] = store.utc_now()
    meta = np.frombuffer(json.dumps(record, ensure_ascii=False).encode("utf-8"), dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    # Write through an open handle: np.savez appends ".npz" to bare paths that
    # lack the suffix, which would break the tmp -> os.replace protocol.
    with open(tmp, "wb") as fh:
        np.savez_compressed(fh, vectors=vectors, meta=meta)
    os.replace(tmp, path)


def commit_documents(
    name: str, embedding_model: str, staged: list[dict[str, Any]]
) -> dict[str, Any]:
    """Commit staged documents to a collection in one atomic write.

    Each staged item is {"document", "path", "content_sha256", "chunks",
    "vectors"} with plain-list vectors, one per chunk. The collection is
    created on first commit (pinning the model and dimension); re-staged paths
    replace their previous rows. Validation happens before anything is
    written, so a failure never leaves a partial index.
    """
    prepared: list[Any] = []
    dimension: int | None = None
    for doc in staged:
        matrix = np.asarray(doc["vectors"], dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(doc["chunks"]):
            raise RuntimeError(
                f"Embedding count does not match chunk count for {doc['document']!r}; "
                "nothing was saved."
            )
        if dimension is None:
            dimension = int(matrix.shape[1])
        elif matrix.shape[1] != dimension:
            raise RuntimeError(
                f"Embeddings returned inconsistent dimensions ({dimension} vs "
                f"{matrix.shape[1]}); nothing was saved."
            )
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        prepared.append(matrix / np.maximum(norms, 1e-12))

    with _LOCK:
        loaded = _load_unlocked(name)
        if loaded is None:
            record = new_collection(name, embedding_model)
            record["dimension"] = dimension
            vectors = np.zeros((0, dimension), dtype=np.float32)
        else:
            record, vectors = loaded
            if record["embedding_model"] != embedding_model:
                raise RuntimeError(
                    f"Collection {name!r} is pinned to embedding model "
                    f"{record['embedding_model']!r} but these embeddings came from "
                    f"{embedding_model!r}; nothing was saved."
                )
            if record["dimension"] != dimension:
                raise RuntimeError(
                    f"Embeddings from {embedding_model!r} have {dimension} dimensions "
                    f"but collection {name!r} stores {record['dimension']}; the "
                    "deployment behind that name may have changed. Delete and "
                    "re-create the collection to re-embed. Nothing was saved."
                )
        for doc, matrix in zip(staged, prepared):
            keep = [i for i, c in enumerate(record["chunks"]) if c["path"] != doc["path"]]
            if len(keep) != len(record["chunks"]):
                record["chunks"] = [record["chunks"][i] for i in keep]
                record["documents"] = [
                    d for d in record["documents"] if d["path"] != doc["path"]
                ]
                vectors = vectors[np.asarray(keep, dtype=np.intp)]
            record["chunks"].extend(
                {
                    "document": doc["document"],
                    "path": doc["path"],
                    "index": chunk["index"],
                    "start": chunk["start"],
                    "end": chunk["end"],
                    "text": chunk["text"],
                }
                for chunk in doc["chunks"]
            )
            record["documents"].append(
                {
                    "document": doc["document"],
                    "path": doc["path"],
                    "content_sha256": doc["content_sha256"],
                    "chunk_count": len(doc["chunks"]),
                    "added_at": store.utc_now(),
                }
            )
            vectors = np.vstack([vectors, matrix])
        _save_unlocked(record, vectors)
    return {
        "collection": name,
        "embedding_model": record["embedding_model"],
        "dimension": dimension,
        "documents": len(record["documents"]),
        "chunks": len(record["chunks"]),
    }


def remove_document(name: str, document: str) -> dict[str, Any]:
    """Remove one document (chunks and vector rows) from a collection.

    ``document`` matches the stored document name first, then the stored
    source path (as given or resolved); an ambiguous name lists the candidate
    paths instead of guessing.
    """
    with _LOCK:
        loaded = _load_unlocked(name)
        if loaded is None:
            raise ValueError(f"No collection named {name!r}.")
        record, vectors = loaded
        matches = [d for d in record["documents"] if d["document"] == document]
        if not matches:
            # normcase both sides: stored paths are canonical-cased from resolve(),
            # but resolve() cannot fix the caller's casing once the source file is
            # gone - exactly the stale-cleanup case this tool exists for.
            candidates = {
                os.path.normcase(document),
                os.path.normcase(str(Path(document).resolve())),
            }
            matches = [
                d for d in record["documents"] if os.path.normcase(d["path"]) in candidates
            ]
        if not matches:
            raise ValueError(f"No document {document!r} in collection {name!r}.")
        if len(matches) > 1:
            paths = ", ".join(sorted(d["path"] for d in matches))
            raise ValueError(
                f"Document name {document!r} is ambiguous in collection {name!r} "
                f"(matches: {paths}); pass the full path instead."
            )
        target = matches[0]["path"]
        keep = [i for i, c in enumerate(record["chunks"]) if c["path"] != target]
        record["chunks"] = [record["chunks"][i] for i in keep]
        record["documents"] = [d for d in record["documents"] if d["path"] != target]
        vectors = vectors[np.asarray(keep, dtype=np.intp)]
        # An emptied collection keeps its model/dimension pin; delete_collection
        # is the way to drop the pin.
        _save_unlocked(record, vectors)
    return {
        "deleted": matches[0]["document"],
        "path": target,
        "collection": name,
        "remaining_documents": len(record["documents"]),
    }


def top_k(
    record: dict[str, Any],
    vectors: Any,
    query_vector: list[float],
    k: int,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """Best-first cosine matches for a query vector against a loaded collection.

    Stored rows are unit-normalized at commit time, so scoring is one matrix
    product. Ordering is deterministic: score desc, then document, then chunk
    index.
    """
    if vectors.shape[0] == 0:
        return []
    query = np.asarray(query_vector, dtype=np.float32)
    if query.shape != (vectors.shape[1],):
        raise RuntimeError(
            f"Query embedding has {query.shape[0] if query.ndim else 0} dimensions but "
            f"collection {record['name']!r} stores {vectors.shape[1]}; the deployment "
            f"behind {record['embedding_model']!r} may have changed. Delete and "
            "re-create the collection to re-embed."
        )
    query = query / max(float(np.linalg.norm(query)), 1e-12)
    scores = vectors @ query
    chunks = record["chunks"]
    order = sorted(
        range(len(chunks)),
        key=lambda i: (-float(scores[i]), chunks[i]["document"], chunks[i]["index"]),
    )
    hits: list[dict[str, Any]] = []
    for i in order:
        score = float(scores[i])
        if min_score is not None and score < min_score:
            break  # scores only get worse from here
        chunk = chunks[i]
        hits.append(
            {
                "document": chunk["document"],
                "path": chunk["path"],
                "chunk_index": chunk["index"],
                "score": round(score, 4),
                "text": chunk["text"],
            }
        )
        if len(hits) == k:
            break
    return hits


def delete(name: str) -> None:
    """Delete a stored collection; corrupt files must still be deletable."""
    path = _path_for(name)
    if not path.exists():
        raise ValueError(f"No collection named {name!r}.")
    with _LOCK:
        path.unlink(missing_ok=True)


def list_all() -> list[dict[str, Any]]:
    """Summaries of all stored collections, most recently updated first.

    Files whose stem is not a valid collection name were not written by this
    server and are skipped; unreadable files are reported as an error entry so
    one bad file never hides the rest.
    """
    directory = collections_dir()
    if not directory.exists():
        return []
    summaries = []
    for path in directory.glob("*.npz"):
        if not store.is_valid_name(path.stem):
            continue
        try:
            with _LOCK:
                loaded = _load_unlocked(path.stem)
        except (RuntimeError, ValueError) as exc:
            summaries.append({"name": path.stem, "error": f"unreadable collection file: {exc}"})
            continue
        if loaded is None:  # deleted between glob and load
            continue
        record, _vectors = loaded
        summaries.append(
            {
                "name": record["name"],
                "embedding_model": record["embedding_model"],
                "dimension": record["dimension"],
                "document_count": len(record["documents"]),
                "chunk_count": len(record["chunks"]),
                "updated_at": record["updated_at"],
            }
        )
    return sorted(summaries, key=lambda s: s.get("updated_at", ""), reverse=True)
