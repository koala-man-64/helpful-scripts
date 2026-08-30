# Yahoo Fantasy Football draft-day workflow

Status: living playbook

Owner: Autodraft All Stars manager and the active Codex draft assistant

Last updated: 2026-08-30

Next required review: before the league draft room opens

This is the canonical operating procedure for preparing, running, and reviewing the Yahoo Fantasy Football draft. The linked research notes hold detailed evidence; this document turns that evidence into one decision loop.

## Operating contract

- Yahoo's live room is authoritative for the clock, available players, completed picks, roster state, and final result.
- The manager owns credentials, verification challenges, destructive room controls, and any decision to delegate real-draft pick entry.
- Unless the manager explicitly delegates real-draft entry before the draft, the assistant recommends and the manager clicks **Draft**.
- During a live clock, selecting safely is more important than explaining. Prepare the shortlist before the turn, act, verify, and log afterward.
- Keep Yahoo **Autodraft** off. The ordered queue is the emergency fallback, so every queued player must be acceptable.
- Never store account credentials, verification codes, cookies, browser storage, private identifiers, or room URLs in this repository.

## Current league constraints

Refresh the linked snapshots on draft day rather than assuming these values are unchanged.

| Constraint | Current value | Draft consequence |
| --- | --- | --- |
| Teams | 8 active | Replacement value is deeper than in the 14-team mocks. |
| Draft | 16-round snake; position 1 | Picks occur in pairs at the turn after the opening selection. |
| Pick clock | 75 seconds | The shortlist must be ready before the turn. |
| Scoring | Head-to-head, non-PPR, fractional | Do not pay a PPR premium for receptions alone. |
| Starters | QB, 3 WR, 2 RB, TE, WR/RB/TE flex, K, DEF | Three starting WR slots preserve WR demand even in non-PPR. |
| Bench | 6 | Favor upside and replaceability over redundant low-ceiling depth. |
| Keeper | TreVeyon Henderson, RB, round 7 / overall 49 | Remove him from every candidate pool; round 7 requires no live selection. |
| Bonuses | 350 passing yards; 150 rushing or receiving yards | Use as a close tie-breaker, not a reason to override a large value gap. |

The team's expected slots are 1, 16, 17, 32, 33, 48, keeper at 49, 64, 65, 80, 81, 96, 97, 112, 113, and 128. That produces 15 manual selections plus the keeper. Confirm this against Yahoo's board because commissioner changes or traded picks supersede this sequence.

Authoritative details:

- [League scoring and settings](yahoo-league-scoring-and-settings.md)
- [Teams, draft order, and every keeper slot](yahoo-2026-teams-and-draft-order.md)

## Tool and source roles

| Surface | Role | Authority and limits |
| --- | --- | --- |
| Yahoo draft room in the Codex browser | Execute and observe the draft | Primary authority. Use visible labels and state; never inspect or export authentication storage. |
| Yahoo queue, player filters, Board, Picks, and roster | Maintain fallbacks and verify state | Queue only acceptable players. Match exact visible position tags, not substrings in a whole row. |
| DraftKick | League-adjusted value, VORP, Impact, wait-risk, roster and board cross-checks | Secondary decision aid. Free unsigned state may disappear; Live sync and paid persistence remain unverified. |
| Boris Chen standard tiers | Consensus-value layer | Tier is not projection, ADP, injury status, or custom-league value. Check freshness and join by normalized name plus position. |
| FFToday | Independent non-PPR ranks, projections, ADP, and risk context | Cross-check only. Its Yahoo preset does not encode this league's custom bonuses and roster. |
| Sleeper | Keeper-aware mock rehearsal and secondary ADP/trend context | Preparation only until a signed-in league-matched rehearsal passes readiness. It does not establish Yahoo availability or room state; avoid double-counting Sleeper data exposed through FFToday or another tool. |
| Fast subagents | Bounded preparation and post-draft logging tasks | Use before the room opens or after the draft for mechanical work such as normalization, comparison, and result formatting. They never own the browser, clock, unavailable set, or final pick. |

Detailed source procedures:

- [DraftKick agent guide](draftkick-football.md)
- [Boris Chen tier guide](boris-chen-draft-tiers.md)
- [FFToday source guide](fftoday.md)
- [Sleeper tool guide](sleeper.md)
- [Yahoo room mechanics and mock workflow](yahoo-mock-drafts.md)
- [Current research-tool readiness matrix](research-tool-readiness-2026-08-30.md)

### Research-tool promotion and readiness

Site research is developed in separate Codex tasks and Git branches. Treat those tasks as a discovery feed, not as draft-day dependencies. A tool moves through four states:

1. **Discovered:** an active task, branch, or PR identifies a potentially useful site. Its behavior and files may still change.
2. **Merged:** its documentation or tooling is present on `origin/main`. An unmerged branch is never part of the draft workflow, even if its task reports success.
3. **Qualified:** the primary draft assistant has reviewed the merged guide, exercised the intended read path when practical, and completed the readiness checks below.
4. **Active:** it appears in the current draft's source manifest with a defined role, freshness result, latency class, and fallback.

Do not activate a tool merely because it exists. A new site must answer these questions:

| Readiness check | Required evidence |
| --- | --- |
| Purpose | The decision signal it contributes: news, projections, rankings, tiers, ADP, availability probability, roster fit, or another named role. |
| Access | Public/authenticated status, user-owned login steps, subscription limits, and a successful draft-day read test. |
| Freshness | Visible publication/update time when available, time checked, acceptable age for its role, and behavior when stale. |
| League fit | Scoring format, roster assumptions, team count, keeper awareness, and every known mismatch with this Yahoo league. |
| Identity join | Player name, NFL team, and position normalization; ambiguous or conflicting identities are excluded. |
| Semantics | Definitions and units for the fields used. Inferred or undocumented fields are labeled and never treated as facts. |
| Coverage | Positions and players represented, missing-data behavior, and whether keepers or drafted players can be removed. |
| Latency | Measured or observed response path classified as cached, quick, or slow/manual. |
| Reliability | Expected failure modes, retry or fallback behavior, and whether reload or sign-out destroys working state. |
| Safety | No credentials, cookies, private URLs, participant identities, or restricted bulk data stored in the repository or draft log. |
| Cross-check | At least one representative player or signal compared with Yahoo and an independent source; material disagreements are recorded. |

Classify a qualified tool for live use:

- **Live:** cached or quick, already open/configured, and safe to consult between turns.
- **Preparation only:** useful for building the board but too slow, fragile, stateful, or distracting for the active room.
- **Fallback:** redundant source used only when the primary source is unavailable or stale.
- **Excluded:** unmerged, inaccessible, stale beyond its threshold, semantically unclear, unsafe, or incompatible with the league.

Before every mock or real draft, build this source manifest from the current merged repository rather than copying the prior run:

```text
Source / guide:
Merged commit:
Role:
Readiness: qualified | degraded | excluded
Usage: live | preparation only | fallback | excluded
Checked at / source updated at:
League or scoring adjustments:
Latency class: cached | quick | slow/manual
Fallback:
Open caveat:
```

The manifest is a snapshot, not a permanent allowlist. Re-check active Codex research tasks, visible Git branches/PRs, the repository's fantasy-research index, `notes/`, and `tools/` to discover additions. Only `origin/main` establishes availability; task summaries and branch contents only identify work that may become eligible after merge.

When a new site guide merges:

1. Confirm it is linked from the fantasy-research index and states its purpose, access model, fields, freshness, league fit, operating procedure, and failure boundaries.
2. Run its documented smoke/read check without exposing authentication material.
3. Normalize its player identities and units before comparing values.
4. Assign one primary role. Additional signals are supporting evidence, not permission to double-count the same underlying rank or projection.
5. Measure whether it can be consulted without interrupting Yahoo monitoring.
6. Add it to the next source manifest with an explicit usage class and fallback.
7. Promote durable mechanics into this playbook; keep volatile player values and site-specific examples in dated notes.

If sources disagree, first compare timestamps, scoring format, units, injury assumptions, and whether they share an upstream feed. Yahoo remains authoritative for room state and availability. For player value, preserve the disagreement and choose using the candidate-ordering rules; do not average incompatible fields or count several presentations of the same source as independent confirmation.

## Draft-day phases

### 1. Final refresh

Complete this before entering the room:

1. Fetch the latest `origin/main`. Inventory merged site guides/tools plus active research tasks, branches, and PRs. Note pending work, but read draft inputs only from the merged tree.
2. Build the source manifest. Exercise and qualify every intended source; demote or exclude any source that fails access, freshness, league-fit, semantics, latency, reliability, or safety checks.
3. Reopen **League → Settings** and compare team count, roster, scoring, clock, and draft time with the settings snapshot.
4. Reopen **League → Managers** and **Draft Results**. Confirm eight teams, position 1, 16 rounds, snake direction, traded picks, and all keepers.
5. Refresh injuries, depth charts, suspensions, and material role news using qualified sources. Remove unavailable players and every keeper from candidate data.
6. Refresh standard-scoring tiers, non-PPR projections, and market ADP. Record source timestamps; flag stale or conflicting data rather than hiding it.
7. Configure DraftKick with the actual scoring, starters, bench, order, keepers, and intentional source weights. Verify Board and Rosters. If it says `Not saved`, keep the tab open and maintain the independent state record below.
8. Build an initial value board and position-specific fallbacks. Mark players as target, neutral, or avoid; an avoid requires a concrete reason such as injury, role, price, or keeper status.
9. Freeze the active source set when the Yahoo room opens. Do not activate a newly merged or newly discovered tool during the live draft without a completed readiness pass.
10. Run a short position-1 rehearsal if time permits. A rehearsal result informs mechanics; it does not override current news or live availability.

Fast helpers may check different qualified sources in parallel, normalize names, diff ranks, identify stale inputs, or format the working board in this phase. Each helper reports the source, access result, update timestamp, league mismatch, and evidence used. The primary assistant reviews their output before it affects candidates. Helpers stop when the room opens; they do not own Yahoo monitoring or introduce a new live source.

### 2. Browser and room preflight

1. Open Yahoo Fantasy in the same persistent Codex browser profile. If signed out, follow the [interactive login procedure](yahoo-login.md); the manager enters all secrets and verification challenges directly.
2. Reach the team through the [Yahoo football navigation procedure](yahoo-football-navigation.md).
3. Enter the correct league draft room and confirm league/team labels using visible UI. Do not record the private URL.
4. Confirm the clock, position, team count, round count, roster, scoring summary, and ranking source shown by Yahoo.
5. Run Yahoo's system test, enable draft sounds, and verify the browser and network are stable.
6. Confirm **Autodraft** is off. Populate and order at least three acceptable queue entries for pick 1.
7. Arrange the room so the clock, current drafter, next pick, last pick, player search, queue, and roster are visible with minimal navigation.
8. Agree on control mode:
   - **Recommend mode** is the default: assistant ranks choices; manager submits the pick.
   - **Delegated entry mode** requires an explicit instruction for the real draft: assistant may submit the highest valid candidate and reports immediately afterward.
9. Start the independent state record. Keep it free of participant identities and private room data.

### 3. Live draft loop

Run one loop continuously from the first pick through **Draft Complete**.

#### Observe while opponents pick

1. Monitor Yahoo's clock and turn state at sub-second intervals inside bounded control windows of roughly 15–20 seconds. Re-enter a new window before control expires; do not use one long browser call.
2. Read each completed selection from Yahoo and add the player to the unavailable set.
3. Reconcile the last selection with the Board/Picks view. Do not let a secondary tool overwrite Yahoo state.
4. Recalculate the next-pick shortlist using the decision logic below.
5. Keep three acceptable players ordered in the Yahoo queue. Remove drafted players immediately.
6. At a snake turn, prepare both picks as a pair: a preferred combination plus at least two alternate combinations.
7. Consult only tools marked **Live** in the frozen source manifest. Use cached results first; run quick lookups only when they cannot interrupt the clock watch. Never call a slow/manual source from the live loop.

#### Act when Yahoo shows **Your Turn**

1. Refresh availability once.
2. Confirm the current overall pick, open roster slots, keeper constraints, and top three available candidates.
3. Choose the highest valid candidate. Do not start new web research, activate a new tool, or delegate a new task while on the clock.
4. In recommend mode, send one compact line: pick, need, recommendation, fallback, and the decision boundary. In delegated entry mode, select immediately.
5. Use the player's visible **Draft** action. If filtering by position, require Yahoo's exact visible position tag.
6. Do not write the pick explanation until Yahoo accepts the selection.

#### Verify after every pick

All four signals must agree:

1. Yahoo's last-pick signal names the intended player.
2. The player's exact position satisfies the chosen role.
3. The roster count increments and the player appears in the expected roster area.
4. The room advances to the expected overall pick and team.

If any signal disagrees, stop automatic entry, preserve the clock watch, and run the smallest recovery that can reconcile known state. Update the remaining plan immediately; never continue a stale fixed-position schedule.

### 4. Decision logic

Apply hard exclusions first, then rank the remaining choices.

#### Hard exclusions

- Already drafted, kept, suspended/unavailable, or not selectable in Yahoo.
- TreVeyon Henderson or any other confirmed keeper.
- A player whose identity or position cannot be matched confidently across sources.
- A player marked avoid for a still-current material reason, unless the manager explicitly overrides it.

#### Candidate ordering

1. **Value tier:** prefer the highest remaining standard-scoring consensus tier and league-adjusted value; do not treat one source as truth.
2. **League fit:** adjust for non-PPR scoring, three starting WRs, two RBs, flex, bonuses, and the existing Henderson keeper.
3. **Scarcity and wait risk:** estimate the chance the player or an equivalent survives to the next team pick using Yahoo ADP, room behavior, DraftKick wait-risk, and remaining tier depth.
4. **Roster utility:** prefer players who fill a starter or add meaningful upside. Avoid forcing positional balance when a materially better value is available.
5. **Risk:** account for injury, role ambiguity, source disagreement, floor/ceiling, and bye concentration.
6. **Tie-breakers:** projected points, VORP/Impact, bonus upside, and roster correlation may resolve a close call; they do not erase a clear tier gap.

Use this compact decision record:

```text
Pick <overall> (<round.pick>) — <player>, <position>
Need: <open starters / construction constraint>
Why now: <value, fit, scarcity, or wait risk>
Fallbacks: <player>; <player>
State verified: <last pick, roster count, next pick>
```

#### Position guardrails

- At pick 1, prefer an elite anchor; the second mock supports RB-first, but current tiers and news decide the player.
- At the 16/17 and later turn pairs, optimize the pair rather than treating either pick independently.
- Do not blindly force RB after an RB-first opening. The second mock gained 140.28 projected RB points but gave back 106.57 combined at WR and TE.
- Preserve enough early/middle capital for three starting WRs; non-PPR does not eliminate their lineup scarcity.
- Waiting at TE can work when a strong tier remains. Waiting at QB is acceptable only when the next tier is deep enough; Bo Nix at pick 86 was the clearest value miss in the second mock.
- Prefer upside RB/WR bench depth. Draft a backup QB or TE only when value, fragility, or scarcity justifies the roster cost.
- Plan K and DEF for the final two open selections unless an exceptional room-specific reason changes the order.

### 5. Failure and takeover

| Failure | Immediate response |
| --- | --- |
| Browser control window ends | Reconnect immediately, rebind to the visible draft page, and reconcile last pick, current pick, queue, and roster before acting. |
| Continuous monitoring is lost | Tell the manager immediately. The manager takes the clock while the assistant reconnects; do not silently enable autodraft. |
| Player selector is ambiguous | Stop that action. Search by player name and exact visible position, then verify the row before drafting. |
| Intended player is taken | Use the highest remaining pre-approved queue candidate; do not improvise a new research process on the clock. |
| Wrong player is selected | Report it immediately and adapt roster logic. Use Yahoo commissioner undo/pause only if the manager/commissioner explicitly chooses it and the league permits it. |
| Yahoo and DraftKick disagree | Yahoo wins for draft state. Pause DraftKick entry/sync and reconcile from the last common pick. |
| DraftKick shows `No draft detected` | Continue manual Yahoo tracking; do not assume extension sync. |
| DraftKick shows `Not saved` or reloads | Recover from the independent state record; do not invent missing annotations. |
| Authentication expires | Manager completes sign-in or verification. Never extract or transfer cookies. |
| New user instruction arrives | If it changes the live pick, apply it immediately when legal. If it is analysis or reporting, defer it until after the selection. |

Do not use **Reset Draft** in any real room. **Pause Draft**, **Undo Draft Picks**, and pick-clock changes are commissioner-level controls and require an explicit manager decision.

## Independent live state record

Maintain this minimal state in working memory or a non-sensitive scratch note. Update it only after Yahoo verification.

```text
Source readiness last checked:
Frozen live-source manifest:
Control mode: recommend | delegated entry
Yahoo state: connected | manager takeover | reconnecting
Round / overall pick / current team:
Our next open pick:
Last verified selection:
Our roster by position:
Open starters:
Keeper slots already consumed:
Unavailable set last refreshed:
Queue, ordered (3):
Preferred next pair:
News or injury flags:
```

Do not include cookies, authorization values, account data, participant identities, or private league/room URLs.

## Draft completion and learning loop

1. Confirm Yahoo displays **Draft Complete** and the final roster has the expected 16 players including the keeper.
2. Capture our picks, round/overall numbers, Yahoo grades, projected standings, and category totals. Exclude other participants' identities and private identifiers.
3. Record the candidate shortlist and decision reason for each pick where available.
4. Separate strategy outcomes from execution defects. A useful player selected through the wrong position matcher is still an execution defect.
5. Compare the result with prior mocks by total projection, positional contribution, roster completeness, grade, and missed alternatives. Do not use Yahoo's letter grade as the sole success metric.
6. Add one result file under [`mock-draft-results/`](mock-draft-results/) for a rehearsal, or a clearly labeled real-draft result file after draft day.
7. Update only durable lessons in this playbook. Volatile player rankings, injuries, prices, and room inventory belong in dated research or result notes.

Prior rehearsals:

- [Slot 7 mock: initial autodraft failure and manual recovery](mock-draft-results/2026-08-30-standard-slot-7.md)
- [Slot 2 mock: full manual run, exact-position fix, and RB-first comparison](mock-draft-results/2026-08-30-standard-slot-2.md)

## Open decisions before the real draft

- Confirm whether the assistant will operate in recommend mode or delegated entry mode.
- Set the final news-source refresh cutoff and the maximum acceptable age for tier/projection data.
- Refresh the [current source readiness matrix](research-tool-readiness-2026-08-30.md) and confirm which qualified tools remain Live, preparation-only, or fallback.
- Decide whether DraftKick will be unsigned/manual, authenticated, or Live-enabled; do not assume unverified sync.
- Build the final position-1 pair strategy for picks 16/17 using current tiers and keeper-adjusted availability.
- Define manager-specific avoid/target overrides and whether any player is an automatic selection at pick 1.

## Change log

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-08-30 | Evaluated every merged research surface and linked the current activation/readiness matrix. | AB#3351 |
| 2026-08-30 | Added dynamic discovery, promotion, readiness, manifest, latency, and source-conflict logic for research tools developed in separate tasks and branches. | AB#3349 |
| 2026-08-30 | Initial canonical workflow assembled from Yahoo navigation/settings, draft-order research, two completed mocks, and DraftKick/FFToday/Boris Chen operating guides. | Linked notes above |
