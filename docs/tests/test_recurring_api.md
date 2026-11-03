# Tests: `backend/tests/test_recurring_api.py`

Run from `backend/`: `../venv/bin/python -m pytest tests/test_recurring_api.py -v`.

## Shared setup

The in-memory database contains the configured development user, stored streams and explicit October run dates. API requests use the test session. No detector, Plaid or model is called.

## `test_recurring_lists_changed_then_active_then_stale`

**What it does:** Checks the as-of date and changed, active, then stale ordering.

**Why it's there:** Changed streams should be easy to find without hiding stopped ones.

## `test_an_inflow_label_is_income_and_never_the_employer`

**What it does:** Returns Income for an inflow and excludes the employer name.

**Why it's there:** API responses do not need paycheck counterparties.

## `test_a_user_with_no_history_gets_an_empty_list`

**What it does:** Returns 200 with a null as-of date and no streams.

**Why it's there:** A new user needs an empty state rather than an error.

## `test_the_get_writes_nothing`

**What it does:** Compares stream, run and alert counts before and after GET.

**Why it's there:** Reading the dashboard must not trigger detection or alerts.

## `test_another_users_streams_are_not_visible`

**What it does:** Seeds a second user and excludes their stream.

**Why it's there:** The current-user dependency scopes every read.

## `test_a_price_increase_alert_marks_the_stream`

**What it does:** Returns the price-change badge and both stored amounts.

**Why it's there:** The UI needs the old and new prices without parsing alert text.

## `test_requires_auth`

**What it does:** Overrides the current-user dependency to deny access and expects 401.

**Why it's there:** The endpoint must use the shared identity dependency; the app still uses development authentication.

## `test_alerts_out_carries_stream_id`

**What it does:** Checks that GET /alerts exposes its recurring stream link.

**Why it's there:** Recurring decisions must remain tied to a stable stream id.

## `test_change_badges_use_simulated_run_dates_and_expire`

**What it does:** Links advice to a simulated run and verifies its badge expires after 30 simulated days.

**Why it's there:** Wall-clock creation times must not make simulation badges permanent.
