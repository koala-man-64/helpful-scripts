# helpful-scripts

## Meeting Booking Agent design

`power-app-meeting-booking/` holds a single self-contained HTML design and
implementation plan for a purely conversational Power Apps canvas app that
books Microsoft 365 meetings on behalf of executives through a Copilot Studio
orchestrator with child agents: architecture, identity model, interface
contracts between the Power App developer and the Copilot developer, an
8-week phased plan with owners, test plan, risks and sources — plus a
Markdown edition (`meeting-booking-agent-design.md`) to hand to an agent. See
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
