# Tests: `backend/tests/test_forecast_graph.py`

Run from `backend/`: `../venv/bin/python -m pytest tests/test_forecast_graph.py -v`.

## Shared setup

These offline tests use explicit dates and integer cents. Database cases use a fresh in-memory SQLite session; model cases use scripted replies and temporary audit/checkpoint paths. No live model or Plaid request runs. Helpers and fixtures in the test file define the planted inputs.

## `test_the_dip_alert_names_rent_and_fires_at_least_3_days_ahead`

**What it does:** The dip alert names rent and fires at least 3 days ahead, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_the_same_dip_on_three_days_alerts_once`

**What it does:** The same dip on three days alerts once, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_deeper_dip_alerts_again_and_a_small_change_does_not`

**What it does:** A deeper dip alerts again and a small change does not, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_dismissed_dip_stays_quiet_unless_it_grows`

**What it does:** A dismissed dip stays quiet unless it grows, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_model_message_without_the_bill_is_replaced`

**What it does:** A model message without the bill is replaced, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_severity_is_high_below_zero_and_medium_under_the_buffer`

**What it does:** Severity is high below zero and medium under the buffer, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_user_with_no_budget_gets_the_alert_with_no_period`

**What it does:** A user with no budget gets the alert with no period, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_user_with_no_paycheck_stream_only_gets_an_alert`

**What it does:** A user with no paycheck stream only gets an alert, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_forecast_failure_does_not_stop_overspend_alerts`

**What it does:** A forecast failure does not stop overspend alerts, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_summary_forecast_holds_cents_and_dates_only`

**What it does:** Summary forecast holds cents and dates only, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_paused_checkpoint_holds_no_bill_name_or_balance`

**What it does:** A paused checkpoint holds no bill name or balance, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_normal_day_adds_no_model_call`

**What it does:** A normal day adds no model call, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.
