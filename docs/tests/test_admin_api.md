# Tests: `backend/tests/test_admin_api.py`

These tests cover `app/api/admin.py` (TPC-21), the development tools for simulated users:
- `GET /admin/sandbox`: is the current user simulated, and at what date;
- `POST /admin/sandbox/advance` with `{"days": 1-31}`: starts a background job that advances the simulated date a day at a time, each day injecting that day's generated transactions and running the real budget agent;
- `GET /admin/sandbox/advance/{job_id}`: the job's progress.

Every route answers `404` unless `ENABLE_ADMIN` is on. Only simulated users can be advanced, and an advance shares the per-user run lock with the run button and the scheduler.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_admin_api.py -v
```

## Shared setup

- **The `admin` fixture:**
  - points `budget_hooks` at the test database, an in-memory checkpointer, and `SchemaReplyModel` (it answers any structured call: `alert` once per category per month, then `no_op`);
  - runs jobs inline, because the in-memory test database is one connection;
  - turns `ENABLE_ADMIN` on.
- `admin.seed(pid, until)` seeds a planted user as simulated from `until` (default: the one-time user from Sep 30), and makes the API act as that user.
- `advance(admin, days)` posts an advance.

---

## `test_admin_off_404`

**What it does:** With `ENABLE_ADMIN` off, all three routes answer `404` "Not found.".

**Why it's there:** Inventing transactions is a development tool. A normal deployment must not expose it, or even reveal that it exists.

## `test_sandbox_state`

**What it does:** For a user simulated from Sep 30, the state is simulated, `overspend-one-time`, 2026-09-30.

**Why it's there:** The Alerts page shows the simulation panel, and the simulated date, from this.

## `test_sandbox_state_real_user`

**What it does:** For a user seeded without a start date, the state is not simulated, with no profile and no date.

**Why it's there:** The panel must stay hidden for anyone living on real time.

## `test_advance_202_then_ok`

**What it does:** Advancing 3 days answers `202` with a job id. The job reads `ok`: 3 of 3 days, from Sep 30 to Oct 3, with 3 run ids and no error. The simulated date is now Oct 3.

**Why it's there:** It's the API path of TPC-21's done-when. A long advance takes minutes with the real model, so it runs in the background and is polled.

## `test_advance_not_simulated_409`

**What it does:** Advancing a user who isn't simulated answers `409` "Only simulated users can be advanced.".

**Why it's there:** Advancing a real user would put invented purchases into their real ledger.

## `test_advance_locked_409`

**What it does:** While the user's run lock is held, an advance answers `409` "A budget run is already in progress.".

**Why it's there:** A simulation, a click on Run, and the morning job must never run on the same budget at once.

## `test_advance_days_422`

**What it does:** `days` of 0 or 32 is rejected with `422`.

**Why it's there:** A month is at most 31 days. A larger number would hold the lock and the model for a very long time.

## `test_other_users_job_404`

**What it does:** Another user's job id answers `404`.

**Why it's there:** One user can't watch another's simulation.

## `test_start_failure_500_frees_lock`

**What it does:** When the job can't be started, the request answers `500` "The simulation couldn't start.", and the user's lock is free again.

**Why it's there:** It's the TPC-20 lesson: a lock left held by a failed start would turn every later run into `409` until a restart.

## `test_advance_job_error_reports_type`

**What it does:** An advance from Oct 11 whose third day crashes (`evaluate` raises `RuntimeError` on its third call) ends `error` with `RuntimeError`. The simulated date stays at Oct 13, the last completed day, and the lock is free (plan Review Focus 5). Since the review on #12, the model being down no longer fails a day, so the crash comes from the engine.

**Why it's there:** The panel says where the simulation stopped and why. A second advance resumes from there instead of skipping a day.

## `test_lock_freed_when_final_status_write_fails`

**What it does:** When the job thread can't write its final status (the write raises), the user's run lock is still released. The job runs in a real thread here, joined at once.

**Why it's there:** It was found in the TPC-21 review. The release came after the status write, so a failing write (SQLite "database is locked") left the user locked out of every run until a restart. Run inline, the exception climbed back into the route, whose fallback hid the leak, which is why this test uses a thread.

## `test_view_switches_between_the_demo_user_and_the_plaid_user`

**What it does:** It starts on the seeded demo user: `GET /admin/view` returns `demo`, and the user is simulated. `PUT /admin/view` with `plaid` sets an HttpOnly `view=plaid` cookie, and every route then reads the dev user (`dev@localhost`, created on first use), which isn't simulated. Switching back to `demo` returns to the seeded user.

**Why it's there:** The demo shows the scripted story on the demo user and the real Plaid path on the user whose banks are linked through Plaid. The cookie switches between them without editing `.env` or restarting.

## `test_view_has_nothing_to_switch_when_the_configured_user_is_the_plaid_one`

**What it does:** With `DEV_USER_EMAIL` set to `dev@localhost`, `PUT /admin/view` with `plaid` answers `{"view": "demo", "available": false}` and sets no cookie.

**Why it's there:** Without a separate demo user, both views would be the same user, so the frontend hides the switch.

## `test_view_cookie_is_ignored_without_the_admin_tools`

**What it does:** After switching to `plaid`, turning `ENABLE_ADMIN` off makes `/admin/view` a 404. The dashboard shows the seeded demo user's "Checking" account even though the cookie is still sent.

**Why it's there:** Like the other admin tools, the switch only exists in development. A leftover cookie mustn't change whose data a request reads.

## `test_view_rejects_an_unknown_view`

**What it does:** `PUT /admin/view` with any value other than `demo` or `plaid` is a 422.

**Why it's there:** The cookie can only ever name the Plaid user. Nothing a request sends can point it at another user.
