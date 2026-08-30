# Fantasy draft research-tool readiness — 2026-08-30

Evaluation date: 2026-08-30

Inventory base: `origin/main` at `dc6703fd89105f005424ae6d056f7605db84dcfc`

League context: eight-team Yahoo keeper league, non-PPR, custom yardage bonuses, three starting WRs, two RBs, one flex, position 1, and TreVeyon Henderson kept in round 7.

This is the current source manifest required by the [draft-day workflow](draft-day-workflow.md). It evaluates every operational draft surface documented on the inventory base. Yahoo help pages, external terms, and upstream methodology pages remain evidence and safety controls; they are not separate pick-selection tools. The repository's `tools/` directory contains no implemented tool beyond its placeholder.

## Method

Each surface was checked against purpose, access, freshness, league fit, identity matching, field semantics, coverage, latency, reliability, safety, and an independent cross-check. The evaluation combined:

- the merged site guides and source catalog;
- safe public read/access checks on 2026-08-30;
- a signed-in, read-only Yahoo check that confirmed the profile control, **My Teams and Leagues**, the expected team entry, and **Draft Central Overview** with mock-draft and ranking controls;
- prior completed Yahoo mock-draft evidence; and
- no new authentication, purchases, extensions, scraping, bulk extraction, or live-room mutations.

Verdicts use the workflow's four usage classes:

- **Live:** frozen before room-open and safe for quick use between opponent picks.
- **Preparation only:** valuable before the room, but too slow, fragile, stateful, or distracting for the live loop.
- **Fallback:** consulted for a narrow question when the primary layer is missing, stale, or disputed.
- **Excluded:** not eligible until a named readiness gap is closed.

## Current source manifest

| Surface | Readiness | Usage | Primary role | Current evidence and freshness | League adjustment | Latency / reliability | Fallback or stop condition |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Yahoo league, Players, Research, queue, Board, Picks, roster, and draft room | Qualified | **Live — primary authority** | Clock, availability, completed picks, roster, keepers, and final state | Signed-in read check passed 2026-08-30; team hub and Draft Central were reachable | Already league-specific; recheck settings/order at room-open | Quick interactive path; browser/session and network remain dependencies | Manager takes the clock during control loss. If Yahoo itself is unavailable, stop automatic entry; no secondary source can establish room state. |
| Yahoo Draft Central, instant mocks, and public mocks | Qualified | **Preparation only** | Interface rehearsal, queue practice, timing, and decision-loop testing | Two completed 2026-08-30 public mocks plus current Draft Central read check | Public mocks can differ from the private league's teams, scoring, roster, keepers, and seat | Interactive and reliable enough for rehearsal; room inventory changes continuously | Manual scenario work in DraftKick; never treat a mock result as live availability. |
| DraftKick unsigned/manual app | Qualified with conditions | **Preparation only** | Best league-adjusted modeling, simulation, VORP/Impact, roster fit, and board configuration | Public app reachable 2026-08-30; merged guide records build `v1.22.27`, built 2026-08-28, projections dated 2026-08-28 | Must replace defaults with the exact Yahoo scoring, eight teams, 16 rounds, roster, order, keeper, and traded picks | Interactive/manual; unsigned state is `Not saved` and may disappear on reload or close | Preserve an independent board. Fall back to Yahoo plus cached Boris/FFToday inputs. |
| DraftKick Live extension, paid persistence, and automatic room sync | Not qualified | **Excluded** | Potential synchronization and in-room overlay | Site describes the capability, but installation, permissions, authenticated persistence, pick sync, recovery, and completed live use were not exercised | Unknown until configured against this exact Yahoo league | Unknown; a sync mismatch could corrupt draft state | Keep manual. A separate reviewed test must verify extension permissions, pick parity, disconnect recovery, and safe disablement. |
| FFToday rankings, projections, tiers, outlooks, and ADP | Qualified with conditions | **Preparation only; fallback when cached** | Independent non-PPR value, projection, risk/upside, and market-timing cross-check | Public pages reachable 2026-08-30; rankings/projections updated 2026-08-27 and ADP updated 2026-08-24 | Top 225 and ADP assume a 12-team market; custom bonuses, eight-team depth, keepers, and Yahoo availability require adjustment | Quick public HTML, but manual page work can consume the 75-second clock; no API/SLA | Use a prepared shortlist. Fall back to refreshed Boris tiers or Yahoo-visible ranks/ADP. |
| FFToday historical stats, consistency, strength of schedule, matchup history, and secondary tools | Qualified with conditions | **Preparation only** | Context for durability, matchup, usage, and risk—not direct live value | Public pages reviewed/reachable 2026-08-30; historical windows and scoring presets vary | Default Half-PPR/Yahoo presets are not this league; logged-in/custom and integrated tools remain untested | Manual, multi-page, and too slow for the active loop | Use current projections/news instead; omit when the statistic cannot be reconciled to the league and season. |
| Boris Chen standard Top 200 and positional tiers | Qualified with conditions | **Live — secondary, cached before room** | Compact consensus-value and tier-break detection | Public standard CSV reachable 2026-08-30; `Last-Modified` was 2026-08-28 06:00:59 GMT | Standard format matches non-PPR reception scoring, but not bonuses, roster depth, keeper cost, or availability | Direct artifact is quick/cacheable; mutable, unversioned, and has no schema/uptime guarantee | Fall back to prepared FFToday non-PPR ranks or Yahoo ranks. Exclude if timestamp/schema cannot be verified. |
| NBC Sports/Rotoworld live Draft Central, rankings, articles, and player news | Qualified with conditions | **Fallback; targeted live lookup only** | Fresh injury/role/transaction context, analyst tiers, and ADP movement | Public Draft Central reachable 2026-08-30 and labeled published 2026-08-28; articles update independently | Overall ranks are PPR and cannot be copied into this non-PPR custom league | Targeted reads can be quick; pages are large, dynamic, ad-heavy, and sometimes empty | Yahoo and primary team/NFL reporting for state/facts; FFToday/Boris for structured value. Stop if the page needs broad extraction. |
| NBC Sports/Rotoworld 98-page static Draft Kit PDF | Qualified with conditions | **Preparation only; offline fallback** | Player profiles, broad position coverage, cheat sheets, and narrative context | Official public download resolved 2026-08-30; static snapshot already differed from the mutable live table | Predominantly PPR/best-ball/dynasty and not keeper- or league-adjusted | Fast and reliable once cached; staleness grows immediately | Use live NBC pages for current news and structured sources for value. Never prefer the PDF over a newer contradiction. |
| Reddit `r/fantasyfootball` interactive feed, search, Index, posts, and comments | Qualified with conditions | **Fallback; broad use is preparation only** | Breaking-signal discovery, counterarguments, sentiment, and discovery of primary sources/tools | Public feed and scoped search reachable 2026-08-30; current Index and timestamps visible | Mixed standard/PPR/superflex/dynasty/keeper/etc.; every claim needs league-context filtering | Targeted read can be quick, but content is noisy, volatile, removable, and subject to login/JS challenges | Open and verify the primary linked source. Fall back to classic Reddit for visible reading or omit the claim. Never rank by votes/comments. |

## Activation decision

Freeze this set when the Yahoo room opens:

1. **Yahoo live surfaces — active primary.** They are the only authority for the clock, player availability, pick acceptance, roster state, and completion.
2. **Boris Chen standard tiers — active secondary only if refreshed and cached.** Use for fast tier/value checks; remove keepers and drafted players locally.
3. **NBC live player news — fallback only.** Open only for a narrow current injury/role question between opponent picks; broad editorial review belongs before the room.
4. **Reddit — fallback only.** Use to discover a possibly breaking fact or counterargument, then verify the underlying primary source. Do not browse discussion while on the clock.
5. **FFToday — prepared cross-check.** Carry a cached non-PPR shortlist into the room. Avoid multi-page manual research during the live loop.
6. **DraftKick manual, Yahoo mocks, FFToday secondary tools, and the NBC PDF — preparation only.** Their useful outputs must be distilled into the board before room-open.
7. **DraftKick Live sync — excluded.** Do not install, enable, or rely on it on draft day without a separate successful rehearsal.

No source discovered or merged after room-open is eligible for the live set during that draft.

## Cross-source controls

- Match players by normalized name plus position and reconcile NFL team changes. Name-only joins are not sufficient.
- Remove every Yahoo keeper and drafted player before applying any external rank. TreVeyon Henderson is already committed to the team's seventh-round slot.
- Yahoo rank or ADP selected inside DraftKick is not independent confirmation of Yahoo's own rank or ADP.
- DraftKick's composite can include FFToday and other feeds. Do not count the composite and its included source as two votes.
- Boris Chen is derived from FantasyPros expert consensus. Another FantasyPros presentation is the same upstream family unless proven otherwise.
- Reddit submissions and NBC/FFToday links count as one underlying source, not separate corroboration.
- Do not average PPR ranks, non-PPR ranks, projected points, ADP, tiers, VORP, and community votes. Preserve their different semantics and use the workflow's candidate-ordering rules.
- A current direct injury/transaction source can override an older ranking assumption, but it does not by itself determine player value.

## Readiness gaps and next tests

| Priority | Gap | Required test or decision |
| ---: | --- | --- |
| 1 | No final freshness thresholds | Before the real draft, set maximum ages for news, ranks/projections, tiers, and ADP. Any material later news invalidates an older otherwise-passing artifact. |
| 2 | DraftKick Live unverified | In a non-consequential mock, review extension permissions, connect the intended room, compare every synced pick with Yahoo, simulate disconnect/reconnect, and prove a safe manual fallback. |
| 3 | DraftKick configuration not frozen | Enter the exact league settings/keepers/order, verify Board and Rosters, record build/projection timestamps, and decide how state will survive or be reconstructed. |
| 4 | No consolidated player identity map | Build a minimal ephemeral map of Yahoo player identity to normalized name, NFL team, and position; reject ambiguous rows. Do not commit restricted ranking datasets. |
| 5 | Current news authority not fixed | Choose the primary team/NFL news path used to verify NBC/Reddit discoveries and define the evidence recorded for a breaking change. |
| 6 | Live latency not rehearsed with the full source set | Run an eight-team position-1 mock using the frozen manifest and measure whether Yahoo monitoring remains continuous. Demote any tool that delays the queue or pick. |

## Per-surface evidence

- [Yahoo Fantasy Sports](https://sports.yahoo.com/fantasy/) and the signed-in league surfaces documented in the [Yahoo notes](yahoo-football-navigation.md)
- [DraftKick Football](https://app.draftkick.com/football) and the [merged guide](draftkick-football.md)
- [FFToday Rankings & Projections](https://www.fftoday.com/rankings/) and the [merged guide](fftoday.md)
- [Boris Chen Draft Sheets](https://www.borischen.co/p/draft-sheets.html) and the [merged tier guide](boris-chen-draft-tiers.md)
- [Rotoworld Draft Central](https://www.nbcsports.com/fantasy/football/news/rotoworld-fantasy-football-draft-central-2026-rankings-strategy-sleepers-and-more) and the [merged NBC guide](nbc-sports-fantasy.md)
- [Reddit r/fantasyfootball](https://www.reddit.com/r/fantasyfootball/) and the [merged Reddit guide](reddit-fantasyfootball.md)
- [Source catalog](../sources.md) for access, methodology, terms, and support references

## Final verdict

The inventory is usable for draft preparation now, but only Yahoo and a refreshed cached Boris tier sheet qualify for the default live set. FFToday and DraftKick should shape the prepared board; NBC and Reddit should answer narrow news/context questions; Yahoo mocks and the NBC PDF are rehearsal/offline aids. DraftKick Live synchronization is the only inventoried operational capability explicitly excluded pending a controlled mock validation.
