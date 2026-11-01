# Tests: `backend/tests/test_simulate_cli.py`

These tests cover `backend/scripts/simulate.py` (TPC-21), the terminal way to advance a simulated user. It prints one line per day (date, transactions injected, run status, flags, alerts written) and a summary at the end.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_simulate_cli.py -v
```

## Shared setup

- `main` takes a `session_factory` and a model, so the tests use the test database and `SchemaReplyModel` (`alert` once per category per month, then `no_op`).
- The prompt audit log and the checkpoint file go to `tmp_path`.
- `seed(db, until)` seeds the one-time user, simulated from Oct 9 by default.

---

## `test_cli_prints_days_and_summary`

**What it does:** Advancing 3 days from Oct 9 prints lines for Oct 10, 11 and 12. Oct 12's line shows the one injected purchase and the general-merchandise flag. The last line reads "simulated 3 day(s): 2026-10-09 → 2026-10-12, 3 runs, …".

**Why it's there:** A month with the real model takes minutes. The per-day lines show progress, and they show the day the planted overspend is caught.

## `test_cli_not_simulated_exit_1`

**What it does:** A user seeded without a start date exits 1 with "not a simulated user; seed it with --start".

**Why it's there:** The message names the fix, and the exit code lets a script notice.

## `test_cli_locked_exit_1`

**What it does:** While the user's run lock is held, the CLI exits 1 with "A budget run is already in progress.".

**Why it's there:** Two simulations in one process must not advance the same user at once.

## Not covered

- **The lock is in-process.** A CLI run doesn't see an API server's locks in another process. Don't simulate a user from the terminal while the API is advancing it.

## `test_simulate_sets_style`

**What it does:** `--style 50_30_20` stores the style before advancing and exits 0; an unknown style exits 2 (argparse `choices`).

**Why it's there:** A simulated user can switch style mid-simulation and see it take effect at the next close.

## `test_cli_refuses_while_another_process_runs`

**What it does:** With a `simulate` run row in progress for the user, as an advance started from the alerts page would leave, the CLI exits 1 with "already in progress".

**Why it's there:** The CLI's `user_lock` only exists in its own process, so the acquire always succeeded, even while the API server was advancing the same user. Both would then inject the same day and run the agent at once. The review on #14 found it. The CLI now also refuses while a `budget` or `simulate` row for the user is `running`.

## `test_cli_ignores_a_stale_running_row`

**What it does:** A `budget` row left `running` three hours ago doesn't stop the CLI. It gets past the guard and stops at "not a simulated user".

**Why it's there:** A process that died leaves its row `running` until the API restarts. Rows older than two hours are treated as left over, so a crash can't block the CLI for good.

## `test_cli_uses_the_app_checkpointer`

**What it does:** Checks that the CLI hands `advance_days` the checkpointer from `budget_hooks.get_checkpointer()`.

**Why it's there:** The CLI built its own `SqliteSaver` over the same file, a copy of the hooks' setup that could drift. The review on #14 found it. `_sim` also reads the simulation through `daily.sim_prefs` now, the same reader `sim_today` uses.
