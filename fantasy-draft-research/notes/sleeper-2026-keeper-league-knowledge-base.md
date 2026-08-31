# Sleeper 2026 keeper-league knowledge base

This note is the working source of truth for the user's 2026 Sleeper draft. It
was assembled from the authenticated Sleeper league, team, and draftboard views
on 2026-08-31, plus the user's explicit keeper confirmation.

Privacy-sensitive league IDs, draft IDs, usernames, opponent names, invite
links, and account/session data are intentionally omitted. Recheck Sleeper
before using any time-sensitive detail.

## Executive summary

- Format: 12-team, head-to-head, half-PPR keeper league.
- Keeper limit: one player.
- Confirmed keeper: Jahmyr Gibbs at pick 1.06.
- First live selection: 2.07, 19th overall.
- Draft: Tuesday, 2026-09-08 at 7:00 PM Central.
- Draft structure: 16-round snake, 90 seconds per pick.
- Core offensive lineup: 1 QB, 1 RB, 2 WR, 1 TE, and 2 FLEX.
- Additional starters: 1 K, 1 team defense, and 1 IDP FLEX.
- Bench/IR: 6 bench and 1 injured-reserve slot.
- Primary construction implication: start with Gibbs, then favor receiving
  volume and flexible WR/RB value. Only one RB is mandatory, while four weekly
  slots can accept wide receivers.

## Source-of-truth boundaries

The Sleeper **Team** page displayed the prior-season roster used during keeper
selection. It is not the post-draft 2026 roster. Once Gibbs is kept, every
non-kept player returns to the draft pool unless the commissioner applies a
different rule outside the visible Sleeper configuration.

Therefore:

- Gibbs is the only known player carried into the draft.
- Baker Mayfield, Drake London, Tee Higgins, Zay Flowers, Travis Kelce, and the
  other prior-roster players must not be treated as already rostered.
- The draftboard did not yet show Gibbs locked when inspected. The keeper and
  first-round cost come from the user's explicit confirmation and must be
  visually verified before the draft begins.

Sleeper is authoritative for the live board, completed picks, current keeper
tiles, and roster state. Rankings, projections, ADP, and external fantasy tools
are decision inputs rather than league state.

## Prior-season roster snapshot

Use this list for keeper history and player familiarity only.

| Position | Players shown on the Team page |
| --- | --- |
| QB | Baker Mayfield |
| RB | Jahmyr Gibbs, Jaylen Warren, Tony Pollard |
| WR | Drake London, Tee Higgins, Zay Flowers, Jayden Reed, Khalil Shakir |
| TE | Travis Kelce, Brenton Strange |
| IDP | Myles Garrett, Nick Cross |
| K | Chase McLaughlin |
| DEF | Jacksonville Jaguars, Buffalo Bills |

## League configuration

| Setting | Verified value |
| --- | --- |
| Teams | 12 |
| Scoring | Half-PPR |
| Playoffs | 6 teams, beginning Week 15 |
| Waiver priority | Rolling waivers |
| Weekly waiver clear | Wednesday at 2:00 AM Central |
| Post-clear availability | Immediate free agents |
| Trade deadline | After Week 11 |
| Draft-pick trading | Enabled |
| Maximum keepers | 1 |
| Injured reserve | 1 slot |

### Starting roster and draft rounds

| Slot | Count |
| --- | ---: |
| QB | 1 |
| RB | 1 |
| WR | 2 |
| TE | 1 |
| FLEX (WR/RB/TE) | 2 |
| K | 1 |
| Team defense | 1 |
| IDP FLEX | 1 |
| Bench | 6 |
| Total draft rounds | 16 |

## Scoring details that affect player value

### Offense

- Passing: 0.04 points per yard, 4 per touchdown, -2 per interception, and 2
  per two-point conversion.
- Rushing: 0.1 points per yard, 6 per touchdown, and 2 per two-point
  conversion.
- Receiving: 0.5 per reception, 0.1 per yard, 6 per touchdown, and 2 per
  two-point conversion.
- Miscellaneous: -2 per fumble lost and 6 per fumble-recovery touchdown.
- Return production: 0.1 per punt-return yard and 0.05 per kickoff-return yard.
  Verify actual return roles before applying a material ranking boost.

### Kicking

- Made field goals: 3 points through 39 yards, 4 from 40-49, and 5 from 50+.
- Made PAT: 1.
- Missed field goal or PAT: -1.

### Team defense

- Standard big plays: 6 per defensive touchdown; 2 per interception, fumble
  recovery, safety, or blocked kick; 1 per sack.
- Points allowed rewards range from 6 for a shutout to 1 for allowing 28-34.
- Yardage allowed rewards range from 4 for under 100 yards to 1 for 300-349.
- Yardage penalties begin at 400 yards allowed and reach -4 at 550+.

### IDP

- Touchdown: 6.
- Sack or interception: 4.
- Fumble recovery, forced fumble, safety, or blocked kick: 2.
- Tackle, tackle for loss, or pass defended: 1.

One IDP starter with these settings rewards high-snap, high-impact defenders,
but the position remains deep enough to address late unless the draft produces
an exceptional value.

## Keeper and draft map

Jahmyr Gibbs occupies 1.06. The first round is therefore not an actionable
selection for this team.

| Round | Pick | Overall | Status |
| ---: | ---: | ---: | --- |
| 1 | 1.06 | 6 | Jahmyr Gibbs keeper |
| 2 | 2.07 | 19 | First live selection |
| 3 | 3.06 | 30 | Live selection |
| 4 | 4.07 | 43 | Live selection |
| 5 | 5.06 | 54 | Live selection |
| 6 | 6.07 | 67 | Live selection |
| 7 | 7.06 | 78 | Live selection |
| 8 | 8.07 | 91 | Live selection |
| 9 | 9.06 | 102 | Live selection |
| 10 | 10.07 | 115 | Live selection |
| 11 | 11.06 | 126 | Live selection |
| 12 | 12.07 | 139 | Live selection |
| 13 | 13.06 | 150 | Live selection |
| 14 | 14.07 | 163 | Live selection |
| 15 | 15.06 | 174 | Live selection |
| 16 | 16.07 | 187 | Live selection |

### Keeper-adjusted availability

The inspected board already showed these players assigned as keepers elsewhere:

- Chris Olave
- Javonte Williams
- Chase Brown
- Cam Skattebo
- Christian McCaffrey
- Quinshon Judkins
- Kenneth Walker
- Travis Etienne

This list is a dated observation, not a final exclusion list. Other keeper
decisions were still outstanding. Refresh the complete keeper board before
building final tiers or estimating who can reach 2.07.

## Draft strategy

### Early rounds

1. Treat Gibbs as the RB1 and do not select for positional need at 2.07.
2. At 2.07, take the best player remaining from the top WR/RB tier. Prefer a
   target-earning wide receiver when values are otherwise close because the
   lineup can start four wide receivers but requires only one running back.
3. Through Rounds 3-5, build at least three credible weekly WR/RB starters in
   addition to Gibbs. Do not force a predetermined position through a clear
   tier break.
4. Recalculate availability after every keeper assignment. Ordinary ADP does
   not account for this league's exact removed-player pool.

### Quarterback and tight end

- This is a one-QB league with four-point passing touchdowns. Do not pay an
  early premium merely to fill QB; prioritize an elite dual-threat value or
  wait for the position's depth.
- Travis Kelce and Brenton Strange were on the prior roster but are not carried
  forward. Tight end is an open draft need unless one is later confirmed as a
  keeper, which would conflict with the Gibbs-only rule.
- Use tier scarcity rather than a fixed round for TE. Take an elite option only
  when the opportunity cost against available WR/RB is acceptable.

### Late rounds

- Fill the single IDP slot late from high-snap edge rushers, linebackers, or
  safeties whose scoring profile fits sacks, tackles, and passes defended.
- Draft one kicker and one team defense near the end. A second defense is not a
  default use of a six-player bench.
- Use remaining bench spots on contingent-value RBs and receivers with a clear
  route to targets or return-yard work. Avoid low-ceiling backup quarterbacks
  and tight ends unless the starter's risk demands one.

## On-the-clock decision rule

When the team is on the clock:

1. Confirm the current pick and that Gibbs occupies 1.06.
2. Read the available-player pool from Sleeper; never infer availability from
   an external rank list.
3. Remove already-filled roster needs and compare the highest remaining tier at
   WR, RB, TE, and QB.
4. Prefer the player with the larger tier drop after him. Break close ties with
   role certainty, half-PPR receiving volume, lineup flexibility, and injury
   risk.
5. Submit the pick only after confirming the player name and active team slot.
6. Verify the player tile on the draftboard before recording the selection as
   complete.

## Pre-draft refresh checklist

Complete this immediately before the draft:

- [ ] Gibbs is visibly locked at 1.06.
- [ ] Draft starts at 7:00 PM Central on 2026-09-08.
- [ ] The team owns slot 6 and the board is still a 16-round snake.
- [ ] Every opponent keeper and charged pick is captured.
- [ ] The first live pick remains 2.07.
- [ ] Injuries, suspensions, transactions, and depth-chart changes are refreshed.
- [ ] Rankings use 12-team half-PPR assumptions with one required RB and two FLEX.
- [ ] A short queue exists for 2.07 and at least the next two turns.
- [ ] Browser notifications, power settings, and connection are ready for a
      90-second clock.

## Open questions and update triggers

Update this note whenever any of the following changes:

- keeper assignments or charged rounds;
- draft time, timer, order, or traded picks;
- roster slots, waiver rules, playoffs, or scoring;
- high-impact injury, suspension, trade, or depth-chart news; or
- a completed mock or live draft provides new evidence about positional runs.

Unverified / needs confirmation:

- The commissioner must still apply or display Gibbs at 1.06 on the live
  draftboard.
- The final keeper-adjusted player pool was not complete at inspection time.
- Sleeper's preseason projections and ADP are time-sensitive and must be
  compared with current independent rankings before draft day.

## Evidence

- Authenticated Sleeper league overview inspected 2026-08-31.
- Authenticated Sleeper Team page inspected 2026-08-31.
- Authenticated Sleeper 2026 draftboard preview inspected 2026-08-31.
- User confirmation on 2026-08-31: "my keeper is gibbs in the first."
- Platform operating and privacy boundaries: [Sleeper fantasy-football tool
  guide](sleeper.md) and [Sleeper mock-draft runbook](sleeper-mock-draft-runbook.md).
