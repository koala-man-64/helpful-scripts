# NBC Sports Fantasy and Rotoworld draft-time source guide

Research date: 2026-08-30. Primary target: the 2026 fantasy-football draft in
this workspace's Yahoo league. All observations below came from public pages
viewed without an NBC account.

## Executive use

NBC Sports Fantasy is the public home of Rotoworld content for football,
baseball, and basketball. For this draft, its strongest uses are:

- a live PPR Top 200 and current position rankings;
- position tiers for QB, RB, WR, TE, DST, and kicker;
- a free downloadable 98-page draft kit with 260 player profiles and several
  compact cheat sheets;
- rapid player news with injury, transaction, recap, and headline filters;
- ADP-mover, sleeper, bust, default-rank, mock-draft, schedule, and team-preview
  analysis;
- player-specific news histories and links to stats/game-log surfaces;
- short videos and the Rotoworld Football Show podcast.

Use Rotoworld as a current news and analyst-opinion layer. Do not use it as the
source of truth for Yahoo scoring, keepers, available players, roster state, or
the live draft board.

This league is non-PPR. Rotoworld's live overall board and the overall board in
the PDF are explicitly PPR. Position order, player profiles, injury news, and
football-stat evidence can still be useful, but the overall order is not a
scoring match.

## Site map and navigation

The [Fantasy home](https://www.nbcsports.com/fantasy) mixes current football,
baseball, and basketball content. Its local navigation exposes **Sport**,
**Player News**, **Videos**, and **Columns**. The page also contains a league
selector for Rotoworld player-news headlines with MLB, NBA, and NFL choices.

The [Fantasy Football home](https://www.nbcsports.com/fantasy/football) exposes
these draft-relevant destinations:

| Navigation item | What it opens | Draft-time value |
| --- | --- | --- |
| Player News | NFL Rotoworld news feed | Latest injuries, transactions, role changes, and analyst context |
| Draft Kit | Current kit landing page and PDF download | Player dossiers and compact offline reference sheets |
| RotoPat | Patrick Daugherty's preseason positional ranks | Independent position-order comparison |
| Top 200 Ranks | Live PPR overall board | Current cross-position analyst order |
| Videos | Fantasy-football clips | Short explanations of news and player outlooks |
| Columns | Article index | Strategy, rankings, previews, and news analysis |
| Teams | Links for all 32 NFL teams | Team overview and supporting data routes |
| Depth Charts | Links for all 32 team depth-chart routes | Role investigation, subject to the availability warning below |

The football home also surfaces the Rotoworld Football Show, player-news
carousels, position-tier articles, Draft Central, and paginated **Load More**
content. Sponsored cards and sportsbook odds are mixed into the wider site;
exclude those from fantasy evidence.

## Best draft-time pages

| Need | Page | Observed data | How to use it |
| --- | --- | --- | --- |
| Current source index | [Draft Central 2026](https://www.nbcsports.com/fantasy/football/news/rotoworld-fantasy-football-draft-central-2026-rankings-strategy-sleepers-and-more) | PPR Top 200, positional ranks/tiers, dynasty, best ball, ADP movers, sleepers, busts, mock results, team previews, schedule and strategy articles | Start here rather than guessing current-year URLs |
| Live cross-position board | [2026 Top 200](https://www.nbcsports.com/fantasy/football/news/2026-fantasy-football-top-200-overall-rankings) | Rank, name, team, position, and position rank | Compare available candidates, while carrying the PPR mismatch |
| Compact and offline reference | [2026 Draft Kit](https://www.nbcsports.com/fantasy/football/rotoworld-2026-fantasy-football-draft-kit-rankings-cheat-sheets-player-profiles) | Free 98-page PDF; 260 profiles; position, PPR, dynasty, best-ball, DST, and kicker sheets | Download shortly before the draft as a fallback, then record its retrieval time |
| Last-minute status | [NFL Player News](https://www.nbcsports.com/fantasy/football/player-news) | Player, team, position, number, headline, analysis, author, news type, relative time, source link, stats and news-history links | Confirm injury, transaction, and role news for finalists |
| Position groups | QB, RB, WR, TE, DST, and kicker tier links on Draft Central | Ordered tiers plus analyst rationale | Break ties and understand replacement depth; do not infer projected points |
| Market movement | ADP Movers links on Draft Central | Players moving up/down, cited draft range, news catalyst, analyst interpretation | Decide urgency; ADP is timing evidence, not player quality |
| Team context | [Team previews hub](https://www.nbcsports.com/fantasy/football/news/2026-rotoworld-fantasy-football-team-previews-hub) | Links to all-team preseason analysis | Investigate scheme, competition, and projected roles |
| Player history | Example [George Kittle overview](https://www.nbcsports.com/nfl/george-kittle/5453) | Overview, Player News, Stats, and Game Log routes plus current related coverage | Review a finalist's news trail and supporting context |

## Draft Central inventory

The hub was published 2026-08-28 and tells readers to check back as coverage is
added. On the review date it grouped material into:

- **Rankings:** Top 200 Overall Rankings (PPR), RotoPat positional rankings,
  DraftKings Best Ball rankings, and dynasty rankings;
- **Positional previews:** QB, RB, WR, TE, DST, and kicker tiers/strategy;
- **Strategy:** ADP movers, exploiting default rankings, first-round ceiling and
  floor, early DST schedules, preseason takeaways, sleepers, busts, staff
  outliers, mock-draft results, breakouts, and late-round targets;
- **Team context:** a 32-team preview hub and player/team-specific articles;
- **Media:** short videos and the Rotoworld Football Show.

This is editorial content rather than one normalized dataset. Preserve the
article title, author, publication date, URL, and retrieval time with any claim.

## Live Top 200

The live table was published 2026-08-26 at 2:04 p.m. and said it would be
updated throughout the preseason for notable news and injuries. The page
exposed one HTML table with:

```text
Rank | Name | Team | Position | Position Rank
```

The page did not display a separate last-modified timestamp during this review.
The Draft Central label is the clearest evidence that this table is PPR.

Do not substitute the PDF for this page when freshness matters. During this
review, the live table and PDF already disagreed materially on some ranks. For
example, Ashton Jeanty appeared at 29 on the live table but 15 on the PDF's PPR
Top 200. That is direct evidence that the downloadable kit can lag the mutable
web table.

## Downloadable draft kit

The landing page explicitly offers a free PDF download. The reviewed file had
98 pages. Its contents page and representative ranking pages were rendered and
visually checked, not inferred only from extracted text.

### Player profiles

The PDF says it contains profiles for 260 players. The offense sections are QB,
RB, WR, and TE; DST and kicker have their own sections. A typical offensive
profile exposes:

- position rank, player, team, age, height, weight, and bye week;
- position-specific 2025 football stats;
- narrative sections labeled **2025**, **What's changed**, and **Outlook**.

Observed position-specific stat fields include:

| Position | Fields shown in the profile tables |
| --- | --- |
| QB | games, completions, attempts, completion rate, passing yards, passing TD, interceptions, rushing yards, rushing TD |
| RB | games, carries, rushing yards, average, yards/game, rushing TD, receptions, targets, receiving yards, receiving TD, total TD |
| WR/TE | games, receptions, targets, receiving yards, average, yards/game, receiving TD, total TD |
| DST | points/game allowed, yards/game allowed, sacks, interceptions, fumble recoveries, defensive TD |
| K | games, PAT, all field goals, field goals by distance bands, fantasy points |

The prose is analyst judgment. The stat lines are historical inputs. Neither is
a custom projection for this Yahoo league.

### Cheat sheets

The final PDF pages contain:

- PPR position sheets for QB, RB, WR, and TE with rank, name, position, team,
  and bye;
- dynasty Top 250 with rank, name, position, team, and bye;
- DraftKings Best Ball Top 250 with rank, name, position, team, bye, and ADP;
- overall PPR Top 200 with rank, name, position, team, and bye.

The PDF also ranks DST and kickers in their profile sections. It does not expose
a non-PPR overall sheet, editable filters, a CSV, or a documented data API.

## Player news and player/team pages

The public player-news feed offers:

- player search;
- **My Favorites** filtering and favorite controls;
- news type: All News, Headline, Injury, Recap, or Transaction;
- a large position selector including QB, RB, WR, TE, K, Defense, offensive
  line, defensive positions, staff, and front-office roles;
- **Load More** pagination.

A feed record can include player name, team/free-agent status, position, jersey
number, a stats link, headline, Rotoworld analysis, author, news type, relative
age, an upstream source link, team link, and a **More [Player] News** link.
The news-item URL embeds its publication date and slug, for example:

```text
/fantasy/football/player-news/2026-08-30/<headline-slug>
```

Player routes use a numeric or UUID-like NBC identifier, not a name alone:

```text
/nfl/<player-slug>/<player-id>
/nfl/<player-slug>/<player-id>/news
/nfl/<player-slug>/<player-id>/stats
/nfl/<player-slug>/<player-id>/game-log
```

Follow the site's current link; do not manufacture player IDs.

Team pages expose Overview, Player News, Rumor Mill, Stats, Depth Chart,
Injuries, Schedule, and Roster routes. The football navigation revealed all 32
team depth-chart links. In the sampled Buffalo pages, however, **Depth Chart**
and **Injuries** rendered their headings without data rows, and the sampled
player **Career Stats** page rendered a heading without visible rows. Treat
these routes as available navigation, not guaranteed draft-time data. Prefer
current player news and another verified source if a supporting table is empty.

The Favorites and Profile controls were visible, and the feed described
favorite-based personalization. Account creation, sign-in, persistence, and
favorite mutation were not exercised, so their exact access requirements remain
unverified. All core research pages above were readable without login.

## Freshness rules

NBC surfaces update on different schedules:

- player-news cards show relative ages measured in minutes or hours;
- articles show a publication date and time;
- Draft Central is a mutable link hub whose article set changes;
- the live Top 200 promises preseason updates but showed no separate modified
  timestamp;
- the downloadable PDF is a static snapshot and already lagged the live table.

For every use, record the page publication label and the actual retrieval time.
Never infer currentness from `2026` in a URL. Re-open Draft Central to discover
the current article instead of changing year or slug strings programmatically.

## Draft-time agent procedure

### Before the room opens

1. Refresh Yahoo league scoring, roster settings, draft order, keepers, and
   available players. Yahoo remains authoritative.
2. Open Draft Central and record its publication label and retrieval time.
3. Open the live Top 200. Record that it is PPR and capture its current visible
   publication label.
4. Open the relevant QB/RB/WR/TE tier pages. Use DST and kicker only when those
   positions are plausibly draftable.
5. Download the current PDF through NBC's explicit link if an offline fallback
   is useful. Record the retrieval time; do not assume it matches the live
   board.
6. Review recent ADP Movers, sleepers/busts, and team previews for the small set
   of players likely to be available near the next two picks.
7. Refresh the player-news feed for Injury and Transaction items at QB, RB, WR,
   and TE. Do not rely on an empty team injury or depth-chart table.

### For each live pick

1. Read the Yahoo board and remove drafted players and keepers.
2. Build a short candidate list from roster need, the live Yahoo pool, and the
   highest remaining Rotoworld tier or Top 200 names.
3. Adjust for the PPR mismatch. For this non-PPR league, do not reward reception
   volume without also checking yards, touchdowns, role, and the verified Yahoo
   scoring rules.
4. Check each finalist in player news for injuries, transactions, team changes,
   and role analysis. Carry the news timestamp and source.
5. Use ADP-mover and default-rank articles only as draft-timing evidence. They
   are not projections and do not prove player value.
6. Use the player profile's football stats and **What's changed/Outlook** prose
   to explain the case, clearly separating fact from analyst opinion.
7. Recommend no more than three players, with one main risk each.

Suggested response:

```text
1. Player - Rotoworld live rank/tier; Yahoo roster fit; current news; main risk
2. Player - alternate construction; current news; main risk
3. Player - fallback if the first two go

NBC evidence: <pages>, published <labels>, retrieved <time>; PPR mismatch noted.
Yahoo unavailable set: refreshed at <time>.
```

## Access, reuse, and automation constraints

The [NBC Sports Terms of Use](https://www.nbcsports.com/terms-use) limit the
service to personal, non-commercial use and restrict copying, downloading, and
redistribution except where NBC explicitly permits it. The draft-kit page does
explicitly provide its PDF as a download.

More importantly for agents, the terms prohibit software robots, spiders,
crawlers, and other data-gathering or extraction tools, whether automated or
manual, from accessing, acquiring, copying, monitoring, scraping, or aggregating
site content. They also prohibit bypassing access controls, content protection,
copyright notices, and advertisements.

Operational rule: do not build or run a scraper, crawler, bulk extractor,
monitor, or unofficial NBC API client. Use the public pages visibly and
interactively for personal draft decisions, keep only minimal source-attributed
notes, and use the explicitly offered PDF as an ephemeral reference. Obtain
permission before any broader collection, storage, redistribution, or
commercial use.

The site displayed advertisements, sponsored recirculation, a prompt to enable
ads, privacy controls, and sportsbook odds. Do not bypass the ad prompt or mix
sponsored material/odds into player recommendations. The terms also describe
the service as intended for U.S. access; availability may differ elsewhere.

## Reliability cautions

- The public pages are large, ad-heavy, and dynamically composed. Headings,
  cards, and load-more controls are safer navigation anchors than brittle CSS
  positions.
- News feeds and article indexes use dynamic pagination. A first render is not
  a complete historical dataset.
- Live rankings, the PDF, position articles, and ADP commentary can disagree
  because they update independently.
- Overall rankings are PPR, while this Yahoo league is non-PPR with custom
  scoring. Never reuse the displayed order as a custom-scoring projection.
- Relative news ages such as `9m` become stale immediately. Preserve an
  absolute retrieval time.
- Player and team stats, injury, or depth-chart routes can render with no rows.
  Empty output is unavailable evidence, not proof that no injury or player
  exists.
- Player names and teams change. Preserve the NBC player URL/identifier and
  position, then reconcile to Yahoo; do not join on name alone.
- No supported public JSON API, CSV export, or ranking filter was found. Do not
  infer one from page internals.
- Articles and profiles combine sourced facts with Rotoworld interpretation.
  Attribute both and keep them logically separate.

## Evidence reviewed

- [NBC Sports Fantasy home](https://www.nbcsports.com/fantasy)
- [Fantasy Football home](https://www.nbcsports.com/fantasy/football)
- [Rotoworld 2026 Fantasy Football Draft Central](https://www.nbcsports.com/fantasy/football/news/rotoworld-fantasy-football-draft-central-2026-rankings-strategy-sleepers-and-more)
- [Rotoworld 2026 Fantasy Football Draft Kit](https://www.nbcsports.com/fantasy/football/rotoworld-2026-fantasy-football-draft-kit-rankings-cheat-sheets-player-profiles)
- The 98-page PDF linked by the Draft Kit page, downloaded and inspected
  2026-08-30; the mutable CDN URL is intentionally not treated as a stable API
- [2026 Top 200 Overall Rankings](https://www.nbcsports.com/fantasy/football/news/2026-fantasy-football-top-200-overall-rankings)
- [NFL Player News](https://www.nbcsports.com/fantasy/football/player-news)
- [2026 Team Previews Hub](https://www.nbcsports.com/fantasy/football/news/2026-rotoworld-fantasy-football-team-previews-hub)
- [Example player overview](https://www.nbcsports.com/nfl/george-kittle/5453)
- [Example player stats route](https://www.nbcsports.com/nfl/george-kittle/5453/stats)
- [Example team depth chart](https://www.nbcsports.com/nfl/buffalo-bills/depth-chart)
- [Example team injuries](https://www.nbcsports.com/nfl/buffalo-bills/injuries)
- [NBC Sports Terms of Use](https://www.nbcsports.com/terms-use)

## Evidence-readiness verdict

Ready for review as a visible, personal-use secondary source. The public site
offers current rankings, structured player news, extensive editorial analysis,
and a useful offline kit. It is not ready for unattended ingestion: the overall
board is a scoring mismatch, the PDF can lag, some supporting routes render
empty, no supported data API was found, and the terms expressly prohibit data
extraction tools.
