# Tests: `backend/tests/test_forecast_engine.py`

Run from `backend/`: `../venv/bin/python -m pytest tests/test_forecast_engine.py -v`.

## Shared setup

These offline tests use explicit dates and integer cents. Database cases use a fresh in-memory SQLite session; model cases use scripted replies and temporary audit/checkpoint paths. No live model or Plaid request runs. Helpers and fixtures in the test file define the planted inputs.

## `test_rent_before_payday_is_a_dip_that_names_rent`

**What it does:** Rent before payday is a dip that names rent, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_low_path_equal_to_the_buffer_is_not_a_dip`

**What it does:** A low path equal to the buffer is not a dip, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_the_deepest_low_in_14_days_sets_the_shortfall`

**What it does:** The deepest low in 14 days sets the shortfall, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_an_event_dated_today_is_not_applied_twice`

**What it does:** An event dated today is not applied twice, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_everyday_spending_falls_by_the_mean_not_the_median`

**What it does:** Everyday spending falls by the mean not the median, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_constant_spending_has_no_band`

**What it does:** Constant spending has no band, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_the_band_orders_and_is_symmetric_until_clamped`

**What it does:** The band orders and is symmetric until clamped, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_no_checking_account`

**What it does:** No checking account, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_under_28_days_of_history_is_no_history`

**What it does:** Under 28 days of history is no history, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_no_income_stream_assumes_no_income`

**What it does:** No income stream assumes no income, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_stale_paycheck_falls_back`

**What it does:** A stale paycheck falls back, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_short_history_falls_back`

**What it does:** Short history falls back, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_an_overdue_bill_lands_tomorrow`

**What it does:** An overdue bill lands tomorrow, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_bill_overdue_by_more_than_5_days_rolls_forward`

**What it does:** A bill overdue by more than 5 days rolls forward, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_card_minimum_lands_on_its_due_date_without_a_stream`

**What it does:** Card minimum lands on its due date without a stream, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_card_with_a_payment_stream_adds_no_minimum`

**What it does:** A card with a payment stream adds no minimum, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_card_with_no_due_date_counts_in_the_surplus_only`

**What it does:** A card with no due date counts in the surplus only, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_past_due_date_rolls_forward_by_months`

**What it does:** A past due date rolls forward by months, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_goal_pays_the_day_after_payday`

**What it does:** Goal pays the day after payday, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_goal_with_no_paycheck_pays_on_the_first`

**What it does:** Goal with no paycheck pays on the first, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_goal_not_yet_started_has_no_event_but_is_in_the_surplus`

**What it does:** A goal not yet started has no event but is in the surplus, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_goal_payments_already_in_history_are_not_counted_twice`

**What it does:** Goal payments already in history are not counted twice, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_surplus_is_a_flow_and_ignores_the_balance`

**What it does:** Surplus is a flow and ignores the balance, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_accepted_goals_lower_the_surplus_by_exactly_their_amount`

**What it does:** Accepted goals lower the surplus by exactly their amount, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_cash_above_buffer_is_reported_separately`

**What it does:** Cash above buffer is reported separately, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_monthly_bill_on_the_31st_is_clamped`

**What it does:** Monthly bill on the 31st is clamped, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_goal_cap_on_the_forecast_path`

**What it does:** Goal cap on the forecast path, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_goal_cap_on_the_measured_path_is_unchanged`

**What it does:** Goal cap on the measured path is unchanged, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_crossing_after_a_bill_keeps_the_bill_identity_without_reapplying_it`

**What it does:** Keeps a recently posted bill as the cause of a following-day crossing without applying its charge twice.

**Why it's there:** Covers a failure or calendar seam found in end-to-end review.

## `test_a_goal_does_not_pay_again_after_a_later_paycheck_in_the_month`

**What it does:** Uses the first paycheck in a calendar month, so a biweekly later paycheck cannot schedule the accepted goal twice.

**Why it's there:** Covers a failure or calendar seam found in end-to-end review.
