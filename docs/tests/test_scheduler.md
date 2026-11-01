# Tests: `backend/tests/test_scheduler.py`

These tests cover `app/scheduler.py` (TPC-21), the budget agent's own schedule:
- **Every morning** (`DAILY_RUN_TIME`, default 06:00), a budget check for each eligible user: anyone with a budget or a real Plaid item, but not simulated users.
- **On the 1st** (`CLOSE_RUN_TIME`, default 05:00), the month-end slot for the month that just ended.

The scheduler only starts when `ENABLE_SCHEDULER` is on. The jobs are plain functions, so the tests call them directly instead of waiting for a clock.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_scheduler.py -v
```

## Shared setup

The `users` fixture seeds four planted users:
- **budgeted:** has a budget, from a run on Oct 2, but only the seed's placeholder Plaid item, so it isn't scheduled;
- **real:** has a real-looking Plaid item but no budget;
- **both:** has a budget and a real item;
- **simulated:** seeded from Nov 30, with its own budget.

`SchemaReplyModel` answers the model calls, and the audit log goes to `tmp_path`.

---

## `test_eligible_users`

**What it does:** The real and both users are eligible, sorted. The simulated user and the seeded budgeted user aren't.

**Why it's there:** The scheduler checks real people every day. Simulated users move on their own date, and a real-date run would put an alert on the wrong day.

## `test_daily_job_runs_eligible`

**What it does:** The job runs both eligible users for the given date, and writes nothing for the simulated user.

**Why it's there:** It's the autonomous part of the agent: nobody presses a button.

## `test_daily_job_skips_locked_and_survives_failure`

**What it does:** One user's run lock is held (both), and another's run raises (real). The job skips the first and records the second as failed, without either stopping the job.

**Why it's there:** A user mid-simulation, or mid-click, isn't run twice. One user's Plaid outage mustn't cost everyone else their morning check.

## `test_month_end_job_closes_previous_month`

**What it does:** On Nov 1 the month-end job runs close for the two real users and reports them as closed, with none skipped or failed. The one with an October budget gets an `ok` close run for October and an open November; the one with no budget yet gets nothing written. The seeded user isn't scheduled, so it isn't closed.

**Why it's there:** The month-end slot now runs the real period close (TPC-22) for every real user, and leaves simulated users to their own simulation.

## `test_month_end_skips_locked_user`

**What it does:** With one user's lock held and close raising for the other, the job reports the first as skipped and the second as failed.

**Why it's there:** A run already going for the user (the button, a simulation) must not overlap with close; the 06:00 daily run closes first instead. One user's failure is reported, not raised.

## `test_build_scheduler_jobs`

**What it does:** The scheduler has a `daily_budget` job at 06:00 and a `month_end` job on the 1st at 05:00, both in America/New_York. Both have one instance at a time, missed runs coalesced, and a one-hour grace. It isn't started.

**Why it's there:** These settings decide when users get alerts. A missed run within the hour still happens once, and a server that was down all morning doesn't replay stale days.

## `test_lifespan_starts_only_when_enabled`

**What it does:** With `ENABLE_SCHEDULER` off, starting the app builds no scheduler. With it on, the scheduler starts with the app and shuts down with it.

**Why it's there:** A dev server or the test suite must never run the daily job by surprise. A real deployment must not leave it running after shutdown.

## `test_catch_up_jobs_after_the_slot`

**What it does:** At startup, each case adds at most one `catch_up` job:
- 09:00 on Oct 29 adds it (the daily slot has passed);
- 04:00 adds nothing;
- 09:00 on Nov 1 adds it, covering the close and the daily slot;
- 05:30 on Nov 1 adds it for the close only, because 06:00 hasn't come yet.

**Why it's there:** It was found in the TPC-21 review. APScheduler's in-memory job store forgets slots that passed while the server was down, and the one-hour misfire grace only covers a running server. A server started after 06:00 would never have run that day's check. Only today's slot is caught up, never earlier days.

## `test_daily_catch_up_skips_users_already_run_today`

**What it does:** With `skip_done`, users whose budget already ran today (here, the fixture's runs moments ago) are skipped, and the user who hasn't run is run.

**Why it's there:** The catch-up must not run a second check for someone who already had one today, whether from the scheduler, the button or an earlier start.

## `test_month_end_rerun_closes_nothing`

**What it does:** Running the month-end job twice on Nov 1 leaves one close run, not two.

**Why it's there:** A restart on the 1st runs the catch-up again. Close does nothing for a month already closed, so it replaced TPC-21's `skip_done` scan of run summaries.

## `test_seeded_users_not_scheduled`

**What it does:** The seeded budgeted user (a budget, only the placeholder item, no simulation) isn't eligible.

**Why it's there:** Seeding a planted user without `--start` gave it a budget and no simulation prefs, so the daily job ran the real model on today's date against its July–October synthetic data, writing runs and alerts for a fake user every morning. The review on #14 found it. Seeded users are run by hand with the CLI or advanced by the simulation.

## `test_ran_today_uses_the_scheduler_day`

**What it does:** A run stored at 03:30 UTC on Oct 29 (23:30 on Oct 28 in New York) counts as Oct 28's run, not Oct 29's.

**Why it's there:** The review on #14 suspected `started_at` came back naive and was read as local time. It doesn't: `UTCDateTime` attaches UTC on read. The check now runs in SQL against the New York day's UTC bounds instead of reading every run the user ever had, and this test pins the day boundary for that query.

## `test_catch_up_closes_before_the_daily_run`

**What it does:** On Nov 1 at 09:00, it runs the one `catch_up` job with the two jobs it calls stubbed out, and checks that October's close ran before the day's budget check.

**Why it's there:** The daily and month-end catch-ups used to be two jobs firing at the same moment on the thread pool, so the 1st's budget run could start before the month was closed. The cron slots (05:00, then 06:00) and simulated days both close first, and TPC-22's real close needs that order. The review on #14 found it.
