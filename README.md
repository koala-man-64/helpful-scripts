# helpful-scripts

## Codex token usage audit (Codex local clients only)

`codex-token-usage-audit/` holds a dependency-free, read-only Python tool
(`codex_token_usage_audit.py`) that reports retained local **Codex** token usage
by turn, model, reasoning effort, root task vs subagent, agent path,
project, and day. It calculates deltas from cumulative rollout counters, links
nested subagent trees, exports CSV or structured JSON, and optionally enriches
task titles from `state_5.sqlite` without modifying it. Credit figures are dated
standard-rate estimates, not billing truth. See
[codex-token-usage-audit/README.md](codex-token-usage-audit/README.md).

## Claude Code token audit (Claude Code only)

`claude-code-token-audit/` holds a single-file, dependency-free Python tool
(`claude_code_token_audit.py`) that reports where your **Claude Code** tokens
go: by model, reasoning effort, main conversation vs Agent-tool subagents vs
Workflow agents, subagent type, skill, session, project, and day, with
API-list-price cost estimates. It parses the local transcripts Claude Code
writes to `~/.claude/projects/` and de-duplicates the multi-line-per-response
format correctly. **Specific to Anthropic's Claude Code** — it does not apply
to Codex, Copilot, Cursor, the claude.ai web app, or direct API usage. See
[claude-code-token-audit/README.md](claude-code-token-audit/README.md).

## Meeting Booking Agent design

`power-app-meeting-booking/` holds a single self-contained HTML design and
implementation plan for a purely conversational Power Apps canvas app that
books Microsoft 365 meetings on behalf of executives through a Copilot Studio
orchestrator with child agents: architecture, identity model, interface
contracts between the Power App developer and the Copilot developer, an
8-week phased plan with owners, test plan, risks and sources — plus a
Markdown edition (`meeting-booking-agent-design.md`) to hand to an agent and
a diagram-first companion (`meeting-booking-agent-flows.md`) with fifteen
Mermaid diagrams covering every touchpoint and resource. See
[power-app-meeting-booking/README.md](power-app-meeting-booking/README.md).

## edgepy - Python inside Edge, no install

`edge-pyodide/` holds a single-file tool (`edge_pyodide.py`, CLI `edgepy`) that
runs Python scripts, modules, and a REPL inside Microsoft Edge via Pyodide
(CPython on WebAssembly) driven over the DevTools Protocol - no pip, no venv,
and fully offline once a vendor folder (Pyodide distribution + pure-Python
wheels, built by `edgepy fetch` on an online machine) is copied in. Byte-exact
stdout/stderr streaming, real exit codes, numpy/pandas from the bundled
distribution, local folders mounted into the sandbox. See
[edge-pyodide/README.md](edge-pyodide/README.md).

## ServiceNow client

`servicenow-client/` holds a single-file ServiceNow REST client
(`servicenow_client.py`) usable as a CLI (`snow get INC0010023`, JSON to
stdout) or as an importable module — create/update records, add comments and
work notes, query with canned filters, read journal and audit history, manage
attachments, and look up users/groups/choices/schema. Built for AI-agent use:
structured error envelopes, deterministic exit codes, and write guardrails
(read-only mode, dry-run, gated delete). See
[servicenow-client/README.md](servicenow-client/README.md).

## MCP chatbot server

`mcp-chatbot/` is a stdio MCP server that chats with Azure AI Foundry model
deployments (Responses API, Chat Completions, or Foundry agents), with
persistent conversations and file attachments. See
[mcp-chatbot/README.md](mcp-chatbot/README.md) for setup and usage.

## Discover activity downloader/uploader

`discover_activity_to_adls.py` automates:

1. Open `https://www.discover.com/credit-cards/`
2. Click **Log In**
3. Fill username/password from `.env`
4. Submit login
5. Click **View Activity & Statements**
6. Click **Download**
7. Choose **CSV** in download options
8. Download CSV
9. Upload CSV to ADLS

### Install

```bash
pip install playwright python-dotenv azure-storage-file-datalake
playwright install chromium
```

### `.env` example

```bash
DISCOVER_USERNAME=your_discover_username
DISCOVER_PASSWORD=your_discover_password
ADLS_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net
ADLS_FILE_SYSTEM=your-container
ADLS_DIRECTORY=optional/subfolder
ADLS_TARGET_FILENAME=optional-fixed-name.csv
```

### Run

```bash
python discover_activity_to_adls.py
```

> Note: If Discover prompts for MFA/CAPTCHA, complete it manually in the opened browser window.
