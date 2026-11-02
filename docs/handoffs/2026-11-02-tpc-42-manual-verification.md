# TPC-42 manual verification: one-command Docker setup

How to check by hand that the project runs from a fresh copy with one command. Expected results are ones seen on a Mac (Apple Silicon, 24 GB, Docker Compose 5.1, Ollama 0.33.2) on November 2, 2026, except where marked not run.

## Setup

Test a separate clean clone, so your own `.env` and `dev.db` stay out of it:

```bash
git clone --branch TPC-42 https://github.com/Del0m/Plaid-Agent-CAP6938.git /tmp/tpc-42-check
cd /tmp/tpc-42-check
```

This needs TPC-42 pushed first. Until then, clone your local checkout's path instead of the GitHub URL.

- Start Docker Desktop and Ollama (`ollama serve`). No `.env` is needed; that's part of the test.
- Stop anything on ports 3000 and 8000, including your normal Docker stack (`docker compose down` in that folder).

## Checks

### 1. One command, no `.env`

```bash
docker compose up --build
```

Expected in the logs:

- `init` prints `created .env from .env.example`, `generated a FERNET_KEY in .env`, then `simulated 1 day(s): 2026-09-30 → 2026-10-01, 1 runs, 0 alerts`, and exits with code 0.
- `models` ends with `all set: chat and the budget agent are ready`, after `knowledge base ready: 96 chunks from 16 sources`. Your models are already pulled, so it won't download them.

The key isn't printed. `grep -c '^FERNET_KEY=..' .env` prints `1`.

### 2. The app opens with data

Open http://localhost:3000. It redirects to the dashboard.

- Net worth is $100, with one Checking account.
- The Budget card shows October with personal care at $0 of $42, and rent at $1,500 of $1,500.

**The browser steps in checks 2, 3, 4 and 6 weren't run.** I ran them through the same `/api` proxy the browser uses, so the data is confirmed, but how the page looks and behaves isn't. Look hardest here.

### 3. The budget agent

On **Alerts**, click **Advance 7 days** (about 30 seconds).

- The date becomes October 8, with one alert. Its cause is recurring, it proposes $135, and it mentions FitClub Gym. The model writes the wording, so it can vary.
- The Budget card shows personal care flagged, at $90 of $42.

Click **Accept**. The alert shows as accepted.

Click **To month end** (about 2 minutes), then **Advance 1 day**.

- The date becomes November 1, with no new alerts.
- The Budget card shows personal care at $135, rent at $1,500, savings at $500 and free at $2,065.

### 4. Chat

On **Chat**:

| Ask | Expected |
|---|---|
| What did I spend on personal care last month? | $130: $40 at Barber, $90 at FitClub Gym. The tool chip shows `2026-10-01` to `2026-10-31` |
| What did I spend on rent last month? | $1,500 |
| What did I spend on personal care in October 2026? | $130 |
| How much have I spent so far this month? | $0, searching only November 1, because the planted data ends on October 31 |
| What is a Roth IRA? | A sound answer, citing IRS sources |
| What is the 50/30/20 budgeting rule? | A sound answer, citing the Budgeting basics page |
| What is my net worth? | $100, no debts |

To see the date fix on another day, restart the demo (see Undo), advance 7 days and ask again. On October 8:

| Ask | Expected |
|---|---|
| How much did I spend on rent last month? | $1,500, searching `2026-09-01` to `2026-09-30` |
| How much have I spent on personal care this month? | $90, searching `2026-10-01` to `2026-10-08` |
| How much have I spent so far this month? | $1,590: $1,500 rent and $90 personal care |

The model chooses the dates, so check them when an answer looks wrong. These were each run once.

### 5. A restart keeps progress

Press Ctrl+C, then run `docker compose up` again.

- `init` prints `FERNET_KEY already set; left as it is` and `demo user budget-overspend-recurring already exists; left as it is`.
- `models` prints that both models are there and `knowledge base already built`.
- The date is where you left it.

### 6. No Plaid keys

On the dashboard, the Link button should read: `Couldn't start Link: Plaid keys aren't set in .env, so linking a bank is off. The demo data works without them; see the README.`

To check the API on its own:

```bash
curl -s -X POST localhost:8000/plaid/link-token
```

### 7. Plaid, with your `.env`

This is the path where you send your professor your `.env`. Copy it into the clean clone and link a sandbox bank:

```bash
cp <your repo>/.env .env
echo 'DEV_USER_EMAIL=dev@localhost' >> .env
docker compose up -d
docker compose exec backend python scripts/sandbox_link.py http://localhost:8000
```

- `init` prints `FERNET_KEY already set; left as it is`.
- The script prints `OK: 48 transactions across 14 accounts; last run added 0 (no duplicates)`.
- The dashboard shows 14 accounts and 3 debts, with net worth around -$77,164.

The script links without the Plaid Link window. **Clicking through Link in a browser wasn't run.** Try **Connect a bank** with First Platypus Bank, `user_good` / `pass_good`.

### 8. Tests

```bash
docker compose exec backend python -m pytest -q -k "not live and not sandbox"
docker compose exec frontend npm test
```

Expected: `701 passed, 15 deselected`, and `73 passed`. `npm run lint` is clean. The frontend's `npm run build` wasn't run.

## Failure paths to try

- **Ollama off at first start.** After about 30 seconds, `models` exits with code 1 and prints `Can't reach Ollama at ...` with the command to run again. The app still opens. Start Ollama, then run `docker compose run --rm models`. Seen by pointing at a closed port, not by actually stopping Ollama.
- **Ollama stops during an advance.** See the first known limit below.
- **A blank key.** Copy `.env.example` to `.env` yourself before the first `up`. `init` fills in the key and the backend starts. Covered by unit tests, not run by hand.

## Not verified

- **The UI in a real browser**, including the Plaid Link window.
- **Linux and Windows.** `extra_hosts` is there for Linux, but it hasn't run there.
- **`docker-compose.gpu.yml`.** Compose accepts the file, but it hasn't run on a GPU.
- **`docker-compose.ollama.yml`** was only partly run: a container pulled `nomic-embed-text` and listed it. The 9 GB chat model and a full demo weren't run on it. On CPU, expect it to be slow.
- **A machine with less memory or a smaller model.** The 16 GB figure in the README is an estimate.
- **A first-time model download.** Both models were already on this Mac for the full runs.
- **The eval harness in Docker.**

## Known limits that can look like bugs

- **Ollama going down mid-advance writes an extra alert per day.** The agent falls back to a plain `Personal care is over its $42.00 limit` alert and doesn't deduplicate it. This happened in testing, with 4 extra alerts. It's existing behavior: an overspend is never hidden. Restart the demo if you see it.
- **Chat can map a merchant to the wrong category.** "How much did I spend at the gym?" searched general services and answered $0. This predates TPC-42. The README says to ask about personal care.
- **The debt planner stays on the real date.** Chat's date follows the simulation, but `plan_debt_payoff` starts next real month, to match the dashboard's payoff card. The demo user has no debts, so this doesn't show in the demo.
- **The eval scenario `mixed-card-vs-emergency` fails on facts.** It scored 33% over 3 runs with the date line and 0% without it, and it was 0-50% on October 27. This isn't from TPC-42. A full eval with the date line, 1 run each, got 100% on the other 5 scenarios. Earlier eval results were made without the date line, and only this scenario was compared directly.
- **The planted data ends on October 31.** November has a budget but no transactions.
- **Your own Docker stack now opens as the demo user.** Set `DEV_USER_EMAIL=dev@localhost` in your `.env` to see your linked accounts. Compose also turns on `ENABLE_ADMIN` by default.
- **The goal agent isn't in this branch.** TPC-42 branches from before TPC-23, so check the demo again after merging.

## Undo

- Restart the demo:

  ```bash
  docker compose exec backend python scripts/seed_budget_user.py --profile overspend-recurring --replace --start default
  docker compose exec backend python scripts/setup_demo.py
  ```

- Remove everything:

  ```bash
  docker compose down -v --rmi local
  rm -rf /tmp/tpc-42-check
  ```

Your own repo, `.env` and `dev.db` aren't touched as long as you ran all of this in the clean clone.
