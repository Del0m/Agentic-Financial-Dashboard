# Tests: `backend/tests/test_goal_decisions.py`

These tests cover `app/budget_service/goal_decisions.py` (TPC-25): `decide_goal` (accept, dismiss or stop a goal) and `finish_decided_runs` (continue a paused goal run whose goals were decided). The goal rows are the source of truth; a resume only continues the run.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_goal_decisions.py -v
```

## Shared setup

- `audit_in_tmp` (autouse) keeps the prompt audit log under `tmp_path`.
- The `user` fixture seeds `goals-card-24` (`eval/budget_users.py`), as in `test_goal_approval_graph.py`; `ck` is an `InMemorySaver`.
- `g` and `model` script the model's answer. `paused` runs the goal graph with the checkpointer and returns the paused run.
- `add_goal` inserts a goal row directly (any kind, status and dates), for cases a run would not produce. `goal` and `run_row` read a row back.

---

## `test_accept_marks_the_row_and_resumes`

**What it does:** Accepting the only goal of a paused run marks it `accepted`, returns it, and the run ends `ok` with nothing left waiting.

**Why it's there:** The main path: the click is written to the row and the run continues.

## `test_other_users_goal_is_not_found`

**What it does:** Another user's id, and an id that doesn't exist, raise `GoalNotFound`. The goal stays `proposed`.

**Why it's there:** A user can't decide on goals that aren't theirs, and the API can answer 404.

## `test_accept_twice_is_already_decided`

**What it does:** A second accept raises `GoalAlreadyDecided`; the status stays `accepted`.

**Why it's there:** A double click must not count as a second decision.

## `test_accept_after_dismiss_is_already_decided`

**What it does:** Accepting a dismissed goal raises `GoalAlreadyDecided` and leaves it `dismissed`.

**Why it's there:** Only a proposed goal can be accepted.

## `test_superseded_goal_cannot_be_accepted`

**What it does:** A superseded goal raises `GoalAlreadyDecided` on accept.

**Why it's there:** A newer run replaced it; the user's page is out of date.

## `test_completed_goal_cannot_be_dismissed`

**What it does:** A completed goal raises `GoalAlreadyDecided` on dismiss and stays `completed`.

**Why it's there:** Dismiss stops a proposed or accepted goal, not a finished one.

## `test_dismiss_stops_an_accepted_goal`

**What it does:** Dismissing an accepted goal makes it `dismissed`.

**Why it's there:** This is how a user stops a goal they already accepted.

## `test_row_without_start_date_cannot_be_accepted`

**What it does:** A proposed row with no `start_date` raises `GoalNotAcceptable` on accept and stays `proposed`. It can still be dismissed.

**Why it's there:** Rows from before TPC-25 have no start month, so no budget line can be made from them. The user must still be able to clear them.

## `test_row_without_target_date_cannot_be_accepted`

**What it does:** The same for a row with no `target_date`.

**Why it's there:** Close assumes an accepted row has both dates. One accepted row without a target would make every later close for that user record an error run.

## `test_second_goal_of_an_accepted_kind_is_refused`

**What it does:** With a debt goal accepted, accepting another proposed debt goal raises `GoalNotAcceptable` and leaves it `proposed`. A goal of another kind is accepted normally.

**Why it's there:** The budget holds one line per kind. A run that overtook the first accept can propose a second goal of that kind.

## `test_a_failing_resume_keeps_the_decision`

**What it does:** With `resume_goal_run` raising, `decide_goal` still returns the `accepted` goal, logs the failure, and the run stays `awaiting_user`. `finish_decided_runs` then resumes it and the run ends `ok`.

**Why it's there:** The decision is committed before any resume, so a broken resume never loses the click, and a later call repairs the run.

## `test_lost_checkpoint_keeps_the_decision_and_ends_the_run`

**What it does:** With the run's checkpoint thread deleted, accepting still works and raises nothing. With no goal left proposed, the run ends `ok` and its summary records the decision.

**Why it's there:** A lost checkpoint must not strand a run in `awaiting_user`; the rows finish it.

## `test_lost_checkpoint_waits_for_the_other_goals`

**What it does:** With two goals and no checkpoint, deciding one leaves the run `awaiting_user`; deciding the second ends it `ok`.

**Why it's there:** The run is only done when every goal it proposed has an answer.

## `test_only_goal_runs_are_resumed`

**What it does:** A paused budget run for the same user is never looked at while a goal decision resumes the goal run.

**Why it's there:** Both kinds of run share `awaiting_user`; a goal click must not touch the budget graph's checkpoints.

## `test_the_conditional_update_lets_one_decision_through`

**What it does:** After one accept commits, a second call whose first read is stale (it still sees `proposed`) raises `GoalAlreadyDecided` and changes nothing. The same for a stale dismiss after a dismiss.

**Why it's there:** The status check lives in the `UPDATE`, not in the read, so two requests can't both win. The two calls run in sequence with a stale read standing in for the race, because the in-memory test database is one connection that threads can't share.
