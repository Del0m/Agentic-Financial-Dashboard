# Tests: `backend/tests/test_daily.py`

These tests cover `app/budget_service/daily.py` (TPC-21), the one way a user's daily budget run happens, whoever starts it: the scheduler, the run button, or a simulated day.
- **`user_lock`:** a per-user lock, so those can never overlap.
- **`run_daily`:** takes the lock, or returns `None` if it's held.
- **`run_daily_locked`:** the run itself. It first closes any month that ended (TPC-22), then syncs Plaid only for users with a real item and runs the budget check.
- **`sim_today`:** a simulated user's date.

The settings the scheduler and admin routes read are also checked here.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_daily.py -v
```

## Shared setup

- `seed(db, pid, until)` seeds a planted user, as simulated from `until` if given.
- `model()` scripts a `one_time` diagnosis and an `alert`.
- The audit log goes to `tmp_path`.

---

## `test_run_daily_none_when_locked`

**What it does:** While the user's lock is held, `run_daily` returns `None` and writes no run. Once it's free, the run happens and the lock is released afterwards.

**Why it's there:** The scheduler skips a user whose run is already going (from the button or a simulation), instead of starting a second one on the same budget.

## `test_run_daily_locked_syncs_only_real_items`

**What it does:** A seeded user (placeholder token) isn't synced. Once its item has a real-looking token, the run syncs it first.

**Why it's there:** Real users must be checked against fresh Plaid data. A seeded user's fake token would only record a sync error on every simulated day.

## `test_daily_closes_before_budget`

**What it does:** After an Oct 20 run creates October's budget, a run on Nov 2 first closes October (its close run id is returned in `close_run_ids` and comes before the budget run), then checks November: the budget run is `ok` on the new period.

**Why it's there:** Every route to a daily run (scheduler, button, simulated day) closes first, so a missed month-end heals on the next run instead of the user being stuck at `period_ended`.

## `test_failed_close_still_runs_budget`

**What it does:** When `close_due` raises, the daily run still happens and ends `period_ended`, with no close run ids.

**Why it's there:** A broken close mustn't stop the daily check; the budget graph's `period_ended` routing is the safety net, and the next run retries the close.

## `test_sim_today`

**What it does:** A user seeded as simulated at Sep 30 gets that date, and a normal user gets `None`.

**Why it's there:** The dashboard and the run button use it as "today" for simulated users, and the real date for everyone else.

## `test_config_flags`

**What it does:** `ENABLE_SCHEDULER=Yes` turns the scheduler on, and an empty `ENABLE_ADMIN` leaves admin off. The times and timezone default to 06:00, 05:00 and America/New_York.

**Why it's there:** Both features must be off unless asked for, so a dev server or the test suite never runs jobs or exposes admin routes by surprise.
