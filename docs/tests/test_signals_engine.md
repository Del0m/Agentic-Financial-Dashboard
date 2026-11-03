# Tests: `backend/tests/test_signals_engine.py`

These tests cover the pure signal rules in `app/budget_engine/signals.py` (TPC-33): ranking, allowed actions, cancellation limits and fallback copy.

Run from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_signals_engine.py -v
```

## Shared setup

`signal()` makes a price increase for FitClub Gym from $40 to $43. Individual tests replace its kind, category or amounts. No database, clock or model is used.

## `test_cancel_limit`

**What it does:** Checks five cases: subtract the monthly charge, round up to $5, and return `None` when the subtraction reaches zero or below.

**Why it's there:** A cancellation proposal must never zero a line or turn it negative.

## `test_rank_puts_low_balance_then_income_then_price_then_new`

**What it does:** Orders mixed signals by kind, then by the larger charge within a kind.

**Why it's there:** Only three signals get model calls, so the most urgent changes go first.

## `test_rent_and_transfers_cannot_be_cancelled`

**What it does:** Parameterizes all seven protected categories. Their only allowed action is `alert`, and a proposed `suggest_cancel` is downgraded.

**Why it's there:** The code, rather than the model, enforces essential-bill and transfer exclusions.

## `test_income_change_only_alerts`

**What it does:** Checks that an income change permits only `alert`.

**Why it's there:** A paycheck is never something to cancel.

## `test_discretionary_stream_can_be_cancelled_and_unknown_actions_downgrade`

**What it does:** Allows a gym cancellation suggestion but rejects `adjust_next_budget` on that signal.

**Why it's there:** Signals use their own action set, separate from overspend decisions.

## `test_fallback_price_message_quotes_the_old_and_new_price`

**What it does:** Checks that code-written copy names the gym and quotes $40.00 and $43.00.

**Why it's there:** Model failures must still leave an understandable alert with correct figures.

## `test_repeat_rule`

**What it does:** Repeat rule.

**Why it's there:** Pins the forecast signal guard, privacy boundary or reversible migration behavior.

## `test_a_dip_that_slides_later_by_3_days_does_not_repeat_and_by_4_does`

**What it does:** A dip that slides later by 3 days does not repeat and by 4 does.

**Why it's there:** Pins the forecast signal guard, privacy boundary or reversible migration behavior.

## `test_move_amount`

**What it does:** Move amount.

**Why it's there:** Pins the forecast signal guard, privacy boundary or reversible migration behavior.

## `test_move_from_savings_is_downgraded_without_cover_or_with_no_income`

**What it does:** Move from savings is downgraded without cover or with no income.

**Why it's there:** Pins the forecast signal guard, privacy boundary or reversible migration behavior.

## `test_delay_spend_is_downgraded_without_a_budget`

**What it does:** Delay spend is downgraded without a budget.

**Why it's there:** Pins the forecast signal guard, privacy boundary or reversible migration behavior.
