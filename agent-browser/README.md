# agent-browser

A modular CLI, [`agent_browser.py`](agent_browser.py) plus `agent_browser_core/`, that gives an AI agent working through a
shell a real, visible Microsoft Edge (or Chrome) window. It exists for Claude Code sessions on
models that do not get the built-in browser tools (Haiku, Sonnet, Opus 4.6): the model runs
`agent-browser goto URL`, reads the page back as a list of element refs, and acts on those refs
one command at a time. Playwright does the driving; the model only ever sees JSON.

- One command per step, JSON on stdout, a structured error on stderr with an exit code the model can branch on.
- A per-profile background daemon keeps the window, its cookies, and the element refs alive between commands.
  Sign in once in the window; the profile keeps the session.
- Elements are addressed by refs (`e12`, `f2e5` inside an iframe) from Playwright's own accessibility snapshot,
  filtered to what an agent can act on and capped so it never overflows the Bash tool's output limit.
- Guardrails live in code: password and one-time-code fields (and fields the tool cannot inspect) are refused
  for typing and as an `eval --ref` target, only `http(s)` URLs open, uploads
  come only from the working folder or the profile folder, page text never reaches a hint, and URLs are
  redacted before they are printed or logged.

## Quickstart

```powershell
$env:PIP_INDEX_URL = "https://pypi.org/simple"; $env:PIP_EXTRA_INDEX_URL = ""; $env:PIP_NO_INPUT = "1"
python -m pip install --user "playwright>=1.61,<2"   # Edge or Chrome is used as installed; no `playwright install`
python -m pip install --user -e agent-browser           # console script: agent-browser
agent-browser doctor                                    # Python, Playwright, browser, policies, Job Object, skill
agent-browser goto https://example.com                  # opens the window and prints the page as refs
agent-browser click e5
agent-browser stop                                      # releases this session; sign-in stays on disk
```

Install the Claude Code skill with the machine profile:
`.\agentic-ide-setup\scripts\Install-AgenticIdeSetup.ps1 -Components Claude -Apply` places
`~/.claude/skills/agent-browser/SKILL.md`. Every Claude Code session on the machine then knows the tool.

`python agent_browser.py <verb>` works identically without the console script.

`python -m agent_browser` also works. The interfaces are private to helpful-scripts.
For stdio MCP install `agent-browser[mcp]` (`mcp>=1.29,<2`), then run `agent-browser-mcp`
or `python -m agent_browser_core.mcp_server`. The 18 typed tools share the dispatcher;
`browser_commit` and `browser_eval` are distinct caller approval targets.
Use one explicit MCP `session_id` throughout the task. Installing repository templates
is a separate step; editing them does not update an already-installed caller policy.

## Commands

Global flags, before or after the verb: `--profile NAME`, `--tab TAB` (target a specific tab by stable id `t1`, `t2` or 1-based index without switching the active tab), `--timeout SECONDS`, `--snapshot` (full snapshot after an
action), `--max-bytes N`, `--session ID`, repeatable `--expect CHECK`, and `--version`.

The detailed [agent interface reference](../agentic-ide-setup/profile/claude/skills/agent-browser/references/interfaces.md)
covers exact role/name or label targets, UTF-8 text/JS file inputs, form JSON and MCP.
`fill-form --file fields.json` preflights every field, applies credential guards,
supports fill/select/check/uncheck, never submits, and reports partial completion.
All fields stay on the original tab even if an earlier field opens a popup;
closing that tab stops the remaining field operations.
`screenshot --annotate` uses temporary noninteractive ref labels removed in `finally`.
`table [REF]` requires a unique table/grid and returns ordered columns/rendered rows
with refs/cells, without pagination. `check URL` uses a temporary 390x844 loopback
diagnostics page and preserves the working tab; `check REF` retains its behavior.

`api URL` uses the current tab's exact origin and browser cookies. Writes require
`commit api URL --method POST --json-file payload.json`. No redirects or retries;
profile host policy is checked before dispatch. Authentication and redirect response
headers are omitted. Response byte limits are post-download checks: Playwright
buffers the response, so they do not impose a hard memory bound. Guarded files
transfer binary attachments without oversized JSON. Relative transfer paths resolve
against the caller's working directory. Cookies/auth headers stay in
the daemon. `SERVICENOW_AUTH=browser` in servicenow-client needs both packages,
matching instance origin and a usable same-origin session key; absent/expired or
unsupported authentication returns unsupported-auth or a human handoff.

`run sn/add-work-note --number INC0010023 --text-file note.txt` previews the built-in
classic-form recipe. `commit run` verifies record identity, submits once, reloads
the same record and checks persistence. Unfamiliar layouts/drafts require handoff.
Applicable ServiceNow site notes appear once per session/host. Real instance write
qualification requires a separately authorized test record.

| Verb | What it does |
|---|---|
| `start [--url U] [--browser msedge\|chrome] [--exe P] [--headless] [--window-size W,H]` | Open the window for a profile. Idempotent: never a second window. |
| `stop [--all] [--force]` | Release only this session; all/force explicitly affect the shared profile. Cookies stay. |
| `status [--all]` | Running, pid, tabs, uptime, `stale_code`. Always exit 0. |
| `goto URL [--wait load\|domcontentloaded] [--new-tab] [--discard-changes]` | Navigate (starts the browser if needed) and return the page. |
| `snapshot [--full] [--find TEXT] [--ref REF]` | The page as refs. `--find` keeps matching lines plus parents. |
| `click REF [--double] [--right] [--accept-dialog] [--dialog-text T] [--discard-changes]` | Click. Popups become the current tab. |
| `fill REF TEXT [--enter]` | Replace a field's text. Refuses password and code fields. |
| `type REF TEXT [--delay MS] [--append] [--enter]` | Type key by key (typeahead fields); clears first unless `--append`. |
| `press KEY [REF]` | `Enter`, `Tab`, `Escape`, `Control+a`. Only those three on a secret field. |
| `select REF VALUE...` | Pick options by label or value; a bad label fails at once with the options listed. |
| `check REF` / `uncheck REF` / `hover REF` / `scroll [REF] [--down PX\|--up PX]` | Idempotent element actions. |
| `text [REF] [--max-chars N]` | Readable text of the page (every frame) or of one element. |
| `value REF` | Current value of a field (masked on secret fields). |
| `screenshot [REF] [--path F.png] [--full-page]` | PNG under the profile's `shots/` (or a path under the cwd). |
| `wait [--url-contains S] [--text S] [--ref R] [--selector S] [--signed-in HOST] [--seconds N] [--timeout S]` | Conditions AND-ed; cooperative polling; inspect the receipt after timeout before deciding what to do next. |
| `back` / `reload` / `tabs` / `tab TAB [--close]` | Navigation and tabs (`TAB` is a stable id `t1`, `t2` or 1-based index). Pass `--tab TAB` on any command to drive tabs in parallel without switching focus. Closing the last tab is refused. |
| `upload REF FILE...` / `downloads` | Attach local files; save and list files the page downloaded. |
| `eval JS [--ref REF] [--frame NAME]` | Run JavaScript. Off when `config.json` says `"eval": false`. Refuses a credential field as `--ref`; page and `--frame` eval are not guarded. |
| `needs-human [--signed-in HOST]` | Focus/flash for human input; returns a durable handoff ID. |
| `doctor [--live]` | Preflight rows; `--live` boots a headless browser, checks refs, clicks, stops. |
| `clean [--profile P] [--purge-profile P] [--dry-run]` | Stale sessions/orphans and old download staging files. Evidence stays until explicitly cleared. |

### Reading a snapshot

```
- link "ServiceNow Home" [ref=e3]
- searchbox "Filter navigator" [ref=e5]
- iframe "gsft_main" [ref=e12] (children f2e...):
  - heading "Incident INC0010023" [level=2]
  - button "Update" [ref=f2e3]
  - textbox "Number" [disabled] [ref=f2e8]: INC0010023
  - combobox "Priority" [ref=f2e14]
  - textbox "Work notes" [ref=f2e20]
```

Buttons, links, fields, checkboxes, tabs, menu items and iframes keep their ref; headings, dialogs and named
regions are kept as context without a ref; everything else is dropped. Iframe lines show the frame's name and the
prefix its children use. A ref stays bound to the same element until its frame navigates; then the tool answers
`stale_ref` and the next `snapshot` renumbers.

Actions that do not navigate return a compact envelope: `target_line`, `value_after`, and a `changes` diff. When the
page navigated, a dialog was handled, or `--snapshot` was passed, the full snapshot comes back instead.

## Output contract (for agent authors)

Runtime replies are compact single-line JSON with `contract_version: 1`. The complete
encoded CLI/socket reply, including escaping/newline, is bounded to 24,000 UTF-8 bytes.
MCP text and structured output are bounded together. Overflow is preserved under an
immutable `output_id`; read `output --id ID --offset 0` and follow `next_offset` until
null. Later commands cannot replace that artifact; `last_output.json` remains available.
`effect` and per-command console/page/network/HTTP error counts describe observations,
not business success. Repeat `--expect` for AND checks under one action deadline:
`text:Saved`, `gone:e12`, `url:changed`, `response:POST **/api/items:2xx`.
Checks are installed before the action. Recipes and wait use their own conditions.
Waits and action checks reject success first observed after their original deadline.
Wait observation/capture errors finish the receipt and remove the pending operation.

Inspect `state --command ID` or `journal --command ID` after a failed check/interruption.
Receipts distinguish completed, failed and unknown. `executed` means attempted, not
business success. Interrupted writes are never automatically replayed.

Journal, masked screenshots, overflow JSON and raw API response files remain until
explicitly cleared. The default quota is 1 GiB/profile (`evidence_max_bytes`, minimum
65536); reaching it blocks new audited mutations/captures while preserving inspection
and explicit clear. `commit journal --clear` clears this session's evidence only.
Journal pages return `next_offset`; state returns `command_next_offset`. Pass that
value with `--offset`. A large receipt returns `r` plus its command ID as `output_id`;
use `output --id ID --offset 0` to read the terminal receipt directly from SQLite.
This read creates no evidence files and works at quota. The distinct receipt ID
cannot be confused with an earlier pending-result overflow artifact.
Entered text, JS, bodies and credentials are omitted from SQLite receipt arguments.
Commit UI operations capture before/after screenshots. API response artifacts contain
application data and count toward quota; transient request staging files are removed.

Incompatible wire protocols require an explicit shared-profile restart. Implementation
hashes include every package module. A restart may affect other sessions; it never
replays a command. Explicit help/version remain human-readable.

Status/doctor health probes use a short diagnostic deadline. Normal commands validate
the recorded protocol and send one actual request under their own deadline, without
preliminary health requests or automatic action retries. Reusing a running profile
with start gives its health response the requested start deadline.

- **Success**: JSON on stdout, exit `0`. Page-derived keys are listed in `untrusted`; hints never contain page text.
  Metadata keys come before `snapshot`, so a truncated read still carries `url`, `title`, `truncated`, `note`.
- **Failure**: `{"error": {"class", "http_status": null, "message", "hint", "details"?}}` on stderr, stdout empty.
- Exit `2` = fix the call: `usage`, `validation`, `ambiguous_profile`, `ref_not_found`, `stale_ref`, `not_found`,
  `ambiguous`, `action_timeout`, `action_failed`, `guarded`, `dialog`, `unsaved_changes`.
- Exit `1` = fix the environment or start the browser: `config`, `playwright_missing`, `browser_not_found`,
  `edge_policy`, `browser_launch`, `profile_in_use`, `not_running`, `browser_closed`, `daemon`, `daemon_unresponsive`.
- Exit `124` = `timeout`: inspect the receipt before another action; the action may already have run.
  Exit `130` = interrupted.
- Every `hint` is a literal next command rendered with the profile in use.

## Configuration

Unconfigured profiles retain full `commit` access and eval enabled. Per-profile
overrides live under `profiles` in config, for example
`{"profiles":{"snow-read":{"permission":"read","eval":false}}}`.
`read` permits observation/navigation, `act` adds ordinary interaction, and `commit`
adds declared submissions, write APIs, mutating recipes and arbitrary eval.
`uploads: false` blocks file uploads and file-backed API bodies; `upload_roots`
constrains permitted paths when enabled. Both capabilities remain enabled by default.
Generic UI actions can autosave. Approval-sensitive workflows must disable eval
and use declared commits under caller approval rules. These are cooperative
controls; writable config/session files cannot sandbox a hostile local process.

Real environment variables, read lazily; no `.env` loading (the tool must run bare).

| Variable | Meaning |
|---|---|
| `AGENT_BROWSER_HOME` | State root (default `%LOCALAPPDATA%\agent-browser`, POSIX `~/.agent-browser`). Keep it short: profile paths over 180 chars are refused. |
| `AGENT_BROWSER_PROFILE` | Default `--profile`. Without either, always `default`. |
| `AGENT_BROWSER_BROWSER` / `AGENT_BROWSER_EXE` | `msedge` (default) or `chrome`; an explicit executable. |
| `AGENT_BROWSER_HEADLESS` | `true` = no window (tests). A human cannot sign in headless. |
| `AGENT_BROWSER_TIMEOUT_SECONDS` | Default action timeout (15). |
| `AGENT_BROWSER_MAX_BYTES` / `AGENT_BROWSER_MAX_CHARS` | Snapshot byte budget (16000); `text` character cap (20000). |
| `AGENT_BROWSER_AUTOSTART` | `false` stops `goto` from starting a browser on its own. |
| `AGENT_BROWSER_LAUNCHER` | `popen` (default) or `wmi`: how the daemon is detached from the shell. |
| `AGENT_BROWSER_IDLE_SECONDS` | Daemon stops after this much inactivity (default 0 = never). |
| `AGENT_BROWSER_LIVE` | `1` enables the live pytest suite. |

`%LOCALAPPDATA%\agent-browser\config.json` is a human-written policy file the tool never creates:
`{"eval": false, "allowed_hosts": ["dev12345.service-now.com"], "upload_roots": ["C:/exports"]}`. With
`allowed_hosts`, `goto` refuses other hosts and a page that wanders off the list gets `off_allowlist: true` with only
`back`, `goto`, `snapshot`, `text`, `tabs`, `tab`, and `stop` allowed until it is back. The daemon reads `config.json`
when it starts, so after editing it run `stop` then `start`. `eval` must be a JSON boolean; anything else is a
`config` error rather than a silent default. An absent `allowed_hosts` leaves hosts
unrestricted; a present list must contain at least one nonblank host. An empty
effective list is a configuration error.

## Session model

```
%LOCALAPPDATA%\agent-browser\profiles\<name>\
  user-data\        the Chromium profile: cookies, local storage, sign-ins (never your daily Edge profile)
  session.json      pid, port, token, browser, heartbeat, busy, code hash (written by the daemon)
  session.lock      held by the daemon for its lifetime; the OS releases it on death (liveness truth)
  launch.json       the settings the client handed the daemon
  daemon.log        command/status/timing diagnostics; no argument values or exception bodies
  journal.sqlite3   redacted receipts, evidence references and durable human handoffs
  last_snapshot.txt the manual session's last filtered snapshot
  downloads\  shots\  outputs\
  sessions\<hash>\   other sessions' snapshots, downloads, screenshots and outputs
```

One daemon, one browser window, one loopback port per profile. `start` is idempotent; a stale session (daemon died)
is cleaned and relaunched on the same profile folder, so the sign-in survives. Two profiles run side by side with
separate cookies; that is opt-in through `--profile`. Selection is explicit profile,
environment default, then `default`; there is no attachment to whichever profile runs.
The `_doctor` profile is reserved for internal diagnostics and cannot be selected by callers.

Session identity is explicit `--session`, CODEX_THREAD_ID, CLAUDE_CODE_SESSION_ID,
then stable `manual`. Tabs, refs, receipts, outputs and downloads belong to that ID;
popups inherit their opener. Cookies/storage remain shared. IDs route work
cooperatively and do not authenticate callers. Normal stop releases one session;
select another ID explicitly to stop it. All/force stops affect the shared profile.

Dialogs are answered synchronously by the daemon: `alert` is accepted; `confirm`/`prompt` during a command are
accepted only with `--accept-dialog` (otherwise the command fails with `dialog`); `beforeunload` during a command
keeps the page and fails with `unsaved_changes` unless `--discard-changes`; between commands a `beforeunload`
raised by a human navigation is accepted.

## Security posture

- Playwright talks to the browser over a pipe. No DevTools TCP port is opened on the signed-in profile.
- The daemon listens on `127.0.0.1` only, behind a per-session token stored in `session.json` under your user profile;
  a wrong token is dropped without a reply, a request has 5 s to arrive and a 2 MiB input budget.
- The tool never types into password, one-time-code, or credential-looking fields, and no flag overrides that. A
  field it cannot inspect counts as one (`action_failed`; retry after a fresh `snapshot`). Open shadow roots are
  inspected; closed ones are invisible to the tool. `eval` is arbitrary page JavaScript: it refuses a credential
  field as its `--ref` target, but page and `--frame` eval can still read or set any field, so approval-sensitive
  profiles set `"eval": false`. The human types in the window and the agent waits with `wait --signed-in`.
- `goto` opens only `http`/`https` (and `about:blank`); `edge://`, `file:`, `javascript:` are refused.
- Uploads and `--path` targets must sit under the current folder, the profile's `downloads`/`shots`, or
  `upload_roots`; UNC paths, hidden folders, and key files are refused.
- Page-derived strings are sanitized (control and bidi characters stripped, lines capped) and only ever appear
  under keys named in `untrusted`; hints are static templates. URLs are redacted (`code`, `token`, `id_token`,
  `SAMLResponse`, ... masked, fragment dropped) in output and logs.
- Environment variables, profile policy, caller allowlists and the socket credential
  are cooperative controls. A hostile local process can rewrite config or call the
  socket directly. Eval remains enabled by default; approval-sensitive workflows
  disable it and use declared commits under caller authorization rules. Ordinary UI
  actions can still autosave or submit; these controls do not prove a submission gate.

## Host matrix (detachment)

The daemon must outlive the shell that started it. Measured with a probe that spawns detached children and checks
them from the next Bash tool call.

| Host | Shell python in a Job? | Limit flags | Plain detached child | `wmi` launcher |
|---|---|---|---|---|
| Claude Code Bash tool, Windows 11, 2026-09-01 | yes | `0x0` (no kill-on-close) | survives, stays in the same Job | survives, lands in WmiPrvSE's Job (`0x1800`) |
| Claude Desktop / VS Code extension | not yet measured | | | |

`doctor` prints the `job_object` row for the shell you run it from; if it ever shows `kill_on_job_close`, set
`AGENT_BROWSER_LAUNCHER=wmi`.

## Validate (offline, no browser)

```powershell
cd agent-browser
python -m pytest        # seams, pure snapshot layer, security policy, CLI contract, skill consistency
```

## Live smoke test

Source, CI, installation, local fixture evidence and real instance qualification are
separate. The live suite uses loopback fixtures only. Real ServiceNow writes require
a separately authorized test record; the manual record-edit step below is conditional
on that authorization.

```powershell
$env:AGENT_BROWSER_LIVE = "1"; python -m pytest -m live     # real headless Edge against tests/fixtures (~40 s)
agent-browser doctor --live                                  # boots a headless browser, checks refs, clicks, stops
```

Then, by hand, from a Claude Code session:

1. Bash call 1: `agent-browser start --profile smoke`; Bash call 2: `agent-browser status --profile smoke` must say
   `running: true`.
2. `agent-browser goto https://<your instance>.service-now.com/` and sign in in the window; then
   `agent-browser wait --signed-in <your instance>.service-now.com`.
3. Open a record, fill a work note inside `gsft_main`, click Update, `screenshot`, `stop`, `start`: still signed in.

## How it's built

Small modules behind a thin entry point; every OS edge is a module-level seam the offline tests replace (`_spawn_daemon`,
`_try_lock`, `_pid_alive`, `_kill_tree`, `_processes_using`, `_registry_value`, `_playwright_factory`).

| Section | Role |
|---|---|
| Constants | Timeouts, byte budgets, role sets for the snapshot filter, secret-field and sign-in heuristics, error classes, the static hint table. |
| Config helpers | `_tag()` rides `error_class`/`hint`/`details` on builtin exceptions; lazy env reads; `ProfilePaths`; the human-written `config.json`. |
| Text hygiene | `sanitize_text`, `redact_url`, `safe_name`. |
| Browser discovery | `find_browser` (explicit > env > PATH > known folders > App Paths), version from the install folder, Edge and Chrome policy decoding. |
| Process seams | detached spawn with the breakaway retry and the WMI fallback, lock files, Job Object facts, CIM process listing by full profile path. |
| Session store | atomic `session.json`, lock liveness, explicit profile/session selection and protocol compatibility. |
| Client protocol | one JSON line over a loopback socket; remote errors re-raised through `_tag()` so `main()` maps exit codes the same way for local and remote failures. |
| Snapshot pure layer | `parse_ai_snapshot`, `frame_prefixes`, `filter_snapshot`, `budget_lines`, `diff_lines`: pure functions over Playwright's ai-mode text, tested against captured real output. |
| Daemon | `Daemon` owns one persistent context; context-level dialog/download/page listeners; per-frame navigation counters that turn a ref from a navigated frame into `stale_ref`; heal-once re-snapshot when a scoped snapshot reset Playwright's ref map; settle across frames; one handler per verb. |
| Client verbs | `start`, session-scoped `stop`, `status`, `clean`, `doctor`, human handoff and typed optional MCP. |
| CLI | argparse with a shared parent parser, MSYS argument de-mangling, UTF-8 stdout on pipes, `_emit_error`, exit mapping. |

Why a daemon and not a fresh Playwright connection per command: Playwright numbers refs inside the injected script of
one connection, so a new connection renumbers the page and the model's `e7` can silently become a different element
after any DOM change. One long-lived connection keeps a ref bound to its element until its frame navigates, keeps
dialog and download listeners live between commands, and avoids an unauthenticated DevTools port on a signed-in profile.

Verified against Playwright 1.61 on this machine: `aria_snapshot(mode="ai")` puts refs on every visible node that
receives pointer events (headings and generic divs included, hence the role-based filter); refs inside a frame carry
`f<N>` prefixes and resolve through `page.locator("aria-ref=f1e4")`; a scoped snapshot empties the ref map and a full
one refills it with the same numbers; a frame navigation reuses numbers (hence the counters); a page reload raises
"Invalid frame in aria-ref selector"; `select_option` blocks on a bad label (hence options are read first);
`press_sequentially` appends (hence `type` clears first); a context-level dialog listener catches timer dialogs.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `not_running` on every command | No daemon for that profile. `agent-browser start --profile <p>`; `goto` starts one by itself unless `AGENT_BROWSER_AUTOSTART=false`. |
| `protocol_mismatch` | Explicitly restart the shared profile after preserving every session's work. |
| `stale_ref` / `ref_not_found` | The page or its frame navigated, or a scoped snapshot was taken. `agent-browser snapshot` and pick again. |
| `guarded` on `fill` | The field looks like a password or code field. The human types it in the window; then `wait --signed-in <host>`. |
| `action_failed` "Could not confirm the field…" | The credential check could not inspect the field (page navigating, frame detached). Take a fresh `snapshot` and retry; give `press` a ref if a press without one keeps failing. |
| `unsaved_changes` | The page has edits. Click its Save/Update button or re-run with `--discard-changes`. |
| `Profile path is too long` (class `config`) | Set `AGENT_BROWSER_HOME` to a short path such as `C:\ab`. |
| `browser_launch` with a log tail | Read `daemon.log`; `agent-browser doctor` decodes Edge and Chrome policies (`DeveloperToolsAvailability=2`, `ForceEphemeralProfiles`). Try `--browser chrome`. |
| `profile_in_use` | A stray browser holds the profile folder. `agent-browser clean --profile <p>`. |
| The window is not visible | Check the taskbar; use `agent-browser needs-human` for an authorized human handoff. |
| Output looks cut off | The Bash tool caps output at 30,000 chars; the tool keeps responses under 24 KB and writes the full snapshot to `last_snapshot.txt` (`snapshot_file`). |
| Git Bash turned `/incident.do` into `C:/Program Files/Git/incident.do` | The tool de-mangles arguments when `MSYSTEM` is set and adds a `note`; prefer host names and never start an argument with `/`. |
| A Bash call was denied by the shell guard | The user's PreToolUse guard denies statements starting with `echo`, `cat`, `type`, `set` when the command mentions a secret word. One `agent-browser` command per call, nothing before it. |
| `stale_code: true` in `status` | Package implementation changed; explicitly stop/restart the shared profile. |
| `doctor` shows `kill_on_job_close` | Your shell's Job kills children on close. `AGENT_BROWSER_LAUNCHER=wmi`. |
| pip hits `pkgs.dev.azure.com` | Prefix with `$env:PIP_INDEX_URL = "https://pypi.org/simple"; $env:PIP_EXTRA_INDEX_URL = ""`. |

## Follow-ups

- Measure the host matrix for Claude Desktop and the VS Code extension, and after the host quits.
- Console capture (`console` verb) and network summaries.
- A Windows named pipe with an owner-only DACL instead of the token-gated loopback socket.
- Cross-origin (OOPIF) iframe prefix stability across snapshots is not asserted by the live suite yet.
