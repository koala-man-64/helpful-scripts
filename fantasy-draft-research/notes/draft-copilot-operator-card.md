# Fantasy draft copilot operator card

Use this live loop for standard, half-PPR, or PPR one-QB snake redrafts on Yahoo, ESPN, or Sleeper. The manager owns sign-in, MFA/CAPTCHA/recovery, approvals, commissioner controls, and takeover. Chrome is the signed-in sensor/executor; Python is the offline deterministic core.

## Before room-open

1. Confirm v1 scope. Auction, dynasty, best ball, superflex, and IDP are unsupported. Record team count, roster/flex slots, scoring overrides, draft position/order, and every keeper; keepers are rostered and unavailable.
2. Refresh the direct sources, then merge a sanitized Chrome/manual projection snapshot. The projection import is mandatory: Boris supplies tiers and Sleeper supplies identity/status data, but neither direct adapter supplies compatible projections. Use the league's `standard`, `half-ppr`, or `ppr` format:

   ```powershell
   draft-assistant research-refresh --source boris sleeper --scoring-format standard --output data/cache/research.json
   Get-Content -Raw data/raw/sanitized-research.json | draft-assistant research-import --snapshot - --merge data/cache/research.json --output data/cache/research-final.json
   ```

   Do not compile `research.json` by itself. `compile-board` must reject a bundle without a compatible projection family.
3. Freeze the board, provision the private run/event store, and check it:

   ```powershell
   draft-assistant compile-board --league league.json --research data/cache/research-final.json --output data/cache/board-2026
   draft-assistant init --league league.json --players data/cache/board-2026/board.json --run data/runtime/draft-2026
   draft-assistant doctor --run data/runtime/draft-2026
   ```

4. Inspect `manifest.json`. Mandatory stale evidence blocks compilation; optional stale evidence is omitted. Material post-freeze news requires a parent-linked revision in a new output directory.
5. Open the intended Chrome room and revalidate settings, order, keepers, and scoring. The sanitized observation must carry the matching `config_hash` and `board_hash`. Reconcile once, then arm the exact room before requesting an actionable turn:

   ```powershell
   Get-Content -Raw data/runtime/fresh-state.json | draft-assistant reconcile --run data/runtime/draft-2026 --observed-state -
   draft-assistant arm --run data/runtime/draft-2026 --mode mock --room-fingerprint room-fp:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
   ```

   Real mode additionally requires `acknowledge-real` before `arm --mode real`, and stays blocked until that platform's witnessed, non-consequential timed mock passes. Unit tests are not rehearsal evidence.

## Evidence rules

| Signal | Authority | Maximum age |
| --- | --- | ---: |
| Projection/VBD | Compatible raw projections, or pre-scored projections matching the league | 72 hours |
| Consensus tier | One Boris Chen/FantasyPros family, counted once | 72 hours |
| Draft timing | Separate Yahoo, ESPN, and Sleeper ADP signals | 24 hours |
| Identity/team | Canonical identity plus current team data | 24 hours |
| Status/news | Official team or NFL reporting | 6 hours |
| Discovery only | NBC/Reddit followed to a qualifying source | Never authoritative alone |
| Live room | Sanitized Chrome observation | 5 seconds |

## Every turn

1. Chrome emits a sanitized envelope containing the top-level `ObservedDraftState` plus `control_snapshots`. Use operation `observe` for reconcile/turn/approval, `queue` and `pick` for the submission boundary, and `verify` for the result. The room fingerprint is `room-fp:` plus a SHA-256 digest; never send its private input. The complete synthetic state and snapshot shape live in `tools/draft-assistant/tests/fixtures/yahoo_state.json` and `tools/draft-assistant/tests/test_platforms.py`. Never include raw DOM, cookies, storage, credentials, private URLs/IDs, or participant identities.
2. Reconcile and calculate the turn from that fresh state:

   ```powershell
   Get-Content -Raw data/runtime/fresh-state.json | draft-assistant turn --run data/runtime/draft-2026 --observed-state - --json
   ```

3. Review the primary, two fallbacks, pair plan, exclusions, and component explanations. Recommendation is pure; it does not claim a queue write. Explicitly approve one player and the exact ordered queue of one to three current candidates, with the approved player first:

   ```powershell
   Get-Content -Raw data/runtime/fresh-state.json | draft-assistant approve-pick --run data/runtime/draft-2026 --observed-state - --player-id player-001 --queue player-001 player-002 player-003
   ```

4. Approval is bound to the board, recommendation, room, pick, availability, queue baseline, and observation for at most 15 seconds. Yahoo and ESPN may consume queued players for auto-draft, so queue approval is material.
5. Chrome immediately re-observes. Changed room/pick/availability/recommendation/queue or ambiguous control voids approval. With at least 20 seconds remaining, it replaces and verifies the exact queue, then clicks and submits the approved player once.
6. After the single Chrome click, record only an unverified attempt with `mark-submitted --observed-state -`. Then verify a fresh state from the same platform and room using `verify-pick --observed-state -`: expected pick/room advancement, matching last-pick evidence, selected player in both our roster IDs and unavailable IDs, and no authentication/modal/reconnect/control ambiguity.
7. Record confirmed manager-approved Chrome provenance only after verification. If explicit last-pick evidence says the timer expired and the platform auto-drafted, record platform auto-draft; a queue entry or Autodraft toggle is not submission proof.

## Stop and take over

- Cancel only an issued-but-unsubmitted intent: `draft-assistant cancel-intent --run data/runtime/draft-2026 --intent-id INTENT_ID --reason "manager changed the plan"`.
- A submitted intent cannot be cancelled or retried. Unknown or contradictory results require manager takeover until resolved.
- Missing controls, ambiguous identity, stale state, queue mismatch, changed state, reconnect/auth/modal ambiguity, or fewer than 20 seconds means no write.
- Never rehearse in a real draft. Qualify Yahoo, ESPN, and Sleeper writes independently.

## Privacy and closeout

Keep raw/restricted research, runtime state, private manifests, and rehearsal evidence local and gitignored. Commit only code, schemas, methodology, synthetic fixtures, and sanitized examples. After the draft, disarm, retain only necessary sanitized evidence, close Chrome normally, and delete the private run directory through the operating system.
