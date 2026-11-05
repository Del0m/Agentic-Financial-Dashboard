# TPC-28: result tables and charts, and how to check them

Written 2026-11-05. All results come from commit `635b529` on branch `TPC-27-benchmark`. Nothing here is committed yet.

## What was built

| File | What it is |
|---|---|
| `eval/results/2026-11-05/comparisons/*.md` and `.json` | The 7 preregistered comparisons, from `eval/compare.py` |
| `eval/make_tables.py` | New. Builds the paper's tables from saved results, with no model calls |
| `eval/results/2026-11-05/tables.md` | Its output: models, retrieval modes, verdicts, variation and budget improvement |
| `paper/benchmark_figures.py` | New. Three charts: models, chat retrieval modes, budget improvement |
| `paper/figures.json`, `paper/refresh.py`, `paper/README.md` | Point the figures at the final results and add the new charts |
| `paper/generated/benchmark-*.{svg,pdf,png}` | The charts |

## Which folders count

Two conditions were cut off and rerun. The final ones are `llama3.1_8b-baseline-2` and `qwen2.5_14b-variation-seed3-2`. The partial `llama3.1_8b-baseline` and `qwen2.5_14b-variation-seed3` folders have no `finished_at` and are never used. `qwen2.5_14b-smoke` was a one-scenario check before the run.

## Results in brief

| Question | Verdict |
|---|---|
| Vector retrieval vs none | Does not help: 3 wins, 3 losses |
| Graph alone vs none | Helps: 11 wins, 1 loss, nearly all temporal |
| Graph added to vector | Helps: 9 wins, 0 losses, all temporal |
| Real vs shuffled graph facts | Helps on temporal (2 wins), not overall (2 wins, 1 loss) |
| Graph in the budget agent | Does not help: 1 win (`budget-graph-reclassified`), 1 loss (`budget-seasonal-holidays`) |
| Budget and goal retries | Uninformative: they fired on 0% of calls |

- **Models:** `qwen2.5:14b` passes 32/34 chat, 15/16 budget and 6/6 goal scenarios. `llama3.1:8b` passes 28, 13 and 5. `mistral` passes 16, 12 and 3.
- **Budget improvement:** every model brings new-club's error from about 28% to 6–9% by October. For the steady and rising users, all models make it worse than no AI. Unseen proposals: 0 for every model.

## Caveats the paper must state

- The temporal scenarios, 12 of the 15 in that group, were written by the same AI that built the graph. Most of the graph's wins come from them.
- Accept decisions were the user approving all proposals as a set, not the rubric.
- About 1:58–2:03 pm, a demo advance ran on the same Ollama model during `chat-vector`. The user chose not to rerun it, and answers should be unaffected, but the latency of roughly 8–12 runs in that window is inflated.
- "Days to flag" is 0.0 for every model, so it doesn't separate them.
- Abstention is 75% at baseline, because `abstain-roth-2030` still fails (TPC-49).

## Check it by hand

From the repo root:

1. **Regenerate the tables.** The output should match `tables.md`, and the first line names commit `635b529` only.

   ```bash
   venv/bin/python eval/make_tables.py eval/results/2026-11-05
   ```

2. **Re-run one comparison.** It should print 9 wins, 0 losses, "helps" for temporal.

   ```bash
   venv/bin/python eval/compare.py eval/results/2026-11-05/qwen2.5_14b-baseline eval/results/2026-11-05/qwen2.5_14b-chat-vector --metric chat
   ```

3. **Regenerate the figures**, then open `paper/generated/index.html`. The last three cards before "Initial budget accuracy" are the new charts.

   ```bash
   python3 paper/refresh.py
   ```

4. **Spot-check one number by hand.** Open `eval/results/2026-11-05/qwen2.5_14b-chat-none/temporal-gym-start.run1.json`, and the same file under `qwen2.5_14b-baseline`. The `scores.facts.passed` value should be `false` without retrieval and `true` with hybrid retrieval.

## Still left

- The blinded hand-read sample is yours to do, with `eval/sample_answers.py pick`, then `score`.
- Commit the results, tables and figures, then update TPC-27, TPC-28 and TPC-40 and the tickets that were waiting on this run.
- Merge `TPC-27-benchmark`, then `dashboard-polish`.
