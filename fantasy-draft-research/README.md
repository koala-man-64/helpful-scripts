# Fantasy Draft Research

Workspace for traceable fantasy-football research, league-specific board compilation, and a local deterministic draft copilot.

## Research areas

- League rules and roster settings
- Player rankings and projections
- Average draft position (ADP)
- Injuries, depth charts, and role changes
- Positional scarcity and roster construction
- Draft-day value and fallback targets

## Layout

- `sources.md` — source catalog with access dates and notes
- `notes/` — league settings, strategy notes, and research summaries
- `data/` — private, gitignored raw/cache/runtime inputs and locally derived boards
- `tools/` — local scripts and applications

Implemented tooling:

- [`tools/draft-assistant/`](tools/draft-assistant/) — offline Python core for evidence validation, frozen board compilation, league-aware VBD ranking, event replay, and fail-closed Chrome pick intents for Yahoo, ESPN, and Sleeper

Current procedures:

- [`notes/draft-copilot-operator-card.md`](notes/draft-copilot-operator-card.md) — compact canonical live loop from research and freeze through explicit approval, exact queue/pick execution, and verification
- [`notes/draft-strategy-foundations.md`](notes/draft-strategy-foundations.md) — league-aware concepts, value and tier logic, roster construction, risk, snake/auction decisions, and an on-the-clock recommendation framework
- [`notes/draft-day-workflow.md`](notes/draft-day-workflow.md) — detailed Yahoo procedure and historical rationale; reference material behind the operator card
- [`notes/research-tool-readiness-2026-08-30.md`](notes/research-tool-readiness-2026-08-30.md) — dated readiness matrix, activation decision, cross-source controls, and next tests for every inventoried draft surface
- [`notes/draftkick-football.md`](notes/draftkick-football.md) — DraftKick Football functionality, data dictionary, settings inventory, and draft-time agent operating procedure
- [`notes/yahoo-login.md`](notes/yahoo-login.md) — repeatable Yahoo Fantasy browser sign-in flow
- [`notes/yahoo-football-navigation.md`](notes/yahoo-football-navigation.md) — route from Yahoo Fantasy to a specific team homepage
- [`notes/yahoo-league-scoring-and-settings.md`](notes/yahoo-league-scoring-and-settings.md) — complete league configuration and scoring snapshot
- [`notes/yahoo-2026-teams-and-draft-order.md`](notes/yahoo-2026-teams-and-draft-order.md) — active teams, snake order, and keeper-occupied picks
- [`notes/yahoo-mock-drafts.md`](notes/yahoo-mock-drafts.md) — instant and live mock-draft navigation, setup, room controls, and rehearsal workflow
- [`notes/yahoo-mock-draft-runbook.md`](notes/yahoo-mock-draft-runbook.md) — repeatable Yahoo mock-draft checklist, verification rules, recovery steps, and results-recording procedure
- [`notes/mock-draft-results/`](notes/mock-draft-results/) — completed mock results, Yahoo grades, projected standings, and lessons learned
- [`notes/real-draft-results/`](notes/real-draft-results/) — completed real-draft rosters, selection provenance, execution incidents, and durable lessons
- [`notes/fftoday.md`](notes/fftoday.md) — FFToday datasets, URL mechanics, access constraints, and draft-time agent procedure
- [`notes/boris-chen-draft-tiers.md`](notes/boris-chen-draft-tiers.md) — source-grounded guide to Boris Chen tiers, live data artifacts, draft-agent use, and constraints
- [`notes/reddit-fantasyfootball.md`](notes/reddit-fantasyfootball.md) — read-only Reddit feed, search, daily-thread, evidence-quality, authentication, and draft-time operating guide
- [`notes/nbc-sports-fantasy.md`](notes/nbc-sports-fantasy.md) — NBC Sports/Rotoworld site map, draft-kit data dictionary, freshness rules, and terms-compliant draft-time procedure
- [`notes/rotoballer.md`](notes/rotoballer.md) — RotoBaller standard/non-PPR, projection, ADP, news, rehearsal, premium-boundary, and terms-aware draft-time procedure
- [`notes/sleeper.md`](notes/sleeper.md) — Sleeper mock-draft, draftboard, official API, readiness, privacy, and draft-time operating guide
- [`notes/sleeper-login.md`](notes/sleeper-login.md) — owner-completed Sleeper Chrome sign-in, Draftboard handoff, session-reuse, and safety procedure
- [`notes/sleeper-mock-draft-runbook.md`](notes/sleeper-mock-draft-runbook.md) — owner-operated Sleeper Draftboard checklist, board verification, recovery, and results-recording procedure

## Supported workflow

The v1 copilot supports standard, half-PPR, and PPR one-QB snake redrafts on Yahoo, ESPN, and Sleeper. Configure team count, roster slots, flex eligibility, scoring overrides, draft position, and keepers before compiling a board. Auction, dynasty, best ball, superflex, and IDP remain out of scope.

Research is frozen before room-open. Chrome supplies sanitized, signed-in observations and performs only explicitly approved queue/pick actions; it never exports credentials, browser storage, tokens, private URLs, or raw DOM. Unit tests validate the local core, while each platform's write path stays disabled until its own witnessed non-consequential timed mock passes.
