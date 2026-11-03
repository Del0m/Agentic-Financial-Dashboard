# TPC-33: handoff after Tasks 1 to 3

Date: 2026-11-02. Work stopped here on purpose, to stay under the usage limit. Tasks 1 to 3 of 8 are done. **Task 4 has not started.** TPC-33 is not finished, and nothing is visible in the app yet.

## Where things stand

Branch `TPC-33`, cut from `TPC-25` (PR #23). Nothing is pushed.

| Commit | What it is |
|---|---|
| `8d93b08` | Investigation handoffs, spec and both plans |
| `e3b1a37` | Spec approved, with three detector and signal fixes from the review |
| `2389b26` | Task 1: `Every`, `Scatter`, price steps, `recurring-planted`, `recurring-noisy`, moving checking balance |
| `6ca2c14` | Task 2: `budget_engine/recurring.py`, `eval/recurring_accuracy.py`, and the gate, which passed |
| `1bc7155` | Task 3: `recurring_streams`, `alerts.stream_id` and `signal_key`, `refresh_streams`, Plaid `days_requested` of 180 |

The fast backend suite passes: 1,096 passed, 22 deselected (live and sandbox, not run).

## How to resume

Start with Task 4 in the [TPC-33 plan](../superpowers/plans/2026-11-02-tpc-33-recurring-streams.md). The [spec](../superpowers/specs/2026-11-02-tpc-33-31-recurring-and-forecast-design.md) is approved, including its 15 departures and the recommendations in its six decisions. TPC-31 starts only after TPC-33 is finished, from its own [plan](../superpowers/plans/2026-11-02-tpc-31-cash-flow-forecast.md).

```bash
git switch TPC-33
```

Things Task 4 needs to know that the plan doesn't say:

- `refresh_streams` commits nothing, so the node has to. Each `StreamChange.token` becomes `<kind>|<stream_id>|<token>` in `alerts.signal_key`, and that key is the only guard against writing an alert twice. It isn't enforced yet.
- `previous_cents` on a stored row is `None` after a second charge at a new price. Read the price rise from the `StreamChange`, not from the row.
- `anchors` isn't stored. Anything that needs `project` must use `current_streams`, not `stored_streams`.
- The usage cost per turn grows with the conversation, so start Task 4 in a fresh session.

## Open items for you

1. **PR #23 could close TPC-31.** Its description mentions TPC-31, and merging it might mark that ticket Done, as happened with TPC-33. Reword that line before merging, or reopen TPC-31 afterwards. The trigger isn't confirmed.
2. **Uncommitted changes that aren't from this work.** `README.md`, `docs/agent-pipelines.md`, `docs/system-walkthrough.md` and several diagrams have edits, and there's a new `goal-agent-loop` diagram. `eval/results/2026-10-29/` and `eval/results/2026-10-31/` are untracked. All of these were left alone and are in none of these commits.

## How to verify by hand

Everything below uses a throwaway database and leaves `dev.db` alone. Nothing needs to be running, and no model or Plaid access is used. Run from the repo root unless a step says otherwise.

**1. The gate.** This writes to `eval/results/<today>/recurring_accuracy/`, which isn't meant to be committed.

```bash
venv/bin/python eval/recurring_accuracy.py
```

Expect precision and recall of 1.00 on all 12 users with no false positives or misses, a noisy everyday share of 1.0, and a monthly next date of `{'cases': 372, 'within_1_day': 313, 'max_error': 2, 'by_error': {'0': 241, '1': 72, '2': 59}}`.

**2. The tests.**

```bash
cd backend && ../venv/bin/python -m pytest -q -k 'not live and not sandbox'
```

Expect 1,096 passed and 22 deselected.

**3. The migration and the refresh, end to end.** From `backend/`:

```bash
export DATABASE_URL="sqlite:////tmp/tpc33-check.db"
```

```bash
../venv/bin/alembic upgrade head
```

```bash
../venv/bin/python scripts/seed_budget_user.py --profile recurring-planted --replace
```

Expect the upgrade to run `a3d9e1b7c2f5 -> c4e8f2a1b6d3`. Then run this in `../venv/bin/python`:

```python
import os; from datetime import date
from cryptography.fernet import Fernet
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())
from app.db.session import SessionLocal
from app.budget_service.recurring import refresh_streams, stored_streams
uid = "budget-recurring-planted"
with SessionLocal() as s:
    for d in (date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 12), date(2026, 10, 20)):
        print(d, [(c.kind, c.previous_cents, c.token) for c in refresh_streams(s, uid, d, None)]); s.commit()
    for r in stored_streams(s, uid):
        print(r.merchant_name, r.direction, r.frequency, r.typical_cents, r.last_cents, r.next_date, r.status)
```

Expect:

```text
2026-10-02 []
2026-10-03 [('price_increase', 4000, '2026-10-03')]
2026-10-12 [('new_subscription', None, 'first_seen')]
2026-10-20 [('price_increase', 1549, '2026-10-20')]
Employer in biweekly 185000 185000 2026-10-30 active
Clipflix out monthly 1549 1799 2026-11-20 active
FitClub Gym out monthly 4000 4300 2026-11-03 active
Landlord out monthly 150000 150000 2026-11-01 active
Streamly out monthly 1549 1549 2026-11-05 active
NewFlix out monthly 999 999 2026-11-12 active
```

The first run stays silent, each rise fires once, and Grocer, Fuel and Pottery Studio aren't there.

**4. Undo.** Delete `/tmp/tpc33-check.db`, unset `DATABASE_URL`, and delete today's folder under `eval/results/`.

**Worth trying by hand:** repeat a date in the loop and the second run should return `[]`. Change a date range to skip past Jul 20 on a paycheck that stopped, and one `income_change` should appear.

## Rulings made during the build

- **Monthly next date:** the first anchored date at least 15 days after the last charge, not simply the month after. A 31st bill paid on the 1st is next due that same month. The spec records this.
- **Pay cuts:** `income_change` fires on the second lower paycheck, because the detector treats a lone lower charge as a one-off. The plan had no test for this, so one was added.
- **`history_start`:** reuses `initialize.history_start` instead of adding a second function.
- **`first_date`:** a stored row keeps its earliest date as the window slides.
- **Stale token:** an `income_change` for stale pay uses the date the run noticed it, so a skipped daily run records a later date.
- **Balance with no `until`:** subtracts every seeded row, so the balance always matches the database.
- **Profile details:** seed 31 for `recurring-planted`, and the merchant names for `Scatter`, weren't in the plan. Neither recurring user sets `opening_cents`.

## Not verified

- **The migration on Postgres.** It has only run on SQLite.
- **Real Plaid data.** All measurements are on synthetic users, whose planted charges mostly land on exact dates. Only the next-date sweep tests late payments.
- **The metered utility names.** `METERED_UTILITIES` came from memory of Plaid's taxonomy, and this repo has no other use of those names. Check them against Plaid's list.
- **The live and sandbox tests,** which weren't run.
- **The 300-seed and 281-day stress runs** aren't in the test suite. Their results are in the message of `6ca2c14`.

## Known limits that can look like bugs

- A stream with a long break stays stale until the new charges alone make a stream (3 for monthly), so the forecast won't count a returning paycheck for about two months.
- Price creep made of rises each under 5% never alerts.
- A rise over 50% alerts one charge late, because the first charge looks like a one-off.
- A one-off 5% to 50% above a bill, landing on its date, reads as a price rise.
- A real rate rise on a metered utility raises no alert.
- The noisy profile's everyday share stayed at 1.0 even on seeds with a false stream, so the false-stream count is the more useful check.
- Plaid's 180 days only applies to newly linked Items. Existing users have to relink.
