# Model and Reasoning Usage Audit Findings

- Logged at: `2026-09-02T05:44:41Z`
- Reporting window: `2026-09-01T04:55:33Z` through `2026-09-02T04:55:33Z`
- Source task: `01a05e11-d690-7682-ab9c-2cd567f42ddd`
- Azure Boards tracking: `AB#3411`
- Source artifact: `codex-usage-last-two-days.json`

## Findings

The original headline was not a true rolling 24-hour total. After filtering records to the exact 24 hours ending at report generation, the corrected totals are:

- 665 turns
- 9,354 model calls
- 1.119B tokens
- 12,577.78 estimated credits

The original 1.374B-token headline was 18.6% higher because it included records preceding the rolling cutoff.

| Model | Turns | Tokens | Share |
|---|---:|---:|---:|
| Sol | 333 | 698.2M | 62.4% |
| Terra | 305 | 393.4M | 35.2% |
| Spark | 7 | 18.1M | 1.6% |
| Luna | 17 | 7.6M | 0.7% |

### Main conclusions

1. **Routing compliance is the primary problem.** Only 19.2% of tokens and 29.2% of turns followed canonical routes.

2. **Sol is heavily misrouted.** Sol consumed 62.4% of tokens and 74.9% of priced credits. Sol-medium roots alone consumed 418.8M tokens; Sol-ultra roots consumed another 168.6M. Approximately 95% of Sol usage was noncanonical.

3. **Terra is frequently over-provisioned.** Terra-high and Terra-max roots consumed 219.3M tokens. Only about 44% of Terra tokens used canonical Terra-medium routing.

4. **Luna is genuinely underused.** All 17 Luna turns were valid Luna-low subagents, but there were too few Lite tasks and focused QA assignments routed to it.

5. **Spark's low usage is not the immediate problem.** All seven Spark turns were noncanonical subagent work. If Spark is root-only, increasing usage requires assigning suitable bounded work as separate root tasks, not spawning Spark agents.

6. **Usage is concentrated.** `asset-allocation-jobs`, `helpful-scripts`, and `asset-allocation-contracts` generated 79.2% of tokens; Jobs alone generated 45.1%.

7. **The token total is mostly repeated context.** About 97.0% of input tokens were cached. Only 33.7M were fresh input tokens, so the 1.119B headline reflects long conversations and repeated cached context more than unique information processed.

### Recommended priorities

- Enforce route validation when root tasks and subagents are created.
- Reject Sol and Spark subagents.
- Normalize Sol roots to high and Terra roots to medium.
- Route simple root work to Luna-low and require Luna-low focused QA for Standard work.
- Use Spark only for bounded root tasks supported by the routing contract.
- Fix the audit workflow so totals are calculated after exact timestamp filtering.
- Track daily canonical-token share, canonical-turn share, model share, cached-context ratio, and rejected-route attempts.

## Data-quality notes

The source report contains 29 scan warnings. Most concern old archives, but one warning near the reporting-window boundary could cause a small undercount. Estimated credit totals are incomplete because Spark and unknown-model records were unpriced.

This is a read-only usage analysis. It does not provide billing, deployment, runtime-health, or user-path evidence.
