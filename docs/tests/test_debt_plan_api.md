# Tests: `backend/tests/test_debt_plan_api.py`

These tests cover `app/api/debt_plan.py`, `POST /debt-plan` (TPC-32). It returns the dashboard's payoff comparison: avalanche and snowball with the user's extra, each again with $0 extra as the baseline, and the debts left out with their reasons.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_debt_plan_api.py -v
```

## Shared setup

- `client` and `db` come from `conftest.py`. `dev_user_id` (from `test_dashboard.py`) creates the user every request runs as.
- `fixed_today` (autouse) pins the route's today to 2026-11-01, so the plan starts on 2026-12-01.
- `seed_debts(db, *cards)` gives the dev user one Plaid item and a credit account with a liability row per card. `CARD` is a $3,200 card at 24.99% with a $95 minimum; `LOAN` is an $18,400 student loan at 5.5% with a $210 minimum.

---

## `test_plan_matches_compare`

**What it does:** With the card and the loan and $200 extra, the response's avalanche and snowball plans (months, debt-free date, total interest, and each payoff's name, month and interest) equal `payoff.compare` run directly on `load_debts`. The baselines' months match too. The plan starts on 2026-12-01, avalanche pays the card off first, and nothing is excluded.

**Why it's there:** The route must only pass the planner's numbers through. Any arithmetic of its own would make the dashboard disagree with chat.

## `test_extra_out_of_bounds_is_422`

**What it does:** An extra of −1 cent or $1,000,000.01 a month gets `422`.

**Why it's there:** A negative extra would make payments smaller than the minimums, and an absurd one is a typo. The spec bounds it at 0 to $1,000,000.

## `test_no_debts_gives_empty_plans`

**What it does:** A user with no debts gets plans with 0 months, no debt-free date, and empty `order`, `never` and `excluded` lists.

**Why it's there:** The card is only shown to users with debts, but the route must still answer, not error.

## `test_excluded_named`

**What it does:** A store card with no APR comes back in `excluded` with its name and reason `no_apr`, and only the Platinum Card is planned.

**Why it's there:** The user must see which debts the plan leaves out and why, instead of a plan that silently ignores them.

## `test_never_debt_named`

**What it does:** A $10,000 card at 30% with a $100 minimum and no extra is never paid off. Its plan has no months and no total interest, and `never` lists the card by account id and name.

**Why it's there:** A debt that's never paid off isn't in the payoff order, so the dashboard had no name to say which debt can't be paid. Found while writing the card's tests (TPC-32), when the first version needed a "Debt 1" fallback.

## Accepted debt-payoff goals

Accepted debt goals are included once, separately from minimum payments and manually entered extra. Tests check the monthly payment breakdown, date schedule, proposed/stopped/completed/non-debt exclusions, expired schedules, future starts and user ownership. The baseline includes accepted goals, so manual-extra savings do not count those contributions again.
