# Tests: `backend/tests/test_recurring_engine.py`

These tests cover `app/budget_engine/recurring.py` (TPC-33): the pure detector that finds repeating charges and paychecks, and the functions that project and normalise them. There is no database or clock.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_recurring_engine.py -v
```

## Shared setup

- `txn`, `monthly`, `every` and `_gapped` build `RTxn` lists. Amounts are Plaid-signed: positive is money out.
- `one(txns, today)` runs `detect` and asserts exactly one stream.
- `stream(**kw)` builds a `Stream` directly, for the `project` tests.

## `test_two_visits_make_no_stream`

**What it does:** Two charges at a merchant make no stream.

**Why it's there:** The ticket's done-when: no stream for a merchant with 2 visits.

## `test_three_monthly_charges_make_a_stream`

**What it does:** Three monthly charges give a monthly stream with the right amounts, next date and status.

**Why it's there:** The basic case every later rule builds on.

## `test_gaps_of_5_and_80_days_make_no_stream`

**What it does:** Three charges with wildly different gaps make no stream.

**Why it's there:** Their median gap fits no cadence.

## `test_next_date_uses_the_median_day_of_the_month`

**What it does:** Jul 31, Aug 31, Sep 30 gives anchor 31 and a next date of Oct 31.

**Why it's there:** A short month must not drag a 31st bill to the 30th.

## `test_a_weekend_shifted_bill_keeps_its_day`

**What it does:** Aug 3, Sep 1, Oct 1 gives anchor 1 and a next date of Nov 1.

**Why it's there:** One weekend shift must not move the bill's day.

## `test_biweekly_pay`

**What it does:** Six paychecks 14 days apart give a biweekly inflow of $1,850.

**Why it's there:** Biweekly pay is part of the ticket's done-when.

## `test_one_weekday_means_biweekly_and_two_anchors_mean_semi_monthly`

**What it does:** Same-weekday charges are biweekly; charges on the 1st and 15th are semi-monthly, with both anchors.

**Why it's there:** The two cadences have nearly the same median gap, and a wrong class gives wrong dates.

## `test_price_rise_of_6_7_percent_keeps_the_stream_fixed`

**What it does:** $30 x3 then $32 keeps a fixed stream with a previous price of $30.

**Why it's there:** A small rise must still alert (departure 1).

## `test_price_rise_of_15_percent_keeps_the_stream_fixed`

**What it does:** $30 x3 then $34.50 keeps the typical amount and sets the previous price.

**Why it's there:** Under the ticket's rule this rise made the stream `amount_varies`, and no alert could fire.

## `test_second_charge_at_the_new_price_is_not_a_rise`

**What it does:** $30, $30, $34.50, $34.50 has no previous price.

**Why it's there:** Only the first charge at a price may alert.

## `test_a_price_step_keeps_the_stream`

**What it does:** $30 x4 then $34.50 x2 stays a monthly stream with $34.50 as its typical amount.

**Why it's there:** Found in the design review: without a step, the dropped charge left a 60-day gap and the stream read as stopped.

## `test_a_small_rise_alerts_only_on_its_first_charge`

**What it does:** $40 x3 then $43 has a previous price; a second $43 doesn't.

**Why it's there:** A rise under 10% stays in the cluster, so nothing else stops it alerting again.

## `test_a_lower_price_that_repeats_keeps_the_stream`

**What it does:** $30 x4 then $25 x2 stays a stream at $25 with no previous price.

**Why it's there:** A price cut would otherwise look like a stream ending.

## `test_a_rise_over_50_percent_counts_on_its_second_charge`

**What it does:** $30 x4 then $50 is treated as a one-off; a second $50 is a step with a previous price of $30.

**Why it's there:** One charge that far off can't be told from a one-off, but the rise must still alert.

## `test_a_price_that_falls_back_and_rises_again_is_a_rise`

**What it does:** $30, $34.50, $30 x3, $34.50 has a previous price of $30.

**Why it's there:** A promo price that returns is a new rise.

## `test_a_one_off_extra_is_dropped`

**What it does:** $120 x4 plus a $520 one-off leaves four charges at $120.

**Why it's there:** Under the ticket's rule the one-off read as a 333% rise.

## `test_a_small_extra_between_bills_is_dropped`

**What it does:** A $5 charge between $15.99 bills is ignored.

**Why it's there:** It had become the stream's last amount.

## `test_one_offs_a_month_apart_are_not_a_stream`

**What it does:** Airline charges of $200, $1,200 and $250 about 30 days apart make no stream.

**Why it's there:** They passed as monthly until the 3x guard.

## `test_a_utility_with_varying_amounts_is_a_stream`

**What it does:** A varying power bill is a stream, with `amount_varies` set.

**Why it's there:** Utilities are real bills, even without a fixed amount.

## `test_weekly_food_with_fixed_amounts_is_not_a_stream`

**What it does:** Thirteen weekly food charges at one amount make no stream.

**Why it's there:** Weekly habits are everyday spending (departure 2).

## `test_a_weekly_service_with_fixed_amounts_is_a_stream`

**What it does:** Six fixed weekly charges outside the everyday categories are a stream.

**Why it's there:** The short-gap rule must not exclude real commitments.

## `test_a_weekly_outflow_with_varying_amounts_is_not`

**What it does:** Six weekly charges with varying amounts make no stream.

**Why it's there:** Short-gap outflows must have fixed amounts.

## `test_a_weekly_gap_that_slips_by_3_days_is_not_a_stream`

**What it does:** Gaps of 7, 7, 10, 7 make no stream.

**Why it's there:** Short-gap outflows need every gap within 2 days of the median.

## `test_a_skipped_month_keeps_the_stream`

**What it does:** Charges in Apr, May, Jul and Aug stay an active monthly stream, next due Sep 5.

**Why it's there:** Found in the design review: one missed charge staled the stream on the day it resumed.

## `test_a_skip_needs_four_charges`

**What it does:** Aug, Sep and Nov make no stream.

**Why it's there:** Three charges with a skip aren't enough evidence.

## `test_two_skipped_months_make_no_stream`

**What it does:** Apr, May, Jul, Sep and Oct make no stream.

**Why it's there:** Only one skip is allowed.

## `test_a_longer_break_restarts_the_series`

**What it does:** After a 76-day break, only the charges after it count: three make a stream and two don't.

**Why it's there:** A stream can come back without waiting for the break to leave the window.

## `test_a_skipped_week_restarts_a_weekly_service`

**What it does:** Gaps of 7, 7, 14, 7, 7, 7 give a weekly stream of 4 charges.

**Why it's there:** The restart rule applies to short gaps too; they have no skip allowance.

## `test_a_cafe_visited_at_random_is_not_a_stream`

**What it does:** Irregular food visits make no stream.

**Why it's there:** This is what the ticket's tolerances got wrong on the noisy profile.

## `test_charges_after_today_and_before_the_window_are_ignored`

**What it does:** Only charges in the 180 days up to `today` count.

**Why it's there:** Synthetic users have rows dated after their simulated day.

## `test_a_null_merchant_paycheck_is_a_stream`

**What it does:** Paychecks with no merchant get the key `~INCOME_WAGES`.

**Why it's there:** Plaid often leaves the merchant empty on pay.

## `test_null_merchant_restaurant_charges_are_not`

**What it does:** Purchases with no merchant make no stream.

**Why it's there:** A category fallback made false streams in 424 of 500 seeds (departure 7).

## `test_raw_descriptors_share_a_key`

**What it does:** Descriptors differing by digits or case share a key; a name with no letters has none.

**Why it's there:** The same merchant must group together.

## `test_paycheck_goes_stale_after_65_days`

**What it does:** A monthly paycheck last seen May 15 is active on Jul 19 and stale on Jul 20.

**Why it's there:** This date drives the `income_change` alert.

## `test_a_roommate_transfer_is_an_inflow_stream_but_not_income`

**What it does:** A monthly transfer in is an inflow stream that keeps its category.

**Why it's there:** The forecast needs it, but it mustn't raise an income alert.

## `test_a_refund_is_its_own_direction`

**What it does:** A refund doesn't change the outflow stream.

**Why it's there:** Money in and money out are grouped apart.

## `test_project_clamps_a_31st_bill`

**What it does:** A 31st bill lands on Jan 31, Feb 28, Mar 31 and Apr 30.

**Why it's there:** February must not move later months.

## `test_project_semi_monthly_uses_both_anchors`

**What it does:** Anchors 1 and 15 give Jun 15, Jul 1 and Jul 15.

**Why it's there:** The forecast places both charges.

## `test_project_steps_a_fixed_cadence`

**What it does:** A biweekly stream steps by 14 days.

**Why it's there:** Weekly and biweekly streams have no anchors.

## `test_monthly_cents_normalises`

**What it does:** $10 is $10, $21.67, $20 and $43.33 a month for monthly, biweekly, semi-monthly and weekly.

**Why it's there:** The surplus compares streams on one monthly scale.
