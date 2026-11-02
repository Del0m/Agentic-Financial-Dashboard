# Tests: `backend/tests/test_goal_validation.py`

TPC-24 validation coverage. Run from `backend/` with `../venv/bin/python -m pytest tests/test_goal_validation.py -q`.

## `test_monthly_amount_rounds_half_up_once`

**What it does:** Checks 1.004, 1.005, and 10.125 dollars become 100, 101, and 1013 cents.

**Why it's there:** The checked allocation must equal the stored allocation, including half-cent ties.

## `test_missing_debt_strategy_defaults_to_avalanche`

**What it does:** Missing debt strategy becomes avalanche; savings strategy is cleared.

**Why it's there:** Retains the existing default and avoids meaningless non-debt strategies.

## `test_combined_exact_cap_passes_and_one_cent_over_fails`

**What it does:** Two rounded amounts fit 2002 cents but exceed 2001 by one cent; feedback names total, cap, and excess.

**Why it's there:** Combined affordability and per-goal rounding must be exact.

## `test_duplicate_and_unoffered_kinds_report_all_failures`

**What it does:** A mixed invalid proposal reports repeat, not_offered, and over_cap with the repeated goal index.

**Why it's there:** The correction should address all independently computable problems.

## `test_monthly_bounds_reject_unsafe_allocations`

**What it does:** Rejects 0, 99, and 100000001 cents.

**Why it's there:** Protects pure-engine callers and avoids zero-amount date division.

## `test_proposal_count_is_bounded`

**What it does:** Rejects empty and four-goal allocations.

**Why it's there:** Keeps the engine consistent with the one-to-three-goal schema.

## `test_computed_dated_plans_pass`

**What it does:** Accepts builder results for all three goal kinds.

**Why it's there:** Validation must preserve existing target/date arithmetic.

## `test_emergency_horizon_boundary`

**What it does:** Accepts 600 payments and rejects 601 with no_date.

**Why it's there:** Prevents an off-by-one at the planner horizon.

## `test_non_amortizing_debt_is_rejected`

**What it does:** Rejects a $1 extra on a non-amortizing 30% card.

**Why it's there:** A positive monthly amount alone does not prove a dated plan is possible.

## `test_invalid_target_and_past_or_outside_date_are_rejected`

**What it does:** Rejects a zero target and dates before start or beyond the horizon.

**Why it's there:** Checks computed plan invariants before publication.
