# Tests: `backend/tests/test_recurring_graph.py`

These tests cover TPC-33 Task 4: refreshing recurring streams in the daily budget graph, deciding signals and recording alerts without pausing on them.

Run from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_recurring_graph.py -v
```

## Shared setup

- The `db` fixture provides a fresh in-memory SQLite database. Audits and checkpoint files use `tmp_path`.
- `seed()` creates three monthly $40 gym charges, refreshes the stored stream on Oct 2, then adds the $43 Oct 3 charge and an October budget. Tests may omit the budget or initial refresh.
- `model()` returns a `ScriptedChatModel`, recording each call and returning the supplied JSON. No Plaid or model service is contacted.
- `quiet_pace()` retains real spending totals but suppresses overspend flags, isolating stream decisions. The combined-alert test uses the real pace rules.

## `test_a_normal_day_makes_no_model_call`

**What it does:** Runs `recurring-planted` on Oct 2, stores five streams and raises no alerts or model calls.

**Why it's there:** Established streams are silent on a quiet day.

## `test_a_stream_flag_and_an_overspend_flag_in_one_category_both_alert`

**What it does:** Makes personal care over limit on the day its gym price rises. Both `overspend` and `price_increase` alerts and their advice rows are recorded.

**Why it's there:** Stream keys must not collide with overspend category keys or require overspend facts.

## `test_a_user_without_a_current_budget_gets_signal_alerts`

**What it does:** Parameterizes `no_budget` and `period_ended`. Both routes write a price alert; a requested cancellation becomes `alert` because there is no current line to reduce.

**Why it's there:** Stream detection must run even when overspend checks cannot.

## `test_a_detector_failure_does_not_stop_overspend_alerts`

**What it does:** Makes stream refresh raise `RuntimeError`. The run finishes `ok`, records its overspend alert and stores only the exception type in `signals_error`.

**Why it's there:** A failed recurring detector must not hide unrelated budget alerts or disclose error details.

## `test_running_twice_on_the_same_day_alerts_once`

**What it does:** Repeats the Oct 3 run; only one alert exists and the second run makes no model call.

**Why it's there:** Daily retries must be quiet after a stream change has been recorded.

## `test_an_existing_signal_key_is_not_written_again`

**What it does:** Replays the exact same `StreamChange` even after its alert exists. No second model call or alert is made.

**Why it's there:** Tests the alert-key guard independently of the detector's date comparison.

## `test_five_signals_make_three_model_calls`

**What it does:** Supplies five price changes. All five receive alerts, advice and audit entries, but only three reach the model.

**Why it's there:** The remaining signals need code-written alerts, not dropped alerts or extra model calls.

## `test_the_models_cancel_of_rent_is_downgraded`

**What it does:** Requests rent cancellation and checks that the saved alert has action `alert` and contains none of that cancellation copy.

**Why it's there:** Protected categories must be enforced after the model's decision.

## `test_new_subscription_alert_carries_stream_id_and_key`

**What it does:** Detects a subscription that began inside the user's history. Checks its stream link, `first_seen` signal key and code-computed cancellation limit. With a checkpointer present, the run still finishes without waiting.

**Why it's there:** Cancellation is a pending alert, not an approval interrupt; close will apply it in Task 5.

## `test_price_alert_message_quotes_the_old_and_new_price`

**What it does:** Replaces a vague model message with copy naming the gym and both prices.

**Why it's there:** Price figures must come from code even when the model leaves them out.

## `test_a_paused_checkpoint_holds_no_signal_names_or_amounts`

**What it does:** Pauses on an overspend adjustment while a new `ZZ-UNIQUE-GYM` subscription costs $43.21. Inspects SQLite checkpoint rows, graph snapshot values and the database file's bytes after resume. Checks that signal payloads, prompt facts, merchant names and charge figures are absent, and resumes successfully.

**Why it's there:** Clearing the latest state alone would leave earlier checkpoints or freed SQLite pages holding the signal. Save-on-exit must prevent those writes.

## `test_summary_json_holds_ids_and_cents_only`

**What it does:** Checks the explicit `as_of` date and stream count/change-id summary, excluding merchant and model text.

**Why it's there:** Run summaries need stable identifiers without another copy of prompt data.

## `test_a_bad_or_unreachable_model_still_alerts_without_retry`

**What it does:** Parameterizes invalid JSON and a connection failure. Each makes exactly one call and records a fallback alert and one LLM failure.

**Why it's there:** Structured retries must not silently exceed the three-call signal cap.

## `test_a_signal_decision_exception_still_writes_a_plain_alert`

**What it does:** Makes the signal decision raise unexpectedly. A plain alert is still saved and only the exception type enters the summary.

**Why it's there:** A failure in signal decisions must not fail the daily run.

## `test_price_message_uses_the_change_even_after_the_stored_previous_is_cleared`

**What it does:** Clears the row's `previous_cents` while replaying a change carrying the old price. The alert still says $40.00 to $43.00.

**Why it's there:** The handoff warns that a second charge clears the row's previous amount; the change is authoritative for the alert.

## `test_signal_preparation_failure_does_not_stop_either_alert`

**What it does:** Makes signal ranking fail on a day with both overspend and price changes. Both alerts still appear, with only the exception type recorded.

**Why it's there:** The decision node must catch preparation errors as well as failures during a model call.

## `test_a_valid_model_message_is_kept`

**What it does:** Supplies a message naming the gym and both exact prices, and checks it is retained.

**Why it's there:** The numeric guard should replace incomplete text while preserving a valid explanation.

## `test_an_income_change_alert_never_names_the_employer_or_suggests_cancel`

**What it does:** Supplies an income reduction and a model cancellation response. The result is an income alert with code-written figures, and the employer appears in neither the message nor audit prompt.

**Why it's there:** Paycheck names stay local and income changes only alert.
