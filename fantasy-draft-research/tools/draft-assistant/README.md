# Fantasy Draft Assistant

This local Python CLI turns traceable research into a frozen league-specific board, reconciles a fresh sanitized Chrome observation, and produces deterministic recommendations. Chrome remains the signed-in sensor and approved executor for Yahoo, ESPN, and Sleeper; the package contains no browser driver and retains no browser credentials or raw DOM.

Supported in v1: standard, half-PPR, and PPR one-QB snake redrafts with configurable teams, roster slots, flex eligibility, scoring overrides, draft position, and keepers. Auction, dynasty, best ball, superflex, and IDP are intentionally out of scope.

Use the [one-page operator card](../../notes/draft-copilot-operator-card.md) during a draft. The longer research and platform notes are references, not mandatory live ledgers.

## Safety boundary

The active platform is authoritative for the clock, current turn, availability, completed picks, queue, roster, and final result. The CLI may recommend and persist a short-lived intent, but a recommendation never claims that the platform queue changed.

The manager must explicitly approve one player and the exact ordered list of up to three queue candidates. Queue approval is material because Yahoo and ESPN may consume queued players for auto-draft. Chrome must then re-observe the room. A changed pick, room, availability, recommendation, queue, stale observation, ambiguous control, reconnect, modal, or authentication challenge voids approval. With fewer than 20 seconds remaining, the write path must fail closed.

An issued-but-unsubmitted intent may be cancelled. A submitted intent cannot be cancelled or retried; it requires strict verification or manager takeover. Verification requires a fresh observation from the same platform and room, expected pick/team advancement, the selected player in both our roster IDs and the unavailable set, matching last-pick evidence, and no control ambiguity. If the timer wins, attribute the result to platform auto-draft rather than Codex.

Never store credentials, cookies, storage state, tokens, private URLs, league or room IDs, participant identities, or raw DOM captures. Runtime directories, raw/cache inputs, SQLite files, and browser state are gitignored.

## Install and unit tests

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

The offline suite validates code behavior. It does **not** qualify a live write path. Yahoo, ESPN, and Sleeper each require a separate witnessed, non-consequential timed mock before queue or pick writes can be enabled for that platform. Read-only observation may activate earlier.

## Data and evidence contracts

- `SourceArtifact` records source, upstream family, signal role, scoring context, acquisition method, publication/retrieval time, checksum, freshness, and safe provenance.
- `PlayerEvidence` links a canonical player identity to a typed projection, tier, ADP, status, news, or risk signal.
- `CompiledPlayer` records projected points, replacement baseline, VBD, independent tier, separate platform ADPs, status/risk bands, bounded personal preference, and evidence references.
- `BoardManifest` records schema/config hashes, the one selected source family for each role, omissions/conflicts, artifact checksums, and freeze/revision history.
- `ObservedDraftState` is sanitized live state: platform, adapter version, opaque room fingerprint, phase, pick/team/clock, roster IDs, unavailable IDs, queue, visible player identities, controls, and last-pick evidence.

Compatible raw projections are scored with the configured league rules. Pre-scored projections are accepted only when their scoring context matches. Boris Chen/FantasyPros is one consensus-tier family and is counted once. Yahoo, ESPN, and Sleeper ADP remain distinct timing signals. Official team/NFL reporting controls material status; NBC and Reddit are discovery-only. The official Sleeper API is read-only, while Boris CSV is the other direct adapter. Other sources enter through a validated sanitized Chrome/manual snapshot.

Default maximum ages at compilation are five seconds for room observations, six hours for news/status, 24 hours for identity/team and ADP, and 72 hours for projections/rankings/tiers. League settings, order, keepers, and scoring require room-open revalidation. Mandatory stale evidence blocks compilation; optional stale evidence is omitted. Material post-freeze news creates a parent-linked board revision.

## Legacy player CSV

The original direct player-board import remains available for compatibility. `players.csv` requires all of these columns, including `ambiguous`:

```text
player_id,name,nfl_team,position,projection,tier,adp,league_fit,scarcity,wait_risk,roster_utility,risk,source,source_family,checked_at,status,ambiguous
```

Names are descriptive only. Stable player ID plus exact position and NFL team form the identity gate. Duplicate or ambiguous identities fail validation rather than being guessed.

## Workflow

Research and freeze before the room opens. The direct refresh supplies Boris
tiers and Sleeper identity/status data; it does **not** supply projections.
A compatible raw-stat or league-scored projection snapshot is therefore a
mandatory sanitized import before compilation:

```powershell
draft-assistant research-refresh --source boris sleeper --scoring-format standard --output data/cache/research.json
Get-Content -Raw data/raw/sanitized-research.json | draft-assistant research-import --snapshot - --merge data/cache/research.json --output data/cache/research-final.json
draft-assistant compile-board --league league.json --research data/cache/research-final.json --output data/cache/board-2026
draft-assistant init --league league.json --players data/cache/board-2026/board.json --run data/runtime/draft-2026
draft-assistant doctor --run data/runtime/draft-2026
```

`init` is the explicit provisioning boundary: it creates the private run and SQLite event store from the league configuration and frozen compiled board. Other runtime commands validate that the store already exists.

Use `standard`, `half-ppr`, or `ppr` to match the league. `compile-board` fails
closed when the merged bundle has no compatible projection family. To revise
for material news without mutating the frozen output, compile to a new
directory with `--parent-board data/cache/board-2026/board.json
--revision-reason "material status update"`.

At room-open, revalidate settings and capture a fresh observation whose `config_hash` and `board_hash` match `status`. Reconcile and arm the exact room before asking for an actionable recommendation. `--observed-state -` reads sanitized JSON from standard input:

```powershell
Get-Content -Raw data/runtime/fresh-state.json | draft-assistant reconcile --run data/runtime/draft-2026 --observed-state -
draft-assistant arm --run data/runtime/draft-2026 --mode mock --room-fingerprint room-fp:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

The room fingerprint is `room-fp:` plus a SHA-256 digest derived by the Chrome
workflow from the private room identity. Only the digest crosses the CLI
boundary. The JSON sent to `--observed-state` is an envelope: all
`ObservedDraftState` fields remain at the top level and a transient
`control_snapshots` list carries semantic-control evidence. Use `observe` for
`reconcile`, `turn`, and `approve-pick`; `queue` plus `pick` for
`mark-submitted`; and `verify` for `verify-pick`. The snapshots are validated
against the matching version in `platforms.py`, hashed into the event evidence,
and never persisted as DOM. See the synthetic
[`yahoo_state.json`](tests/fixtures/yahoo_state.json) state and
[`test_platforms.py`](tests/test_platforms.py) snapshot builder for the complete
sanitized contract.

For a real draft, run `acknowledge-real` for the same fingerprint before `arm --mode real`; the platform-specific rehearsal gate still applies. After arming, `turn` reconciles each fresh observation and calculates the recommendation in one command:

```powershell
Get-Content -Raw data/runtime/fresh-state.json | draft-assistant turn --run data/runtime/draft-2026 --observed-state - --json
```

`turn` returns a primary, two fallbacks, a sequentially reranked snake-turn pair plan, exclusions, and component explanations. It never writes or claims to write the platform queue.

After reviewing the current result, explicitly approve one player and the exact ordered queue of one to three candidates:

```powershell
Get-Content -Raw data/runtime/fresh-state.json | draft-assistant approve-pick --run data/runtime/draft-2026 --observed-state - --player-id player-001 --queue player-001 player-002 player-003
```

The compact Chrome transaction re-observes, validates the hash-bound approval, verifies at least 20 seconds remain, replaces and verifies the exact queue, clicks/submits once, records that unverified attempt with `mark-submitted --observed-state -`, and then calls `verify-pick --observed-state -` with a fresh result observation. Only successful verification records confirmed manager-approved Chrome provenance. Keep both commands behind that transaction. Cancel only an issued intent:

```powershell
draft-assistant cancel-intent --run data/runtime/draft-2026 --intent-id INTENT_ID --reason "manager changed the plan"
```

Recovery commands retain their narrow roles:

```powershell
draft-assistant doctor --run data/runtime/draft-2026
draft-assistant status --run data/runtime/draft-2026
draft-assistant replay --run data/runtime/draft-2026
draft-assistant disarm --run data/runtime/draft-2026 --reason "draft complete"
```

After a restart, replay first, compare with a fresh platform observation, then run `turn`; it reconciles before producing the new recommendation. Never repair history by rewriting SQLite events.

## Ranking logic

The board derives positional replacement levels from active-team starter demand and deterministic flex allocation, treats keepers as committed, removes all keeper/drafted IDs, and matches the roster to legal slots. When remaining picks equal required unfilled slots, candidates that make legal roster completion impossible are excluded.

Eligible candidates are ordered deterministically by roster legality, 10-point VBD band, independent tier, no-return/wait-risk band, lineup-fit band, risk band, bounded target/avoid preference, raw VBD, same-position tier drop, active-platform ADP, then stable player ID. Wait risk uses next-pick distance, platform ADP, same-tier supply, and observed positional demand; opponent behavior may resolve a close call but cannot leapfrog a material VBD band.

At a snake turn, the first candidate is simulated, the second recommendation is reranked against the resulting roster, and fallback branches are returned. Every recommendation and exclusion has component-level reasons; there is no aggregate confidence score.

## Platform mappings and activation

Yahoo, ESPN, and Sleeper use small versioned Chrome mappings based on semantic labels or stable attributes. Missing or ambiguous controls fail closed. Yahoo and ESPN use Chrome for live reads and approved writes. Sleeper may use its official API for read-only structured data; Chrome still owns queue and pick writes. Yahoo OAuth and undocumented ESPN endpoints are not part of v1.

Keep witnessed rehearsal facts in a private local `rehearsal-readiness.json` beside the runtime database. Record readiness independently for each platform. Do not create or promote this file from assumptions, fixtures, or automated tests. Until that platform's timed mock verifies queue replacement, one-click submission, result verification, timeout attribution, and takeover behavior, its write path remains disabled.

Each private platform entry is bound to the mapping version and witnessed
evidence. It contains `mapping_version`, `witnessed_at`, a non-URL local
`evidence_reference`, `recommendation_p99_ms`, and the six boolean gates checked
by `doctor`: timed mock passed, zero wrong/duplicate/ambiguous actions, takeover
witnessed, queue fallback witnessed, no external calls after room-open, and
timer expiry classified as platform auto-draft. A missing, malformed, or
version-mismatched entry blocks real writes.

Platform references: [Yahoo live standard drafts](https://help.yahoo.com/kb/fantasy-football/participate-live-standard-draft-sln6230.html), [Yahoo Fantasy Sports API](https://developer.yahoo.com/fantasysports/guide/), [ESPN player queue](https://support.espn.com/hc/en-us/articles/360000140911-Online-Draft-Player-Queue), [ESPN draft methods](https://support.espn.com/hc/en-us/articles/360003780852-Draft-Methods), and the [Sleeper read-only API](https://docs.sleeper.com/).

## Retirement

After the draft, disarm, retain only sanitized evidence that is actually needed, close Chrome normally, and delete the private run directory through the operating system. There is no cloud resource, service identity, API credential, or background process to retire.
