# TPC-27 preregistration

Written on 2026-11-03, before any ablation has run and before any graph code exists. Design: [`docs/superpowers/specs/2026-11-03-tpc-27-full-benchmark-design.md`](../docs/superpowers/specs/2026-11-03-tpc-27-full-benchmark-design.md).

Any later change to this file is listed under "Changes" below, with its date and reason, and the paper reports it. Git history shows whether a change came before or after a result.

## The claim being tested

Whether each technology improves **factual accuracy against tool figures and planted facts**. These tests don't show whether the advice is good for the user.

## Scenario result

A scenario counts as passing in a condition when it passes on at least 2 of its 3 runs. In the variation pass, it counts as passing on at least 2 of its 3 seeds.

## Comparisons

All comparisons run on `qwen2.5:14b`, on one commit, and change one factor.

| Question | Compare | Main metric | Scenarios |
| --- | --- | --- | --- |
| Does vector retrieval help? | `vector` vs `none` | Chat fact pass | Chat |
| Does the graph help alone? | `graph` vs `none` | Chat fact pass | Chat |
| Does the graph add to vector? | `hybrid` vs `vector` | Chat fact pass | Chat |
| Is it the facts or the prompt length? | `hybrid` vs `hybrid-shuffled` | Chat fact pass | Chat |
| Does the graph help the budget agent? | `KG_MODE` on vs off | Budget cause and action pass | Budget |
| Do retries help? | Retries on vs off | Budget and goal pass, read with the fire rate | Budget and goal |

## Verdict rule

For each comparison and each group, count wins (passes only with the technology) and losses (passes only without it), and leave out ties.

| Wins minus losses | Verdict |
| --- | --- |
| 2 or more | Helps |
| -1 to 1 | Does not help |
| -2 or less | Hurts |

**Retries:** each mechanism is reported on its own. A mechanism that fired on fewer than 5% of model calls is reported as "uninformative", whatever its score.

**`hybrid` vs `hybrid-shuffled`:** shuffled facts may mislead the model more than a long prompt alone. If `hybrid` wins, the paper says the facts helped compared with unreliable facts of the same length, and doesn't claim prompt length had no effect.

**Small groups:** each scenario moves a group of 12 by about 8 points, so results are reported as counts. No significance claims are made.

## Reported beside the main metric, with no verdict

Judge pass, similarity, specificity, abstention, tool choice, temporal correctness and latency.

## Scenario groups

Each chat scenario has exactly one group, in its `tags`:

- **`temporal`:** the question names a past date or asks about a change over time, using data older than the default window.
- **`graph_suited`:** not temporal, and the answer depends on facts the graph holds: concepts and rules from the guidance, or recurring charges.
- **`graph_unsuited`:** not temporal, and the answer depends on data the graph doesn't hold, such as balances, debts and forecasts, or on a recent-window total.

The groups for the existing 22 chat scenarios were set from this rule, before the graph existed:

| Group | Scenarios |
| --- | --- |
| `temporal` | `abstain-balance-2019`, `abstain-shop-never-visited`, `subs-price-rose` |
| `graph_suited` | `abstain-roth-2030`, `kb-emergency-fund`, `kb-roth-vs-401k`, `recurring-subscriptions`, `subs-several-listed`, `subs-unused-cancel-savings` |
| `graph_unsuited` | `abstain-credit-score`, `cash-before-payday`, `cash-no-income`, `debt-avalanche-vs-snowball`, `debt-higher-apr-smaller-balance`, `debt-highest-apr`, `debt-none`, `debt-plan-extra-200`, `income-last-30-days`, `mixed-card-vs-emergency`, `savings-balance`, `spending-food-30d`, `spending-refund-excluded` |

All new `temporal-*` scenarios are in the `temporal` group. There are 12, and each records its author in an `author` field. Claude wrote all 12 from the design and the guidance files, before any TPC-36 to TPC-39 code existed. Claude is also building the graph, so the set isn't independent of the builder, and the paper says so.

## Tool choice

`expect.tools` lists tools that must be called, `expect.allowed_tools` lists tools that may be called, and `expect.tools_none` means any call fails. `get_history` is listed before it exists, because the rules were set before the graph.

## Variation

The 3 temperature-0 runs are reported as a repeatability check. Variation comes from one pass per seed at temperature 0.7, with seeds 1, 2 and 3, at the baseline settings on `qwen2.5:14b`.

## Budget improvement

Budget error (`mape()`) per closed period is reported for 3 users over 3 periods, with one pass per model, beside the no-AI baseline. The agent is credited only with the difference between them. Accept decisions follow [`acceptance/rubric.md`](acceptance/rubric.md). This metric has no repeatability check.

## Hand-read sample

The author reads 30 answers, chosen with a fixed seed across conditions and groups, with conditions hidden. The paper reports how often the author and the judge agree.

## A model that fails badly

It stays in the tables with its results. If a pass takes more than 3 times as long as `qwen2.5:14b`, that model stops after that pass, and the paper says so.

## Changes

None yet.
