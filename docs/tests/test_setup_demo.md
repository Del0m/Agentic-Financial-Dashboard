# Tests: `backend/tests/test_setup_demo.py`

These tests cover `backend/scripts/setup_demo.py` (TPC-42). It seeds the demo user on a simulated date and advances it to the 1st, so the app opens with a budget instead of empty.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_setup_demo.py -v
```

## Shared setup

- An autouse fixture points the audit log and the budget checkpoints at a temporary directory.
- `counts(db)` returns the demo user's number of transactions and budget periods.
- `NoModel` fails the test if the model is called.

## `test_seeds_the_user_with_an_open_budget_and_no_llm`

**What it does:** Runs from an empty database with `NoModel`. It checks that the user exists and that its simulated date falls inside its budget period.

**Why it's there:** Setup runs before Ollama is guaranteed to be up, or its models downloaded, so opening the budget must not need the model.

## `test_running_again_changes_nothing`

**What it does:** Runs twice and checks that the simulated date, transactions and periods are unchanged.

**Why it's there:** `docker compose up` runs the script on every start, and a restart mustn't lose the days the viewer has advanced.

## `test_a_seed_interrupted_before_the_budget_is_finished`

**What it does:** Starts from a simulated user with no budget, and checks that a run opens one.

**Why it's there:** A first start stopped between the two steps shouldn't leave a demo with no budget for good.

## `test_a_hand_seeded_user_is_left_alone`

**What it does:** Starts from the same user seeded without a simulated date. It checks that nothing changes and that the script says so.

**Why it's there:** A developer may have seeded this user by hand on the same `dev.db`. Advancing or replacing it would destroy their work.
