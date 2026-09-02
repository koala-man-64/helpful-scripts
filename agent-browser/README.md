# agent-browser

A single-file CLI, [`agent_browser.py`](agent_browser.py), that gives an AI agent working through a
shell a real, visible Microsoft Edge (or Chrome) window. It exists for Claude Code sessions on
models that do not get the built-in browser tools (Haiku, Sonnet, Opus 4.6): the model runs
`agent-browser goto URL`, reads the page back as a list of element refs, and acts on those refs
one command at a time. Playwright does the driving; the model only ever sees JSON.

- One command per step, JSON on stdout, a structured error on stderr with an exit code the model can branch on.
- A per-profile background daemon keeps the window, its cookies, and the element refs alive between commands.
  Sign in once in the window; the profile keeps the session.
- Elements are addressed by refs (`e12`, `f2e5` inside an iframe) from Playwright's own accessibility snapshot,
  filtered to what an agent can act on and capped so it never overflows the Bash tool's output limit.
- Guardrails live in code: password and one-time-code fields are refused, only `http(s)` URLs open, uploads
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
agent-browser stop                                      # closes the window; the sign-in stays on disk
```

Install the Claude Code skill with the machine profile:
`.\agentic-ide-setup\scripts\Install-AgenticIdeSetup.ps1 -Components Claude -Apply` places
`~/.claude/skills/agent-browser/SKILL.md`. Every Claude Code session on the machine then knows the tool.

`python agent_browser.py <verb>` works identically without the console script.

## Commands

Global flags, before or after the verb: `--profile NAME`, `--timeout SECONDS`, `--snapshot` (full snapshot after an
action), `--max-bytes N`, `--verbose`, `--version`.

| Verb | What it does |
|---|---|
| `start [--url U] [--browser msedge\|chrome] [--exe P] [--headless] [--window-size W,H]` | Open the window for a profile. Idempotent: never a second window. |
| `stop [--all] [--force]` | Close the window (unsaved page edits are discarded); cookies stay. Exit 0 when nothing runs. |
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
| `wait [--url-contains S] [--text S] [--ref R] [--selector S] [--signed-in HOST] [--seconds N] [--timeout S]` | Conditions AND-ed; max 100 s per call; exit 124 says "run it again". |
| `back` / `reload` / `tabs` / `tab N [--close]` | Navigation and tabs. Closing the last tab is refused. |
| `upload REF FILE...` / `downloads` | Attach local files; save and list files the page downloaded. |
| `eval JS [--ref REF] [--frame NAME]` | Run JavaScript. Off when `config.json` says `"eval": false`. |
| `focus` | Bring the window to the front (best effort, Windows). |
| `doctor [--live]` | Preflight rows; `--live` boots a headless browser, checks refs, clicks, stops. |
| `clean [--profile P] [--purge-profile P] [--dry-run]` | Stale sessions, orphaned browsers, artifacts older than 7 days. |

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

- **Success**: JSON on stdout, exit `0`. Page-derived keys are listed in `untrusted`; hints never contain page text.
  Metadata keys come before `snapshot`, so a truncated read still carries `url`, `title`, `truncated`, `note`.
- **Failure**: `{"error": {"class", "http_status": null, "message", "hint", "details"?}}` on stderr, stdout empty.
- Exit `2` = fix the call: `usage`, `validation`, `ambiguous_profile`, `ref_not_found`, `stale_ref`, `not_found`,
  `ambiguous`, `action_timeout`, `action_failed`, `guarded`, `dialog`, `unsaved_changes`.
- Exit `1` = fix the environment or start the browser: `config`, `playwright_missing`, `browser_not_found`,
  `edge_policy`, `browser_launch`, `profile_in_use`, `not_running`, `browser_closed`, `daemon`, `daemon_unresponsive`.
- Exit `124` = `timeout`: a `wait` deadline or the daemon not answering; run the same command again.
  Exit `130` = interrupted.
- Every `hint` is a literal next command rendered with the profile in use.

## Configuration

Real environment variables, read lazily; no `.env` loading (the tool must run bare).

| Variable | Meaning |
|---|---|
| `AGENT_BROWSER_HOME` | State root (default `%LOCALAPPDATA%\agent-browser`, POSIX `~/.agent-browser`). Keep it short: profile paths over 180 chars are refused. |
| `AGENT_BROWSER_PROFILE` | Default `--profile`. Without either, the single running profile is used, else `default`. |
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
`config` error rather than a silent default.

## Session model

```
%LOCALAPPDATA%\agent-browser\profiles\<name>\
  user-data\        the Chromium profile: cookies, local storage, sign-ins (never your daily Edge profile)
  session.json      pid, port, token, browser, heartbeat, busy, code hash (written by the daemon)
  session.lock      held by the daemon for its lifetime; the OS releases it on death (liveness truth)
  launch.json       the settings the client handed the daemon
  daemon.log        one line per command, masked arguments, tracebacks
  last_snapshot.txt the untruncated filtered snapshot of the last served page
  downloads\  shots\
```

One daemon, one browser window, one loopback port per profile. `start` is idempotent; a stale session (daemon died)
is cleaned and relaunched on the same profile folder, so the sign-in survives. Two profiles run side by side with
separate cookies; that is opt-in through `--profile`. When exactly one profile is running and no `--profile` is
given, commands go to it; several running and no flag is `ambiguous_profile`.

Dialogs are answered synchronously by the daemon: `alert` is accepted; `confirm`/`prompt` during a command are
accepted only with `--accept-dialog` (otherwise the command fails with `dialog`); `beforeunload` during a command
keeps the page and fails with `unsaved_changes` unless `--discard-changes`; between commands a `beforeunload`
raised by a human navigation is accepted.

## Security posture

- Playwright talks to the browser over a pipe. No DevTools TCP port is opened on the signed-in profile.
- The daemon listens on `127.0.0.1` only, behind a per-session token stored in `session.json` under your user profile;
  a wrong token is dropped without a reply, a request has 5 s to arrive and 64 KB to say it.
- The tool never types into password, one-time-code, or credential-looking fields; there is no override. The human
  types in the window and the agent waits with `wait --signed-in`.
- `goto` opens only `http`/`https` (and `about:blank`); `edge://`, `file:`, `javascript:` are refused.
- Uploads and `--path` targets must sit under the current folder, the profile's `downloads`/`shots`, or
  `upload_roots`; UNC paths, hidden folders, and key files are refused.
- Page-derived strings are sanitized (control and bidi characters stripped, lines capped) and only ever appear
  under keys named in `untrusted`; hints are static templates. URLs are redacted (`code`, `token`, `id_token`,
  `SAMLResponse`, ... masked, fragment dropped) in output and logs.
- What the environment cannot do: on a machine where the agent runs Bash unprompted, `AGENT_BROWSER_*` variables and
  the `Bash(agent-browser *)` allowlist are conveniences, not controls. The boundaries are what the CLI refuses in
  code, the human-written `config.json`, and what the human types in the window. `eval` is on by default by
  decision; turn it off in `config.json` for a locked-down machine.

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

One file, layered bottom-up; every OS edge is a module-level seam the offline tests replace (`_spawn_daemon`,
`_try_lock`, `_pid_alive`, `_kill_tree`, `_processes_using`, `_registry_value`, `_playwright_factory`).

| Section | Role |
|---|---|
| Constants | Timeouts, byte budgets, role sets for the snapshot filter, secret-field and sign-in heuristics, error classes, the static hint table. |
| Config helpers | `_tag()` rides `error_class`/`hint`/`details` on builtin exceptions; lazy env reads; `ProfilePaths`; the human-written `config.json`. |
| Text hygiene | `sanitize_text`, `redact_url`, `safe_name`. |
| Browser discovery | `find_browser` (explicit > env > PATH > known folders > App Paths), version from the install folder, Edge and Chrome policy decoding. |
| Process seams | detached spawn with the breakaway retry and the WMI fallback, lock files, Job Object facts, CIM process listing by full profile path. |
| Session store | atomic `session.json`, liveness from the lock, sticky profile resolution. |
| Client protocol | one JSON line over a loopback socket; remote errors re-raised through `_tag()` so `main()` maps exit codes the same way for local and remote failures. |
| Snapshot pure layer | `parse_ai_snapshot`, `frame_prefixes`, `filter_snapshot`, `budget_lines`, `diff_lines`: pure functions over Playwright's ai-mode text, tested against captured real output. |
| Daemon | `Daemon` owns one persistent context; context-level dialog/download/page listeners; per-frame navigation counters that turn a ref from a navigated frame into `stale_ref`; heal-once re-snapshot when a scoped snapshot reset Playwright's ref map; settle across frames; one handler per verb. |
| Client verbs | `start` (spawn, poll, classify the log tail), `stop` ladder, `status`, `clean`, `doctor`, `focus`. |
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
| `ambiguous_profile` | Two profiles are running. Add `--profile NAME`. |
| `stale_ref` / `ref_not_found` | The page or its frame navigated, or a scoped snapshot was taken. `agent-browser snapshot` and pick again. |
| `guarded` on `fill` | The field looks like a password or code field. The human types it in the window; then `wait --signed-in <host>`. |
| `unsaved_changes` | The page has edits. Click its Save/Update button or re-run with `--discard-changes`. |
| `Profile path is too long` (class `config`) | Set `AGENT_BROWSER_HOME` to a short path such as `C:\ab`. |
| `browser_launch` with a log tail | Read `daemon.log`; `agent-browser doctor` decodes Edge and Chrome policies (`DeveloperToolsAvailability=2`, `ForceEphemeralProfiles`). Try `--browser chrome`. |
| `profile_in_use` | A stray browser holds the profile folder. `agent-browser clean --profile <p>`. |
| The window is not visible | It opened behind other windows: check the taskbar or `agent-browser focus`. |
| Output looks cut off | The Bash tool caps output at 30,000 chars; the tool keeps responses under 24 KB and writes the full snapshot to `last_snapshot.txt` (`snapshot_file`). |
| Git Bash turned `/incident.do` into `C:/Program Files/Git/incident.do` | The tool de-mangles arguments when `MSYSTEM` is set and adds a `note`; prefer host names and never start an argument with `/`. |
| A Bash call was denied by the shell guard | The user's PreToolUse guard denies statements starting with `echo`, `cat`, `type`, `set` when the command mentions a secret word. One `agent-browser` command per call, nothing before it. |
| `stale_code: true` in `status` | `agent_browser.py` changed since the daemon started. `stop` then `start`. |
| `doctor` shows `kill_on_job_close` | Your shell's Job kills children on close. `AGENT_BROWSER_LAUNCHER=wmi`. |
| pip hits `pkgs.dev.azure.com` | Prefix with `$env:PIP_INDEX_URL = "https://pypi.org/simple"; $env:PIP_EXTRA_INDEX_URL = ""`. |

## Follow-ups

- Measure the host matrix for Claude Desktop and the VS Code extension, and after the host quits.
- Console capture (`console` verb) and network summaries.
- A Windows named pipe with an owner-only DACL instead of the token-gated loopback socket.
- Cross-origin (OOPIF) iframe prefix stability across snapshots is not asserted by the live suite yet.
