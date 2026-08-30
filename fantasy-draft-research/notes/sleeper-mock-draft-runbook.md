# Repeatable Sleeper mock-draft runbook

Use this procedure to rehearse roster construction and record comparable Sleeper Draftboard results. It is a preparation workflow: for a Yahoo-hosted league, Yahoo remains authoritative for the live clock, player pool, completed picks, roster, and keepers.

## Operating boundary

The account owner completes Sleeper sign-in, board creation, draft selections, queue edits, and commissioner controls directly in Chrome. Do not provide credentials, verification codes, cookies, tokens, session identifiers, or a Draftboard link to a third party. Do not automate or scrape the Sleeper web application, inspect browser storage, or use account data through an unapproved third-party service.

Use the owner-controlled [Sleeper sign-in and Draftboard handoff](sleeper-login.md) before this runbook. The detailed platform configuration and Yahoo-league assumptions remain in [Sleeper fantasy-football tool guide](sleeper.md).

## Before creating a Draftboard

1. Refresh the authoritative league's scoring, roster, team count, draft order, and keeper constraints.
2. State one rehearsal objective, such as "test an eight-team non-PPR build from slot 1 with the keeper charged to round 7." Treat a mock as a rehearsal, not a perfect league simulation.
3. Prepare an ordered candidate list for the first several roster needs. Keep it easy to revise as the board changes.
4. The owner signs in and opens **Draftboards (Mock Drafts)** from beneath the league list in Sleeper's web app.
5. The owner creates a football Draftboard, claims the intended slot, and does not copy the shareable board link into a note or chat.

## Configure and verify the board

Before starting, the owner visually verifies and records:

- draft type and order, such as snake, linear, third-round reversal, or auction;
- team count, scoring type, roster positions, and player-pool restriction;
- assigned slot, round count, and time-per-pick;
- keeper tiles, their cost/round, and the team column in which each tile appears;
- CPU opponents and whether commissioner auto-pick is enabled; and
- every material difference from the target league.

Sleeper supports mock-board scoring, team-count, draft-order, roster, and keeper configuration, but not every target-league scoring rule is confirmed to map exactly. Mark unsupported or unverified rules explicitly rather than treating the board as exact.

If a setup value is wrong, the owner corrects it before the draft begins. Starting a mismatched board and trying to normalize its result later defeats the rehearsal.

## Run the draft

1. The owner starts the Draftboard and makes every Sleeper pick directly.
2. Before each turn, compare the visible roster, remaining candidates, positional scarcity, keeper effects, and board state against the rehearsal objective.
3. When the owner is on the clock, choose the best available candidate from the current shortlist, then capture the rationale after the pick is visible on the board.
4. Verify each completed selection by checking the board tile, roster assignment, round/overall position, and the player's intended role.
5. If a selection or keeper tile does not match the intended plan, revise the remaining plan from the actual board state before the next pick.
6. Record only the owner's roster and aggregate board settings. Exclude participant names, owner IDs, team IDs, avatars, invite links, account data, and session values.

Sleeper's timer can be a soft timer when commissioner auto-pick is off: expiration need not make a selection. Only the commissioner can configure league-wide auto-pick or force a CPU pick. Do not use either as an unattended-pick mechanism; if it is used, record the resulting pick as CPU/autopick and the reason.

## Pause, edit, and recover

- **Wrong configuration before start:** the owner fixes the settings before beginning.
- **Need a break:** the commissioner pauses the draft. On resume, verify the active team and timer; Sleeper resets the on-clock timer after a manual pause.
- **Incorrect board selection:** the owner uses Sleeper's visible edit capability, then records the correction and re-verifies the keeper tiles, roster, and current turn.
- **Soft-timer expiry:** the owner resumes from the visible board state. Do not assume a pick was made.
- **Forced CPU pick:** capture it as an automated pick, including the visible player, team, and reason it was forced.
- **Login, verification, or access prompt:** stop and let the owner complete it. Do not work around it by exporting browser data or opening another account.

## Complete and record results

After the mock finishes, the owner opens the saved Draftboard result and verifies the final roster count, required starter slots, and keeper placement. Copy [the Sleeper result template](mock-draft-results/SLEEPER-TEMPLATE.md) to a dated filename such as `YYYY-MM-DD-sleeper-slot-1.md` and complete it from the board.

Capture:

- board setup and every material difference from the target league;
- every selection's round, overall slot, position, player, owner/CPU mode, and rationale;
- final roster construction and keeper verification;
- any board-level grade, projection, or analytics that Sleeper visibly provides; and
- two to five evidence-based execution and strategy lessons.

Use `not shown` for information the board does not display. Do not infer projections, grades, availability, or opponent behavior from an incomplete result screen.

## Review before saving

- Is the report free of account/session material, participant identities, IDs, and board/invite URLs?
- Do the recorded selections agree with the saved Draftboard and final roster?
- Are every CPU/autopick and every post-pick edit labeled clearly?
- Are keeper round costs and locations explicitly verified?
- Does each lesson connect an observed result to a concrete next rehearsal?

## Evidence basis

- [How to Create a Mock Draft](https://support.sleeper.com/en/articles/3982891-how-to-create-a-mock-draft) documents Draftboards, settings, keeper tiles, CPU/human participants, edits, and saved mock results.
- [How does the draft timer work?](https://support.sleeper.com/en/articles/4029085-how-does-the-draft-timer-work) documents soft-timer, commissioner auto-pick, forced CPU pick, pause, and timer-reset behavior.
- [Sleeper General Terms of Use](https://support.sleeper.com/en/articles/5486620-general-terms-of-use) is the source for the no-credential-sharing and no-unapproved-third-party-access boundary.
- [Sleeper fantasy-football tool guide](sleeper.md) records the league-specific preparation assumptions and known limits.
