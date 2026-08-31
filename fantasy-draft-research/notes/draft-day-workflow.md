# Yahoo Fantasy Football draft-day workflow

Status: living playbook

Owner: Autodraft All Stars manager and the active Codex draft assistant

Last updated: 2026-08-31

Next required review: before the next mock or live draft

This is the canonical operating procedure for preparing, running, and reviewing the Yahoo Fantasy Football draft. The linked research notes hold detailed evidence; this document turns that evidence into one decision loop.

## Operating contract

- Yahoo's live room is authoritative for the clock, available players, completed picks, roster state, and final result.
- The manager owns credentials, verification challenges, destructive room controls, and any decision to delegate real-draft pick entry.
- Unless the manager explicitly delegates real-draft entry before the draft, the assistant recommends and the manager clicks **Draft**.
- Queue maintenance is not pick-entry authorization. In recommend mode, approval applies only to the exact named player or pair; the manager's latest instruction cancels any not-yet-submitted conflicting action.
- During a live clock, selecting safely is more important than explaining. Prepare the shortlist before the turn, act, verify, and log afterward.
- Keep Yahoo **Autodraft** off. The ordered queue is the emergency fallback, so every queued player must be acceptable.
- Never store account credentials, verification codes, cookies, browser storage, private identifiers, or room URLs in this repository.

## Draft-time configuration and pacing

Do not carry team count, pick clock, round count, roster, scoring, or keeper assumptions from an earlier mock or saved snapshot. Before the room opens, record the values visible in Yahoo for this specific draft. Commissioner changes, traded picks, and a different room format can change any of them.

| Field | Verify and record at draft time | Used for |
| --- | --- | --- |
| Draft format and order | Snake, linear, or salary-cap; our slot; direction; traded or missing pick slots | Turn-pair handling and our next-decision sequence. |
| Active teams and round slots | Actual active-team count and the visible number of pick slots in each round | Full-round timing ceiling and total board shape. |
| Pick clock | Configured seconds per pick, including any commissioner override | Observation cadence and the full-clock ceiling. |
| Rounds | Actual round count and any rounds already consumed by keepers | Remaining selections and endgame planning. |
| Roster makeup | Every required starter, flex eligibility, bench, IR, and position limit | Replacement baseline, scarcity, and roster utility. |
| Scoring and bonuses | Visible scoring summary and material bonuses | League-adjusted value and tie-breakers. |
| Keepers | Player, position, cost, and exact board slot | Candidate exclusions and available manual selections. |

For every round, maintain both a capacity estimate and an observed measure:

- **Full-clock ceiling:** `visible round slots × configured pick-clock seconds`. It is the longest a round would take if every slot used its full clock; it is not a forecast.
- **Observed round elapsed:** timestamp from the round's first visible selection through the final selection or Yahoo's advance to the next round. Record the completed selection count and `observed elapsed ÷ completed selections` when both are known.
- **Time to our next decision:** use Yahoo's current drafter, visible next-pick distance, and live countdown. Never substitute a historical average for the active clock.

If a round begins or ends before it can be observed, record the timing as `not observed` rather than inferring a 75-second clock, a fixed team count, or a full round duration. Faster selections shorten the observed round; they do not permit less frequent clock monitoring.

Authoritative details:

- [League scoring and settings](yahoo-league-scoring-and-settings.md)
- [Teams, draft order, and every keeper slot](yahoo-2026-teams-and-draft-order.md)

## Tool and source roles

| Surface | Role | Authority and limits |
| --- | --- | --- |
| Yahoo draft room in the Codex browser | Execute and observe the draft | Primary authority. Use visible labels and state; never inspect or export authentication storage. |
| Yahoo queue, player filters, Board, Picks, and roster | Maintain fallbacks and verify state | Queue only acceptable players. Match exact visible position tags, not substrings in a whole row. |
| Yahoo sign-in, verification, and browser-managed session | Restore access before the room opens | Operational dependency, not a pick signal. The manager completes password, Google sign-in, two-step verification, Account Key, passkey, CAPTCHA, recovery, or risk challenges. Never inspect or export session storage. |
| Yahoo Draft Central, instant/live/salary-cap mocks, waiting room, and draft client controls | Rehearse room mechanics, queue management, recovery, and the decision loop | Preparation only. Public mock settings and opponents do not establish private-league availability or value. Draft Scout and Ultra Draft Kit are optional aids, not authorities. |
| DraftKick unsigned/manual app | League-adjusted value, VORP, Impact, wait-risk, simulation, roster, and board cross-checks | Preparation decision aid. Free unsigned state may disappear; preserve an independent board. |
| DraftKick Live extension, paid persistence, and automatic sync | Potential automatic pick synchronization | Excluded until a controlled mock verifies permissions, pick parity, disconnect recovery, and safe disablement. |
| Boris Chen standard tiers | Consensus-value layer | Tier is not projection, ADP, injury status, or custom-league value. Check freshness and join by normalized name plus position. |
| Boris Chen CSV/Google Sheet and FantasyPros ECR provenance | Delivery and provenance surfaces behind the tier view | One consensus family, not additional votes. Prefer one refreshed artifact and record its timestamp. |
| FFToday rankings, projections, outlooks, tiers, and ADP | Independent non-PPR value, market timing, and risk context | Preparation cross-check. Its Yahoo preset and 12-team ADP do not encode the configured league's bonuses, team count, or keeper state. |
| FFToday stats, consistency, strength of schedule, matchup history, Draft Buddy, and MFL integration | Historical or matchup context and optional external integrations | Preparation only and omit by default. Use a sub-tool only for a named question; authenticated/integrated paths remain unqualified until tested. |
| NBC Sports/Rotoworld live Draft Central, articles, and player news | Current injury/role news and analyst context | Targeted fallback only. Overall ranks are PPR; visible personal-use reading must follow NBC's no-extraction terms. |
| NBC Sports/Rotoworld static Draft Kit PDF | Offline profiles and broad cheat-sheet context | Preparation/offline fallback only. It can lag live pages and includes PPR, dynasty, best-ball, and DraftKings views that do not match this league. |
| Reddit `r/fantasyfootball` | Breaking-signal, counterargument, and primary-source discovery | Read-only fallback. Votes/comments are attention signals; verify material claims at the underlying primary source. |
| Sleeper mock draft and draftboard | Keeper-aware rehearsal, alternate board, and custom ADP context | Preparation only until a signed-in league-matched rehearsal passes readiness. It never establishes Yahoo availability or room state. |
| Sleeper official read-only API | NFL state, player identity, add/drop trends, and public Sleeper context | Optional preparation input. Trends measure activity, not value; respect published limits and avoid private identifiers. |
| Fast subagents | Optional bounded preparation and post-draft logging | Use only for mechanical work such as normalization, comparison, stale-input checks, and result formatting. They never own the browser, clock, unavailable set, or final pick. |

Yahoo help pages, site terms, upstream methodology/code, Google Sheets, MFL, DraftKings, and NBC's other sports are evidence, delivery mechanisms, integrations, formats, or out-of-scope surfaces—not independent player-value tools. Consult them only when validating access, provenance, semantics, or safe use.

### Selective tool activation

Do not open or refresh every qualified tool for every draft. Choose the smallest source set that covers the current phase and unanswered decision risks. More sources add latency and can create false confidence when several surfaces repeat the same upstream data.

The minimum viable set is:

1. **Every live draft:** Yahoo room state, queue, Picks/Board, and roster.
2. **Every final board refresh:** one league-adjusted value source plus one standard-scoring tier or projection source confirmed independent after checking DraftKick's configured upstreams. The current default is DraftKick manual plus cached Boris Chen tiers; FFToday may replace or challenge either when freshness, league fit, or upstream independence is better.
3. **Only when a current availability or role question exists:** use Yahoo-visible player status/news first. NBC or Reddit may discover or contextualize a claim; verify it against linked official NFL/team reporting when available. If the official basis cannot be verified, mark the claim unresolved rather than treating discussion or analyst repetition as confirmation.
4. **Only when a rehearsal or recovery question exists:** one of Yahoo mocks, Sleeper draftboard, or DraftKick simulation. Use more than one only when comparing a named mechanic such as keeper placement, timer behavior, or saved-state recovery.

Select optional tools by trigger:

| Trigger | Add | Do not add automatically |
| --- | --- | --- |
| Baseline board build or major refresh | DraftKick manual plus Boris Chen or FFToday | Reddit, NBC PDF, historical matchup tools, or multiple ADP presentations |
| Tier disagreement or unexpected value gap | The unused independent Boris/FFToday source; inspect timestamps, scoring, and shared upstreams | Another presentation of FantasyPros ECR or a source already included in DraftKick's composite |
| Injury, suspension, depth-chart, or role uncertainty | Yahoo-visible status/news; linked official NFL/team reporting when available; targeted NBC lookup; Reddit only for discovery | Broad article/feed browsing, old PDF profiles, or an unverified claim |
| Identity ambiguity or trend question | Sleeper API plus Yahoo-visible name, team, and position | Treating add/drop counts as rank or Yahoo availability |
| Keeper, order, timer, queue, or recovery rehearsal | One league-matched Yahoo or Sleeper mock; DraftKick simulation for board logic | Carrying mock availability or grade into the real room as fact |
| Need historical durability or matchup context | One relevant FFToday stats/consistency/SOS/matchup surface | Loading the full FFToday tool suite |
| Primary preparation source stale or unavailable | Its declared fallback from the frozen manifest | Activating a newly discovered source after room-open |

For each run, mark every inventoried surface **selected**, **standby**, or **excluded**. `Standby` means qualified but unopened unless its trigger occurs. A tool's existence in the repository never makes it mandatory.

The dated [research-tool readiness matrix](research-tool-readiness-2026-08-30.md) is the qualification baseline. The per-run manifest defined below is the only authority for **selected**, **standby**, and **excluded** state. Do not edit the dated matrix to represent a transient draft selection.

Detailed source procedures:

- [DraftKick agent guide](draftkick-football.md)
- [Boris Chen tier guide](boris-chen-draft-tiers.md)
- [FFToday source guide](fftoday.md)
- [NBC Sports/Rotoworld guide](nbc-sports-fantasy.md)
- [Reddit `r/fantasyfootball` guide](reddit-fantasyfootball.md)
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
Run selection: selected | standby | excluded
Selection trigger or exclusion reason:
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
2. Build the source manifest for every inventoried surface, then choose the minimum selected set for this run. Recheck selected tools and any standby tool whose trigger is plausible; preserve prior qualification evidence for the rest. Demote or exclude a source that fails access, freshness, league-fit, semantics, latency, reliability, or safety checks.
3. Reopen **League → Settings** and record the actual team count, roster makeup, scoring, clock, draft format, and draft time for this run.
4. Reopen **League → Managers** and **Draft Results**. Confirm the assigned slot, active teams, visible round slots, round count, direction, traded picks, and all keepers.
5. Refresh injuries, depth charts, suspensions, and material role news through the selected news path. Add NBC or Reddit only when a targeted question or conflicting claim triggers them. Remove unavailable players and every keeper from candidate data.
6. Refresh standard-scoring tiers and non-PPR projections through the selected board sources. Refresh ADP only when market timing affects a decision. Record source timestamps; flag stale or conflicting data rather than hiding it.
7. If DraftKick is selected, configure it with the actual scoring, starters, bench, order, keepers, and intentional source weights. Verify Board and Rosters. If it says `Not saved`, keep the tab open and maintain the independent state record below. Otherwise build the board from the selected fallback sources.
8. Build an initial value board and position-specific fallbacks. For each candidate, retain the league-adjusted value or replacement baseline, tier, ADP/expected availability, next equivalent, role or availability risk, and source timestamp. Mark players as target, neutral, or avoid; an avoid requires a concrete reason such as injury, role, price, or keeper status.
9. Initialize the opponent ledger from confirmed keepers and draft order. Index it by draft slot or anonymous team label, never a manager identity. Record confirmed picks and starter coverage; calculate open starter/flex paths and the teams that pick before each of our turns. Treat unfilled positions as a probability signal, not proof of an opponent's next pick.
10. Freeze the active source set when the Yahoo room opens. Do not activate a newly merged or newly discovered tool during the live draft without a completed readiness pass.
11. Run a short assigned-slot rehearsal if time permits. A rehearsal result informs mechanics; it does not override current news or live availability.

Fast helpers may check different qualified sources in parallel, normalize names, diff ranks, identify stale inputs, or format the working board in this phase. Each helper reports the source, access result, update timestamp, league mismatch, and evidence used. The primary assistant reviews their output before it affects candidates. Helpers stop when the room opens; they do not own Yahoo monitoring or introduce a new live source.

### 2. Browser and room preflight

1. Open Yahoo Fantasy in the same persistent Codex browser profile. If signed out, follow the [interactive login procedure](yahoo-login.md); the manager enters all secrets and verification challenges directly.
2. Reach the team through the [Yahoo football navigation procedure](yahoo-football-navigation.md).
3. Open the **draft-tool readiness tab pack** in the Codex browser, keep it visible, and verify that each tab reaches its intended surface before starting a mock or entering the real room. This validates access only; it does not promote every tool to active live use.

   | Tab | Readiness check | Live-use rule |
   | --- | --- | --- |
   | Yahoo mock lobby or draft room | The intended league label and the mock/room choice are visible. | Yahoo is the only authority for room state and completed picks. |
   | Yahoo player pool | Player search and visible position labels load. | Use for availability, queue, roster, and player status. |
   | DraftKick | The manual league/board surface loads without relying on unverified sync. | Selected only when configured and current; otherwise standby. |
   | Boris Chen standard tiers | The standard-scoring tier page loads and exposes a visible update time or freshness caveat. | Consensus layer only; do not treat as projections or custom scoring. |
   | FFToday rankings | The non-PPR-relevant rankings/projections entry point loads. | Independent cross-check only; apply league and keeper adjustments. |
   | NBC Sports/Rotoworld Draft Central | The current Draft Central/news entry point loads. | Targeted injury or role fallback only; do not extract or bulk collect content. |
   | Sleeper mock draft | The mock-draft entry point loads without creating a league or connecting an account. | Rehearsal and alternate-board standby only. |
   | Reddit `r/fantasyfootball` | The community landing page loads in read-only mode. | Discovery and counterargument standby only; verify material claims elsewhere. |

   Record each tab in the run manifest as **selected** or **standby**. Do not type credentials, share private data, create leagues, connect extensions, or submit a pick as part of this readiness check. If a selected tab cannot be made ready, mark it degraded, use its declared fallback, and do not troubleshoot it during the live clock.
4. Enter the correct league draft room and confirm league/team labels using visible UI. Do not record the private URL.
5. Confirm and record the draft-time configuration from Yahoo: format and direction, assigned slot, active teams, visible round slots, round count, pick clock, roster makeup, scoring summary, and keepers/traded picks.
6. Initialize the timing ledger with the configured pick clock, the visible slots for round 1, its full-clock ceiling, and the local timestamp at room start. Recalculate the ceiling from Yahoo whenever a round's visible slot count changes; never derive it from a prior draft's team count.
7. Run Yahoo's system test, enable draft sounds, and verify the browser and network are stable.
8. Confirm **Autodraft** is off. Populate and order at least three acceptable queue entries for pick 1.
9. Arrange the room so the clock, current drafter, next pick, last pick, player search, queue, and roster are visible with minimal navigation. Keep Yahoo foregrounded; leave the readiness tab pack accessible but consult only the frozen **selected** sources during the live clock.
10. Agree on control mode:
   - **Recommend mode** is the default: assistant ranks choices; manager submits the pick.
   - **Delegated entry mode** requires an explicit instruction for the real draft: assistant may submit the highest valid candidate and reports immediately afterward.
   - Agree on action vocabulary before the clock: **queue** changes fallbacks only; **approved** or **pick _player_** authorizes the named live selection; a named pair authorizes both only while neither has been superseded by a newer instruction.
11. Start the independent state record, including the anonymous opponent ledger and timing ledger. Keep it free of participant identities and private room data.

### 3. Live draft loop

Run one persistent loop from the first pick until one of these explicit stop
conditions: the manager gives another direction, Yahoo reports **Draft
Complete**, the manager must complete sign-in or a verification challenge, or a
confirmation is required for a non-delegated real-draft action. Do not finish,
idle, or switch to post-draft analysis merely because the current turn belongs
to another manager.

The active assistant owns the clock watch. It must immediately start the next
bounded observation window after every state read, pick, recovery, or browser
control timeout. Reconnect to the same visible Yahoo room if a control session
expires; do not open a second room, infer state from a stale screenshot, or
silently turn on Autodraft. Treat a new manager instruction as the only normal
way to change the live-loop objective.

Use the clock to set the observation cadence without creating a long,
uninterruptible browser call:

| Yahoo state | Next observation window |
| --- | --- |
| More than eight picks and 45 seconds before the next decision | Re-enter after 10–15 seconds. |
| Four to eight picks, or 11–45 seconds before the next decision | Re-enter after 3–5 seconds. |
| 10 seconds or less, or **Your Turn** is visible | Watch continuously in short, fresh checks and act or recommend immediately. |
| Browser control timeout or page-state uncertainty | Reconnect, obtain one fresh Yahoo state, then resume the applicable window. |

The loop must preserve a visible handoff to the active Yahoo room between
control windows so monitoring can resume without rejoining or losing draft
state. Never report the draft as complete while the room is live.

#### Round pacing ledger

At room start and whenever Yahoo advances a round, reconcile the timing ledger from the visible board:

1. Record the round number, its visible pick-slot count, configured clock, full-clock ceiling, and the timestamp of the first visible selection.
2. After every verified selection, update the completed-slot count and elapsed time. When Yahoo advances, record the observed round elapsed, observed seconds per completed selection, and whether the full round was seen.
3. Use the current countdown and next-pick distance to choose the next observation window. The completed-round pace can inform preparation during another manager's turn, but it never overrides the live clock or permits a long unattended wait.
4. If the room pauses, the clock changes, or Yahoo changes the board shape, close the affected observation as partial, record the reason, and start a fresh measure from the new visible state.

The ledger answers how much time each round actually used while retaining a conservative ceiling for planning. It must describe the current room, not a prior 75-second, eight-team, or fixed-roster draft.

#### Between-pick room circulation

A wait window is active draft work, not a pause. During each observation
window while another manager has the clock, make one useful, non-destructive
transition inside the already-open Yahoo room, then return to the clock. The
goal is to keep draft state fresh and advance the next decision—not to
simulate input or evade an inactivity detector. Never add clicks, mouse
movement, keystrokes, reloads, or tab changes solely to appear active.

Run this cycle in order, restarting at **Board/Picks** after a verified
selection:

1. **Board/Picks:** read the last selection, current drafter, next-pick
   distance, and clock; update the unavailable set, opponent ledger, and timing ledger.
2. **Roster/queue:** reconcile our roster and the pre-approved three-player
   queue. Remove drafted names or reorder known acceptable candidates only;
   do not add a new candidate merely because it is visible. Treat a batch
   queue edit as provisional until a fresh Yahoo read confirms the final
   order; if picks complete during the batch, stop and rebuild from the new
   survivor set.
3. **Player pool:** inspect exact visible position tags, availability, and
   cached status for the shortlist and its fallbacks. Do not press **Draft**,
   change the room, launch new research, or activate a slow source.
4. **Return to clock:** return to the active Board/Picks or clock view with
   the next three candidates known. If Yahoo shows **Your Turn** or fewer than
   four picks remain, skip any remaining circulation steps and use the
   live-turn procedure immediately.

Perform at most one circulation step per observation window. If no completed
pick is visible, continue with the next useful step rather than repeatedly
refreshing the same panel. Yahoo's visible state remains authoritative, and
the clock watch always takes priority over the cycle.

#### Observe while opponents pick

1. Monitor Yahoo's clock, next-pick distance, and turn state in bounded control windows. A public mock can advance several picks during a 10–15 second window; once eight or fewer picks remain, re-enter after 3–5 seconds and shorten further in the final three picks. Do not use one long browser call.
2. Read each completed selection from Yahoo, add the player to the unavailable set, and update the current round's completed-slot count and elapsed time.
3. Update the anonymous opponent ledger for that draft slot: drafted position, starter/flex coverage, bench or duplicate depth, unfilled starter paths, and its next pick before our turn. Base this on Yahoo's visible roster and completed picks only; do not record manager names or infer hidden intent.
4. Reconcile the last selection with the Board/Picks view. Do not let a secondary tool overwrite Yahoo state.
5. Recalculate the next-pick shortlist using the decision logic below, including the opponent-pressure check.
6. Keep three acceptable players ordered in the Yahoo queue. Remove drafted players immediately.
7. At a snake turn, prepare both picks as a pair: a preferred combination plus at least two alternate combinations. Order the first pick by the larger expected loss if delayed—tier drop multiplied by no-return risk—not by raw rank alone.
8. Consult only tools marked **Live** in the frozen source manifest. Use cached results first; run quick lookups only when they cannot interrupt the clock watch. Never call a slow/manual source from the live loop.

#### Act when Yahoo shows **Your Turn**

1. Refresh availability once.
2. Confirm the current overall pick, open roster slots, keeper constraints, top three available candidates, and opponent pressure through the next of our picks.
3. Apply the candidate ordering below, including the tier break, wait risk, opponent pressure, and material risk note. Do not start new web research, activate a new tool, or delegate a new task while on the clock.
4. In recommend mode, send one compact line: pick, need, recommendation, fallback, opponent-pressure boundary, and the decision boundary. In delegated entry mode, select immediately.
5. Use the player's visible **Draft** action. If filtering by position, require Yahoo's exact visible position tag.
6. Do not write the pick explanation until Yahoo accepts the selection.
7. Do not rebuild the queue on the clock. Inside ten seconds, the top queue entry is the effective emergency selection, so it must already match the current safe fallback.

#### Verify after every pick

All four signals must agree:

1. Yahoo's last-pick signal names the intended player.
2. The player's exact position satisfies the chosen role.
3. The roster count increments and the player appears in the expected roster area.
4. The room advances to the expected overall pick and team.

If any signal disagrees, stop automatic entry, preserve the clock watch, and run the smallest recovery that can reconcile known state. Update the remaining plan immediately; never continue a stale fixed-position schedule.

A click timeout or client error is an unknown outcome, not proof that the pick failed. Re-read Yahoo and retry only when the last-pick, roster, queue, and turn signals show that no selection occurred.

After either our pick or an opponent pick is verified, refresh the ledger's next-pick relation and the intervening-team set for our next decision. The roster facts do not change on our pick, but the relevant opponents and available-player pressure do.

When Yahoo advances to the next round, finalize the preceding round's observed timing, then initialize the new round from its visible slot count and the current configured clock before resuming normal circulation.

#### Public-mock execution findings

Use public Yahoo mocks as an interface and recovery rehearsal, not as a direct
value simulation. Their team count, roster limit, pick order, scoring, and
public-player behavior can differ materially from the private league.

1. Keep **Autodraft** off for a manual or delegated-entry rehearsal. It consumes
   the queue first and may continue drafting from Yahoo's ranking when the
   queue is empty. Treat the visible Autodraft state as a required preflight and
   post-pick verification signal.
2. Refresh the queue from the current rendered player list and verify its exact
   contents immediately. Player rows reorder rapidly; a coordinate derived from
   an earlier frame can queue a different player. Prefer a stable visible or
   semantic control, then take a fresh Yahoo state before the next action.
3. After filtering by position, verify both the selected filter and the visible
   row position tags before acting. A changed selector can precede the visible
   player-list refresh.
4. Treat **Join next available draft** as a request, not proof of entry. Verify
   the assigned room format, draft slot, and launchable draft client from fresh
   visible state. Never save a room or session URL in this repository.
5. Draft completion and a roster count are not sufficient validation. Audit
   every required starter slot—especially K and DEF—against the displayed final
   roster before recording the rehearsal as complete.

### 4. Decision logic

This is the mandatory live ordering. It operationalizes the broader [advanced draft strategy foundations](draft-strategy-foundations.md); it does not replace Yahoo as the authority for availability, the clock, or completed picks.

Apply hard exclusions first, then rank the remaining choices. Compare positions jointly: never select a player merely because that position is empty when a materially stronger value with acceptable roster utility is available.

#### Hard exclusions

- Already drafted, kept, suspended/unavailable, or not selectable in Yahoo.
- TreVeyon Henderson or any other confirmed keeper.
- A player whose identity or position cannot be matched confidently across sources.
- A player marked avoid for a still-current material reason, unless the manager explicitly overrides it.

#### Opponent ledger and pressure

Track every other roster as an availability model, not as a prediction of human intent. For each anonymous draft slot, retain only the current Yahoo-visible facts:

```text
Draft slot / next pick before ours:
Drafted positions and confirmed keepers:
Filled required starters and flex-eligible starters:
Bench or duplicate position depth:
Open starter/flex paths:
Positions with a plausible remaining need:
Last Yahoo verification:
```

Update this ledger after every completed selection, after our own selection, and immediately before an on-clock recommendation. A manager can draft for value, upside, stack/correlation, bye planning, or a non-obvious preference, so an open roster position is never a deterministic forecast.

Use the ledger to classify pressure on a candidate's position only among teams that pick before our next turn:

- **Low:** ample same-tier alternatives remain, or few intervening teams have a plausible need.
- **Medium:** one or more intervening teams have a plausible need and the current tier has limited depth.
- **High:** several intervening teams can reasonably use the position, few same-tier options remain, and their picks occur before our next turn.

Pressure changes the probability that an equivalent survives; it does not create player value. A confirmed tier/value edge remains above opponent inference. When the ledger is stale, incomplete, or conflicts with Yahoo, reduce pressure confidence and default to the verified tier and actual available set.

#### Candidate ordering

1. **League-adjusted baseline:** compare projected value with the next viable starter or flex replacement for this league's active-team count, lineup, scoring, keeper cost, and drafted-player set. Raw points and an outside site's overall rank are insufficient.
2. **Value tier:** group candidates by the highest remaining league-appropriate tier. A tier is a close-call set, not equal projected points, ADP, injury status, or custom-scoring value. Prefer a clear tier edge; do not manufacture precision among same-tier players.
3. **League fit:** apply non-PPR scoring, three starting WRs, two RBs, flex eligibility, yardage bonuses, bench depth, the existing keeper, and the actual open roster slots. A bonus or positional need may decide a close comparison; it does not erase a material tier gap.
4. **Scarcity, wait risk, and opponent pressure:** compare each candidate with the next acceptable equivalent at that position. Estimate no-return risk from Yahoo ADP, picks until the next turn, remaining tier depth, the anonymous opponent ledger's intervening-position pressure, observed room behavior, and—only if already configured—DraftKick wait-risk. Draft before a tier cliff when the equivalent is unlikely to return; wait when replacement depth is real. Use opponent pressure to distinguish close candidates or refine a survival estimate, never to leapfrog a clear tier/value edge.
5. **Roster utility:** before starter coverage is adequate, favor a player who fills a required role or creates a hard-to-replace flex option. After coverage is adequate, prefer asymmetric upside and contingent value over redundant low-ceiling bench depth. Do not force a named RB/WR/QB/TE strategy after its value condition has disappeared.
6. **Risk and concentration:** record role, availability, projection, correlation, and opportunity risk separately. Current primary news can invalidate an older ranking; source disagreement is a warning to investigate, not a reason to average incompatible fields. Avoid silently stacking several fragile assumptions from the same offense or game script.
7. **Tie-breakers:** projected points, VORP/Impact, bonus upside, bye concentration, and roster correlation resolve only a close, same-tier decision. They do not justify passing a clear value or urgency edge.

#### Turn-pair optimizer

At a snake turn, evaluate feasible pairs instead of two unrelated picks:

1. List the best two-player combination if both candidates survive and two alternate combinations that cover the likely first-pick loss. For every pair, note the intervening teams and positions under medium/high pressure.
2. Take first the candidate whose absence creates the largest combined loss: tier drop plus the likelihood that no acceptable equivalent reaches the second pick. Increase that likelihood only when the opponent ledger shows credible intervening pressure on the position; do not pretend to know which player another manager will select.
3. Re-read Yahoo immediately after the first selection. Before explaining the pick, make the top of the queue safe for the second selection by removing the drafted player and any stale same-position or superseded fallback. Update the opponent ledger and rebuild the second-pick shortlist from the actual available set; never assume the planned pair survived unchanged.
4. If the first choice was an elite value that already covers a starter, let the second choice address the largest remaining tier/roster gap. Do not use a pair to force positional symmetry.

#### Decision evidence requirements

Every live recommendation must be able to name:

- the current tier/value edge and the next acceptable replacement;
- why the player must be selected now or can safely wait;
- the relevant roster/keeper constraint;
- the intervening opponent-pressure evidence and its confidence, or why it was not used;
- one material risk or the fact that none was identified; and
- at least one fallback that remains valid if the player is drafted first.

If those facts are unavailable, say so and choose the highest available verified tier rather than inventing a model output. Yahoo state still controls whether any candidate can be selected.

Use this compact decision record:

```text
Pick <overall> (<round.pick>) — <player>, <position>
Need: <open starters / construction constraint>
Why now: <tier/value edge and wait-risk decision>
Opponent pressure: <low / medium / high; intervening slots and position rationale, or not used>
Risk: <role, availability, projection, correlation, or none identified>
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
- With one bench skill slot plus K and DEF remaining, order the emergency queue **skill → preferred DEF → preferred K**. After one special-teams slot is filled, remove its backups and promote the still-open position before the next clock.

### 5. Failure and takeover

| Failure | Immediate response |
| --- | --- |
| Browser control window ends | Reconnect immediately, rebind to the visible draft page, and reconcile last pick, current pick, queue, and roster before acting. |
| Continuous monitoring is lost | Tell the manager immediately. The manager takes the clock while the assistant reconnects; do not silently enable autodraft. |
| Player selector is ambiguous | Stop that action. Search by player name and exact visible position, then verify the row before drafting. |
| Intended player is taken | Use the highest remaining pre-approved queue candidate; do not improvise a new research process on the clock. |
| Picks complete during a queue rebuild | Stop the stale batch, re-read Yahoo, remove unavailable names, and confirm the survivor order before reporting the queue. |
| Draft action times out or errors | Treat the outcome as unknown. Reconcile last pick, roster, queue, and turn before any retry. |
| First pick of a turn pair completes | Make the second-pick queue safe immediately; remove the drafted player and stale positional fallbacks before analysis. |
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
Current pick-entry authorization: none | exact player | exact pair | delegated
Yahoo state: connected | manager takeover | reconnecting
Between-pick circulation: next surface / last completed room transition:
Round / overall pick / current team:
Draft-time configuration: format / teams / visible round slots / rounds / pick clock:
Roster makeup: starters / flex eligibility / bench / IR / position limits:
Current round timing: start / visible slots / completed slots / full-clock ceiling / elapsed:
Previous completed round timing: observed elapsed / seconds per selection / complete or partial:
Our next open pick:
Last verified selection:
Our roster by position:
Open starters:
Keeper slots already consumed:
Unavailable set last refreshed:
Opponent ledger last refreshed:
Intervening opponent slots before our next pick:
Opponent position pressure / confidence:
Replacement baseline and remaining tier breaks:
Next-pick wait-risk / next acceptable equivalent:
Queue, ordered (3):
Preferred next pair:
Pair fallback combinations:
News or injury flags:
Role, projection, and correlation risk flags:
```

Do not include cookies, authorization values, account data, participant identities, or private league/room URLs.

## Draft completion and learning loop

1. Confirm Yahoo displays **Draft Complete** and the final roster matches the draft-time roster makeup and confirmed keeper slots.
2. Capture our picks, round/overall numbers, Yahoo grades, projected standings, and category totals. For every pick, record only the strongest supported provenance: assistant click confirmed by Yahoo, manager-reported manual click, Yahoo queue/autodraft, keeper, or not directly observed. Never infer manual control from the final roster alone. Exclude other participants' identities and private identifiers.
3. Capture the draft-time configuration and per-round timing: visible slots, configured clock, full-clock ceiling, observed elapsed, observed seconds per selection, and whether the round was fully observed. Mark partial or missing measures explicitly.
4. Record the candidate shortlist and decision reason for each pick where available.
5. Separate strategy outcomes from execution defects. A useful player selected through the wrong position matcher is still an execution defect.
6. Compare the result with prior mocks by total projection, positional contribution, roster completeness, grade, missed alternatives, and actual round pacing. Do not use Yahoo's letter grade as the sole success metric.
7. Add one result file under [`mock-draft-results/`](mock-draft-results/) for a rehearsal, or a clearly labeled real-draft result file after draft day.
8. Update only durable lessons in this playbook. Volatile player rankings, injuries, prices, and room inventory belong in dated research or result notes.

Prior rehearsals:

- [Slot 7 mock: initial autodraft failure and manual recovery](mock-draft-results/2026-08-30-standard-slot-7.md)
- [Slot 2 mock: full manual run, exact-position fix, and RB-first comparison](mock-draft-results/2026-08-30-standard-slot-2.md)

Completed real draft:

- [2026 Yahoo slot-1 real draft: mixed control, queue races, and complete roster](real-draft-results/2026-08-30-yahoo-slot-1.md)

## Open decisions before the next live draft

- Confirm whether the assistant will operate in recommend mode or delegated entry mode.
- Set the final news-source refresh cutoff and the maximum acceptable age for tier/projection data.
- Refresh the [current source readiness matrix](research-tool-readiness-2026-08-30.md) and confirm which qualified tools remain Live, preparation-only, or fallback.
- Decide whether DraftKick will be unsigned/manual, authenticated, or Live-enabled; do not assume unverified sync.
- Build the final assigned-slot strategy using current tiers, observed board shape, and keeper-adjusted availability.
- Define manager-specific avoid/target overrides and whether any player is an automatic selection at pick 1.

## Change log

| Date | Change | Evidence |
| --- | --- | --- |
| 2026-08-31 | Replaced fixed draft assumptions with a Yahoo-observed draft-time configuration and per-round pacing ledger. | AB#3374 |
| 2026-08-30 | Recorded the completed Yahoo real draft and hardened queue races, pick authorization, timeout reconciliation, turn-pair resets, endgame ordering, and selection provenance. | AB#3367 |
| 2026-08-30 | Refreshed the merged inventory and added per-surface roles, minimum viable source sets, trigger-based optional packs, and selected/standby/excluded run states. | AB#3353 |
| 2026-08-30 | Evaluated every merged research surface and linked the current activation/readiness matrix. | AB#3351 |
| 2026-08-30 | Added dynamic discovery, promotion, readiness, manifest, latency, and source-conflict logic for research tools developed in separate tasks and branches. | AB#3349 |
| 2026-08-30 | Added anonymous opponent-roster tracking and pressure-aware wait-risk rules for live and turn-pair decisions. | AB#3361 |
| 2026-08-30 | Added bounded, non-destructive between-pick room circulation so the assistant maintains Yahoo state instead of passively idling. | AB#3365 |
| 2026-08-30 | Initial canonical workflow assembled from Yahoo navigation/settings, draft-order research, two completed mocks, and DraftKick/FFToday/Boris Chen operating guides. | Linked notes above |
