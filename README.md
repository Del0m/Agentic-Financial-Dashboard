# Agentic Financial Dashboard

Agentic financial dashboard: link bank accounts through Plaid, see balances, net worth and debts, chat about your money, and let a local-LLM budget agent flag overspending and propose changes. Design: [`docs/architecture.md`](docs/architecture.md).

For how the agents work, see [the agentic system guide](docs/agent-pipelines.md): model decisions, tool loops, memory, goal self-correction, approval, and evaluation limits.

## Quickstart

You need [Docker](https://www.docker.com/products/docker-desktop/) and [Ollama](https://ollama.com/download), about 13 GB of free disk space, and roughly 16 GB of memory for the chat model. No keys or accounts are needed.

1. Start Docker and Ollama.
2. From the repo root:

   ```bash
   docker compose up --build
   ```

3. Open http://localhost:3000. The dashboard opens as a demo user on a simulated date, October 1, 2026.

The first start builds the images and downloads a 9 GB model. The app opens before the download finishes, but chat and the budget agent's advice need it. It's ready when the logs say `all set: chat and the budget agent are ready`. To stop, press Ctrl+C. To start again, run the same command; your progress is kept.

**Setup runs by itself.** [`setup_env.py`](backend/scripts/setup_env.py) creates `.env` and generates its encryption key, [`setup_demo.py`](backend/scripts/setup_demo.py) seeds the demo user, [`setup_graph.py`](backend/scripts/setup_graph.py) fills the temporal graph, and [`setup_models.py`](backend/scripts/setup_models.py) pulls the models and builds the knowledge base. If Ollama wasn't running the first time, start it and run `docker compose run --rm models`.

### Demo walkthrough

The demo user has a new $90 gym membership that its $42 personal care budget doesn't cover.

1. Open **Alerts** and click **Advance 7 days**. Each simulated day runs the budget agent, so this takes about 2 to 3 minutes on an Apple Silicon Mac. The date moves to October 8. A **Personal care** alert proposes a $135 budget for next month. You'll also see a few **Low balance** warnings from the cash forecast, because the demo checking account holds only $100 against $1,500 rent. The dashboard's Budget card shows personal care in red, $48 over its limit.
2. Click **Accept** on the Personal care alert. Dismiss the low-balance warnings, or leave them.
3. Click **To month end**, then **Advance 1 day**. Expect about 20 seconds per simulated day, so roughly 8 minutes in all. On November 1 the month closes, and the dashboard's Budget card shows personal care at $135.
4. Open **Chat** and try these:
   - "What did I spend on personal care last month?" ($130: $40 at Barber and $90 at FitClub Gym)
   - "What did I spend on rent last month?" ($1,500)
   - "What is a Roth IRA?"
   - "What is the 50/30/20 budgeting rule?"
   - "What is my net worth?" ($100)

Chat follows the simulated date, so "last month" means October once the demo reaches November. The planted data ends on October 31, so November has no transactions. The model can also pick the wrong category for a merchant, so ask about "personal care" rather than "the gym".

To show the agents reacting to new spending, add a purchase mid-demo with [`demo_purchase.py`](backend/scripts/demo_purchase.py), then click **Run budget check** on Alerts. `--list` shows the ready-made purchases:

```bash
docker compose exec backend python scripts/demo_purchase.py --list
```

To restart the demo:

```bash
docker compose exec backend python scripts/seed_budget_user.py --profile overspend-recurring --replace --start default
docker compose exec backend python scripts/setup_demo.py
```

### Link a bank with Plaid (optional)

This needs Plaid sandbox keys, from your own free [Plaid](https://dashboard.plaid.com/signup) account or supplied separately. Sandbox keys only reach Plaid's test data. Add them to `.env`, along with a user that starts empty:

```bash
PLAID_CLIENT_ID=...
PLAID_SECRET=...
DEV_USER_EMAIL=dev@localhost
```

If you were given a complete `.env` instead, put it in the repo root and add only the `DEV_USER_EMAIL` line. Never commit `.env`.

Run `docker compose up -d`, click **Connect a bank**, pick **First Platypus Bank**, and log in with `user_good` / `pass_good`. To return to the demo user, remove the `DEV_USER_EMAIL` line and run `docker compose up -d` again.

With Docker Compose you can skip the `DEV_USER_EMAIL` line: the Dashboard and Alerts headers have a **Demo user / Plaid bank** switch, available while `ENABLE_ADMIN` is on. **Plaid bank** shows `dev@localhost`, the user Plaid links banks to.

To send purchases through Plaid, link Plaid's `user_transactions_dynamic` test user. You can log in as that user in **Connect a bank** (any password), or run:

```bash
docker compose exec backend python scripts/sandbox_link.py --dynamic
docker compose exec backend python scripts/demo_purchase.py laptop --plaid --run
```

`--plaid` sends the purchase to Plaid Sandbox. Plaid names and categorizes it, and the next budget run's sync pulls it in like any bank transaction. Without `--run`, click **Run budget check** on Alerts a few seconds later. Plaid rate-limits new Sandbox transactions to a few a minute.

### On other machines

macOS is the tested setup. On Linux or Windows, or if you'd rather not install Ollama, run it in a container:

```bash
docker compose -f docker-compose.yml -f docker-compose.ollama.yml up --build
```

That's CPU only, so the model will be slow. With an NVIDIA GPU, the [NVIDIA container toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) and Compose 2.30 or newer, add `-f docker-compose.gpu.yml` as well. Don't use the GPU file on a Mac, where Docker can't use the Apple GPU. To use Ollama on a Linux host instead of the container, make it listen beyond localhost with `OLLAMA_HOST=0.0.0.0`.

With less memory, a smaller model may work: set `OLLAMA_MODEL=` in `.env` to one that supports tool calling, then run `docker compose run --rm models`. Answers will be different from the ones above.

If a port is taken, stop whatever is using 3000 or 8000.

## Run locally

First time only:

```bash
cp .env.example .env                 # fill in PLAID_CLIENT_ID, PLAID_SECRET, FERNET_KEY
python3.12 -m venv venv && venv/bin/pip install -r backend/requirements.txt -c backend/requirements.lock
cd backend && ../venv/bin/alembic upgrade head && cd ..
cd frontend && npm install && cd ..
```

Then, in two terminals:

```bash
# terminal 1: API on :8000
cd backend && ../venv/bin/uvicorn app.main:app --reload --port 8000

# terminal 2: web app on http://localhost:3000
cd frontend && npm run dev
```

The browser only talks to the Next.js server. It forwards `/api/*` to FastAPI on `:8000` (`BACKEND_URL` overrides that), so no CORS setup is needed.

To try it: open http://localhost:3000, click **Connect a bank**, pick **First Platypus Bank**, and log in with `user_good` / `pass_good`.

Linking returns immediately after saving the bank connection; its first full sync runs in the background. The dashboard polls while it is syncing and keeps **Retry sync** available if any step fails, including balances or liabilities. Run one API worker: per-item sync locks serialize webhook, initial and manual syncs within that process.

## Run with Docker

The details behind the quickstart, for developing in the containers. The backend and the frontend run as two containers on one Docker network, in dev mode with live reload. On a Mac, Ollama stays on the host, because Docker can't use the Apple GPU and `qwen2.5:14b` would crawl on the CPU.

1. Start Ollama (`ollama serve`) and Docker Desktop.
2. Stop the API and web servers from "Run locally" if they're running. They hold ports 8000 and 3000, and `docker compose up` fails with "Ports are not available ... address already in use" otherwise. Don't run both at once: they share `backend/dev.db`, and SQLite locking across the Docker mount isn't reliable.
3. From the repo root:

   ```bash
   docker compose up --build        # -d to run in the background; docker compose down to stop
   ```

   Open http://localhost:3000. The API is still on http://localhost:8000 (`/docs`).

The repo is mounted into both containers, so they use the same `.env`, `dev.db`, checkpoints and `kb/chroma` as running locally, and edits reload like `--reload` and `npm run dev`. The one-off `init` service runs on every `up`, before the backend: it creates `.env` if needed, runs `alembic upgrade head`, seeds the demo user if it's missing, and fills the temporal graph with [`setup_graph.py`](backend/scripts/setup_graph.py). The one-off `models` service pulls whatever's missing and builds the knowledge base if it's empty. Nothing waits for it. The frontend reaches the backend at `http://backend:8000` over the Compose network, and the backend reaches Ollama at `host.docker.internal:11434`.

Compose sets `DEV_USER_EMAIL` to the demo user and `ENABLE_ADMIN` to `1` unless `.env` says otherwise. To see accounts you linked yourself, set `DEV_USER_EMAIL=dev@localhost` in `.env`.

One-off commands run inside the backend container, where it's `python`, not `../venv/bin/python`:

```bash
docker compose exec backend python scripts/seed_budget_user.py --profile overspend-recurring --replace
docker compose exec backend python -m pytest -q -k "not live and not sandbox"
```

After changing `backend/requirements.txt`, regenerate `backend/requirements.lock` (its header says how), then run `docker compose up -d --build backend` (a plain `build` leaves the old container running). After changing `frontend/package-lock.json`, run `docker compose build frontend && docker compose down -v`. The container's `node_modules` lives in a volume (the Mac's native packages don't run in Linux), and `down -v` drops it so the new packages show up.

## Knowledge base

The chat agent's `search_kb` tool reads public financial guidance from a local Chroma store. One-time setup, from `backend/`:

```bash
ollama pull nomic-embed-text
../venv/bin/python scripts/fetch_kb.py      # fetches only what isn't in kb/sources/ yet (on a fresh clone: the git-ignored copyrighted page)
../venv/bin/python scripts/ingest_kb.py     # rebuilds kb/chroma/ from kb/sources/
../venv/bin/python scripts/search_kb.py "Roth IRA vs 401k"
```

Run `fetch_kb.py --refresh` to update the saved pages (for example, when the IRS publishes new contribution limits), read the changed files, then ingest again. The sources are listed in `kb/sources.json`.

## Chat

`/chat` (linked from the dashboard) answers questions about your linked accounts and cites the public guidance it used. It needs Ollama running with both models pulled, and the knowledge base ingested (see above):

```bash
ollama pull qwen2.5:14b
ollama pull nomic-embed-text
```

Answers stream in as they're written; the chips under each answer show which tools ran, and the links are the guidance the app retrieved. A conversation survives a refresh (one per tab); **New chat** starts a fresh one. One reply runs at a time per user, so run the API with a single uvicorn worker (the default).

## Budget agent and alerts

The budget agent checks spending against this month's budget. It asks the local model (`qwen2.5:14b`) why a category is overspent and what to do about it, and posts alerts to `/alerts`. A proposal to change next month's budget waits for your Accept or Dismiss. The dashboard's **Budget** card shows each line's pace, and the **Transactions** card shows this month's transactions with the line each one counts against: pick another line to correct one, then **Always for <merchant>?** to remember it.

To try it on a synthetic user with a planted overspend:

```bash
cd backend
../venv/bin/python scripts/seed_budget_user.py --profile overspend-recurring --replace
DEV_USER_EMAIL=overspend-recurring@eval.local ../venv/bin/uvicorn app.main:app
```

Then start the web app. On `/alerts`, run a budget check for **2026-10-20**, accept the $135 proposal, and run again: the agent remembers and doesn't alert twice. Other planted users are `overspend-one-time`, `overspend-seasonal` and `overspend-known-merchant`. The same run works from the terminal with `../venv/bin/python -m app.agents.budget_graph --user budget-overspend-recurring --date 2026-10-20 --no-sync`.

## Simulate a month

A synthetic user can live on a simulated date. Each advanced day injects that day's generated transactions and runs the real budget agent, so a planted month can be replayed and watched:

```bash
cd backend
../venv/bin/python scripts/seed_budget_user.py --profile overspend-one-time --replace --start default
../venv/bin/python scripts/simulate.py --user budget-overspend-one-time --days 31
```

With Ollama running, each flagged day takes a few seconds of model time, so a month takes several minutes. To drive it from the browser, start the API with `ENABLE_ADMIN=1 DEV_USER_EMAIL=overspend-one-time@eval.local`; the Alerts page then shows the simulated date with **Advance 1 day**, **Advance 7 days** and **To month end**.

## Month-end close and budget styles

On the 1st, every daily run first closes the month that ended. Each line's next limit comes from what was spent: a proposal you accepted is used as-is, fixed bills (rent, savings) follow the actual amount, and everything else moves 30% of the way toward it, by at most 15%. A proposal you never answered expires. If the server missed a month-end, the next run closes each missed month in order.

The budget's total is your typical monthly income. Besides the category lines it has a **Savings** line (fitted from transfers to savings) and a **Free** line: whatever income the other lines leave, never below $0, which also covers spending in categories without a line. If the lines add up to more than your income, close tells you instead of cutting anything.

Two styles: `caps` (the default, above) and `50_30_20`, which sets needs, wants and savings targets from income (free is the room left in wants). Choose one when seeding, or switch a simulated user; it applies from the next close:

```bash
../venv/bin/python scripts/seed_budget_user.py --profile overspend-recurring --replace --start default --style 50_30_20
../venv/bin/python scripts/simulate.py --user budget-overspend-recurring --days 32 --style caps
```

The API has `PUT /budget/style`, `PATCH /budget/lines/{id}` (rename a line, or move it between needs and wants) and `GET`/`PATCH /transactions`; the UI for styles and renaming comes later.

## Debt payoff

The dashboard's **Paying off your debts** card compares the two usual ways to pay off debt: **avalanche** (highest APR first) and **snowball** (smallest balance first). Type an extra amount per month and it shows each method's months to debt-free, total interest, the month each debt is paid off, and what the extra saves. In chat, ask "How fast am I debt-free if I add $200 a month?" and the answer comes from the same simulation. It charges interest monthly at APR/12 and holds minimum payments at today's amounts, so it's for comparing plans, not predicting a statement. Debts with no APR or minimum on file are listed and left out. The API is `POST /debt-plan`.

## Cash forecast

The dashboard's Cash forecast card shows the expected checking balance and a typical range, the buffer line, scheduled bill markers, and a daily table. Change **Keep at least $** and Save to redraw it. `GET /forecast?days=7` reads a live snapshot; `PUT /forecast/buffer` accepts integer cents from $0 to $10,000. Chat answers "Will I run short before payday?" with `get_cash_forecast`, the seventh tool.

Daily runs warn when the low path crosses the buffer within 14 days. The alert names the bill. A warning repeats only for a sufficiently deeper shortfall, the one-time approach within two days, or a genuinely new dip date; changing the buffer never dismisses an existing alert. Proposals to delay spending or move savings do not move money. With no paycheck pattern, the app assumes no income and only alerts.

Monthly surplus comes from normalized income minus bills, uncovered card minimums, accepted goals and average everyday spending; it is independent of the cash balance and buffer. Goals use this figure when supported, otherwise the measured three-month fallback, and display the source. The debt planner shows a suggested extra without entering it for you.

```bash
cd backend
../venv/bin/python scripts/seed_budget_user.py --profile forecast-dip --replace --start 2026-09-30
DEV_USER_EMAIL=forecast-dip@eval.local ENABLE_ADMIN=1 ../venv/bin/uvicorn app.main:app
# another terminal, same disposable DATABASE_URL:
../venv/bin/python scripts/simulate.py --user budget-forecast-dip --days 12
```

See the [forecast verification handoff](docs/handoffs/2026-11-02-tpc-31-manual-verification.md) for isolated setup. Offline replay: `venv/bin/python eval/forecast_accuracy.py`. On 79 synthetic dates: MAE $7.31 at d+7 and $10.07 at d+14, coverage 68.4% / 79.7%, Landlord dip named 14 days ahead. Real Plaid balances and live seven-tool choice remain unverified.

## Recurring charges

The dashboard lists stored recurring charges and income between Goals and Transactions. The daily run detects posted transactions over 180 days, raises new-subscription, price-increase and income-change alerts, and keeps stopped streams in a muted group. Chat can answer "What subscriptions do I have?" with `get_recurring`; `GET /recurring` reads the same stored data without running detection.

Accepting an optional `suggest_cancel` lowers the category's next budget line once, after accepted budget adjustments, rounded up to $5. It does not cancel anything with a merchant. Pending suggestions survive month-end, and accepted streams stop raising recurring signals. These alerts do not pause the run.

```bash
cd backend
../venv/bin/python scripts/seed_budget_user.py --profile recurring-planted --replace --start 2026-09-30
DEV_USER_EMAIL=recurring-planted@eval.local ENABLE_ADMIN=1 ../venv/bin/uvicorn app.main:app
# in another terminal, with the same disposable DATABASE_URL:
../venv/bin/python scripts/simulate.py --user budget-recurring-planted --days 25
```

Use a disposable database as described in [the recurring verification handoff](docs/handoffs/2026-11-02-tpc-33-manual-verification.md). New Plaid Items request 180 days; existing Items need relinking. The offline detector gate is `venv/bin/python eval/recurring_accuracy.py` from the repo root.

## Goals

Open **Goals** from the dashboard header. **Generate goals** asks the goal agent to work out what you have left in a typical month, asks the local model for 1-3 goals (debt payoff, emergency fund, savings) that fit within 80% of it, and computes each goal's target and date. High-interest debt comes first. Accept or dismiss each goal on its own. An accepted goal shows on the dashboard's Goals card and joins the budget as a line (for example "Debt payoff: $583.00/mo") when the next month opens; the month under way never changes. **Stop** ends an accepted goal's line at the next close. A proposal you don't answer expires when that month opens, and you can generate again. The API is `POST /goals/generate`, `GET /goals/runs/{id}`, `POST /goals/{id}/accept` and `/dismiss`.

The same agent runs from the command line, which doesn't pause for approval:

```bash
cd backend
../venv/bin/python scripts/seed_budget_user.py --profile goals-card-24 --replace
../venv/bin/python -m app.agents.goal_graph --user budget-goals-card-24 --date 2026-11-02
```

`--prefs '{"skip_kinds": ["savings"], "max_share": 0.5, "note": "emergency fund matters most"}'` sets your preferences. `GET /goals` returns the current proposal (`?status=accepted` or `current` for accepted goals too). Goals proposed on the command line can be accepted in the app. The app doesn't track what you actually pay toward a goal, so a goal line is a slice of the plan with no spending measured against it. See the [TPC-25 verification handoff](docs/handoffs/2026-11-02-tpc-25-manual-verification.md) to try it by hand.

Goals are validated as a complete proposal before storage: the monthly total must fit the cap, kinds must be offered and unique, and every computed date must be achievable within 600 months. There are at most two model calls, including malformed replies; the correction receives scrubbed feedback. Failed attempts preserve the previous proposal and report why no replacement was generated. A cap below $1 withdraws it without calling the model. See the [TPC-24 verification handoff](docs/handoffs/2026-11-02-tpc-24-manual-verification.md) for deterministic failure checks and a live CLI walkthrough.

## Run the agent every morning

Set `ENABLE_SCHEDULER=1` and the API runs each real user's budget check daily at `DAILY_RUN_TIME` (06:00), plus the month-end close on the 1st at `CLOSE_RUN_TIME` (05:00), in `SCHEDULER_TZ`. If the server starts after today's slot, it runs it once at startup for anyone not yet checked today. Earlier missed days aren't replayed. Run a single uvicorn worker (the default); the scheduler lives inside it.

## Demo on a phone

Use **Plaid Sandbox data only** for this demo. The app has one unauthenticated dev user: anyone who can reach the LAN address or tunnel can read the dashboard and call its API. Use a trusted network, do not share the tunnel URL publicly, and stop the server/tunnel when the demo ends. Real accounts require authentication and webhook verification first.

1. Build and serve the web app on every network interface:
   ```bash
   cd frontend && npm run build && npm run start:lan
   ```
   (`next dev` blocks its dev resources when opened through a LAN IP, so the demo uses the production server.)
2. Find your Mac's IP: `ipconfig getifaddr en0`. If macOS asks whether `node` may accept incoming connections, allow it.
3. On a phone on the same Wi-Fi, open `http://<that-ip>:3000`. The backend stays on `localhost:8000`, because the Next.js server reaches it from the Mac.
4. If the phone can't connect (campus Wi-Fi often blocks one device from reaching another), use your phone's hotspot or run `ngrok http 3000` and open the HTTPS URL it prints.
5. Use a non-OAuth Sandbox bank such as First Platypus Bank. OAuth banks need a registered `redirect_uri`, which isn't set up.

## Reproducing the results

Python packages are pinned in [`backend/requirements.lock`](backend/requirements.lock), which both the venv install above and the Docker image use. `requirements.txt` is only the short list of what the app imports directly.

Ollama model names are tags, and a tag can be republished with different weights. The benchmark ran on Ollama 0.33.2 with these models:

| Model | Role | Digest |
|---|---|---|
| `qwen2.5:14b` | chat and budget agent | `7cdf5a0187d5c58cc5d369b255592f7841d1c4696d45a8c8a9489440385b22f6` |
| `nomic-embed-text` | knowledge base embeddings | `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f` |
| `gemma3:12b` | eval judge | `b92b218582c521ba287f38f1d6057dcd3c6cbff036f13cac97b5d861f5e58ec3` |
| `llama3.1:8b` | baseline comparison | `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e` |
| `mistral` | baseline comparison | `6577803aa9a036369e481d648a2baebb381ebc6e897f2bb9a766a2aa7bfbc1cf` |

`ollama list` shows the first 12 characters of each digest in its `ID` column. If yours differ, the model changed upstream, and results can drift. If `nomic-embed-text` differs, rebuild the store with `ingest_kb.py`, since its vectors no longer match the ones the results used. Every eval run records the digests it actually used in its `config.json` under `model_digests`.

Even with the same packages and models, model output can differ a little across GPUs and Ollama versions, so expect close numbers rather than identical ones.

## Tests

```bash
cd backend && ../venv/bin/python -m pytest
cd frontend && npm test && npm run lint && npm run build
```

Live tests (real Ollama models, real knowledge base) run when those are available and are skipped otherwise. Every test file, backend and frontend, is documented in [`docs/tests/`](docs/tests/).
