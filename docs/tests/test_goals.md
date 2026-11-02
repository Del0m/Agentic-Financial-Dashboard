# Tests: `backend/tests/test_goals.py`

These tests cover `app/budget_engine/goals.py` (TPC-23), the arithmetic behind a goal: what the user has left each month, what an emergency fund still needs, and a goal's target and date. The LLM picks the goals and their monthly amounts; everything else is computed here.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_goals.py -v
```

## Shared setup

- `TODAY` is 2026-11-02 and `START`, the first planned month, is 2026-12-01.
- `T(y, m, d, cents, cat)` builds a `Txn`.
- `months(spend, saved)` gives August to October with $4,200 of income, that month's spending, and $500 to savings. By default that leaves $700, $400 and $900.
- `needs_months()` has rent of $1,200, $1,500 and $1,300, plus a $900 food purchase that isn't a need here.
- `DEBTS` is a $3,200 card at 24.99% and an $18,400 loan at 5.5%.

---

## `test_surplus_is_the_median_month`

**What it does:** $700, $400 and $900 left gives a surplus of $700.

**Why it's there:** The surplus is measured from what actually happened, and the median keeps one odd month from setting it (spec decision).

## `test_refund_nets_its_category`

**What it does:** A $100 refund in August makes August's $700 into $800, the new median.

**Why it's there:** A returned purchase isn't spending, the same rule as the budget's totals.

## `test_savings_transfers_subtract`

**What it does:** Without transfers to savings, each month has $500 more, so the surplus is $1,200.

**Why it's there:** Money already going to savings isn't free for a new goal.

## `test_partial_first_month_skipped`

**What it does:** History that starts on Aug 15 only counts September and October: the median of $400 and $900 is $650.

**Why it's there:** A partial month would look like a month with little spending. It's the same rule `fit` uses, so both see the same months.

## `test_negative_month_stays_negative`

**What it does:** Spending $5,000 a month leaves −$1,300, and the surplus says so.

**Why it's there:** A month in the red is evidence against committing money. Flooring it at $0 would hide how far under the user is.

## `test_no_complete_month_is_none`

**What it does:** Transactions only in the current month, or none at all, give `None`.

**Why it's there:** "We can't measure it yet" isn't the same as "$0 left". The graph stops either way, and records which.

## `test_gap_is_three_needs_months_minus_savings`

**What it does:** Median needs month $1,300 × 3 is $3,900, less $1,000 saved: a $2,900 gap. The food purchase doesn't count.

**Why it's there:** The emergency-fund target comes from code, from the user's own needs spending.

## `test_gap_floored_at_zero`

**What it does:** With $5,000 saved, the gap is $0.

**Why it's there:** A covered fund has nothing left to save. It's then not offered as a goal.

## `test_debt_goal_matches_the_planner`

**What it does:** A `debt_payoff` goal at $200 a month has the planner's debt-free date for that extra and strategy, and targets the debts' $21,600 total.

**Why it's there:** The goal and the dashboard's payoff card must agree, since both come from `payoff.simulate`.

## `test_debt_target_leaves_out_what_the_planner_does`

**What it does:** A mortgage, a debt with no APR, and an overpaid card don't count toward the target.

**Why it's there:** The target must be what the plan actually pays off. The planner's own rules decide, so they stay in one place.

## `test_never_debt_goal_has_no_date`

**What it does:** A $1 extra on $10,000 at 30% with a $100 minimum never pays off, so the goal has no date. Its target is still the balance.

**Why it's there:** It's plan Review Focus 3. The model chooses the amount, so this can happen; it must not crash.

## `test_emergency_date_rounds_up`

**What it does:** $1,000 at $300 a month takes 4 payments, so the date is March 2027.

**Why it's there:** The goal isn't met until the last partial payment lands.

## `test_emergency_exact_months`

**What it does:** $1,000 at $250 a month is exactly 4 payments, also March 2027.

**Why it's there:** An exact division must not add a month.

## `test_savings_is_a_year`

**What it does:** $100 a month targets $1,200, dated November 2027, 12 payments from December.

**Why it's there:** Savings has no named purchase behind it, and every goal must be dated (TPC-24), so it's defined as a year of saving.

## `test_offered_kinds`

**What it does:** All three kinds with debts and a gap. No debts, or only a debt the planner can't plan, means no `debt_payoff`. No gap means no `emergency_fund`. `skip_kinds` removes a kind.

**Why it's there:** The model is only offered what code can compute a target and date for, and what the user hasn't ruled out.

## `test_emergency_fund_past_the_planners_stop_has_no_date`

**What it does:** A $100,000 gap at $1 a month keeps its target and has no date.

**Why it's there:** 100,000 months is past year 9999, which crashed the run (final review). Past the planner's 600 months, it's "no end date", the same as a debt plan that never ends.
