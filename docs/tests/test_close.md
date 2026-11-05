# Tests: `backend/tests/test_close.py`

These tests cover period close (TPC-22, architecture 6.5): `app/budget_service/close.py` (`close_due` and the steps it runs) and the graph in `app/agents/close_graph.py`. For the oldest open period that ended before "today", close:
1. expires `adjust_next_budget` proposals the user never decided;
2. reads the month's actuals and the proposals the user accepted;
3. sets each line's next limit with `close_period` (accepted as-is, fixed follows the actual, flexible smoothed and clamped), then applies the user's style (free, total, alerts);
4. writes it all in one commit: the old period closed, the next one opened, alerts, and an `agent_runs` row (`graph="close"`).

Since TPC-25 it also turns accepted goals into lines of the next period, completes finished goals, expires goal proposals whose start month has arrived, and takes a goal's payments out of the loan and saving actuals. `app/budget_service/goal_lines.py` holds the goal queries.

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
- `november(db, lines, loan, saved, style)` runs `setup` for November and adds the loan payment. By default the lines are rent $1,500, loan $210 and savings $500 (all fixed) plus free $1,990, and the month spends exactly those, so with $4,200 of income December's free is $1,990 before any goal. `NO_LOAN` and `NO_SAVING` are the same lines without that line.
- `add_goal(db, kind, cents, status, start, target, run_status, priority)` adds a goal row, by default an accepted $583 debt payoff from Dec 1 2026 to Jun 1 2027, optionally with a `goals` run in the given status. It returns the goal's and the run's ids.
- `goal_status(db, id)` and `set_goal_status(db, id, status)` read and change a goal in their own session.

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

## Goals (TPC-25)

These use `november` and `add_goal`. The netting tests put the goal's line straight into November's lines, as if an earlier close had written it.

## `test_accepted_goal_becomes_a_line_in_the_next_period`

**What it does:** A $583 debt payoff accepted during November. Closing November gives December a line named "Debt payoff", group `goal`, kind `fixed`, limit $583, `source="user"`, with `fit_json` `{old_cents: 0, actual_cents: 0, rule: "goal"}` and the same figures in the run summary. November gains no line.

**Why it's there:** The ticket's done-when: accepting a goal shows it in next month's budget, and the month under way never changes.

## `test_goal_line_takes_its_amount_from_free_only`

**What it does:** December's limits are rent $1,500, loan $210, savings $500, goal $583 and free $1,407 ($1,990 − $583). The total stays at income, $4,200, with no alerts.

**Why it's there:** Goals are funded from the surplus and never cut another line, so Free shrinks by exactly the goal.

## `test_goal_over_free_keeps_other_lines_and_raises_over_income_alert`

**What it does:** A $2,500 goal against $1,990 of free. Every other line keeps its limit, free is $0, the total rises to $4,710, and one `over_income` alert on December says the lines are $510 over income.

**Why it's there:** Code never cuts a line the user didn't ask to cut. When the lines don't fit, the user is told.

## `test_503020_goal_line_comes_out_of_saving_target`

**What it does:** In 50/30/20, a $583 goal leaves the saving line at $257 (the $840 target less the goal) and free at the $1,260 wants target.

**Why it's there:** The goal has to reach `apply_style` as a `goal` line for the style to fund it from the 20%. The engine tests cover the rule; this covers close passing the line in.

## `test_goal_starting_later_gets_no_line_yet`

**What it does:** A goal starting Jan 1 2027 has no line in December, where free stays $1,990. January has the line and free of $1,407.

**Why it's there:** A line starts in the goal's own start month, not at the first close after it's accepted.

## `test_goal_line_carries_user_rename`

**What it does:** December's goal line is renamed "Card payoff". January's line keeps that name at $583.

**Why it's there:** The line is rebuilt from the goal row at every close, so the name has to come from the old line. Without that a rename would last a month.

## `test_goal_line_never_smoothed_or_adopted`

**What it does:** Two closes with $0 spent on the goal line. January's line is still $583, `source="user"`, rule `goal`, with `old_cents` $583 and `actual_cents` $0, and free is $1,407.

**Why it's there:** A goal line has no spending to measure, so `close_period` must never see it. A smoothed line would decay toward $0.

## `test_finished_goal_completes_and_frees_its_line`

**What it does:** A goal with target date Dec 1 2026. December has the line, because its target month is still a payment. Closing December writes no line, sets the status `completed`, reports the id in the summary's `finished_goal_ids`, returns free to $1,990, and `committed_goals` no longer lists it.

**Why it's there:** Lines shouldn't run forever. Free comes back, and the kind can be offered again.

## `test_stopped_goal_line_ends_at_close`

**What it does:** The goal is dismissed during December. December keeps its line, January doesn't, free returns to $1,990, and the status stays `dismissed`, not `completed`.

**Why it's there:** Stopping is how a user undoes a commitment. The month under way doesn't change, and the next one is free of it.

## `test_stop_during_close_stands`

**What it does:** A finished goal is dismissed after `gather_month` listed it as finished. After the close it's still `dismissed`, `finished_goal_ids` is empty, and no alert is written.

**Why it's there:** `persist_month` rechecks `accepted` in the same write that completes the goal, as it does for proposals, so a click during close isn't overwritten.

## `test_finished_goal_raises_a_goal_ended_alert`

**What it does:** A goal with target date Dec 1 2026. Closing December writes one alert on January's period: kind `goal_ended`, no category, severity `medium`, no cause, action `alert`, status `pending_user`, with the message "Your Debt payoff goal reached its planned end date, so its $583.00 budget line has ended. Check that it's done, and generate goals again if there's more to do." Its id is in the summary's `alert_ids`, and closing November wrote none.

**Why it's there:** Nothing tracks contributions, so Free rising when a line ends could be read as "debt done". The user has to be told the plan ended, not that the debt did.

## `test_goal_ended_alert_uses_the_renamed_line`

**What it does:** The goal's December line is renamed "Card payoff" before the close. The alert says "Your Card payoff goal".

**Why it's there:** The message uses the name the user sees in the budget.

## `test_no_goal_ended_alert_while_a_goal_runs`

**What it does:** A goal that runs past January. Closing November and December writes no alert.

**Why it's there:** The alert is for an ending, not for every close with a goal.

## `test_stopped_goal_raises_no_goal_ended_alert`

**What it does:** A goal dismissed during December. January's close writes no alert and reports no `alert_ids`.

**Why it's there:** The user stopped it themselves, so telling them it ended would be noise.

## `test_accept_just_before_expiry_is_not_overwritten`

**What it does:** The user accepts a stale proposal in the instant before `expire_goal_proposals` writes (the test fires the accept just before the first `UPDATE goals`). The goal ends `accepted`, `expired_goal_ids` is empty, and its run stays `awaiting_user`.

**Why it's there:** The old code read the row, then wrote `superseded` unconditionally, so a click between the two was overwritten. The write is now one conditional update on `status = 'proposed'`.

## `test_stop_just_before_completion_is_not_overwritten_or_announced`

**What it does:** The user stops a finished goal in the instant before the completion write. The goal ends `dismissed`, `finished_goal_ids` is empty, and no alert is written.

**Why it's there:** The completion step has the same read-then-write shape; it is now one conditional update on `status = 'accepted'`, and the alerts come from the ids it returns.

## `test_saving_line_ignores_goal_transfers`

**What it does:** November's saving limit is $500, its goal line is $194.40, and $694.40 was saved. December's saving line is $500 with an actual of $500, the goal line stays $194.40, and free is $1,795.60.

**Why it's there:** A fixed line adopts its actual. Without netting it would adopt $694.40 while the goal line budgets the $194.40 again.

## `test_saving_line_ignores_both_saving_goals`

**What it does:** Saving limit $500, savings goal $194.40, emergency fund goal $100, and $794.40 saved. December's saving line is $500.

**Why it's there:** Both kinds are transfers to savings, so they net together against the one saving line.

## `test_goal_transfer_alone_adds_no_saving_line`

**What it does:** No saving line, a $194.40 goal, and $194.40 saved. December has no `SAVINGS_TRANSFER` line.

**Why it's there:** Close gives a budget a saving line once the user is seen saving. The goal's transfer isn't that.

## `test_saving_above_goal_adds_a_saving_line_for_the_rest`

**What it does:** No saving line, a $194.40 goal, and $300 saved. December's saving line is $105.60.

**Why it's there:** With a limit of $0, only the amount above the goal is the user's own saving. That amount still earns a line.

## `test_loan_line_ignores_debt_goal`

**What it does:** Loan limit $210, debt goal $583, $793 paid in `LOAN_PAYMENTS`. December's loan line is $210 with an actual of $210, free is $1,407 with an actual of $0, and nothing is unbudgeted.

**Why it's there:** The same double count as saving, through the loan line.

## `test_debt_goal_paid_to_a_card_leaves_the_loan_line`

**What it does:** A $200 debt goal, a $210 loan payment on a $210 limit, and a $200 card payment. December's loan line stays $210.

**Why it's there:** Avalanche usually sends the goal to a card, and card payments aren't in the loan actual. Subtracting the whole goal would give a $10 loan line, so only the amount above the old limit is the goal's. This test passes before the netting exists; it guards the rule against the naive version.

## `test_goal_paid_to_an_unlined_loan_is_not_free_spending`

**What it does:** No loan line, a $583 goal, $583 paid in `LOAN_PAYMENTS`. December has no loan line, free's actual is $0, and `unbudgeted` is empty.

**Why it's there:** An unlined category's spending lands on Free and in the unbudgeted list. A goal's payment belongs in neither.

## `test_unlined_loan_above_goal_stays_free_spending`

**What it does:** Same, with $700 paid. Free's actual is $117 and `unbudgeted` lists `LOAN_PAYMENTS`.

**Why it's there:** Only the goal's share comes off. The rest is real spending the user should see.

## `test_stopped_goal_still_nets_its_closing_month`

**What it does:** November has a $583 goal line, but the goal row is already dismissed, and $793 was paid. December's loan line is $210 and there's no goal line.

**Why it's there:** Netting reads the closed period's lines, not the goal rows. The month was budgeted with that line whatever the row says now.

## `test_undecided_proposal_expires_with_its_start_month`

**What it does:** A proposal starting Dec 1, with a paused `goals` run. Closing November marks it `superseded` and the run `expired`, with `finished_at` set and `expired_goal_ids` added to the run's summary. The close summary lists the id too, and December has no goal line.

**Why it's there:** The surplus behind a proposal goes stale. A paused run whose proposals expired must not keep waiting.

## `test_later_proposal_survives_close`

**What it does:** A proposal starting Jan 1 2027 stays `proposed` after November's close, and its run stays `awaiting_user`.

**Why it's there:** Its start month's budget hasn't opened, so the user can still decide.

## `test_null_start_proposal_expires`

**What it does:** A proposal with no `start_date` is `superseded` by the close.

**Why it's there:** Rows from before TPC-25 can't be accepted, so nothing else would ever clear them.

## `test_answer_during_close_stands`

**What it does:** The user accepts after `gather_month` read the rows. The goal stays `accepted`, the summary's `expired_goal_ids` is empty, and the run stays `awaiting_user`. December has the goal's line.

**Why it's there:** `expire_goal_proposals` rechecks the status in the same write, and `persist_month` works out the goal lines again from the rows it just locked.

## `test_lost_race_leaves_goals_alone`

**What it does:** Another close opens December between this close's read and its commit. A finished goal stays `accepted`, a stale proposal stays `proposed`, and its run stays `awaiting_user`.

**Why it's there:** The goal writes are in the month's one commit, so a lost race rolls them back with everything else.

## `test_committed_goals_are_accepted_by_priority_then_id`

**What it does:** Accepted goals come back by priority, then id, as `CommittedGoal`s with the row's values. Proposed and dismissed rows are left out.

**Why it's there:** The goal graph uses this list for its cap as well as close, so it has to be exactly the accepted rows, in a stable order.

## `test_stale_proposals_are_due_or_undated_and_proposed`

**What it does:** For Dec 1, it returns the proposals starting on or before that date and the undated one. A proposal starting Jan 1, an accepted goal and another user's rows are left out.

**Why it's there:** This is the rule for what a new budget retires.

## `test_expire_goal_proposals_rechecks_and_leaves_the_commit_to_the_caller`

**What it does:** Given an accepted and a proposed goal, it returns only the proposed id. A rollback undoes it and a commit keeps it. The paused run becomes `expired`, and the accepted goal's `ok` run doesn't change. An empty list returns `[]`.

**Why it's there:** Both callers put the expiry in their own commit, so it mustn't commit itself, and it mustn't overwrite an answer.

## `test_goal_accepted_during_close_gets_its_line`, `test_goal_dismissed_during_close_reserves_nothing`

**What they do:** A goal is accepted or dismissed after `gather_month` read the goals and before `persist_month` writes. The new month's lines follow the decision, and they still add up to the period total.

**Why they're there:** The decision and the close commit separately, and TPC-25 requires an accepted goal in next month's budget.
