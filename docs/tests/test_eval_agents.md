# Tests: `backend/tests/test_eval_agents.py`

These tests cover `eval/agent_runner.py`, which runs budget and goal scenarios through the real graphs (TPC-26). They also check every committed golden answer against the code, with no model, so a wrong golden answer fails here instead of looking like an agent failure in a benchmark run.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_eval_agents.py -v
```

## Shared setup

- `pytestmark` sets `cash_forecast` and `forecast_surplus`. `tests/conftest.py` swaps the forecast out for the rest of the suite, whose fixtures have static balances. The eval runs the real one, so these tests must too.
- `SCENARIOS` loads the committed files in `eval/scenarios/`.
- `SchemaReplyModel` answers any structured call with a valid reply, and `ScriptedChatModel` replays given replies. A scripted model with no replies raises if it's called.
- `KNOWN_FAILURES` lists scenarios whose golden answer is right and whose agent isn't yet, each with its ticket.

---

## The runners

## `test_budget_run_records_flags_and_alerts_by_day`

**What it does:** Runs `budget-one-time-new-shop` and checks the record's id, run number and kind, that General Merchandise was first flagged on Oct 12, and that the first overspend alert has that day, its category, cause and action.

**Why it's there:** Scoring reads only this record, so a wrong day or field here would make time-to-flag and cause accuracy wrong for every model.

## `test_budget_run_lists_each_alert_once`

**What it does:** Runs `signal-price-increase`, whose window has several daily runs, and checks that the price alert appears once, on Oct 3.

**Why it's there:** The runner reads every alert after every run. Listing one twice would double-count it, and giving it a later day would inflate time-to-flag.

## `test_budget_run_counts_model_failures`

**What it does:** A model that replies with text that isn't JSON still gives a finished run. `llm_failures` is above 0, and the first alert is the code's plain one: no cause, action `alert`, and a message about the limit.

**Why it's there:** This is the safety net the fallback scoring rule relies on: a model failure must never hide an overspend.

## `test_budget_run_audits_to_its_own_file_and_restores_the_setting`

**What it does:** With `PROMPT_AUDIT_PATH` pointing at a stand-in for the real log, runs a scenario. The run's audit file exists, the stand-in doesn't, and the setting is back to what it was.

**Why it's there:** `advance_days` takes no audit path, so the runner swaps the setting. Eval prompts must never reach the app's real audit log.

## `test_budget_run_failure_is_a_result_and_restores_the_setting`

**What it does:** With `advance_days` raising, the runner returns a record holding the error, restores the setting, and every check on that record fails.

**Why it's there:** One crash mustn't stop a long eval, leave the setting swapped, or score as a pass.

## `test_goal_run_records_plans_in_priority_order`

**What it does:** A scripted proposal on `goal-debt-first` records status `ok`, one attempt, the surplus and cap from the real forecast ($1,020 and $816), the offered kinds, and the plans in priority order with the code-computed strategy and date. The audit file exists.

**Why it's there:** The goal checks read these fields, and the cap has to come from the same forecast the app uses.

## `test_goal_run_applies_the_scenarios_prefs`

**What it does:** `goal-respects-skipped-kind` skips debt goals, and `debt_payoff` isn't offered.

**Why it's there:** A scenario's preferences must reach the real graph, or the banned-kind check would test nothing.

## `test_goal_run_reports_a_retry`

**What it does:** An over-cap proposal followed by a valid one gives two attempts and the second plan. The score reports two attempts, and the plan is under the cap.

**Why it's there:** Retries are reported and never fail a run on their own, per the TPC-26 decision.

## `test_goal_run_with_a_broken_model_is_a_result`

**What it does:** A model that can't be reached gives status `error`, no plans, and a failed score.

**Why it's there:** The goal graph reports an unreachable model as a status, so the eval must score that as a failure rather than crash.

## `test_goal_setup_failure_is_a_result`

**What it does:** With `run_goals` raising, the record holds the error and has no status.

**Why it's there:** The same never-raises rule as the budget runner.

---

## The golden answers

## `test_chat_golden_answer_passes_its_own_facts`

**What it does:** For each chat scenario, scores the golden answer against the scenario's own facts and abstain rule.

**Why it's there:** A golden answer that fails its own check could never be passed by the agent, and would drag similarity down too.

## `test_chat_number_facts_come_from_the_expected_tools`

**What it does:** For each chat scenario, seeds its user, calls the tools it expects through the real `make_tools` and gateway, and checks that every number fact appears in their output.

**Why it's there:** The design keeps arithmetic out of the model. A number no tool returns would test the model's arithmetic, or be a mistake by the scenario's author.

## `test_debt_none_has_no_debts_to_find`

**What it does:** `get_debts` returns an empty list for `debt-none`.

**Why it's there:** Its only right answer is saying there are none, so its user must really have none.

## `test_budget_golden_answer_states_its_message_facts`

**What it does:** Each budget scenario's golden answer contains the facts it requires of the alert message.

**Why it's there:** The same self-consistency check as for chat.

## `test_budget_code_decisions_match_the_golden_answer`

**What it does:** Runs each budget scenario with a fake model and checks what code decides, whatever the model says: `flags`, `flag_by`, `alert_kinds` and `lead_time`. A scenario in `KNOWN_FAILURES` must still fail one of them.

**Why it's there:** It separates a bad scenario or a code problem from a model's choice, and needs no Ollama. A known failure that starts passing fails the test, so its entry and ticket get closed instead of going stale.

## `test_signal_fallback_messages_carry_their_facts`

**What it does:** With a model whose signal messages don't name the charge, the two signal scenarios still pass their message check.

**Why it's there:** The graph rejects a signal message that doesn't quote the charge and uses a message written by code. These facts must hold on that path.

## `test_goal_scenario_passes_with_a_sensible_model`

**What it does:** Each goal scenario passes every check given a reply built from its own expected answer. A scenario where nothing is affordable gets no replies, so any model call would fail its run.

**Why it's there:** It proves each scenario can be passed, and that the affordability endings really are decided by code before the model is asked.
