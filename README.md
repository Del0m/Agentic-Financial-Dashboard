# Agentic Financial Dashboard

Agentic financial dashboard: link bank accounts through Plaid, see balances, net worth and debts, chat about your money, and let a local-LLM budget agent flag overspending and propose changes. Design: [`docs/architecture.md`](docs/architecture.md).

For how the agents work, see [the agentic system guide](docs/agent-pipelines.md): model decisions, tool loops, memory, goal self-correction, approval, and evaluation limits.

## Quickstart

You need [Docker](https://www.docker.com/products/docker-desktop/), [Ollama](https://ollama.com/download), the supplied **Plaid client ID and Sandbox secret** (or your own [Plaid developer account](https://dashboard.plaid.com/signup)), about 13 GB of free disk space, and roughly 16 GB of memory for the chat model. The walkthrough uses Plaid Sandbox test accounts; no real bank account is needed.

1. Start Docker and Ollama.
2. In the repo root, create `.env` by copying `.env.example` **only if `.env` does not already exist**. Set these values before starting the app:

   ```dotenv
   PLAID_CLIENT_ID=<your client ID>
   PLAID_SECRET=<your Sandbox secret>
   PLAID_ENV=sandbox
   DEV_USER_EMAIL=dev@localhost
   ```

   On a fresh installation, leave `FERNET_KEY` blank: setup generates it. If `.env` already has a key, retain it so existing bank tokens remain readable. Never commit `.env`.
3. From the repo root:

   ```bash
   docker compose up --build
   ```

4. Open http://localhost:3000. With the configuration above, the dashboard opens directly on the Plaid user. Follow the [Plaid Sandbox walkthrough](#plaid-sandbox-demo-income-goals-and-live-purchases) below to connect the custom test accounts, generate goals and add a live purchase.

The first start builds the images and downloads a 9 GB model. The app opens before the download finishes, but Chat and model-driven agent decisions require it. Wait for successful model setup; on a first installation the logs finish with `all set: chat and the budget agent are ready`. To stop, press Ctrl+C. To restart, run the same command; linked accounts and progress are kept.

Setup creates the encryption key if needed, applies database migrations, initializes the temporal graph, pulls the models and builds the knowledge base. If Ollama was unavailable during setup, start it and run:

```bash
docker compose run --rm models
```

### Plaid Sandbox demo: income, goals and live purchases

This walkthrough uses simulated bank accounts served through Plaid Sandbox. No real bank account is needed. Use this custom scenario on its own: combining it with the built-in College Student or generic accounts changes the totals and can introduce missing card-payment data.

#### 1. Configure Plaid

Quickstart configures these values in the repo-root `.env`. Confirm them before following the walkthrough:

```dotenv
PLAID_CLIENT_ID=...
PLAID_SECRET=...
PLAID_ENV=sandbox
DEV_USER_EMAIL=dev@localhost
```

Keep the generated `FERNET_KEY` and never commit `.env`. `DEV_USER_EMAIL=dev@localhost` selects the Plaid user for the walkthrough, so the full walkthrough uses the same linked accounts. After changing configuration, run:

```bash
docker compose up -d
```

Wait for the model and knowledge base setup described in Quickstart to finish. Open http://localhost:3000/dashboard. If this user already has other banks linked, use the reset in step 5 before proceeding.

#### 2. Connect the custom test accounts through Link

The complete configuration is [backend/fixtures/plaid-goals-demo.json](backend/fixtures/plaid-goals-demo.json). It specifies:

| Data | Configuration |
|---|---|
| Demo Checking | $6,000 starting balance |
| Emergency Savings | $1,000 starting balance |
| Demo Rewards Card | $2,400 starting balance, 24% purchase APR, $75 minimum payment |
| Salary | $4,200 monthly, categorized as income in the validation run |
| Regular expenses | $2,600 per complete month |
| History | July–October 2026 plus early November activity; dates shift forward when a new connection is created |

The savings/card histories contain $0 initialization entries to prevent random transactions. Available balances are fixed for the demo; do not expect the $6,000 available balance to fall after each injected purchase. The app computes goal affordability from the imported history and forecast, so proposal amounts are not fixed by this table.

1. Click **Connect a bank**. At the phone-number screen, choose **Continue without phone number**. The **Switch profile** menu and **Default Returning User** are not needed for this route.
2. Choose **First Platypus Bank**, a non-OAuth Sandbox institution.
3. Use either login method below:

   | Method | Username | Password |
   |---|---|---|
   | Direct JSON, no Sandbox Studio access needed | `user_custom` | Copy the **entire contents** of [plaid-goals-demo.json](backend/fixtures/plaid-goals-demo.json), including the outer braces, into the password field. Do not enter the filename. |
   | Saved custom user | In your Plaid Dashboard, open **Build → Sandbox → Users**, create a custom user with the same JSON, and save it. Enter that saved username here. | Any nonempty password, such as `pass_good`. |

   A saved custom **user** is a set of bank-login test credentials; it does not automatically appear in Link's phone-number **Switch profile** menu. The direct JSON method uses the API credentials already configured on the server and needs no Dashboard login.
4. Complete the remaining Link screens and select all three accounts. Wait for background import. If it fails, use **Retry sync** rather than connecting the bank again.
5. Confirm the three account names and check **Debts by APR** for the card's **24% APR and $75 minimum**. There should be no unrelated generic loan or credit-card accounts.

Both custom login methods are described in [Plaid's custom Sandbox user documentation](https://plaid.com/docs/sandbox/user-custom/). Editing a saved custom user's JSON does not update an existing connection; reset and link again to apply changes. The dashboard prompts **Connect a bank** only when there is no connection. Once linked, it shows **Bank connected** and will not start another Link session until you explicitly disconnect or reset. Refreshing or returning to the website reuses the saved connection; use **Retry sync** for an incomplete import. To replace the Sandbox bank, reset first.

#### 3. Generate and approve goals

1. Open **Alerts**, leave **Date (blank for today)** empty, and click **Run budget check**. Wait for it to finish so the budget and recurring streams are available.
2. Inspect the Dashboard budget and **Recurring charges → Income**. This user follows the real date.
3. Open **Goals → Generate goals**. This configuration produced valid **debt-payoff** and **emergency-fund** proposals in the isolated real-model verification. Model wording, amounts and the set of goals may differ.
4. Accept a proposal. It appears among accepted goals; its budget allocation starts next month. No money is transferred.

If Goals reports a missing balance/minimum, inspect the card shown on Dashboard: the intended custom card has both fields. An additional generic card can block all goals. If Goals reports no surplus, check the imported income, history and existing commitments against the custom fixture.

#### 4. Send a purchase through Plaid and inspect the agent

After the initial budget check, run this from the repo root:

```bash
docker compose exec backend python scripts/demo_purchase.py laptop --plaid
```

This sends a $1,200 test purchase to Plaid Sandbox, not directly into the app database. Wait at least five seconds, then, still as **Plaid bank**, click **Alerts → Run budget check** with the date blank. Inspect the bank-sync result and **Agent activity**, then return to Dashboard Transactions to find the new purchase. Plaid may rename or recategorize it. If nothing arrives, wait and retry the budget check before sending another purchase.

A new budget alert depends on the current limits, date and previous warnings. Show the actual measured facts, remembered advice, diagnosis, decision and code checks even if the agent stays quiet. In Chat, try **“What debts do I have, including their APRs?”** or **“What did I spend on general merchandise this month?”** using the category actually returned by Plaid. Compare the answer with the imported data.

For this walkthrough, do **not** also run `sandbox_link.py --dynamic`: it creates a different test bank with generic accounts. The custom Sandbox connection supports purchase injection; the supplied CLI tries the linked bank directly.

Verification: the JSON was accepted by Plaid Sandbox and returned the three intended balances, 24% purchase APR, $75 minimum, and 38 transactions, including five salary deposits categorized as income. The returned data generated goals in an isolated app database. This verifies the configuration/API path; a complete browser Link walkthrough with this JSON and a live-purchase run against this custom user still need rehearsal. The JSON was validated on November 8, 2026.

#### 5. Reset for another presentation

For the browser demo, click **Disconnect bank** beside **Bank connected** on Dashboard. Review the confirmation, then click **Disconnect and clear demo data**. It creates a database backup, revokes the Sandbox Item at Plaid, and clears the local connection, transactions, budget, alerts, goals and advice. The dashboard returns to **Connect a bank** so you can demonstrate the full Link login again. **Cancel** leaves everything intact. This button is available only for the Sandbox demo with admin tools enabled, and refuses to run during active Chat, agent or sync work.

Reconnect with the custom JSON from step 2, then wait for import and run a new budget check. This is a fresh presentation: prior approvals and injected purchases do not carry over. The database backup and older checkpoint/audit files remain on disk.

The terminal command below is an alternative local reset. Unlike the button, it does not revoke the old Item at Plaid:

Wait for active runs to finish, then run:

```bash
docker compose stop backend && docker compose run --rm --no-deps backend python scripts/reset_plaid.py --yes && docker compose up -d backend
```

This backs up the database, clears only the local Plaid user's data and leaves it ready for **Connect a bank**. Old Items remain at Plaid. Reconnect using the same custom JSON in step 2 for fresh history. Run the reset script without `--yes` for a preview. If reset fails, the command stops before restart; read the error and restart with `docker compose up -d backend` when ready.

#### Plaid demo command reference

Run these from the repository root after connecting the custom Sandbox user. Choose the purchase command for the step you want; do not run every example in sequence unless you intend to add every purchase. Keep `--plaid` on purchase commands so they go through Plaid Sandbox.

**Copy the custom login JSON (macOS):**

```bash
pbcopy < backend/fixtures/plaid-goals-demo.json
```

Use username `user_custom` and paste that JSON as the password at the **First Platypus Bank** login. On other systems, open the JSON file and copy its entire contents.

**List the available purchase presets without adding anything:**

```bash
docker compose exec backend python scripts/demo_purchase.py --list
```

Preset descriptions are examples, not guaranteed outcomes. Plaid's returned categories, your current budget and alert history determine the results.

**Add the main $1,200 laptop purchase, then start the agent in the browser:**

```bash
docker compose exec backend python scripts/demo_purchase.py laptop --plaid
```

Wait at least five seconds, then click **Alerts → Run budget check**, leaving Date blank. This gives live activity in the browser. Check the sync result and find the new transaction on Dashboard.

**Alternatively, add the laptop and run the agent from the terminal:**

```bash
docker compose exec backend python scripts/demo_purchase.py laptop --plaid --run
```

This adds its own laptop, waits five seconds, syncs and prints the completed decision trace. Use it **instead of** the preceding command for that purchase. Refresh Alerts for its completed trace; CLI runs do not supply the API process's live step events.

**Add a small purchase or your own merchant/amount:**

```bash
docker compose exec backend python scripts/demo_purchase.py coffee --plaid
```

Or:

```bash
docker compose exec backend python scripts/demo_purchase.py --plaid --merchant "Sushi Go" --amount 64.20
```

Wait at least five seconds and run the budget check in the browser afterward. Plaid assigns the category; `--category` cannot force its returned category. `--undo` cannot remove a purchase sent through Plaid. Repeated pushes accumulate and Sandbox rate limits can require waiting at least a minute before retrying.

**Run another check without adding a purchase:** use **Alerts → Run budget check** again. Inspect Remembered and Decided in Agent activity. A duplicate warning may be suppressed, but silence is not guaranteed if another signal changed.

**Preview the reset target without deleting anything:**

```bash
docker compose exec backend python scripts/reset_plaid.py
```

For a full reset, use the stop/reset/restart command in [step 5](#5-reset-for-another-presentation), then reconnect the custom JSON. Use **Goals → Generate goals → Accept** for human approval; no terminal purchase command is needed to generate goals. On Dashboard, **Cash forecast → Show as a table** and **Paying off your debts → Extra per month** use the same linked accounts.

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
# Configure .env as in Quickstart; retain an existing FERNET_KEY.
python3.12 -m venv venv && venv/bin/pip install -r backend/requirements.txt -c backend/requirements.lock
venv/bin/python backend/scripts/setup_env.py
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

To try it, follow the [custom Plaid Sandbox login](#2-connect-the-custom-test-accounts-through-link) above. Use the same JSON and only that connection for the walkthrough.

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

The repo is mounted into both containers, so they use the same `.env`, `dev.db`, checkpoints and `kb/chroma` as running locally, and edits reload like `--reload` and `npm run dev`. The one-off `init` service runs on every `up`, before the backend: it creates `.env` if needed, runs `alembic upgrade head`, initializes its fixtures, and fills the temporal graph with [`setup_graph.py`](backend/scripts/setup_graph.py). The one-off `models` service pulls whatever's missing and builds the knowledge base if it's empty. Nothing waits for it. The frontend reaches the backend at `http://backend:8000` over the Compose network, and the backend reaches Ollama at `host.docker.internal:11434`.

The documented Plaid walkthrough requires `DEV_USER_EMAIL=dev@localhost` in `.env`; keep it set when using Compose. `ENABLE_ADMIN` is enabled by default for local development.

One-off commands run inside the backend container, where it's `python`, not `../venv/bin/python`:

```bash
docker compose exec backend python scripts/demo_purchase.py laptop --plaid
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

Use the [Plaid walkthrough](#4-send-a-purchase-through-plaid-and-inspect-the-agent) to run a check before and after a Sandbox purchase. Inspect each run's measured facts, earlier advice, model diagnosis, chosen action and any code override in **Agent activity**. An additional check without a new purchase can demonstrate how prior advice affects the next decision; a new alert is not guaranteed.

## Month-end close and budget styles

On the 1st, every daily run first closes the month that ended. Each line's next limit comes from what was spent: a proposal you accepted is used as-is, fixed bills (rent, savings) follow the actual amount, and everything else moves 30% of the way toward it, by at most 15%. A proposal you never answered expires. If the server missed a month-end, the next run closes each missed month in order.

The budget's total is your typical monthly income. Besides the category lines it has a **Savings** line (fitted from transfers to savings) and a **Free** line: whatever income the other lines leave, never below $0, which also covers spending in categories without a line. If the lines add up to more than your income, close tells you instead of cutting anything.

Two styles are supported: `caps` (the default) and `50_30_20`, which sets needs, wants and savings targets from income. Style changes apply from the next close. The Plaid walkthrough uses the default style and real dates; there are no day-advance steps in this presentation.

The API has `PUT /budget/style`, `PATCH /budget/lines/{id}` (rename a line, or move it between needs and wants) and `GET`/`PATCH /transactions`; the UI for styles and renaming comes later.

## Debt payoff

The dashboard's **Paying off your debts** card compares the two usual ways to pay off debt: **avalanche** (highest APR first) and **snowball** (smallest balance first). Type an extra amount per month and it shows each method's months to debt-free, total interest, the month each debt is paid off, and what the extra saves. In chat, ask "How fast am I debt-free if I add $200 a month?" and the answer comes from the same simulation. It charges interest monthly at APR/12 and holds minimum payments at today's amounts, so it's for comparing plans, not predicting a statement. Debts with no APR or minimum on file are listed and left out. The API is `POST /debt-plan`.

Accepted **debt-payoff goals are included automatically** during their start-to-target months. The card lists each contribution and separates minimum payments, accepted goals and additional extra. For example, a $600 accepted debt goal plus a $75 card minimum models $675/month when Extra per month is zero. Enter only money beyond those commitments in Extra per month. Proposed, stopped, expired and non-debt goals are excluded. Both strategies apply the scheduled goal money across eligible debts in their respective payoff order; goals are not assigned to individual accounts. Chat's payoff tool uses the same schedule. Extra-payment savings compare against minimums plus accepted goals, so the goal is not counted twice.


## Cash forecast

The dashboard's Cash forecast card shows the expected checking balance and a typical range, the buffer line, scheduled bill markers, and a daily table. Change **Keep at least $** and Save to redraw it. `GET /forecast?days=7` reads a live snapshot; `PUT /forecast/buffer` accepts integer cents from $0 to $10,000. Chat answers "Will I run short before payday?" with `get_cash_forecast`, the seventh tool.

Daily runs warn when the low path crosses the buffer within 14 days. The alert names the bill. A warning repeats only for a sufficiently deeper shortfall, the one-time approach within two days, or a genuinely new dip date; changing the buffer never dismisses an existing alert. Proposals to delay spending or move savings do not move money. With no paycheck pattern, the app assumes no income and only alerts.

Monthly surplus comes from normalized income minus bills, uncovered card minimums, accepted goals and average everyday spending; it is independent of the cash balance and buffer. Goals use this figure when supported, otherwise the measured three-month fallback, and display the source. The debt planner shows a suggested extra without entering it for you.

See the [forecast verification handoff](docs/handoffs/2026-11-02-tpc-31-manual-verification.md) for isolated setup. Offline replay: `venv/bin/python eval/forecast_accuracy.py`. On 79 synthetic dates: MAE $7.31 at d+7 and $10.07 at d+14, coverage 68.4% / 79.7%, Landlord dip named 14 days ahead. Those scores measure synthetic fixtures, not forecast accuracy on production bank accounts.

## Recurring charges

The dashboard lists stored recurring charges and income between Goals and Transactions. The daily run detects posted transactions over 180 days, raises new-subscription, price-increase and income-change alerts, and keeps stopped streams in a muted group. Chat can answer "What subscriptions do I have?" with `get_recurring`; `GET /recurring` reads the same stored data without running detection.

Accepting an optional `suggest_cancel` lowers the category's next budget line once, after accepted budget adjustments, rounded up to $5. It does not cancel anything with a merchant. Pending suggestions survive month-end, and accepted streams stop raising recurring signals. These alerts do not pause the run.

Use a disposable database as described in [the recurring verification handoff](docs/handoffs/2026-11-02-tpc-33-manual-verification.md). New Plaid Items request 180 days; existing Items need relinking. The offline detector gate is `venv/bin/python eval/recurring_accuracy.py` from the repo root.

## Goals

Open **Goals** from the dashboard header. **Generate goals** asks the goal agent to work out what you have left in a typical month, asks the local model for 1-3 goals (debt payoff, emergency fund, savings) that fit within 80% of it, and computes each goal's target and date. High-interest debt comes first. Accept or dismiss each goal on its own. An accepted goal shows on the dashboard's Goals card and joins the budget as a line (for example "Debt payoff: $583.00/mo") when the next month opens; the month under way never changes. **Stop** ends an accepted goal's line at the next close. A proposal you don't answer expires when that month opens, and you can generate again. The API is `POST /goals/generate`, `GET /goals/runs/{id}`, `POST /goals/{id}/accept` and `/dismiss`.

`GET /goals` returns the current proposal (`?status=accepted` or `current` for accepted goals too). Goals proposed on the command line can be accepted in the app. The app doesn't track what you actually pay toward a goal, so a goal line is a slice of the plan with no spending measured against it. See the [TPC-25 verification handoff](docs/handoffs/2026-11-02-tpc-25-manual-verification.md) to try it by hand.

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
