# Tests: `backend/tests/test_goal_prefs.py`

These tests cover `app/budget_service/goal_prefs.py` (TPC-23), the user's goal preferences in `prefs_json["goals"]`: goal kinds to skip, the most of the surplus goals may use, protected categories, and a free-text note.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_goal_prefs.py -v
```

## Shared setup

- The `user` fixture creates user `u1` and returns the session factory.
- `EXAMPLE` sets all four preferences.

---

## `test_defaults_with_nothing_stored`

**What it does:** A user with no preferences gets nothing skipped, a `max_share` of 0.8, nothing protected and no note.

**Why it's there:** Every user can generate goals without setting anything first.

## `test_round_trip`

**What it does:** What `set_goal_prefs` returns is what a later `user_goal_prefs` reads.

**Why it's there:** The preferences survive being stored as JSON and come back as the same values.

## `test_other_prefs_survive`

**What it does:** Setting goal preferences leaves `prefs_json["budget"]` and `["sim"]` alone.

**Why it's there:** The same column holds the budget style and a simulated user's date. Overwriting them would change the user's budget or lose their simulation.

## `test_invalid_raises`

**What it does:** An unknown kind, a `max_share` of 0, 1.5 or text, an unknown category, a 501-character note and a misspelled field each raise `ValueError`, and nothing is stored.

**Why it's there:** A typo like `max_shar` would otherwise be ignored silently and leave the user thinking their limit applied.
