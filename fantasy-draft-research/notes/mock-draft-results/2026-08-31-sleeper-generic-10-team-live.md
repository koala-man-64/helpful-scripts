# Sleeper generic Draftboard live-mock mechanics — 2026-08-31

This note records a completed live walkthrough of Sleeper's generic NFL
Draftboard. It is a mechanics rehearsal, not a comparable simulation of the
target Yahoo league. Private board, account, participant, invite, and session
identifiers are intentionally excluded.

## Setup and completion

| Item | Observed result |
|---|---|
| Entry point | **Mock Drafts** then **New Mock NFL Draft** |
| Board type | Generic Sleeper Draftboard, not league-cloned |
| Format | 10-team standard snake |
| Rounds | 15 |
| Pick clock | 2 minutes per pick |
| Assigned slot | Claimed slot 1 (picks 1.01, 2.10, 3.01, and so on) |
| Opponents | Unclaimed before start; their selection mode was not independently verified |
| Actual Sleeper queue | Empty during the run |
| Final board state | All 15 owner slots filled; no active countdown remained |
| Board grade or projection | Not shown |

The generic board therefore cannot be used as evidence for target-league
scoring, roster, keeper, or draft-order behavior. Those must be verified on a
league-cloned mock.

## Selection provenance

The final owner column contained 15 tiles. Pick provenance is deliberately
conservative:

| Rounds | Visible result | Selection mode supported by the board state |
|---|---|---|
| 1.01 | J. Gibbs | CPU/autopick — the board explicitly entered auto-pick after the clock expired |
| 2.10–3.01 | C. Brown; T. McBride | CPU/autopick — populated while auto-pick was active, with no manual confirmation |
| 4.10 | D. Adams | Not directly confirmed — an attempted player-grid action overlapped with this tile, but Sleeper gave no confirmed manual-pick signal |
| 5.01–15.01 | T. Etienne; D. Moore; T. McLaurin; J. Herbert; A. Jones; S. LaPorta; T. Lawrence; W. Marks; Rams D/ST; J. Coleman; C. Boswell | CPU/autopick — auto-pick reappeared after the next owner-clock expiry and completed the board |

Do not reclassify the 4.10 result as a manual pick from the completed roster
alone. A final tile is proof of the player, not of how the player was chosen.

## Mechanics observed

1. **Start has a confirmation boundary.** Selecting **Start Draft** produced a
   browser confirmation dialog. The original click timed out, but the board
   was live after the confirmation was resolved. A click timeout is an unknown
   outcome: inspect the dialog and the board state before retrying.
2. **The first owner clock began immediately.** Slot 1 displayed a two-minute
   countdown after start. A ranked fallback must be ready before resolving the
   start confirmation.
3. **Visible player text was not a reliable action handle.** The player pool
   rendered as a virtualized grid. It showed names visually, but a semantic
   player-row lookup could not reliably resolve a clickable row in the active
   browser-control surface. Searching a name and clicking an assumed grid
   coordinate did not yield a verified manual-pick confirmation.
4. **Auto-pick is visible and consequential.** After the 1.01 expiry, Sleeper
   explicitly showed that the owner was on auto-pick. Turning it off during a
   later opponent turn did not retroactively change the first three tiles, and
   it reappeared after the next owner-clock expiry.
5. **An empty queue is not a fallback.** The Queue sidebar visibly showed no
   players. A local candidate list and an active player search had no effect on
   the order Sleeper used for auto-pick.
6. **Snake turn pairs need two ready choices.** Slot 1's 4.10 and 5.01 turns
   arrived back-to-back. The second choice must be safe before the first turn
   begins; there is no useful window for interface investigation between them.
7. **Completion needs more than a roster count.** The saved board showed all
   15 owner tiles and no active timer. That verifies completion, but not
   manual-control provenance or target-league simulation quality.

## Next-run controls

1. Use a **No Limit** or paused copy to qualify the exact manual player-row
   action before starting any timed test. Verify one selection by tile, roster
   increment, and turn advance.
2. Before every owner turn, verify both the visible auto-pick state and the
   actual Queue sidebar. Populate the queue with only acceptable, ordered
   fallbacks; do not rely on a search filter or an offline list.
3. Resolve start confirmation and immediately re-read the active slot, timer,
   and auto-pick indicator. Do not retry a timed-out start action blindly.
4. If the manual action path is unavailable, stop trying alternate selectors
   on the clock. Pause when permitted or let the owner take the selection, then
   record any site-generated result as CPU/autopick.
5. Run the timed rehearsal only after the manual-control qualification passes,
   and use a league-cloned board when the result will inform keeper, scoring,
   roster, or draft-order decisions.

## Evidence

- Direct visual observation of an authenticated generic Sleeper Draftboard on
  2026-08-31, with private information excluded.
- [Sleeper mock-draft runbook](../sleeper-mock-draft-runbook.md) for the
  durable operating controls and official-support references.
- [Earlier league-cloned mechanics walkthrough](2026-08-31-sleeper-slot-6-mechanics.md)
  for the distinct 12-team keeper-board observations.
