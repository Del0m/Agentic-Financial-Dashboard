# Tests: `backend/tests/test_forecast_api.py`

Run from `backend/`: `../venv/bin/python -m pytest tests/test_forecast_api.py -v`.

## Shared setup

These offline tests use explicit dates and integer cents. Database cases use a fresh in-memory SQLite session; model cases use scripted replies and temporary audit/checkpoint paths. No live model or Plaid request runs. Helpers and fixtures in the test file define the planted inputs.

## `test_forecast_for_the_dip_user`

**What it does:** Forecast for the dip user, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_days_param_bounds`

**What it does:** Days param bounds, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_no_checking_account_is_200_with_an_empty_path`

**What it does:** No checking account is 200 with an empty path, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_no_history_is_200`

**What it does:** No history is 200, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_no_paycheck_says_so`

**What it does:** No paycheck says so, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_the_get_writes_nothing`

**What it does:** The get writes nothing, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_simulated_user_gets_the_simulated_date`

**What it does:** A simulated user gets the simulated date, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_another_users_data_is_not_visible`

**What it does:** Another users data is not visible, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_buffer_put_rejects_bad_values`

**What it does:** Buffer put rejects bad values, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_buffer_put_persists_and_the_get_echoes_it`

**What it does:** Buffer put persists and the get echoes it, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_pending_alert_survives_a_buffer_change`

**What it does:** A pending alert survives a buffer change, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_suggested_extra_uses_the_goal_cap`

**What it does:** Suggested extra uses the goal cap, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_requires_auth`

**What it does:** Requires auth, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_get_cash_forecast_returns_a_range_and_no_account_names`

**What it does:** Get cash forecast returns a range and no account names, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.
