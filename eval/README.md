# Eval harness

Scores the agents on golden scenarios. Each scenario is a synthetic user with known numbers and the answer it should get. Budget and goal scenarios are covered [below](#budget-and-goal-scenarios). For chat, the harness runs the same graph and tools as `POST /chat`, against a throwaway in-memory database, and scores every answer three ways:

| Metric | How | Target |
|---|---|---|
| Facts | Every listed fact must appear in the answer (numbers within a tolerance), and no forbidden string may | report pass % |
| Similarity | Cosine similarity of `nomic-embed-text` embeddings of the answer and the golden answer | ≥ 0.85 |
| Judge | `gemma3:12b` scores coherent / consistent with the user's data / safe from 1 to 5; all three must be ≥ 4 | 85% pass |

It also reports whether the expected tools were called, whether the expected sources were retrieved, latency, and errors. Design: `docs/superpowers/specs/2026-10-27-tpc-16-eval-harness-design.md`; architecture section 10.

## Before you run it

- Ollama is running, with three models pulled: the agent model, `nomic-embed-text`, and the judge.

  ```bash
  ollama pull qwen2.5:14b && ollama pull nomic-embed-text && ollama pull gemma3:12b
  ```

- The knowledge base is ingested: `venv/bin/python backend/scripts/ingest_kb.py`.

The harness checks all of this first. If anything is missing, it prints the fix and exits with code 2.

## Running it

From the repo root:

```bash
venv/bin/python eval/run_eval.py                          # every scenario, 3 runs each, on OLLAMA_MODEL
venv/bin/python eval/run_eval.py --model llama3.1:8b      # another agent model
venv/bin/python eval/run_eval.py --only debt-highest-apr --runs 1
```

| Flag | Default | |
|---|---|---|
| `--model` | `OLLAMA_MODEL` (`qwen2.5:14b`) | agent model |
| `--judge` | `gemma3:12b` | judge model; keep it outside the agent models being compared |
| `--runs` | 3 | runs per scenario, all at temperature 0 |
| `--only` | | one scenario id |
| `--kind` | all | `chat`, `budget` or `goal`. Checks only what the scenarios that will run need, so budget and goal don't need the judge, embeddings or knowledge base |

The exit code is 0 whenever the eval completes, even with low scores. Low scores are a result, not an error.

## Results

`eval/results/<YYYY-MM-DD>/<model>/` (`:` and `/` become `_`). A second run on the same day goes to `<model>-2`, then `-3`, and so on. Results are committed so progress can be charted.

| File | Contents |
|---|---|
| `<id>.run<N>.json` | `record`: answer, tool calls, tool results, sources, latency, error. `scores`: facts (found, missing, forbidden hits), tools, sources, similarity, judge. Written as soon as the run finishes |
| `summary.json` | For each scenario and overall: pass rates, mean similarity and the share of runs at target, judge means, tool and source accuracy, mean and p95 latency, errors, and the min/max across runs |
| `config.json` | Models and their Ollama digests, the Ollama version, run count, git commit, scenario ids, whole-run and per-kind scenario hashes, start and finish times |
| `audit.jsonl` | Every prompt the privacy gateway built during the eval |

### Comparing conditions

```bash
venv/bin/python eval/compare.py <with-technology folder> <without-technology folder> --metric chat --out <output-prefix>
```

Use `chat` for chat fact pass, `budget` for budget cause and action pass, or `retries` for budget/goal pass and retry fire rates.
The folders must record the same commit. New runs record `scenario_hashes_by_kind`, so an all-kinds baseline can be compared
with a `--kind chat`, `--kind budget` or `--kind goal` condition. Each compared kind must have identical scenario hashes and
the same scenario IDs in its run files; unrelated kinds are excluded. Retry fire rates use only the compared scenarios.

Older folders without per-kind hashes still require identical whole-run `scenario_hash` values. They cannot support a
baseline-versus-subset comparison when those hashes differ; rerun with the updated harness rather than editing saved metadata.

## Adding a scenario

Create `eval/scenarios/<id>.json`. The `id` must equal the file name.

```json
{
  "id": "debt-highest-apr",
  "question": "Which of my debts should I pay off first?",
  "user": {
    "accounts": [
      {"name": "Everyday Checking", "type": "depository", "subtype": "checking", "balance": 2150.00},
      {"name": "Platinum Card", "type": "credit", "balance": 3200.00,
       "liability": {"kind": "credit", "apr": 24.99, "min_payment": 95.00, "due_in_days": 12}}
    ],
    "transactions": [
      {"account": "Everyday Checking", "days_ago": 4, "amount": 42.10,
       "category": "FOOD_AND_DRINK", "merchant": "Chipotle"}
    ]
  },
  "golden_answer": "Pay the Platinum Card first: at 24.99% APR it costs the most in interest. ...",
  "expect": {
    "facts":   [{"text": "Platinum Card"}, {"number": 24.99, "tol": 0.01}],
    "forbid":  ["http"],
    "tools":   ["get_debts"],
    "sources": []
  }
}
```

| Field | Rules |
|---|---|
| `accounts[]` | `name`, `type` (a Plaid account type) and `balance` in dollars are required. `subtype` and `liability` are optional |
| `liability` | `kind` (`credit`, `student`, `mortgage`) and `apr`; optional `min_payment` (dollars) and `due_in_days` |
| `transactions[]` | `account` must name an account above. `amount` is in dollars with Plaid's sign: **positive = money out**, negative = money in or a refund. `category` must be a Plaid primary category (`PFC_CATEGORIES` in `backend/app/agents/tools.py`). `days_ago` ≥ 0; `merchant` is optional |
| `expect.facts` | At least one fact. Each is `{"text": ...}` (case-insensitive substring) or `{"number": ..., "tol": ...}` |
| `expect.forbid` | Strings that must not appear, case-insensitive |
| `expect.tools` | Tools that must have been called. Reported, not part of facts pass/fail |
| `expect.sources` | `source_url`s that must be among the retrieved guidance |

| `expect.abstain` | `true` replaces `facts`: the answer must say the data isn't available and state no dollar amount. A bare year is fine |
| `expect.no_numbers` | With `abstain`: the answer can't state any number. For a missing fact that's a bare number, like a credit score |
| `tags` | Optional, on every kind. Results are also reported per tag |

Tips:
- Keep a number fact to a figure the tools return directly. The design keeps arithmetic out of the model, so a fact the model would have to compute tests something else.
- Remember the gateway's rules when choosing expected numbers. Spending totals count money out only, so refunds are excluded, and the default window is the last 30 days.
- A text fact can't be something the question already says, or an answer could pass by repeating the question.
- Run `cd backend && ../venv/bin/python -m pytest tests/test_eval_fixtures.py tests/test_eval_agents.py` after adding a file. They check that every scenario parses (update the counts), that each number fact comes from the tools it expects, and that each golden answer passes its own facts.

## Budget and goal scenarios

The same command runs them, and they use the agent model only. They're scored by rule on what the real graphs decided, so there's no similarity or judge score. Each check is reported on its own, and the run passes if all of them do.

```bash
venv/bin/python eval/run_eval.py --kind budget --runs 1
```

### Budget

Add `"kind": "budget"`, a `profile` from `eval/budget_users.py`, and a `start` and `end`. The user is seeded through `start` with no run, then gets one real daily run per day through `end`.

```json
{
  "id": "budget-one-time-new-shop",
  "kind": "budget",
  "tags": ["one_time"],
  "profile": "overspend-one-time",
  "start": "2026-10-11",
  "end": "2026-10-13",
  "golden_answer": "General merchandise goes over its limit on Oct 12 because of one $600 purchase at ElectroMart...",
  "expect": {
    "flags": ["GENERAL_MERCHANDISE"],
    "causes": {"GENERAL_MERCHANDISE": "one_time"},
    "actions": {"GENERAL_MERCHANDISE": {"accept": ["alert", "suggest_cuts"], "ban": ["adjust_next_budget"]}},
    "flag_by": {"GENERAL_MERCHANDISE": "2026-10-12"},
    "message_facts": [{"text": "ElectroMart"}]
  }
}
```

Only the checks a scenario sets are scored:

| Field | Passes when |
|---|---|
| `expect.flags` | The categories flagged over the whole window are exactly these. `[]` makes the scenario a control, and leaving it out means flags aren't checked |
| `expect.causes` | The category's first overspend alert has this cause |
| `expect.actions` | That alert's action is in `accept`, and no alert for the category has an action in `ban` |
| `expect.flag_by` | The category is first flagged on or before this date |
| `expect.crossed_on` | Not a pass or fail check. The day the planted spending crosses the threshold, which time-to-flag counts from |
| `expect.alert_kinds` | Every signal kind listed appears (`new_subscription`, `price_increase`, `income_change`, `low_balance`) |
| `expect.message_facts` | These facts appear in the alert messages. They use the same form as `facts` |
| `expect.min_days_ahead` | The earliest low-balance warning is at least this many days ahead |

Tips:
- Keep windows short. Each flagged category costs two model calls a day, and a planted user's static $100 balance adds low-balance alerts too. Give a new user `opening_cents` so its balance moves.
- Start the day before the planted event when pace would otherwise flag early, or the first alert's cause will be about something else.
- A control must really be on budget, or its flag isn't a false alarm. Pace is only fair to spending that's even through the month.
- If the model can't answer, code still writes a plain alert. The flag passes, cause and message fail, and `llm_failures` counts it.

### Goal

Add `"kind": "goal"`, a `profile`, a `today`, and optionally `prefs` (the same as `--prefs` on the goal graph).

```json
{
  "id": "goal-respects-skipped-kind",
  "kind": "goal",
  "tags": ["goal_affordability"],
  "profile": "goals-card-24",
  "today": "2026-11-02",
  "prefs": {"skip_kinds": ["debt_payoff"]},
  "golden_answer": "The user has a 24.99% card but has opted out of debt goals...",
  "expect": {"status": "ok", "banned_kinds": ["debt_payoff"]}
}
```

| Field | Passes when |
|---|---|
| `expect.status` | The run ended with this: `ok`, `no_surplus`, `nothing_offered` or `no_affordable_goals` |
| `expect.required_kinds` | These kinds are in the plan |
| `expect.banned_kinds` | These kinds aren't |
| `expect.first_kind` | This kind has priority 1. Use it only where a rule decides the order: a debt at 8% APR or more goes first |
| (always, on `ok`) | A plan exists, and its total is within the cap |
| (always, otherwise) | The model was never called |

Retries are reported in `attempts` and never fail a run on their own.

### Adding one

Add its user to `SCENARIO_USERS` in `eval/budget_users.py` if no existing user fits, with a test in `test_budget_users.py` that its planted fact is there. `test_eval_agents.py` then runs the scenario with a fake model and checks what code decides: flags, deadlines, signals and lead time. If that fails because of a known agent problem, keep the right golden answer, file a ticket, and add the scenario to `KNOWN_FAILURES`.

### What the summary adds

`summary.json` keeps `overall` for chat, and adds `by_tag`, `fact_pass_all_runs_rate` and these sections:

| Section | Holds |
|---|---|
| `budget` | `pass_rate`, `pass_all_runs_rate` (pass^3), each check's rate, `false_alarm_rate` (control runs with any flag or overspend alert), `time_to_flag_days_mean` (planted crossing to first alert), `missed_alerts` (crossings with no alert), `low_balance_days_ahead_mean`, `llm_failures`, `by_tag`, `scenarios` |
| `goal` | `pass_rate`, `pass_all_runs_rate`, each check's rate, `attempts_mean`, `by_tag`, `scenarios` |

A check no scenario sets has a rate of `null`.

## Budget accuracy

A second, separate eval: how close is a user's **first budget** to what they actually spend the next month (architecture 6.3 and 10.2)? It makes no LLM calls and needs no Ollama, so it runs in seconds. From the repo root:

```bash
venv/bin/python eval/budget_accuracy.py
```

For each synthetic user in `eval/budget_users.py`, it:
- seeds a throwaway database;
- runs the real `initialize_budget` and prints the stored budget (limits, outlier months, skipped categories), so you can check it looks sensible;
- fits May-July with the **median** and with the **mean** and scores each against August.

The scores per user and pooled are:
- **MAPE** over lines whose category had August spending;
- **zero-actual lines**, which got a limit but had no spending, so a percentage can't score them;
- **unbudgeted** categories, which had spending but no line.

Results go to `eval/results/<YYYY-MM-DD>/budget_accuracy/results.json`, with `-2` for a second run the same day.

### Adding a budget user

Add a `Profile` to `PROFILES` in `eval/budget_users.py`. A profile is a few patterns:
- `Weekly(category, merchant, cents, jitter, weekday, growth)`;
- `Monthly(category, merchant, cents, day, moved)`, where `moved` pays one month on a different date;
- `Once(category, merchant, cents, day)`.

Each profile has its own seed, so its transactions never change between runs. Use Plaid primary categories. Include `BASE` so the user has income and a transfer to filter out. Then add a test to `backend/tests/test_budget_users.py` checking that the planted fact really is in the generated transactions, and document it in `docs/tests/test_budget_users.md`.
