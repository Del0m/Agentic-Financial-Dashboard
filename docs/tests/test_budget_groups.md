# Tests: `backend/tests/test_budget_groups.py`

These tests cover `app/budget_engine/groups.py` (TPC-22): the group and the name a new budget line starts with, until the user changes either. The budget rules read the group (`needs`, `wants`, `saving`, `free`); the name is only a label.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_budget_groups.py -v
```

---

## `test_default_groups`

**What it does:** `FOOD_AND_DRINK` and `RENT_AND_UTILITIES` start in `needs`, `ENTERTAINMENT` in `wants`, `SAVINGS_TRANSFER` in `saving` and `FREE` in `free`.

**Why it's there:** The 50/30/20 style splits income by these groups. Plaid's `FOOD_AND_DRINK` mixes groceries and dining, so it starts in `needs`, and the user can move it.

## `test_default_names`

**What it does:** Categories become sentence-case names ("Food and drink", "Rent and utilities", "Entertainment"); the reserved keys become "Savings" and "Free".

**Why it's there:** The dashboard shows the name, not Plaid's raw key.
