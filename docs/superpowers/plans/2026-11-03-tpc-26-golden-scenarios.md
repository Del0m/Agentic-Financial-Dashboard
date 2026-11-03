# TPC-26: golden scenarios implementation plan

Design: [`2026-11-03-tpc-26-golden-scenarios-design.md`](../specs/2026-11-03-tpc-26-golden-scenarios-design.md). The branch `TPC-26` is cut from `TPC-31`, whose recurring and forecast tools the scenarios use.

## Task 1: scenario format

- `eval/fixtures.py`: add `kind` and `tags`, `BudgetScenario` and `GoalScenario`, `expect.abstain`, and validation that names the file and field.
- `eval/budget_users.py`: add `profile_by_id`. `simulate.advance_days` uses it in place of its own list.
- Tests: `test_eval_fixtures.py`.

## Task 2: runners and scoring

- New `eval/agent_runner.py`: `run_budget_once` and `run_goal_once`, which never raise and keep eval prompts out of the real audit log.
- `eval/metrics.py`: `check_abstain`, `score_budget`, `score_goal`, `summarize_budget`, `summarize_goal`, and pass^3 and tags in `summarize`.
- `eval/run_eval.py`: `--kind`, what's needed comes from the scenarios that will run, and table rows per kind.
- Tests: `test_eval_metrics.py`, `test_eval_cli.py`, and the runner half of `test_eval_agents.py`.

## Task 3: synthetic users

- `SCENARIO_USERS` in `eval/budget_users.py`: a wine club, back-to-school, three clean controls, and three goal users.
- Tests: `test_budget_users.py`. `test_new_fields_leave_existing_profiles_identical` guards existing users.

## Task 4: scenario files

- 32 new files in `eval/scenarios/`, and category tags on the 8 existing ones.
- Tests: the golden-answer half of `test_eval_agents.py`, and `test_committed_scenarios_cover_the_ticket`.

## Task 5: docs

- `docs/tests/` for every new test, `eval/README.md`, architecture section 10, and the manual verification handoff.

## Task 6: verify

```bash
cd backend && ../venv/bin/python -m pytest
```

```bash
venv/bin/python eval/run_eval.py --runs 1
```

Report pass rates by kind and tag, every failure, and the time taken. File a ticket for each agent problem found, leave the golden answer as it is, and add the scenario to `KNOWN_FAILURES` if code decides the failure.
