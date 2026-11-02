# TPC-24: TDD implementation plan

Date: 2026-11-02. Status: approved and implemented on `codex/tpc-24-goal-validation`; verification complete.

Design: [Goal validation and bounded correction](../specs/2026-11-02-tpc-24-goal-validation-design.md).
Ticket: [TPC-24](https://ucf-ctcw-senior-design.atlassian.net/browse/TPC-24).

## Working rules

- The user reviewed this plan and authorized implementation before any code changes.
- For each slice: write meaningful behavior tests, run them and verify the expected failure, implement the smallest change, rerun for green, then refactor only while green. Record the actual command and concise observed result below.
- Do not claim existing green tests as red/green evidence. Do not commit a deliberately failing suite unless explicitly requested.
- Keep the total model-call limit at two, including malformed output; no lower-level automatic retry.
- Use the existing in-memory database fixtures and `ScriptedChatModel` for deterministic cases. Keep local development data and unrelated untracked evaluation results intact.
- Document every added/changed test in the relevant `docs/tests/` module.
- Created `codex/tpc-24-goal-validation` from the reviewed TPC-23 state before code changes. Unrelated evaluation outputs were left untouched.
- Tests live in dedicated `test_goal_validation.py` and `test_goal_validation_graph.py` modules; existing graph tests were updated where their TPC-23 expectations changed.

## Slice 1: Pure allocation validation

Files: `budget_engine/goals.py`, `tests/test_goals.py`, `docs/tests/test_goals.md`.

- [x] Test a valid single/multiple-goal allocation.
- [x] Test a sum exactly at the cap, one cent over, and multiple individually affordable goals whose sum is unaffordable.
- [x] Test that each amount is converted/rounded to cents once, and summing those cents gives the same figure checked and stored; include fractional-cent boundaries.
- [x] Test duplicates and preference-excluded/unoffered kinds reject the complete allocation.
- [x] Retain schema bounds; test the normalization/validation boundary without importing agent types into the engine.
- [x] Run the new tests, inspect the failure, implement pure validation, and rerun.
- [x] Document the tests and record evidence.

## Slice 2: Computed plan validation

Files: `budget_engine/goals.py`, `tests/test_goals.py`, `docs/tests/test_goals.md`.

- [x] Test valid dates for debt, emergency fund, and savings against existing builders.
- [x] Test a non-amortizing debt candidate and an emergency fund beyond the planner horizon are rejected.
- [x] Test the horizon boundary: 600 payments is valid; a goal needing 601 is not.
- [x] Keep the low-level tests showing that builders can describe impossible goals with null dates.
- [x] Test invalid computed dates/targets fail before any persistence, using focused pure tests where inputs can express those conditions.
- [x] Run red, implement, run green, and document.

## Slice 3: Shared two-call correction loop

Files: `agents/goal_llm.py`, `agents/goal_graph.py`, `privacy/gateway.py`, `tests/test_goal_graph.py`, `tests/test_gateway.py`, and corresponding test docs.

- [x] Invalid over-cap proposal followed by a valid correction: exactly two calls, only corrected goals persisted.
- [x] Two invalid proposals: exactly two calls, error reasons returned, no new goals, earlier proposal preserved.
- [x] Malformed JSON followed by valid output succeeds in two calls.
- [x] Malformed JSON followed by over-cap output fails without a third call.
- [x] Over-cap output followed by malformed JSON fails without a third call.
- [x] Null computed date followed by a valid dated allocation succeeds; repeated null-date failure exhausts the same call budget.
- [x] Duplicate/unoffered kind followed by correction succeeds; no partial goals survive either attempt.
- [x] Feedback gathers all computable failures for a multi-error proposal, including over-cap and missing dates, without building unsafe allocations.
- [x] Correction uses the same debts, facts, and scrubbed context snapshot as the initial attempt; an intervening database update cannot silently change its inputs.
- [x] Every successful run replaces the earlier proposal atomically; failures leave it untouched.
- [x] First-attempt valid output uses one call. Model outage uses one attempted call, reports the outage, and preserves prior rows.
- [x] Corrective messages contain the same scrubbed facts, rejected normalized kinds/amounts/strategies, and all actionable code-generated reasons, without user identifiers, unsanitized notes, rejected rationale, raw parser exceptions, or raw invalid output.
- [x] Malformed/schema-invalid output gets a fixed contract explanation and no echoed rejected allocation.
- [x] Inspect messages through the scripted model; assert the gateway records the outgoing correction context, attempt number, and the same pseudonym.
- [x] Assert per-attempt failure evidence survives a successful correction in the run summary; earlier failures do not block the corrected result.
- [x] Run red before implementing graph routing and correction messages; run green and document.

## Slice 4: Impossible cap and observable outcomes

Files: `agents/goal_graph.py`, `tests/test_goal_graph.py`, `tests/test_goals_api.py`, corresponding test docs.

- [x] Positive surplus with cap 0–99 cents: no model call, `no_affordable_goals`, prior proposal withdrawn, successful completed run with an explicit reason.
- [x] Cap exactly 100 cents allows a $1 savings proposal.
- [x] Keep no history, nonpositive surplus, no offered kinds, simulated date, card minimums, locking, cross-process busy detection, and interruption regressions passing.
- [x] Run summaries record attempts and validation reasons for success after correction and exhaustion.
- [x] CLI success prints only validated goals; exhaustion exits nonzero and explicitly distinguishes retained older goals from new results.
- [x] CLI no-affordable-goals reports the cap/minimum problem and withdrawal.
- [x] `GET /goals` shows corrected new rows after success, preserved old rows after error, and an empty list after an impossible-cap withdrawal; other users remain isolated.
- [x] Replace obsolete graph/CLI tests asserting over-cap storage, undated publication, or dropping invalid kinds. Preserve historical API serialization tests.
- [x] Run red, implement, run green, and document.

## Slice 5: Acceptance test and regressions

- [x] Create a deterministic tight-surplus fixture whose cap is independently established from its transactions/minimums/preferences.
- [x] Script deliberately unaffordable replies rather than relying on the real model to make a mistake.
- [x] Prove invalid-then-valid returns/persists only a fitting proposal; invalid-twice returns no new goals and stores none.
- [x] Assert failed runs expose no rejected candidate plans as usable results; allocations in validation feedback are explicitly diagnostic.
- [x] Include an earlier proposal in the failure scenario so “no new goals” cannot be confused with “the API has no older rows.”
- [x] Assert the sum of persisted new monthly cents is at most the cap and every new goal is dated.
- [x] Run focused goal/LLM/gateway/API tests, then the complete offline backend suite once the focused checks pass.
- [x] Add five parameterized live trials using `goals-card-24` with `max_share=0.05`. Assert surplus 97175 cents and cap 4859 cents independently of model output; successful new goals must fit and be dated.
- [x] Record each live trial's success/failure, call count, total, cap, and dates. An exhausted run preserves safety but is a failed usefulness trial. If Ollama/model is unavailable, report five skipped trials explicitly.

Commands, from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_goals.py tests/test_goal_graph.py tests/test_goals_api.py tests/test_goal_prefs.py tests/test_budget_llm.py tests/test_gateway.py -q -k 'not live and not sandbox'
../venv/bin/python -m pytest -q -k 'not live and not sandbox'
```

Implemented acceptance test entry points:

```bash
../venv/bin/python -m pytest tests/test_goal_validation_graph.py::test_tight_surplus_retries_to_affordable_dated_goals tests/test_goal_validation_graph.py::test_tight_surplus_exhaustion_preserves_previous_proposal -q
../venv/bin/python -m pytest tests/test_goal_validation_graph.py::test_live_tight_surplus_trials -q -s
```

The second command collects five parameterized trials. Both commands were executed successfully. No frontend suite is required unless implementation unexpectedly changes frontend behavior.

## Slice 6: Documentation and user handoff

Files: `docs/architecture.md`, relevant README goal instructions, test docs, this plan's evidence record, and `docs/handoffs/2026-11-02-tpc-24-manual-verification.md`.

- [x] Update the architecture's implemented behavior and decision log; link the detailed design without replacing unrelated architecture content.
- [x] Remove current documentation claims that new over-cap/undated goals may be stored, and explain the changed duplicate/unoffered-kind behavior.
- [x] Write a concise handoff in the TPC-23 format, running from `backend/` with `../venv/bin/python`.
- [x] Include environment setup, a dedicated throwaway database/audit path, migration, synthetic user seeding, commands, expected cap/date outcomes, and cleanup.
- [x] Include the focused scripted pytest commands above for rejection/correction/exhaustion, and a live CLI smoke check; no standalone verification script or production debug flag.
- [x] Explain preserved earlier proposals after failure, the below-$1 no-call outcome, and where attempts/reasons can be inspected.
- [x] Execute the documented commands against disposable data and correct any mismatches before delivery.
- [x] Record actual results and unverified areas; do not reuse TPC-23's test count as a post-change result.

## TDD and verification evidence

Observed during implementation, in order. Commands run from `backend/`; the new pure/graph modules split the originally proposed tests for focused execution.

| Slice | Red command and observed failure | Green command and observed result | Refactor/check notes |
|---|---|---|---|
| Allocation validation | `../venv/bin/python -m pytest tests/test_goal_validation.py -q`: **11 failed**, missing allocation/validation helpers | Same command: **11 passed** | Rounding, combined cap, bounds, duplicate/unoffered kinds |
| Computed plans | Same pure-module command after adding date tests: **7 failed, 11 passed**, missing plan validator | `../venv/bin/python -m pytest tests/test_goal_validation.py tests/test_goals.py -q`: **35 passed** | Includes all three kinds, non-amortizing debt, 600/601 boundary |
| Two-call loop | `../venv/bin/python -m pytest tests/test_goal_validation_graph.py -q -k "not live"`: **11 failed, 5 deselected**; old code accepted over-cap replies/reloaded changed debts and lacked call/history evidence | Same command after implementing correction: **11 passed, 5 deselected** | Mixed parsing/amount errors, snapshot, feedback privacy and audit |
| Outcomes/CLI/API | `../venv/bin/python -m pytest tests/test_goal_validation_graph.py -q -k "cap_below or cli_failure or api_reflects or exact_minimum"`: **3 failed, 2 passed**; CLI crashed on no-affordable-goals and omitted exhaustion details | Focused pure/graph/legacy/gateway/API command: **123 passed, 6 deselected** | Six obsolete TPC-23 tests were updated to whole-proposal rejection, not removed |
| Tight-surplus acceptance | Included in the graph red run above: old code stored an unaffordable reply and replaced earlier goals | Two named acceptance cases: **2 passed**; pure/new graph suite: **34 passed, 5 deselected** | No additional implementation needed after the correction loop |

Pre-change baseline on TPC-23: focused goal suite **78 passed, 1 deselected**; offline backend suite **773 passed, 16 deselected**.

Final verification:

- Focused pure/new graph/legacy graph/gateway/API suite: **123 passed, 6 deselected**.
- Full offline backend suite: **807 passed, 21 deselected**, 10.81 seconds.
- Five live `qwen2.5:14b` trials: **5 passed**, 20.46 seconds. Every trial used one call, total/cap **4859 cents**, with debt target date **2033-03-01**.
- Existing live high-interest-debt regression: **1 passed**, 6.39 seconds. Debt payoff remained first at 58300 cents/month, emergency fund second at 19440, exactly the 77740-cent default cap, with dates 2029-02-01 and 2029-11-01.
- Initial live invocation inside the sandbox skipped all five because localhost was inaccessible. A check outside the sandbox found Ollama and the required model; the five trials were then actually run. The final live outcome is five passes, not five skips.
- Fresh throwaway CLI database: migrations, seed, tight-cap live run and stored-row inspection, recorded run summary, below-$1 no-call withdrawal, and cleanup verified.
- The two focused acceptance commands in the handoff were executed: **2 passed**, then **34 passed, 5 deselected**.
- The rewritten manual walkthrough was also executed verbatim through steps 1–5 and cleanup: live output and saved rows, controlled $50 → $48.59 correction with inspected feedback, two unaffordable replies preserving the prior row, and $0.01-cap withdrawal with zero calls. The temporary `demo` function exercises the real graph without pytest or a production debug flag.
- Docker follow-up: offline backend suite **807 passed, 21 deselected** (13.78 seconds); five live tight-surplus trials **5 passed** (16.26 seconds). The container reached host Ollama and each live allocation fit the 4859-cent cap.
- Linked-data follow-up: migrated a disposable copy of the existing database inside Docker and refreshed all four Plaid Sandbox items successfully. Counts remained 56 accounts / 240 transactions / 12 liabilities. Before and after refresh, the graph returned `no_surplus` with zero model calls and both `/goals` and `/dashboard` returned HTTP 200. Original database SHA-256 unchanged. See the [verification report](../../handoffs/2026-11-02-tpc-24-docker-linked-verification.md).
- Production Plaid data and browser/frontend execution were not exercised. No frontend behavior changed.

An early broad regression invocation still had the six intentionally obsolete TPC-23 expectations and failed those six; after updating them, the focused and full suites above passed. No known failing tests remain.

## Completion criteria

- No new goal proposal is persisted/returned as successful unless the entire proposal passes allocation and computed-plan checks.
- No run makes more than two model calls, including parsing/schema errors.
- Tight-surplus acceptance cases, regression checks, and selected live requirements pass or are explicitly reported according to the user's verification choice.
- The concise verification handoff is accurate and executable, and red/green evidence is recorded.
- The user reviewed the plan and authorized implementation; the TDD work, five live trials, and verification handoff are complete.
