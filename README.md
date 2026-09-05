# helpful-scripts

## VS Code GLM orchestrator with Microsoft Foundry workers

`foundry-vscode-setup/` is an agent-ready handoff package for configuring VS Code's Local agent harness with a tool-capable local GLM coordinator and Microsoft Foundry-backed custom worker subagents. It includes model-provider templates, least-privilege custom-agent definitions, staged canaries, troubleshooting, and rollback guidance for a Copilot-disabled environment. See [foundry-vscode-setup/README.md](foundry-vscode-setup/README.md).

## Claude Code + Microsoft Foundry hybrid kit

`claude-foundry-hybrid/` provides a Windows/PowerShell launcher that keeps
Claude Code on native Microsoft Foundry Claude deployments while exposing
other Foundry deployments through an explicit MCP model bench. It also
contains an isolated, opt-in LiteLLM compatibility lab. See
[claude-foundry-hybrid/README.md](claude-foundry-hybrid/README.md).

## GitHub activity scanner

`github-activity-scanner/` holds a dependency-free, read-only Python tool
(`github_activity_scanner.py`) that takes a list of author emails and reports
their GitHub activity: every indexed commit (repo, dates, first message line,
additions/deletions) and every pull-request review by the matching account
(state + timestamp, so approvals are a spreadsheet filter away), written to
`commits.csv` and `reviews.csv`. All API access goes through the GitHub CLI
(`gh api`), so auth is just `gh auth login`; optional `--org`/`--repo`
scoping, otherwise it searches all of public GitHub. See
[github-activity-scanner/README.md](github-activity-scanner/README.md).

## GitHub keyword search

`github-keyword-search/` holds a dependency-free, read-only Python tool
(`github_keyword_search.py`) that walks a GitHub org's commit history —
messages and diffs, on each repo's default branch, in an optional date
window — for keywords and writes a single self-contained HTML report: repo,
commit URL, branch, commit SHA, commit message, committer, commit date,
matched keyword, and a highlighted snippet, with a live filter box. No
command-line arguments: org, repo-name filter, date range, and keywords are
all set by editing constants at the top of the script, then running it.
All API access goes through the GitHub CLI (`gh api`). See
[github-keyword-search/README.md](github-keyword-search/README.md).

## Codex token usage audit (Codex local clients only)

`codex-token-usage-audit/` holds a dependency-free, read-only Python tool
(`codex_token_usage_audit.py`) that reports retained local **Codex** token usage
by turn, model, reasoning effort, root task vs subagent, agent path,
project, and day. It calculates deltas from cumulative rollout counters, links
nested subagent trees, exports CSV or structured JSON, and optionally enriches
task titles from `state_5.sqlite` without modifying it. Credit figures are dated
standard-rate estimates, not billing truth. See
[codex-token-usage-audit/README.md](codex-token-usage-audit/README.md).

## Codex hook bytecode recovery

[`Repair-CodexHookBytecode.ps1`](codex-workflow/tools/Repair-CodexHookBytecode.ps1)
previews a narrowly verified cache-only integrity failure, then quarantines the
four approved files when explicitly applied. It checks release bindings, path
boundaries, preserved bytes, doctor health, and the installed self-test. See the
[recovery runbook](codex-workflow/tools/HOOK_BYTECODE_RECOVERY.md).

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

## agent-browser - a visible Edge/Chrome window for AI agents

`agent-browser/` holds a single-file Playwright CLI (`agent_browser.py`,
console script `agent-browser`) that lets a Claude Code session on a model
without built-in browser tools drive a real, visible Edge or Chrome window
through the Bash tool: `goto URL` returns the page as element refs, `click e12`
/ `fill f2e5 "text"` / `select` / `press` act on them, and every result is JSON
with a structured error and exit code the model can branch on. A per-profile
background daemon keeps the window, its cookies, and the refs alive between
commands, so a human signs in once (SSO/MFA in the window) and the agent
continues. Password fields are refused in code, only `http(s)` opens, page text
never reaches a hint. The Claude Code skill ships in the `agentic-ide-setup`
profile. See [agent-browser/README.md](agent-browser/README.md).

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

## Claude subagent model-evaluation ladder

`hooks/claude-model-evaluation/` documents how to build, install, validate,
roll out, and roll back a portable Claude Code hook that uses the explicit
Haiku -> Sonnet -> Opus preference and escalation order. The folder also
contains a ready-to-paste implementation handoff for Claude. See
[hooks/claude-model-evaluation/README.md](hooks/claude-model-evaluation/README.md).

## Resume .docx tools

`resume-docx-tools/` holds two Python tools for job-application hygiene:
`scrub_docx_metadata.py` (stdlib-only) reports and strips corporate metadata
from Word files — MSIP sensitivity labels, classification tags, stale titles,
attached-template paths that leak a local username, and it flags
gateway-rewritten (urldefense/safelinks) hyperlinks; `build_resume_docx.py`
(python-docx) generates a clean, ATS-safe .docx resume from a Markdown source
— plain paragraphs and real heading styles instead of the stock template's
layout table and content controls, with a `--check` mode proving a plain
parser gets every line back. See
[resume-docx-tools/README.md](resume-docx-tools/README.md).

## Global load-bearing canon

`global-load-bearing-canon/` installs one short block of engineering-judgment
guidance into the *user-level* instruction files that Codex and Claude Code
load for every repository, and documents how VS Code/GitHub Copilot Chat
discovers the same text without creating a duplicate source. It contains the
canonical body, a reproduction guide with a self-checking verification script,
and a handoff prompt for Claude Code. See
[global-load-bearing-canon/README.md](global-load-bearing-canon/README.md).

**Do not edit `load-bearing-canon.md` casually.** Its normalized SHA-256 is
pinned in three places, and the installed copies in `~/.codex/AGENTS.md` and
`~/.claude/CLAUDE.md` are compared against it byte-for-byte. Changing the body
— even adding a comment — breaks every pin and puts both global files out of
sync until they are reinstalled. Update the body, all three pins, and both
installed files together, or not at all.
