# Tests: `backend/tests/test_transactions_api.py`

These tests cover `app/api/transactions.py` (TPC-22), behind the dashboard's Transactions table:
- **`GET /transactions?month=YYYY-MM`:** a month's transactions, newest first, each with its category, the budget line it counts against (`line_name`) and where the category came from (`plaid`, `rule` or `user`).
- **`PATCH /transactions/{id}`:** corrects one transaction to one of the user's lines; with `remember`, the correction becomes a rule for that merchant.

A transaction's category resolves as the user's correction, then the merchant rule, then the LLM's category, then Plaid's (`resolve_category`, shared with the budget).

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_transactions_api.py -v
```

## Shared setup

- The `api` fixture points the routes at the test database, signs in as the planted `overspend-one-time` user, and fixes "today" at Oct 20.
- `seed(db, until)` seeds that user (a $600 ElectroMart purchase on Oct 12, a weekly Corner Shop, rent, a paycheck and a savings transfer) and creates October's budget. Its lines are general merchandise, rent, savings and free.
- `txn_id(db, merchant, since)` is the merchant's first transaction on or after `since` (Oct 1 by default).

---

## `test_list_month_with_sources`

**What it does:** With a rule for "corner shop", October's list is newest first and all in October. ElectroMart is general merchandise from Plaid, $600, on the "General merchandise" line; Corner Shop comes from the rule; the savings transfer is `SAVINGS_TRANSFER` on "Savings"; and the paycheck is `INCOME` on no line.

**Why it's there:** The table shows where each transaction lands in the budget and why, so the user can spot a wrong category. Income and transfers aren't spending, so they count against no line, not Free.

## `test_list_defaults_to_sim_month`

**What it does:** For a user simulated up to Oct 15, the list without `month` holds October 1–15.

**Why it's there:** The dashboard follows a simulated user's date, like `GET /budget`.

## `test_correct_one`

**What it does:** Correcting ElectroMart to Free returns it as `FREE` on "Free" from `user`, stores `user_category`, and creates no rule.

**Why it's there:** The default fixes one transaction only; remembering is the user's choice.

## `test_remember_creates_and_updates_rule`

**What it does:** Correcting one Corner Shop purchase to Free with `remember` moves every other Corner Shop purchase to Free (source `rule`). Doing it again with general merchandise updates the same rule, stored as `"corner shop"`.

**Why it's there:** "Always for Corner Shop?" applies to past and future purchases, and there's one rule per merchant.

## `test_override_beats_rule`

**What it does:** With a rule sending Corner Shop to Free, correcting one purchase to general merchandise returns it from `user`.

**Why it's there:** A one-off correction (a TV at Costco) wins over the merchant's rule.

## `test_category_must_be_a_line`

**What it does:** Correcting to `TRAVEL`, which has no line, is a 422 "Pick one of your budget lines.".

**Why it's there:** The choice always lands in the budget the user sees.

## `test_remember_without_merchant`

**What it does:** Remembering a transaction with no merchant is a 422 "This transaction has no merchant to remember.".

**Why it's there:** A rule is keyed by merchant; one without a name would match nothing, or everything unnamed.

## `test_correct_closed_month_txn`

**What it does:** After October closes, correcting an October purchase to Free saves `user_category`, but October stays closed and every line of both periods keeps its limit.

**Why it's there:** Closed months stay closed: a correction doesn't reopen or redo a close (spec, out of scope).

## `test_other_users_txn_404`

**What it does:** Correcting another user's transaction is a 404 "Not found.".

**Why it's there:** Every row is scoped to its user.

## `test_a_goal_line_is_not_a_correction_target`

**What it does:** With a goal line ("Debt payoff", group `goal`) on the open budget, correcting a purchase to it is a 422 "Pick one of your budget lines." and the transaction keeps no correction.

**Why it's there:** A goal line is money set aside, not spending, so it has no spending to hold. The table doesn't offer it and the route refuses it.
