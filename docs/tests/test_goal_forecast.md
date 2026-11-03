# Tests: `backend/tests/test_goal_forecast.py`

Run from `backend/`: `../venv/bin/python -m pytest tests/test_goal_forecast.py -v`.

## Shared setup

These offline tests use explicit dates and integer cents. Database cases use a fresh in-memory SQLite session; model cases use scripted replies and temporary audit/checkpoint paths. No live model or Plaid request runs. Helpers and fixtures in the test file define the planted inputs.

## `test_a_paycheck_stream_makes_the_forecast_the_source`

**What it does:** A paycheck stream makes the forecast the source, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_the_cap_follows_the_gross_form_around_an_accepted_goal`

**What it does:** The cap follows the gross form around an accepted goal, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_balance_of_100_dollars_or_20000_gives_the_same_surplus`

**What it does:** A balance of 100 dollars or 20000 gives the same surplus, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_the_card_minimum_is_not_subtracted_twice`

**What it does:** The card minimum is not subtracted twice, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_an_unavailable_forecast_falls_back_to_the_measured_figure`

**What it does:** An unavailable forecast falls back to the measured figure, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_the_simulated_date_is_used`

**What it does:** The simulated date is used, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_paused_budget_run_with_an_empty_summary_does_not_matter`

**What it does:** A paused budget run with an empty summary does not matter, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_surplus_at_or_under_zero_with_a_paycheck_is_no_surplus_not_a_fallback`

**What it does:** A surplus at or under zero with a paycheck is no surplus not a fallback, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.
