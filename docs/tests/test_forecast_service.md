# Tests: `backend/tests/test_forecast_service.py`

Run from `backend/`: `../venv/bin/python -m pytest tests/test_forecast_service.py -v`.

## Shared setup

These offline tests use explicit dates and integer cents. Database cases use a fresh in-memory SQLite session; model cases use scripted replies and temporary audit/checkpoint paths. No live model or Plaid request runs. Helpers and fixtures in the test file define the planted inputs.

## `test_buffer_defaults_to_100_dollars_and_reads_either_key`

**What it does:** Buffer defaults to 100 dollars and reads either key, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_set_user_buffer_rejects_bad_values`

**What it does:** Set user buffer rejects bad values, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_setting_the_buffer_replaces_the_forecast_dict`

**What it does:** Setting the buffer replaces the forecast dict, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_two_checking_accounts_are_summed_and_a_card_purchase_is_ignored`

**What it does:** Two checking accounts are summed and a card purchase is ignored, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_available_balance_wins_over_current`

**What it does:** Available balance wins over current, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_a_user_with_no_depository_account_has_no_start_balance`

**What it does:** A user with no depository account has no start balance, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_everyday_series_leaves_out_stream_charges_and_transfers`

**What it does:** Everyday series leaves out stream charges and transfers, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_everyday_series_has_90_entries_ending_today_with_zero_days`

**What it does:** Everyday series has 90 entries ending today with zero days, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_card_obligations_keep_a_null_minimum_null`

**What it does:** Card obligations keep a null minimum null, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_snapshot_writes_nothing`

**What it does:** Snapshot writes nothing, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_snapshot_follows_the_date_it_is_given`

**What it does:** Snapshot follows the date it is given, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_the_dip_user_dips_every_month`

**What it does:** The dip user dips every month, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_the_dip_user_has_a_checking_balance_that_moves`

**What it does:** The dip user has a checking balance that moves, using the explicit fixtures and assertions in the test.

**Why it's there:** Guards this forecast behavior independently of live data and model wording.

## `test_everyday_series_covers_only_the_days_with_history`, `test_everyday_series_is_empty_with_no_history`

**What they do:** 65 days of history give 65 entries, not 90 with 25 zeros, and no history gives an empty series.

**Why they're there:** The forecast can feed goal surplus from 65 days of history, and padding zeros would dilute the daily spending rate.
