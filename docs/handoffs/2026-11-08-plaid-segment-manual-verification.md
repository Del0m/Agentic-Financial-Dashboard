# Plaid segment: manual verification

The demo now has a real Plaid path next to the scripted demo user. A **Demo user / Plaid bank** switch in the header changes users without editing `.env` or restarting. `demo_purchase.py --plaid` sends a purchase to Plaid Sandbox, not into the database. The app's normal Plaid sync pulls it in on the next budget run, and the Agent activity panel says what the sync brought in.

The demo user still never talks to Plaid. It's seeded so the demo can control dates and plant problems. For any real user, Plaid is the only source of bank data.

## What changed

| File | Change |
|---|---|
| `backend/app/api/deps.py` | `current_email`: with `ENABLE_ADMIN` on, a `view=plaid` cookie makes requests act for `dev@localhost`, the user Plaid links banks to, in place of the configured demo user. |
| `backend/app/api/admin.py` | `GET`/`PUT /admin/view`: reads, sets or clears the cookie. `available` is false when the configured user already is `dev@localhost`. |
| `backend/app/agents/budget_graph.py` | `plaid_sync` also returns `added`, how many new transactions Plaid sent this run. |
| `backend/app/plaid_service/client.py` | `sandbox_public_token(username=...)` can link a Sandbox test user. `DYNAMIC_USER` is `user_transactions_dynamic`. |
| `backend/scripts/sandbox_link.py` | `--dynamic` links that test user. It always sends the `view=plaid` cookie, so the bank goes to `dev@localhost`, and it names the bank First Platypus Bank. |
| `backend/scripts/demo_purchase.py` | `--plaid` calls `/sandbox/transactions/create` on the Plaid user's first linked bank that accepts it. It sends every date in one call, ignores `--category` (Plaid categorizes), and refuses `--undo`. `--run` waits 5 s, runs the agent, then prints the `[plaid]` sync count and the transaction as Plaid named it. It loads `.env` first so it can decrypt the bank's token. |
| `frontend/components/ViewSwitch.tsx` | New. The switch, shown only when available. Choosing a view saves it and reloads. Placed in the Dashboard and Alerts headers. |
| `frontend/components/AgentActivity.tsx` | `syncLine`: "Bank sync through Plaid: N new transactions from M linked banks", or that the demo user's data is seeded, not linked through Plaid. |
| `frontend/lib/api.ts` | `getView`, `setView`, and `sync.added` in the run summary. |
| Docs | README Plaid section, new test docs `test_demo_purchase.md` and `ViewSwitch.test.md`, entries in `test_admin_api.md`, `test_budget_graph.md` and `AgentActivity.test.md`, and the local demo guide's run of show. |

## Check it

1. Open http://localhost:3000/dashboard. The header has **Demo user | Plaid bank**, with Demo user pressed.
2. Click **Plaid bank**. The page reloads with First Platypus Bank's Sandbox accounts, transactions and budget. Click **Demo user** to go back: net worth is $100 and the date chip is back.
3. If the Plaid user has no `user_transactions_dynamic` bank yet, link one:

   ```bash
   docker compose exec backend python scripts/sandbox_link.py --dynamic
   ```

   It prints `OK` lines and about 360 transactions across 7 accounts.
4. Push a purchase through Plaid and run the agent:

   ```bash
   docker compose exec backend python scripts/demo_purchase.py laptop --plaid --run
   ```

   It prints `sent to Plaid Sandbox: ElectroHub laptop $1,200.00 …`, then `[plaid] synced 1 new transaction(s)…`, the trace, and `synced from Plaid: Electric Hub Inc. $1,200.00 …, Plaid's category GENERAL_MERCHANDISE`.
5. Do it again without `--run`: push `--plaid --merchant "Sushi Go" --amount 64.20`, switch to **Plaid bank**, open Alerts and click **Run budget check**. Agent activity's first line reads "Bank sync through Plaid: 1 new transaction from …".
6. As **Demo user**, click Run budget check. The line reads "Bank sync: skipped. This user's data is seeded for the demo, not linked through Plaid."

## Tests

- Backend: 1,964 offline tests pass. The new ones are 4 for the view switch, 1 for the sync count and 9 for `--plaid` (Plaid faked).
- Frontend: 168 tests pass, including 5 for the switch and 4 for the sync line. ESLint and `tsc` are clean.
- Against real Plaid Sandbox:
  - A purchase pushed to a `user_transactions_dynamic` bank synced back in about 3 seconds, renamed and categorized by Plaid.
  - Backdating worked up to 20 days.
  - Plaid rate-limited after about four pushes in a minute.
  - `user_custom` banks also accept pushed purchases. Loading months of custom history didn't work in my tests.
- Steps 3 and 4 ran for real on the live database, by mistake. I meant to use a copy, but zsh passed the copy settings to Docker as one argument. They worked as described: run 89 on the Plaid user flagged General merchandise as one-time and wrote alerts 7–13. The demo user wasn't touched.
- In the browser, the switch moved between the two users both ways, with no console errors.

## Limits

- Cleanup, with the user's OK:
  - The Plaid user's 4 older bank links were removed (rows 1, 2, 3, 11), with their 56 accounts, 246 transactions and 12 liabilities. Three of them failed to sync, and the duplicated accounts had pushed net worth to -$335,656.60.
  - The budget, run, alerts, recurring charges and graph built from the duplicated data were removed too. The graph was rebuilt with `setup_graph.py`, and a fresh budget run built a clean budget. The new run reused id 89, and its trace started clean, with no earlier alerts carried over.
  - A backup from before the cleanup is in `backend/dev.backup-2026-11-08-before-plaid-cleanup.db`.
  - The Plaid user now has one link (First Platypus Bank, `user_transactions_dynamic`) with 7 accounts.
- The first budget for the Plaid user was fitted from history that already includes the first pushed laptop, so General merchandise's limit absorbed it. The next pushed laptop is what gets flagged.
- A Plaid budget run calls the model for several lines and takes about a minute.
- Plaid can't take a pushed purchase back.
- The view switch only exists with `ENABLE_ADMIN` on. It can only choose between the configured user and `dev@localhost`.
