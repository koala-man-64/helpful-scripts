# Waiver-wire pickup evaluation workflow

Status: living playbook

Owner: Autodraft All Stars manager and the active Codex fantasy assistant

Last updated: 2026-08-31

Next required review: before every waiver run and after any commissioner setting change

Use this procedure to turn the live free-agent pool into an ordered, explicitly authorized claim plan. Keep player names, injuries, projections, and weekly recommendations in dated decision records; keep only durable process here.

## Operating contract

- Yahoo's live league, team, Players, and transaction pages are authoritative for rules, roster state, availability, waiver timing, claim results, and priority or budget.
- Treat the saved [league settings](yahoo-league-scoring-and-settings.md) as a reference snapshot, not current state. Reconfirm the active-team count, because `Maximum teams` is a capacity setting rather than proof of how many teams are active.
- Use outside rankings, projections, news, and add/drop trends only as timestamped decision support. Record scoring or roster mismatches and do not count several presentations of the same upstream data as independent confirmation.
- Recommend by default. Submit an add, drop, bid, or priority claim only when the manager authorizes that exact transaction or an exact ordered claim set.
- Treat **no move** as a valid outcome. Unlimited acquisitions do not make roster churn, waiver priority, or a dropped player's option value free.
- Never store credentials, cookies, private league or team identifiers, transaction URLs, or other participants' identities in this repository.

## 1. Freeze the live decision state

Open Yahoo immediately before evaluating candidates and record these inputs:

| Input | Required state | Freshness rule |
| --- | --- | --- |
| Decision window | Current fantasy week, first relevant game lock, waiver-processing time, and any post-waiver free-agent window | Read from the current Yahoo week in the same session. |
| Acquisition mechanism | Rolling priority, reverse order, FAAB, or free agents; current priority or remaining budget; waiver period; pending claims | Recheck before finalizing claim order. Do not infer it from an old note. |
| League shape | Active teams, scoring, starters, flex eligibility, bench, IR, position limits, acquisition limits, and playoff weeks | Refresh after a commissioner change and at the start of each season. |
| Team state | Current roster, legal lineup, injured/bye players, open slots, IR eligibility, and every pending add/drop | Re-read after any transaction or lineup change. |
| Candidate state | Exact player, NFL team, Yahoo position, game status, availability, waiver/free-agent label, and lock time | Refresh immediately before authorization and again before submission. |
| Decision support | Source, scoring format, update time, role or injury claim, projection or tier, and known disagreement | Mark undated or stale evidence as `unknown`; never silently treat it as current. |

Stop the run if the live league cannot be identified, the acquisition mechanism is unclear, or Yahoo state is too stale to prove that a proposed transaction is legal.

## 2. Define the roster problem and the no-move baseline

State one primary objective before searching:

- replace an unavailable starter this week;
- cover a bye or short-term injury;
- stream a matchup-dependent position;
- add a credible rest-of-season starter;
- acquire contingent upside before a role change becomes fully priced; or
- block a specific roster failure without sacrificing a more valuable bench option.

Record the exact lineup or bench slot affected, the decision horizon, and what happens if no transaction is made. Name the likely drop candidate now. If no acceptable drop exists, the default decision is **pass** unless an open roster slot or legal IR move changes the comparison.

Keep two baselines separate:

- **No-move baseline:** the best legal lineup and roster outcome if no transaction is made. Use it to measure the immediate weekly gain from acting.
- **Waiver replacement baseline:** the next viable player or role expected to remain obtainable for the affected starter, flex, or bench use after the current claim window. Build it from the live free-agent pool, active-team demand, lineup and flex requirements, pending claims, and remaining same-role alternatives. Use it to measure sustainable rest-of-season gain and scarcity.

Recompute the waiver replacement baseline from the current league. Do not import a public 10- or 12-team replacement rank unchanged, and do not treat the no-move baseline as proof that the candidate has durable value above replacement.

Preserve optionality deliberately. Do not drop the only backup to a fragile starter, a justified IR stash, or a high-leverage contingent player for an interchangeable one-week projection gain without recording why the immediate gain is worth the lost upside.

## 3. Build a small, legal candidate set

Start from Yahoo-visible availability and keep the first comparison set to roughly three to six players. Expand it only when the initial set has no legal or material improvement.

Exclude a candidate when any of these gates fails:

- exact identity, NFL team, or Yahoo position is ambiguous;
- the player is rostered, locked, ineligible for the intended slot, or otherwise unavailable;
- a material injury, suspension, depth-chart, or transaction question is unresolved and the deadline does not permit a later check;
- the case depends on PPR scoring, dynasty value, best-ball value, or another format mismatch without a documented adjustment;
- there is no plausible path to improving a starter, flex, bench option, or future transaction; or
- the required drop is worse than the candidate under the same time horizon.

For each survivor, name the opportunity trigger: injury replacement, promotion, sustained snap/route/touch growth, red-zone work, schedule stream, or contingent upside. A single spike week without a changed role is not a trigger by itself.

Classify each candidate as one of:

- `streamer` — matchup-driven, normally replaceable after the week;
- `short-term bridge` — useful while a role or teammate absence lasts;
- `rest-of-season starter` — likely to improve a recurring lineup slot;
- `contingent upside` — bench value depends on a plausible future role change.

## 4. Assemble an evidence packet

Separate observed facts from forecasts and recommendations.

| Signal | Record | Use |
| --- | --- | --- |
| Yahoo state | Availability, roster percentage when visible, position, game status, lock state, and transaction type | Eligibility and live state authority, not proof of value. |
| Role and opportunity | Snaps, routes, carries, targets, red-zone work, depth-chart position, and the event that changed the role | Establish whether usage is real, durable, and relevant in non-PPR scoring. |
| Health and availability | Yahoo status plus current official team/NFL reporting when material | Override an older projection or rank when the facts conflict. |
| Projections or tiers | Value, scoring format, update time, methodology or upstream family, and material cross-source disagreement | Estimate this-week and rest-of-season outcomes without false source independence. |
| Schedule | Opponent, game environment, bye, and playoff schedule when relevant | Use mainly for streamers and close comparisons; role comes first. |
| Market pressure | Add/drop trend, roster percentage, positional need across anonymous opponents, and remaining alternatives | Estimate claim risk, never player quality by itself. |

Classify claim pressure as `low`, `medium`, `high`, or `unknown`, and record confidence separately:

- **Low:** ample same-tier alternatives remain or few managers ahead have a plausible positional need.
- **Medium:** at least one manager ahead has a plausible need and same-tier depth is limited.
- **High:** several managers ahead can use the role, few same-tier alternatives remain, and acquisition timing makes a loss likely.
- **Unknown:** priority, opponent need, timing, or alternative depth is stale, incomplete, or contradictory.

Base pressure only on mechanism-appropriate live state: the current waiver order or remaining FAAB, Yahoo-visible roster percentage and add/drop trends, anonymous roster needs, remaining same-tier alternatives, and acquisition timing. Treat trend signals as attention rather than proof of hidden claim intent. Pressure changes the chance a comparable player survives; it never creates player value or overrides a clear net add/drop edge. Reduce confidence when any input is stale or inferred.

If sources disagree, preserve the disagreement and determine whether it comes from freshness, scoring, role assumptions, or shared upstream data. Do not average incompatible inputs into artificial precision.

## 5. Compare the pickup with the exact drop

Use this conceptual test:

```text
net pickup value = lineup or bench gain + option value
                   - drop cost - acquisition cost - uncertainty
```

Do not invent numeric precision. Compare candidates categorically and record the evidence behind each judgment.

| Criterion | Question |
| --- | --- |
| This-week gain | Does the player materially improve the best legal lineup over the no-move baseline? |
| Rest-of-season gain | Is the projected role above this league's waiver replacement level, and can it persist? |
| Role confidence | Is the opportunity `confirmed`, `supported`, `speculative`, or `unknown`? |
| Scarcity | How many acceptable alternatives remain at the position, given the live active-team count and lineup requirements? |
| Drop cost | What current production, future role, bye coverage, handcuff value, or trade value is surrendered? |
| Acquisition cost | Is the transaction spending scarce priority, budget, roster flexibility, or a free-agent opportunity? |
| Risk exposure | What happens if the role, availability, projection, correlation, or opportunity-cost assumption is wrong? |

Require a clearer advantage for speculative churn in a shallow active league, where replacement-level starters are more likely to remain available. Yardage bonuses may break a close non-PPR tie, but they should not erase a material role or tier gap.

Label each material risk separately: `role` for usage or workload, `availability` for health or suspension, `projection` for range or source uncertainty, `correlation` for assumptions that can fail together, or `opportunity` for consuming roster or claim capital that blocks a higher-value path. Correlation includes several roster decisions depending on the same fragile offense, quarterback, game script, or workload assumption. Treat avoidable concentration as a tie-breaker or uncertainty adjustment, not a blanket diversification rule and not a reason to reject a clear net gain.

For each candidate, name the current option tier and the next acceptable same-role alternative. Translate the draft's tier-drop timing rule into waiver terms:

```text
claim urgency = loss to the next acceptable tier
                × chance no acceptable equivalent remains when needed
```

This is a qualitative decision aid, not a forecast. Increase urgency only when the tier loss is material and current priority, claim pressure and confidence, acquisition timing, and remaining alternatives support a real no-return risk. A popular player with an interchangeable fallback can still be a low-urgency claim.

Place each add/drop pair in one decision tier:

1. **Claim now** — material lineup or rest-of-season gain, acceptable drop cost, and evidence strong enough for the acquisition cost.
2. **Fallback or free-agent target** — useful but replaceable, uncertain, or not worth the current priority/bid.
3. **Pass** — no clear net gain, illegal transaction, stale evidence, unacceptable drop, or avoidable uncertainty.

## 6. Price the acquisition mechanism

The saved league snapshot records a continual rolling list, a two-day waiver period, Game Time-Tuesday weekly waivers, and no acquisition limits. It does not show a separate FAAB field; use the rolling-priority branch only after live settings confirm that the mechanism has not changed.

For **rolling priority**:

- spend a high claim only for a credible rest-of-season starter, a durable role change, or an immediate lineup gain whose tier drop and no-return risk justify moving to the back of the list;
- preserve priority for streamers, marginal upgrades, and uncertain committee players when comparable free-agent alternatives are likely to remain;
- account for the chance that managers ahead in the order need the same position, but do not pretend to know their exact claim; and
- record the next acceptable equivalent, claim pressure and confidence, and why waiting for free agency is or is not acceptable.

For **FAAB**, only if live settings prove the league changed mechanisms:

- record remaining budget, future-week reserve, candidate tier, replacement depth, likely demand, and the exact maximum acceptable bid;
- size the cap from this team's net pickup value, tier-drop cost, pressure confidence, and alternatives, not a generic article percentage; and
- submit a lower bid or pass when the same roster outcome is likely after waivers.

For an available **free agent**, compare the roster gain with drop and lock risk, then act only after the same identity, legality, and authorization checks. A zero-priority or zero-bid add can still destroy option value through the wrong drop.

## 7. Build an ordered, independently safe claim plan

Record the plan before submission:

| Order | Add | Drop | Classification | Maximum cost | Why this order | Cancel if |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | `<player>` | `<player>` | `<type>` | `<priority or bid>` | `<material edge>` | `<state change>` |
| 2 | `<fallback>` | `<player>` | `<type>` | `<priority or bid>` | `<fallback edge>` | `<state change>` |

- Make every claim acceptable on its own.
- Do not reuse a player as the drop in a later claim if an earlier successful claim may already remove that player, unless Yahoo's displayed claim behavior proves the chain is valid.
- Give each fallback an exact drop, cost limit, role, and cancellation condition.
- Keep K/DEF streams separate from core skill-position claims unless their roster and priority effects are explicitly compared.
- Remove a claim when late news, a roster change, a successful earlier transaction, or a newly available free agent invalidates it.

## 8. Pass the pre-submission gate

Immediately before any live action, verify all of the following:

- current week, deadline, waiver/free-agent label, and processing time;
- exact player name, NFL team, Yahoo position, status, availability, and lock state;
- exact drop and the resulting roster, lineup coverage, bye coverage, and IR legality;
- current priority or budget, pending claims, and the intended order;
- material role/injury news and the freshness of every decisive source;
- the chosen add/drop still beats **no move**; and
- manager authorization names the exact transaction or exact ordered claim set.

If any check fails, stop and refresh the plan. Do not broaden a player approval into permission to drop a different player, raise a bid, or submit additional claims.

After submission, treat the outcome as **unknown** until Yahoo's roster and transaction history agree. A button click, queued row, timeout, or success-looking message alone is not proof of a completed transaction.

## 9. Verify processing and update the lineup

After waivers process or a free-agent add completes:

1. Confirm the transaction result and any Yahoo failure reason.
2. Verify the resulting roster count, exact player identity, drop, priority change or budget charge, and remaining pending claims.
3. Confirm the lineup is legal and the new player is not incorrectly started while questionable, on bye, locked, or IR-ineligible.
4. Rebuild later claims from the actual roster. Do not retry a failed transaction without refreshing live state.
5. Record the result without private league, account, or participant identifiers.

## 10. Learn from the decision, not only the box score

Review the pickup after one week and again after roughly three weeks. Compare the pre-claim hypothesis with snaps, routes, carries, targets, red-zone work, availability, fantasy output, role persistence, and the opportunity cost of the drop.

Classify misses correctly:

- **process error** — stale news, wrong identity, format mismatch, invalid order, avoidable drop, unauthorized action, or failure to verify;
- **model error** — the role, replacement baseline, demand, or uncertainty was evaluated poorly with adequate inputs; or
- **normal variance** — the evidence and decision were sound but the outcome was noisy.

Update replacement baselines, evidence thresholds, or the standard for spending priority. Do not create a permanent player blacklist from one outcome.

## Decision record template

```text
Week / decision time:
Yahoo state last refreshed:
Acquisition mechanism / processing time:
Priority or FAAB remaining:
Active teams / scoring / roster shape confirmed:
Team objective / horizon:
No-move baseline:
Waiver replacement baseline / next acceptable equivalent:

Candidate / exact Yahoo identity:
Classification:
Opportunity trigger and role evidence:
This-week gain:
Rest-of-season gain:
Current tier / loss to next acceptable tier:
Claim pressure / confidence:
Claim urgency / why now or wait:
Material risks (role / availability / projection / correlation / opportunity):
Roster-concentration justification (if material):
Exact drop and drop cost:
Acquisition cost / maximum acceptable cost:
Decision: claim now | fallback/free agent | pass
Fallbacks and cancellation conditions:
Manager authorization:

Result / Yahoo verification:
One-week review:
Three-week review:
Process, model, or variance lesson:
```

## Stop conditions and league-specific edge cases

- **Active-team mismatch:** verify the live number of active teams. Do not equate the saved maximum-team setting with current league depth.
- **IR:** the saved settings do not allow an injured waiver/free-agent player to be added directly to IR. Ensure an active roster slot exists before planning a later IR move.
- **Non-PPR:** targets and receptions inform role but earn no direct points in the saved scoring. Adjust public PPR recommendations before using them.
- **Lock and processing:** distinguish locked players, pending Game Time-Tuesday waivers, and post-processing free agents. A later fallback may become available in a different transaction state.
- **Late news:** if decisive role or health news is unresolved near lock, reduce confidence, choose a safe fallback, or pass.
- **Playoff horizon:** weigh Weeks 15-17 only after establishing a durable role and a credible path to reaching the playoffs.
- **Kicker and defense:** stream them with position-specific evidence and low acquisition cost; do not let a replaceable stream consume priority needed for a scarce skill player.

## Related evidence

- [Yahoo league scoring and settings](yahoo-league-scoring-and-settings.md)
- [Draft strategy foundations](draft-strategy-foundations.md)
- [Canonical draft-day decision logic](draft-day-workflow.md)
- [2026 real-draft execution lessons](real-draft-results/2026-08-30-yahoo-slot-1.md)
- [Yahoo football navigation](yahoo-football-navigation.md)
- [NBC Sports/Rotoworld news procedure](nbc-sports-fantasy.md)
- [Reddit discovery and verification limits](reddit-fantasyfootball.md)
- [Sleeper identity and trend-data limits](sleeper.md)
