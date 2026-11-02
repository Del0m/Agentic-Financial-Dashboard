# Tests: `backend/tests/test_goal_graph.py`

These tests cover `app/agents/goal_graph.py` and `goal_llm.py` (TPC-23/24): the graph `load_facts → propose → validate_candidate → persist`, with one correction loop, `run_goals`, and the CLI. Code measures the surplus and computes each goal's target and date; the model picks the goals, their order and their monthly amounts.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_goal_graph.py -v
```

## Shared setup

- `audit_in_tmp` (autouse) keeps the prompt audit log under `tmp_path`.
- The `user` fixture seeds `goals-card-24` (`eval/budget_users.py`): a $971.75 monthly surplus after the card's $95 minimum, so a $777.40 cap at the default 80%, a $3,200 card at 24.99%, an $18,400 student loan, payments on both and a $1,000 savings account. `TODAY` is its date, 2026-11-02, so plans start on 2026-12-01.
- `g(kind, cents, strategy, why)` is one proposed goal, sent in dollars as the model answers; `model(*goals)` is a `ScriptedChatModel` that answers with them as JSON.
- `stored(db, status)` reads the user's goals by priority; `run_row` reads a run; `set_prefs` sets the goal preferences.
- `cli(db, *args, fake)` calls `main` with the user and date filled in.

---

## `test_valid_proposal_stored_from_code`

**What it does:** The model proposes a $200 avalanche debt goal and $100 of savings. Both are stored as `proposed` with priorities 1 and 2. The debt goal's date is the planner's and its target is the $21,600 total; savings is $1,200 by November 2027. The run row is `ok`, finished, and records the surplus, the cap and the goal ids, and each goal points at the run.

**Why it's there:** The whole path. The model never supplies a target or a date.

## `test_second_run_supersedes_the_first`

**What it does:** After two runs, only the second's goal is `proposed`; the first's is `superseded`.

**Why it's there:** The user only ever sees one current proposal, and the history stays.

## `test_no_surplus_never_calls_the_model`

**What it does:** A user spending more than they earn: status `no_surplus`, the −$1,000 figure is recorded, the model is never called, and the run row is `ok`.

**Why it's there:** A model shouldn't be asked to invent room. It isn't a failure either, so the run isn't an error.

## `test_no_history_is_no_surplus`

**What it does:** A user with no transactions: status `no_surplus`, surplus `None`, no model call.

**Why it's there:** A newly linked user gets a clear result, not a crash.

## `test_nothing_offered`

**What it does:** Preferences that skip all three kinds: status `nothing_offered`, no model call.

**Why it's there:** With no kind to offer, any answer from the model would be dropped anyway.

## `test_unoffered_goals_rejected_not_partially_stored`

**What it does:** Two proposals contain savings excluded by preferences. Both are rejected, no emergency-fund subset is stored, and the run records not_offered reasons.

**Why it's there:** TPC-24 validates the complete allocation, including structured preferences.

## `test_debt_goal_without_a_strategy_is_avalanche`

**What it does:** A `debt_payoff` goal with no strategy is stored with avalanche, first, with the planner's avalanche date, and nothing is dropped.

**Why it's there:** Live, `qwen2.5:14b` left the strategy out of 1 run in 25. The first version dropped the goal, which put the emergency fund first and broke the done-when. The prompt's default applies instead.

## `test_repeated_kind_rejects_the_proposal`

**What it does:** Repeated savings kinds on both calls fail the run and store no goals.

**Why it's there:** A duplicate must trigger correction, not a partial plan.

## `test_nothing_usable_is_an_error`

**What it does:** A run whose only goal is unusable ends in `error`, reason `validation_failed` after two rejected calls, and the earlier proposal stays `proposed`.

**Why it's there:** A bad run mustn't wipe out a good proposal.

## `test_bad_json_twice_is_an_error`

**What it does:** Two malformed replies consume the entire two-call budget and end with bad_model_output.

**Why it's there:** Parsing and affordability share one retry budget; correction includes a fixed schema explanation.

## `test_model_outage_recorded`

**What it does:** The model raises `ConnectionError` on its one call: `run_goals` returns `error` with that name, the row says the same, the log has the error, and nothing is written.

**Why it's there:** The CLI and TPC-25's route get a result to report, and a run row is never left as `running`. This used to patch `invoke_structured` to raise, which hid that the real one swallowed the error and called it `bad_model_output` (final review).

## `test_prompt_gets_the_gateway_context`

**What it does:** The model's second message holds the gateway's `goals` context, with the $777.40 cap and `debt_payoff` offered. The system prompt carries the 8% rule, and one `goals` audit line exists.

**Why it's there:** User data reaches the model only through the gateway, audited. The 8% rule is what the done-when relies on.

## `test_no_budget_uses_default_needs`

**What it does:** A user with no budget period still gets an emergency-fund gap above $0, from the default needs categories.

**Why it's there:** It's plan Review Focus 1: goals mustn't depend on having run a budget first.

## `test_open_period_needs_lines_used`

**What it does:** With an open period whose only needs line is entertainment, the gap comes from that line alone: 3 × a $15.99 bill is less than the $1,000 saved, so $0, where the defaults gave more.

**Why it's there:** A line's group is the user's to change (TPC-22), so their `needs` lines decide what an emergency fund must cover.

## `test_none_savings_balance_counts_zero`

**What it does:** With the savings account's balance set to `None`, the gap grows by exactly the $1,000 it had.

**Why it's there:** It's plan Review Focus 5: Plaid can leave a balance out, and that must not crash or count as money.

## `test_never_goal_rejected_without_a_date`

**What it does:** A $1 extra cannot amortize the card within 600 months. Two such replies fail with no_date and no goal rows.

**Why it's there:** Only achievable dated proposals may be published; low-level builders still describe impossible plans with null dates.

## `test_busy_when_the_lock_is_held`

**What it does:** With the user's lock held, the status is `busy` and no run row is written.

**Why it's there:** It's plan Review Focus 4: a goal run reads the money a budget run or close may be changing.

## `test_lock_released_after_a_run`

**What it does:** The lock is free again after a run.

**Why it's there:** A leaked lock would mark the user busy for the budget run too, until the server restarts.

## `test_cli_prints_goals`

**What it does:** The report shows the surplus, the cap, and each goal's kind, strategy, monthly amount, target, date and rationale.

**Why it's there:** Until TPC-25's page, this is how a person sees what the agent proposed.

## `test_cli_refuses_goals_over_the_cap`

**What it does:** A combined $800 proposal exceeds the $777.40 cap on both calls. The CLI exits 1, explains the excess, and prints no numbered goal lines.

**Why it's there:** The command must not present a rejected allocation as a successful plan.

## `test_cli_says_when_a_goal_has_no_end`

**What it does:** The CLI exits 1 and reports that the candidate cannot finish within 600 months.

**Why it's there:** An undated candidate is refused rather than printed as a usable goal.

## `test_cli_defaults_to_the_simulated_date`

**What it does:** With no `--date`, a simulated user's run gets their simulated date, not the real one.

**Why it's there:** It's plan Review Focus 2: on the real date, a simulated user's history would look stale or empty.

## `test_cli_bad_prefs_exit_1`

**What it does:** A `max_share` of 2, and text that isn't JSON, each print a message and exit 1 without starting a run.

**Why it's there:** A bad flag must not run the model on the wrong preferences.

## `test_cli_good_prefs_stored`

**What it does:** `--prefs` stores the preferences before the run, and it uses them.

**Why it's there:** There's no UI for preferences yet, so this is how they're set.

## `test_cli_error_exit_1`

**What it does:** A failed run prints its reason and exits 1.

**Why it's there:** Scripts and people can tell a failed run from a run with no goals.

## `test_cli_busy_exit_1`

**What it does:** With the lock held, it says a run is in progress and exits 1.

**Why it's there:** The same, for a run that never started.

## `test_live_card_debt_goal_first`

**What it does:** Runs the real model when available. Debt payoff is first, all stored monthly amounts fit the cap, and every goal is dated.

**Why it's there:** Retains the TPC-23 done-when while checking TPC-24 safety; skipped without the configured Ollama model.

## Final review

These came from the final review. `red_user(db)` seeds a user spending $2,000 a month on $1,000 of income, with one `proposed` savings goal.

## `test_no_surplus_withdraws_the_last_proposal`

**What it does:** A user now in the red has no goals `proposed`, the old one is `superseded`, and the CLI says it's withdrawn.

**Why it's there:** `GET /goals` kept showing a plan funded from a surplus the user no longer had.

## `test_nothing_offered_withdraws_the_last_proposal`

**What it does:** After a proposal, skipping every kind ends with `nothing_offered` and withdraws it.

**Why it's there:** It kept showing a kind the user had just excluded.

## `test_long_history_keeps_all_three_months`

**What it does:** Six months of history, with August's first transaction on Aug 5. The months leave $3,000, $100 and $3,000, so the surplus is $3,000.

**Why it's there:** The loaded window started at August, so Aug 5 looked like the start of the user's history and August was skipped. The surplus came out as $1,550, the median of two months.

## `test_unusable_amount_gets_a_retry`

**What it does:** An amount of $0.001, $0.50 or $1e16 gets a second try, and the second answer is stored.

**Why it's there:** $0.001 rounds to 0 cents, which crashed the emergency fund's date or stored a $0 goal, and took the valid goals in the same answer down with it. The answer in dollars opened this; the earlier integer cents couldn't reach 0.

## `test_running_budget_elsewhere_is_busy`

**What it does:** With a budget run's `running` row and no lock held, the run is `busy`, the model isn't called and nothing is stored.

**Why it's there:** `user_lock` covers one process, and the CLI is its own. The lock tests alone passed while a real overlap went through.

## `test_stale_running_row_does_not_block`

**What it does:** A `running` row three hours old doesn't stop a run.

**Why it's there:** It's left over from a process that died, and it mustn't block the user for good.

## `test_a_goal_run_counts_as_running_elsewhere`

**What it does:** A goal run's `running` row counts for `running_elsewhere`.

**Why it's there:** A simulation shares this check, and now sees goal runs, and two goal runs can't overlap.

## `test_interrupted_run_closes_its_row`

**What it does:** Ctrl-C during the model call re-raises, the row is `error` with reason `Interrupted`, and the lock is released.

**Why it's there:** `except Exception` doesn't catch it, so the row stayed `running` and would then have blocked the user's runs for two hours.

## `test_startup_closes_a_dead_goal_run`

**What it does:** `close_interrupted_runs` closes a goal run's `running` row.

**Why it's there:** A killed process can't close its own row. This covers it when the API next starts.

## `test_cli_refuses_prefs_that_are_not_an_object`

**What it does:** `--prefs` of `[]`, `""` or `5` prints `bad --prefs` and exits 1.

**Why it's there:** The first two gave a traceback.

## `test_cli_unknown_user`

**What it does:** An unknown `--user` says so and exits 1.

**Why it's there:** It gave a traceback, from a foreign-key failure on the real database.

## `test_card_minimums_come_out_of_the_surplus`

`card_user(db, spend)` seeds $3,000 of income, that month's spending, a card with a $95 minimum, and its $95 payment from checking.

**What it does:** With $2,700 of spending, the report says `surplus $205.00 a month after $95.00 of card minimums; goals may use $164.00`, and the run summary records all three.

**Why it's there:** Found in the final review. Card payments aren't spending, so this user measured $300 and could be given a $240 goal with $205 in hand.

## `test_card_minimums_can_leave_no_surplus`

**What it does:** $50 measured less a $95 minimum is `no_surplus` at −$45, with no model call.

**Why it's there:** A user whose minimums take everything has nothing to allocate.
