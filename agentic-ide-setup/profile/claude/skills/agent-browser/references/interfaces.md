# Browser interfaces

All examples are one command per shell call. Use the profile/session flags after the
verb when using the skill's allowlist. File paths must be local, readable UTF-8 files
within the working directory, session download/screenshot directory, or configured
upload roots. Hidden/credential paths and UNC shares are refused. Do not place input
files inside a hidden managed worktree directory; stage them in an allowed location.

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

Expectations accept exact `text:...` presence, an initially visible ref `gone:e12`,
`url:changed`, and `response:METHOD URL_GLOB:STATUS` with a three-digit status or
`2xx`-style mask. Responses must occur after observation starts on the same page.
Repeat checks for AND semantics. They share the action's deadline and never replay it.
Same-page network matches are evidence, not proof of a causal business operation.

API reads use GET/HEAD; other supported methods require `commit api`. The current
tab and request must have identical scheme, host and effective port. No automatic
redirects or request retries occur. Cookies and internal ServiceNow authentication
headers stay in the daemon. Response authentication and redirect headers are omitted.
Response byte limits are post-download limits: Playwright buffers the response.

Use the optional MCP extra (`pip install -e "agent-browser[mcp]"`) and register the
`agent-browser-mcp` stdio command with your host. Pass the same explicit session ID
through every tool in one task. The 18 typed tools share the daemon dispatcher;
`browser_commit` and `browser_eval` are distinct approval targets. Tool text is compact
JSON and matches structuredContent; complete serialized tool replies are bounded.
Use `browser_output` and its `next_offset` for smaller MCP pages.

The canonical Claude settings template asks for commit/eval and evidence clear calls,
including tools from any MCP server using these browser tool names. A template edit
does not update an already-installed caller policy. For approval-sensitive workflows,
verify the installed policy and configure the profile with eval disabled. These
[permission patterns](https://code.claude.com/docs/en/permissions) are cooperative.
