# Boris Chen fantasy-football draft tiers and data

Research date: 2026-08-30. Decision context: determine how a fantasy draft agent can use Boris Chen's 2026 tiers for this workspace's non-PPR Yahoo league without mistaking them for projections, ADP, or a stable licensed API.

## 1. Executive summary

Evidence-readiness verdict: Ready for review.

Boris Chen's 2026 draft kit converts a selected FantasyPros Expert Consensus Rankings pool into tiers with a Gaussian mixture model. It publishes Top 200 and position views for standard, half-PPR, and PPR formats. The standard-scoring output matches this workspace's league, which awards no points per reception.

The best draft-agent use is as a live consensus-value layer: prefer an available player from the highest remaining tier, use roster construction and league context to break ties within a tier, and compare the result with Yahoo availability and draft position. Do not treat tier number as projected points, ADP, injury status, or custom-scoring value.

Public CSV and text files are convenient for live personal use, but they are unversioned publication artifacts rather than a documented API. Check freshness before every draft session and do not commit or redistribute the underlying rankings without permission.

## 2. Key claims with supporting evidence

### What the model represents

- **Fact — source and transformation.** The 2026 kit says it uses selected FantasyPros expert-consensus data and a Gaussian mixture model to find tiers. It says experts are chosen for demonstrated accuracy over multiple seasons. [S1]
- **Fact — clustering input.** The current source code applies `Mclust` to `Avg.Rank`; the tier assignment is therefore a clustering of consensus average rank, not a separate player-performance forecast. Best rank, worst rank, and standard deviation remain descriptive output fields but are not inputs to the cluster call. [S3]
- **Fact — upstream consensus.** FantasyPros says ECR aggregates expert rankings with rank points rather than a simple arithmetic mean, and its NFL pool is filtered using prior/current accuracy and recency rules. [S4]
- **Inference — correct interpretation.** A tier means the model found nearby consensus ranks, not that every player in it has equal expected points or identical risk. The site nevertheless recommends treating same-tier decisions as close calls. [S1, S3]

### Current draft surfaces

The 2026 site and source code expose these draft-relevant views: [S1, S2, S3]

| Surface | Standard | Half-PPR | PPR | Draft-agent role |
| --- | --- | --- | --- | --- |
| Top 200 | Yes | Yes | Yes | Cross-position draft board |
| QB | Yes | Same ranking basis | Same ranking basis | Positional scarcity and fallback groups |
| RB | Yes | Yes | Yes | Scoring-appropriate position tiers |
| WR | Yes | Yes | Yes | Scoring-appropriate position tiers |
| TE | Yes | Yes | Yes | Scoring-appropriate position tiers |
| Flex | Yes | Yes | Yes | Cross-position comparison after non-flex needs |
| K and DST | Yes | No separate variants shown | No separate variants shown | Late-round standard views |

The official Draft Sheets page links the live standard Top 200 CSV and a Google Sheet. On 2026-08-30, the public standard CSV returned this schema: [S2, S6]

```text
Rank, Player.Name, Tier, Position, Best.Rank, Worst.Rank, Avg.Rank, Std.Dev
```

The source repository publishes `weekly-<view>.csv` and `text_<view>.txt` artifacts under the `fftiers/out/` S3 prefix. Standard, PPR, and half-PPR variants use names such as `ALL`, `ALL-PPR`, `ALL-HALF-PPR`, `RB`, `RB-PPR`, and `RB-HALF`. [S3]

Useful entry points:

- Human-facing kit: `https://www.borischen.co/2026/08/2026-fantasy-football-draft-kit.html`
- Official draft-sheet page: `https://www.borischen.co/p/draft-sheets.html`
- Standard Top 200 CSV linked by that page: `https://s3-us-west-1.amazonaws.com/fftiers/out/weekly-ALL.csv`
- Standard position CSV pattern: `https://s3-us-west-1.amazonaws.com/fftiers/out/weekly-<POSITION>.csv`
- Standard position text pattern: `https://s3-us-west-1.amazonaws.com/fftiers/out/text_<POSITION>.txt`

Treat the patterns as implementation details. Only the standard Top 200 CSV is explicitly linked as a real-time data surface by the Draft Sheets page. [S2, S3]

### Freshness

- **Fact — stated intent.** The 2026 kit says charts will be updated throughout the preseason and regular season, while warning that recent news can be slow to appear because experts do not all update promptly. [S1]
- **Fact — upstream cadence.** FantasyPros says it updates rankings and cheat sheets daily, sometimes more frequently, but individual experts may update less often. [S5]
- **Fact — repository schedule.** The current repository documents pre-draft runs on Monday and Thursday at 6:00 a.m. Pacific and in-season runs Tuesday, Thursday, and Sunday; it also notes that a sleeping host can miss runs. [S3]
- **Observed snapshot.** The public standard, PPR, and half-PPR CSV responses checked on 2026-08-30 reported `Last-Modified: Fri, 28 Aug 2026 06:00:57–59 GMT`. This is observation, not a service-level guarantee. [S6]

For operational use, trust the artifact's HTTP `Last-Modified` value and visible chart timestamp over an assumed cadence.

### Draft-agent procedure for this league

This league is non-PPR, so use the standard Top 200 and standard position tiers.

1. Before entering the draft room, fetch the current standard Top 200 CSV and record its retrieval time and `Last-Modified` value.
2. Reject or clearly flag data that is not from the current 2026 kit or is materially stale relative to late injury, depth-chart, suspension, or role news.
3. Join players to Yahoo's live player pool by normalized player name plus position; never use name alone when an ambiguity exists.
4. Remove drafted players and known keepers from consideration.
5. Prefer the highest remaining tier, then apply roster needs, replacement depth, bye-week concentration, injury/news checks, and Yahoo room timing as tie-breakers.
6. Compare the tier view with Yahoo ADP or room rank. A large difference is a prompt for investigation, not an automatic pick.
7. Preserve the original `Rank`, `Tier`, `Best.Rank`, `Worst.Rank`, `Avg.Rank`, and `Std.Dev` fields in working memory so the agent can explain its recommendation.
8. If the live artifact cannot be refreshed, disclose the timestamp and fall back to a separately verified current source rather than silently using an old snapshot.

## 3. Counter-evidence and contradictions

- **Cadence mismatch.** The site uses broad language about continual updates, but the repository documents a specific two- or three-day weekly cron schedule and possible missed runs. The live object's timestamp is the decisive freshness evidence. [S1, S3, S6]
- **Upstream lag.** FantasyPros may refresh its consensus daily while individual experts remain stale. A recently generated Boris Chen file can therefore contain older expert opinions. [S1, S5]
- **No custom league model.** The available variants cover standard, half-PPR, and PPR. They do not encode this Yahoo league's yardage bonuses, roster depth, keeper costs, draft order, or opponent behavior. [S1, S3]
- **Not ADP or projections.** The pipeline clusters average expert rank. It does not model when this room will select a player or the player's projected point distribution. [S3]
- **Access is not a stability promise.** Public S3 files currently work, but the repository and site do not document an API compatibility or availability contract. Filenames, schemas, permissions, and hosting can change. [S2, S3]
- **Reuse is constrained.** The site says all data comes exclusively from FantasyPros. FantasyPros permits a single personal-use copy but otherwise restricts reproduction and distribution without permission. The `fftiers` repository did not contain a license file at reviewed commit `896c6cc`. [S1, S3, S7]

## 4. Source quality assessment

| Source | Type | Quality | Use |
| --- | --- | --- | --- |
| S1 — Boris Chen 2026 Draft Kit | Primary publisher page | High | Current offering, methodology summary, pool selection, lag warning |
| S2 — Boris Chen Draft Sheets | Primary publisher page | High | Official live CSV and sheet entry points |
| S3 — `borisachen/fftiers` at `896c6cc` | Primary source code | High | Exact clustering input, formats, filenames, pipeline, and schedule |
| S4 — FantasyPros ECR calculation | Primary upstream documentation | High | Consensus and expert-selection method |
| S5 — FantasyPros ECR cadence | Primary upstream documentation | High | Refresh and expert-staleness behavior |
| S6 — live S3 responses | Primary derived artifacts | Medium-high | Current schema and object freshness; unversioned and mutable |
| S7 — FantasyPros Terms of Use | Primary legal terms | High | Reuse constraints |

No secondary article is required for the core claims. The main uncertainty is operational stability, not source identity.

Source register:

- **S1:** [Boris Chen — 2026 Fantasy Football Draft Kit](https://www.borischen.co/2026/08/2026-fantasy-football-draft-kit.html), accessed 2026-08-30.
- **S2:** [Boris Chen — Draft Sheets](https://www.borischen.co/p/draft-sheets.html), accessed 2026-08-30.
- **S3:** [`borisachen/fftiers` at commit `896c6cc`](https://github.com/borisachen/fftiers/tree/896c6cc130360797e8e5889a97b37b6db99b80c7), accessed 2026-08-30.
- **S4:** [FantasyPros — How ECR is calculated](https://support.fantasypros.com/hc/en-us/articles/115001219327-What-is-ECR-Expert-Consensus-Rankings-and-how-do-you-calculate-it), updated 2026-07-17 and accessed 2026-08-30.
- **S5:** [FantasyPros — How often ECR is updated](https://support.fantasypros.com/hc/en-us/articles/115001268028-How-often-do-you-update-your-ECR), accessed 2026-08-30.
- **S6:** [Boris Chen standard Top 200 CSV](https://s3-us-west-1.amazonaws.com/fftiers/out/weekly-ALL.csv), response inspected 2026-08-30.
- **S7:** [FantasyPros Terms of Use](https://www.fantasypros.com/about/legal/), accessed 2026-08-30.

## 5. What is well-supported

- The tiers are derived from selected FantasyPros consensus rankings with a Gaussian mixture model.
- The implementation clusters `Avg.Rank` and publishes tiered CSV/text/chart outputs.
- The 2026 draft kit supports standard, half-PPR, and PPR Top 200 boards plus position views.
- Standard scoring is the correct variant for this workspace's current Yahoo league.
- Recent news and upstream expert staleness can lag the generated artifact.
- Public artifacts are usable for personal live decision support, subject to freshness and reuse controls.

## 6. What is still uncertain

- No uptime, schema-stability, retention, or backward-compatibility commitment was found for the public S3 artifacts.
- The exact selected expert IDs are not explained on the 2026 page; source comments contain filter history, but that is not a reliable current roster of experts.
- No explicit license was found in the reviewed `fftiers` repository, so source-code reuse rights should not be inferred.
- The site does not publish a formal maximum acceptable data age for draft-day use.

## 7. Open questions for follow-up

1. What staleness threshold should the draft agent enforce during the final 24 hours before the league draft?
2. Should the agent use Yahoo room ADP, FantasyPros ADP, or both as the independent market-timing signal alongside tiers?
3. Is written permission available for persistent storage or redistribution of Boris Chen/FantasyPros-derived rows, or should all use remain ephemeral and personal?
4. What deterministic name-mapping source should resolve suffixes, punctuation, defenses, and future duplicate player names?

## 8. Final evidence-readiness verdict

Ready for review. The methodology, current 2026 surfaces, output schema, scoring match, freshness risks, and legal constraint are supported by primary sources. The draft agent can safely use the standard live tier data as one ephemeral decision layer if it checks timestamps, joins defensively, verifies late news independently, and does not treat the artifacts as a stable or redistributable API.

Scorecard:

- Source quality: `5`
- Traceability: `5`
- Completeness: `4`
- Contradiction handling: `5`
- Decision usefulness: `5`
