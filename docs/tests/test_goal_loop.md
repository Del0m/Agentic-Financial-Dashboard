# Tests: `backend/tests/test_goal_loop.py`

These tests are TPC-25's done-when, end to end: accepting a goal shows it on the dashboard and in next month's budget. Every step goes through the HTTP routes (`/budget/run`, `/goals/generate`, `/goals/runs/{id}`, accept, dismiss, `/budget`) on the seeded `goals-card-24` user, with a scripted goal model and a stand-in budget model. They don't test the pieces; the other goal test files do that. They check that the pieces fit together.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_goal_loop.py -v
```

## Shared setup

- The `api` fixture is `test_goals_api.py`'s: it seeds `goals-card-24` (history ends 2026-10-31), points the routes at the test database, an in-memory checkpointer and a queue of scripted models (`api.models`), and runs both the goal and budget background workers inline. When the queue is empty, the budget run gets a model that answers any structured call.
- `set_today(api, day)` patches the clock of both route modules. It is the only clock the first three tests have; the fourth leaves the clocks alone.
- `run_budget(api, day)` posts `/budget/run` and checks the run finished `ok`. `propose(api, *goals)` queues a scripted goal model, posts `/goals/generate` and returns the run id with the run's body.

---

## `test_accepting_a_goal_reaches_the_dashboard_and_next_months_budget`

**What it does:** Builds November's budget, generates a $583.00 debt goal and accepts it. `GET /goals?status=accepted` lists it with its December start, which is what the dashboard's card shows after it asks for `current` and keeps the accepted goals, and November's budget has no goal line and the same Free limit as before. Then December 1 closes November: the new budget has a `Debt payoff` line of 58300 cents in the `goal` group, unflagged, with nothing spent and no pace, and the lines still add up to the total with Free smaller than November's.

**Why it's there:** This is the ticket's done-when. It fails if accepting changes the month under way, if close drops the goal, or if the goal is added on top of the total instead of out of Free.

## `test_the_paused_run_finishes_and_a_lost_checkpoint_changes_nothing`

**What it does:** Generates two goals, so the run pauses waiting on both. Accepting the first leaves it waiting on the second. The checkpoint is then deleted, and the run still reports the second as waiting. Dismissing it finishes the run as `ok` with nothing waiting, and both decisions are stored.

**Why it's there:** The rows are the source of truth and the checkpoint only tracks the run. A click must never be lost, and a run must never stay paused, because the checkpoint file was lost or deleted.

## `test_a_regenerated_proposal_fits_around_the_accepted_goal`

**What it does:** Accepts a $583.00 debt goal, then generates again. The cap is now 19440 cents, the $777.40 cap less the accepted goal. An emergency fund of 19441 cents is rejected on both attempts and nothing is stored; one of 19440 is accepted as a proposal and can be accepted too, leaving two goals.

**Why it's there:** Two runs must not commit the same money twice. This pins the exact boundary through the real routes: one cent over fails, the cap itself fits.

## `test_a_simulated_user_sees_the_line_without_patching_the_clock`

**What it does:** Marks the user as simulated on 2026-11-02, builds the budget, generates a debt and an emergency fund goal and accepts only the debt goal. Then `advance_days` runs 29 days to 2026-12-01 with a checkpointer, and no clock is patched. `GET /budget` shows December's budget with the debt line, the undecided emergency fund goal is `superseded` with no line, accepting it now is 409, and its paused run is `expired`.

**Why it's there:** The manual check and the Alerts page's simulation both work this way, and `GET /budget` and the goal routes must follow the simulated date, not the wall clock. It also covers a proposal left undecided over month end.
