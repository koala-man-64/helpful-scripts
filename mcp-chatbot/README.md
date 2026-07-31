# mcp-chatbot

A stdio MCP server that acts as a chatbot against an Azure AI Foundry resource.
One model-invoking tool (`chat`) covers single-shot questions and persistent
named conversations across three API modes, plus three small tools to manage
stored conversations.

## Tools

| Tool | Purpose |
|---|---|
| `chat(prompt, conversation?, mode?, model?, system?, reasoning_effort?, temperature?, max_output_tokens?, attachments?)` | Send a prompt. Omit `conversation` for single-shot (nothing persisted); pass a name to create/continue a persistent conversation. |
| `list_conversations()` | Summaries of stored conversations, newest first. |
| `get_conversation(name)` | Full stored record (transcript keeps attachment paths, never base64). |
| `delete_conversation(name)` | Delete the local record. Agents-mode remote objects are left for portal cleanup. |

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
data-URL vision input. Max 15 MB per file. PDFs are not supported.
Images in `agents` mode are best-effort (data-URL input alongside an agent
reference is not officially documented); Azure's error is surfaced verbatim if
a project/model rejects them.

### Parameter pass-through

Deployment names are user-chosen, so the server never guesses model families:
`reasoning_effort`, `temperature`, and `max_output_tokens` are sent only when
you provide them, and Azure's own error (e.g. `temperature` on a reasoning
deployment) is returned verbatim.

## Configuration

| Variable | Required | Meaning |
|---|---|---|
| `FOUNDRY_OPENAI_BASE_URL` | responses/chat modes | Full base URL ending in `/openai/v1/` (either `*.openai.azure.com` or `*.services.ai.azure.com` host). No `api-version` needed. |
| `FOUNDRY_API_KEY` | responses/chat modes | Foundry resource API key. |
| `FOUNDRY_PROJECT_ENDPOINT` | agents mode | Project endpoint: `https://<resource>.services.ai.azure.com/api/projects/<project>`. |
| `FOUNDRY_DEFAULT_DEPLOYMENT` | optional | Deployment used when `model` is omitted. |
| `FOUNDRY_TIMEOUT_SECONDS` | optional | OpenAI client timeout override (SDK default 600 s). |
| `MCP_CHATBOT_DATA_DIR` | optional | Conversation store dir (default `~/.mcp-chatbot/conversations`). |

No variable is read until the first call that needs it — the server starts and
lists tools with zero configuration. Config comes from the MCP client's `env`
block (preferred, since clients launch servers from an arbitrary cwd) or from
the repo-root `.env`, which the server loads by explicit path.

## Install

```powershell
cd mcp-chatbot
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Register with an MCP client

```json
{
  "mcpServers": {
    "mcp-chatbot": {
      "command": "C:\\Users\\rdpro\\Projects\\helpful-scripts\\mcp-chatbot\\.venv\\Scripts\\python.exe",
      "args": ["-m", "mcp_chatbot.server"],
      "env": {
        "FOUNDRY_OPENAI_BASE_URL": "https://your-resource.services.ai.azure.com/openai/v1/",
        "FOUNDRY_API_KEY": "your-key",
        "FOUNDRY_DEFAULT_DEPLOYMENT": "your-deployment"
      }
    }
  }
}
```

## Validate (offline — no deployment needed)

```powershell
python -m pytest    # 52 mocked tests, no network
python smoke.py     # spawns the real server over stdio -> "SMOKE OK: 4 tools"
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

## Follow-ups

- Migrate to mcp SDK 2.x once stable (FastMCP is renamed MCPServer there).
- Optional remote cleanup of agent versions/conversations on delete.
