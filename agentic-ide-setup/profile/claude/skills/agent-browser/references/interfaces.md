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
query secrets become `***`, the fragment is dropped, and a bare `#anchor` shows `#`.
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
headers stay in the daemon. Response authentication and redirect headers are omitted.
A 401, or a redirect whose Location leaves the origin or names a sign-in path or IdP,
returns `needs_human` and a `handoff_id`; repeats reuse the open handoff. Every redirect
(3xx except 304) returns `redirect: true` and a placeholder body. Other redirects and a
403 return a `note` and no handoff; a 403 keeps its real, untrusted body.
Response byte limits are post-download limits: Playwright buffers the response.

Use the optional MCP extra (`pip install -e "agent-browser[mcp]"`) and register the
`agent-browser-mcp` stdio command with your host. Pass the same explicit session ID
through every tool in one task. The 18 typed tools share the daemon dispatcher;
`browser_commit` and `browser_eval` are distinct approval targets. Tool text is compact
JSON and matches structuredContent; complete serialized tool replies are bounded.
Use `browser_output` and its `next_offset` for smaller MCP pages. A tool error has the
same `error` object as CLI stderr (`class`, `http_status`, `message`, `hint`, optional
`details`); its hint names the equivalent CLI command. Bad arguments and unknown tools
are `validation`. A reply that cannot be stored keeps `details.receipt`.

Journal inspection returns `next_offset`; state command history returns
`command_next_offset`. Use `--offset` for the next page. Oversized receipts return
an `output_id` prefixed with `r`; `output --id ID --offset 0` reads an immutable
terminal receipt (completed, failed, unknown or needs_human) directly from SQLite,
including when quota is full.
Pending receipts are unavailable through this fallback until they finish.

The canonical Claude settings template asks for commit/eval and evidence clear calls,
including tools from any MCP server using these browser tool names. A template edit
does not update an already-installed caller policy. For approval-sensitive workflows,
verify the installed policy and configure the profile with eval disabled. These
[permission patterns](https://code.claude.com/docs/en/permissions) are cooperative.
