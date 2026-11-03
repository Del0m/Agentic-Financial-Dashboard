# Tests: `backend/tests/test_recurring_accuracy.py`

These tests cover `eval/recurring_accuracy.py` and are the acceptance gate for TPC-33's detector (spec departures 2 and 4). A stream counts as planted when a `Monthly` or `Every` pattern has 3 or more charges in the 180 days before the user's run date. A stream found for a `Weekly`, `Once` or `Scatter` pattern is a false positive.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_recurring_accuracy.py -v
```

## Shared setup

- `recurring(pid)` looks up a profile in `RECURRING_USERS`.
- `GATE` is every synthetic user: `PROFILES`, `PLANTED`, `GOAL_USERS` and `RECURRING_USERS`.

## `test_planted_users_are_found_exactly`

**What it does:** `recurring-planted` scores precision and recall of 1.0: its six streams are found, and Pottery Studio, Grocer and Fuel aren't.

**Why it's there:** The ticket's done-when, and the first part of the gate.

## `test_the_noisy_profile_has_at_most_one_false_stream`

**What it does:** `recurring-noisy` has at most 1 false stream and misses nothing.

**Why it's there:** The ticket's tolerances gave 11 false streams of 17 on a noisy profile.

## `test_the_noisy_profile_keeps_its_everyday_spending`

**What it does:** Removing detected streams leaves at least 85% of the purchase days that removing only the planted streams leaves.

**Why it's there:** False streams empty the forecast's everyday estimate.

## `test_every_profile_scores_at_least_0_95`

**What it does:** Pooled precision and recall over every synthetic user are at least 0.95.

**Why it's there:** The gate covers existing users, not only those built for it.

## `test_weekly_habits_and_one_offs_are_never_streams`

**What it does:** Grocer, Fuel, Corner Shop, Airline and Diner aren't streams for any user.

**Why it's there:** Weekly habits stay everyday spending (departure 2).

## `test_monthly_next_dates_are_close`

**What it does:** Over 372 cases, at least 75% of monthly next dates are within 1 day and none is off by more than 4.

**Why it's there:** The ticket's rule gets 53%. Rent on the wrong side of payday flips a low-balance alert.

## `test_rtxns_stop_at_the_as_of_date`

**What it does:** The eval's rows stop at the run date.

**Why it's there:** Synthetic users have rows after it, which would be scoring the future.

## `test_results_json_has_the_documented_keys`

**What it does:** `run` writes `results.json` with the documented keys.

**Why it's there:** Later reports read this shape.
