# Tests: `backend/tests/test_close.py`

These tests cover period close (TPC-22, architecture 6.5): `app/budget_service/close.py` (`close_due` and the steps it runs) and the graph in `app/agents/close_graph.py`. For the oldest open period that ended before "today", close:
1. expires `adjust_next_budget` proposals the user never decided;
2. reads the month's actuals and the proposals the user accepted;
3. sets each line's next limit with `close_period` (accepted as-is, fixed follows the actual, flexible smoothed and clamped), then applies the user's style (free, total, alerts);
4. writes it all in one commit: the old period closed, the next one opened, alerts, and an `agent_runs` row (`graph="close"`).

Then it loops, so missed months close one at a time.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_close.py -v
```

## Shared setup

- `setup(db, lines, start, style, **spend)` gives `u1` an open period (October by default) with rent $1,500 (fixed), food $600, entertainment $200, savings $500 (fixed) and free $1,400, a $4,200 paycheck every month from July, and the month's spending: rent $1,500, food $700, entertainment $100, travel $50 (no line, so free covers it) and a $500 transfer to savings.
- With that October, a normal close gives November: rent $1,500, food $630 (0.7 × 600 + 0.3 × 700), entertainment $170 (clamped at −15%), savings $500, free $1,400.
- `add_alert(db, pid, category, cents, status, run_status)` adds an `adjust_next_budget` alert with its advice row, optionally from a budget run in the given status.
- `rent_paid_on(db, day, **spend)` runs `setup`, adds a $1,500 rent payment on `day`, closes October, and returns November's rent limit, its recorded actual and the summary's actual.

---

## `test_close_opens_adapted_month`

**What it does:** Closing on Nov 1 marks October `closed` and opens November 1–30 with the limits above, income and total $4,200, every line's name and group carried over, `source="adapted"`, and each line's `fit_json` holding its old limit, actual and rule (free's actual is the $50 of travel). One `ok` run holds the summary `close_due` returned: the month, the new period, each line's rule, income, total, free, style, `unbudgeted: ["TRAVEL"]`, and no expired alerts or new alerts.

**Why it's there:** The ticket's core behavior: compare budget against actual and open the next month, adapted.

## `test_accepted_applied_latest_wins`

**What it does:** Two accepted food proposals ($800, then $900) give November's food line $900, `source="user"`, rule `accepted`.

**Why it's there:** An accepted number is applied exactly, without the clamp. When the user accepted twice, their latest answer counts.

## `test_accepted_after_rename`

**What it does:** The user renamed the food line "Groceries" and moved it to wants, then accepted $900. November's line keeps the new name and group, at $900.

**Why it's there:** Proposals match by category, not name, and the user's customizations carry forward.

## `test_pending_proposal_expires`

**What it does:** A food proposal still `pending_user` from a run that's `awaiting_user`: after close, the alert is `dismissed`, its advice outcome is `ignored`, and the run is `expired` with the alert id in its summary. November's food line is the smoothed $630, not the proposal, and a late `decide_alert` raises `AlertAlreadyDecided` (a 409 in the API).

**Why it's there:** The user's choice: an undecided proposal counts as dismissed at close, and the Alerts page stops offering a decision that can't do anything.

## `test_nothing_due_writes_nothing`

**What it does:** Closing on Oct 31 (October hasn't ended) or for a user with no budget returns `[]` and writes no run and no period.

**Why it's there:** The month-end slot and every daily run call close. When nothing is due, which includes a simulation's first month with no budget, it must be silent.

## `test_rerun_writes_nothing`

**What it does:** A second close on Nov 1 returns `[]`; there is still one close run.

**Why it's there:** The scheduler, the startup catch-up and the daily run can all call close for the same day.

## `test_lost_race_rolls_back`

**What it does:** Another writer opens November between this close's read and its commit. Close returns `[]`, writes no run, and leaves October open; the other writer's November has no lines from this close.

**Why it's there:** The `(user_id, start_date)` constraint is the guard; when it fires, the whole month rolls back, including the expiry.

## `test_two_missed_months`

**What it does:** With October still open on Dec 3, close returns two summaries (October, then November), writes two runs in that order, closes November and opens December 1–31. December's food is $591, from October's $630 and November's own $500.

**Why it's there:** A server that was down over a month-end heals on its next run, and each month is closed from its own actuals.

## `test_failure_rolls_back`

**What it does:** When `close_period` raises, close returns `[]`, writes one `error` run with `{"month": "2026-10-01", "error": "RuntimeError"}`, leaves October open and opens no November.

**Why it's there:** A failed close must not half-write a month; the next daily run tries again.

## `test_legacy_period_gains_free_and_saving`

**What it does:** An October with only rent and food lines (from before TPC-22) closes into a November with a "Savings" line of $500 (`saving`, rule `fixed`), because October had a transfer, and a free line of $4,200 − $1,500 − $630 − $500.

**Why it's there:** Demo and seeded users created before TPC-22 pick up the new model at their first close, with no re-seed.

## `test_503020_close_alerts`

**What it does:** A 50/30/20 user whose rent rose to $1,800 gets November's saving line at $840 (rule `target`) and one alert on the **new** period: `group_over_target`, a plain medium `alert`, "Needs are 58% of income, above the 50% target." The summary lists the alert and the style.

**Why it's there:** A group over target is reported, never trimmed, and it describes the month the user is about to live in.

## `test_over_income_alert`

**What it does:** With rent at $4,000, the lines add up to $5,300. Free is $0, the total rises to $5,300, and the alert reads "Your budget lines add up to $5,300.00, $1,100.00 more than your typical income of $4,200.00."

**Why it's there:** The user's rule: free bottoms out, the total rises to cover the lines, and the user is told.

## `test_style_change_applies_at_close`

**What it does:** Switching to 50/30/20 mid-October leaves October's saving line at $500; November's is $840.

**Why it's there:** A month is judged against the budget it started with; the new style starts with the next one.

## `test_close_across_new_year`

**What it does:** Closing December on Jan 1 2027 opens January 1–31 2027, with the summary month `2026-12-01`.

**Why it's there:** Date arithmetic across the year boundary (month 12 + 1).

## `test_decision_during_close_is_kept`

**What it does:** The user accepts a pending $900 food proposal while close is running, after close listed it as pending. The alert stays `accepted` with its advice `accepted`, the paused run isn't expired, the summary lists no expired alerts, and November's food line is the accepted $900.

**Why it's there:** Found in the TPC-22 review. Expiry re-checks each alert's status inside the close's own commit, so a decision made in that window is never overwritten as expired.

## `test_early_rent_not_adopted_doubled`

**What it does:** October has its rent on Oct 1 and November's rent paid early on Oct 30. October's rent actual is $1,500, not $3,000, and November's rent line stays $1,500.

**Why it's there:** The TPC-17 review (#15) moved a fixed bill paid in a month's last 3 days, within 10% of the usual bill, to the next month in fit, pace and close. TPC-22 rewrote close, and merging main into it would otherwise have dropped the rule: close would adopt a doubled rent as next month's fixed limit. `gather_month` now takes its actuals from `period_actuals`.

## `test_rent_paid_early_counts_for_its_month`

**What it does:** October's rent is paid on Sep 30 and nothing on Oct 1. October's rent actual is $1,500.

**Why it's there:** The other half of the rule: close loads the month's transactions from 3 days before it starts, so a bill paid early for this month is counted here instead of disappearing.

## `test_close_fits_income_on_all_three_months`

**What it does:** Uses `setup`'s October with an extra $2,000 of income on Oct 20, so income is $4,200 in August and September and $6,200 in October. After the close, November's `income_cents` is $4,200, the median of all three months.

**Why it's there:** August's first transaction is the paycheck on the 15th. Close used to load only the three-month window, so August looked like a partial first month and was dropped, giving $5,200 (halfway between September and October). Close now loads July too, which shows August is complete. Income and the fallback total are the only things close takes from the fit, so that's what this bug moved.
