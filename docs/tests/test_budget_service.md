# Tests: `backend/tests/test_budget_service.py`

These tests cover the budget tables (TPC-18, architecture section 5) and `app/budget_service/initialize.py`, which builds the user's first budget from history and stores it (architecture 6.3):
- `budget_periods` holds one budget per user per calendar month.
- `budget_lines` holds one limit per category in a period.
- Each period and line keeps the evidence behind it in `fit_json`, so the agent can explain the budget.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_budget_service.py -v
```

## Shared setup

Uses the `db` fixture from `conftest.py` (see [test_plaid.md](test_plaid.md#shared-setup-backendtestsconftestpy)) and `add_user`, `add_account` and `add_txn` from `tests/factories.py`.

- `period(user_id, start)` builds an unsaved October 2026 `BudgetPeriod`.
- `TODAY` is Oct 15 2026, so the fit window is July, August and September 2026.
- `user(s, user_id)` adds a user with one checking account and returns the account.
- `history(s, acct, travel_merchant)` adds three months of food ($400 / $450 / $500), rent ($1,500 on the 1st) and travel ($200, then $1,200 + $700 in August, then $250).
- `counts(db)` returns the number of periods and lines, read in a fresh session.

---

## `test_one_period_per_user_month`

**What it does:** Saving two October periods for the same user raises `IntegrityError`.

**Why it's there:** `initialize_budget` and period close must never open two budgets for one month. The unique `(user_id, start_date)` constraint makes that a database guarantee, which also covers two runs racing each other.

## `test_one_line_per_category`

**What it does:** Saving two FOOD_AND_DRINK lines in one period raises `IntegrityError`.

**Why it's there:** Pace, alerts and period close all look a line up by category. A duplicate would make "the" food limit ambiguous and double count it in totals.

## `test_period_defaults`

**What it does:** A new period is `open` with no `fit_json`. A new line starts at `spent_cents=0` and `last_pace=None`, and is reachable through `period.lines`.

**Why it's there:** These are the states the budget graph (TPC-19) starts from before its first `update_totals`. A period opened by period close has no fit evidence, so `fit_json` must be allowed to be empty.

## `test_alert_proposed_limit`

**What it does:** An alert can be saved without a proposed limit, and with one (16000¢). Both read back correctly.

**Why it's there:** TPC-19 stores the code-computed limit for an `adjust_next_budget` alert here, and TPC-22's close applies exactly that number when the user accepts. Every other alert leaves it empty.

## `test_initialize_creates_period_and_lines`

**What it does:** With the shared history, `initialize_budget` opens an October period. Read back in a new session, it has three `init` category lines: food $472.50 (flexible), rent $1,500 (fixed) and travel $262.50 (flexible), plus the free line (TPC-22) at $127.50, the room the spending fallback leaves since this history has no income. Travel's `fit_json` holds its monthly totals and August as an outlier, with both August purchases as ISO dates and integer cents. The period's `fit_json` lists the three window months.

**Why it's there:** It's the done-when: a user with history gets a sensible first budget. Reading in a fresh session matters because a `date` left inside the JSON would only fail when written, and the identity map would hide it.

## `test_skipped_recorded`

**What it does:** A single $300 general-merchandise purchase in August is listed in the period's `skipped` and gets no line.

**Why it's there:** The agent can only explain "Shopping was left out, you spent there in 1 of 3 months" if the skipped categories are stored.

## `test_load_txns_mapping`

**What it does:** Of seven rows, `load_txns` returns the four that belong to the user, aren't removed and fall in the date range, including both boundary days. A row with `our_category="TRAVEL"` comes back as TRAVEL with its detailed category and merchant, and a row with no category comes back as `UNCATEGORIZED`.

**Why it's there:** Removed rows are soft-deleted Plaid transactions and would double count. Another user's spending must never leak into a budget. Once the `categorize` node writes `our_category`, its choice has to win over Plaid's.

## `test_current_month_ignored`

**What it does:** Initializing on Aug 1 with $100 of food each in May, June and July and $5,000 on Aug 1 gives a $105 food line (and a $0 free line).

**Why it's there:** The current month is partial, even when today is its first day. A purchase on the 1st must not reach the fit.

## `test_second_call_returns_same_period`

**What it does:** Calling `initialize_budget` twice returns the same period, and there is still one period with four lines (three categories and free).

**Why it's there:** The budget graph runs every day and calls this first. It must be safe to call again.

## `test_existing_older_period_returned`

**What it does:** With a closed September period already stored, the call returns it and opens no October period.

**Why it's there:** Architecture 6.2: the budget is initialized only if none exists. Opening the next month belongs to period close (TPC-22), which carries limits forward instead of re-fitting.

## `test_no_history_returns_none`

**What it does:** A user whose only transaction is in the current month gets `None`, and nothing is written.

**Why it's there:** There's no complete month to fit. An empty period would block every later attempt, because the next run would find a budget and stop.

## `test_only_excluded_history_returns_none`

**What it does:** Three months of only paychecks and savings transfers give `None` and no rows.

**Why it's there:** Income and transfers aren't spending. Their history must not count as "history exists" and produce an empty budget.

## `test_all_skipped_returns_none`

**What it does:** History with only one occasional purchase (every category skipped) gives `None` and no rows.

**Why it's there:** Same reason as no history: a period with no lines would stop the next run from trying again once there's real spending.

## `test_outlier_without_merchant`

**What it does:** When the outlier month's purchases have no merchant name, the stored top transaction has `"merchant": null`.

**Why it's there:** Plaid often has no merchant name. Storing the evidence must not crash on it.

## `test_race_returns_existing_period`

**What it does:** An October period already exists, but the first existence check is made to miss it, as if another run committed it just after the check. The commit hits the unique constraint, and the call returns the existing period. Afterwards there's one period and no stray lines.

**Why it's there:** The scheduler and a manual run can start together. The loser must roll back cleanly and use the winner's budget, not crash or leave half a budget behind.

## `test_other_integrity_error_raises`

**What it does:** The commit fails on the unique constraint, but no period can be found afterwards (the lookup is patched to miss). `initialize_budget` re-raises the `IntegrityError` instead of returning `None`.

**Why it's there:** `None` means "not enough history yet", so the daily run would retry forever and nobody would see the real error. Only a race, where another run's period now exists, is safe to swallow (found in the TPC-18 final review).

## `test_load_txns_precedence`

**What it does:** One user, a rule "costco → Food and drink", and six transactions on one day. The categories come out as: the user's own correction (Travel), the rule (beating the LLM's category), the LLM's category, Plaid's category, `SAVINGS_TRANSFER` for a `TRANSFER_OUT_SAVINGS` transfer, and plain `TRANSFER_OUT` for any other transfer. Only the first two are marked `overridden`. `resolve_category` reports their sources as `user`, `rule`, `plaid` and `plaid`.

**Why it's there:** TPC-22 makes `load_txns` the one place a transaction's category is decided (user > rule > LLM > savings transfer > Plaid), so the fit, the pace check, close and the transactions list always agree.

## `test_rule_matches_case_insensitively`

**What it does:** A rule stored as `"costco"` matches a transaction from `"COSTCO "`.

**Why it's there:** Plaid's merchant names vary in case and spacing; "Always for Costco" should still apply.

## `test_init_writes_groups_names_income_total_free`

**What it does:** Initializing the planted `overspend-one-time` user stores income $4,200 and a total of $4,200; a fixed "Savings" line of $500 in the `saving` group; "Rent and utilities" in `needs`; and a "Free" line (`flexible`, `free`, `init`) at $4,200 minus every other line, with `fit_json = {"rule": "remainder"}`.

**Why it's there:** TPC-22's budget model starts at init: every line has a name and a group, and free is the room left in income after the other lines.

## `test_init_503020`

**What it does:** With the user's style set to 50/30/20 before init, the saving line is $840 (20% of $4,200), not the fitted $500, and the style reads back with the default targets.

**Why it's there:** The style is a per-user setting that init honours.

## `test_set_user_style_validates`

**What it does:** A user with no setting reads as `caps` with 50/30/20 targets. Targets that sum to 110 raise "Targets must add up to 100.", an unknown style raises, and valid custom targets are stored.

**Why it's there:** The API and CLIs write the setting through this function, so bad input stops here.

## `test_rule_moves_only_spending`

**What it does:** With a rule "target → General merchandise", a Target paycheck, a transfer in and a card payment keep their own categories and aren't marked overridden; only a Target purchase moves to General merchandise.

**Why it's there:** Found in the TPC-22 review. "Always for Target" means Target purchases. Applied to a paycheck, the rule turned income into negative shopping, so the line could never flag and close lost the user's income.

## Not covered

- **The Alembic migration** is checked by hand, not by pytest: on a scratch SQLite file, `alembic upgrade head`, `alembic check` (no drift from the models), `alembic downgrade -1`, then `upgrade head` again.

## `test_last_pace_is_double`

**What it does:** Checks that `budget_lines.last_pace` is mapped as `Double`.

**Why it's there:** The migration creates the column as `sa.Double()`, but the model left the type to SQLAlchemy. That gives `Double` on 2.1 (installed here) but `Float` on 2.0, which `requirements.txt` (`sqlalchemy>=2`) still allows, and autogenerate or `alembic check` would then report drift. The type is now pinned, the same way `Liability.apr` pins `Float`. The test passes on 2.1 either way; it's a guard against an install that resolves 2.0. The review on #11 found it.

## The history window's edge

`fitted_food(db, *months)` adds one food transaction per `(day, cents)` pair for a new user, runs `initialize_budget` on `TODAY`, and returns the window, the food line's monthly totals and its limit.

`initialize_budget` loads one month before the window it fits. `fit_budget` decides where the history starts from the earliest transaction it's given, so without that month a first in-window transaction after the 3rd looks like the start of the history, and a complete month is dropped.

## `test_longer_history_fits_all_three_months`

**What it does:** Six months of food, April to September, each on the 5th. The window is July, August and September, the totals are $300 / $450 / $500, and the limit is $472.50.

**Why it's there:** This is the bug itself. Before the fix the window was August and September, and the limit was $498.75.

## `test_history_starting_mid_window_month_skips_it`

**What it does:** History really starts on Jul 5. The window is August and September.

**Why it's there:** Loading further back mustn't undo the partial-month rule. With nothing before July, July is still the user's real first month and still only partly covered. This test passes with or without the fix; it guards the rule against the fix.

## `test_history_starting_mid_month_before_window_keeps_all_three`

**What it does:** History starts on Jun 20, then one transaction on the 5th of each month from July to September. The window is all three months and June's $100 isn't in the totals.

**Why it's there:** The boundary of the extra month. June is partial, but it's outside the window, so all it does is show that July is complete.

## `test_rent_paid_just_before_window_counts_for_its_month`

**What it does:** July's rent is paid on Jun 30, with rent on Aug 1 and Sep 1 and food on Jul 2. The rent line's monthly totals are $1,500 for each month.

**Why it's there:** The other thing the extra month changes. A rent paid in the last 3 days of a month counts for the next one, and until the fix a rent paid just before the window wasn't loaded, so the window's first month showed $0.

## Goals in the first budget (TPC-25)

`paid_user(s, income)` adds a user with `history` and a paycheck ($4,200 by default) in July, August and September, so the fitted lines are rent $1,500, food $472.50 and travel $262.50, and free is $1,965 before any goal. `add_goal(s, status, start, run_status, cents)` adds a debt payoff ($583 by default) through Jun 1 2027, by default accepted and starting Oct 1 2026, optionally with a `goals` run in the given status.

## `test_first_budget_includes_goal_already_started`

**What it does:** An accepted goal starting in the first budget's month. Read back in a fresh session, the budget has a "Debt payoff" line in group `goal`, kind `fixed`, $583, `source="user"`, `fit_json` `{rule: "goal"}`. Free is $1,382 ($1,965 − $583), rent is unchanged, the total is income, and no alert is written.

**Why it's there:** With no period to close, the first budget is the only way this goal joins a budget in its start month.

## `test_first_budget_goal_over_income_raises_alert`

**What it does:** A $2,500 goal on top of $2,235 of lines, with $4,200 of income. Free is $0, rent is unchanged, and the total rises to $4,735. One alert on the new period has kind `over_income`, action `alert`, severity `medium`, status `pending_user`, no category or cause, and says the lines are $535 over income.

**Why it's there:** The spec rejects the total rising silently. Close already raises this alert, and a goal in the first budget has to do the same.

## `test_first_budget_without_goals_writes_no_alert`

**What it does:** No goals, with $1,000 of income against $2,235 of lines. The total rises to $2,235, so `apply_style` does report the lines as over income, and no alert is written.

**Why it's there:** Only a first budget with goal lines writes these alerts. A first budget without goals stays exactly as it was.

## `test_first_budget_lost_race_writes_no_alert`

**What it does:** Another run opened the month first, and this run's budget had an over-income goal. No alert is written, and one period remains with no new lines.

**Why it's there:** The alert needs the period's id, so it comes after a flush inside the `try`. The flush is where a lost race now surfaces, and it must not leave an alert behind.

## `test_first_budget_leaves_out_goal_starting_later`

**What it does:** An accepted goal starting next month. There's no line, free stays $1,965, and the goal stays `accepted`.

**Why it's there:** The first close after this budget adds the line. Adding it now would charge a month the plan doesn't start in.

## `test_first_budget_expires_a_stale_proposal`

**What it does:** A proposal starting in the first budget's month, with a paused `goals` run, and another starting next month. Read back in a fresh session, the first is `superseded` with no line and its run `expired` with `finished_at` and `expired_goal_ids`. The second stays `proposed`.

**Why it's there:** A goal can predate the first budget when the user generated before running a budget check. With no period to close, nothing else would retire it. The fresh session shows the expiry went in the budget's own commit.

## `test_no_first_budget_leaves_the_proposal`

**What it does:** With nothing to fit, `initialize_budget` returns `None` and a stale proposal stays `proposed`.

**Why it's there:** `None` means nothing was written and the next run retries.

## `test_first_budget_lost_race_keeps_the_proposal`

**What it does:** Another run opened the month first. `initialize_budget` returns without error, one period remains with no new lines, and the proposal stays `proposed`.

**Why it's there:** The expiry's queries autoflush, so they have to run before the period joins the session, or the unique constraint would fail outside the `try` around the commit. It also shows the expiry rolls back with the budget.
