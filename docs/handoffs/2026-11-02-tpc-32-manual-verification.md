# TPC-32 debt-payoff planner and Docker setup: how to verify it by hand

**What was built:**
- A debt-payoff planner: avalanche vs snowball, month by month, with an optional extra payment. It's available through `POST /debt-plan`, a "Paying off your debts" card on the dashboard, and a `plan_debt_payoff` chat tool.
- A Docker dev setup, on the same branch: `docker compose up` runs the backend and frontend as two containers.

**The ticket's done-when:** asked "How fast am I debt-free if I add $200 a month?", chat gives the same months and interest (within $1) as the planner (check 4).

It takes about 10 minutes, plus image builds for check 7 if they aren't cached.

## Setup

You need branch `TPC-32` checked out and Ollama running with `qwen2.5:14b`. Stop any servers on the ports below.

These checks use a throwaway database, so your `dev.db` isn't touched. From `backend/`, in one terminal:

```bash
export DATABASE_URL=sqlite:///./card-check.db PROMPT_AUDIT_PATH=logs/card-check-audit.jsonl DEV_USER_EMAIL=card@check.local
../venv/bin/alembic upgrade head
PYTHONPATH=.:.. ../venv/bin/python - <<'PY'
from datetime import date
from app.db.session import SessionLocal
from tests.factories import add_account, add_txn, add_user
with SessionLocal() as s:
    item = add_user(s, "card-check", "card@check.local")
    a = add_account(s, item, "Checking", balance=215000)
    add_account(s, item, "Platinum Card", type_="credit", subtype="credit card", balance=320000,
                liability={"kind": "credit", "apr": 24.99, "min_payment_cents": 9500})
    add_account(s, item, "Student Loan", type_="loan", subtype="student", balance=1840000,
                liability={"kind": "student", "apr": 5.5, "min_payment_cents": 21000})
    add_account(s, item, "Store Card", type_="credit", subtype="credit card", balance=50000,
                liability={"kind": "credit", "apr": None, "min_payment_cents": 2500})
    add_account(s, item, "Home", type_="loan", subtype="mortgage", balance=25000000,
                liability={"kind": "mortgage", "apr": 4.0, "min_payment_cents": 150000})
    add_txn(s, a, -420000, "INCOME", day=date(2026, 10, 15))
    s.commit()
PY
../venv/bin/uvicorn app.main:app --port 8021
```

In a second terminal, from `frontend/`:

```bash
rm -rf .next
BACKEND_URL=http://127.0.0.1:8021 npm run dev -- -p 3021
```

Clear `.next` first: `next dev` reusing the folder from a production build left the dashboard stuck on "Loading…" when I tried it.

The months and dollars below were seen on 2026-11-02. They don't depend on the date, but the months named do, because a plan starts on the 1st of next month.

## Checks

### 1. The dashboard card

Open http://localhost:3021/dashboard. Under "Debts by APR" there's a "Paying off your debts" card.

**Expected at $0 extra:** both columns show 95 months, debt-free Oct 2034, and $7,109.82 of total interest. Platinum Card is paid off in Oct 2031 and Student Loan in Oct 2034. Below them: "Store Card: no APR on file" and "Home: mortgage (left out)".

`Couldn't start Link: Request failed (500)` at the top is expected if your `.env` has no Plaid keys. It has nothing to do with this card.

### 2. What an extra payment changes

Type `200` in "Extra per month".

**Expected, about 300 ms after you stop typing:** both columns show 49 months, debt-free Dec 2030, and $2,941.08 of total interest, plus `+$200/mo: 46 months sooner, $4,168.74 less interest`.

Avalanche and snowball match here because the card is both the highest APR and the smallest balance. So there's no "saves the most interest" tag, which only shows when one costs less.

### 3. The API

```bash
curl -s localhost:8021/debt-plan -X POST -H 'content-type: application/json' -d '{"extra_monthly_cents":20000}' | python3 -m json.tool | head -20
curl -s -o /dev/null -w "%{http_code}\n" localhost:8021/debt-plan -X POST -H 'content-type: application/json' -d '{"extra_monthly_cents":-1}'
curl -s -o /dev/null -w "%{http_code}\n" localhost:8021/debt-plan -X POST -H 'content-type: application/json' -d '{"extra_monthly_cents":100000001}'
```

**Expected:** `months` of 49 and `total_interest_cents` of 294108, then `422` twice.

With `"include_mortgage":true` and the same extra, it's 182 months and only Store Card is left out.

### 4. Chat gives the planner's numbers (the done-when)

Open http://localhost:3021/chat and ask: `How fast am I debt-free if I add $200 a month?`

**Expected:** a `plan_debt_payoff` tool call, then an answer with 49 months and $2,941.08 of total interest. In my run it also said December 2030, 95 months and $7,109.82 without the extra, and 46 months and $4,168.74 saved. The wording can vary, and the numbers shouldn't.

### 5. Names don't reach the model

```bash
tail -1 logs/card-check-audit.jsonl
```

**Expected:** a `debt_plan` entry. To see scrubbing, rename an account in the seed to `Jane Doe's Visa` and reseed: the name goes out as `[person]'s Visa`. `test_debt_plan_matches_compare_and_scrubs` also covers this, so you can skip the reseed.

### 6. The automated tests

```bash
../venv/bin/python -m pytest -q -k "not live and not sandbox"
../venv/bin/python -m pytest tests/test_tools.py::test_live_debt_free_with_extra -q
```

From `frontend/`: `npm test && npm run lint && npm run build`.

**Expected:** `675 passed`, the live test passes, and `73 passed` with a clean lint and build. Run `rm -rf .next` afterwards if you go back to `npm run dev`.

These also cover what's hard to trigger by hand: a debt never paid off, a 0% APR, an overpaid card, an extra that clears everything in month 1, a user whose debts are all excluded, and an older response arriving after a newer one.

### 7. Docker

Stop the servers above, and unset the variables from setup first (see "Clean up"), or they'd override `.env` in the containers. Start Docker Desktop and Ollama. From the repo root:

```bash
docker compose up --build -d
curl -s localhost:8000/health
curl -s localhost:3000/api/health
docker compose exec backend date
docker compose exec backend python -m pytest -q -k "not live and not sandbox"
```

**Expected:** `{"status":"ok"}` twice, the proxy one proving the frontend reaches the backend over the Compose network. The date is your local time, not UTC, and 675 tests pass. Open http://localhost:3000.

This uses your real `dev.db` and runs its migrations. Then:

```bash
docker compose down
```

Starting a host server on port 8000 while the containers run should be refused with "address already in use".

## Not verified

- **Docker today.** Check 7's results are from 2026-11-01. I didn't re-run it on 2026-11-02, because it would migrate your `dev.db`.
- **Fast typing in a real browser.** The stale-response handling is covered by a test only.
- **Your own data.** The card was only checked on the user seeded above. Plaid's Sandbox user should work too, with its 12.5% card, student loan and mortgage.

## Limits that can look like bugs

- **Interest is simplified.** It's charged monthly at APR/12, and minimums stay at today's amounts. Real card minimums shrink as the balance falls.
- **The message for a debt that never ends** says it "isn't paid off within 50 years at this pace". The stop is 600 months, so a long loan can get this even when its minimum covers its interest.
- **Snowball with an extra can rarely cost more interest**, about 1 in 3,000 random cases. The card then reads like "-$23.36 less interest".
- **All debts at $0:** the card shows only the input and the footnote.
- **The extra input:** there's no maximum, so over $1,000,000 shows an error above the old plan. "2.5" plans $3 and "-200" plans $0, while the box still shows what you typed.
- **An APR of about 110% or more** on a debt that's never paid off overflows the arithmetic: a 500 from the API, or a generic error in chat.
- **A small model sending `extra_monthly` as blank or null** gets a generic validation error instead of being treated as $0.

## Clean up

In `backend/`, after stopping the servers:

```bash
rm -f card-check.db logs/card-check-audit.jsonl
unset DATABASE_URL PROMPT_AUDIT_PATH DEV_USER_EMAIL
```
