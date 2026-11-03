# Tests: `backend/tests/test_forecast_loop.py`

Run from `backend/`: `../venv/bin/python -m pytest tests/test_forecast_loop.py -v`.

## Shared setup

These offline tests use explicit dates and integer cents. Database cases use a fresh in-memory SQLite session; model cases use scripted replies and temporary audit/checkpoint paths. No live model or Plaid request runs. Helpers and fixtures in the test file define the planted inputs.

## `test_rent_before_payday_alerts_at_least_3_days_ahead_and_names_rent`

**What it does:** Rent before payday alerts at least 3 days ahead and names rent, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_the_alert_does_not_repeat_after_the_single_urgency_reminder_through_payday`

**What it does:** The alert does not repeat after the single urgency reminder through payday, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_the_next_months_dip_has_a_new_key_and_one_initial_warning`

**What it does:** The next months dip has a new key and one initial warning, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_the_chart_data_shows_the_band_under_the_buffer_at_the_rent_marker`

**What it does:** The chart data shows the band under the buffer at the rent marker, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_raising_the_buffer_redraws_the_forecast_and_keeps_the_alert`

**What it does:** Raising the buffer redraws the forecast and keeps the alert, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_goals_use_the_forecast_and_say_so`

**What it does:** Goals use the forecast and say so, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_user_with_no_paycheck_gets_no_dip_alert_other_than_alert`

**What it does:** A user with no paycheck gets no dip alert other than alert, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.
