# Browser interfaces

All examples are one command per shell call. Use the profile/session flags after the
verb when using the skill's allowlist. File paths must be local, readable UTF-8 files
within the working directory, session download/screenshot directory, or configured
upload roots. The daemon checks them against your working directory, not its own.
Refused: the agent-browser state folder, dot-named files and folders below the allowed
folder, dot-folders directly under your home (except `.claude`), key files, UNC and
device paths, and non-fixed drives. A hidden folder above your working directory is fine.

```text
agent-browser fill e5 --text-file note.txt
agent-browser type --label "Short description" --text-file note.txt
agent-browser eval --js-file inspect.js
agent-browser fill-form --file fields.json
agent-browser click --role button --name Save --expect "text:Saved" --expect "url:changed"
agent-browser api https://example.com/api/items
agent-browser commit api https://example.com/api/items --method POST --json-file payload.json
agent-browser screenshot --annotate
agent-browser table e15
agent-browser check http://localhost:3000
agent-browser state --command 0123456789abcdef0123456789abcdef
agent-browser output --id 0123456789abcdef0123456789abcdef --offset 0
agent-browser commit journal --clear
agent-browser commit journal --clear --include-unknown
```

`fields.json` is a list, or an object containing that list as `fields`:

```json
[
  {"target":{"label":"Short description"},"operation":"fill","value":"Investigating"},
  {"target":{"ref":"e7"},"operation":"select","value":"2 - High"},
  {"target":"e8","operation":"check","value":true},
  {"target":"e9","operation":"uncheck","value":false}
]
```

Preflight includes uniqueness, credentials, editable control type, disabled/read-only
state, and select options. Execution can still fail if the page changes afterwards.
`completed_fields` contains zero-based field indexes. No field submits the form.

Snapshot values: each field line with a value is checked before it is shown, under a
budget of 200 checks and 2 s per snapshot. A credential-looking field, one that cannot
be checked, one with no ref, and any past the budget show `***` for the value and the
field's contents; rows, cells, buttons and links that repeat the value in their name show
`***` in its place. `/url` lines in `--full` and `--find` are redacted like other URLs:
query and `;` path-parameter secrets, JWT-shaped values and a token segment after a route
such as `reset-password/` become `***`, the fragment is dropped, and a bare `#anchor`
shows `#`; ids such as `sys_id` stay. Names, values and frame labels render on one line
without invisible or tag characters.
`--find` never matches a masked value or a redacted link. Semantic-target candidates are
described from the masked page snapshot, without their values.

Expectations accept `text:...`, an initially visible ref `gone:e12`, `url:changed`, and
`response:METHOD URL_GLOB:STATUS` with a three-digit status or `2xx`-style mask.
`text:` is a case-insensitive substring that needs a new occurrence: it passes when a
frame shows the text more times than before the action. Text already shown does not
count, also after its frame reloads; a frame or tab the action opened counts from zero.
A frame whose text cannot be read before the action fails the command with
`action_failed`, and nothing is done. (`wait --text` still accepts text already shown.)
Responses must occur after observation starts on the same page; each is matched as it
arrives, so other traffic from this or another session cannot push a match out.
Repeat checks for AND semantics; checks given before and after the verb (and around
`commit`) all apply, in order. They share the action's deadline and never replay it.
Same-page network matches are evidence, not proof of a causal business operation.

API reads use GET/HEAD; other supported methods require `commit api`. The current
tab and request must have identical scheme, host and effective port. No automatic
redirects or request retries occur. Cookies and internal ServiceNow authentication
headers stay in the daemon. Inline bodies are scrubbed of this session's tokens (below);
read pages with `text` or `snapshot`. Response headers named like credentials (cookie, `auth`,
token, session, `g_ck`, secret, CSRF/XSRF, password, credential, or ending in `key`) and
`Location`/`Refresh` are omitted; the rest are single-line with URLs redacted, including
`Content-Location` and `Link` targets.
A 401, or a redirect whose Location leaves the origin or names a sign-in path or IdP,
returns `needs_human` and a `handoff_id`; repeats reuse the open handoff. Every redirect
(3xx except 304) returns `redirect: true` and a placeholder body. Other redirects and a
403 return a `note` and no handoff; a 403 keeps its real, untrusted body.
Response byte limits are post-download limits: Playwright buffers the response.
An inline body (every CLI and MCP reply) is not returned as received. Invisible characters
are stripped, and this session's tokens become `***`: the page's `g_ck` and the cookie values
for the request and page URLs wherever they occur, plus a token-shaped value (16+ characters
with a digit or capital) after a credential-named key, in a hidden input or meta tag named
like one, after `Bearer`/`Basic`, or shaped like a JWT. `body_masked` counts them. A JSON
value under a credential-named key that is not token-shaped (`"state": "1"`) is kept. A known
token still visible once the body is decoded (HTML entities, JS escapes, percent-encoding)
and read without separators (encoded, split or case-changed), or a key-and-token pair only the
decoding shows, withholds the whole body: `body` is a placeholder, `body_withheld` is
`session_token`, and a `note` follows. So do unreadable cookies (`unverified`), a body over
8 MiB (`too_large`) and JSON nested too deeply (`too_deep`). Base64, hex or other
re-encodings of a token are not detected. A body the Python bridge saves (`save_response`)
is byte-exact and unscrubbed. `eval` values lose invisible characters and are never masked.

`table` without a ref reads the page's one visible table, or its one ARIA grid when it has
no table; grid cells are included. `check URL` (MCP `browser_check_url`) accepts only
`localhost`, `127.0.0.1` or `::1`. Its temporary page cannot reach other hosts: such
requests, every redirect hop and its dedicated workers' requests included, are blocked and their
hosts listed (deduplicated, at most 20) in the untrusted `blocked_external`; a redirect of
the page itself, or a page that ends up off loopback, fails as `guarded` with the same list
in `details` (page-derived). The page's WebSockets to other hosts never connect, and popups
(and theirs) are closed (`popups_closed`). `unlabeled_controls` is page data too and is
listed in `untrusted`. Not covered: a popup's traffic until it is closed, a worker's
WebSockets, service workers (one the page registers stays in the profile for that origin)
and redirect hops inside a cross-site iframe. A page that cannot load (for example,
connection refused) fails as `action_failed`.

Use the optional MCP extra (`pip install -e "agent-browser[mcp]"`) and register the
`agent-browser-mcp` stdio command with your host. Pass the same explicit session ID
through every tool in one task. The 18 typed tools share the daemon dispatcher;
`browser_commit` and `browser_eval` are distinct approval targets. A tool reply carries the
same compact JSON twice, as text and as structuredContent; each copy is bounded to 7,000
UTF-8 bytes, and the whole serialized reply stays under 24,000.
Tools send the CLI's daemon payload: page tools take `tab` (`t2` or `2`), the `tab`
operation of `browser_action` takes `index` the same way, `browser_wait` takes `ref` and
`selector`, `browser_snapshot` `ref`, `browser_eval` `ref` and `frame`, `browser_read`
`max_chars`, `browser_goto` `wait` and `new_tab`. `timeout` is 0-86400 seconds and at most
100 for `browser_wait`, as on the CLI. Only `browser_commit` takes `accept_dialog`, `dialog_text`
(a prompt answer, only with `accept_dialog`) and `discard_changes`, for its page actions and for
operation `stop`. That stop takes only `discard_changes` and releases the session even when its tabs
have unsaved edits, which a plain `browser_stop` keeps; pass them only with the user's authorization. Snapshots default to 4,500 bytes so a page read fits
one reply; use `find` or `ref` to narrow it. `browser_commit` api with `body_file` sends
`Content-Type: application/json` unless `content_type` (only with `body_file`) is given.
Use `browser_output` and its `next_offset` for smaller MCP pages (each is `untrusted: ["chunk"]`); an overflow reply keeps
`url`, `title` and `untrusted`. A tool error has the same `error` object as CLI stderr
(`class`, `http_status`, `message`, `hint`, optional `details`); its hint names the
equivalent CLI command. `error.untrusted` names `message` (Playwright's text) and `details`
(candidates, options, dialogs, a call log) when they can quote the page; an overflow stub keeps it. Bad arguments and unknown tools are `validation`. A reply that
cannot be stored keeps `details.receipt`.

Journal inspection returns `next_offset`; state command history returns
`command_next_offset`. Use `--offset` for the next page. Oversized receipts return
an `output_id` prefixed with `r`; `output --id ID --offset 0` reads an immutable
terminal receipt (completed, failed, unknown or needs_human) directly from SQLite,
including when quota is full.
Pending receipts are unavailable through this fallback until they finish.

`commit journal --clear` first finishes this session's pending operations (one that ran
becomes `unknown`). It then deletes completed, failed and needs_human receipts, unknown
receipts of reads, and resolved handoffs. It keeps unknown receipts of commit verbs,
upload, eval, commands that ran and interrupted non-reads, their `<id>` screenshots
and saved responses, and open handoffs. The reply counts `cleared_commands`,
`retained_commands`, `cleared_handoffs` and `retained_handoffs`, plus `files_not_removed`
when a file was held open (a later clear removes it). `--include-unknown` (MCP
`include_unknown`) deletes the kept ones too. When the quota refuses a command, its
`journal_full` details split `evidence_bytes` into `session_bytes`, `other_sessions_bytes`
and the shared `journal_bytes`, against `quota`; other sessions' evidence is beyond this
session's clear.

The canonical Claude settings template asks for commit/eval and evidence clear calls, and for
any command with `--accept-dialog` or `--discard-changes` (flags must be spelled out, never
abbreviated), including tools from any MCP server using these browser tool names. MCP rules see
tool names only, so a stop that discards edits is `browser_commit` operation `stop`, which is
asked about, and a plain `browser_stop` is not. Nothing asks before `press Enter`, a Submit
click or `fill`/`type --enter` (which needs a `commit` profile, not the `commit` verb). A template edit
does not update an already-installed caller policy. For approval-sensitive workflows,
verify the installed policy and configure the profile with eval disabled. These
[permission patterns](https://code.claude.com/docs/en/permissions) are cooperative.
