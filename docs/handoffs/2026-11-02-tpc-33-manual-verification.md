# TPC-33 recurring streams: manual verification

Tasks 1–8 are implemented. Recurring charges and income now appear on the dashboard and through `GET /recurring` and `get_recurring()` in chat. Daily runs alert on new subscriptions, price increases and income changes. Accepted cancellation suggestions lower the next budget line once and mute later signals, without pausing or cancelling anything with a merchant.

## Setup

Use a disposable database and checkpoint directory. From the repo root, in terminal 1:

```bash
cd backend
export TPC33_CHECK_DIR="$(mktemp -d /tmp/tpc33-manual.XXXXXX)"
export DATABASE_URL="sqlite:///${TPC33_CHECK_DIR}/recurring.db"
export PROMPT_AUDIT_PATH="${TPC33_CHECK_DIR}/audit.jsonl"
export BUDGET_CHECKPOINT_PATH="${TPC33_CHECK_DIR}/budget.db"
export CHAT_CHECKPOINT_PATH="${TPC33_CHECK_DIR}/chat.db"
export FERNET_KEY="$(../venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
../venv/bin/alembic upgrade head
../venv/bin/python scripts/seed_budget_user.py --profile recurring-planted --replace --start 2026-09-30
DEV_USER_EMAIL=recurring-planted@eval.local ENABLE_ADMIN=1 ../venv/bin/uvicorn app.main:app
```

Start Ollama with the configured chat model for interactive advice. In terminal 2 start `npm run dev` from `frontend/`; open the dashboard at localhost:3000. Use the Alerts page's advance controls, or run `scripts/simulate.py --user budget-recurring-planted --days 25` from `backend/` with the same exported database and checkpoint variables. Do not advance the same user concurrently through both interfaces.

## Checks

1. Advance to Oct 2. The recurring card lists Landlord, Income (biweekly), Streamly, FitClub Gym and Clipflix. Pottery Studio, Grocer and Fuel are absent. No new-subscription flood occurs.
2. Advance one day. One gym price alert says $40.00 to $43.00. Its dashboard badge says `Price up $40.00 to $43.00`. Repeat a daily run and no duplicate recurring alert appears.
3. Advance to Oct 12. NewFlix appears after its third charge, with one new-subscription alert. The Alerts page uses the new signal labels rather than `Over limit`.
4. Advance to Oct 20 and then Oct 25. Clipflix gets one price alert, despite its rise exceeding 10%. All six planted streams remain listed.
5. If the model chooses `suggest_cancel`, accept it and advance past month-end. The category's next line applies accepted adjustments first, then subtracts the stream's normalized monthly typical amount, rounding up to $5. It skips a missing line or nonpositive result and records that outcome. The same accepted stream is not subtracted again next month. Pending cancellation suggestions remain answerable after month-end. Nothing cancels the merchant subscription automatically.
6. A new user has the empty recurring text and history hint. Inflows use Income rather than employer names. Stopped streams appear under No longer seen.
7. In chat ask `What subscriptions do I have?`. Inspect the tool chip for `get_recurring` and the audit for scrubbed outflow names, dollars and no employer name. Live model selection of this sixth tool has not been run here.

After a second charge at a higher price, `previous_cents` clears and the row badge becomes `Price up`; the original alert retains both figures. This is expected.

## Automated evidence

Offline backend: **1,160 passed, 22 live/sandbox cases excluded**. Frontend: **122 passed**. Lint and TypeScript checks passed. End-to-end tests advance real daily runs from Sep 30 through Oct 25, verify the exact first alert dates and accept a cancellation through the real close path. Checkpoint tests inspect SQLite rows, graph snapshots and file bytes and successfully resume.

`venv/bin/python eval/recurring_accuracy.py` passed:

| Profiles | Precision | Recall | False streams | Misses |
|---|---:|---:|---:|---:|
| Each of all 12 profiles | 1.00 | 1.00 | 0 | 0 |
| Pooled | 1.00 | 1.00 | 0 | 0 |

Noisy everyday share: 1.0. Monthly next dates: 372 cases; 313 within one day, maximum error two days (241 exact, 72 one day, 59 two days).

PlantUML was available; the data-model, budget-loop and lifecycle SVGs and PNGs were rendered. Pre-existing documentation and diagram edits remain in the working tree; only recurring additions were staged for this work.

The production build was attempted with both bundlers. Turbopack is blocked by local worker-port binding (`Operation not permitted`), including the escalated attempt. The webpack fallback cannot resolve `fonts.googleapis.com` for the existing Geist fonts. These environment failures remain unverified production-build checks; they were not counted as passing. No app font or build configuration was changed to conceal them.

## Not run and known limits

Postgres migration, real Plaid streams and live chat tool choice were not run. Measurements remain synthetic. The detector's short-gap, restart, skipped-month and price-step behavior is covered offline; annual detection is deferred. Price creep below the threshold stays silent; a large rise can alert on its second charge; metered utilities deliberately do not raise rate-change alerts. Subscription categories remain flexible budget lines, so early-month pace flags remain possible.

For a later Plaid comparison: use Sandbox credentials and an Item linked after the 180-day request change; add a client wrapper for `/transactions/recurring/get`; match its rows to ours by normalized merchant key, direction and frequency; report agreement separately from synthetic precision and recall. That wrapper/comparison is not built. Existing Items need relinking to request longer history. The metered utility taxonomy names still need confirmation against the applicable Plaid category version.

PR #23's description warning from the investigation remains open: avoid mentioning an unfinished ticket in a merged PR description because the Jira trigger may close it. No PR text or Jira state was changed by this work.

## Next handoff

Continue with the approved [cash-flow forecast plan](../superpowers/plans/2026-11-02-tpc-31-cash-flow-forecast.md) on a new branch from this work. It reuses the detector, calendar projection, signal decisions and current/no-current-budget routing. Preserve the unrelated local documentation, diagram and old eval-result edits.
