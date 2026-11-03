# TPC-27: Full Benchmark of Retrieval and Retries in the Finance Agent

*   **Author:** Del0m
*   **Status:** Proposed
*   **Date:** 2026-11-03

--------------------------------------------------------------------------------

## Overview

This work produces the benchmark results for the course paper on the local-model personal finance agent. It tests whether the retrieval methods (vector search, the temporal knowledge graph, and both together) and the retries built into the agents improve the factual accuracy of what the agents tell a user. It also compares the app's default model, `qwen2.5:14b`, with `llama3.1:8b` and `mistral`.

To get there, the known agent bugs have to be fixed, the knowledge graph has to be built, the missing metrics have to be added, and each technology needs a switch that turns it off.

This document covers the benchmark and how the parts connect. The graph's own design stays in [architecture section 6b](../../architecture.md), and this document only records what the benchmark needs from it.

## The Problem

The paper needs evidence about whether these technologies are worth having. The project can't produce that evidence today, for five reasons:

1.  **The agents have known failures.** The 2026-11-03 baseline failed 9 of 40 scenarios, tracked in TPC-45 to TPC-49. One of these, TPC-48, is answers that misstate tool figures, which affects every chat metric.
2.  **There's nothing to compare against.** The knowledge graph has no code, and no setting turns off retrieval or retries.
3.  **Some metrics are missing or can't compare models.** Specificity, budget improvement and allowed-tool scoring aren't built. Initial budget accuracy makes no model calls, so it gives the same number for every model.
4.  **The results couldn't show that a technology fails to help.** Architecture 10.2 says "on >= off; report the difference" for the budget agent and "report all three" for chat. Neither sets a threshold, and "no retrieval" would still leave the `search_kb` tool available.
5.  **Some results could look better than they are.**
    *   Three runs at temperature 0 gave identical answers on the 5 chat scenarios tested on 2026-10-27, so they may not measure reliability.
    *   Budget error could fall over time with no AI involved, because code sets the next budget and the synthetic users follow fixed patterns.
    *   The author wrote the temporal scenarios and is also building the graph, so those scenarios could favour it.

The proposed solution is to fix the bugs that distort measurement, build the graph and the switches, write the pass rules down before any ablation runs, and then run everything on one frozen version of the code.

## Goals

*   **Fix the bugs that distort measurement.** `income-last-30-days`, `debt-plan-extra-200`, `control-steady-spender`, `debt-highest-apr`, `debt-none` and `debt-higher-apr-smaller-balance` pass on `qwen2.5:14b` over 3 runs, in the order TPC-48, TPC-45, TPC-47, and no scenario that passed before starts failing.
*   **Test the graph on the failures it targets.** TPC-46 and TPC-49 are checked against the graph at the cutoff. If they still fail, the paper reports that, and they get a different fix before the final run.
*   **Build the full graph.** TPC-35 to TPC-39 meet their own "done when" lists by the end of 2026-11-06.
*   **Report every metric in architecture 10.2.** Each one has a saved result under `eval/results/`, or a written reason why it doesn't apply.
*   **Compare models.** All model-dependent metrics run on all three models, on one commit, with `gemma3:12b` as judge.
*   **Run ablations on `qwen2.5:14b`, one factor at a time:** no retrieval, vector, graph, hybrid, and retries on and off.
*   **Make it possible to find no benefit.** Before any ablation runs, a committed file states each ablation's metric, expected direction, threshold and "does not help" rule, along with the scenario groups.
*   **Report ablations by group.** Graph-suited, graph-unsuited and temporal scenarios get separate tables, with wins, losses and ties per scenario. Every case where a technology doesn't help is listed.
*   **Credit budget improvement fairly.** Budget error per period over 3 or more simulated periods is shown beside a no-AI baseline, with accept decisions that can be replayed from a saved file.
*   **Report variation honestly.** The 3 temperature-0 runs are reported as a repeatability check, and a seeded pass at a higher temperature measures variation.
*   **Meet the dates.** Switches and metrics freeze on the night of 2026-11-04, the graph at the end of 2026-11-06, and the paper is submitted about 2026-11-08, ahead of the hard deadline of 2026-11-09.

## Non-Goals

*   Claiming the advice is good for the user. The metrics check answers against tool figures and planted facts.
*   Measuring changes in user behaviour. Synthetic transactions don't react to advice, so budget improvement means budget error over periods only.
*   Estimating run-to-run variation from the temperature-0 repeats.
*   Running ablations on `llama3.1:8b` or `mistral`, or crossing retrieval modes with retries.
*   Comparing initial budget accuracy, recurring detection or forecast error across models. These make no model calls and are reported once.
*   Changing product behaviour beyond the bug fixes, the graph and the switches.
*   Changing any agent code after the final run starts. Any change means running again.

## Open Questions

*   **Proposed values awaiting the author's confirmation:** the "accuracy" wording, verdict rule, variation settings, hand-read sample size, slow-model rule and accept rubric. All are listed in items 1 and 2 of the Detailed Implementation Plan, and they must be final before any ablation runs.

## Critical User Journeys (CUJs)

### CUJ 1: Author produces the model comparison

*   **Given**: The code is frozen at one commit, Ollama has all the models pulled, and the knowledge base and graph are built.
*   **When**: The author runs the full benchmark for each of the three models.
*   **Then**: Each model has a results folder whose `config.json` records the commit and every switch setting, and the results can be compared directly because they share a commit.

### CUJ 2: Author tests whether a technology helps

*   **Given**: The pass rules are committed before any ablation result exists.
*   **When**: The author runs one condition at a time on `qwen2.5:14b`, changing one switch.
*   **Then**: The output shows wins, losses and ties per scenario for each group, and applying the written rule shows whether the technology helped. A "does not help" result is reported the same way as a positive one.

### CUJ 3: Author tests budget improvement

*   **Given**: The accept rubric and saved decisions exist, and Claude wrote the decisions without seeing golden answers.
*   **When**: A synthetic user runs through 3 or more simulated periods, once with saved decisions and once as the no-AI baseline.
*   **Then**: Budget error per period is reported for both, along with how many proposals had a saved decision. A proposal with no saved decision counts as rejected.

### CUJ 4: Author reads a sample by hand

*   **Given**: A sample fixed in advance, selected across conditions, with the condition hidden from the reader.
*   **When**: The author reads and rates each answer.
*   **Then**: The paper reports how often the author agrees with the `gemma3:12b` judge.

### CUJ 5: A reader or grader checks a result

*   **Given**: The committed results, the pass rules and the saved accept decisions.
*   **When**: They open a run's results folder.
*   **Then**: They can see which commit, model and settings produced it, what was predicted beforehand, and every accept decision the budget result relied on.

## Technical Plan (Architecture)

### How the pieces fit

```text
 committed before any ablation runs
 ┌───────────────────────────────┐   ┌───────────────────────────────┐
 │ eval/preregistration.md       │   │ eval/acceptance/rubric.md     │
 │ hypotheses, thresholds, groups│   │ accept rules, written first   │
 └───────────────┬───────────────┘   └───────────────┬───────────────┘
                 │                                   │ Claude applies it, no golden answers
                 │                                   ▼
                 │                   ┌───────────────────────────────┐
                 │                   │ eval/acceptance/decisions.json│
                 │                   └───────────────┬───────────────┘
                 │                                   │ replayed
 ┌───────────────┼───────────────────────────────────┼───────────────┐
 │ eval/run_benchmark.py: runs each condition on one commit          │
 │   ├─ run_eval.py: chat, budget, goal (--retrieval --kg --retries  │
 │   │                --temperature --seed --today)                  │
 │   └─ budget_improvement.py: multi-period simulation               │
 └───────────────┬───────────────────────────────────────────────────┘
                 ▼
 eval/results/<date>/<condition>/  config.json, one file per run, summary.json
                 ▼
 eval/compare.py: wins, losses and ties per scenario and group, checked against the
                  preregistered rules. Its tables feed TPC-28.
```

### Order of work

1.  **Write the rules first.** Commit the preregistration, the accept rubric and the temporal scenarios before any graph code. Scenario groups can't change once results exist.
2.  **Fix TPC-48, TPC-45, TPC-47.** Re-run the baseline on `qwen2.5:14b`, and keep the 2026-11-03 results labelled as pre-fix.
3.  **Add the switches and metrics, and freeze them on the night of 2026-11-04.** Then run the first ablations.
4.  **Build the graph, TPC-35 to TPC-39, by the end of 2026-11-06.** Its design stays in architecture 6b.
5.  **Check TPC-46 and TPC-49 against the graph.** If either still fails, record that result and fix it another way.
6.  **Freeze the code and run everything.** All reported numbers come from this one commit.

**Earlier ablation results are a fallback.** Any fix for TPC-46 or TPC-49 changes prompts, and that makes earlier results out of date even if the graph-off test passes. So the first ablations check that the switches work and give the paper numbers if the graph misses its cutoff. The final run repeats them.

### Switches

Each switch is a setting in `backend/app/config.py`. `KG_MODE` and `KG_CHAT` are already specified that way, and a `run_eval.py` flag sets each one for the run. Every setting goes in `config.json`.

| Condition | App retrieval | `search_kb` | Graph facts | `get_history` | Prompt |
| --- | --- | --- | --- | --- | --- |
| `none` | off | removed | off | removed | Guidance and `search_kb` sentences removed |
| `vector` | on | available | off | removed | Today's prompt |
| `graph` | off | removed | on | available | `search_kb` sentence removed |
| `hybrid` | on | available | on | available | Today's prompt plus graph facts |
| `hybrid-shuffled` | on | available | Shuffled, same length | available | Same as `hybrid` |

*   **Tool and prompt differences:** without them, the model could still reach the retrieval method being removed. `none` and `graph` therefore change the prompt too, and the paper states this.
*   **Budget agent:** `KG_MODE` off and on is its only retrieval comparison. It never uses the knowledge base.
*   **Goal agent:** no retrieval switch applies.
*   **`sources` check:** this is left out of `none` and `graph`, where no vector sources exist by design.

**Retries** are one switch, `RETRIES_ENABLED`, covering two mechanisms that are counted separately:

| Mechanism | Where | On | Off | Can the answer change? |
| --- | --- | --- | --- | --- |
| Resend after a bad reply | `invoke_structured` in `budget_llm.py` | 1 retry | 0 retries | Unlikely at temperature 0, because the request is identical |
| Correct and ask again | `MAX_GOAL_CALLS` in `goal_graph.py` | 2 calls | 1 call | Yes, because the correction changes the prompt |

Chat has no retries. `MAX_TOOL_ROUNDS` limits a tool loop and stays unchanged. Each run records how often each mechanism fired. If one rarely fired, the paper says the test was uninformative, not that retries don't help.

### What runs

The baseline is `hybrid`, `KG_MODE` on, retries on, at temperature 0, with a pinned simulated date. Each row changes one factor. Estimates scale from the 14-minute baseline (about 11 minutes of agent time, the rest judging and setup) and assume about 56 scenarios: 34 chat, 16 budget and 6 goal.

| Experiment | Conditions | Scenarios | Runs | Estimate |
| --- | --- | --- | --- | --- |
| Model comparison | 3 models at baseline | All | 3 each | About 3 h at `qwen2.5:14b` speed |
| Chat retrieval | `none`, `vector`, `graph`, `hybrid-shuffled` on `qwen2.5:14b` | Chat | 3 each | About 2 h 40 min |
| Budget graph | `KG_MODE` off on `qwen2.5:14b` | Budget | 3 | About 20 min |
| Retries | Off on `qwen2.5:14b` | Budget and goal | 3 | About 20 min |
| Variation | Higher temperature, 3 seeds on `qwen2.5:14b` | All | 1 per seed | About 1 h |
| Budget improvement | 3 models, plus the no-AI baseline | 3 multi-period users | 1 per model | About 2 to 4.5 h |

That's about 7 hours before budget improvement, and about 11 to 16 hours once budget improvement and its collection pass are included. Nothing has been timed on `llama3.1:8b` or `mistral`, so the pilot times them before the final run.

**Run order.** The collection pass goes first, because Claude's decisions have to exist before the rest can run unattended. Started the evening of 2026-11-06, the final run ends around midday on 2026-11-07, or later if the other two models are slower. That leaves about a day for one re-run before the 2026-11-08 submission.

### Budget improvement

**Why the model's effect is narrow.** The next budget depends only on actual spending and accepted proposals (`close_period`). Without acceptances, the result comes entirely from code. So the no-AI baseline runs in seconds, and a model can only change the result by proposing `adjust_next_budget` for something that gets accepted.

**Metric.** Budget error (`mape()`) per closed period, per user. The agent condition is reported beside the no-AI baseline, and the paper credits the agent only with the difference between them.

**Users.** Today's profiles stop on 2026-08-31, which leaves too few periods. New profiles extend the generated patterns by at least three more months. They include a user with lasting higher spending, because that's the case a proposal should help.

**Running it.** `advance_days` already calls `on_day` after each simulated day. The runner uses that to look up each new proposal in the saved decisions and set its status before the month closes, when pending proposals expire. `suggest_cancel` isn't simulated, because the generated charges wouldn't stop after a cancellation.

**Cost.** A flagged day took 8 to 20 seconds in the baseline, so about 15 to 30 minutes per user for 3 periods if a category stays flagged. This is an estimate and the pilot needs to measure it.

**Run size (author's decision).** 3 users over 3 periods, with one pass per model, which adds about 2 to 4.5 hours. This metric therefore has no repeatability check, and the paper states that.

### Accept decisions

Each decision is stored under a key made of the user, simulated date, category, action and proposed amount, because alert IDs change every run. Each entry also records the decision, a one-line reason, the rubric version and the model that decided. Claude sees the proposal and its facts, but not golden answers, the agent model or the condition. Claude never runs during a benchmark. A proposal with no saved decision counts as rejected, and the results report how many proposals that affected.

**Collection (author's decision).** Each model gets one pass on the frozen code with nothing accepted, and Claude decides on the proposals it collected. The final pass then replays those decisions. This adds about 2 to 4.5 hours.

Accepting one proposal changes the next period's budget, which can produce proposals nobody saw during collection. Those count as rejected, which could understate the agent. The results report how many there were per model, and the paper states this limit.

### Results and comparison

*   **Folders:** `results_dir` gains a condition in the name, such as `2026-11-06/qwen2.5_14b/chat-none`, so a condition can't be mistaken for a repeat.
*   **`config.json`:** adds every switch, the seed, temperature, pinned date, Ollama version and a hash of the scenarios. `run_benchmark.py` refuses to start with uncommitted changes.
*   **`compare.py`:** compares two conditions on the same commit, scenario by scenario, and applies the preregistered rule. It reports counts as well as percentages, because one scenario moves a 12-scenario group by about 8 points.
*   **Judging (author's decision):** answers are scored during the run, as they are today, with the same judge and embeddings for every condition. Specificity and any other scoring rule must therefore be final before the freeze, because rescoring means running the agents again.
*   **Latency:** the agent and judge take about 17 GB of 24 GB, so Ollama may swap them and add reload time to reported latency. The pilot checks for this. If it happens, the paper reports latency with that caveat, and nothing else runs on the machine during the benchmark.

### New metrics

*   **Specificity:** a new `specific` score in the existing judge call, which adds no extra model call. It asks whether the advice names a concrete product or action.
*   **Tool choice:** `expect` gains `allowed_tools` and `tools_none`, both optional so the current scenarios still load. A run is marked on missing tools, unlisted tools and calls where none fits. Argument checks follow the same pattern.
*   **Temporal correctness:** the right year or date range appears, and a wrong one doesn't, as specified in TPC-40.
*   **Reliability:** pass^3 is still reported for all runs, labelled as repeatability at temperature 0. The seeded pass is the measure of variation.

### Scenario groups and validity

*   **Groups:** `graph_suited`, `graph_unsuited` and `temporal`, set by a written rule in the preregistration. The existing 22 chat scenarios are grouped before the graph exists.
*   **Who wrote what:** each temporal scenario records its author. About half are written by Claude from the graph's schema description alone, without seeing graph code.
*   **Longer prompts:** graph facts make the prompt longer, so a test confirms that `LLM_NUM_CTX` (8192) doesn't cut off the longest one. A cut-off prompt would make retrieval look worse than it is.
*   **Prompt-length control (author's decision):** `hybrid-shuffled` uses the same graph facts with their values shuffled between facts, so the prompt length stays the same but the facts stop being reliable. Shuffled facts may mislead the model more than a long prompt alone, so the paper describes this condition as a control for both length and misleading content. The shuffle uses a fixed seed, and prompt length is recorded for every condition.

## Alternatives Considered

### Alternative A: Cross every model, condition and run

*   **Pros**: Shows whether a technology helps one model more than another.
*   **Cons**: About 15 to 25 hours by the reviewers' estimate, before budget improvement. Retries don't apply to chat, so much of the grid would change nothing.
*   **Reason for Rejection**: Rejected by author: it doesn't fit the schedule. Ablations run on `qwen2.5:14b` only, one factor at a time, so the paper can't say whether results hold on other models.

### Alternative B: Minimal graph

*   **Pros**: Far less to build. Keeping tax-year limits, `IS_A` and a merchant-months query would still cover TPC-46, TPC-49 and most temporal scenarios.
*   **Cons**: It would no longer be the design in architecture 6b, and the paper would be testing a reduced version.
*   **Reason for Rejection**: Rejected by author: the full graph gets built.

### Alternative C: Fix TPC-46 and TPC-49 before the graph

*   **Pros**: Follows ticket order and gives a cleaner baseline sooner.
*   **Cons**: The graph is meant to solve both, so fixing them first would hide its effect.
*   **Reason for Rejection**: Rejected by author: they're tested against the graph first. Fixing them afterwards can invalidate earlier ablation results, which is why the final run repeats them.

### Alternative D: Drop or narrow the retries test

*   **Pros**: Saves building a switch that may show nothing, since the resend mechanism is unlikely to change anything at temperature 0.
*   **Cons**: Leaves a technology the author wants evaluated unmeasured.
*   **Reason for Rejection**: Rejected by author: retries stay in, with how often they fired reported beside them.

### Alternative E: Accept-all and accept-none bounds instead of Claude's decisions

*   **Pros**: No replay store, no rubric and no AI rater to disclose. It gives the widest possible range for what acceptance could change.
*   **Cons**: Accepting everything doesn't resemble how a user behaves, so the result is a range and not an estimate.
*   **Reason for Rejection**: Rejected by author: Claude decides under a written rubric, with saved decisions. Accept-none is still reported, because the no-AI baseline gives the same result.

### Alternative F: Rule-based acceptance, with Claude's decisions as a sample check

*   **Pros**: Replay can't miss a proposal, because a rule can answer any proposal.
*   **Cons**: Writing the rule requires knowing what good acceptance looks like, and a simple rule could favour whatever lowers error.
*   **Reason for Rejection**: Rejected by author: Claude's saved decisions were chosen, with unseen proposals counted as rejected.

### Alternative G: Other ways to collect accept decisions

*   **Pros**: Repeating passes or pausing at each month end would leave few or no unseen proposals.
*   **Cons**: Repeating adds up to about 9 hours and needs someone between rounds. Pausing needs up to 9 attended stops and a saved database.
*   **Reason for Rejection**: Rejected by author: one collection pass followed by replay. It can understate the agent, and the results report how many proposals that affected.

### Alternative H: More or less budget improvement simulation

*   **Pros**: Three passes per model would add a repeatability check. `qwen2.5:14b` alone would save time compared with three passes, at the same cost as the chosen option.
*   **Cons**: Three passes total about 13 to 20 hours. `qwen2.5:14b` alone would remove this metric from the model comparison.
*   **Reason for Rejection**: Rejected by author: one pass per model.

### Alternative I: Judge in a separate pass after the run

*   **Pros**: Latency is measured without the judge in memory, and scoring can be redone without new agent runs.
*   **Cons**: Needs a new command and more schedule time, and results stay incomplete until it finishes.
*   **Reason for Rejection**: Rejected by author: judging stays in the run. Scoring rules must be final at the freeze.

### Alternative J: Other ways to measure variation

*   **Pros**: One run plus a stated determinism check would be cheaper. Scenario paraphrases would test sensitivity to wording. Resampling scenarios would give a range without new model runs.
*   **Cons**: One run drops the ticket's 3-run requirement. Paraphrases need writing and checking, and resampling only shows how much the scenario set matters.
*   **Reason for Rejection**: Raised by reviewers and not adopted: the 3 temperature-0 runs stay as a repeatability check, and a seeded higher-temperature pass measures variation.

### Alternative K: Measure changes in user behaviour

*   **Pros**: Closer to what "improvement" means to a user, such as less overspending.
*   **Cons**: Synthetic transactions come from fixed patterns and don't react to advice, so a result would be meaningless.
*   **Reason for Rejection**: Rejected by author: budget improvement means budget error over periods.

### Alternative L: Claude as a comparison model or as the spot checker

*   **Pros**: A stronger comparison point, and a spot check that doesn't depend on the author's time.
*   **Cons**: Claude as a model needs a new provider in the harness and runs outside the local-only design. Claude as the spot checker wouldn't be the human check section 10.2 asks for.
*   **Reason for Rejection**: Rejected by author: the author reads the sample by hand.

### Alternative M: Report percentage differences only, with significance tests

*   **Pros**: Simple tables, and a formal test looks more rigorous.
*   **Cons**: One scenario moves a 12-scenario group by about 8 points, so percentages alone can mislead, and formal tests have little power at these sizes.
*   **Reason for Rejection**: Rejected by author: wins, losses and ties per scenario are reported with counts. Whether to add a sign test is left to the preregistration.

--------------------------------------------------------------------------------

## Non-Functional Requirements

*   **Privacy:** every model prompt still goes through the privacy gateway, and eval prompts never reach the app's real audit log. Graph facts about a user go through the new `graph` gateway purpose. Claude sees only synthetic proposals.
*   **Reproducibility:** `run_benchmark.py` refuses to start with uncommitted changes, and every results folder records the commit and all settings.
*   **App behaviour:** with the graph off, today's prompts must stay byte-identical, and a test proves it. Benchmark-only conditions (`none`, `hybrid-shuffled`) aren't offered in the app UI.
*   **Machine use:** nothing else runs on the machine during the final run, because latency is a reported result.

--------------------------------------------------------------------------------

## Detailed Implementation Plan

All paths are from the repo root. Work happens on a new branch stacked on `TPC-26`. Every new test is documented in `docs/tests/`, as this project requires.

**Time limit on bug fixes:** if a bug isn't fixed by the freeze on the night of 2026-11-04, its scenarios keep failing, the paper lists them, and the benchmark goes ahead. TPC-46 and TPC-49 get the same treatment at the graph cutoff.

**Tickets covered:** TPC-27, TPC-48, TPC-45, TPC-47, TPC-46, TPC-49, TPC-35 to TPC-39, the ablation parts of TPC-40, and the result tables TPC-28 needs. The bank-connection tools (TPC-41) aren't part of this benchmark and stay out of scope.

### Target Files to Create/Modify

#### Phase 1: Rules, before any graph code

##### 1. `eval/preregistration.md` (Create)

*   **Type / Scope**: Written rules, committed before any ablation runs.
*   **Proposed Changes**: The values below are proposals for the author to confirm.
    *   **Claim:** "factual accuracy against tool figures and planted facts".
    *   **Scenario result:** a scenario counts as passing in a condition when it passes on at least 2 of 3 runs.
    *   **Comparisons and main metric:**

        | Question | Compare | Main metric |
        | --- | --- | --- |
        | Does vector retrieval help? | `vector` vs `none` | Chat fact pass |
        | Does the graph help alone? | `graph` vs `none` | Chat fact pass |
        | Does the graph add to vector? | `hybrid` vs `vector` | Chat fact pass |
        | Is it the facts or the length? | `hybrid` vs `hybrid-shuffled` | Chat fact pass |
        | Does the graph help the budget agent? | `KG_MODE` on vs off | Budget cause and action pass |
        | Do retries help? | Retries on vs off | Budget and goal pass, read with the fire rate |

    *   **Verdict rule, per group:** "helps" if wins minus losses is 2 or more scenarios, "hurts" if it is -2 or less, and otherwise "does not help". A retry mechanism that fired on fewer than 5% of model calls is reported as "uninformative".
    *   **Reported beside the main metric, with no verdict:** judge pass, similarity, abstention, tool choice, temporal correctness and latency.
    *   **Group rule:** `temporal` means the question asks about a past date or a change over time. `graph_suited` means the answer depends on facts held in the graph, and `graph_unsuited` means it doesn't. Every chat scenario is assigned one, listed by id.
    *   **Variation pass:** temperature 0.7, seeds 1, 2 and 3.
    *   **Hand-read sample:** 30 answers, chosen with a fixed seed across conditions and groups.
    *   **Model that fails badly:** it stays in the tables with its results. If a pass takes more than 3 times the `qwen2.5:14b` time, it stops after that pass and the paper says so.

##### 2. `eval/acceptance/rubric.md` (Create)

*   **Type / Scope**: Written rules for Claude's decisions.
*   **Proposed Changes**: Accept `adjust_next_budget` only when the facts show spending has stayed higher for at least two periods and the cause is `recurring`, and otherwise reject. The rubric relies only on what a user would see in the alert. It's written before any proposal is seen, and it has a version number.

##### 3. `eval/scenarios/temporal-*.json` (Create, about 12 files)

*   **Type / Scope**: New chat scenarios.
*   **Proposed Changes**: Cover tax-year limits past and present, when something started, how a budget changed, and past decisions, as listed in TPC-40. Each has an `author` field. About half are written by Claude from the schema description alone. They're committed before TPC-36 to TPC-39 code exists.

##### 4. `eval/scenarios/*.json` (Modify, existing scenarios)

*   **Type / Scope**: Scenario data.
*   **Proposed Changes**: Add group tags to the 22 chat scenarios, plus `allowed_tools` or `tools_none` where they apply. Tag `debt-plan-extra-200` and `debt-avalanche-vs-snowball` as `debt_plan`. Their fact checks use exact months and interest within $1, which matches the debt-plan exactness rule in 10.2. Check the second scenario's facts when tagging it. Golden answers don't change.

#### Phase 2: Bug fixes

##### 5. `backend/app/agents/chat_graph.py` (Modify)

*   **Type / Scope**: Chat prompt, TPC-48 and TPC-47.
*   **Proposed Changes**: Investigate first, since TPC-47 may be a regression from the added tools. Change `SYSTEM_PROMPT` so that questions about the user's own debts look up their debts first, and answers give the totals tools return. Later phases add the modes.

##### 6. `backend/app/agents/tools.py` (Modify)

*   **Type / Scope**: Tool descriptions, TPC-48.
*   **Proposed Changes**: Make `get_spending` and `plan_debt_payoff` state which figures an answer must give, if the prompt alone doesn't fix the failures.

##### 7. `backend/app/budget_engine/pace.py` (Modify)

*   **Type / Scope**: Flag rules, TPC-45.
*   **Proposed Changes**: Change the pace rule so a monthly charge or weekly shop early in the month doesn't count as overspending. Planted overspend scenarios must still flag by their `flag_by` dates.

##### 8. `backend/tests/test_eval_agents.py` and `docs/tests/test_eval_agents.md` (Modify)

*   **Type / Scope**: Tests.
*   **Proposed Changes**: Remove `control-steady-spender` from `KNOWN_FAILURES`, and add entries for any bug that isn't fixed in time.

##### 9. `backend/tests/test_pace.py`, `backend/tests/test_chat_graph.py`, `backend/tests/test_tools.py` and their `docs/tests/` pages (Create or Modify)

*   **Type / Scope**: Tests.
*   **Proposed Changes**: Cover the new pace rule, the prompt text and the tool descriptions. `test_live_chat_answers_which_debt_first` and `test_live_debt_free_with_extra` must pass when run live.

##### 10. `eval/results/<date>/qwen2.5_14b-postfix/` (Create)

*   **Type / Scope**: Results.
*   **Proposed Changes**: A new baseline after the fixes, kept beside the 2026-11-03 results, which stay labelled as pre-fix.

#### Phase 3: Switches and metrics, frozen on the night of 2026-11-04

##### 11. `backend/app/config.py` (Modify)

*   **Type / Scope**: Settings.
*   **Proposed Changes**: Add `KG_MODE` (`off` or `on`), `KG_CHAT` (`none`, `vector`, `graph`, `hybrid`, `hybrid-shuffled`) and `RETRIES_ENABLED`. Defaults keep today's behaviour until TPC-39 lands, then move to `on` and `hybrid`, as architecture 6b specifies.

##### 12. `backend/app/agents/llm.py` (Modify)

*   **Type / Scope**: Model setup.
*   **Proposed Changes**: `get_chat_model` accepts an optional `seed`, passed to Ollama. With no seed, behaviour doesn't change.

##### 13. `backend/app/agents/budget_llm.py` (Modify)

*   **Type / Scope**: Retries.
*   **Proposed Changes**: `invoke_structured` uses 0 retries when `RETRIES_ENABLED` is off, and accepts an optional counter that records each resend.

##### 14. `backend/app/agents/budget_graph.py` (Modify)

*   **Type / Scope**: Retry reporting.
*   **Proposed Changes**: Add `llm_retries` to the run summary, beside `llm_failures`.

##### 15. `backend/app/agents/goal_graph.py` (Modify)

*   **Type / Scope**: Retries.
*   **Proposed Changes**: Allow 1 call when `RETRIES_ENABLED` is off. `attempts` already records how often it fired.

##### 16. `backend/app/agents/chat_graph.py` and `backend/app/agents/tools.py` (Modify)

*   **Type / Scope**: Retrieval modes.
*   **Proposed Changes**: Choose the prompt text by mode, following the table in the Technical Plan. `make_tools` leaves out `search_kb` in `none` and `graph`.

##### 17. `eval/fixtures.py` (Modify)

*   **Type / Scope**: Scenario loading.
*   **Proposed Changes**: `Expect` gains optional `allowed_tools`, `tools_none` and `temporal` checks, and scenarios gain an optional `author`, so the current 40 files still load unchanged. Reject a scenario that has both `tools` and `tools_none`.

##### 18. `eval/metrics.py` (Modify)

*   **Type / Scope**: Scoring.
*   **Proposed Changes**:
    *   Add a `specific` score (1 to 5) to `JUDGE_PROMPT`, reported on its own, so the judge pass stays comparable with earlier results.
    *   Add scoring for tool names, arguments and irrelevance, leaving `tools_ok` as it is.
    *   Add temporal correctness.
    *   Add repeat alerts and retry-fire rates to the budget and goal summaries.
    *   Add `debt_plan` as a reported group.

##### 19. `eval/runner.py` (Modify)

*   **Type / Scope**: Chat runs.
*   **Proposed Changes**: Build retrieval and tools from the mode instead of always using `kb.search`, and record prompt length per run.

##### 20. `eval/agent_runner.py` (Modify)

*   **Type / Scope**: Budget and goal runs.
*   **Proposed Changes**: Record retry counts for each run.

##### 21. `eval/run_eval.py` (Modify)

*   **Type / Scope**: Command line.
*   **Proposed Changes**: Add `--retrieval`, `--kg`, `--retries`, `--temperature`, `--seed`, `--today` and `--label`. `results_dir` uses the label in the folder name. `config.json` records every setting, the Ollama version and a hash of the scenarios.

##### 22. `eval/budget_users.py` and `backend/tests/test_budget_users.py` (Modify)

*   **Type / Scope**: Synthetic users.
*   **Proposed Changes**: Add 3 profiles running at least three months past the first budget, including one with lasting higher spending, and test that the planted facts exist. These add new users only, so existing results don't change.

##### 23. `eval/budget_improvement.py` (Create)

*   **Type / Scope**: Benchmark.
*   **Proposed Changes**: Takes `--model`, and either `--collect` or `--replay`.
    *   **`--collect`:** runs with nothing accepted, and writes each proposal with its facts to `eval/acceptance/proposals-<model>.json`.
    *   **`--replay`:** uses `on_day` to apply saved decisions before the month closes.
    *   **`--baseline`:** runs the close logic with no model calls.
    *   **Output:** budget error per period, the difference from the baseline, proposals seen, proposals with a decision and unseen proposals.

##### 24. `eval/acceptance/decisions.json` (Create)

*   **Type / Scope**: Saved decisions, written by Claude after the collection pass.
*   **Proposed Changes**: Each entry has the key (user, simulated date, category, action, amount), decision, reason, rubric version and deciding model. Proposal files have the agent model removed before Claude reads them.

##### 25. `eval/compare.py` (Create)

*   **Type / Scope**: Reporting.
*   **Proposed Changes**: Takes two results folders and refuses to compare them if their commit or scenario hash differs. Outputs wins, losses and ties per scenario and group, and the verdict from the preregistered rule, as Markdown tables and JSON for TPC-28.

##### 26. `eval/run_benchmark.py` (Create)

*   **Type / Scope**: Run driver.
*   **Proposed Changes**: Runs the conditions from the Technical Plan in order, refuses to start with uncommitted changes, skips finished conditions on a restart, and supports `--pilot`, which runs about 10 scenarios per condition and times each model.

##### 27. `eval/sample_answers.py` (Create)

*   **Type / Scope**: Hand-read sample.
*   **Proposed Changes**: Writes the sample to a file with conditions hidden, with a separate key file. After the author rates them, it reports how often the author and judge agree.

##### 28. `backend/tests/test_eval_compare.py`, `test_eval_benchmark.py`, `test_budget_improvement.py`, `test_eval_metrics.py`, `test_eval_fixtures.py`, `test_eval_cli.py`, `test_eval_runner.py`, `test_budget_llm.py`, `test_goal_graph.py`, `test_llm.py`, `test_chat_graph.py`, `test_tools.py` and their `docs/tests/` pages (Create or Modify)

*   **Type / Scope**: Tests.
*   **Proposed Changes**: Cover each switch, including that `none` removes `search_kb`, that `vector` matches today's prompt byte for byte, and that retries off makes one call. Cover scoring, decision replay including unseen proposals, and `compare.py` refusing mismatched folders. `test_eval_fixtures.py` counts get updated.

#### Phase 4: The graph, by the end of 2026-11-06

Each ticket's scope and "done when" list are its specification. These entries record which files they touch and what the benchmark needs in addition.

##### 29. `backend/app/db/models.py` and a new file in `backend/alembic/versions/` (Modify, Create)

*   **Type / Scope**: TPC-35.
*   **Proposed Changes**: `kg_nodes` and `kg_edges` tables, with a migration that can be reversed. `temp_db` picks them up through `create_all`.

##### 30. `backend/app/kg/__init__.py`, `store.py`, `query.py`, `project.py` (Create)

*   **Type / Scope**: TPC-35, TPC-36.
*   **Proposed Changes**: The store, queries and projection from architecture 6b.1 and 6b.2.

##### 31. `backend/app/agents/budget_graph.py`, `backend/app/budget_service/close.py`, `backend/scripts/build_kg.py`, `docs/diagrams/budget-agent-loop.puml` (Modify, Create)

*   **Type / Scope**: TPC-36.
*   **Proposed Changes**: The `update_graph` node, projection at close, and the backfill script.

##### 32. `backend/app/budget_engine/diagnose.py`, `backend/app/agents/budget_llm.py`, `backend/app/privacy/gateway.py`, `backend/scripts/seed_budget_user.py`, `eval/budget_users.py`, `eval/scenarios/budget-*.json` (Modify, Create)

*   **Type / Scope**: TPC-37.
*   **Proposed Changes**: `graph_facts`, the `reclassified` cause with its guard, and `KG_MODE`. The four new planted users each get a budget scenario, so `run_eval.py --kind budget --kg off|on` covers the 8-user comparison without the separate `eval/budget_causes.py` that TPC-40 proposed.

##### 33. `kb/concepts.json`, `kb/graph.json`, `backend/scripts/extract_kg.py`, `backend/scripts/ingest_kb.py`, `eval/results/<date>/kg_extraction/` (Create, Modify)

*   **Type / Scope**: TPC-38.
*   **Proposed Changes**: The guidance graph with tax-year limits, read by hand before committing, plus precision on 30 triples and recall on 15 facts.

##### 34. `backend/app/agents/chat_graph.py`, `backend/app/agents/tools.py`, `backend/app/privacy/gateway.py`, `frontend/components/ChatPanel.tsx`, `frontend/lib/api.ts`, `docs/diagrams/chat-flow.puml` (Modify)

*   **Type / Scope**: TPC-39.
*   **Proposed Changes**: The `graph_retrieve` node, the `get_history` tool, the `graph` gateway purpose and the UI chip. For the benchmark, `hybrid-shuffled` shuffles fact values with a fixed seed, using the existing `KG_CHAT` setting.

##### 35. `eval/fixtures.py` and `eval/runner.py` (Modify)

*   **Type / Scope**: Graph in the eval.
*   **Proposed Changes**: After seeding a scenario's user, build its graph with `app/kg/project.py` when the mode uses the graph.

##### 36. `backend/tests/test_kg_store.py`, `test_kg_query.py`, `test_kg_project.py`, `test_kg_extract.py`, `test_chat_graph.py`, `test_budget_graph.py`, `frontend/components/ChatPanel.test.tsx`, `frontend/lib/api.test.ts` and their `docs/tests/` pages (Create or Modify)

*   **Type / Scope**: Tests.
*   **Proposed Changes**: Each ticket's "done when" list, plus tests that `KG_MODE=off` and `KG_CHAT=vector` send byte-identical prompts to today's, and that the longest prompt fits within `LLM_NUM_CTX`.

#### Phase 5: Documentation

##### 37. `eval/README.md` (Modify)

*   **Type / Scope**: Docs.
*   **Proposed Changes**: How to run the benchmark, the new flags and metrics, and how decisions are collected.

##### 38. `docs/architecture.md` (Modify)

*   **Type / Scope**: Docs.
*   **Proposed Changes**: Update sections 6.4, 6b and 10 with what was built, including the new `KG_CHAT` values. The decision log records the pace thresholds, the benchmark design and the removal of `budget_causes.py`.

##### 39. `docs/agent-pipelines.md`, `docs/system-walkthrough.md`, `docs/diagrams/evaluation-flow.puml`, `docs/diagrams/data-model.puml` (Modify)

*   **Type / Scope**: Docs.
*   **Proposed Changes**: Ticket status, the graph tables and the benchmark flow. The rendered `.png` and `.svg` files get regenerated.

##### 40. `docs/handoffs/<date>-tpc-27-manual-verification.md` (Create)

*   **Type / Scope**: Handoff, as this project requires after implementation work.
*   **Proposed Changes**: How to verify each phase by hand, with the commands and expected results.

### Verification Plan

*   **Build**: `cd frontend && npm run build`
*   **Unit Tests**: `cd backend && ../venv/bin/python -m pytest`, and `cd frontend && npm test`
*   **Linting**: `cd frontend && npm run lint`. No Python linter config was found in the repo.
*   **Coverage**: no percentage target. Every new test has a page in `docs/tests/`.
*   **Live checks**:
    *   The six fixed scenarios pass over 3 runs on `qwen2.5:14b`, using `venv/bin/python eval/run_eval.py --only <id> --runs 3`.
    *   `test_live_chat_answers_which_debt_first` and `test_live_debt_free_with_extra` pass.
    *   TPC-35 to TPC-39 meet their "done when" lists.
*   **Pilot, before the freeze**: `venv/bin/python eval/run_benchmark.py --pilot` runs every condition on all three models. It must show:
    *   each switch changes what it should;
    *   `llama3.1:8b` and `mistral` call tools;
    *   one multi-period user's time;
    *   whether Ollama swaps models;
    *   how often retries fire.
*   **Final run checks**:
    *   All results folders share one commit and scenario hash.
    *   `compare.py` produces every table.
    *   Budget improvement reports unseen proposals for each model.
    *   The author has read the sample.

--------------------------------------------------------------------------------

*Drafted with EGM*
