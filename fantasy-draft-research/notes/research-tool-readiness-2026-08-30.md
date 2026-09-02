# Fantasy draft research-tool readiness — 2026-08-30

Evaluation date: 2026-08-30

Inventory base: `origin/main` at `0d641c99174efc4621608e2bf6ed26a86c12df38`

League context: eight active teams in a Yahoo league configured for a maximum of ten, non-PPR, custom yardage bonuses, three starting WRs, two RBs, one flex, position 1, and TreVeyon Henderson kept in round 7. Draft modeling uses the eight active teams and published eight-position order unless the current Yahoo Managers or Draft Results page changes.

This is the current source manifest required by the [draft-day workflow](draft-day-workflow.md). It evaluates every operational draft surface documented on the inventory base. Yahoo help pages, external terms, and upstream methodology pages remain evidence and safety controls; they are not separate pick-selection tools. The repository's `tools/` directory contains no implemented tool beyond its placeholder.

## Method

Each surface was checked against purpose, access, freshness, league fit, identity matching, field semantics, coverage, latency, reliability, safety, and an independent cross-check. The evaluation combined:

- the merged site guides and source catalog;
- safe public read/access checks on 2026-08-30;
- safe anonymous public checks of FantasyPros and Unabated product, pricing,
  documentation, and terms pages on 2026-09-02, with no login, subscription,
  API call, export, upload, or transaction;
- a current signed-in, read-only Yahoo check that confirmed the profile control, **My Teams and Leagues**, the expected team entry, and **Draft Central Overview** with mock-draft and ranking controls;
- prior completed Yahoo mock-draft evidence; and
- no new authentication, purchases, extensions, scraping, bulk extraction, or live-room mutations.

Verdicts use the workflow's four usage classes:

- **Live:** frozen before room-open and safe for quick use between opponent picks.
- **Preparation only:** valuable before the room, but too slow, fragile, stateful, or distracting for the live loop.
- **Fallback:** consulted for a narrow question when the primary layer is missing, stale, or disputed.
- **Excluded:** not eligible until a named readiness gap is closed.

## Current source manifest

| Surface | Readiness | Usage | Primary role | Evidence status / freshness | League adjustment | Latency / reliability | Fallback or stop condition |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Yahoo league, Players, Research, queue, Board, Picks, roster, and draft room | Qualified | **Live — primary authority** | Clock, availability, completed picks, roster, keepers, and final state | **Current:** signed-in read check passed 2026-08-30; team hub and Draft Central were reachable | Already league-specific; recheck active teams, settings, and order at room-open | Quick interactive path; browser/session and network remain dependencies | Manager takes the clock during control loss. If Yahoo itself is unavailable, stop automatic entry; no secondary source can establish room state. |
| Yahoo Draft Central, instant mocks, and public mocks | Qualified | **Preparation only** | Interface rehearsal, queue practice, timing, and decision-loop testing | **Current + prior:** Draft Central read check passed 2026-08-30; two public mock completions are prior artifacts from the same date | Public mocks can differ from the private league's teams, scoring, roster, keepers, and seat | Interactive and reliable enough for rehearsal; room inventory changes continuously | Manual scenario work in DraftKick or Sleeper; never treat a mock result as live availability. |
| DraftKick unsigned/manual app | Qualified with conditions | **Preparation only** | Best league-adjusted modeling, simulation, VORP/Impact, roster fit, and board configuration | **Current + documented:** public app reachable 2026-08-30; merged guide records build `v1.22.27`, built 2026-08-28, projections dated 2026-08-28 | Must replace defaults with the exact Yahoo scoring, eight active teams, 16 rounds, roster, order, keeper, and traded picks | Interactive/manual; unsigned state is `Not saved` and may disappear on reload or close | Preserve an independent board. Fall back to Yahoo plus cached Boris/FFToday inputs. |
| DraftKick Live extension, paid persistence, and automatic room sync | Not qualified | **Excluded** | Potential synchronization and in-room overlay | **Site-stated/unverified:** installation, permissions, authenticated persistence, pick sync, recovery, and completed live use were not exercised | Unknown until configured against this exact Yahoo league | Unknown; a sync mismatch could corrupt draft state | Keep manual. A separate reviewed test must verify extension permissions, pick parity, disconnect recovery, and safe disablement. |
| Sleeper mock draft and draftboard | Degraded pending rehearsal | **Preparation only** | Keeper-aware eight-team mock rehearsal, saved boards, custom ADP, and alternate market/order context | **Current + site-stated:** public first-party pages reachable and guide merged 2026-08-30; no account or signed-in board was exercised | Supports eight teams, snake, roster customization, and keeper tiles; exact Yahoo bonuses and short-FG scoring fidelity remain unverified | Public pages were quick; account state, board creation, saved recovery, timer behavior, and completed export remain untested | Use Yahoo mocks or DraftKick until a league-matched signed-in rehearsal passes. Never infer Yahoo availability or sync. |
| Sleeper official read-only API | Qualified with conditions | **Preparation only** | NFL phase, filtered player identity, add/drop trend, and public Sleeper league/draft context | **Current:** state, filtered active-QB, and five-row add-trend endpoints succeeded at 2026-08-30T15:28:18Z | Not Yahoo state. Trend count is activity, not value; public Sleeper leagues use their own scoring/rosters | Quick documented endpoints; stay below published limits and fetch the full player map no more than daily | Use Yahoo/player-source facts when data is stale or ambiguous. Do not retain private league/user identifiers. |
| FFToday rankings, projections, tiers, outlooks, and ADP | Qualified with conditions | **Preparation only; fallback when cached** | Independent non-PPR value, projection, risk/upside, and market-timing cross-check | **Current:** public pages reachable 2026-08-30; rankings/projections updated 2026-08-27 and ADP updated 2026-08-24 | Top 225 and ADP assume a 12-team market; custom bonuses, eight-team depth, keepers, and Yahoo availability require adjustment | Quick public HTML, but manual page work can consume the 75-second clock; no API/SLA | Use a prepared shortlist. Fall back to refreshed Boris tiers or Yahoo-visible ranks/ADP. |
| FFToday historical stats, consistency, strength of schedule, matchup history, and secondary tools | Qualified with conditions | **Preparation only** | Context for durability, matchup, usage, and risk—not direct live value | **Current + documented:** public pages reviewed/reachable 2026-08-30; historical windows and scoring presets vary | Default Half-PPR/Yahoo presets are not this league; logged-in/custom and integrated tools remain untested | Manual, multi-page, and too slow for the active loop | Use current projections/news instead; omit when the statistic cannot be reconciled to the league and season. |
| Boris Chen standard Top 200 and positional tiers | Qualified with conditions | **Live — secondary, cached before room** | Compact consensus-value and tier-break detection | **Current:** public standard CSV reachable 2026-08-30; `Last-Modified` was 2026-08-28 06:00:59 GMT | Standard format matches non-PPR reception scoring, but not bonuses, roster depth, keeper cost, or availability | Direct artifact is quick/cacheable; mutable, unversioned, and has no schema/uptime guarantee | Fall back to prepared FFToday non-PPR ranks or Yahoo ranks. Exclude if timestamp/schema cannot be verified. |
| NBC Sports/Rotoworld live Draft Central, rankings, articles, and player news | Qualified with conditions | **Fallback; targeted live lookup only** | Fresh injury/role/transaction context, analyst tiers, and ADP movement | **Current:** public Draft Central reachable 2026-08-30 and labeled published 2026-08-28; articles update independently | Overall ranks are PPR and cannot be copied into this non-PPR custom league | Targeted reads can be quick; pages are large, dynamic, ad-heavy, and sometimes empty | Yahoo and primary team/NFL reporting for state/facts; FFToday/Boris for structured value. Stop if the page needs broad extraction. |
| NBC Sports/Rotoworld 98-page static Draft Kit PDF | Qualified with conditions | **Preparation only; offline fallback** | Player profiles, broad position coverage, cheat sheets, and narrative context | **Current + documented:** official public download resolved 2026-08-30; prior comparison found the static snapshot already differed from the mutable live table | Predominantly PPR/best-ball/dynasty and not keeper- or league-adjusted | Fast and reliable once cached; staleness grows immediately | Use live NBC pages for current news and structured sources for value. Never prefer the PDF over a newer contradiction. |
| RotoBaller public NFL rankings, projections, ADP, cheat sheets, and player news | Qualified with conditions | **Fallback; targeted live lookup only** | Standard/non-PPR ranking opinion, market timing, and current injury/transaction/role context | **Current:** visible public pages and standard/non-PPR selector reviewed 2026-08-31; each page's update label must be re-read | The standard view matches no-reception scoring, but not custom Yahoo bonuses, eight-team depth, keepers, or availability | Large dynamic/ad-supported pages; rankings, projections, ADP, and editorial content may update independently | Use a prepared shortlist and corroborate material news with a primary team/NFL source. Stop if a page is slow, stale, unavailable, premium-gated, or would require broad extraction. |
| RotoBaller mock drafts, live assistant, team sync, and premium NFL tools | Not qualified | **Excluded** | Potential rehearsal, customization, and live-room support | **Site-stated/unverified:** public entry points were visible 2026-08-31; no subscription, login, account link, configuration, persistence, permission, timer, or Yahoo synchronization test was performed | Unknown until tested against this league's exact scoring, eight active teams, keepers, and order | Unknown; account/sync mismatch or a premium dependency could distract from Yahoo during the live loop | Keep manual. A separate non-consequential rehearsal must verify configuration, visible pick parity, disconnect/recovery, permissions, and safe disablement. |
| FantasyPros public standard rankings, ECR/ADP metadata, projections, and news | Qualified with conditions | **Preparation only; targeted fallback when fresh** | Standard/non-PPR consensus context, rank dispersion, ADP timing, and named current player-research questions | **Current:** anonymous standard draft page exposed rank, dispersion, ADP, expert update labels, and a page date on 2026-09-02; public tools page exposed research categories | Standard/non-PPR does not reproduce custom Yahoo bonuses, active-team count, keepers, roster scarcity, or availability; ECR is the same upstream family as Boris Chen | Direct pages were quick, but content and individual tool gates are mutable; a public tool listing does not prove anonymous, league-specific access | Use prepared Yahoo/Boris/FFToday inputs for the live loop. Reopen only for a concrete disagreement or current news question; stop if stale, slow, sign-in-gated, premium-gated, or duplicative of Boris. |
| FantasyPros Draft Assistant, My Playbook, paid plans, league import, sync, browser extensions, API, and automated actions | Not qualified | **Excluded** | Potential customized draft and in-season assistance | **Observed/site-stated:** public help and pricing pages describe paid Draft Assistant, My Playbook, sync, and API features; no account workflow was exercised on 2026-09-02 | Unknown until tested against the exact Yahoo league, custom scoring, keepers, and order | Subscription, account, sync, extension, timer, pick parity, recovery, and privacy behavior are unverified | Keep manual. A separate owner-authorized, non-consequential rehearsal must verify access, permissions, configuration, pick parity, recovery, safe disablement, and no unwanted submissions. |
| Unabated public NFL, props, pricing, API-documentation, and terms pages | Not qualified | **Excluded** | Potential future market-context research for a named player-stat or team-environment question | **Current:** public explanatory pages and paid-tier boundary were visible on 2026-09-02; no permitted anonymous live market, player-prop, futures, API, or export read was exercised | Odds, implied probabilities, props, futures, and fantasy projections are different semantic objects and do not encode this Yahoo league's scoring, keepers, or availability | Product pages are quick, but the usable data path, coverage, local availability, permission, and latency remain unverified | Do not add it to the draft or in-season source manifest. Fall back to Yahoo and already qualified research sources until a separate owner-authorized read-path and latency rehearsal passes. |
| Unabated paid tools, account features, API calls, uploads, exports, and betting actions | Not qualified | **Excluded** | Potential paid market-data and betting workflow | **Observed/site-stated:** pricing and API documentation are public; no plan, login, credential, upload, endpoint call, account link, or transaction was attempted on 2026-09-02 | Unknown until a separately authorized assessment confirms legal availability, terms, plan scope, data semantics, and Yahoo-safe manual use | Terms prohibit unauthorized copying and automated or equivalent collection; account, data, and transaction paths are deliberately untested | Do not subscribe, log in, upload, call the API, export, automate, or place a bet. A separate owner-authorized audit must pass before reconsideration. |
| Reddit `r/fantasyfootball` interactive feed, search, Index, posts, and comments | Qualified with conditions | **Fallback; broad use is preparation only** | Breaking-signal discovery, counterarguments, sentiment, and discovery of primary sources/tools | **Current:** public feed and scoped search reachable 2026-08-30; current Index and timestamps visible | Mixed standard/PPR/superflex/dynasty/keeper/etc.; every claim needs league-context filtering | Targeted read can be quick, but content is noisy, volatile, removable, and subject to login/JS challenges | Open and verify the primary linked source. Fall back to classic Reddit for visible reading or omit the claim. Never rank by votes/comments. |

## Workflow dependencies and controls

Yahoo sign-in, two-step verification, Account Key, passkeys, risk verification, and browser-managed session persistence are operational dependencies, not pick-selection tools. Their documentation was reviewed in the existing [login guide](yahoo-login.md), but this evaluation did not repeat password, MFA, recovery, CAPTCHA, passkey, Account Key, or expired-session flows.

The current browser profile restored a signed-in Yahoo session on 2026-08-30. That proves only today's access path. Before the real draft, reopen Yahoo in the same profile early enough for the manager to complete any interactive verification. Never export or repair the session with a cookie file. If authentication fails near or during the draft, the manager owns the browser while the assistant preserves the prepared board and state record.

## Activation decision

Freeze this set when the Yahoo room opens:

1. **Yahoo live surfaces — active primary.** They are the only authority for the clock, player availability, pick acceptance, roster state, and completion.
2. **Boris Chen standard tiers — active secondary only if refreshed and cached.** Use for fast tier/value checks; remove keepers and drafted players locally.
3. **NBC live player news — fallback only.** Open only for a narrow current injury/role question between opponent picks; broad editorial review belongs before the room.
4. **Reddit — fallback only.** Use to discover a possibly breaking fact or counterargument, then verify the underlying primary source. Do not browse discussion while on the clock.
5. **FFToday — prepared cross-check.** Carry a cached non-PPR shortlist into the room. Avoid multi-page manual research during the live loop.
6. **Sleeper — preparation only.** Its official API can support the pre-draft identity/trend pass, but its board remains degraded until a league-matched signed-in rehearsal succeeds.
7. **DraftKick manual, Yahoo mocks, FFToday secondary tools, and the NBC PDF — preparation only.** Their useful outputs must be distilled into the board before room-open.
8. **RotoBaller public pages — fallback only.** Prepare the standard/non-PPR board before room-open; consult player news narrowly between opponent picks and corroborate material changes. Its mock, sync, and premium capabilities remain excluded.
9. **FantasyPros public pages — preparation or targeted fallback only.** Use a current standard page only for a named disagreement, freshness check, or news question; do not count ECR independently from Boris Chen, and keep paid/account features excluded.
10. **Unabated — excluded.** Its public product documentation does not prove a permitted, usable market-data path. Do not consult, subscribe, log in, upload, call an API, export, or transact without a separate authorized qualification.
11. **DraftKick Live sync — excluded.** Do not install, enable, or rely on it on draft day without a separate successful rehearsal.

No source discovered or merged after room-open is eligible for the live set during that draft.

## Cross-source controls

- Match players by normalized name plus position and reconcile NFL team changes. Name-only joins are not sufficient.
- Remove every Yahoo keeper and drafted player before applying any external rank. TreVeyon Henderson is already committed to the team's seventh-round slot.
- Yahoo rank or ADP selected inside DraftKick is not independent confirmation of Yahoo's own rank or ADP.
- DraftKick's composite can include FFToday and other feeds. Do not count the composite and its included source as two votes.
- Sleeper ADP or trend data can already appear in DraftKick, FFToday composites, or another comparison tool. Count the upstream Sleeper signal once.
- Boris Chen is derived from FantasyPros expert consensus. Another FantasyPros presentation is the same upstream family unless proven otherwise.
- FantasyPros ECR, its expert count, and its rank dispersion do not make a second independent vote alongside Boris Chen. Direct FantasyPros ADP is a timing signal with its own market semantics, not proof of value or Yahoo availability.
- RotoBaller's methodology and upstream dependencies are not yet documented. Treat it as a separate editorial view, not independent corroboration, until they are.
- Reddit submissions and NBC/FFToday links count as one underlying source, not separate corroboration.
- Do not average PPR ranks, non-PPR ranks, projected points, ADP, tiers, VORP, and community votes. Preserve their different semantics and use the workflow's candidate-ordering rules.
- Do not average Unabated odds, implied probabilities, player props, futures, market-based projections, or simulations with fantasy rankings or points. A market movement is an investigation trigger only after identity, statistic, period, timestamp, and causative news are verified.
- A current direct injury/transaction source can override an older ranking assumption, but it does not by itself determine player value.

## Readiness gaps and next tests

| Priority | Gap | Required test or decision |
| ---: | --- | --- |
| 1 | No final freshness thresholds | Before the real draft, set maximum ages for news, ranks/projections, tiers, and ADP. Any material later news invalidates an older otherwise-passing artifact. |
| 2 | DraftKick Live unverified | In a non-consequential mock, review extension permissions, connect the intended room, compare every synced pick with Yahoo, simulate disconnect/reconnect, and prove a safe manual fallback. |
| 3 | Sleeper mock board not qualified | Complete a signed-in eight-team, position-1, 16-round rehearsal with the keeper at pick 49; record scoring mismatches, timer/CPU behavior, saved recovery, and latency. |
| 4 | DraftKick configuration not frozen | Enter the exact league settings/keepers/order, verify Board and Rosters, record build/projection timestamps, and decide how state will survive or be reconstructed. |
| 5 | No consolidated player identity map | Build a minimal ephemeral map of Yahoo player identity to normalized name, NFL team, and position; reject ambiguous rows. Do not commit restricted ranking datasets. |
| 6 | Current news authority not fixed | Choose the primary team/NFL news path used to verify NBC/Reddit discoveries and define the evidence recorded for a breaking change. |
| 7 | Live latency not rehearsed with the full source set | Run an eight-team position-1 mock using the frozen manifest and measure whether Yahoo monitoring remains continuous. Demote any tool that delays the queue or pick. |
| 8 | RotoBaller premium and live-assistant paths unverified | In a non-consequential owner-authorized rehearsal, verify account requirements, exact-league configuration, pick parity, persistence, disconnect recovery, permissions, and safe disablement. Do not enable it for the real draft without a passing test. |
| 9 | FantasyPros paid/account workflow unverified | Keep excluded until an owner-authorized, non-consequential Yahoo rehearsal verifies plan scope, access, permissions, custom scoring, keeper handling, pick parity, recovery, safe disablement, and no unwanted submission. |
| 10 | Unabated permitted data path and market semantics unverified | Keep excluded until an owner-authorized audit verifies legal availability, plan/terms, one permitted player-prop and futures read path, timestamp/coverage, fantasy-semantic controls, Yahoo-safe latency, and a manual no-transaction fallback. |

## Per-surface evidence

- [Yahoo Fantasy Sports](https://sports.yahoo.com/fantasy/) and the signed-in league surfaces documented in the [Yahoo notes](yahoo-football-navigation.md)
- [DraftKick Football](https://app.draftkick.com/football) and the [merged guide](draftkick-football.md)
- [Sleeper mock drafts](https://sleeper.com/mockdraft), the [official API](https://docs.sleeper.com/), and the [merged Sleeper guide](sleeper.md)
- [FFToday Rankings & Projections](https://www.fftoday.com/rankings/) and the [merged guide](fftoday.md)
- [Boris Chen Draft Sheets](https://www.borischen.co/p/draft-sheets.html) and the [merged tier guide](boris-chen-draft-tiers.md)
- [Rotoworld Draft Central](https://www.nbcsports.com/fantasy/football/news/rotoworld-fantasy-football-draft-central-2026-rankings-strategy-sleepers-and-more) and the [merged NBC guide](nbc-sports-fantasy.md)
- [RotoBaller NFL Fantasy Football](https://www.rotoballer.com/) and the [merged RotoBaller guide](rotoballer.md)
- [FantasyPros standard rankings](https://www.fantasypros.com/nfl/rankings/?scoring=STD), [public tools](https://www.fantasypros.com/fantasy-football-tools/), and the [merged FantasyPros guide](fantasypros.md)
- [Unabated NFL tools](https://www.unabated.com/sports/nfl), [Prop Odds Screen](https://www.unabated.com/tools/core/props-tool), and the [merged Unabated guide](unabated.md)
- [Reddit r/fantasyfootball](https://www.reddit.com/r/fantasyfootball/) and the [merged Reddit guide](reddit-fantasyfootball.md)
- [Source catalog](../sources.md) for access, methodology, terms, and support references

## Final verdict

The inventory is usable for draft preparation now, but only Yahoo and a refreshed cached Boris tier sheet qualify for the default live set. FFToday, DraftKick, and Sleeper should shape the prepared board; NBC, Reddit, and narrowly targeted FantasyPros public pages can answer specific news or context questions; Yahoo/Sleeper mocks and the NBC PDF are rehearsal/offline aids. FantasyPros paid/account features and all Unabated market-data, account, API, export, and betting paths are excluded pending separately authorized qualification. DraftKick Live synchronization is also excluded pending a controlled mock validation. Sleeper's board is degraded rather than excluded because its public capabilities and API are verified, but the league-matched signed-in rehearsal is still missing.
