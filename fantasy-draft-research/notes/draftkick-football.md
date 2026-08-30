# DraftKick Football: Draft-Time Agent Guide

- Source: [DraftKick Football](https://app.draftkick.com/football)
- Observed: 2026-08-30, unsigned/free browser session
- Observed build: `v1.22.27`, built 2026-08-28
- Observed projection update: 2026-08-28

## Purpose and evidence boundary

This is an operational inventory for an agent assisting during a fantasy football draft. It describes the public Football 2026 application, the data exposed in the interface, and the state an agent should monitor.

Evidence labels used below:

- **Observed** means the behavior or field was directly visible or exercised in the unsigned app.
- **Site-stated** means DraftKick described the capability in its own onboarding or registration UI, but it was not independently exercised.
- **Inferred** means the interpretation follows from the label and live values, but DraftKick did not expose a definition in the inspected interface.
- **Unverified** means authentication, payment, a browser extension, or a real draft room was required.

Player values, availability percentages, injuries, prices, source choices, app versions, and projection dates are volatile. Re-read the current interface at draft time. Do not treat the example build or data observed on 2026-08-30 as a frozen specification.

## Access and persistence model

### Free, unsigned session

**Observed:** an unsigned user can open the full football workspace, change league settings, inspect players, start a simulated draft, and manually record picks. The header displays `Not saved`.

DraftKick's onboarding states that all settings are unlocked in the free session, but the league and draft progress do not persist after the page is closed. Treat reload, tab closure, browser restart, or lost browser state as potentially destructive until current behavior is re-verified.

### Sign-in

**Observed:** `Sign in` opens an email-only form with `Send verification code`. No password field was present. The agent must never request, store, or relay the verification code; the user should complete authentication directly.

`Sign up` routes to `/football/register`, which presented the paid products rather than a separate credential form.

### Paid capabilities

The registration page described these one-time, season-specific products:

- **Football Basic 2026:** saves league settings and draft progress and permits unlimited football leagues for the season.
- **Football Live 2026:** adds automatic pick synchronization and in-draft-room rankings for Yahoo, ESPN, CBS, and Sleeper.
- **Two Sports:** combines football and basketball with Live capabilities.

The page states that Live draft-room sync requires the DraftKick Live Chrome extension. Prices were visible but are intentionally omitted because they are commercial and volatile. Paid persistence, account recovery, extension behavior, and live-room sync were not exercised.

## Workspace and live draft state

The fixed header is the primary state authority:

- integration state, observed as `No draft detected` without the extension;
- current round;
- current overall pick;
- current team;
- a turn prompt such as `It's your turn to pick!`;
- draft controls (`Simulate draft`, or `End sim` and `Undo pick` while simulating);
- persistence state, observed as `Not saved` in the free session.

The right-side roster panel selects a team and displays that team's configured slots. In the default league, the teams were Adam through Scott, `Joe (6th)` was the selected team, and the roster was 1 QB, 2 WR, 2 RB, 1 TE, 1 Flex, 1 K, 1 DEF, and 6 bench slots. Those are defaults, not project league settings.

## Players view

### Controls and filters

**Observed controls:**

- player-name search;
- NFL team filter (`All Teams`, `FA`, and every NFL abbreviation);
- tag filter (`Any Tag`, then configured tags such as `Target` and `Avoid`);
- `Hide drafted`;
- display modes: `List`, `Positions`, `Cheatsheet`, and `Keepers`;
- positions: All, QB, WR, RB, TE, Flex, K, and DEF;
- row limits: Top 50, Top 200, Top 500, and Show All;
- sortable linked headers in List view;
- a `CSV` export control.

The CSV button was present, but the automated browser did not receive a download event. File contents and download reliability remain unverified.

### List data dictionary

| Field | Meaning and use |
| --- | --- |
| Team | Drafted owner when assigned; blank/`Draft` action while available. |
| Pick | Overall pick number when drafted; before selection the column provides the draft action. |
| Rk | DraftKick value rank. Onboarding describes Rank as the highest projected player. |
| ADP | Selected site's average draft position or rank feed. Use it for market timing, not player quality. |
| `$` | DraftKick auction-dollar value under current league settings. Negative values were possible low in the default player pool. |
| Name | Player or defense name plus NFL team, bye week in parentheses, and position. |
| Info | Status button. A plus indicated no surfaced alert; an ambulance opened injury/outlook details. |
| Year | Experience/season count as exposed by DraftKick. |
| Impact | Roster-fit value. Onboarding says Standings Impact identifies the best fit for the current roster. It changed after simulated picks. |
| `Pk<n>` | **Inferred:** probability the player remains available at the named upcoming pick. The two headers matched later picks for the selected team and values rose toward 100% for lower-ranked players. Confirm semantics if DraftKick publishes a definition. |
| Pts | Projected fantasy points under current scoring and projection blend. |
| VORP | Value over replacement under the current roster and replacement-level settings. |
| Pts/G | Projected points per game. |
| PAYD, PATD, INT | Passing yards, passing touchdowns, and interceptions. |
| RUYD, RUTD | Rushing yards and rushing touchdowns. |
| REC, REYD, RETD | Receptions, receiving yards, and receiving touchdowns. |
| 2PT, FL | Two-point conversions and fumbles lost. |
| FGM, 40-49M, 50+M, XPM | Kicker projection fields. |
| DEFPT | Defense/special-teams projected points. |

All model-derived fields depend on league scoring, roster positions, replacement levels, projection weights, and the chosen ADP source. Values can change after every pick.

### Alternate player views

- **Positions:** a round/tier matrix split into QB, WR, RB, TE, K, and DEF tables. Each compact row exposes Pick, Rk, ADP, player, and Impact. Tier boundaries were visible.
- **Cheatsheet:** compact position tables. Offense/kicker columns were position, Year, Rk, ADP, and `$`; defense omitted Year.
- **Keepers:** the full player pool with Rk, ADP, `$`, Name, Info, Year, Team, and Round selectors. This is the manual keeper-assignment surface.

### Player detail modal

Clicking a status icon opens four tabs:

- **Info:** injury designation and reason when present, plus a sourced outlook. The observed example cited ESPN. DraftKick states that more news is available in the Live extension.
- **Projections:** source-by-source stats and a Composite row. Observed sources were ESPN, CBS, Sleeper, FFToday, Footballguys, and The BLITZ, plus Custom.
- **Notes:** a free-text `Notes...` field.
- **Tags:** configured checkboxes such as Target and Avoid.

Notes and tags are part of browser league state. In a free `Not saved` session, assume they will be lost with the page.

## Recording and simulating picks

### Manual draft

**Observed:** each available List or Positions row contains a `Draft` button for the current team.

**Site-stated:** right-clicking a player adds that player to a chosen team, and `Ctrl+Z` undoes the last pick. These shortcuts were described in onboarding but were not exercised.

After a pick, verify all three surfaces rather than trusting the click alone:

1. header advanced to the expected pick and team;
2. player row shows the drafted team and pick;
3. Board and Rosters show the player in the intended location.

### Simulated draft

**Observed:** `Simulate draft` immediately let AI opponents make picks until the configured `My Team` was on the clock. The header changed from pick 1 to pick 6 in the default ten-team league, displayed `It's your turn to pick!`, and exposed `End sim` and `Undo pick`. Drafted player rows gained team and pick values; Impact values recalculated.

Onboarding says the simulation uses realistic AI opponents and the selected ADP source. That realism claim was not benchmarked. Use simulations for workflow rehearsal and scenario exploration, not as evidence of actual room behavior.

## Rosters, Board, and Standings

### Rosters

The Rosters view is a team-by-slot grid with a `Single row` toggle and CSV export. It is the fastest check for positional completeness and accidental assignment to the wrong team.

### Board

The Board view is a round-by-team matrix. The default ten-team, 15-round Snake setup showed overall pick and round-pick labels (`1.1`, `2.10`, and so on). It is the authoritative visual check for order, traded-pick ownership, and missing or duplicate selections.

### Standings

The Standings view has `Project final rosters`, checked by default. It showed:

- a week-by-week table (weeks 1 through 17 in the default setup) with Points and Pts/Player;
- a position-contribution table for QB, WR1, WR2, RB1, RB2, TE, Flex, K, DEF, and total Points;
- the team-selector/roster sidebar.

Before any picks, projected teams were identical because the app was filling all rosters hypothetically. Treat projected standings as a decision aid, not an outcome forecast.

## Settings inventory

### Basics

- league name;
- draft type: Draft or Auction;
- draft order: Snake, Straight, or Third-Round Reversal;
- regular-season weeks;
- playoff weeks;
- app build and projection-update dates.

Auction drafting was selectable but not exercised. Do not assume the observed snake-draft controls and `$` values fully describe auction behavior.

### Teams

- `My Team` selector;
- Spectator Mode;
- rename and remove teams;
- Add Team.

### Scoring

Presets: Yahoo, ESPN PPR, ESPN Non-PPR, CBS, Sleeper, Fantrax, Underdog, NFFC, and Ottoneu.

Editable categories observed:

- passing attempts, completions, incompletions, yards, touchdowns, first downs, interceptions, and sacks taken;
- rushing attempts, yards, touchdowns, and first downs;
- targets, receptions, receiving yards, touchdowns, and first downs;
- return yards and touchdowns;
- two-point conversions and fumbles lost;
- position-specific receptions and first downs;
- field goals, 40-49 yard field goals, 50+ yard field goals, extra points, and defense points.

Yardage fields expose both points-per-yard and yards-per-point representations.

### Positions

Presets: Yahoo, Sleeper, ESPN, CBS, Fantrax, Underdog, and NFFC.

Editable starters: QB, WR, RB, TE, Flex, WR-TE, Superflex, K, and DEF. Bench is a single BN count. Position-color themes included Sleeper, Pastel NFFC, True NFFC, Fantrax, Yahoo, ESPN, and RealTime.

Replacement levels are separately editable for every position. DraftKick explains that lowering a replacement-level count lowers that position's value and raising it raises the value. This directly affects VORP, rank, and dollar values.

### Projections

Projection-source weights are relative; zero excludes a source. Observed sources and default weights were:

| Source | Observed weight |
| --- | ---: |
| ESPN | 1 |
| CBS | 1 |
| Sleeper | 1 |
| FFToday | 1 |
| Footballguys | 1 |
| The BLITZ | 3 |

Custom projections are a beta CSV upload. DraftKick requires the exact columns below and says a new upload replaces the previous custom set:

```text
Sleeper ID,PAYD,PATD,INT,RUYD,RUTD,REC,REYD,RETD,2PT,FL,FGM,40-49M,50+M,XPM,DEFPT
```

Unused scoring stats may be blank. `Clear custom projections` is destructive to the current custom set; an agent must not use it without explicit user instruction.

### Draft Picks

Every overall pick has Pick, Round pick, Original team, and editable New team. Use this surface to encode traded picks before the draft and verify the Board afterward.

### Site Ranks & ADP

One feed is selected at a time. Observed choices:

- Yahoo ADP and Yahoo Ranks;
- ESPN ADP, Non-PPR Ranks, and PPR Ranks;
- CBS Non-PPR ADP and PPR ADP;
- NFFC ADP;
- Sleeper Non-PPR, Half-PPR, PPR, and 2QB ADP;
- FantasyPros Non-PPR, Half-PPR, and PPR ADP;
- FantasyPros Non-PPR, Half-PPR, and PPR ECR.

The selected feed controls market-timing signals and simulation behavior. Match it to the actual host and scoring format; do not silently accept the default Yahoo ADP.

### Tags

Tags have an editable label and symbol. Target/✅ and Avoid/❌ were the defaults, followed by blank slots. DraftKick explicitly supports emoji, text, or any other character as the symbol.

## Draft-time operating procedure

### Before the room opens

1. Confirm app build and projection-update dates.
2. Confirm persistence state. If it says `Not saved`, keep the page open and maintain a separate pick record.
3. Enter authoritative league basics: teams, `My Team`, draft type/order, regular season, and playoffs.
4. Enter authoritative scoring and roster positions. Never use a platform preset without comparing exceptions.
5. Review replacement levels and projection weights; record intentional overrides.
6. Select an ADP/rank source matched to the host and scoring format.
7. Encode traded picks and keepers; verify Board and Rosters.
8. Add concise notes and Target/Avoid tags only if persistence is safe.
9. If using Live, verify that the extension reports the intended draft and current pick before relying on sync.
10. Run at least one short simulation to rehearse the actual pick position and fallback process.

### For every live pick

1. Read header: round, pick, team, integration state, and save state.
2. Confirm the previous selection on Board and Rosters.
3. Filter to available players (`Hide drafted`) and the positions still needed.
4. Compare four independent signals:
   - Rk/VORP/Pts for modeled value;
   - Impact and projected Standings for roster fit;
   - ADP and `Pk<n>` for timing;
   - Info/Notes/Tags for injury and qualitative risk.
5. Open the candidate's projection tab when the decision depends on source disagreement.
6. Present a short recommendation with at least one fallback. State whether urgency comes from value, fit, or availability risk.
7. Record the selected player only after the user chooses, unless the user has explicitly delegated pick entry.
8. Verify header, player row, Board, and Roster after entry.

### Recommended agent response shape

```text
On the clock: <team>, pick <overall> (<round.pick>)
Roster need: <positions/constraints>
Best value: <player> — Rk <n>, ADP <n>, VORP <n>, Impact <n>
Wait risk: <observed Pk probability or "unknown">
Risk: <injury/outlook/source disagreement>
Recommendation: <player>; fallback <player>
```

Do not dump the whole table during a live clock. Preserve exact numbers for the top two or three choices and explain the decision boundary.

## Failure and recovery playbook

| Symptom | Safe response |
| --- | --- |
| `No draft detected` | Do not assume live sync. Verify supported platform, extension availability, intended browser tab, and room state. Continue manual tracking if the board and current team are known. |
| `Not saved` | Do not close or reload the page. Keep an independent pick log. Sign-in/purchase decisions remain user-owned. |
| Header, Board, and source room disagree | Stop automatic entry. Identify the last common pick, compare team and overall pick, then repair only the smallest known mismatch. |
| Wrong manual pick | Use the visible `Undo pick` control during a sim or the site-stated `Ctrl+Z` shortcut only when the exact last action is known. Verify all views afterward. |
| Projection date is stale | Disclose it. Use injury/news sources separately and avoid presenting the rank as current fact. |
| Tags/notes disappear | Assume unsigned state was lost. Recover from the independent draft sheet; do not invent prior annotations. |
| CSV export fails | Continue from visible tables and the independent pick log. The observed CSV control was not validated end to end. |

## Unverified or unavailable in this research pass

- authenticated persistence and recovery;
- paid checkout and account lifecycle;
- Chrome extension installation and permission model;
- real Yahoo, ESPN, CBS, or Sleeper auto-sync;
- in-room ranking overlay;
- completed live-draft recovery after disconnection;
- auction workflow after choosing Auction;
- CSV file schemas for Players and Rosters;
- exact published definitions for Impact, `Pk<n>`, and all recommendation algorithms;
- update cadence and licensing terms for each projection/ADP source.

An agent must surface these boundaries instead of extrapolating from the public demo.
