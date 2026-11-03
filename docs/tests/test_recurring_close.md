# Tests: `backend/tests/test_recurring_close.py`

Run from `backend/`: `../venv/bin/python -m pytest tests/test_recurring_close.py -v`.

## Shared setup

`cancel()` uses the existing close fixtures to create an October budget, a $15.99 Streamly stream and a cancellation suggestion. Databases and dates are explicit; no model or Plaid calls run.

## `test_accepting_suggest_cancel_lowers_the_line_at_close`

**What it does:** An accepted cancellation reduces a $100 line to $85 and records an applied outcome.

**Why it's there:** Cancellation subtracts the monthly stream amount, rounded up to $5, before style allocation.

## `test_an_accepted_adjust_and_an_accepted_cancel_both_apply_in_order`

**What it does:** An accepted $120 adjustment followed by cancellation produces $105.

**Why it's there:** An adjustment must not overwrite the accepted cancellation.

## `test_a_category_with_no_line_is_unchanged_and_the_outcome_is_recorded`

**What it does:** Records `no_line` without creating a category.

**Why it's there:** Close must not invent a budget line for an old suggestion.

## `test_a_limit_the_cancel_would_take_to_zero_is_left_alone`

**What it does:** Records `nonpositive` when the subscription exceeds the line, leaving normal adaptation in place.

**Why it's there:** Cancellation must never zero or make a line negative.

## `test_a_pending_suggest_cancel_survives_month_end`

**What it does:** Keeps a pending suggestion answerable after October and applies a later acceptance at November close.

**Why it's there:** Cancellation proposals do not expire like budget adjustments.

## `test_a_muted_stream_raises_no_more_signals`

**What it does:** An accepted stream receives a higher charge; its stored row updates but no change signal is returned.

**Why it's there:** Muting alerts must preserve accurate recurring data.

## `test_decline_changes_nothing`

**What it does:** A dismissed suggestion produces neither a close outcome nor a muted stream.

**Why it's there:** Only the user's acceptance changes the budget.

## `test_a_cancellation_is_applied_only_once_across_months`

**What it does:** Closes two months and verifies that the second has no cancellation outcome.

**Why it's there:** The same subscription must not be subtracted every month.
