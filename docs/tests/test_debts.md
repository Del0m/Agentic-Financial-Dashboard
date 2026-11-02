# Tests: `backend/tests/test_debts.py`

These tests cover `app/budget_service/debts.py`, the loader that turns a user's `liabilities` and `accounts` rows into the planner's `Debt`s (TPC-32). The API (`POST /debt-plan`) and the gateway's `debt_plan` purpose both use it, so they always plan from the same numbers.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_debts.py -v
```

## Shared setup

- `add_user` and `add_account` come from `tests/factories.py`. `add_account(..., liability=...)` writes the liability row with the same balance as the account.
- `CARD` is a credit liability at 24.99% APR with a $95 minimum.

---

## `test_balance_comes_from_the_account`

**What it does:** The liability's snapshot says $3,000, but the account's current balance is $3,200. The loaded debt has $3,200, the account's name, kind `credit`, the APR and the minimum. The checking account isn't loaded.

**Why it's there:** The dashboard and the gateway's `debts` purpose show the current account balance (review fix on #6). The planner must plan from the same number the user sees.

## `test_other_users_debts_not_loaded`

**What it does:** Another user's card exists. Loading for `u1`, who has no debts, returns an empty list.

**Why it's there:** A plan must only ever include the user's own debts.

## `test_credit_balance_kept_for_the_engine`

**What it does:** An overpaid card with a −$50 balance loads as −5000 cents.

**Why it's there:** The loader reports what's there. The planner is the one place that decides a credit balance has nothing to pay off (`test_payoff.py::test_credit_balance_left_out_silently`).
