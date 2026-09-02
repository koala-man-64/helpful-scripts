# FantasyPros fantasy-football source guide

Public FantasyPros surfaces were reviewed anonymously on 2026-09-02 at
00:45 America/Chicago. This guide adds FantasyPros as a terms-aware,
visible research source for the existing Yahoo non-PPR draft workflow and
for targeted in-season context. It is not an approved data feed, scraper
target, account integration, or live-room controller.

## Evidence vocabulary

- **Observed** means a field, route, or access gate was visible in the
  anonymous browser session on the review date.
- **Site-stated** means FantasyPros described a capability in a public help,
  product, or pricing page; it was not exercised here.
- **Inferred** means an operational interpretation follows from an observed
  label or field and is not a provider definition.
- **Unverified** means a login, subscription, browser extension, league link,
  export, API access, or live draft was required and was intentionally not
  attempted.

## Best use

FantasyPros is a narrow, preparation-first source for:

- public standard/non-PPR draft ECR, spread, ADP, and expert-update metadata;
- a targeted second opinion on current projections, rest-of-season rankings,
  player news, or injury/role context; and
- pre-draft mock/rehearsal discovery without treating a simulator as Yahoo
  room state.

Yahoo remains authoritative for league settings, scoring, keepers,
availability, the clock, accepted picks, and the final roster. FantasyPros
does not model this league's custom yardage bonuses, eight active teams,
keeper cost, or live Yahoo player pool unless an unverified account workflow
is separately configured and tested.

## Relevant NFL surfaces

| Need | Page | What it contributes | Access and use |
| --- | --- | --- | --- |
| Standard draft consensus | [2026 standard rankings](https://www.fantasypros.com/nfl/rankings/?scoring=STD) | **Observed:** draft rank, best/worst/average, standard deviation, ADP, `vs. ADP`, visible expert count, per-expert update labels, and page date | Public on 2026-09-02. Standard scoring is closer to the league's no-reception baseline than PPR, but still not its custom scoring, keeper, or availability model. |
| ECR methodology and cadence | [ECR calculation](https://support.fantasypros.com/hc/en-us/articles/115001219327-What-is-ECR-Expert-Consensus-Rankings-and-how-do-you-calculate-it) and [update cadence](https://support.fantasypros.com/hc/en-us/articles/115001268028-How-often-do-you-update-your-ECR) | Provider documentation for consensus and refresh interpretation | Use the visible page date and individual expert update labels at retrieval time; do not infer freshness from a future URL. |
| Research and in-season context | [Fantasy Football Tools](https://www.fantasypros.com/fantasy-football-tools/) | **Observed:** public navigation advertises player news, consensus rankings/projections, rest-of-season rankings, ADP-adjacent research, and waiver/start-sit/trade categories | A listed tool is not proof that its destination is anonymous or league-adjusted. Treat tools that request an import, upgrade, or sign-in as unavailable for this workflow. |
| Draft Assistant | [Draft Assistant help](https://support.fantasypros.com/hc/en-us/articles/115001308567-What-is-the-Draft-Assistant) | **Site-stated:** manual and synchronized assistants, Yahoo host support, queue suggestions, roster needs, and position scarcity | The provider says this feature is for paid subscriber tiers. No account, sync, extension, timer, pick parity, recovery, or Yahoo integration was exercised. |
| My Playbook and premium tools | [plans and feature matrix](https://www.fantasypros.com/premium/plans/npc/) | **Observed:** paid plans advertise league imports, lineups, waivers, trades, live sync, and API access | Pricing and feature availability are volatile. No purchase, trial, signup, import, API key, account link, or browser extension is in scope. |

## League and evidence controls

- Start from the observed standard/non-PPR view and apply verified Yahoo
  scoring, roster, active-team, and keeper facts separately.
- Preserve rankings, projections, ADP, expert dispersion, tiers, and news as
  distinct inputs. They have different meanings and update cadences.
- FantasyPros ECR is the same upstream family as Boris Chen's tier output.
  Do not count a direct FantasyPros ECR presentation as independent
  corroboration of Boris Chen; direct ADP remains a timing signal, not a
  ranking vote.
- Remove Yahoo keepers and drafted players before comparing an external list.
  Match on normalized player name, NFL team, and position; do not join by name
  alone.
- Treat player news as a fact to corroborate with Yahoo-visible status and a
  primary team or NFL source when it would change a recommendation. It is not
  an automatic rank override.
- Keep only minimal source-attributed decision notes: URL, scoring view,
  visible update/publication label, retrieval time, player identity, and the
  narrow decision it informed. Do not copy or commit raw rankings,
  projections, expert lists, or other restricted content.

## Draft-time and in-season procedure

### Before the room opens

1. Confirm Yahoo settings, active teams, order, keepers, and player pool from
   the existing Yahoo guides.
2. If a fresh independent consensus check is needed, open the standard draft
   ranking page and record the visible date and relevant expert update labels.
3. Build a short, league-adjusted candidate list; do not retain the underlying
   ranking table or substitute its ADP for Yahoo availability.
4. Use public articles, news, or projections only for a named disagreement or
   role question. Do not browse broadly during the live room.

### During the season

1. Start from the Yahoo roster, scoring, and transaction state.
2. Use a targeted FantasyPros public page to contextualize a start/sit,
   waiver, trade, or rest-of-season question only when its scoring label and
   freshness are visible.
3. Record the source and one material limitation when it changes a decision;
   verify consequential injury, transaction, or role news elsewhere.
4. If the page is stale, slow, sign-in-gated, premium-gated, or semantically
   mismatched, fall back to the prepared Yahoo, Boris Chen, FFToday, NBC, and
   RotoBaller sources already qualified for their respective roles.

## Access, terms, and reliability constraints

FantasyPros' [Terms of Use](https://www.fantasypros.com/about/legal/) were
read on 2026-09-02. The service may change its products, access requirements,
and terms without notice. This repository therefore adopts the stricter
operational rule: use visible pages only for personal, targeted research.

Do not build or run a scraper, crawler, bulk exporter, background monitor,
unofficial API client, or ranking-data store. Do not bypass ads, paywalls,
account controls, rate limits, or tracking choices. Do not reproduce,
redistribute, or publish derived rankings. Login, subscription, free-trial,
league import, live synchronization, browser-extension installation, API
access, Auto-Pilot, and any pick submission remain excluded until the owner
explicitly authorizes a separate non-consequential rehearsal.

Public pages are dynamic and can be stale, incomplete, or differently scoped
from the Yahoo league. A public label or listing is not proof that a paid tool
will work with this league. Reopen a direct current page rather than guessing
a route, and treat an unavailable page as missing evidence.

## Evidence reviewed

- [FantasyPros home](https://www.fantasypros.com/)
- [2026 standard draft rankings](https://www.fantasypros.com/nfl/rankings/?scoring=STD)
- [Fantasy Football Tools](https://www.fantasypros.com/fantasy-football-tools/)
- [Draft Assistant help](https://support.fantasypros.com/hc/en-us/articles/115001308567-What-is-the-Draft-Assistant)
- [premium plans and feature matrix](https://www.fantasypros.com/premium/plans/npc/)
- [ECR calculation](https://support.fantasypros.com/hc/en-us/articles/115001219327-What-is-ECR-Expert-Consensus-Rankings-and-how-do-you-calculate-it)
- [ECR update cadence](https://support.fantasypros.com/hc/en-us/articles/115001268028-How-often-do-you-update-your-ECR)
- [Terms of Use](https://www.fantasypros.com/about/legal/)

## Evidence-readiness verdict

FantasyPros' anonymous standard rankings and public research pages are
qualified with conditions for preparation and narrowly targeted fallback use.
They are not in the default live draft set because Boris Chen already derives
from the FantasyPros consensus family, and they do not establish current Yahoo
state. Draft Assistant, My Playbook, paid plans, league sync, browser
extensions, API access, and automated or account-connected behavior are
excluded pending a separately authorized, league-matched rehearsal.
