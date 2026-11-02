# TPC-24: manual verification, step by step

Use one terminal and run the steps **in order**. This takes about 5–10 minutes. There is **no Goals page/button yet**; that is TPC-25. TPC-24 is verified through the terminal, stored rows, and optionally the browser's API documentation.

These instructions use the host venv and a disposable synthetic database. Your linked data and `dev.db` are unaffected. Docker equivalents and linked Sandbox results are in the [Docker verification guide](2026-11-02-tpc-24-docker-linked-verification.md).

## 1. Open a terminal and prepare the test user

On this machine, paste:

```bash
cd /Users/armindelmo/Documents/College/Fall26/COP6938/Plaid-Agent-CAP6938/backend
export TPC24_CHECK_DIR="$(mktemp -d /tmp/tpc24-manual.XXXXXX)"
export DATABASE_URL="sqlite:///${TPC24_CHECK_DIR}/goals.db"
export PROMPT_AUDIT_PATH="${TPC24_CHECK_DIR}/audit.jsonl"

../venv/bin/alembic upgrade head
../venv/bin/python scripts/seed_budget_user.py --profile goals-card-24 --replace
g() { ../venv/bin/python -m app.agents.goal_graph --user budget-goals-card-24 --date 2026-11-02 "$@"; }
```

**Check:** the last migration is `7c3a1f0e2b64`, and seeding prints `seeded budget-goals-card-24`. Stop here if either command fails. If `../venv/bin/python` is missing, first follow the README's local setup.

For the next step, Ollama must be running with `qwen2.5:14b`. Check `ollama list`; if necessary, start the Ollama app and run `ollama pull qwen2.5:14b`. Steps 3–5 also work without Ollama.

## 2. Ask the real model for goals with only $48.59 available

```bash
g --prefs '{"max_share":0.05}'
```

**Look for:**

```text
surplus $971.75 a month after $95.00 of card minimums; goals may use $48.59
Validated in 1 model calls.
1. debt_payoff (avalanche): $48.59/mo, target $21,600.00, 2033-03
```

The allocation/date/sentences may vary, and two calls are allowed. **Pass:** numbered goals have dates, their monthly amounts together are at most $48.59, and validation takes at most two calls. An error means this live attempt did not produce a usable proposal; read the printed reasons. An unavailable model is a setup problem, not a successful manual check.

Inspect what was actually saved:

```bash
sqlite3 -header -column "$TPC24_CHECK_DIR/goals.db" "select id,kind,monthly_cents,target_date,status from goals where status='proposed' order by priority;"
sqlite3 -header -column "$TPC24_CHECK_DIR/goals.db" "select count(*) as goals,sum(monthly_cents) as total_cents,count(target_date) as dated_goals from goals where status='proposed';"
```

**Pass:** at least one row; `total_cents` ≤ **4859**; `dated_goals` equals `goals`. This checks the stored fields, not the model's rationale text.

## 3. Force an unaffordable reply, then watch the correction

A live model may fit the cap on its first try. To see the rejection path every time, paste this temporary terminal function. It supplies controlled replies to the **real goal graph**, writing to the same disposable database; it does not run pytest or change production code.

```bash
demo() {
  ../venv/bin/python - "$1" <<'PY'
import json, sys
from datetime import date
from langchain_core.messages import AIMessage
from app.agents.goal_graph import run_goals, _print_report
from app.budget_service.goal_prefs import set_goal_prefs
from app.db.session import SessionLocal
from tests.fakes import ScriptedChatModel

mode = sys.argv[1]
if mode not in ('retry', 'reject'):
    raise SystemExit('use: demo retry OR demo reject')
with SessionLocal() as s:
    set_goal_prefs(s, 'budget-goals-card-24', {'max_share': 0.05})
    s.commit()
amounts = [50, 48.59] if mode == 'retry' else [50, 60]
replies = [AIMessage(content=json.dumps({'goals': [{
    'kind': 'savings', 'monthly_dollars': amount,
    'strategy': None, 'rationale': 'Save for later.'
}]})) for amount in amounts]
fake = ScriptedChatModel(replies=replies)
print(f'Controlled replies: ${amounts[0]:.2f}, then ${amounts[1]:.2f}')
result = run_goals(SessionLocal, 'budget-goals-card-24', date(2026, 11, 2), model=fake)
for attempt in result['validation_history']:
    for issue in attempt['issues']:
        print(f"Attempt {attempt['attempt']} rejected: {issue['message']}")
_print_report(result)
print(f"Actual calls: {len(fake.seen)}; new goal IDs: {result['goal_ids']}")
PY
}
demo retry
```

**Look for these exact facts:**

- Attempt 1 is rejected: **$50.00** exceeds **$48.59** by **$1.41**.
- The corrected result is `savings: $48.59/mo, target $583.08, 2027-11`.
- `Actual calls: 2` and a nonempty list of new goal IDs.

Then inspect the rows again:

```bash
sqlite3 -header -column "$TPC24_CHECK_DIR/goals.db" "select id,kind,monthly_cents,target_date from goals where status='proposed';"
```

**Pass:** exactly one proposed savings row with **4859 cents** and date **2027-11-01**. Remember its `id` for the next step. The rejected $50 allocation must not be stored as a goal.

To see the rejection reason actually sent on the correction call:

```bash
../venv/bin/python - <<'PY'
import json, os
from pathlib import Path
for line in reversed(Path(os.environ['PROMPT_AUDIT_PATH']).read_text().splitlines()):
    entry = json.loads(line)
    if entry['params'].get('attempt') == 2:
        print(json.dumps(json.loads(entry['prompt'])['correction'], indent=2))
        break
PY
```

**Pass:** `attempt` is 2, `rejected_goals` contains savings at **50.0 dollars**, and `failures` contains `over_cap` with the **$1.41** excess. The rejected rationale is not in this payload.

## 4. Force both replies to fail and confirm the older proposal stays

```bash
demo reject
sqlite3 -header -column "$TPC24_CHECK_DIR/goals.db" "select id,kind,monthly_cents,target_date from goals where status='proposed';"
```

**Pass:** both $50 and $60 replies are rejected, the report says `run failed: validation_failed` and `No replacement`, `Actual calls: 2`, and `new goal IDs: []`. The stored savings row has the **same id, 4859 cents, and 2027-11-01 date** as step 3. No third call occurs and neither rejected amount becomes a goal.

`demo` intentionally prints the failed run for inspection without terminating your terminal. The production `g` command exits 1 for a failed run.

## 5. Make the cap too small for any goal

```bash
g --prefs '{"max_share":0.00001}'
sqlite3 "$TPC24_CHECK_DIR/goals.db" "select count(*) from goals where status='proposed';"
sqlite3 "$TPC24_CHECK_DIR/goals.db" "select summary_json from agent_runs where graph='goals' order by id desc limit 1;"
```

**Pass:** the cap is **$0.01**; the report says `below the $1.00 minimum`, the model was not called, and the earlier proposal is withdrawn. The count is **0**. The JSON has `outcome: no_affordable_goals` and `attempts: 0`.

This deliberately differs from step 4: a failed model reply preserves the old proposal, while a proven impossible cap withdraws it.

## 6. Optional: inspect the goals in your browser

While the terminal functions/environment are still set, recreate step 3's known valid goal and start an API on a spare port:

```bash
demo retry
DEV_USER_EMAIL=goals-card-24@eval.local ../venv/bin/uvicorn app.main:app --port 8011
```

Open [http://localhost:8011/docs](http://localhost:8011/docs). Expand **GET /goals**, click **Try it out**, then **Execute**.

**Pass:** HTTP **200**, one savings goal, `monthly_cents: 4859`, `target_date: "2027-11-01"`, and `status: "proposed"`. You will not see a Goals page on the frontend yet.

Press **Ctrl+C** in the terminal to stop this temporary API before cleanup. If port 8011 is occupied, choose another spare port and use that port in the browser URL.

## 7. Cleanup

In the same terminal, after stopping the optional API:

```bash
rm -f "$TPC24_CHECK_DIR/goals.db" "$TPC24_CHECK_DIR/audit.jsonl"
rmdir "$TPC24_CHECK_DIR"
unset DATABASE_URL PROMPT_AUDIT_PATH TPC24_CHECK_DIR
unset -f g demo
```

## Recorded verification and remaining limits

Steps 1–5 and cleanup above were executed exactly as written. The live run saved a 4859-cent debt goal; `demo retry` rejected $50 and saved a 4859-cent savings goal; `demo reject` made exactly two calls and preserved that row's id; the $0.01 cap then withdrew it with zero calls. The correction audit payload was also inspected. The optional browser step is an additional way to inspect the stored row.

On 2026-11-02, the offline suite passed **807 tests**, the five host and five Docker tight-surplus trials passed, and the existing debt-priority live regression passed. Four existing Plaid Sandbox links refreshed successfully in a Docker database copy; its negative surplus correctly produced no goals and zero calls. The original linked database was unchanged. See the [Docker report](2026-11-02-tpc-24-docker-linked-verification.md) and [TDD evidence](../superpowers/plans/2026-11-02-tpc-24-goal-validation.md#tdd-and-verification-evidence).

Production bank data and the frontend were not exercised. Free-text preferences and numerical claims in rationale are model guidance/text; this ticket validates computed amounts, kinds, and dates.
