# Meeting Booking Agent — design & implementation plan

`meeting-booking-agent-design.html` is a single self-contained HTML document
(inline CSS/JS, no external assets, light and dark themes, printable) that
designs and plans a **purely conversational Power Apps canvas app** that books
Microsoft 365 meetings **on behalf of executives** via a **Copilot Studio
orchestrator with child agents** (People & Delegation, Scheduling &
Availability, Rooms & Resources) calling Microsoft Graph through a delegated
custom connector.

## What's inside

1. Executive summary, scope, personas, assumptions
2. Platform reality check (what changed in Copilot Studio / Power Apps / Graph
   as of August 2026 and why it matters)
3. Architecture: component and sequence diagrams, chat-surface decision,
   agent topology, identity & authorization (delegated primary, application
   fallback), Graph capability matrix, conversation design, Dataverse model,
   non-functional design
4. Interface contracts between the Power App developer and the Copilot
   developer (context payload, events/cards, connector and flow signatures,
   Dataverse matrix, environment variables)
5. 8-week implementation plan with one owner per step, dependencies,
   acceptance criteria and effort; swimlane timeline; critical path
6. Ownership summary and RACI, test plan, Definition of Done and gates,
   risks, decision log, open questions, sources (verified vs unverified)

## Open it

Double-click the file or open it in any browser; it needs no server and no
network. Print to PDF from the browser for distribution.

## How it was produced

Authored with Claude Code on 2026-08-23 from three research passes against
Microsoft Learn (architecture, delivery planning, critical review) plus two
rounds of stakeholder questions. Every platform claim carries a source link or
an explicit **UNVERIFIED** badge that names the Phase 0 spike that confirms it.

## Updating

Edit the HTML directly — it is plain HTML with a token-based stylesheet in the
`<head>`. Keep it self-contained (no CDN scripts, fonts or images) and bump
the version/date in the header block.
