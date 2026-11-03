# Tests: `backend/tests/test_recurring_service.py`

These tests cover `app/budget_service/recurring.py` (TPC-33). It stores the detector's streams and compares each run with the last, returning the changes that later become alerts. Nothing in it commits; the caller does.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_recurring_service.py -v
```

## Shared setup

- `monthly(db, amounts, first, ...)` adds one charge a month and returns the dates. `_pay` does the same for paychecks.
- `refresh(db, day)` runs `refresh_streams` and commits; `refresh_on_each(db, days)` does it for each day and returns every change.
- `row(db, key, direction)` reads a stored stream.

## `test_first_refresh_stores_streams_and_raises_nothing`

**What it does:** `goals-card-24`'s first refresh stores its 6 streams and returns no changes.

**Why it's there:** Every stream is new to us on a first run, and 8 alerts on day one would be noise (departure 6).

## `test_a_second_refresh_the_same_day_changes_nothing`

**What it does:** A second refresh returns nothing and leaves the rows as they were.

**Why it's there:** A retried daily run must not alert twice.

## `test_a_subscription_that_starts_inside_the_history_is_new`

**What it does:** With history from Jan 1, a subscription first charged Mar 10 is a `new_subscription` on its third charge.

**Why it's there:** This is the alert the ticket asks for.

## `test_a_subscription_older_than_the_history_is_not_new`

**What it does:** A stream starting when the history starts raises nothing.

**Why it's there:** It probably began before the data did.

## `test_a_savings_transfer_is_stored_but_never_new`

**What it does:** A savings transfer is stored but raises nothing.

**Why it's there:** The forecast needs the row, but it isn't a subscription.

## `test_price_increase_fires_once_per_new_price`

**What it does:** Day by day on `recurring-planted`, the gym's rise to $43 alerts once on Oct 3, and not on any day after, including its second charge on Nov 3.

**Why it's there:** The ticket's done-when, and the guard against daily repeats.

## `test_a_rise_of_16_percent_also_fires`

**What it does:** Clipflix's rise from $15.49 to $17.99 alerts.

**Why it's there:** The ticket's rule could not alert on a rise over 10% (departure 1).

## `test_a_charge_under_5_percent_or_under_a_dollar_does_not`

**What it does:** $30 to $31 and $5 to $5.60 raise nothing.

**Why it's there:** Both limits apply together.

## `test_a_price_step_stays_active_and_alerts_once`

**What it does:** $30 x4 then $34.50 x2 alerts once and stays active at $34.50.

**Why it's there:** Found in the design review: the stream used to read as stopped on the second charge.

## `test_a_price_that_falls_back_and_rises_again_alerts_twice`

**What it does:** A rise, a fall back and a later rise to the same price alert twice, with different tokens.

**Why it's there:** A token built from the price would have silenced the second rise forever.

## `test_a_raise_on_a_paycheck_is_not_a_price_increase`

**What it does:** A 6% pay raise raises nothing.

**Why it's there:** Found in the design review: `price_increase` had no direction filter.

## `test_a_growing_card_payment_is_not_a_price_increase`

**What it does:** A larger card payment raises nothing.

**Why it's there:** It isn't spending, and `spending()` already says so.

## `test_a_winter_electric_bill_is_not_a_price_increase`

**What it does:** A metered electric bill's jump raises nothing.

**Why it's there:** It moves with the season, not with a rate change.

## `test_a_phone_bill_rise_and_a_rent_rise_still_fire`

**What it does:** A phone bill and rent, both in the utilities category, still alert.

**Why it's there:** Only metered utilities are excluded; these prices really do rise.

## `test_a_skipped_paycheck_month_raises_nothing`

**What it does:** A paycheck that misses June raises nothing and stays active.

**Why it's there:** Found in the design review: it used to raise a false `income_change` on the day pay resumed.

## `test_income_change_when_the_paycheck_goes_stale`

**What it does:** A paycheck last seen May 15 raises one `income_change` on Jul 20, and none before or after.

**Why it's there:** The 65-day limit, and the alert only fires on the change.

## `test_a_pay_cut_is_an_income_change_on_its_second_charge`

**What it does:** $4,200 x4 then $3,700 x2 raises one `income_change` on the second lower charge, with $4,200 as the earlier amount.

**Why it's there:** A lone lower charge looks like a one-off, so a pay cut is only clear on its second charge.

## `test_a_returning_paycheck_is_the_same_row_and_silent`

**What it does:** After going stale, a paycheck back for three months is active on the same row and raises nothing. It stays stale until the third charge.

**Why it's there:** Stream rows keep their alert links, and pay coming back isn't bad news.

## `test_a_missing_stream_is_marked_stale_not_deleted`

**What it does:** When a stream's transactions are all removed, its row stays and goes stale.

**Why it's there:** Alerts link to the row.

## `test_a_roommate_transfer_going_stale_raises_no_income_change`

**What it does:** A transfer in going stale raises nothing.

**Why it's there:** It's an inflow, but not income.

## `test_pending_removed_and_future_rows_are_ignored`

**What it does:** Pending, removed and future rows don't count towards a stream.

**Why it's there:** A pending charge can still change or vanish.

## `test_signal_key_tokens`

**What it does:** A new stream's token is `first_seen`, and a price rise's is the charge date.

**Why it's there:** Tokens make up `alerts.signal_key`, which stops a signal being written twice.

## `test_plaid_asks_for_180_days`

**What it does:** `create_link_token` and `sandbox_public_token` both send `days_requested` of 180.

**Why it's there:** The docs said 180 but the code sent 90, and the detector's window is 180 days. It only applies to newly linked Items.
