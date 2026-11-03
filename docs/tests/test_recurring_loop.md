# Tests: `backend/tests/test_recurring_loop.py`

Run from `backend/`: `../venv/bin/python -m pytest tests/test_recurring_loop.py -v`.

## Shared setup

Each test seeds recurring-planted through Sep 30 and advances the real daily simulation through Oct 25 with a schema-aware scripted model. The model suggests cancellation on signals and avoids repeated overspend alerts. API identity and audits use the throwaway database.

## `test_every_planted_stream_is_detected`

**What it does:** GET /recurring lists the six planted streams, with generic Income and a biweekly cadence.

**Why it's there:** Verifies the complete recurring seam from seeded charges through real daily runs and persisted results.

## `test_the_gym_price_alert_fires_on_the_run_after_the_higher_charge`

**What it does:** No gym price alert exists Oct 2; exactly one with $40 and $43 exists from Oct 3 through Oct 25.

**Why it's there:** Verifies the complete recurring seam from seeded charges through real daily runs and persisted results.

## `test_a_rise_over_ten_percent_also_alerts`

**What it does:** Clipflix first alerts on Oct 20, despite its rise exceeding 10%.

**Why it's there:** Verifies the complete recurring seam from seeded charges through real daily runs and persisted results.

## `test_the_new_subscription_alert_fires_when_its_third_charge_posts`

**What it does:** NewFlix alerts on its third charge, Oct 12, and older streams do not produce a subscription flood.

**Why it's there:** Verifies the complete recurring seam from seeded charges through real daily runs and persisted results.

## `test_a_merchant_with_two_visits_is_never_a_stream`

**What it does:** Pottery Studio and everyday Grocer and Fuel remain absent from the recurring API.

**Why it's there:** Verifies the complete recurring seam from seeded charges through real daily runs and persisted results.

## `test_accepting_a_cancel_suggestion_reaches_next_months_line`

**What it does:** Accepts the Clipflix suggestion, advances past close, and checks the next line against the recorded cancellation outcome and rounded monthly subtraction.

**Why it's there:** Verifies the complete recurring seam from seeded charges through real daily runs and persisted results.
