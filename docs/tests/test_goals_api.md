# Tests: `backend/tests/test_goals_api.py`

These tests cover `app/api/goals.py` (TPC-23, TPC-25), the goals the agent proposes and the user's answer to them:
- **`GET /goals?status=proposed|accepted|current`:** the user's goals; no parameter means proposed only (TPC-23's contract).
- **`POST /goals/generate`:** starts a goal run in the background (202 with its `run_id`), or 409 when the user already has a run going.
- **`GET /goals/runs/{id}`:** the run's status, outcome, goals, the goals it is still waiting on, and any validation issues.
- **`POST /goals/{id}/accept` and `/dismiss`:** the user's decision; dismissing also stops an accepted goal.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_goals_api.py -v
```

## Shared setup

- The `api` fixture seeds the `goals-card-24` profile (a 24.99% card and a student loan, with money left each month) and runs every request as that user. It points the routes at the test database, an in-memory checkpointer and a queue of scripted models (`api.models`), and runs the background worker inline because the in-memory test database is one connection that threads can't share.
- `goal(s, user_id, priority, status, **over)` writes a goal row, a $100-a-month savings goal that starts Dec 1 unless overridden.
- `generate(api, *goals, **body)` queues a model that proposes those goals, posts `/goals/generate` and returns the run id. `g(kind, cents, ...)` is one goal in the model's reply.

---

## `test_own_proposed_goals_by_priority`

**What it does:** Goals stored with priorities 2 and 1 come back as 1, 2, with every field, including a `null` target date for a debt plan with none and the `start_date`.

**Why it's there:** The page shows them in this order, most important first, and needs the month each plan starts.

## `test_other_users_and_superseded_hidden`

**What it does:** Another user's goal and this user's superseded goal aren't returned.

**Why it's there:** A user only sees their own goals, and only the current proposal, not the ones a later run replaced.

## `test_no_goals_is_an_empty_list`

**What it does:** A user with no goals gets `200` and `[]`.

**Why it's there:** No goals yet is a normal state, not an error.

## `test_get_goals_status_filter`

**What it does:** With proposed, accepted, superseded, dismissed and completed goals, plus another user's accepted goal: no parameter and `proposed` return the proposals; `accepted` returns the accepted goal; `current` returns proposals first and then accepted goals, each by priority. Superseded, dismissed, completed and other users' goals never appear, and an unknown status is a 422.

**Why it's there:** TPC-23's contract (proposed only by default) has to hold while the page and dashboard can ask for the accepted goals too.

## `test_get_goals_finishes_a_decided_run`

**What it does:** After a run pauses, its goal is accepted straight in the database (a click whose resume failed). The run still says `awaiting_user` until `GET /goals`, after which it is `ok` and waiting on nothing.

**Why it's there:** A failed resume must not leave a run paused forever; opening the page heals it.

## `test_generate_then_poll_reports_waiting`

**What it does:** Generating returns a run id. Polling it gives `awaiting_user`, one attempt, no issues or error, and `waiting_for` equal to the run's goals, which `GET /goals` lists. The user's lock is free afterwards.

**Why it's there:** This is the page's main loop: start, poll, show the proposals the run is waiting on.

## `test_generate_uses_the_simulated_date`

**What it does:** The user's `prefs_json["sim"]` date is Nov 20 while the clock says March 2027. The proposed plan starts Dec 1.

**Why it's there:** A simulated user lives on their own date, as in the budget run, not the real one.

## `test_generate_uses_the_request_date`

**What it does:** A `date` in the request body drives the run.

**Why it's there:** The body's date is the explicit override of both the clock and the simulation.

## `test_generate_busy_is_409_and_keeps_the_lock_clean`

**What it does:** With the user's lock already held, generate is a 409. Then, with a recent `running` budget row, it is a 409 again, no goal run row is written and the lock is released.

**Why it's there:** One run at a time per user, across the budget agent, the scheduler and goals. The route takes the lock before it checks the rows, so every refusal has to hand the lock back or the user is stuck until a restart.

## `test_generate_start_failure_releases_the_lock`

**What it does:** When starting the worker raises, the response is a 500 and the lock is free. The next generate works.

**Why it's there:** A failure before the thread owns the lock must not leave every later run answering 409.

## `test_generate_that_dies_before_its_row_is_500_and_releases_the_lock`

**What it does:** When making the model raises inside the worker, the response is a 500 and the lock is released.

**Why it's there:** A run that dies before it writes its row has nothing to poll, so the page needs an error, not a missing id. The worker's `finally` is the other place the lock must come back.

## `test_run_reports_outcomes`

**What it does:** A user whose spending leaves nothing gets a run with status `ok`, outcome `no_surplus`, no goals and nothing waiting.

**Why it's there:** "No goals because there's no surplus" is a result the page explains, not a failure.

## `test_run_reports_validation_failure`

**What it does:** The model twice proposes an amount over the cap, with a marker in its rationale. The run is `error` after two attempts with no goals; every issue has exactly `message`, `index` and `kind`, and the marker appears nowhere in the response.

**Why it's there:** The page shows why a run failed, but only text the code wrote. Raw model output must never reach the browser.

## `test_run_of_another_user_or_graph_is_404`

**What it does:** Another user's goal run, the user's own budget run and a missing id are all 404 "Run not found.".

**Why it's there:** Runs are scoped to their user and to the goals graph; the budget's runs have their own route.

## `test_waiting_for_survives_a_lost_checkpoint`

**What it does:** After the run's checkpoint is deleted, polling still says `awaiting_user` and waiting on the run's goals.

**Why it's there:** `waiting_for` is read from the goal rows, so a lost checkpoint doesn't break the page.

## `test_waiting_for_lists_only_goals_still_proposed`

**What it does:** After one of a run's two goals is accepted, `goal_ids` still lists both and `waiting_for` only the other.

**Why it's there:** The page shows which proposals still need an answer.

## `test_interrupted_run_reports_its_error`

**What it does:** A run row closed by `close_interrupted_runs` (its summary holds only an error) reports status `error` and `error` "Interrupted", with no goals, issues or attempts.

**Why it's there:** That summary has none of the usual keys; the route must tolerate it and still say what happened.

## `test_a_running_row_has_no_summary_to_read`

**What it does:** A `running` row with an empty summary is reported as `running`, with no outcome, goals or waiting.

**Why it's there:** The page polls while the run is still going, before any summary exists.

## `test_accept_returns_the_goal`

**What it does:** Accepting the run's only proposal is a 200 with the goal `accepted` and its `start_date`; the paused run is then `ok` and waiting on nothing.

**Why it's there:** Accepting is the click that commits money to a plan, and the last decision finishes the run that was waiting for it.

## `test_accept_decided_is_409_other_users_404`

**What it does:** Accepting twice is a 409 "This goal was already decided.". Another user's goal is a 404 "Goal not found." for both accept and dismiss, and stays proposed. A missing id is a 404.

**Why it's there:** A decision is final, and every goal is scoped to its user.

## `test_accept_expired_proposal_says_it_expired`

**What it does:** Accepting a superseded goal is a 409 "This proposal expired. Generate goals again." and it stays `superseded`. Dismissing it is a 409 "This goal was already decided."

**Why it's there:** The user never decided an expired proposal, so "already decided" would be wrong.

## `test_accept_commits_when_the_checkpointer_cannot_open`

**What it does:** `get_checkpointer` raises. Accepting is still a 200 with the goal `accepted`, and its run stays `awaiting_user`. Once the checkpointer works again, `GET /goals` finishes the run.

**Why it's there:** The spec says a decision commits before any resume. An unopenable checkpoint file must not turn a click into a 500 with the row left `proposed`.

## `test_accept_without_start_date_is_409`

**What it does:** A goal with no `start_date` (a row from before TPC-25) can't be accepted: 409, with a message telling the user to generate again, and it stays proposed.

**Why it's there:** Every later close would record an error run for a plan with no first month.

## `test_accept_second_of_a_kind_is_409`

**What it does:** With a savings goal accepted, accepting another proposed savings goal is a 409 saying the proposal is older or a goal of that kind is already accepted, and to generate again. It stays proposed.

**Why it's there:** The budget holds one line per kind.

## `test_dismiss_proposed_and_stop_accepted`

**What it does:** Dismissing a proposed goal and an accepted one both give `dismissed`. Dismissing either again, or a completed goal, is a 409.

**Why it's there:** "Stop" on an accepted goal is the same route as "Dismiss"; a finished goal can't be undone.

## `test_run_out_reports_the_surplus_source`

**What it does:** Runs goal generation on the forecast path and checks its public source label.

**Why it's there:** The user needs to know the affordability basis.
