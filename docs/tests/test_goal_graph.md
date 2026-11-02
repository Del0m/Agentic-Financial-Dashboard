# Tests: `backend/tests/test_goal_graph.py`

These tests cover `app/agents/goal_graph.py` and `goal_llm.py` (TPC-23): the graph `load_facts → propose → build → persist`, `run_goals`, and the CLI. Code measures the surplus and computes each goal's target and date; the model picks the goals, their order and their monthly amounts.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_goal_graph.py -v
```

## Shared setup

- `audit_in_tmp` (autouse) keeps the prompt audit log under `tmp_path`.
- The `user` fixture seeds `goals-card-24` (`eval/budget_users.py`): a $1,276.75 monthly surplus, so a $1,021.40 cap at the default 80%, a $3,200 card at 24.99%, an $18,400 student loan and a $1,000 savings account. `TODAY` is its date, 2026-11-02, so plans start on 2026-12-01.
- `g(kind, cents, strategy, why)` is one proposed goal; `model(*goals)` is a `ScriptedChatModel` that answers with them as JSON.
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

## `test_unusable_goals_dropped_and_listed`

**What it does:** Savings is skipped by the preferences and `debt_payoff` has no strategy, so both are dropped and listed in the state and the run summary. The emergency fund is stored, renumbered to priority 1.

**Why it's there:** Code keeps what it can compute. The run shows what was thrown away, so a pattern in the model's mistakes can be seen.

## `test_repeated_kind_dropped`

**What it does:** Two savings goals: the first is kept, the second dropped as `repeat`.

**Why it's there:** The prompt asks for at most one of each kind, and code doesn't rely on the model following that.

## `test_nothing_usable_is_an_error`

**What it does:** A run whose only goal is unusable ends in `error`, reason `no_usable_goals`, and the earlier proposal stays `proposed`.

**Why it's there:** A bad run mustn't wipe out a good proposal.

## `test_bad_json_twice_is_an_error`

**What it does:** The model answers with junk twice: `error`, reason `bad_model_output`, exactly two calls, nothing written.

**Why it's there:** `invoke_structured` gets one retry on bad JSON; after that the run reports it. TPC-24 adds retries with a reason.

## `test_model_outage_recorded`

**What it does:** The model call raises `ConnectionError`: `run_goals` returns `error` with that name, the row says the same, and nothing is written.

**Why it's there:** The CLI and TPC-25's route get a result to report, and a run row is never left as `running`.

## `test_prompt_gets_the_gateway_context`

**What it does:** The model's second message holds the gateway's `goals` context, with the $1,021.40 cap and `debt_payoff` offered. The system prompt carries the 8% rule, and one `goals` audit line exists.

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

## `test_never_goal_stored_without_a_date`

**What it does:** A $1 extra on a $10,000 card at 30% with a $100 minimum is stored with no date and the full target.

**Why it's there:** It's plan Review Focus 3: the model chooses the amount, so a plan that never ends can reach storage.

## `test_busy_when_the_lock_is_held`

**What it does:** With the user's lock held, the status is `busy` and no run row is written.

**Why it's there:** It's plan Review Focus 4: a goal run reads the money a budget run or close may be changing.

## `test_lock_released_after_a_run`

**What it does:** The lock is free again after a run.

**Why it's there:** A leaked lock would mark the user busy for the budget run too, until the server restarts.

## `test_cli_prints_goals`

**What it does:** The report shows the surplus, the cap, and each goal's kind, strategy, monthly amount, target, date and rationale.

**Why it's there:** Until TPC-25's page, this is how a person sees what the agent proposed.

## `test_cli_marks_goals_over_the_cap`

**What it does:** A $1,000 debt goal fits the $1,021.40 cap; the $100 savings goal after it pushes the total over, and only that line says `(over cap)`.

**Why it's there:** TPC-24 will refuse these. Until then the report makes them visible.

## `test_cli_says_when_a_goal_has_no_end`

**What it does:** A never-ending debt goal prints "no end date".

**Why it's there:** The other half of Review Focus 3: printing a `None` date must not crash the report.

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
