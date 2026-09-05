# Repository guidance

Apply the global Codex working agreements for interaction, operating lanes,
delegation, implementation, validation, and delivery. This file adds project facts
and the timed-browser procedure. On an installation without global guidance, read
`agentic-ide-setup/profile/codex/AGENTS.md` before substantive work.

## Timed Browser Workflows

For live browser workflows with a countdown, such as fantasy mock drafts:

- Prepare a ranked action queue and fallbacks before entering the timed state.
- During a live turn, use one compact transaction: inspect only the minimum state, choose the first valid candidate, act immediately, and verify the result in the same transaction when possible.
- Do not perform exploratory DOM inspection, selector debugging, queue maintenance, or extended reasoning between reading the state and submitting the action. If the timer is short or the state is uncertain, submit the highest-ranked safe fallback immediately.
- Replenish the queue only during other participants' turns.
- Report a choice as manually made by Codex only after the UI confirms it. If the timer expires, label the result as site auto-drafted; a queued player is not evidence of a submitted pick.

## Project: mcp-chatbot

Stdio MCP server (`mcp-chatbot/`) chatting with Azure AI Foundry deployments;
the repo's first Python package (pyproject + pytest).

- Install: `cd mcp-chatbot; py -3 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ".[dev]"`
- Test: `.\.venv\Scripts\python.exe -m pytest` (offline; Azure clients mocked)
- Smoke: `.\.venv\Scripts\python.exe smoke.py` (boots the real server over stdio)
- Run: `.\.venv\Scripts\python.exe -m mcp_chatbot.server` (stdio; config via `FOUNDRY_*` env vars, read lazily)
- If pip hits the private `pkgs.dev.azure.com` index interactively, prefix with
  `$env:PIP_INDEX_URL = "https://pypi.org/simple"; $env:PIP_EXTRA_INDEX_URL = ""; $env:PIP_NO_INPUT = "1"`.

## Project: servicenow-client

Single-file ServiceNow REST client (`servicenow-client/servicenow_client.py`),
CLI + importable module, stdlib HTTP + python-dotenv only.

- Install: `cd servicenow-client; py -3 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ".[dev]"`
- Test: `.\.venv\Scripts\python.exe -m pytest` (offline; transport faked)
- Run: `.\.venv\Scripts\snow.exe <command>` or `python servicenow_client.py <command>` (config via `SERVICENOW_*` env vars, read lazily)
- Agent sessions should default to `SERVICENOW_READ_ONLY=true` and preview
  writes with `--dry-run`; `delete` additionally needs `SERVICENOW_ALLOW_DELETE=true` + `--force`.
- Same pip note as mcp-chatbot: if pip hits the private `pkgs.dev.azure.com`
  index, prefix with `$env:PIP_INDEX_URL = "https://pypi.org/simple"; $env:PIP_EXTRA_INDEX_URL = ""; $env:PIP_NO_INPUT = "1"`.

## Project: edge-pyodide

Single-file runner (`edge-pyodide/edge_pyodide.py`, CLI `edgepy`) that executes
Python inside Microsoft Edge (Pyodide over the DevTools Protocol); stdlib only,
no runtime dependencies, works fully offline from a vendored `vendor/` folder.

- Install: nothing for the tool itself. Tests: `cd edge-pyodide; python -m pytest`
  (pytest 9 is installed globally; or `py -3 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ".[dev]"`)
- Test: `python -m pytest` (offline; Edge, websocket, HTTP and registry seams faked)
- Vendor (online, once): `python edge_pyodide.py fetch --flavor full --pkg <name>` builds
  `edge-pyodide/vendor/` (gitignored, ~380 MB for full; `--flavor core` is 6 MiB, stdlib only)
- Run: `python edge_pyodide.py run script.py`, `run -m pkg.mod`, `run -c "..."`, `repl`,
  `doctor --live`; config via `EDGEPY_*` env vars read lazily (no .env loading on purpose)
- Live checks need Edge >= 137 on the machine; the offline suite never launches it.
- Same pip note as mcp-chatbot for the optional dev venv.

## Project: agent-browser

Single-file Playwright CLI (`agent-browser/agent_browser.py`, console script
`agent-browser`) that gives an AI agent a visible Edge/Chrome window through a
per-profile background daemon: JSON in and out, elements addressed by refs
from accessibility snapshots, sign-ins kept per profile.

- Install: `python -m pip install --user "playwright>=1.61,<2"` then
  `python -m pip install --user -e agent-browser` (same pip-index prefix as mcp-chatbot;
  no `playwright install`, the installed Edge/Chrome is used).
- Test: `cd agent-browser; python -m pytest` (offline; no browser, Playwright never imported).
- Live: `$env:AGENT_BROWSER_LIVE = "1"; python -m pytest -m live` (headless Edge against
  `tests/fixtures`, ~50 s) and `agent-browser doctor --live`.
- Run: `agent-browser goto URL`, `click e12`, `fill f2e5 "text"`, `wait --signed-in HOST`,
  `text`, `screenshot`, `stop`; config via `AGENT_BROWSER_*` env vars read lazily (no .env
  loading), policy via the human-written `%LOCALAPPDATA%\agent-browser\config.json`.
- Skill: the Claude Code skill lives in `agentic-ide-setup/profile/claude/skills/agent-browser/`
  and reaches `~/.claude/skills` through `Install-AgenticIdeSetup.ps1 -Components Claude`;
  `tests/test_skill.py` keeps it consistent with the parser. Do not add a copy under `.claude/skills`.
- Agent rules: one `agent-browser` command per Bash call (the shell guard denies chained
  print commands with secret words), never type credentials (the tool refuses those
  fields), refs only from the newest output.

## Final Principle

Act like a strong senior engineer who respects Rudy's time: investigate first, reason clearly, make focused changes, validate them, and surface the important tradeoffs.
