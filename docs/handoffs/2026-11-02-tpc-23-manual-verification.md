# TPC-23 goal graph: how to verify it by hand

**What was built:** a goal agent. Code measures what a user has left each month, the local model proposes 1-3 goals within it, and code computes each goal's target and date. Goals are stored as `proposed`, and `GET /goals` reads them.

**The ticket's done-when:** a synthetic user with a 24% APR card gets a debt-payoff goal first (check 1).

There's no page for goals yet (that's TPC-25), so you verify this from the terminal. It takes about 5 minutes.

## Setup

You need branch `TPC-23` checked out and Ollama running with `qwen2.5:14b`.

These checks use a throwaway database, so your `dev.db` isn't touched and your servers or containers can stay up. From `backend/`, in one terminal:

```bash
export DATABASE_URL=sqlite:///./goals-check.db PROMPT_AUDIT_PATH=logs/goals-check-audit.jsonl
g() { ../venv/bin/python -m app.agents.goal_graph --user budget-goals-card-24 --date 2026-11-02 "$@"; }

../venv/bin/alembic upgrade head
../venv/bin/python scripts/seed_budget_user.py --profile goals-card-24 --replace
```

**Expected:** the last migration line says `5b7c2e9d4a10 -> 7c3a1f0e2b64, goals`, and the seed prints `seeded budget-goals-card-24`.

The user has $4,200 of monthly income, a $3,200 card at 24.99% ($95 minimum), an $18,400 student loan at 5.5% ($210 minimum), payments on both, and $1,000 in savings.

## Checks

The dollar amounts below are from real runs. The surplus, the cap and every target and date for a given amount come from code, so those won't change. The model chooses the monthly amounts and writes the sentences, so those can vary. If an amount differs, the date should move with it.

### 1. Debt payoff comes first (the done-when)

```bash
g
```

**Expected:**
```
surplus $971.75 a month after $95.00 of card minimums; goals may use $777.40
1. debt_payoff (avalanche): $583.00/mo, target $21,600.00, 2029-02
   <a sentence or two>
2. emergency_fund: $194.40/mo, target $6,851.78, 2029-11
   <a sentence or two>
```

- `debt_payoff` is number 1. This is the part that must hold.
- $971.75 is what's measured ($1,066.75) less the card's $95 minimum. The loan's $210 is already in spending. Card payments aren't spending, so without this the surplus would overstate what's in hand.
- $777.40 is 80% of the surplus. The monthly amounts stay within it, with no `(over cap)` note. The model sometimes proposes only the debt goal.
- $21,600 is the two debts' balances added together. $583 is an extra on top of their minimums.

### 2. Compare the debt date with the planner

The dashboard's payoff card and chat use the same planner. This runs it directly with the same debts and $583 extra:

```bash
../venv/bin/python -c "
from datetime import date
from app.budget_engine.payoff import Debt, simulate
d = [Debt(1,'Card','credit',24.99,320000,9500), Debt(2,'Loan','student',5.5,1840000,21000)]
print(simulate(d, 58300, 'avalanche', start=date(2026,12,1)).debt_free)"
```

**Expected:** `2029-02-01`, the same month as check 1. If check 1 chose a different amount, use that amount in cents.

### 3. A new run replaces the old proposal

```bash
g > /dev/null
sqlite3 goals-check.db "select status, count(*) from goals group by status;"
```

**Expected:** the newest run's goals are `proposed` and the earlier ones are `superseded`. After checks 1 and 3 that's `proposed|2` and `superseded|2`.

### 4. Preferences change the result

```bash
g --prefs '{"skip_kinds": ["emergency_fund"], "max_share": 0.5}'
```

**Expected:**
```
surplus $971.75 a month after $95.00 of card minimums; goals may use $485.88
1. debt_payoff (avalanche): $485.88/mo, target $21,600.00, 2029-05
```

The cap halves, no emergency fund appears, and the smaller extra pushes debt-free out to May 2029. The model may also add a savings goal, with a target of 12 × its monthly amount, finishing 12 payments from December 2026.

Preferences are stored, so they stay in force for later runs until check 8 clears them.

### 5. Nothing to offer

```bash
g --prefs '{"skip_kinds": ["debt_payoff", "emergency_fund", "savings"]}'
```

**Expected:** the surplus line, `No goals: the preferences skip every kind that's available.`, then `The earlier proposal no longer fits, so it's withdrawn.` It returns right away, because the model isn't called. `GET /goals` would now return nothing, until the next run with goals.

### 6. Bad preferences are refused

```bash
g --prefs '{"max_share": 2}'; echo "exit $?"
```

**Expected:** `bad --prefs: max_share must be above 0 and at most 1.` and `exit 1`, with no run started. Also try a misspelled field like `{"max_shar": 0.5}`, which is refused too rather than ignored.

### 7. A name in the note doesn't reach the model

```bash
g --prefs '{"note": "Jane Doe'"'"'s card first"}' > /dev/null
tail -1 logs/goals-check-audit.jsonl | grep -c "Jane"
tail -1 logs/goals-check-audit.jsonl | grep -o 'person[^,]*card first'
```

**Expected:** `0` for the first, because "Jane" isn't in what was sent. The second shows the note as `person]'s card first`, from `[person]'s card first`. The audit log holds exactly what left for the model, so you can read the whole line.

Then a note with contractions, which must reach the model intact:

```bash
g --prefs '{"note": "it'"'"'s the emergency fund that matters most to me, that'"'"'s all"}'
```

**Expected:** `emergency_fund` is number 1. This one depends on the model following the note, so a rare miss isn't a bug by itself. In one run it gave `1. emergency_fund: $685.18/mo, target $6,851.78, 2027-09`.

### 8. No history yet

```bash
g --prefs '{}' > /dev/null
../venv/bin/python -m app.agents.goal_graph --user budget-goals-card-24 --date 2026-08-20
```

**Expected:** `No goals: no complete month to measure yet.`, then the withdrawn line, because a proposal existed. On Aug 20 no complete month exists, so the model isn't called. `--prefs '{}'` clears the preferences from the earlier checks.

### 9. `GET /goals`

Run a goal run, then start the API on a spare port, acting as this user:

```bash
g > /dev/null
DEV_USER_EMAIL=goals-card-24@eval.local ../venv/bin/uvicorn app.main:app --port 8011
```

In a second terminal:

```bash
curl -s localhost:8011/goals | python3 -m json.tool
```

**Expected:** the goals from the run, in priority order: usually `monthly_cents` of 58300 and 19440, with `target_date` of `2029-02-01` and `2029-11-01`, and `status` of `proposed`. The superseded ones aren't listed. Stop the server with Ctrl-C.

### 10. Reseeding clears goals

```bash
../venv/bin/python scripts/seed_budget_user.py --profile goals-card-24 --replace
sqlite3 goals-check.db "select count(*) from goals;"
```

**Expected:** `0`.

### 11. The automated tests

```bash
../venv/bin/python -m pytest -q -k "not live and not sandbox"
../venv/bin/python -m pytest tests/test_goal_graph.py::test_live_card_debt_goal_first -q -s
```

**Expected:** `773 passed` on the first. The second passes and prints the goals.

The first also covers what's hard to trigger by hand: a run while a budget run holds the user's lock or is `running` in another process, Ctrl-C during a run, a debt plan that never ends, a savings account with no balance, a user with no budget, a simulated user's date, and the model failing or going down.

## Not verified

- **Your own data.** I only ran this on synthetic users. Your Sandbox user may report `No goals`, because Plaid's Sandbox data may not include income in the same way. To try it, run `alembic upgrade head` against `dev.db` first, and find your user's id with `sqlite3 dev.db "select id, email from users;"`. I haven't run this.
- **Inside Docker.** I ran it on the host only. The backend container runs the migration when it starts, so it should work with `docker compose exec backend python -m app.agents.goal_graph ...`.
- **Reliability after the review fixes.** The done-when passed 5 of 5 live runs on the final figures. The 30 of 30 was on the earlier figures, and I didn't repeat all 30.

## Limits that can look like bugs

- **Goals can go over the cap.** Nothing refuses them until TPC-24. The report marks them `(over cap)`, and in 30 live runs this model stayed within the cap.
- **You can't accept or dismiss goals yet.** That's TPC-25, along with goals becoming budget lines and generating them from the app.
- **The model's sentences are its own.** Amounts and dates on the numbered lines are code's. A sentence might say "in 12 months" where the line says 2027-12, which is 13 payments, or call a savings goal an emergency fund. Trust the numbered line. Keeping a sentence's numbers honest is a known follow-up from M3.
- **`protected_categories` has no effect yet.** It's stored and shown to the model, but goals never cut a line, so it only starts to matter with TPC-25.
- **A card paid in full still loses its minimum from the surplus.** The app can't tell from the debt data, and the error is on the safe side. A user who moved a card payment into a budget line has it counted twice for the same reason.
- **The surplus looks back, not forward.** A new bill that began this month doesn't count until a complete month includes it. TPC-31's forecast replaces this figure.

## Failure paths worth trying

- **Model down:** `OLLAMA_BASE_URL=http://127.0.0.1:9 g --prefs '{}'` prints `run failed: ConnectError` and exits 1. The last proposal stays.
- **Unknown user:** `../venv/bin/python -m app.agents.goal_graph --user nobody` prints `No user nobody.` and exits 1.

## Clean up

```bash
rm -f goals-check.db logs/goals-check-audit.jsonl
unset DATABASE_URL PROMPT_AUDIT_PATH
unset -f g
```

To run this against `dev.db` after merging, use `cd backend && ../venv/bin/alembic upgrade head`. The Docker backend does that on its own when it starts.
