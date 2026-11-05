# Tests: `backend/tests/test_goal_validation_graph.py`

TPC-24 validation coverage. Run from `backend/` with `../venv/bin/python -m pytest tests/test_goal_validation_graph.py -q`.

Uses the TPC-23 synthetic profile and in-memory database; scripted replies require no running model. `-k "not live"` excludes the five Ollama trials.

## `test_tight_surplus_retries_to_affordable_dated_goals`

**What it does:** At a $48.59 cap, rejects $50 and accepts a corrected $48.59 allocation. Checks stored dates, two calls, history, scrubbed feedback, and exact correction audit payload.

**Why it's there:** This is the ticket acceptance case with a deliberate model mistake.

## `test_tight_surplus_exhaustion_preserves_previous_proposal`

**What it does:** Two over-cap replies fail without new IDs/plans/rows or superseding the older proposal.

**Why it's there:** Exhaustion must be safe and preserve the selected failure policy.

## `test_parsing_and_affordability_share_two_calls`

**What it does:** Exercises malformed-then-over-cap and over-cap-then-malformed; never calls a third time. Raw invalid text is absent from feedback/history.

**Why it's there:** Prevents multiplying lower-level and graph retries or echoing raw output.

## `test_malformed_then_valid_uses_one_correction`

**What it does:** A malformed reply then valid savings succeeds with exactly two calls.

**Why it's there:** Parsing errors must be correctable within the same budget.

## `test_invalid_kind_or_missing_date_retries_whole_proposal`

**What it does:** A repeated kind or an emergency allocation taking over 600 months is replaced by a complete valid proposal.

**Why it's there:** No valid subset from the first reply should leak into the second.

## `test_preference_excluded_kind_retries_without_partial_storage`

**What it does:** Skips savings in preferences, rejects a mixed initial reply, and stores only the corrected debt goal.

**Why it's there:** Structured preferences are enforced, not advisory.

## `test_all_computable_failures_reported_together`

**What it does:** One reply violates uniqueness, total cap, and achievable date; all three failures reach history.

**Why it's there:** The sole correction call needs complete actionable feedback.

## `test_debt_snapshot_does_not_reload_during_correction`

**What it does:** Changes a card balance after facts load. The corrected target and both contexts retain the original snapshot.

**Why it's there:** Amounts, model facts, and dates must describe the same finances.

## `test_outage_after_rejection_keeps_evidence_and_no_third_call`

**What it does:** A connection error on correction records two calls and the first failure, with no raw exception text in the run summary.

**Why it's there:** Outages must not erase useful evidence or lead to more calls.

## `test_cap_below_minimum_withdraws_without_model`

**What it does:** At 0 and 99 cents, the CLI reports no affordable goals, withdraws prior goals, and records zero calls.

**Why it's there:** No schema-valid goal can fit; the model must not invent room.

## `test_exact_minimum_cap_allows_one_call`

**What it does:** A $1 cap accepts a $1 savings goal in one call.

**Why it's there:** The short-circuit boundary is strictly below $1.

## `test_cli_failure_explains_exhaustion_and_preservation`

**What it does:** Exits 1 with total/cap, two calls, and an explicit no-replacement/preserved-earlier-proposal message.

**Why it's there:** The CLI must distinguish failure from publishing the earlier proposal anew.

## `test_api_reflects_correction_preservation_and_withdrawal`

**What it does:** Reads GET /goals after exhausted retries, a successful correction, and an impossible-cap withdrawal. It points the routes' session factory (`budget_hooks`) at the test database, because `GET /goals` reads the hooks since TPC-25.

**Why it's there:** The storage policy must be visible through the existing API.

## `test_live_tight_surplus_trials`

**What it does:** Five parameterized real-model runs at a $48.59 cap print outcome, calls, total, and dates. Each must produce a valid proposal; skipped if the configured model is unavailable.

**Why it's there:** Measures real-model usefulness separately from deterministic safety.

## `test_new_goal_cannot_spend_accepted_money`

**What it does:** With a $583 accepted goal the cap is $194.40. A scripted $194.41 proposal is rejected as `over_cap` on both calls, the run is an `error`, and nothing is stored.

**Why it's there:** Validation checks the cap net of accepted goals, so a retry can't spend their money either (TPC-25).

## `test_retries_off_stops_after_one_goal_call`

**What it does:** With `RETRIES_ENABLED` off, a malformed first reply ends the run after one model call, with no correction.

**Why it's there:** TPC-27's retry ablation has to turn this mechanism off for real.
