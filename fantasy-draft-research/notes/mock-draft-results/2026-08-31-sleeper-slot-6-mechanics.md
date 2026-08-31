# Sleeper mock-draft mechanics walkthrough — 2026-08-31

This note records a live, authenticated walkthrough of Sleeper's legacy
Draftboard for the 2026 keeper league described in the
[league knowledge base](../sleeper-2026-keeper-league-knowledge-base.md). The
walkthrough reached the start confirmation but did not complete an in-draft
selection. Findings below are separated into directly verified behavior and
unverified behavior so the note can be used safely as a rehearsal checklist.

Private board, league, account, participant, invite, and session identifiers
are intentionally excluded.

## Walkthrough status

| Item | Observed result |
|---|---|
| Entry point | League page, then **Mock Drafts** |
| Board creation | **New Mock Draft** cloned the league configuration |
| Format | 12-team, 16-round snake |
| Pick clock | 1.5 minutes |
| Assigned slot | Slot 6 was claimed automatically for the signed-in owner |
| Keeper setup | Jahmyr Gibbs was manually placed at 1.06 and visibly verified |
| Queue preparation | Multiple 2.07 candidates were added before start |
| Draft start | Start confirmation opened; the draft did not transition into a verified live state during this run |
| Completed selections | None; Gibbs was a pre-draft manual keeper tile, not a live pick |

## What creating the league mock copies

Creating a mock from the league produced a new board with these values already
set:

- 12 teams and 16 rounds;
- snake order;
- 1.5 minutes per pick;
- the league's roster positions;
- the signed-in owner's slot 6 assignment; and
- keeper tiles already assigned on the source league board.

The board did **not** contain Gibbs because the live league board had not yet
displayed that keeper. The mock therefore proved an important boundary: a
league mock copies the keeper state visible in Sleeper, not a keeper that is
known only from an offline decision or user confirmation.

## Keeper and manual-pick mechanics

Before the draft starts, selecting an empty board cell opens two actions:

- **Set Player** — manually populate that exact pick; and
- **Let CPU Auto-pick** — delegate the pick to Sleeper's CPU.

For 1.06, **Set Player** opened a modal labeled for manual pick number 6.
Selecting Jahmyr Gibbs placed a visible `J. Gibbs`, `RB - DET` tile at 1.06.
The roster counters then changed to `1/16` overall and `RB 1/1`.

Treat this workflow as board setup, not draft history. A player placed this way
should be reported as a keeper or manual pre-draft tile rather than as an
owner-made live selection.

## Draft settings observed

The header settings menu exposed:

- **Copy Invite Link**;
- **Try New Draftboard**;
- **Create a Copy**;
- **Draft Settings**;
- **Start Draft**; and
- **Delete Draft**.

No invite link or private board identifier was copied into this note.

### General settings

The General Settings tab exposed the following controls:

- draft name;
- draft type: Snake, Linear, or Auction;
- rankings/scoring preset: Standard, PPR, Half-PPR, 2QB, IDP, or Dynasty
  variants of those presets;
- team count from 4 through 22;
- time per pick: No Limit, 10 seconds, 30 seconds, 60 seconds, 1.5 minutes,
  2 minutes, 3 minutes, 5 minutes, 10 minutes, 30 minutes, 1 hour, 2 hours,
  4 hours, 8 hours, 12 hours, or 24 hours;
- CPU auto-pick when a user runs out of time;
- player pool: All, Rookies Only, or Vets Only;
- Third Round Reversal;
- alphabetical sorting instead of ADP; and
- team-name display.

These are Draftboard controls. A scoring preset changes the ranking context;
it does not establish that every custom league scoring rule is simulated.

### Roster settings

The cloned board contained the exact 16 draft slots expected for this league:

| Position | Count |
|---|---:|
| QB | 1 |
| RB | 1 |
| WR | 2 |
| TE | 1 |
| FLEX (WR/RB/TE) | 2 |
| K | 1 |
| DEF | 1 |
| IDP FLEX | 1 |
| Bench | 6 |

The editor also offered FLEX (WR/RB), FLEX (WR/TE), superflex
(QB/WR/RB/TE), DL, LB, DB, and additional bench positions.

### Draft order

The Draft Order tab displayed 12 numbered columns. Slot 6 contained the
signed-in owner, while every other slot was empty. The tab offered
**Randomize Teams** and allowed the commissioner to build the order manually.

An empty opponent slot remained visibly claimable before start. This is the
surface used to invite or assign people; unclaimed slots are the candidates
for CPU control in a solo rehearsal.

## Player pool and queue mechanics

The player pool included:

- search and position filters for All, QB, RB, WR, TE, FLEX, K, DEF, and IDP;
- Watchlist, Show Drafted, and Rookies Only toggles;
- rank, player, ADP, bye, projected points, projected average, and detailed
  rushing, receiving, and passing columns; and
- a queue action on each available-player row.

The right sidebar exposed **Queue**, **Roster**, and **Chat** tabs plus an
**Auto-pick** control. Adding players marked their pool rows as selected and
created ordered queue entries with ADP, projected points, and a Remove action.
The first entry was labeled `next pick`.

For this slot-6 rehearsal, the queue was prepared before start around the
expected 2.07 decision window. This confirms the desired operating pattern:
prepare a ranked fallback list before a timed state, then use opponent turns
for queue maintenance.

## Practical runbook derived from the walkthrough

1. Open the target league and enter **Mock Drafts** from that league rather
   than creating an unrelated generic board.
2. Create a new mock and verify team count, rounds, order, timer, slot, and all
   roster positions before making any board edits.
3. Compare every visible keeper tile with the authoritative keeper list. Add a
   missing keeper manually only in the exact charged pick.
4. Open Draft Order and verify the owner column. Assign or claim participants
   deliberately; leave opponents unclaimed only when CPU opponents are the
   intended rehearsal setup.
5. Build a short ordered queue before pressing **Start Draft**. The queue
   should cover the next decision plus several safe fallbacks.
6. Start the draft only after the board and queue pass verification.
7. During a timed turn, inspect the minimum necessary state, choose the first
   valid candidate, submit, and verify the board tile before recording the
   pick as owner-made.
8. Label timer-driven, forced, or delegated selections as CPU/autopick. Do not
   infer manual selection from a queued player.

## Unverified / needs confirmation

This run did not produce direct in-draft evidence for the following behaviors:

- whether unclaimed teams immediately operate as CPU opponents after start;
- the exact countdown and commissioner auto-pick behavior at timer expiry;
- manual pick submission and tile verification during the live clock;
- queue consumption when Auto-pick is enabled;
- pause, resume, timer reset, force-pick, and edit-pick controls during a live
  draft; and
- completed-board analytics, grades, projections, and saved-result behavior.

The existing [Sleeper mock-draft runbook](../sleeper-mock-draft-runbook.md)
contains platform-support documentation for several of these mechanics, but
they remain unverified in this specific walkthrough. The next rehearsal should
start from a short-clock duplicate board, complete at least three owner turns,
exercise pause/resume once during an opponent turn, and then inspect the saved
result.

## Evidence

- Authenticated Sleeper league page and legacy Draftboard inspected on
  2026-08-31.
- New league mock created and visually checked before start.
- Draft Settings tabs inspected: General Settings, Roster Settings, and Draft
  Order.
- Gibbs tile visibly verified at 1.06 after manual pre-draft placement.
- Player-pool and queue state inspected after adding multiple candidates.
- [Sleeper 2026 keeper-league knowledge base](../sleeper-2026-keeper-league-knowledge-base.md)
  for the target league and keeper assumptions.
- [Repeatable Sleeper mock-draft runbook](../sleeper-mock-draft-runbook.md) for
  the operating boundary and platform-support references.
