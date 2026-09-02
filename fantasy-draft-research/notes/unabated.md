# Unabated NFL market-context source guide

Public Unabated pages were reviewed anonymously on 2026-09-02 at 00:45
America/Chicago. This guide records an external market-context resource for
draft preparation and in-season research. It is not an approved data feed,
fantasy projection model, account integration, betting workflow, or live-room
controller.

## Evidence vocabulary

- **Observed** means a field, route, price gate, or terms restriction was
  visible in the anonymous browser session on the review date.
- **Site-stated** means Unabated described a capability in a public product,
  marketing, pricing, or API-documentation page; it was not exercised here.
- **Inferred** means an operational interpretation follows from the displayed
  market type or label and is not a provider definition.
- **Unverified** means a login, paid plan, tool session, API credential,
  export, upload, live market read, or betting account would have been needed
  and was intentionally not attempted.

## Best use and boundary

Unabated can eventually provide market context for a narrow question about
team environment, player-stat expectations, or a material line move. It must
never replace Yahoo for league rules, availability, the clock, roster state,
or accepted picks; it must never become an automated drafting signal or a
standalone fantasy ranking source.

No usable anonymous odds, player-prop, or futures read path was exercised in
this audit. The resource is therefore documented but **excluded** from the
current active draft and in-season workflow. Its public pages are sufficient
to explain the possible data categories and the access boundary, not to
support a player decision.

## Relevant NFL surfaces

| Need | Page | What it contributes | Access and use |
| --- | --- | --- | --- |
| NFL product overview | [NFL tools](https://www.unabated.com/sports/nfl) | **Site-stated:** real-time odds, player props, futures, an NFL Futures Simulator, and ratings | Public explanatory page was visible on 2026-09-02. It does not establish access to a live market, the coverage of an individual player, or a fantasy-scoring conversion. |
| Player-prop context | [Prop Odds Screen](https://www.unabated.com/tools/core/props-tool) | **Site-stated:** multi-book props, movement, alternate lines, custom/blended projections, and a simulator | The product page is public, but no interactive odds screen, player search, line, upload, or output was used. Treat the claimed behavior as unverified for this workflow. |
| Price and account boundary | [pricing](https://tools.unabated.com/pricing) | **Observed:** the pricing page presents paid tiers that include prop screens, projections, and futures tooling | Prices and entitlements are volatile. No plan, trial, login, payment, account, or account link was created. |
| API boundary | [market-data odds documentation](https://docs.unabated.com/guide/rest-api-point-in-time-data/queries/market-data/odds) | **Site-stated:** an NFL-capable odds API with straight, futures, and props market types | Public documentation is not authorization to call the endpoint. No request, credential, websocket, schema ingestion, or export was performed. |

## Market and league controls

- Treat a sportsbook line, an implied probability, a season-long prop, a
  game total, and a fantasy-point projection as different semantic objects.
  Never average them into one rank or claim that one implies a Yahoo-specific
  value without a documented scoring conversion and its assumptions.
- A market move is an investigation trigger, not a recommendation. Validate
  player identity, statistic, period, book coverage, timestamp, and potential
  role/injury/news cause before assigning any fantasy relevance.
- Yahoo remains the authority for all league-specific facts. Market context
  cannot establish player availability, keeper status, draft order, roster
  need, waiver eligibility, or an accepted transaction.
- Do not treat multiple books inside an odds screen as multiple independent
  fantasy analysts, and do not treat a product's proprietary projection as an
  independently validated forecast.
- If a future authorized use needs a comparison, preserve only a minimal
  source-attributed note about the question, visible market type, timestamp,
  and limitation. Do not store raw odds, line histories, player tables,
  simulations, projections, or derived public recommendations in this repo.

## Future qualification procedure

A separate owner-authorized, non-consequential audit is required before any
use beyond this documentation:

1. Confirm the exact plan, legal availability, terms, and permitted personal
   use without creating a payment commitment or placing a wager.
2. Verify the anonymous or authorized read path for one NFL player prop and
   one relevant futures market; record access requirements, visible timestamp,
   market coverage, latency, and failure behavior.
3. Compare the result manually with a named Yahoo player and one independent
   source. Preserve market and fantasy semantics separately.
4. Rehearse a targeted lookup outside a live draft or deadline, then prove it
   does not interrupt Yahoo state monitoring or create an automated action.
5. Update the readiness matrix only if all access, semantic, safety, and
   latency checks pass. A successful account login or a paid plan alone is not
   qualification.

## Access, terms, and reliability constraints

Unabated's [Terms of Use](https://www.unabated.com/terms) were reviewed on
2026-09-02. They describe a personal, limited use privilege and expressly
restrict copying, reproduction, derivative works, automated or equivalent
manual collection, scraping, monitoring, and unauthorized access.

Operational rule: do not build or run a scraper, crawler, bulk exporter,
background monitor, unofficial API client, odds-data store, or derived public
player/prop page. Do not bypass account controls, price gates, rate limits,
or location restrictions. Do not sign in, subscribe, start a trial, enter
payment information, upload a projection, call an API, export data, link an
account, submit a pick, or place a bet.

The product pages use betting-oriented terminology and make performance
claims. They are provider statements, not independent proof of predictive
quality. Market access, availability, and legal eligibility can change; a
public marketing page is not evidence of current local availability or
permission to transact.

## Evidence reviewed

- [Unabated NFL tools](https://www.unabated.com/sports/nfl)
- [Prop Odds Screen](https://www.unabated.com/tools/core/props-tool)
- [pricing](https://tools.unabated.com/pricing)
- [market-data odds documentation](https://docs.unabated.com/guide/rest-api-point-in-time-data/queries/market-data/odds)
- [Terms of Use](https://www.unabated.com/terms)

## Evidence-readiness verdict

Unabated is documented as a potentially useful future market-context source,
but it is not qualified for draft or in-season decision use in the current
workflow. The audit verified public product, pricing, API-documentation, and
terms pages only; it did not verify a permitted anonymous live-market read or
any paid/account capability. Keep it excluded until the separately authorized
qualification procedure succeeds.
