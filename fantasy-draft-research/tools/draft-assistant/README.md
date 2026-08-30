# Fantasy Draft Assistant

This local CLI turns a frozen league configuration and a manually supplied
player board into deterministic recommendations, a vetted queue, and a durable
draft-event journal. It performs no network requests and contains no browser
driver. Codex may use its in-app browser to observe Yahoo and pass sanitized
visible state to this CLI.

## Safety boundary

Yahoo is authoritative for the clock, current turn, available players, picks,
roster, and final result. The CLI can issue a pick intent, but it never clicks
Yahoo itself. A browser operator may act on an intent only when its safety
decision is `allow`. One uncertain click, state mismatch, reconnect, modal,
authentication challenge, or verification failure disarms automatic entry.

Never put credentials, cookies, storage state, private URLs, league IDs, room
IDs, or participant identities in configuration, observed-state files, logs,
or the repository. Runtime directories and SQLite files are gitignored.

Real automatic mode is a rehearsal-gated capability. A successful install or
test run is not evidence that live submission is ready.

## Install and test

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

## Input files

`league.json` records the active draft shape, roster, keeper slots, and safety
defaults. Keep `active_teams`, `maximum_teams`, and `draft_slots` separate.
`draft_slots` is the ordered list of private local aliases such as `team-1`
through `team-8`; do not store real team or manager names.

`players.csv` uses these columns:

```text
player_id,name,nfl_team,position,projection,tier,adp,league_fit,scarcity,wait_risk,roster_utility,risk,source,source_family,checked_at,status
```

Names are descriptive only. The stable local player ID plus exact position and
NFL team form the identity gate. Duplicate or ambiguous identities fail
initialization rather than being guessed.

## Typical workflow

```powershell
draft-assistant init --league league.json --players players.csv --run data/runtime/draft-2026
draft-assistant doctor --run data/runtime/draft-2026
draft-assistant recommend --run data/runtime/draft-2026 --top 3 --json
draft-assistant queue --run data/runtime/draft-2026 --json
draft-assistant observe-pick --run data/runtime/draft-2026 --overall 1 --player-id player-001
draft-assistant reconcile --run data/runtime/draft-2026 --observed-state yahoo-state.json
draft-assistant arm --run data/runtime/draft-2026 --mode mock --room-fingerprint local-alias
draft-assistant issue-intent --run data/runtime/draft-2026 --observed-state yahoo-state.json
```

Real acknowledgement is a separate transition and does not bypass any safety
check:

```powershell
draft-assistant acknowledge-real --run data/runtime/draft-2026 --room-fingerprint local-alias
draft-assistant arm --run data/runtime/draft-2026 --mode real --room-fingerprint local-alias
```

## Visible Yahoo state

The browser operator supplies a local JSON file containing only visible,
sanitized fields:

```json
{
  "room_fingerprint": "local-alias",
  "your_turn": true,
  "overall_pick": 16,
  "current_team": "team-1",
  "clock_seconds": 54,
  "roster_count": 1,
  "autodraft_off": true,
  "captured_at": "2026-08-30T23:29:55Z",
  "unavailable_player_ids": ["keeper-player-id"],
  "roster_player_ids": [],
  "rows": [
    {"name": "Example Player", "nfl_team": "DET", "position": "WR", "available": true, "has_draft_control": true}
  ],
  "authentication_challenge": false,
  "modal_ambiguity": false,
  "reconnecting": false,
  "control_interrupted": false
}
```

The operator must persist an intent before clicking, click once, mark it
submitted, and verify all four post-pick signals. Never retry an uncertain
click.

## Live-use gate

Real automatic mode is not ready until `doctor` passes on freshly confirmed
settings, the frozen board is current, a full mock has zero wrong or duplicate
actions, takeover and queue fallback are witnessed, p99 recommendations remain
under 500 ms, and there are no external source calls after room-open. Otherwise
use recommendation plus the vetted queue.

Those witnessed rehearsal facts live in a private, local-only
`rehearsal-readiness.json` beside the SQLite database:

```json
{
  "full_mock_passed": true,
  "zero_wrong_duplicate_ambiguous_actions": true,
  "takeover_witnessed": true,
  "queue_fallback_witnessed": true,
  "no_external_calls_after_room_open": true,
  "recommendation_p99_ms": 100
}
```

Do not create this file from assumptions or automated unit-test results. It is
a signed-off record of witnessed browser rehearsal evidence. `arm --mode real`
remains blocked when the file is missing or any gate is false.

## Recovery and retirement

After a process restart, run `replay`, compare the result with Yahoo, and run
`reconcile` before re-arming. A mismatch or uncertain submission stays in
takeover mode; never repair it by rewriting SQLite events.

After the draft, disarm the run, retain only any sanitized evidence you
actually need, close the browser session normally, and delete the private run
directory through the operating system. The application has no cloud resource,
service identity, API credential, or background process to retire.
