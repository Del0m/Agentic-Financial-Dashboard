# Tests: `backend/tests/test_goals_api.py`

These tests cover `app/api/goals.py`, `GET /goals` (TPC-23): the goals the agent has proposed for the user. Generating goals from the app and deciding on them come with TPC-25.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_goals_api.py -v
```

## Shared setup

- `client` and `db` come from `conftest.py`, and `dev_user_id` (from `test_dashboard.py`) creates the user every request runs as.
- `goal(s, user_id, priority, status, **over)` writes a goal row, a $100-a-month savings goal unless overridden.

---

## `test_own_proposed_goals_by_priority`

**What it does:** Goals stored with priorities 2 and 1 come back as 1, 2, with every field, including a `null` date for a debt plan with none.

**Why it's there:** TPC-25's page shows them in this order, most important first.

## `test_other_users_and_superseded_hidden`

**What it does:** Another user's goal and this user's superseded goal aren't returned.

**Why it's there:** A user only sees their own goals, and only the current proposal, not the ones a later run replaced.

## `test_no_goals_is_an_empty_list`

**What it does:** A user with no goals gets `200` and `[]`.

**Why it's there:** No goals yet is a normal state, not an error.
