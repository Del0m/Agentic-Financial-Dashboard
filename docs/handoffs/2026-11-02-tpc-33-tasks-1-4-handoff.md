# TPC-33: handoff after Tasks 1 to 4

Date: 2026-11-02. Tasks 1 to 4 of 8 are done on branch `TPC-33`. **Task 5 has not started.** Recurring alerts now run in the backend; the recurring API, chat tool and dashboard card are still pending. Nothing was pushed.

The previous [Tasks 1 to 3 handoff](2026-11-02-tpc-33-tasks-1-3-handoff.md) still applies for detector measurements, migration verification, known limits and unrelated local edits. Resume at Task 5 in the [implementation plan](../superpowers/plans/2026-11-02-tpc-33-recurring-streams.md).

## What Task 4 added

- `budget_engine/signals.py`: signal ranking, the three-call cap, protected categories, action guards, cancellation-limit maths and code-written messages.
- `budget_llm.py`: `SignalAction`, the signal prompt and `decide_signal`. It makes one structured call with no retry, so malformed output cannot exceed the cap.
- The daily graph refreshes streams after updating totals, then checks overspend and decides signals before logging advice. `no_budget` and `period_ended` also refresh streams and decide signals.
- Each change becomes `<kind>|<stream_id>|<token>`. Existing alert keys are excluded before model calls and checked again when writing. Signal and overspend alerts can share a category.
- Changes past the cap, invalid replies and model outages still get plain alerts. Detector and decision errors record only their type in `signals_error`.
- The recurring gateway context has explicit fields and dollars. Outflow merchant names are scrubbed; inflows expose only category `INCOME`, frequency and typical amount. Each signal builds one audited context, including signals beyond the model-call cap.
- `summary_json` includes `as_of`, `streams: {count, new, price_up, income_change}` and `signals_error`. Change lists contain stream ids, never names or messages.

## Details the next task needs

1. **Cancellation is still only a proposal.** Optional charges may produce a `pending_user` `suggest_cancel` alert with a code-computed `proposed_limit_cents`. Task 5 must implement its effect at close and stream muting. No cancellation alert pauses the graph.
2. **No usable line means alert only.** A cancellation suggestion becomes `alert` when the current category line is missing, its reduced limit would be zero or below, or the budget has ended. The model's cancellation text is replaced in that case.
3. **Price copy uses the change.** `StreamChange.previous_cents` is copied into the signal even if the stored row has cleared its previous amount. Valid model copy naming the charge and quoting both prices is kept; incomplete copy is replaced. Income copy is code-written and never names an employer.
4. **Checkpoint privacy requires save-on-exit.** Initial and resumed budget invocations use `durability="exit"`. `log_advice` stores the summary in `AgentRun`, then clears signals, signal actions and overspend prompt facts before approval. Merely clearing the final state would leave earlier SQLite checkpoints holding merchant names and amounts.
5. **CLI reporting remains available.** An optional process-local `report` dict preserves overspend results for `run_budget`'s return value. It is never put into checkpoint state. `persist_state` finishes the summary already stored in the run row and adds decisions.
6. **The low-balance path is reserved.** Signal ranking and action names include it, but this task produces only recurring signals. Cash-flow context, balance guards, repeat rules and forecast routing belong to the later forecast implementation.
7. **Use the existing run lock.** The alert-key guard is a read/check/write under the normal daily user lock; no database uniqueness constraint was added. Direct concurrent `run_budget` calls are not a supported replacement for that lock.

## Verification

From `backend/`:

```bash
../venv/bin/python -m pytest tests/test_signals_engine.py tests/test_recurring_graph.py tests/test_gateway.py tests/test_budget_graph.py tests/test_budget_graph_cli.py tests/test_simulate_cli.py -q -k 'not live and not sandbox'
../venv/bin/python -m pytest -q -k 'not live and not sandbox'
```

The final full backend suite passed: **1,135 passed, 22 deselected**. This adds 39 cases to the prior handoff's 1,096. `git diff --check` also passed.

The tests cover quiet runs, same-category overspend and stream alerts, users without a current budget, duplicate keys, five signals with three calls and five audit entries, protected rent, cancellation without an interrupt, old/new price copy, model failures without retries, detector/decision/preparation failures, income-name privacy and summaries without merchant text. A real SQLite saver is inspected at the row, snapshot and file-byte levels; the paused run also resumes successfully.

Every added or changed test has a test-doc entry. An existing unrelated gateway test, `test_goals_note_extracts_priority_without_forwarding_text`, already lacked an entry and was left unchanged.

No frontend files changed, so frontend checks were not run. Live models, Plaid Sandbox and Postgres were not run. The detector was not changed, and its separate accuracy gate was not rerun; its existing accuracy tests are included in the passing suite.

## Next

Task 5: accepted `suggest_cancel` values apply after accepted `adjust_next_budget` values at close, skip missing/nonpositive lines with recorded outcomes, and mute later signals for the accepted stream. Pending cancellation suggestions must survive month-end. Then continue with Tasks 6 to 8 for API, chat, UI and final verification.

The earlier warning about PR #23's description and unrelated local documentation, diagrams and eval outputs remains open. None of those files was changed or staged for Task 4.
