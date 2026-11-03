# TPC-27 progress: how to verify it by hand

This covers the work so far on branch `TPC-27`, which is stacked on `TPC-26`: the benchmark's rules and the fixes for TPC-48, TPC-45 and TPC-47. Design: [spec](../superpowers/specs/2026-11-03-tpc-27-full-benchmark-design.md).

| Commit | What |
|---|---|
| `f452af7` | Design, preregistration, accept rubric, scenario groups, 6 temporal scenarios |
| `c146758` | TPC-48 fix |
| `8bab89a` | TPC-45 fix |
| `98052f6` | TPC-47 fix and `--skip-tag` |
| `aaccb22` | Forecast summary, post-fix baseline results and this handoff |

## Post-fix baseline

From `eval/results/2026-11-03/qwen2.5_14b-postfix/`: commit `98052f6`, one pass on `qwen2.5:14b`, the original 40 scenarios, 9 minutes, no crashed runs. One pass can't show how often each result happens, so these are checks, not final numbers.

| Measure | Nov 3 baseline | Post-fix |
|---|---|---|
| Chat facts | 16 of 22 (73%) | 20 of 22 (91%) |
| Judge | 82% | 91% |
| Expected tools called | 91% | 100% |
| Budget | 9 of 12 (75%) | 10 of 12 (83%) |
| False alarms on controls | 1 of 4 (25%) | 0 of 4 (0%) |
| Goal | 6 of 6 | 6 of 6 |

Six scenarios changed from failing to passing: `income-last-30-days`, `debt-plan-extra-200`, `control-steady-spender`, `debt-highest-apr`, `debt-none` and `debt-higher-apr-smaller-balance`.

Four scenarios fail in this pass:

| Scenario | Why | Status |
|---|---|---|
| `cash-before-payday` | Left out the bill name. **It passed on Nov 3** | Fixed after this baseline. See below |
| `budget-seasonal-holidays`, `budget-seasonal-back-to-school` | Wrong cause | Expected. TPC-46 waits for the graph |
| `abstain-roth-2030` | States a limit for 2030 | Expected. TPC-49 waits for the graph |

## Regression found and fixed: `cash-before-payday`

This broke the design's "no earlier pass regresses" goal. It passed 1 of 3 runs when repeated after the baseline, and one answer said "you won't run short" although the forecast dips below the user's buffer. Nov 3 had only one run, so it may have been unreliable before these changes. That wasn't tested on the older code.

The fix gives the forecast a code-written summary, like the debt plan's, and the prompt tells the model to repeat it. Live, `cash-before-payday` and `cash-no-income` each passed 3 of 3. To check it:

```bash
venv/bin/python eval/run_eval.py --only cash-before-payday --runs 3
```

Each answer should say the balance could run short, reaching $50.00 below the $100.00 buffer, because of the Landlord.

The fix came after the baseline, so the table above doesn't include it. Prompts changed again, so the final run still has to cover every chat scenario.

## The disclaimer is now added by code

Answers sometimes left out "This is general information, not financial advice." It was missing from 3 of 22 chat answers on Nov 3, 5 of 22 in the post-fix baseline, and 2 of 3 `cash-before-payday` answers after the forecast fix. A new `finish` node in the chat graph now adds it when it's missing, and `POST /chat` streams the added line.

This came after the baseline too, and it adds text to some answers, so similarity and judge scores may shift slightly in the final run. To see it, ask anything in the chat UI and check that the answer ends with the line, or run:

```bash
venv/bin/python eval/run_eval.py --only spending-food-30d --runs 1
```

The run file's `answer` ends with the disclaimer. `docs/diagrams/chat-flow.puml` doesn't show the new node yet.

## Checks

Run these from the repo root unless a step says otherwise. Steps 3 to 6 need Ollama running with `qwen2.5:14b`, `gemma3:12b` and `nomic-embed-text`, and the knowledge base ingested. Each `run_eval.py` command adds a results folder.

1. **Offline tests, which need no model:**

   ```bash
   cd backend && ../venv/bin/python -m pytest -q -k "not live"
   ```

   Expect 1,497 passed and 18 deselected.

2. **The rules came before any graph code.** This shows they were committed in `f452af7`:

   ```bash
   git log --oneline -- eval/preregistration.md eval/acceptance/rubric.md
   ```

   Read [the preregistration](../../eval/preregistration.md) and [the accept rubric](../../eval/acceptance/rubric.md). Check that you agree with the verdict rule and the scenario groups, especially `abstain-balance-2019`, `abstain-shop-never-visited` and `subs-price-rose` being in `temporal`. The accept rule also shapes the budget improvement result.

3. **TPC-48.** Both should print 100% under `facts`:

   ```bash
   venv/bin/python eval/run_eval.py --only income-last-30-days --runs 3
   ```

   ```bash
   venv/bin/python eval/run_eval.py --only debt-plan-extra-200 --runs 3
   ```

   In the second results folder, the answers should state 49 months and $2,941.08 total interest. In the `tool_results`, each plan has a `summary`.

4. **TPC-45.** This should print 100% and `false alarms on controls: 0%`:

   ```bash
   venv/bin/python eval/run_eval.py --only control-steady-spender --runs 1
   ```

   Then confirm real overspending still flags. `budget-recurring-wine-club` should print 100%:

   ```bash
   venv/bin/python eval/run_eval.py --only budget-recurring-wine-club --runs 1
   ```

   To see the rule, read `usual_by_day` in `backend/app/budget_engine/pace.py`.

5. **TPC-47.** This should print 100% under `facts`, and `tool_calls` in each run file should include `get_debts`:

   ```bash
   venv/bin/python eval/run_eval.py --only debt-highest-apr --runs 3
   ```

   Do the same for `debt-none` and `debt-higher-apr-smaller-balance`. On `debt-none`, the answer should say there are no debts. Its judge passed 2 of 3 runs in one earlier check.

6. **The ticket's live tests:**

   ```bash
   cd backend && ../venv/bin/python -m pytest tests/test_chat_api.py tests/test_tools.py -q -k "test_live_chat_answers_which_debt_first or test_live_debt_free_with_extra"
   ```

   Both pass, though a repeat could differ, because runs at temperature 0 do vary.

7. **The baseline's record.** Open `eval/results/2026-11-03/qwen2.5_14b-postfix/config.json`. `commit` is `98052f6...`, and `scenarios` has 40 ids with no `temporal-*`.

## What the fixes changed that you may want to review

- **The debt answer now picks a method.** "Which debt first" gets the highest-APR debt, which is the avalanche method, unless the user asks about snowball.
- **TPC-45's rule isn't independently tested yet.** It was developed against `control-steady-spender` and `budget-recurring-wine-club`. The decision log says so, and the paper should too.
- **Three forecast tests changed.** They relied on the false alarms TPC-45 removed, so they now plant a real overspend.
- **TPC-47's cause wasn't traced.** The fix addresses the behaviour. Whether the added tools caused it, as the ticket suspected, wasn't tested.
- **Temperature-0 runs vary.** `debt-plan-extra-200` and `cash-before-payday` gave different answers across 3 runs, which corrects the brief and design. The design's text still needs updating.

## Not done

- **Uncommitted:** the disclaimer change and this file's update. `eval/results/2026-11-03/qwen2.5_14b-2` to `-16` are debugging runs. They're uncommitted too, and can be deleted.
- **Also untracked, left alone:** `concepts_brief.md`, `design_draft.md` and `docs/superpowers/plans/2026-11-03-tpc-35-temporal-store.md`. The last one belongs to the TPC-35 work.
- **Temporal scenarios:** 6 of about 12 exist, and the rest need budget and advice data in the loader. They must be committed before any TPC-36 to TPC-39 code.
- **Phase 3:** switches, new metrics and `run_benchmark.py` aren't started. Only `--skip-tag` and the loader fields exist, and the new fields aren't scored yet.
- **Phase 4:** TPC-36 to TPC-39 wait for TPC-35, which was still uncommitted in `/private/tmp/plaid-tpc35` when this was written.
- **Jira:** no tickets were changed. TPC-48's description still has the wrong income diagnosis.
