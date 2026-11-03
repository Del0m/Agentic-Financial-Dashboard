# Tests: `backend/tests/test_forecast_accuracy.py`

Run from `backend/`: `../venv/bin/python -m pytest tests/test_forecast_accuracy.py -v`.

## Shared setup

These offline tests use explicit dates and integer cents. Database cases use a fresh in-memory SQLite session; model cases use scripted replies and temporary audit/checkpoint paths. No live model or Plaid request runs. Helpers and fixtures in the test file define the planted inputs.

## `test_the_planted_dip_is_found_at_least_3_days_ahead_and_names_rent`

**What it does:** The planted dip is found at least 3 days ahead and names rent, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_balance_on_matches_the_seeded_account`

**What it does:** Balance on matches the seeded account, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_mae_is_finite_and_coverage_is_a_share`

**What it does:** Mae is finite and coverage is a share, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_the_forecast_beats_a_flat_balance_at_d14_on_the_dip_user`

**What it does:** The forecast beats a flat balance at d14 on the dip user, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_results_json_has_the_documented_keys`

**What it does:** Results json has the documented keys, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_only_forecast_users_are_scored`

**What it does:** Only forecast users are scored, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_existing_budget_accuracy_numbers_are_unchanged`

**What it does:** Existing budget accuracy numbers are unchanged, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.
