# Tests: `backend/tests/test_goal_approval_graph.py`

These tests cover the approval pause in `app/agents/goal_graph.py` (TPC-25): `persist → forget_facts → await_approval`, `run_goals_locked`, `resume_goal_run`, `goal_waiting_for`, `finish_paused_run`, and `_supersede` expiring paused runs. The goal rows are the source of truth; `interrupt()` only tracks the run.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_goal_approval_graph.py -v
```

## Shared setup

- `audit_in_tmp` (autouse) keeps the prompt audit log under `tmp_path`.
- The `user` fixture seeds `goals-card-24` (`eval/budget_users.py`): a $971.75 monthly surplus, a card called Platinum Card and a loan called Student Loan.
- `ck` is an `InMemorySaver`; the privacy test uses a real `SqliteSaver` instead.
- `g` and `model` script the model's answer, as in `test_goal_graph.py`. `stored` reads goals by status, `run_row` reads a run, `set_status` stands in for the user's decision committing, and `thread_of` is a run's checkpoint thread.
- `go_red` adds spending that leaves no surplus, so the next run withdraws.
- `click_before_the_mark` decides the first goal just before the run writes `awaiting_user`.

---

## `test_run_pauses_after_persisting`

**What it does:** A run with a checkpointer returns `awaiting_user`, waiting for its goal. The goal is `proposed`; the run row is `awaiting_user`, unfinished, with the goal ids, the surplus and outcome `awaiting_user` in its summary.

**Why it's there:** The pause itself, and the page reads the row while the run waits.

## `test_no_checkpointer_does_not_pause`

**What it does:** Without a checkpointer the run ends `ok`, and the plans are still in the result.

**Why it's there:** The CLI has nowhere to pause and prints those plans.

## `test_no_goals_means_no_pause`

**What it does:** A `no_surplus` run with a checkpointer ends `ok`, with nothing waiting and no checkpoint.

**Why it's there:** With no goal there's nothing to decide.

## `test_goals_changed_ends_without_pausing`

**What it does:** A goal accepted during the model call makes the run end `error` with reason `goals_changed`, without pausing or leaving a checkpoint.

**Why it's there:** That run wrote nothing, so it can't wait for anything.

## `test_pause_releases_the_lock`

**What it does:** After a pause, the user's lock is free and `running_elsewhere` says no.

**Why it's there:** A paused run can wait for days. It mustn't block budget runs or a new proposal.

## `test_paused_checkpoint_holds_no_debts_or_facts`

**What it does:** Runs with a real `SqliteSaver`, through one correction round, and reads every row of the `checkpoints` and `writes` tables as bytes. Neither debt name nor the rationale is there, and `get_state` shows empty debts, plans, allocations, proposal and context. It then resumes on that saver and finds both tables empty.

**Why it's there:** The privacy rule for checkpoints. It checks the bytes because a saver's own read-back wouldn't show what earlier steps left on disk.

## `test_failed_run_leaves_no_checkpoint`

**What it does:** The model call raises `RuntimeError`, then `KeyboardInterrupt`. Each time the run row is `error` and the thread has no checkpoint.

**Why it's there:** `durability="exit"` writes a checkpoint when a run raises, with the facts still in it.

## `test_finished_run_deletes_its_checkpoint`

**What it does:** A paused run has a checkpoint; after its goal is decided and it's resumed, it has none.

**Why it's there:** Only a paused run needs one.

## `test_resume_waits_for_every_goal`

**What it does:** With two goals, the first resume reports `awaiting_user` and the second goal, and the second finishes the run. The summary has both decisions, and keeps the surplus, attempts and goal ids.

**Why it's there:** Each goal is decided on its own, and the final row still says what the run measured.

## `test_rows_decide_not_the_resume_payload`

**What it does:** A resume whose row is still `proposed` pauses again. Once the row says `dismissed`, a resume carrying `accepted` records `dismissed`.

**Why it's there:** A decision commits before any resume, so the row is the answer.

## `test_resume_never_calls_a_model`

**What it does:** Resuming with `get_chat_model` patched to fail still finishes.

**Why it's there:** A broken model setting can't block a decision.

## `test_a_second_resume_of_a_finished_run_reports_it`

**What it does:** Resuming a finished run returns its status and an empty `waiting_for`.

**Why it's there:** Two clicks can queue on the per-run lock; the second finds the work done.

## `test_failed_resume_keeps_waiting`

**What it does:** The database fails during `await_approval`. The resume raises, the run stays `awaiting_user`, and the next resume finishes it.

**Why it's there:** A failed resume never loses a click.

## `test_decision_before_the_pause_is_mark_still_finishes`

**What it does:** A decision lands between the graph pausing and the `awaiting_user` mark. `run_goals` resumes it, so the run ends `ok` with that decision and no checkpoint.

**Why it's there:** That click found no paused run to resume, and nothing else would finish this one.

## `test_early_decision_on_one_goal_keeps_waiting_for_the_other`

**What it does:** The same, with two goals: the run stays `awaiting_user`, waiting only for the second.

**Why it's there:** An early decision shouldn't be asked for again.

## `test_failed_early_resume_still_reports_the_pause`

**What it does:** The early resume raises. The run still returns `awaiting_user` and keeps waiting.

**Why it's there:** The proposal is stored and the checkpoint is fine, so the run isn't a failure.

## `test_regenerating_expires_the_paused_run`

**What it does:** A second proposal marks the first run `expired`, finished, with `expired_goal_ids`, and deletes its checkpoint. The second run is the one waiting.

**Why it's there:** A run can't wait on goals that have been superseded.

## `test_a_checkpoint_that_will_not_delete_is_only_logged`

**What it does:** Deleting the expired run's thread raises. The new run still pauses, the old one is `expired`, and a warning names the thread.

**Why it's there:** Those rows are already committed, and that checkpoint holds no facts.

## `test_failed_regeneration_keeps_the_paused_run`

**What it does:** Two rejected attempts leave the first run `awaiting_user`, its goal `proposed` and still waiting.

**Why it's there:** A failed run leaves the last proposal standing.

## `test_withdrawing_expires_the_paused_run`

**What it does:** A later `no_surplus` run withdraws the proposal, expires the paused run and deletes its checkpoint.

**Why it's there:** Withdrawing supersedes goals too.

## `test_a_leftover_checkpoint_can_not_revive_an_expired_run`

**What it does:** `expire_goal_proposals` expires a paused run and its checkpoint stays, as it does at close. `goal_waiting_for` is `[]`, and a late resume reports `expired` without changing the row.

**Why it's there:** Close can't reach the checkpointer, so the checkpoint alone can't be what says a run is waiting.

## `test_restart_cleanup_leaves_a_paused_run`

**What it does:** `close_interrupted_runs` closes nothing and the run stays `awaiting_user`.

**Why it's there:** A paused run has to survive a restart.

## `test_reused_run_id_starts_clean`

**What it does:** After `delete_user` and a reseed, the new run gets the old id. Its thread is already empty when `on_start` runs, and it pauses on its own goal.

**Why it's there:** SQLite reuses the id of a deleted run, and the new run mustn't start on the old thread.

## `test_checkpointer_failure_marks_error`

**What it does:** A saver that can't delete makes the run end `error`, with the row saying so.

**Why it's there:** Setup that fails before the graph starts mustn't leave a run `running`.

## `test_finish_paused_run_from_rows`

**What it does:** With one goal still `proposed`, nothing changes. Once both are decided, the run is `ok` and finished, with outcome `ok`, decisions from the rows, and the rest of the summary kept.

**Why it's there:** A lost checkpoint can't resume, and the run still has to end.

## `test_finish_paused_run_leaves_other_statuses`

**What it does:** An expired run stays `expired`.

**Why it's there:** It only finishes runs that are waiting.
