# ServiceNow browser work

Prefer the read-only ServiceNow API client for record queries. When a browser is
needed, let the user sign in and use the newest snapshot's frame-prefixed refs.
Use `agent-browser snapshot` after navigation and `agent-browser text` to verify
the displayed record. Preview record changes and submit only within the user's
explicit authorization. A successful click does not prove the record was saved;
verify the resulting record and any validation message.

`agent-browser run sn/add-work-note --number INC0010023 --text-file note.txt` previews a built-in
classic `gsft_main` operation. Use `agent-browser commit run` only within the authorized write
scope. It verifies Number/stable record identity, refuses an existing draft or
identical displayed note, submits once, reloads that record and checks persisted
noneditable activity. The preview is a read: it makes the same identity and
identical-note checks (`guarded`), and for an unfamiliar layout or an existing draft
returns `blocked: true` with `blocked_reason`, opening no handoff and taking no focus.
`commit run` on such a page returns a human handoff before anything is typed; that
receipt's status is `needs_human`. Receipts keep the recipe and record
number, never the note. Inspect the receipt before any retry. Real instance writes
need a separately authorized test record.

Optional `SERVICENOW_AUTH=browser` in servicenow-client uses the same signed-in
profile and daemon API. Install both packages, select the intended profile/session
and navigate to the configured exact instance origin. Basic auth stays available;
all existing client write/delete/dry-run/attachment guards still apply first.
No cookies, authentication headers or session key values leave the daemon.
`agent-browser api URL --servicenow` does the same from the shell: it sends the page's
session key as `X-UserToken`, and the reply carries no request headers. A page
without a usable session key (not signed in, or not a classic page) fails as
`unsupported_browser_auth` before any request and opens no handoff. If the user must
sign in, run `agent-browser needs-human --signed-in <instance host>`; otherwise open a
classic form page and retry. An expired session (401 or a redirect to the sign-in
page or IdP) opens one handoff and focuses the window once; repeats reuse it. A 403
is usually an ACL decision: its body is returned with a `note`, and no handoff is
opened. If a gateway answers an expired session with 403, run `needs-human` yourself.

Raw response artifacts contain application data and remain under the owning
session's outputs until `agent-browser commit journal --clear`, which keeps those of
unknown write receipts unless `--include-unknown` is added. They count toward
the default 1 GiB profile quota; request staging files are transient. Response-size
checks occur after Playwright downloads/buffers data and are not hard memory bounds.
Browser write errors with `write_outcome: unknown` retain safe receipt metadata and
are never retried automatically. Inspect that record and receipt before another write.
