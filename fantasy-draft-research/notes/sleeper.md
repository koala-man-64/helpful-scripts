# Sleeper fantasy-football tool guide

Public Sleeper pages, first-party support material, API documentation, and small
read-only API endpoints were reviewed on 2026-08-30. No account was created or
used, and no private league or draft was opened.

## Verdict

Sleeper is a strong **preparation-only** tool for the current Yahoo league:

- run repeatable mock drafts against CPU or human managers;
- model an eight-team snake board with custom roster positions and a keeper
  placed in its charged round;
- compare Sleeper's player ordering and ADP with Yahoo, DraftKick, Boris Chen,
  and FFToday;
- use the official read-only API for NFL state, player identity, public Sleeper
  league/draft data, and add/drop trends when that signal has a defined role.

It is not authoritative for the Yahoo clock, available-player pool, completed
picks, keeper state, or roster. The public material reviewed does not establish
Yahoo draft-room synchronization. Keep Yahoo open and authoritative, and do not
count Sleeper ADP twice when it is already present in FFToday's composite ADP or
another tool.

The mock-draft workflow still needs one signed-in rehearsal configured to this
league before Sleeper can be marked qualified. Until then its readiness is
**degraded** and its usage class remains **preparation only**.

## What the draftboard contributes

Sleeper describes its mock drafts as free on desktop, iOS, and Android. A board
can use CPU opponents, invited people, or both. First-party pages state that the
draftboard supports:

- redraft, keeper, and dynasty formats;
- snake, linear, third-round-reversal, and auction drafts;
- team count, scoring type, roster-position, and player-pool customization;
- custom ADP;
- editable keeper tiles and reusable/copyable boards;
- pausing, pick edits, and saved prior mock results;
- shareable boards and a big-screen view.

For this league, the most useful configuration is eight teams, snake order,
slot 1, the verified Yahoo roster shape, non-PPR scoring, 16 rounds, and
TreVeyon Henderson locked at pick 49. Sleeper's public mock-draft support page
documents scoring *types*, not every Yahoo scoring field. Do not claim that the
mock reproduces the league's yardage bonuses or short-field-goal rules until a
signed-in settings review proves it.

### Draft-timer and recovery semantics

Sleeper calls its no-autopick behavior a soft timer: when commissioner autopick
is off, expiration does not force a selection. The commissioner can pause the
draft, change time per pick, force CPU autopick, and resume with the current
team's timer reset. A forced CPU pick consults that team's queue when one exists
and otherwise considers positional need and higher-ranked available players.

These are Sleeper-room controls, not Yahoo controls. In a Sleeper rehearsal,
verify the board, current team, timer mode, and keeper tiles after every pause,
undo, or manual edit. In the real Yahoo draft, use Yahoo's own recovery rules.

## Official read-only API

The [Sleeper API](https://docs.sleeper.com/) is a public HTTP API. Its
documentation says:

- no API token is required because the API is read-only;
- non-commercial use is free;
- commercial use requires contacting Sleeper about licensing;
- clients should remain below 1,000 calls per minute;
- the full NFL player map is roughly 5 MB and should be fetched no more than
  once per day; filtered player responses are preferable when sufficient.

Useful documented endpoints include:

| Need | Endpoint | Important fields or boundary |
| --- | --- | --- |
| Current NFL phase | `GET /v1/state/nfl` | Season, week, season type, display week, and season start date |
| Filtered identities | `GET /v1/players/nfl?position=QB&active=true` | Sleeper player ID, name, team, position, status, injury fields, and third-party IDs when populated |
| Add/drop signal | `GET /v1/players/nfl/trending/add` or `/drop` | Player ID and transaction count for a requested lookback; activity is not player value |
| Public Sleeper league | `GET /v1/league/{league_id}` | Scoring, roster positions, team count, status, season, and draft ID |
| League drafts | `GET /v1/league/{league_id}/drafts` | Draft type, status, settings, order, timestamps, and IDs |
| Draft state | `GET /v1/draft/{draft_id}` | Draft order, slot-to-roster mapping, settings, status, and last-pick time |
| Draft picks | `GET /v1/draft/{draft_id}/picks` | Player ID, round, slot, overall pick, keeper flag, roster, and pick metadata |
| Rosters | `GET /v1/league/{league_id}/rosters` | Player IDs, starters, reserve, roster ID, and owner ID |

The API documentation says its league data is unauthenticated, but that does
not make participant or league identifiers appropriate for this repository.
Use only a user-provided league/draft ID for a bounded task, keep it out of
commits and logs, and never store usernames, display names, owner IDs, avatars,
invite links, or private board URLs.

### Smoke result

At `2026-08-30T15:28:18Z`, these official endpoints returned successfully:

- `/v1/state/nfl`: season `2026`, regular season, display week `1`;
- `/v1/players/nfl/trending/add?lookback_hours=24&limit=5`: five rows;
- `/v1/players/nfl?position=QB&active=true`: a keyed player map.

The smoke proves public read access and response shape only. It does not prove
historical completeness, projection accuracy, mock-draft behavior, account
persistence, or live Yahoo integration.

## Data semantics and joins

Sleeper's stable join key inside its own API is `player_id`. Preserve it in any
ephemeral comparison. For cross-source comparisons, join on normalized player
name plus position and NFL team, then manually exclude ambiguous identities.
Do not treat populated third-party IDs as complete or permanently stable.

Keep the signals distinct:

- draft pick order is observed Sleeper board state;
- ADP or search rank is market/order context, not a projection;
- trending `count` is recent add/drop activity for the requested window, not
  popularity percentage, availability probability, or expected points;
- injury, team, depth-chart, and news timestamps can become stale independently;
- the NFL-state endpoint describes Sleeper's platform calendar, not a player's
  data freshness.

If Sleeper disagrees with another source, compare scoring format, team count,
keeper exclusions, timestamp, player identity, and upstream data before using
the difference. Preserve material disagreement instead of averaging unlike
fields.

## Access, reliability, and safety

Observed public access was quick for the marketing, support, documentation,
NFL-state, filtered-player, and five-row trending paths. Authenticated board
creation, saved-board recovery, mobile behavior, custom-ADP entry, auction
behavior, and completed-mock export were not exercised.

Expected failure modes:

- account or verification prompt blocks draftboard creation;
- stale board configuration no longer matches Yahoo settings or keepers;
- soft-timer/autopick mode differs from the intended rehearsal;
- a shared board exposes participant names or a reusable invite URL;
- a large or repeated player-map request is slow or triggers rate controls;
- player fields are null, stale, renamed, or missing for a cross-source join;
- a Sleeper room changes while a cached API response remains unchanged.

Sleeper's [General Terms of Use](https://support.sleeper.com/en/articles/5486620-general-terms-of-use),
last updated 2026-08-27, prohibit crawling, scraping, and automated or
systematic extraction without written consent. They also prohibit giving a
third party credentials, tokens, or session identifiers to access an account.
Use only the explicitly documented API within its stated non-commercial and
rate boundaries. Do not scrape the web application, automate login, inspect
browser storage, or automate picks.

## Preparation procedure

For the compact owner-operated checklist and copyable results record, use the [repeatable Sleeper mock-draft runbook](sleeper-mock-draft-runbook.md).

1. Refresh the authoritative Yahoo settings, teams, order, and keeper notes.
2. Use the owner-completed [Sleeper browser sign-in and draftboard handoff](sleeper-login.md).
   Do not record account or session data, automate authentication, inspect
   browser storage, or automate Sleeper picks.
3. Create a football mock board and set eight teams, snake order, slot 1, 16
   rounds, and the verified roster positions.
4. Select non-PPR scoring. Record any Yahoo scoring rule the board cannot
   reproduce, especially the yardage bonuses and field-goal differences.
5. Place TreVeyon Henderson at overall pick 49 and confirm the locked tile is in
   the correct team column.
6. Set the intended timer and CPU behavior. Populate the queue only with
   acceptable fallbacks.
7. Run a short mock. Verify board state, roster shape, keeper behavior, pause or
   undo recovery, and saved-result access.
8. Compare at least one early, middle, and late candidate with Yahoo and one
   independent source. Identify any signal already inherited through FFToday
   or DraftKick.
9. Record latency and freshness in the draft's source manifest. Keep Sleeper
   preparation-only unless it passes every readiness check in the canonical
   workflow.

## Draft-time agent procedure

If Sleeper has been qualified for the current draft, use it before the Yahoo
room opens to rehearse roster construction, keeper-adjusted pick spacing, and
fallback sequences. During the live Yahoo draft:

1. Read the Yahoo room first.
2. Consult Sleeper only for its assigned secondary signal.
3. Never infer Yahoo availability from a Sleeper mock or public league.
4. Do not open or manipulate a Sleeper board if doing so would interrupt Yahoo
   clock monitoring.
5. State the source and timestamp when Sleeper materially changes a
   recommendation.

## Evidence reviewed

- [Sleeper Fantasy Football](https://sleeper.com/fantasy-football)
- [Sleeper mock drafts](https://sleeper.com/mockdraft)
- [Sleeper draftboard](https://sleeper.com/draftboard)
- [How to create a mock draft](https://support.sleeper.com/en/articles/3982891-how-to-create-a-mock-draft)
- [How the draft timer works](https://support.sleeper.com/en/articles/4029085-how-does-the-draft-timer-work)
- [Keeper round costs](https://support.sleeper.com/en/articles/2219811-how-do-i-set-the-round-cost-for-keepers)
- [Sleeper API](https://docs.sleeper.com/)
- [General Terms of Use](https://support.sleeper.com/en/articles/5486620-general-terms-of-use)
