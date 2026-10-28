# ServiceNow browser work

Prefer the read-only ServiceNow API client for record queries. When a browser is
needed, let the user sign in and use the newest snapshot's frame-prefixed refs.
Use `agent-browser snapshot` after navigation and `agent-browser text` to verify
the displayed record. Preview record changes and submit only within the user's
explicit authorization. A successful click does not prove the record was saved;
verify the resulting record and any validation message.

`run sn/add-work-note --number INC0010023 --text-file note.txt` previews a built-in
classic `gsft_main` operation. Use `commit run` only within the authorized write
scope. It verifies Number/stable record identity, refuses an existing draft or
identical displayed note, submits once, reloads that record and checks persisted
noneditable activity. Unfamiliar layouts return a human handoff before anything is
typed; that receipt's status is `needs_human`. Receipts keep the recipe and record
number, never the note. Inspect the receipt before any retry. Real instance writes
need a separately authorized test record.

Optional `SERVICENOW_AUTH=browser` in servicenow-client uses the same signed-in
profile and daemon API. Install both packages, select the intended profile/session
and navigate to the configured exact instance origin. Basic auth stays available;
all existing client write/delete/dry-run/attachment guards still apply first.
No cookies, authentication headers or session key values leave the daemon. Missing
or expired browser auth requires human sign-in or unsupported-auth handling.

Raw response artifacts contain application data and remain under the owning
session's outputs until `agent-browser commit journal --clear`. They count toward
the default 1 GiB profile quota; request staging files are transient. Response-size
checks occur after Playwright downloads/buffers data and are not hard memory bounds.
Browser write errors with `write_outcome: unknown` retain safe receipt metadata and
are never retried automatically. Inspect that record and receipt before another write.
