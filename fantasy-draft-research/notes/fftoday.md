# FFToday draft-time source guide

Live site reviewed on 2026-08-30. This guide tells a draft-time agent what
FFToday exposes, which pages are most useful, how to address them, and where the
data does not match this league.

## Executive use

FFToday is a strong secondary draft source for:

- current 2026 projections by position;
- position rankings, tiers, comments, upside flags, and risk flags;
- cross-position Top 225 cheatsheets for non-PPR, half-PPR, PPR, Superflex,
  and FFPC formats;
- 12-team ADP aggregated from RTSports, ESPN, and Sleeper;
- individual player outlooks, career stats, recent game logs, and depth-chart
  context;
- historical season and weekly stats, including targets for RB, WR, and TE;
- consistency, opponent fantasy points allowed/scored, and strength of
  schedule.

Use it to challenge or enrich the Yahoo player list, not to replace the
league's authoritative settings, keepers, available-player state, or live draft
board.

For this league, start with the **Non-PPR Top 225** and **Non-PPR ADP**. The
league has no reception points. Its yardage bonuses and short-field-goal rules
are custom, however, so FFToday's public presets will not necessarily reproduce
Yahoo's point totals.

## Best draft-time pages

| Need | Page | Data available | Draft-time use |
| --- | --- | --- | --- |
| Current content index and timestamps | [Rankings & Projections](https://www.fftoday.com/rankings/) | Last-updated dates and links for every current preseason and in-season dataset | Check freshness before trusting a cached ranking |
| Cross-position board | [2026 Non-PPR Top 225](https://www.fftoday.com/rankings/26-cheatsheet-non-ppr.html) | Overall rank, positional rank, player, age, team, bye, full-season schedule indicator, Weeks 15-17 schedule indicator, upside/risk markers | Primary FFToday comparison board for this league |
| Cleaner cross-position board | Use the **Print** link on the selected Top 225 page | The same ordered list in simpler markup with fewer page elements | Best page for quick reading or careful table extraction |
| Market cost | [2026 Non-PPR ADP](https://www.fftoday.com/rankings/26-adp-standard.html) | Overall rank, player, position and positional rank, team, source-specific RTSports/ESPN/Sleeper positions, composite ADP | Identify reaches, values, and likely availability; never treat ADP as player quality |
| Position rank and rationale | Example: [QB rankings](https://www.fftoday.com/rankings/playerrank.php?PosID=10&o=1) | Tiers, rank, team, bye, positional ADP, comments, upside and risk markers; format tabs for non-PPR/half-PPR/PPR | Break ties and explain why a player is above or below market |
| Numeric forecast | Example: [QB projections](https://www.fftoday.com/rankings/playerproj.php?PosID=10) | Position-specific projected football stats, bye, fantasy points, change/upside/risk markers, update date | Compare plausible workload and positional output rather than rank alone |
| Player dossier | Example: [Josh Allen](https://www.fftoday.com/stats/players/16228/Josh_Allen?LeagueID=0) | Bio, draft/college/size/age, career season stats, current projection, current outlook, depth-chart link, and recent game logs | Investigate one candidate when the broad tables disagree |
| Historical production | [Stats hub](https://www.fftoday.com/stats/index.htm) | Season, week, recent-window, playoff, player, team, target, fantasy-points-allowed, fantasy-points-scored, and run/pass data | Validate track record, workload, volatility, or offensive tendency |
| Volatility | [Consistency Calculator](https://www.fftoday.com/tools/crank.php) | Fantasy points per game, consistency score, elite/#1/subpar game rates or counts; configurable season/window, league size, starters, and minimum games | Distinguish boom/bust players from steady producers |
| Schedule context | [Fantasy Strength of Schedule](https://www.fftoday.com/stats/fantasystats.php?o=3) | Weekly opponent grid by position; percentage difference from NFL-average fantasy points allowed; positive/negative matchup highlighting | Tiebreaker only; the current Top 225 also includes Weeks 15-17 schedule context |
| Auction reference | [2026 Non-PPR Auction Values](https://www.fftoday.com/rankings/26-av-non-ppr.html) | Overall/position/player/team/max bid for a fixed $200 cap and fixed roster | Reference only: this Yahoo league uses a snake draft |

Static current-year pages include the two-digit season in the filename. Discover
the current link from the rankings index instead of manufacturing next year's
URL.

## What the site exposes

### Preseason rankings and projections

The 2026 rankings hub showed these public datasets on 2026-08-30:

- preseason projections for QB, RB, WR, TE, K, DEF, DL, LB, and DB;
- preseason rankings for those same offensive, team-defense, and IDP positions;
- expanded rankings with three-year history and more than 200 player outlooks;
- Top 225 cheatsheets for Superflex, FFPC, PPR, half-PPR, and non-PPR;
- ADP for PPR, half-PPR, and non-PPR;
- auction values for PPR, half-PPR, and non-PPR;
- dynasty rankings by QB, RB, WR, and TE;
- rookie rankings for 1-QB and Superflex leagues;
- printable views for several ranking products.

On the review date, projections, rankings, and Top 225 pages were marked updated
2026-08-27; ADP was marked 2026-08-24; auction values were marked 2026-08-20.
Read the displayed date on every use because the paths themselves do not prove
freshness.

Position rankings are more than ordered names. They include tiers, bye weeks,
positional ADP, short analyst comments, and selected upside/risk notes. Projection
tables expose the underlying position-specific stat assumptions and calculated
fantasy points.

### ADP

The 2026 ADP pages state that they model **12-team** leagues and aggregate three
sources: RTSports, ESPN, and Sleeper. Each row exposes the three source positions
and a composite ADP.

This league currently has eight active teams. Compare overall pick numbers, not
FFToday's implied draft rounds. A 12-team ADP is a market reference, not a
guarantee that a player lasts to the equivalent round of an eight-team keeper
draft. Keepers also remove players and consume specific picks before the live
draft begins.

### Auction values

The displayed auction model is based on a 12-team league, a $200 salary cap, 18
roster spots, and a stated starting lineup. Those assumptions do not match this
league's snake draft or exact roster. Preserve the values only as a rough signal
of relative scarcity; do not present them as actionable bids here.

### Historical player and team stats

The stats hub provides regular-season and playoff tables for:

- QB, RB, WR, TE, K, and team DEF;
- IDP defensive linemen, linebackers, and defensive backs;
- fantasy points allowed and scored by QB, RB, WR, TE, and K;
- Weeks 1-18, last three weeks, last five weeks, full season, and playoffs;
- Wild Card, Divisional, Conference Championship, and Super Bowl games;
- team run/pass ratios.

Columns vary by position. Examples include passing attempts/yards/TD/INT,
rushing attempts/yards/TD, receiving targets/receptions/yards/TD, games, total
fantasy points, and fantasy points per game. Targets are useful workload evidence
in this non-PPR league even though receptions themselves do not score points.

The Player Index supports last-name search and browsing by position, NFL team,
or draft year. The draft-year index visible on the review date covered 1995
through 2026. Individual player pages expose career season rows, a current-year
projection, editorial outlook, depth-chart context, and recent game logs. The
stats hub advertises more than 2,500 player pages and three years of game logs.

### Secondary tools

- **Consistency Calculator:** seasons back to 2000; last eight weeks, one
  season, last two seasons, or last three seasons; minimum games; league size;
  required starters; percentage or game-count views. The score and elite/#1/
  subpar thresholds change with the selected position and league variables.
- **Fantasy Strength of Schedule:** position, recent-data window or full season,
  and displayed week range. Each cell is the opponent and percentage difference
  from the NFL-average fantasy points allowed; green/red represents at least one
  standard deviation in either direction.
- **Player History:** historical production for the players in a selected weekly
  matchup. This is mainly an in-season lineup tool, not a core draft input.
- **Draft Buddy:** Excel-based draft/auction tracking and customized cheatsheets.
  Microsoft Excel is required. This review did not download or execute it.
- **MFL Power:** integration for MyFantasyLeague-hosted leagues. It is not
  relevant to the current Yahoo league.

## Public access, accounts, and scoring

The tables reviewed above were readable without an FFToday account. Public pages
defaulted to **FFToday Half-PPR** scoring during this review.

The scoring selector also exposed public preset IDs in page markup:

| `LeagueID` | Observed preset |
| ---: | --- |
| `1` | FFToday Standard |
| `193033` | FFToday Half-PPR |
| `107644` | FFToday PPR |
| `26955` | ESPN |
| `17` | Yahoo! |
| `107437` | FFPC |
| `204760` | Underdog |

Other presets were present. Treat these numeric values as observed implementation
details, not a documented API contract; confirm the label after navigation.

A free member registration is available. A logged-in member can create a league
profile and apply custom scoring to stats, projections, rankings, and consistency
pages, and can track players. Registration/login and custom-profile creation were
not performed in this review.

Do not assume the `Yahoo!` preset equals the private league. This league adds
yardage bonuses and changes short field goals. Use a verified custom FFToday
profile if one exists; otherwise use raw projected football stats and calculate
the league score independently from
[`yahoo-league-scoring-and-settings.md`](yahoo-league-scoring-and-settings.md).

## URL and query reference

FFToday exposes server-rendered HTML tables over public GET URLs. No public JSON
API or CSV/export control was found during this review.

### Position IDs

| `PosID` | Position |
| ---: | --- |
| `10` | QB |
| `20` | RB |
| `30` | WR |
| `40` | TE |
| `50` | DL |
| `60` | LB |
| `70` | DB |
| `80` | K |
| `99` | DEF/DST |

### Historical stats

```text
https://www.fftoday.com/stats/playerstats.php?Season=2025&GameWeek=Season&PosID=20&LeagueID=1
```

Observed `GameWeek` values include `1` through `18`, `Last3`, `Last5`, `Season`,
`Playoffs`, and playoff-round values used by the site's links. Regular-season
pages exposed seasons 2001-2025 on the review date. Tables paginate at 50 rows;
later pages add `cur_page`, `order_by`, and `sort_order`.

### Current projections

```text
https://www.fftoday.com/rankings/playerproj.php?PosID=20&LeagueID=1
```

Projection pages also paginate at 50 rows. Preserve `cur_page`, `order_by`, and
`sort_order` when following pagination or a column sort.

### Consistency

```text
https://www.fftoday.com/tools/crank.php?Season=2025&Option=Season&PosID=20&MinGames=4&Teams=8&Starters=2&View=Percent&LeagueID=1
```

Observed `Option` choices include season, last eight weeks, last two seasons,
and last three seasons. `View` is `Percent` or `Games`. League size choices were
8, 10, 12, 14, and 16; required-starter choices varied by position.

### Strength of schedule

```text
https://www.fftoday.com/stats/fantasystats.php?o=3&PosID=20&Data=Season&LeagueID=1
```

Observed `Data` choices were `Last3`, `Last5`, `Last8`, and `Season`. The form
also supports a displayed start/end week range.

### Player pages

```text
https://www.fftoday.com/stats/players/<numeric-player-id>/<name-slug>?LeagueID=1
```

The numeric ID is the most useful stable identifier within FFToday. Do not derive
it from a player's name; follow the link from a current table or Player Index.

## Draft-time agent procedure

### Before the room opens

1. Refresh Yahoo's league settings, active teams, draft order, and keepers using
   the existing Yahoo notes in this folder.
2. Open the FFToday rankings hub. Record the visible update dates for rankings,
   projections, Top 225, and ADP.
3. Load the current Non-PPR Top 225 and its print view as the FFToday baseline.
4. Load Non-PPR ADP. Record that it is 12-team data before comparing it with this
   eight-team room.
5. Load projections for QB, RB, WR, and TE. Add K/DEF only near the point those
   positions become draftable.
6. Remove Yahoo keepers and already-rostered players from every candidate set.
7. If exact fantasy points matter, calculate them from raw projections with the
   Yahoo scoring rules. Do not silently substitute an FFToday preset.

### For each live pick

1. Read the live Yahoo board first and update the unavailable set.
2. Build a small candidate pool from the highest remaining Non-PPR Top 225
   players, constrained by roster needs and positional scarcity.
3. Compare each candidate's FFToday rank with composite and source-specific ADP.
   A rank/ADP gap is evidence of value or disagreement, not an automatic pick.
4. Use position projections to compare workload and scoring paths. Use targets
   as opportunity evidence for RB/WR/TE even though receptions score zero.
5. Open the player page or expanded ranking for finalists. Check risk/upside,
   outlook, team, role, bye, and recent history.
6. Treat schedule and consistency as tiebreakers. Weeks 15-17 align with this
   league's fantasy playoffs, but schedule projections remain uncertain.
7. Recommend no more than three players. State the data dates, scoring mode,
   league-size mismatch, and the reason for the final ordering.

Suggested response shape:

```text
1. Player — FFToday rank / ADP / projected role; roster fit; main risk
2. Player — FFToday rank / ADP / projected role; roster fit; main risk
3. Player — fallback case

Data: FFToday Non-PPR rankings updated <date>; ADP updated <date>, 12-team.
Unavailable set: refreshed from the live Yahoo board at <time>.
```

## Reliability and parsing cautions

- Pages are legacy server-rendered HTML with advertisements and nested table
  markup. CSS selectors and visual column positions are more fragile than the
  data labels. Prefer the print Top 225 when it has the fields needed.
- Ranking, projection, ADP, and auction pages update independently. Always carry
  each page's displayed date into derived advice.
- Some tables have more than 50 rows. Following only the first page silently
  drops late-round players.
- Player names, suffixes, apostrophes, team changes, and defense labels can break
  name-only joins. Preserve the FFToday numeric player URL when available and
  normalize names only as a fallback.
- The site can contain data-entry or rendering anomalies. For example, the 2026
  PPR print cheatsheet displayed an implausible `39.3` age for D.J. Moore during
  this review. Verify material bio, injury, team, and role facts against another
  current source.
- Upside/risk markers and outlooks are editorial judgments. Separate them from
  numeric projections and market ADP in agent reasoning.
- No authenticated pages, custom league profile, Draft Buddy workbook, or MFL
  integration was exercised. Those capabilities remain unverified beyond the
  public site's own descriptions.
- No public, supported API or bulk export was found. If automation is added,
  keep request volume low, cache pages with retrieval timestamps, retain source
  URLs, and revalidate against site terms before unattended collection.

## Evidence reviewed

- [Stats hub](https://www.fftoday.com/stats/index.htm)
- [Rankings & Projections hub](https://www.fftoday.com/rankings/)
- [Tools hub](https://www.fftoday.com/tools/)
- [Player Index](https://www.fftoday.com/stats/players)
- [My FFToday](https://www.fftoday.com/myfftoday/index.php)
- Current public pages linked throughout this guide, retrieved 2026-08-30
