# Yahoo mock draft result — 2026-08-30, slot 2

Completed in Yahoo's signed-in desktop draft room on 2026-08-30. Private room, league, account, participant, and session identifiers are intentionally excluded.

## Setup and outcome

- Format: public standard snake mock, head-to-head
- Teams: 14
- Roster size: 15
- Draft position: 2
- Pick clock: 30 seconds
- Yahoo overall grade: **B+**
- Yahoo projected finish: **4th of 14**
- Yahoo projected total: **2,457.72 points**
- Gap to projected first: **80.68 points**
- Selection mode: manual for all 15 picks; Yahoo autodraft remained off

This was a second interface and decision-speed rehearsal, not an exact simulation of the private league. Yahoo assigned slot 2 after slot 1 was taken during the join race. The target league has eight teams, non-PPR scoring, a first-overall slot, 16 rounds, and a seventh-round TreVeyon Henderson keeper.

## Results

| Round | Overall | Slot | Player | Yahoo grade | Planned role |
|---:|---:|---|---|:---:|---|
| 1 | 2 | RB | Bijan Robinson | A+ | RB-first anchor |
| 2 | 27 | WR | Malik Nabers | B- | WR1 |
| 3 | 30 | WR | DeVonta Smith | B | WR2 |
| 4 | 55 | WR/Flex | Terry McLaurin | B+ | Intended RB2; selector mismatch |
| 5 | 58 | RB | Bhayshul Tuten | B- | RB2 repair |
| 6 | 83 | TE | Harold Fannin Jr. | A+ | Starting TE |
| 7 | 86 | QB | Bo Nix | C- | Starting QB |
| 8 | 111 | RB/Bench | Jordan Mason | C+ | RB depth |
| 9 | 114 | RB/Bench | Kyle Monangai | A | RB depth |
| 10 | 139 | WR/Bench | Khalil Shakir | B | WR depth |
| 11 | 142 | QB/Bench | Sam Darnold | A- | Backup QB |
| 12 | 167 | RB/Bench | Ray Davis | A- | RB depth |
| 13 | 170 | WR/Bench | Jalen McMillan | A | WR depth |
| 14 | 195 | K | Tyler Loop | A+ | Delayed kicker |
| 15 | 198 | DEF | Dallas Cowboys | A+ | Delayed defense |

Yahoo placed McLaurin in the flex position. Every selection was made manually while a sub-second clock monitor was active.

## Candidate log

The active loop retained a three-player shortlist before each manual action. The shortlists below use Yahoo's visible order at the instant of selection.

| Pick | Target | Selected | Next alternatives |
|---:|---|---|---|
| 2 | RB | Bijan Robinson | J. Chase; P. Nacua |
| 27 | WR | Malik Nabers | C. Olave; D. Smith |
| 30 | WR | DeVonta Smith | T. Higgins; T. McBride |
| 55 | RB | Terry McLaurin | C. Skattebo; R. Odunze |
| 58 | RB | Bhayshul Tuten | D. Montgomery; J. Price |
| 83 | TE | Harold Fannin Jr. | K. Pitts Sr.; G. Kittle |
| 86 | QB | Bo Nix | J. Dart; M. Stafford |
| 111 | RB | Jordan Mason | K. Monangai; R. Harvey |
| 114 | RB | Kyle Monangai | R. Harvey; C. Rodriguez Jr. |
| 139 | WR | Khalil Shakir | D. Boston; K. Allen |
| 142 | QB | Sam Darnold | C. Stroud; D. Jones |
| 167 | RB | Ray Davis | B. Allen; K. Black |
| 170 | WR | Jalen McMillan | D. Wicks; P. Bryant |
| 195 | K | Tyler Loop | W. Reichard; C. Santos |
| 198 | DEF | Dallas Cowboys | Chargers; Lions |

Pick 55 exposes an execution bug rather than a defensible RB decision: the first position matcher searched the full row text for the letters `RB`, which also matched unrelated content in McLaurin's row. The selection itself was useful flex value and received a B+, but it did not satisfy the intended RB2 rule. The matcher was immediately changed to require Yahoo's exact visible `RB` position tag; Tuten then repaired RB2 at pick 58.

## Yahoo projected standings detail

| Category | Projected points |
|---|---:|
| QB | 513.66 |
| WR | 763.63 |
| RB | 799.98 |
| TE | 141.05 |
| K | 146.22 |
| DEF | 93.17 |
| **Total** | **2,457.72** |

## Comparison with the first completed mock

| Measure | Slot 7 mock | Slot 2 mock | Change |
|---|---:|---:|---:|
| Yahoo overall grade | A | B+ | Lower |
| Projected rank | 4th | 4th | No change |
| Total points | 2,447.25 | 2,457.72 | **+10.47** |
| QB points | 520.53 | 513.66 | -6.87 |
| WR points | 823.12 | 763.63 | -59.49 |
| RB points | 659.70 | 799.98 | **+140.28** |
| TE points | 188.13 | 141.05 | -47.08 |
| K points | 149.02 | 146.22 | -2.80 |
| DEF points | 106.75 | 93.17 | -13.58 |

The RB-first adjustment materially fixed the first mock's main roster weakness and increased total projection. It did not improve projected rank because the second room's first-place roster projected 2,538.40 points, substantially higher than the first room's leader. Yahoo's letter grade also penalized the lower-value QB and early WR selections even though the team projection improved.

## Lessons learned

### Draft execution

- **Continuous monitoring worked.** All 15 picks were manual and no turn expired.
- **Short monitoring windows are safer than one long control call.** A long-running browser-control call reset once after the first pick. Reconnecting preserved Yahoo state, and bounded 18-second monitor windows maintained sub-second polling without another reset.
- **Match structured position evidence, not arbitrary row text.** An exact visible position tag is required. Full-row substring matching caused the McLaurin/RB mismatch.
- **Verify the result against both the target and Yahoo's last-pick signal.** The target position, selected player position, roster count, and last pick should agree before advancing the plan.
- **Keep the remaining plan adaptive.** After McLaurin filled flex instead of RB2, the next pick was immediately reassigned to RB rather than continuing the stale schedule.
- **Autodraft remained off.** This rehearsal exercised the decision loop on every round.

### Strategy

- **RB-first improved the roster's projection.** RB output increased 140.28 points and total output increased 10.47 points over the first mock.
- **The balance tradeoff was real.** WR and TE projections fell by a combined 106.57 points, showing that RB repair cannot come from blindly forcing the position at every later opportunity.
- **Do not equate Yahoo's letter grade with projected strength.** The B+ roster projected more total points than the prior A roster and finished in the same projected position.
- **Bo Nix was the clearest value miss.** Yahoo graded the pick C-. The next rehearsal should compare waiting one more turn at QB against the cost of the remaining alternatives.
- **Fannin was a strong delayed-TE result.** He received A+, supporting patience at TE even though the category projection was lower than in the first mock.
- **Kicker and defense stayed late.** Both final selections received A+, reproducing the successful part of the first plan.
- **The keeper constraint was respected.** TreVeyon Henderson was excluded from every candidate set because he is already committed to the private league's seventh-round keeper slot.

## Next rehearsal

Prefer an eight-team room from position 1 if Yahoo offers one. Carry forward exact position-tag matching, bounded sub-second monitor windows, Henderson exclusion, RB-first consideration, and the three-candidate log. Test a less rigid middle-round plan: compare the best RB and best non-RB by value before forcing another RB, and delay QB if the available option carries a clear Yahoo value penalty.
