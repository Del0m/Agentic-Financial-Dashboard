# TPC-26 golden scenarios: manual verification

The benchmark now has 40 scenarios across chat, budget and goal, run by one command. The work is on branch `TPC-26`, stacked on `TPC-31`, `TPC-33` and `TPC-25`. Design: [spec](../superpowers/specs/2026-11-03-tpc-26-golden-scenarios-design.md). Format: [`eval/README.md`](../../eval/README.md).

## Baseline

From `eval/results/2026-11-03/qwen2.5_14b/`, one pass on `qwen2.5:14b`, 14 minutes, no crashed runs.

| Kind | Passed | Notes |
|---|---|---|
| Chat | 16 of 22 facts (73%) | Judge 82%, tools 91%, sources 100%, similarity 0.91 |
| Budget | 9 of 12 (75%) | False alarms on 1 of 4 controls, cause 67%, action 100%, alert on the day of the flag, low-balance warning 6 days ahead |
| Goal | 6 of 6 | One model call or none on every run |

| Tag | Result |
|---|---|
| `high_interest_debt` | 33% |
| `unused_subscription`, `cash_flow`, `guidance` | 100% |
| `spending`, `abstain` | 75% |
| `one_time`, `recurring`, `signal` (budget) | 100% |
| `seasonal` (budget) | 0% |
| `no_overspend_control` (budget) | 75% |
| `goal_affordability` | 100% |

Nine scenarios fail. Each is an agent problem with its own ticket, and its golden answer was left as it is:

| Ticket | Problem | Scenarios |
|---|---|---|
| [TPC-45](https://ucf-ctcw-senior-design.atlassian.net/browse/TPC-45) | Pace flags weekly and monthly spending with no overspend | `control-steady-spender` |
| [TPC-46](https://ucf-ctcw-senior-design.atlassian.net/browse/TPC-46) | Spread spending is diagnosed `one_time`, not `seasonal` | `budget-seasonal-holidays`, `budget-seasonal-back-to-school` |
| [TPC-47](https://ucf-ctcw-senior-design.atlassian.net/browse/TPC-47) | "Which debt first" gets generic advice with no lookup | `debt-highest-apr`, `debt-none`, `debt-higher-apr-smaller-balance` |
| [TPC-48](https://ucf-ctcw-senior-design.atlassian.net/browse/TPC-48) | Answers misstate or leave out tool figures | `income-last-30-days`, `debt-plan-extra-200` |
| [TPC-49](https://ucf-ctcw-senior-design.atlassian.net/browse/TPC-49) | States a limit for a year the guidance doesn't cover | `abstain-roth-2030` |

An earlier pass was run before four corrections to my own work, and the same 9 failed the same way in both. The corrections were two scenarios that demanded a name the question didn't ask for, a phrase the abstain rule wrongly rejected, and a lead-time average that included unrelated users. That pass isn't in the repo.

One pass can't show how often each of these happens. TPC-27's three-run comparison will.

## Checks

Run these from the repo root unless a step says otherwise.

1. **Offline tests, which need no Ollama:**

   ```bash
   cd backend && ../venv/bin/python -m pytest tests/test_eval_fixtures.py tests/test_eval_metrics.py tests/test_eval_agents.py tests/test_eval_cli.py tests/test_eval_runner.py tests/test_budget_users.py -v
   ```

   All pass. `test_budget_code_decisions_match_the_golden_answer` runs every budget scenario with a fake model.

2. **A budget scenario with the real model.** It takes under a minute, and needs only Ollama and the agent model:

   ```bash
   venv/bin/python eval/run_eval.py --only budget-one-time-new-shop --runs 1
   ```

   The table shows `budget-one-time-new-shop` at 100%. In the printed results folder, `budget-one-time-new-shop.run1.json` has `flags` first set on 2026-10-12, an overspend alert with cause `one_time` that names ElectroMart, and `scores.checks` all true.

3. **A false alarm is visible:**

   ```bash
   venv/bin/python eval/run_eval.py --only control-steady-spender --runs 1
   ```

   It prints 0% and `false alarms on controls: 100%`. The run file lists the three flagged categories under `scores.extra_flags`. The Streamly alert may propose `adjust_next_budget`, which is why TPC-45 matters.

4. **Goal scoring.** In the baseline folder, open `goal-no-surplus.run1.json`: `attempts` is 0 and `model_not_called` is true. In `goal-debt-first.run1.json`, the first plan is `debt_payoff`, and its `monthly_cents` total is within `cap_cents`.

5. **Abstention.** In the baseline folder, `abstain-shop-never-visited.run1.json` should pass, and `abstain-roth-2030.run1.json` should fail with both `says the data isn't available` and `states no dollar amount` under `facts.missing`.

6. **The real audit log isn't touched.** Note the size and date of `backend/logs/prompt_audit.jsonl`, run step 2, and check they haven't changed. The results folder has its own `audit.jsonl`.

7. **Bad scenarios fail loudly.** Temporarily change `"profile"` in one budget file to a name that doesn't exist, and run:

   ```bash
   venv/bin/python eval/run_eval.py --kind budget --runs 1
   ```

   It prints `Bad scenario:` with the file and `profile`, and exits with code 2 before running anything. Put the file back afterwards.

## Test status

- **Non-live suite:** 1,462 passed.
- **Live tests (Ollama):** 16 of 18 pass, run before the final corrections, which only touched `eval/`. `test_live_chat_answers_which_debt_first` (TPC-47) and `test_live_debt_free_with_extra` (TPC-48) fail on the model's answers. The only app code changed on this branch is the profile lookup in `simulate.py`.

## Not done

- The baseline's `commit` field records `6ce8567`, the last commit on `TPC-31`, because the baseline ran before this work was committed. The code it ran is this branch's commit.
- `TPC-26` is still In Progress in Jira.
- `tools_none` scoring and `temporal` scenarios are left for TPC-27 and TPC-39.
- Your uncommitted docs, diagrams and older `eval/results` folders were left alone.
