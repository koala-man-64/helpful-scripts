# Fantasy Draft Research

Workspace for gathering draft resources, recording league-specific assumptions, and building small tools that support fantasy draft preparation.

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
- `data/` — snapshots or derived datasets used by future tooling
- `tools/` — scripts or applications added as the workflow becomes clear

Implemented tooling:

- [`tools/draft-assistant/`](tools/draft-assistant/) — offline Python CLI for validated player imports, deterministic recommendations, SQLite event replay, vetted queues, and fail-closed browser pick intents

Current procedures:

- [`notes/draft-strategy-foundations.md`](notes/draft-strategy-foundations.md) — league-aware concepts, value and tier logic, roster construction, risk, snake/auction decisions, and an on-the-clock recommendation framework
- [`notes/draft-day-workflow.md`](notes/draft-day-workflow.md) — canonical living playbook for draft-day preparation, selective tool activation, live clock monitoring, decision logic, recovery, and post-draft learning
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
- [`notes/fantasypros.md`](notes/fantasypros.md) — FantasyPros public standard-rank, ADP, projection, news, premium-boundary, and upstream-duplication controls
- [`notes/unabated.md`](notes/unabated.md) — Unabated NFL market-context audit, semantic boundaries, access gates, and terms-safe future-qualification procedure
- [`notes/sleeper.md`](notes/sleeper.md) — Sleeper mock-draft, draftboard, official API, readiness, privacy, and draft-time operating guide
- [`notes/sleeper-login.md`](notes/sleeper-login.md) — owner-completed Sleeper Chrome sign-in, Draftboard handoff, session-reuse, and safety procedure
- [`notes/sleeper-mock-draft-runbook.md`](notes/sleeper-mock-draft-runbook.md) — owner-operated Sleeper Draftboard checklist, board verification, recovery, and results-recording procedure

## Starting point

Yahoo Fantasy Sports is the first platform source. Record the sport, league scoring format, roster settings, draft date, and number of teams before comparing rankings or building draft recommendations.
