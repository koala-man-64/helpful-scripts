# mcp-chatbot

A stdio MCP server that acts as a chatbot against an Azure AI Foundry resource.
One model-invoking tool (`chat`) covers single-shot questions and persistent
named conversations across three API modes, plus tools to manage stored
conversations. A local RAG layer adds persistent document collections: upload
files once, and `search_documents` (or `chat(collection=...)`) retrieves the
most relevant chunks by embedding similarity.

Everything persists locally under `~/.mcp-chatbot/` as plain files — no
database, no services beyond the Foundry resource itself.

## Quickstart

```powershell
cd mcp-chatbot
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest     # mocked tests, no network needed
python smoke.py      # proves full=11 tools and least-privilege bench=2 tools
```

Then register the server with your MCP client (see
[Register with an MCP client](#register-with-an-mcp-client)), set the
`FOUNDRY_*` variables for the modes you use (see
[Configuration](#configuration)), and try:

```
chat(prompt="Reply with the word pong")
upload_documents(paths=["C:/docs/handbook.pdf", "C:/docs/notes.md"])
search_documents(query="vacation policy")
chat(prompt="Summarize the vacation policy", collection="default")
```

## Tools

| Tool | Purpose |
|---|---|
| `list_models()` | List the Foundry model/deployment IDs available through the configured OpenAI-compatible endpoint. IDs can be passed to `chat(model=...)`. |
| `chat(prompt, conversation?, mode?, model?, system?, reasoning_effort?, temperature?, max_output_tokens?, attachments?, collection?)` | Send a prompt. Omit `conversation` for single-shot (nothing persisted); pass a name to create/continue a persistent conversation. Pass `collection` to inline the 5 most relevant stored document chunks. |
| `list_conversations()` | Summaries of stored conversations, newest first. |
| `get_conversation(name)` | Full stored record (transcript keeps attachment paths, never base64). |
| `delete_conversation(name)` | Delete the local record. Agents-mode remote objects are left for portal cleanup. |
| `upload_documents(paths, collection?, embedding_model?)` | Extract, chunk, embed, and store local files in a named persistent collection. Per-file statuses; one bad file never aborts the batch. |
| `search_documents(query, collection?, top_k?, min_score?)` | Cosine top-k over a collection, best first, with scores. |
| `list_collections()` | Summaries of stored collections, newest first. |
| `get_collection(name)` | Collection metadata and per-document details (chunk texts omitted). |
| `delete_document(document, collection?)` | Remove one document's chunks and vectors (by stored name, or path if ambiguous). |
| `delete_collection(name)` | Delete a whole collection, including its embedding-model pin. |

### Least-privilege model bench

`python -m mcp_chatbot.bench` is a separate stateless entry point for clients
that need model consultation rather than the full chatbot. It registers
exactly two tools: filtered `list_models()` and bounded `chat()`. Bench chat
supports only `responses` and `chat` modes, caps prompt/system/output sizes,
and exposes no conversations, agents, attachments, local-file ingestion,
document persistence, retrieval, or deletion tools. The Claude Foundry hybrid
kit registers this entry point.

### Modes

- `responses` (default) — Azure OpenAI Responses API. Server-side state via
  `previous_response_id`; Azure retains stored responses for ~30 days, after
  which the server transparently rebuilds the conversation from its local
  transcript.
- `chat` — Chat Completions; full history is replayed from the local record.
- `agents` — Foundry agents (azure-ai-projects 2.x, Responses protocol). The
  first turn of a conversation creates an agent version named
  `mcp-chatbot-<conversation>` and a remote conversation. Model and system
  prompt are fixed in the agent definition; `reasoning_effort`, `temperature`,
  and `max_output_tokens` are rejected in this mode. **Requires Entra ID auth**
  (`az login`), not the API key.

### Attachments

Local file paths. UTF-8 text/code files are inlined into the prompt with
`--- file: <name> ---` headers; `.png/.jpg/.jpeg/.gif/.webp` become base64
data-URL vision input. Max 15 MB per file. PDFs are not supported as chat
attachments — index them with `upload_documents` instead. Images in `agents`
mode are best-effort (data-URL input alongside an agent reference is not
officially documented); Azure's error is surfaced verbatim if a project/model
rejects them.

### Parameter pass-through

Deployment names are user-chosen, so the server never guesses model families:
`reasoning_effort`, `temperature`, and `max_output_tokens` are sent only when
you provide them, and Azure's own error (e.g. `temperature` on a reasoning
deployment) is returned verbatim.

## Documents (RAG)

### Uploading

`upload_documents` accepts UTF-8 text/code files, `.pdf` (text layer only —
scanned PDFs are rejected with a clear error), and `.docx`, max 15 MB each.
Text is split into ~1600-character chunks (200-char overlap, preferring
paragraph/sentence boundaries) and embedded with a Foundry embedding
deployment. Files are processed independently: the result carries a per-file
`status` (`indexed`, `replaced`, `unchanged`, `duplicate`, or `failed` with
the error), so one unreadable file never aborts the batch.

```
upload_documents(paths=["C:/docs/handbook.pdf", "C:/docs/broken.pdf"])
-> {"collection": "default", "embedding_model": "text-embedding-3-small",
    "dimension": 1536, "indexed": 1, "replaced": 0, "unchanged": 0, "failed": 1,
    "files": [{"path": "C:/docs/handbook.pdf", "status": "indexed", "chunks": 42},
              {"path": "C:/docs/broken.pdf", "status": "failed", "error": "..."}]}
```

Re-uploading a file replaces its chunks; unchanged files (same content hash)
are skipped without any embedding API calls. A document's identity is its
resolved absolute path; its stored display name is the file name.

### Model pinning

Each collection **pins** the embedding deployment (and vector dimension) it
was created with: later uploads and every search always use the pinned
deployment, so an env-default change can never mix vector spaces. To switch
embedding models, upload into a new collection or delete and re-create the
old one. If the deployment behind the pinned name is swapped for a model with
a different dimension, the server refuses to write rather than corrupt the
index.

### Searching

```
search_documents(query="vacation policy", top_k=3, min_score=0.2)
-> {"collection": "default", "embedding_model": "text-embedding-3-small",
    "results": [{"document": "handbook.pdf", "path": "C:/docs/handbook.pdf",
                 "chunk_index": 17, "score": 0.83, "text": "..."}, ...]}
```

Scoring is cosine similarity (vectors are unit-normalized at index time), and
ordering is deterministic: score desc, then document name, then chunk index.
`top_k` accepts 1–50 (default 5).

### Chat integration

`chat(collection=...)` runs the same retrieval and appends the top 5 chunks
to the prompt with `--- retrieved: <doc> (chunk n, score s) ---` framing. The
augmented text is what the transcript stores, so history replay resends
exactly what the model originally saw. Retrieval provenance (document, chunk,
score) is returned in the result and stored on the message. A collection that
exists but matches nothing simply proceeds without a context block (the
result's `retrieved` list is empty); a collection that does not exist is an
error.

Embeddings always go through the key-authenticated `/openai/v1` endpoint, so
document tools need `FOUNDRY_OPENAI_BASE_URL` and `FOUNDRY_API_KEY` even when
chatting in agents mode.

## Configuration

| Variable | Required | Meaning |
|---|---|---|
| `FOUNDRY_OPENAI_BASE_URL` | responses/chat modes and all document tools | Full base URL ending in `/openai/v1/` (either `*.openai.azure.com` or `*.services.ai.azure.com` host). No `api-version` needed. |
| `FOUNDRY_API_KEY` | responses/chat modes and all document tools | Foundry resource API key. |
| `FOUNDRY_PROJECT_ENDPOINT` | agents mode | Project endpoint: `https://<resource>.services.ai.azure.com/api/projects/<project>`. |
| `FOUNDRY_DEFAULT_DEPLOYMENT` | optional | Deployment used when `model` is omitted. |
| `FOUNDRY_ALLOWED_DEPLOYMENTS_JSON` | optional | Nonempty JSON array of allowed chat, agent, and embedding deployment names. When set, malformed policy fails closed, `list_models()` is filtered, and model calls outside the list are rejected. Omit only for intentional resource-wide standalone access. |
| `FOUNDRY_EMBEDDING_DEPLOYMENT` | document tools | Embedding deployment used when a collection is created and `embedding_model` is omitted. Existing collections always use their pinned deployment. |
| `FOUNDRY_TIMEOUT_SECONDS` | optional | OpenAI client timeout override (SDK default 600 s). |
| `MCP_CHATBOT_DATA_DIR` | optional | Data dir: conversations at its root, document collections under `collections/` (default `~/.mcp-chatbot/conversations` and `~/.mcp-chatbot/collections`). |

No variable is read until the first call that needs it — the server starts and
lists tools with zero configuration. Config comes from the MCP client's `env`
block (preferred, since clients launch servers from an arbitrary cwd) or from
the repo-root `.env`, which the server loads by explicit path.

## Storage layout

```
~/.mcp-chatbot/
├── conversations/
│   └── <name>.json      one JSON record per conversation (transcript,
│                        mode, model, response chain ids)
└── collections/
    └── <name>.npz       one file per document collection: L2-normalized
                         float32 vector matrix + JSON metadata (documents,
                         chunk texts, pinned embedding model/dimension)
```

All writes are atomic (tmp file + `os.replace`) and serialized behind a lock;
a collection's vectors and metadata live in a single `.npz` so they can never
disagree on disk. Corrupt files are reported with the exact path and never
hide other records; a corrupt collection can always be removed with
`delete_collection`. Everything survives server restarts — persistence is the
point.

## Register with an MCP client

```json
{
  "mcpServers": {
    "foundry-model-consult": {
      "command": "C:\\Users\\rdpro\\Projects\\helpful-scripts\\mcp-chatbot\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_chatbot.bench"],
      "env": {
        "FOUNDRY_OPENAI_BASE_URL": "${CFH_FOUNDRY_OPENAI_BASE_URL}",
        "FOUNDRY_API_KEY": "${CFH_MCP_API_KEY}",
        "FOUNDRY_DEFAULT_DEPLOYMENT": "${CFH_DEFAULT_DEPLOYMENT}",
        "FOUNDRY_ALLOWED_DEPLOYMENTS_JSON": "${CFH_ALLOWED_DEPLOYMENTS_JSON}"
      }
    }
  }
}
```

## Validate (offline — no deployment needed)

```powershell
python -m pytest    # 151 mocked tests, no network
python smoke.py     # exact stdio inventories -> full=11 tools, bench=2 tools
```

## Live smoke test (once a model deployment exists)

1. Fill the `FOUNDRY_*` group in the repo-root `.env` (base URL, key, deployment name).
2. Responses mode:
   ```powershell
   python -c "from mcp_chatbot.server import main, chat; import dotenv, pathlib; dotenv.load_dotenv(pathlib.Path('..') / '.env'); print(chat(prompt='Reply with the word pong'))"
   ```
3. Chat Completions: same with `mode='chat'`.
4. Agents: `az login`, set `FOUNDRY_PROJECT_ENDPOINT`, same with
   `mode='agents', conversation='live-smoke'`; afterwards run
   `delete_conversation('live-smoke')` and remove the `mcp-chatbot-live-smoke`
   agent in the Foundry portal.
5. Vision: repeat step 2 with `attachments=['some.png']` (also settles the
   undocumented agents-mode image question).
6. Documents: set `FOUNDRY_EMBEDDING_DEPLOYMENT`, then
   `upload_documents([r'..\README.md'])`, `search_documents('what is this repo?')`,
   and `chat(prompt='Summarize the docs', collection='default')`; confirm
   `~/.mcp-chatbot/collections/default.npz` survives a process restart, then
   `delete_collection('default')`.

## How it's built

| Module | Role |
|---|---|
| `mcp_chatbot/server.py` | FastMCP server: all 11 tools, the three mode runners, and every Azure call (model inventory, chat, and embeddings). |
| `mcp_chatbot/store.py` | Conversation persistence: one JSON file per conversation, atomic writes, name validation (names double as filenames). |
| `mcp_chatbot/docstore.py` | Collection persistence and search: `.npz` round-trip, integrity checks on load, model/dimension pinning, cosine top-k. The only module that touches numpy. |
| `mcp_chatbot/documents.py` | Text extraction (UTF-8 / pypdf / python-docx) and boundary-aware character chunking. Pure and offline. |
| `mcp_chatbot/attachments.py` | Chat attachments: text inlining and base64 image encoding per API shape. |
| `tests/` | 132 offline tests; the Azure clients are replaced by recording fakes, including deterministic fake embeddings (identical text → identical vector) so ranking is exactly testable. |
| `smoke.py` | Spawns the real server over stdio with all config stripped and asserts the full tool list — proves zero-config boot. |

Error convention throughout: `ValueError` means bad input (fix the call),
`RuntimeError` means environment or remote failure (fix config, or Azure's
error is surfaced verbatim). Messages say what to do next.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Missing required environment variable: FOUNDRY_...` | The mode or tool you called needs that variable — see [Configuration](#configuration). Nothing is read until first use, so the server lists tools fine without it. |
| `...embedding model ... fixed at collection creation` | You passed an `embedding_model` that conflicts with the collection's pin. Use the pinned model, a new collection, or delete and re-create. |
| `Collection file ... is corrupt` | The `.npz` failed integrity checks. Run `delete_collection(<name>)` and re-upload — corrupt files are always deletable. |
| `No extractable text in <file>` | Scanned PDF without a text layer (or an empty file). Run OCR first. |
| `Document ... is password-protected` | Decrypt the PDF before uploading. |
| `Model returned an empty reply` | A reasoning deployment spent the whole token budget on hidden reasoning — raise `max_output_tokens` or lower `reasoning_effort`. |
| Responses-mode conversation older than ~30 days | Handled automatically: the server rebuilds the thread from the local transcript when Azure's stored response has expired. |

## Follow-ups

- Migrate to mcp SDK 2.x once stable (FastMCP is renamed MCPServer there).
- Optional remote cleanup of agent versions/conversations on delete.
