# TPC-26: grow the benchmark to 40 golden scenarios

Design for [TPC-26](https://ucf-ctcw-senior-design.atlassian.net/browse/TPC-26). Architecture section 10 lists the metrics this feeds. TPC-27 runs them across models, and TPC-28 charts them.

## Problem

The harness from TPC-16 asks the chat agent a question and scores the answer. The ticket's categories are mostly not chat questions. Whether an overspend is one-time, recurring or seasonal is the budget agent's decision, and so is whether it stays quiet on a user with no overspend. Goal affordability is the goal agent's. Chat scenarios can't test those, and nothing scored false alarms or time-to-flag.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Size and mix | 40: 8 existing + 14 chat + 12 budget + 6 goal | Inside 30-50, with room to add a few |
| How a budget scenario runs | A short window of real daily runs around the planted event | Measures time-to-flag and lets stream alerts fire, with few model calls. A flagged category costs 2 calls a day, so a whole month would take hours |
| Pass rule | Flags, cause, action and alert kind by rule, plus required text in the message. Similarity and the judge stay chat only | A useless message shouldn't pass, and the code-written fallback messages would score oddly with the judge |
| Known false alarms | Measure them and tune later | Architecture 6.4 already expects lumpy spending to be flagged. A benchmark shaped to pass tells us nothing |
| "Unused subscriptions" | The user says they don't use it | The app has no usage data, and adding one is product work |
| Extra metrics | pass^3 and 4 abstain scenarios. `tools_none` is left to TPC-27 | pass^3 needs no extra runs. Abstention guards against invented figures |
| Model failure | Each check is scored apart | The user was still warned, so the flag counts, but cause and message fail and the failure count is reported |
| Action check | An accepted set per scenario, plus banned actions | The prompt allows more than one sensible action. Cause stays one exact answer |
| Goal order | `first_kind` only where a rule decides | The prompt's only rule is that a debt at 8% APR or more comes first. Elsewhere it would encode an order the agent was never given |
| Agent bugs found | The scenario keeps failing, and a ticket holds the evidence | Keeps this first baseline honest and agent code out of this change |

## Scenario format

A top-level `kind` defaults to `chat`, so existing files load unchanged. `tags` group results for reporting. `eval/README.md` has the field-by-field rules.

- **`budget`** names a `profile` from `eval/budget_users.py` and a `start` and `end`. The user is seeded through `start` with no run, then gets one real daily run per day through `end`.
- **`goal`** names a `profile`, a `today` and optional `prefs`.
- **Chat `expect.abstain`** replaces facts: the answer must say the data isn't available and state no dollar amount.

In `expect`, an empty `flags` list makes a control. Leaving `flags` out means they aren't checked, which suits a scenario about a signal. The two signal users have lumpy spending that gets flagged, which isn't what those scenarios test.

Users are Python profiles, not inline JSON. That reuses the seeded generators and `advance_days`, and planted facts can't be mis-summed by hand.

## Running and scoring

`eval/agent_runner.py` has `run_budget_once` and `run_goal_once`. Like `runner.run_once` they never raise, because a failed run is a result. They go through the real `advance_days` and `run_goals`.

`advance_days` takes no audit path, and each daily run builds its own gateway. So the budget runner points `config.PROMPT_AUDIT_PATH` at the results folder for the run and restores it afterwards. Eval prompts must never reach the app's real audit log.

`eval/metrics.py` adds `score_budget` and `score_goal`, which return named checks and `passed`, so one failure doesn't hide another. Only checks a scenario sets are scored.

**Budget checks:**

| Check | Passes when |
|---|---|
| `flags` | The flagged categories over the whole window are exactly the expected ones |
| `cause` | The first overspend alert for the category has the expected cause |
| `action` | That alert's action is accepted, and no alert for the category has a banned action |
| `flag_by` | The category is first flagged by this date |
| `alert_kinds` | Every listed signal kind appears |
| `message` | The required facts appear in the alert messages |
| `lead_time` | The earliest low-balance warning is at least this many days ahead |

**Goal checks:**

| Check | Passes when |
|---|---|
| `status` | The run ended as expected |
| `required_kinds`, `banned_kinds`, `first_kind` | The plan has these kinds, not those, and this one at priority 1 |
| `under_cap` | On `ok`: a plan exists and its total is within the cap |
| `model_not_called` | Otherwise: zero attempts |

Retries are reported and never fail a run on their own.

The summary keeps `overall` chat-only, so it stays comparable with the October results, and adds `budget` and `goal` sections. The budget section includes the false-alarm rate, which is the share of control runs with any flag or overspend alert. A low-balance warning doesn't count, because it isn't an overspend alarm.

`run_eval.py` gains `--kind`. What it needs comes from the scenarios that will run, so a budget-only run doesn't require the judge, embeddings or knowledge base.

## Windows and controls

Probing each window with a fake model changed the design in three ways:

- **Windows start the day before the planted event, where pace would otherwise flag early.** `overspend-one-time` is flagged on Oct 10, two days before its $600 purchase, because one weekly $35 shop is already over pace on day 10. That's a real false alarm, but it would make the first alert's cause about the wrong thing. Starting on Oct 11 scores the planted decision, and `control-steady-spender` measures the early flag on its own.
- **Controls must really be on budget.** A first draft used a weekly shop and a $60 one-off "under its limit". Both were flagged, and the second really did end the month over. The three clean controls now spend the same amount every day, so pace is fair and any flag is a real false alarm. `control-steady-spender` keeps the lumpy spending and is expected to fail today.
- **Planted users have a static $100 balance**, so they also raise low-balance alerts. These don't count as false alarms, but they use model calls. New users set `opening_cents` so their balance moves and the forecast stays quiet. Existing users are unchanged.

## Checking the golden answers

Offline tests catch a wrong golden answer before it can look like an agent failure:

- Every chat number fact must appear in the real output of the tools its scenario expects.
- Every golden answer must pass its own facts.
- Every budget scenario runs with a fake model, and what code decides (flags, deadlines, signals, lead time) must match.
- Every goal scenario must pass given a sensible reply, and an unaffordable one must never call the model.

`KNOWN_FAILURES` in `test_eval_agents.py` names scenarios that fail because of a known agent problem, with their tickets. A new mismatch fails the test, and so does a known failure that starts passing.

## Out of scope

- Threshold tuning and agent fixes.
- `tools_none` and allowed-tool scoring (TPC-27).
- `temporal` scenarios, which need the graph tools from TPC-39.
- The full model comparison (TPC-27).
