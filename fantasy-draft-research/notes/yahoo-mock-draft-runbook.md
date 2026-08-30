# Repeatable Yahoo mock-draft runbook

Use this procedure to rehearse the Yahoo draft interface and capture comparable results. It is intentionally separate from the real-league draft workflow: public mock rooms can use different team counts, scoring, rosters, and draft positions.

For detailed navigation, waiting-room controls, and draft-room behavior, see [Yahoo mock drafts](yahoo-mock-drafts.md). Complete Yahoo sign-in through the owner-controlled [Yahoo login flow](yahoo-login.md); do not record credentials, cookies, session data, or URLs containing room/session parameters.

## Before joining

1. Refresh the real league's scoring, roster, team-count, draft-order, and keeper assumptions in the applicable league notes.
2. State the rehearsal objective in one sentence, such as "test RB-first from an early snake slot" or "practice a 30-second decision loop." Do not call a public mock a direct simulation unless its setup matches the league.
3. Prepare a short, ordered candidate list for each early-round roster need. It must be easy to refresh, not a rigid pick script.
4. Open Yahoo Draft Central and choose the practice mode:
   - Use an **Instant Mock Draft** for immediate interface practice.
   - Use **Live Mock Drafts → Standard Drafts** to select a numbered seat in a public snake room.
5. Keep one mock session active at a time. Do not save private room identifiers or invite links.

## Verify the room

In the waiting room, record the visible setup before the countdown starts:

- mock type and snake or salary-cap format;
- team count and roster size;
- scoring type and material deviations from the target league;
- assigned draft position and pick-clock length; and
- whether autodraft is enabled.

Run Yahoo's system test when available. Leave the room and rejoin a suitable one if the position, format, or timer makes the rehearsal invalid. This is preferable to trying to reinterpret mismatched results afterward.

## Run the draft

1. Keep **Autodraft** off for a manual-logic rehearsal. Turn it on only as an explicit emergency fallback, and record the reason and every resulting pick.
2. Before each turn, refresh the available-player shortlist against the roster, scoring, position scarcity, and draft-board state.
3. Monitor continuously while the draft is active. With a 30-second clock, use bounded, fresh observation windows and increase attention in the final 15 seconds; periodic minute-scale checks are too slow.
4. When Yahoo shows **Your Turn**, refresh availability once, select the highest viable candidate, then record the rationale after the pick completes.
5. Verify every selection with all three visible signals:
   - the selected player occupies the intended position or flex role;
   - Yahoo's last-pick display names that player; and
   - the roster count increments.
6. If the target position, selected row, or roster state differs from the plan, repair the plan before the next turn. Match Yahoo's exact visible position tag; do not infer a position from arbitrary row text.
7. Record only your own results and aggregate room context. Exclude participant names, account data, cookies, session values, and room-specific links.

## Complete and capture results

When Yahoo reports **Draft Complete**, verify the final roster count and open starting slots. Then copy [the result template](mock-draft-results/TEMPLATE.md) to a dated filename such as `YYYY-MM-DD-standard-slot-3.md` and complete it from visible Yahoo results.

Capture these fields while the result page is still available:

- setup: format, teams, roster size, slot, clock, and deviations from the real league;
- every selection: round, overall pick, player, position, Yahoo grade when shown, and manual/autodraft mode;
- final Yahoo outcome: overall grade, projected finish, projected points, and gap to first when shown;
- projected points by position when Yahoo exposes them; and
- two to five evidence-based lessons, including execution misses separately from strategy conclusions.

Use `not shown` rather than estimating a Yahoo grade, projection, or rank that was not visible. A completed roster and an incomplete result card are still a valid rehearsal; do not invent missing analytics.

## Review before saving

- Is the report identifier-safe and free of participant names, room IDs, account/session values, and private URLs?
- Does every pick agree with the final roster or Yahoo's draft board?
- Are automated picks clearly marked rather than presented as manual decisions?
- Are setup differences prominent enough to prevent over-generalizing the result to the real league?
- Does each lesson identify the observed evidence and a specific next test?

Link the new report from the appropriate strategy or retrospective note only when it adds a durable lesson. The report itself is the source of truth for that rehearsal.

## Recovery rules

- **Wrong seat or format:** leave before the draft starts and choose another room.
- **Tool/browser control interrupted:** state that the manual monitoring loop is interrupted; the owner takes over or the result is marked as partly automated.
- **Pick timed out:** record the autodrafted player and the missed intended target, then rebuild the next shortlist from the actual roster.
- **Yahoo result data unavailable:** save the verified selections and mark unavailable fields as `not shown`.

The detailed platform constraints and destructive controls, including reset/undo behavior, remain in [Yahoo mock drafts](yahoo-mock-drafts.md).

## Evidence basis

- [Yahoo mock drafts](yahoo-mock-drafts.md) records the observed Yahoo navigation, waiting-room, draft-room, and recovery behavior used by this runbook.
- [Yahoo league scoring and settings](yahoo-league-scoring-and-settings.md) is the league-specific comparison point for mock-room differences.
- Existing [mock-draft results](mock-draft-results/) demonstrate the report fields and retrospective use of the template.
