# Reddit r/fantasyfootball: draft-time agent guide

- Source: [r/fantasyfootball](https://www.reddit.com/r/fantasyfootball/)
- Observed: 2026-08-30 in the modern and classic desktop interfaces
- Access exercised: public reading and an existing browser-managed signed-in session
- Decision context: supplemental research for this workspace's fantasy-football draft

## Purpose and evidence boundary

`r/fantasyfootball` is a live, user-generated research stream. Its best draft-time
uses are discovering breaking news, finding discussion of a player or draft
decision, locating community-created tools, and surfacing arguments that should
be checked against primary sources. It is not a scoring-normalized projection,
ADP feed, depth chart, injury authority, or substitute for the live Yahoo draft
board.

Evidence labels used below:

- **Observed** means the control, field, or behavior was directly visible on
  2026-08-30.
- **Reddit-stated** means an official Reddit help or policy page describes it.
- **Community-stated** means the subreddit rules, wiki, sidebar, or FFBot thread
  describes it.
- **Unverified** means the surface was blocked, not exercised, or would require a
  separate approved integration.

Posts, comments, scores, flairs, links, account state, page design, and community
bookmarks are volatile. Re-read the site at draft time and carry the retrieval
time into every recommendation.

## Executive use

Use Reddit as four distinct inputs:

1. **Breaking facts to verify:** injuries, practices, transactions, depth-chart
   movement, coach/player remarks, and beat-reporter observations.
2. **Market and sentiment context:** which players, risks, and draft constructions
   are drawing fresh discussion. Vote and comment counts measure attention, not
   truth.
3. **Decision arguments:** upside/downside cases, role assumptions, roster-build
   tradeoffs, and scoring-format differences found in posts and replies.
4. **Source discovery:** rankings, models, spreadsheets, draft companions, AMAs,
   and external reporting linked by the community.

Do not copy a popular answer directly into the pick recommendation. Resolve the
underlying facts, confirm that the advice matches the league's scoring and roster
rules, and compare it with the structured sources already documented in this
workspace.

## Access and authentication

### Public reading

**Observed:** the community feed, posts, comments, wiki, rules, search, flair
links, and community bookmarks were readable without starting a new login flow.
Reddit may still show a login prompt, an advertisement, or a JavaScript challenge
on some routes. A failed page is not evidence that the community is empty.

### Cookie-backed signed-in session

**Observed:** the persistent browser session exposed signed-in controls during
this review, including the user menu, inbox, chat, create-post entry point,
voting controls, and classic Reddit's save/hide/report/crosspost actions. No
cookie value, token, account identifier, or storage database was inspected.

Use browser-managed cookies rather than a cookie file in Git:

1. Open `https://www.reddit.com/r/fantasyfootball/` in the persistent draft
   browser profile.
2. If **Log In** is shown and signed-in reading is needed, hand the browser to the
   user. The user completes credentials, MFA, security prompts, and any CAPTCHA.
3. Resume only after the normal signed-in navigation is visible. Do not record
   the username as an authentication check.
4. Reuse the same browser profile on draft day. Do not export cookies into a
   prompt, Markdown file, command line, log, fixture, or PR.
5. If the session expires, stop authenticated work and repeat the manual handoff.
   Do not attempt to reconstruct or refresh session values.

Local authentication artifacts belong only under ignored paths such as
`fantasy-draft-research/browser-profile/`, `auth/`, or `user-data/`. The local
`.gitignore` also rejects common cookie and storage-state filenames. Browser
cookies are bearer-equivalent secrets even when the site can also be read
publicly.

The draft workflow is read-only. Do not vote, save, hide, report, repost, share,
join/leave, post, comment, or message on the user's behalf. This is especially
important because the current [subreddit rules](https://www.reddit.com/r/fantasyfootball/wiki/rules_guidelines)
prohibit generative-AI posts and comments while allowing assistive use to inform
analysis.

## Main surfaces and controls

### Modern Reddit

The modern community page exposed:

- community highlights/pinned posts;
- feed sorts **Best**, **Hot**, **New**, **Top**, and **Rising**;
- Card and Compact display modes;
- community-scoped search, with a control to remove the community scope;
- clickable post flair filters;
- a **Create Post** entry point;
- community bookmarks for the wiki, rules, AMAugust, the community-created tools
  index, and classic Reddit;
- a rules summary and related communities.

The visible flairs are content-dependent, not a fixed data dictionary. Examples
observed on the review date included `Injury Report`, `Player Discussion`,
`Tools & Resources`, `Daily Thread`, `Index`, `Mod Post`, and topical novelty
flairs. Follow the flair link from a current post instead of manufacturing a
filter value.

### Classic Reddit

[Classic Reddit](https://old.reddit.com/r/fantasyfootball/) exposed a denser,
server-rendered view that is useful when an agent needs compact metadata:

- tabs for **hot**, **new**, **rising**, **controversial**, **top**, and **wiki**;
- post title, canonical post or external-link URL, author, exact timestamp,
  flair, source domain, score when visible, and comment count;
- link posts and self/text posts as distinct domains;
- next-page pagination with a Reddit `after` cursor;
- subreddit-scoped search and links to the latest Index thread;
- direct links to rules, FAQ, beginner/resource guides, and content filters.

The default modern feed showed **Best** while classic Reddit opened on **hot**.
Always state the selected view; a list from one sort is not interchangeable with
another.

### Feed-sort intent

| View | Draft-time role | Main caveat |
| --- | --- | --- |
| New | Breaking news and the latest player discussion | No quality/engagement filter; duplicates and weak claims are common |
| Rising | Early high-velocity topics | Short-lived and unstable |
| Hot / Best | Current community attention | Ranking is algorithmic and not an accuracy score |
| Top | Retrospective research and established resources; apply a time window | Older consensus can be stale after injuries, transactions, or role changes |
| Controversial (classic) | Find disputed assumptions and downside cases | Polarization is not evidence of a close projection |

The classic **Top** page defaulted to the past 24 hours and offered hour, week,
month, year, and all-time windows. Reddit's official sort help also documents
time filters for all time, year, month, week, 24 hours, and hour.

## Post and comment data available

### Post fields

The inspected feed and search results exposed:

| Field | Draft-time use |
| --- | --- |
| Title | Fast claim/topic classification; never treat it as the full evidence |
| Post URL and Reddit post ID/permalink | Stable citation and deduplication handle within Reddit |
| External URL and source domain | Follow to the underlying report or tool; distinguish self-posts from external evidence |
| Author | Attribution only; username is not a credibility guarantee |
| Submission timestamp | Freshness and event ordering; classic Reddit exposed an ISO timestamp |
| Post flair | Filter news, injuries, discussion, daily threads, tools, indexes, and moderator posts |
| Self-text or search excerpt | Reasoning, methodology, league context, and disclosed assumptions |
| Score/upvote display | Attention signal; may be hidden, approximate, or change continuously |
| Comment count | Discussion-depth signal and a route to counterarguments |

Advertisements appear in the same visual flow. Exclude promoted content before
building a candidate evidence set.

### Comment fields and controls

The inspected thread exposed author, relative or exact time, body, score when
visible, nesting depth, direct replies, user/community flair, moderator labels,
and contributor badges. The modern thread offered **Best**, **Top**, **New**,
**Controversial**, **Old**, and **Q&A** comment sorts. The daily draft-advice
thread selected **New** by default and explicitly asked readers to sort by New.

Comments are independent claims, not votes in a model. Preserve the parent/reply
relationship when summarizing a disagreement, and do not flatten a rebuttal into
support for the original claim. A badge or flair can add context, but it does not
verify player news or projections.

## Search and addressing

Use the community-scoped search bar or a URL that visibly retains the
`r/fantasyfootball` scope. Reddit's official
[search guide](https://support.reddithelp.com/hc/en-us/articles/19696541895316-Available-search-features)
documents these useful manual filters:

| Filter | Example | Use |
| --- | --- | --- |
| `author:` | `author:FFBot` | Find official bot threads |
| `flair:` | `flair:"Injury Report"` | Narrow to a current flair label |
| `self:` | `self:true` | Require text/self posts |
| `selftext:` | `selftext:"zero RB"` | Match the post body |
| `site:` | `site:team-domain.example` | Restrict external source domain |
| `subreddit:` | `subreddit:fantasyfootball` | Force community scope |
| `title:` | `title:"Christian McCaffrey"` | Require a title phrase |
| `url:` | `url:player-news` | Match the submitted URL |

Field names must touch the value (`author:FFBot`, not `author: FFBot`). Put
multi-word field values in quotes. Reddit also supports uppercase `AND`, `OR`,
and `NOT` plus grouping.

**Observed search result fields:** title, Reddit permalink, excerpt, author,
timestamp, score, comment count, flair, and matched source/body text. A scoped
search for a player with `sort=new&t=month` returned current tools, comparison
threads, camp notes, rankings, and ADP discussion in one result set.

Reddit's official
[sort documentation](https://support.reddithelp.com/hc/en-us/articles/19695706914196-What-filters-and-sorts-are-available)
defines search sorts Relevance, Hot, Top, New, and Comment Count. For live draft
research, start with New and a short time window, then inspect Top or Comment
Count for developed counterarguments. Relevance mixes word rarity, age, votes,
and comments and should not be read as evidence quality.

## Daily index and draft-specific threads

The best route for individualized questions is the current FFBot Index. The
sidebar's **Latest Index Thread** link used this query during inspection:

```text
author:FFBot Index flair:index
```

with the subreddit restricted, sorted New, and limited to the past day.

The 2026-08-30 Index linked these active thread types:

- Add/Drop;
- Dynasty, Best Ball, and Guillotine Strategy;
- Keeper;
- League, Commissioner, and Platform Issues;
- Mock Draft;
- Rate My Team;
- Trade;
- Who Do I Draft?

Thread availability changes by season. The sidebar also described an in-season
Who Do I Start? thread. Discover the current set from today's Index rather than
reusing a dated permalink.

The inspected Who Do I Draft thread required questioners to include league size,
scoring format, roster rules, all candidates, the roster, and other relevant
context. It contained nested answers and highlighted questions with fewer than
two replies. FFBot's Index and daily threads displayed helper counts and stated
that the tables refresh about every 15 minutes. Treat those counts as community
workflow metadata, not answer-quality scores.

Subreddit Rule 1 directs team- and league-specific questions into these
consolidated threads. Even if future write access is contemplated, an agent must
not create an individual roster-advice post.

## Wiki, rules, AMAs, and tool discovery

The [wiki home](https://www.reddit.com/r/fantasyfootball/wiki/index) linked:

- rules and posting guidance;
- FAQ and common fantasy questions;
- a beginner guide covering league creation, drafting, in-season management,
  and general advice;
- a resource guide and glossary;
- accuracy challenges and community games.

The modern sidebar also linked the current AMAugust program and the
[community-created draft-tools index](https://www.reddit.com/r/fantasyfootball/comments/1es4t74/a_consolidated_list_of_all_draft_tools_created_by/).
The tools post cataloged rankings-comparison sheets, value-based and auction
tools, draft boards, live-sync companions, ADP comparison tools, projection
workbooks, strength-of-schedule resources, and league-history products.

That tools post was created and last edited in August 2024 but remained a 2026
community bookmark. Use it only for discovery. Verify each tool's current season,
owner, pricing, authentication, data date, scoring support, and safety before
using it. The wiki and rules also showed stale/inconsistent descriptive text,
so a visible bookmark is not proof of current maintenance.

## Draft-time agent procedure

### Before the room opens

1. Load the authoritative Yahoo league settings, active teams, draft order,
   keepers, and available-player state from the existing notes in this folder.
2. Verify the Reddit session. Public reading is sufficient for this procedure;
   request a manual login handoff only if a required surface is unavailable.
3. Open the current Index through its search, not a dated permalink.
4. Open the main community in **New** and review the latest injury, transaction,
   practice, depth-chart, and moderator posts.
5. Search priority players by exact title/name over the past day and week. Repeat
   with an `Injury Report` or `Player Discussion` flair only when the current
   flair link confirms the spelling.
6. Open the underlying external source for every material factual claim. Record
   its publication time separately from the Reddit submission time.
7. Keep only short derived notes: player, claim, source URL, source time, Reddit
   permalink, retrieval time, scoring/league context, and confidence. Do not
   persist a bulk comment corpus.

### For each live pick

1. Refresh the Yahoo board and remove drafted/kept players before consulting
   Reddit.
2. Search only the two or three live candidates. Use **New** with a short time
   window first to catch breaking facts.
3. Read the strongest primary link and the most relevant discussion. Use Top,
   Comment Count, or a developed reply chain to find counterarguments, not to
   select the winner by popularity.
4. Separate each candidate's evidence into:
   - verified current fact;
   - projection/ranking assumption;
   - community opinion;
   - unresolved contradiction.
5. Reject advice whose league size, scoring, roster slots, keeper cost, or draft
   position does not match this league. The subreddit mixes standard, half-PPR,
   PPR, superflex, best ball, dynasty, auction, keeper, and guillotine contexts.
6. Compare the surviving evidence with Yahoo availability and the structured
   FFToday, Boris Chen, and DraftKick inputs documented in this workspace.
7. Return no more than three choices. Cite the underlying source and Reddit
   permalink when Reddit materially changed the conclusion.

Suggested response shape:

```text
1. Player — verified update at <time>; effect on role/value; league fit; main risk
2. Player — strongest alternative; where the discussion disagrees
3. Player — fallback if the first two go

Reddit check: r/fantasyfootball New/search refreshed <time>.
Evidence: <primary source URL>; discussion <Reddit permalink>.
Unresolved: <claim that could not be verified>.
```

### Contradiction handling

When Reddit posts or comments disagree:

1. Prefer the newest direct primary source for factual events.
2. Keep publication time and Reddit submission time distinct.
3. Prefer disclosed methods and league settings for analytical claims.
4. Treat score, awards, flair, and reply volume as discovery aids only.
5. State the disagreement if it changes the pick order; do not manufacture a
   consensus.
6. If a material injury, role, or availability claim remains unverified, lower
   confidence or omit the affected player rather than guessing.

## Failure and recovery playbook

| Symptom | Safe response |
| --- | --- |
| Login prompt or expired session | Continue publicly if possible; otherwise hand the browser to the user for login and MFA, then resume in the same profile |
| JavaScript challenge or blank modern feed | Do not bypass it. Try the site's visible classic-Reddit bookmark or pause for user/browser recovery |
| Modern and classic views disagree | Refresh both, compare exact URLs/sorts/times, and use the newest successfully rendered source; page presentation is not data authority |
| Search returns old/high-score posts | Switch to New and a day/week window; verify the event date in the underlying source |
| Post was removed, deleted, or link is dead | Drop it as current evidence unless another primary source independently supports the claim |
| Score is hidden or changing | Ignore it; use source quality, timestamp, methodology, and league match |
| Candidate discussion is mostly jokes or repetition | Move to the next evidence source; comment volume alone does not satisfy the research check |
| Draft clock is short | Stop broad browsing. Check only a fresh injury/role fact, then rely on the prepared structured board |

## Structured access, retention, and legal constraints

Reddit pages expose structured-looking fields and stable permalinks, but that
does not make the website a supported bulk-data API. Direct attempts to open the
subreddit JSON and RSS listing routes were blocked by the browser client during
this review, so they are **unverified and unsupported for this project**.

Do not build an unattended scraper around the signed-in browser or stored
cookies. Reddit's current [User Agreement](https://redditinc.com/policies/user-agreement)
prohibits scraping without prior written consent and restricts automated access.
The [Data API Terms](https://redditinc.com/policies/data-api-terms) require
approved access information such as OAuth identity, permit Reddit to enforce
limits, restrict retention and use, and do not grant model-training rights to
user content.

If structured ingestion becomes necessary, make it a separate reviewed task:

- register and use Reddit's supported developer access;
- use an honest, unique user agent and OAuth identity;
- honor rate, retention, deletion, attribution, and content-owner requirements;
- store only the minimum approved data for the minimum approved duration;
- keep browser session cookies out of the integration;
- obtain explicit review of the current terms before implementation.

For the current draft assistant, interactive read-only browsing and concise
source-linked synthesis are sufficient.

## Unverified or intentionally out of scope

- password, MFA, recovery, and CAPTCHA behavior;
- raw cookie export/import and cross-machine session portability;
- mobile-app-only controls;
- voting, saving, posting, commenting, messaging, moderation, or notification
  behavior;
- completeness or stability of JSON, RSS, or undocumented endpoints;
- Data API registration, OAuth scopes, quotas, schemas, and commercial terms;
- reliability, licensing, and current-season support of every linked third-party
  tool;
- any claim that subreddit popularity predicts fantasy performance.

## Evidence reviewed

- [r/fantasyfootball modern community](https://www.reddit.com/r/fantasyfootball/)
- [r/fantasyfootball classic community](https://old.reddit.com/r/fantasyfootball/)
- [Subreddit rules and guidelines](https://www.reddit.com/r/fantasyfootball/wiki/rules_guidelines)
- [Subreddit wiki](https://www.reddit.com/r/fantasyfootball/wiki/index)
- [Community-created draft-tools index](https://www.reddit.com/r/fantasyfootball/comments/1es4t74/a_consolidated_list_of_all_draft_tools_created_by/)
- Current FFBot Index and Who Do I Draft threads, inspected 2026-08-30; dated
  permalinks intentionally omitted from the operating procedure
- [Reddit Help: Available search features](https://support.reddithelp.com/hc/en-us/articles/19696541895316-Available-search-features)
- [Reddit Help: filters and sorts](https://support.reddithelp.com/hc/en-us/articles/19695706914196-What-filters-and-sorts-are-available)
- [Reddit User Agreement](https://redditinc.com/policies/user-agreement)
- [Reddit Data API Terms](https://redditinc.com/policies/data-api-terms)
