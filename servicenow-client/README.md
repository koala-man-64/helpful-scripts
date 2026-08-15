# servicenow-client

A single-file ServiceNow REST client — [`servicenow_client.py`](servicenow_client.py) —
usable as a CLI (`snow ...`, JSON to stdout) or as an importable Python module.
It creates, updates, and annotates records, reads them back, and pulls journal
and audit history for incidents, changes, catalog requests (REQ/RITM/SCTASK),
Vulnerability Response Vulnerable Items (VITs), and any other table.

It is built to be driven by an AI agent acting on a user's behalf: every result
is machine-readable JSON with a stable shape, every failure is a structured
error envelope on stderr with a `class` the agent can branch on, and writes are
guarded (read-only mode, `--dry-run`, double-gated delete, platform-table
denylist, write verification).

The script is self-contained — copy `servicenow_client.py` anywhere, `pip
install python-dotenv`, and it runs. The folder exists for the tests.

## Quickstart

```powershell
cd servicenow-client
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest        # 171 offline tests, no credentials needed
```

Add credentials to the repo-root `.env` (see Configuration), then:

```powershell
.\.venv\Scripts\snow.exe whoami
.\.venv\Scripts\snow.exe get INC0010023
.\.venv\Scripts\snow.exe query incident --group "Network Ops" --active --opened-last 7d
```

`python servicenow_client.py <command>` works identically without the console
script.

## Commands

| Command | What it does |
|---|---|
| `whoami` | Verify auth; show the service account and instance. |
| `tables` | Dump the record-type registry (prefixes, aliases, default fields). Needs no config. |
| `get <id>` | Fetch one record by number (`INC0010023`) or sys_id (needs `--table`). |
| `query <table>` (alias `list`) | List records: `--query` encoded query plus canned flags (`--active`, `--group`, `--assigned-to`, `--unassigned`, `--opened-last 7d`, `--updated-last 12h`, `--order-by`, `--desc`), `--limit/--offset/--all`. |
| `count <table>` | Record count via the Aggregate API. |
| `create <table>` | Create a record from `--field k=v` (repeatable) and/or `--data '{json}'`; `--correlation-id` makes retries idempotent. |
| `update <id>` | PATCH fields on a record. |
| `comment <id> <text>` | Add a comment — **VISIBLE TO THE END USER**. |
| `work-note <id> <text>` | Add an internal work note. |
| `notes <id>` | Read the journal back (`sys_journal_field`), newest first; `--type comments\|work_notes\|all`. |
| `history <id>` | Field-level audit trail (`sys_audit`); `--field` to filter. Often ACL-restricted — see Troubleshooting. |
| `resolve <id>` | Incident resolve (state 6); **requires** `--code` and `--notes`. |
| `close <id>` | Close via the table's registered transition (incident 7, CTASK/RITM/SCTASK 3, REQ `closed_complete`). |
| `assign <id>` | Set `--to <user>` and/or `--group <group>` (names, emails, or sys_ids — resolved for you). |
| `user <term>` / `group <term>` | Search sys_user / sys_user_group. |
| `choices <table> <field>` | Valid values+labels for a choice field **on this instance** — use before setting states on changes, problems, or VITs. |
| `schema <table>` | Field dictionary (`sys_dictionary`), including task-inherited fields. |
| `sla <id>` | SLA timers for a record (`task_sla`): stage, breached, time left. |
| `approvals <id>` | Approval states for a record (read-only). |
| `attachments <id>` / `attach <id> <file>` / `download <attachment_sys_id>` | List, upload, download attachments (downloads go to files, never stdout). |
| `delete <id>` | Hard-delete — requires `--force` **and** `SERVICENOW_ALLOW_DELETE=true`. |

Flags on every command: `--output json|table`, `--max-field-chars N` (default
20000; 0 disables truncation), `--timeout`, `--verbose` (request lines to
stderr, no secrets). Commands that take a record identifier also accept
`--table` (name or alias; required with a sys_id). Write commands accept
`--dry-run` (prints the exact method/URL/payload, sends nothing). `--display
value|display|both` and `--fields a,b,c` are available on `get` and `query`.

Every registered table has aliases (`inc`, `chg`, `ctask`, `req`, `ritm`,
`sctask`, `prb`, `vit`) and any other table works by real name via `--table` /
the `query` table argument.

## Output contract (for agent authors)

- **Success** → result JSON on stdout, exit `0`. List results are
  `{"table", "records", "count", "total", "has_more", "next_offset", "truncated"}`.
  Records put identity fields first, collapse `{value, display_value}` pairs to
  a scalar when equal (else `{"value", "display"}`), convert raw UTC datetimes
  to ISO-8601 `Z`, and append a deep `url` to the record form.
- **Failure** → `{"error": {"class", "http_status", "message", "hint"}}` on
  stderr; stdout stays empty. Exit `2` = fix the call (classes `usage`,
  `validation`, `not_found` — and `guarded` when the refusal is
  per-invocation: missing `--force`, or a denylisted table). Exit `1` = fix
  config or retry (classes `config`, `auth`, `forbidden`, `rate_limited`,
  `unavailable`, `remote` — and `guarded` when a standing policy gate refuses:
  `SERVICENOW_READ_ONLY`, `SERVICENOW_ALLOW_DELETE`). Branch on the exit code
  for who-fixes-it, on `class` for what happened.
- **Writes** report `fields_not_applied` when ServiceNow accepted the request
  (2xx) but silently dropped or rewrote a field — an ACL block or a business
  rule. Journal fields are exempt (they are never echoed back).
- 429s are retried up to 3 attempts honoring `Retry-After` (capped 30s);
  502/503/504 retries are GET-only — **a POST is never auto-retried** (it could
  duplicate a ticket). Use `create --correlation-id <key>` to make orchestrator
  retries idempotent.

## Configuration

Read lazily from real env vars or the repo-root `.env` (client env wins; a
second default-path `load_dotenv` covers the copied-out-of-repo case).

| Variable | Required | Meaning |
|---|---|---|
| `SERVICENOW_INSTANCE` | yes | Bare instance name (`dev12345`) or full `https://` URL. |
| `SERVICENOW_USERNAME` | yes | Service-account user. |
| `SERVICENOW_PASSWORD` | yes | Service-account password. There is deliberately no `--password` flag (argv leaks into process lists). |
| `SERVICENOW_AUTH` | no | `basic` (default). `oauth` is reserved for a follow-up; the auth seam (`_auth_headers`) is the only place it will touch. |
| `SERVICENOW_TIMEOUT_SECONDS` | no | Per-request timeout (default 30). |
| `SERVICENOW_READ_ONLY` | no | `true` refuses every write **before any request is sent**. Recommended default for exploratory agent sessions. |
| `SERVICENOW_ALLOW_DELETE` | no | Must be `true` for `delete` (in addition to `--force`). |
| `SERVICENOW_MAX_ATTACHMENT_MB` | no | Attachment size cap (default 100). |

### Service account & roles (the real security boundary)

Client-side guardrails are safety rails, **not** security controls — the
enforced boundary is instance-side ACLs. Use a dedicated service account with
`web_service_access_only=true` and the minimal roles per feature:

| Feature | Typical role/ACL |
|---|---|
| Incident/change/request read+write | `itil` (or scoped ACL equivalents) |
| Vulnerable Items (`sn_vul_*`) | Vulnerability Response read/write roles (e.g. `sn_vul.read`, remediation-owner-level for updates; names vary by VR release) |
| `notes` (sys_journal_field) / `history` (sys_audit) | Often admin-only out of the box — request a scoped read ACL instead of admin |
| `count` (Aggregate API) | Aggregate API access; if denied, use `query --limit 1` and read `total` |

Guardrail layering is deliberate: env vars (`SERVICENOW_READ_ONLY`,
`SERVICENOW_ALLOW_DELETE`) gate capability classes and are set by the human
deploying the agent; flags (`--dry-run`, `--force`) gate individual actions and
are visible in agent logs. A prompt-injected agent can pass flags, but cannot
unset the environment.

Attribution: everything this client does is recorded against the **service
account**, not the human. Notifications fire exactly as if a person made the
change — a comment on a ticket emails its watch list.

## Validate (offline — no instance needed)

```powershell
.\.venv\Scripts\python.exe -m pytest      # 171 tests; recording-fake transport, no network
python servicenow_client.py tables        # works with zero configuration
```

## Live smoke test (once you have an instance + service account)

Each step confirms one thing the offline suite cannot:

1. `snow whoami` — credentials, instance URL, account visibility.
2. `snow get <known INC number>` — Table API read + display normalization.
3. `snow notes <that INC>` — `sys_journal_field` read ACL.
4. `snow history <that INC>` — `sys_audit` read ACL.
5. `snow update <that INC> --field urgency=3 --dry-run` — preview shape; nothing sent.
6. `snow work-note <that INC> "smoke test from servicenow_client"` — write path + `fields_not_applied` verification.
7. `snow count incident --query active=true` — Aggregate API access.
8. `snow choices incident state` — choice introspection.
9. `snow get <known VIT number>` — VR plugin present, VIT field names/roles correct.

Findings from step 9 may require editing the `sn_vul_vulnerable_item` row of
the registry in `servicenow_client.py` (default fields are a best-effort guess;
VR schemas drift by release).

## How it's built

One file, strictly layered — CLI handlers → high-level operations →
`ServiceNowClient` → `_http_send`:

| Section | Role |
|---|---|
| Transport (`HttpResponse`, `_http_send`) | The only code that touches the network; normalizes `HTTPError`, caps response size, detects hibernating dev instances. **The test seam** — tests monkeypatch it with a recording fake. |
| `ServiceNowClient` | One `_request()` choke point: URL/params/headers, bounded retries (429 any method, 5xx GET-only), read-only refusal and dry-run preview enforced here so no layer can bypass them, central error translation preserving ServiceNow's `error.message`/`detail` and HTTP status verbatim. |
| Auth seam (`_auth_headers`) | Basic today; OAuth later is one new branch + token cache, zero call-site changes. |
| Registry (`TableConfig`, `TABLES`) | Pure data: prefixes, aliases, curated default fields, journal fields, state transitions. Instance quirks are one-row fixes. Unknown tables get a synthesized generic config. |
| Operations | The importable API (`get_record`, `query_records`, `add_work_note`, ...). Write verification diffs payload vs response into `fields_not_applied`. |
| CLI | argparse subcommands, JSON emit, error envelopes, truncation. |

Error convention (repo-wide): **`ValueError` means bad input** — fix the call
(exit 2); **`RuntimeError` means environment or remote failure** — fix config
or retry (exit 1). Messages say what to do next; ServiceNow's own error text is
surfaced verbatim with its status code. Machine-readable `class`/`http_status`/
`hint` ride on the exception for the CLI envelope — no custom exception classes.

Tests (`tests/`): 171 offline tests mirroring the layers —
transport/retry/error translation, query building and pagination, identifier
detection, registry invariants, operation payload shapes, guardrails
(read-only/dry-run/delete gates/denylist), and CLI exit codes/envelopes via
`main([...])`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Missing required environment variable: SERVICENOW_INSTANCE` | Add the `SERVICENOW_*` block to the repo-root `.env` (see `.env.example`). |
| `Authentication failed (401)` / `User Not Authenticated` | Wrong credentials or locked account (`sys_user.locked_out`). Locked vs wrong password are not distinguishable from the API. |
| `... (403) ... requires the Vulnerability Response plugin ...` | The VR plugin is absent or the account lacks `sn_vul.*` roles. |
| `Reading sys_journal_field requires a read ACL ...` (or sys_audit) | These tables are often admin-only out of the box; request a scoped read ACL for the service account. |
| `ServiceNow returned a non-JSON response (status 200) ... hibernate` | Developer instance is asleep — wake it at developer.servicenow.com. |
| `Refusing PATCH ... SERVICENOW_READ_ONLY is set` | Working as intended; unset the variable to allow writes. |
| Write "succeeded" but `fields_not_applied` lists your field | An ACL silently dropped it or a business rule overwrote it. Check the account's write ACL for that field, or the table's business rules. |
| A state update is listed in `fields_not_applied` | The transition was vetoed by the state model / a business rule. Run `choices <table> state` to see valid values on this instance. |
| Mandatory-on-form field accepted empty via API | UI policies do **not** apply to the API; only data policies do. The record may be invalid by UI standards even though the write succeeded. |
| Encoded query returns far more rows than expected | An invalid `sysparm_query` silently matches ALL rows unless the instance sets `glide.invalid_query.returns_no_rows`. Prefer the canned flags; double-check operator syntax. |

## Follow-ups

- OAuth 2.0 (`SERVICENOW_AUTH=oauth`): refresh-token grant against
  `/oauth_token.do` with in-memory token cache — slots into `_auth_headers`.
- Change Management API (`/api/sn_chg_rest`) verbs with model-aware state
  transitions; current `close` on `change_request` uses stock state 3 — verify
  per instance.
- Approve/reject (`sysapproval_approver` writes) — deliberately omitted:
  an agent approving with a service account defeats the approval control.
- Catalog browse/order (`/api/sn_sc`), KB search, merged activity timeline,
  batch operations, multi-instance profiles.
- Full `sys_db_object` class-hierarchy walk for `schema` (currently unions the
  table with `task` only).
