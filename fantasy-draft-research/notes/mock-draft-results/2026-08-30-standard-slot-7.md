# Yahoo mock draft result — 2026-08-30

Completed in Yahoo's signed-in desktop draft room on 2026-08-30. Private room, league, account, participant, and session identifiers are intentionally excluded.

## Setup and outcome

- Format: public standard snake mock, head-to-head
- Teams: 14
- Roster size: 15
- Draft position: 7
- Pick clock: 30 seconds
- Yahoo overall grade: **A**
- Yahoo projected finish: **4th of 14**
- Yahoo projected total: **2,447.25 points**
- Gap to projected first: **30.76 points**

This was an interface and decision-speed rehearsal, not an exact simulation of the private league. The target league has eight teams, non-PPR scoring, a first-overall slot, 16 rounds, and a seventh-round TreVeyon Henderson keeper.

## Results

| Round | Overall | Slot | Player | Yahoo grade | Selection mode |
|---:|---:|---|---|:---:|---|
| 1 | 7 | WR | Amon-Ra St. Brown | A+ | Yahoo autodraft |
| 2 | 22 | WR | A.J. Brown | B+ | Yahoo autodraft |
| 3 | 35 | TE | Trey McBride | A+ | Yahoo autodraft |
| 4 | 50 | WR/Flex | Terry McLaurin | B- | Yahoo autodraft |
| 5 | 63 | RB | TreVeyon Henderson | B | Manual logic |
| 6 | 78 | RB | Rico Dowdle | C+ | Manual logic |
| 7 | 91 | QB | Dak Prescott | A+ | Manual logic |
| 8 | 106 | RB/Flex | Kyle Monangai | B | Manual logic |
| 9 | 119 | RB/Bench | Khalil Gainwell | B | Manual logic |
| 10 | 134 | WR/Bench | Xavier Worthy | B | Manual logic |
| 11 | 147 | RB/Bench | Ray Davis | B+ | Manual logic |
| 12 | 162 | QB/Bench | Cam Ward | A | Manual logic |
| 13 | 175 | WR/Bench | Ryan Flournoy | A | Manual logic |
| 14 | 190 | K | Jason Myers | A+ | Manual logic |
| 15 | 203 | DEF | Los Angeles Chargers | A+ | Manual logic |

Yahoo's final roster card placed Kyle Monangai in the flex and Terry McLaurin at the third WR position. The positional labels above reflect that final arrangement.

## Yahoo projected standings detail

| Category | Projected points |
|---|---:|
| QB | 520.53 |
| WR | 823.12 |
| RB | 659.70 |
| TE | 188.13 |
| K | 149.02 |
| DEF | 106.75 |
| **Total** | **2,447.25** |

Yahoo projected the team only 0.17 points behind third place, but 30.76 points behind first. The strongest visible construction signal was WR; RB quality was the primary weakness, consistent with Yahoo's C+ grade for Dowdle.

## What happened

Autodraft was enabled at the beginning to ensure the room would complete. That was the wrong operating mode for a draft-logic rehearsal. It made the first four selections and prevented the manual decision process from being exercised. After the issue was identified, autodraft was disabled and the remaining 11 picks were made through a continuous sub-second clock monitor.

The manual logic used this sequence:

1. Repair the empty RB slots with the best available RB values.
2. Take a starting QB before the 14-team pool thinned further.
3. Fill flex and bench with RB/WR value.
4. Add a backup QB in a deep league.
5. Delay kicker and defense until the final two rounds.

## Lessons learned

### Draft execution

- **Continuous monitoring is mandatory.** A 45–50 second polling interval is incompatible with a 30-second pick clock. Use a sub-second watch loop for the full active draft.
- **Speed outranks reporting while on the clock.** Precompute the shortlist, make the pick immediately, and write the explanation afterward.
- **Autodraft stays off for logic tests.** It is an emergency fallback only, and turning it on must be explicit.
- **Verify the state, not just the click.** Confirm the autodraft checkmark is absent and confirm the roster count increments after every selection.
- **Keep the candidate rule executable.** The successful manual rounds used a simple ordered rule—fill an open starter, then take the highest-ranked available player at the required position.

### Strategy

- **The opening was too passive at RB.** Three WRs and a TE before the first RB forced catch-up selections and produced the roster's weakest grade at RB2.
- **The elite receiving core still carried the build.** Yahoo's 823.12 projected WR points helped the team finish fourth despite the RB weakness.
- **Waiting on QB worked in this 14-team room.** Prescott at pick 91 received an A+ and preserved early capital for other positions.
- **K and DEF were correctly delayed.** Both final-round selections received A+ grades.
- **Do not copy the Henderson pick into the real draft.** Henderson is already the private league's seventh-round keeper; live-draft logic must treat that roster slot and pick as pre-committed.
- **This room is not a league simulation.** A 14-team slot-7 result should validate the interface and decision loop, not set the private league's first-overall strategy.

## Next rehearsal

Run an eight-team standard mock from position 1 if Yahoo inventory permits. Seed the roster with the Henderson keeper constraint, keep autodraft off, and log the top three candidates considered at each user pick. The next run should specifically test whether an elite RB-first opening improves projected RB points without giving up too much of the three-WR advantage.
