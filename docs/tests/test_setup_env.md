# Tests: `backend/tests/test_setup_env.py`

These tests cover `backend/scripts/setup_env.py` (TPC-42). It creates the repo-root `.env` from `.env.example` and generates a `FERNET_KEY`, so the app starts on a fresh clone.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_setup_env.py -v
```

## Shared setup

- `repo` is a temporary directory holding a small `.env.example`, so the tests never touch the real `.env`.
- `key_in(env)` reads the `FERNET_KEY` value out of a file.

## `test_fresh_clone_gets_env_with_a_working_key`

**What it does:** Runs with no `.env`. It checks that the generated key is one `Fernet` accepts and that the rest of `.env.example` came along.

**Why it's there:** This is the professor's first run. A key `Fernet` rejects would crash the backend at import.

## `test_blank_key_in_existing_env_is_filled_and_the_rest_kept`

**What it does:** Starts from a `.env` with a blank `FERNET_KEY` and a Plaid secret. It checks that the key is filled and the secret is still there.

**Why it's there:** Someone may copy `.env.example` by hand and add only their Plaid keys.

## `test_missing_key_line_is_added`

**What it does:** Starts from a `.env` with no `FERNET_KEY` line and no newline at the end. It checks that the new line doesn't run into the last one.

**Why it's there:** Appending without the newline would corrupt the secret on the last line.

## `test_existing_key_is_never_replaced`

**What it does:** Starts from a `.env` that already has a key, and checks that the file is byte for byte the same afterwards.

**Why it's there:** The key encrypts stored Plaid tokens. Replacing it would make every linked bank unreadable.

## `test_running_twice_keeps_the_generated_key`

**What it does:** Runs twice from nothing and checks that the key didn't change.

**Why it's there:** `docker compose up` runs the script on every start.

## `test_the_key_is_never_printed`

**What it does:** Checks that the generated key isn't in the script's output.

**Why it's there:** The output goes to Docker's logs, which people paste into bug reports.
