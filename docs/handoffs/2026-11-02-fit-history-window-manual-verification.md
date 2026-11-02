# Manual verification: budget fit history window

The bug: `fit_budget` decides where a user's history starts from the earliest transaction it's given, and skips that month if the transaction falls after the 3rd. Callers loaded only the 3-month window, so for a user with longer history, a first in-window transaction after the 3rd looked like the start of their history, and a complete month was dropped.

The fix: `initialize_budget`, close and the accuracy eval load one month further back. `fit_budget` uses that month to find the start of the history and to count a bill paid early at its end, but doesn't fit it or count it toward the usual bill.

## Setup

- Branch: `claude/jolly-lamport-5f927a`. No migration, and nothing needs to be running (no Ollama, servers or Docker).
- Every check uses a throwaway in-memory database. Your dev database and `eval/results/` aren't touched.
- The commands use `PY` for the repo's venv python. From this worktree's root:

```bash
export PY=$PWD/../../../venv/bin/python
```

## Checks

### 1. The backend suite passes

```bash
(cd backend && $PY -m pytest -q -k "not live and not sandbox")
```

Expected: `642 passed, 14 deselected`. It was 636 before this branch; six tests are new.

### 2. A user with six months of history gets all three months

Save this as `check.py` outside the repo, then run it from the repo root:

```python
import os
from datetime import date

from cryptography.fernet import Fernet

os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())

from app.budget_service.initialize import initialize_budget
from eval.fixtures import temp_db
from tests.factories import add_account, add_txn, add_user

with temp_db() as Session, Session() as s:
    acct = add_account(s, add_user(s, "u1", "u1@x"), "Checking")
    for m, cents in ((4, 40000), (5, 40000), (6, 40000), (7, 30000), (8, 45000), (9, 50000)):
        add_txn(s, acct, cents, "FOOD_AND_DRINK", day=date(2026, m, 5))
    s.commit()
    p = initialize_budget(s, "u1", date(2026, 10, 15))
    food = next(ln for ln in p.lines if ln.category == "FOOD_AND_DRINK")
    print(p.fit_json["window"], food.fit_json["monthly_totals"], food.limit_cents)
```

```bash
PYTHONPATH=backend:. $PY /path/to/check.py
```

Expected:

```
['2026-07-01', '2026-08-01', '2026-09-01'] [30000, 45000, 50000] 47250
```

On `main` the same script prints `['2026-08-01', '2026-09-01'] [45000, 50000] 49875`.

### 3. A history that really starts mid-month still skips that month

In `check.py`, change the months to `((7, 30000), (8, 45000), (9, 50000))` and run it again.

Expected: `['2026-08-01', '2026-09-01'] [45000, 50000] 49875`. This is the same with or without the fix.

### 4. The accuracy eval's scores haven't moved

This writes to the scratch folder you give it, not `eval/results/`:

```bash
PYTHONPATH=backend:. FERNET_KEY=x $PY -c "from datetime import date; from pathlib import Path; from eval.budget_accuracy import run; from eval.budget_users import PROFILES; run(PROFILES, Path('/tmp/fit-window-eval'), date(2026, 11, 2))"
```

Expected table:

```
profile       median MAPE  mean MAPE  zero-actual (mean)  unbudgeted
steady               5.8%       4.0%                   0           0
vacation             9.4%      58.8%                   0           0
early-rent           5.8%       4.6%                   0           0
rising               9.1%       7.7%                   0           0
occasional           4.8%       3.0%                   1           0
pooled               7.1%      17.7%
```

## What moved

**Eval and planted users: no stored budget changed.** I compared `main` and this branch for all 5 accuracy profiles and all 4 planted users: a first budget on the 1st of every month from the profile's second month to 3 months past its end (54 dates), each followed by closes through that last date (189 periods), plus the eval's stored, median and mean fits. Every limit, income, total, window and MAPE matched.

They don't move because they never hit the bug: each pays rent on the 1st, so every window month has a transaction by the 3rd.

**Two numbers moved inside one fit, and no stored limit uses them.** `early-rent` pays July's rent on Jun 30. In the fit for Oct 1 (window July to September, after that profile's data ends):

| | `main` | this branch |
|---|---|---|
| Rent `monthly_totals` for July | $0.00 | $1,500.00 |
| `spending_total_cents` | $925.20 | $2,500.20 |

The rent limit stays $1,500. The spending total is only the fallback for a user with no income, and this user has income in that window.

**Users with longer history do change.** To see the size of it, I ran each accuracy profile with history from April and everything from May 1-4 moved to May 5. May is then the window's oldest month and its first transaction is on the 5th. This is a one-off variant in a scratch script, not a committed profile, and starting in April changes the random draws, so its numbers aren't comparable with the table in check 4.

| Profile | Line | `main` | This branch |
|---|---|---:|---:|
| steady | Food | $688.29 | $707.54 |
| | Transportation | $194.44 | $177.68 |
| vacation | Food | $715.70 | $757.80 |
| | Transportation | $189.93 | $173.75 |
| | Travel | $1,128.75 | $262.50 |
| early-rent | Food | $708.23 | $725.51 |
| | Transportation | $191.07 | $173.87 |
| rising | Food | $816.25 | $879.24 |
| | Transportation | $193.45 | $169.96 |
| occasional | Food | $697.24 | $716.04 |
| | Transportation | $189.21 | $174.93 |
| | General merchandise | $157.50 | no line (skipped) |

Rent and entertainment didn't change. Median MAPE for these variants:

| Profile | `main` | This branch |
|---|---:|---:|
| steady | 4.3% | 5.7% |
| vacation | 85.0% | 8.5% |
| early-rent | 3.6% | 5.2% |
| rising | 7.9% | 9.2% |
| occasional | 4.5% | 5.7% |

So the fix isn't an accuracy win across the board. Four variants score slightly worse against one following month. Vacation scores much better because a median of two months let the June trip set the Travel limit, which is what the median is meant to prevent.

## Not verified

- The real app: no browser check, no dev database and no live Plaid data. A real user's budget only changes when it's next fitted, and existing periods aren't rewritten.
- `-k "live or sandbox"` tests weren't run.

## Known limits that could look like bugs

- **A quiet month before the window.** The check looks one month back only. A user with nothing in that month and a first in-window transaction after the 3rd still has that month skipped, as if their history started there.
- **An empty month inside the window now counts as $0.** On `main`, a user with older history and nothing at all in the window's oldest month had that month left out. Now it's fitted as $0, which pulls flexible medians down.
- **The budget graph's diagnose step is unchanged.** It loads the 3-month window, but `overspend_facts` counts a month as covered from the first transaction's month, with no day-of-month rule, so it doesn't have this bug.

## Undo

The checks change nothing except `/tmp/fit-window-eval` and your `check.py`, which you can delete.
