# TPC-31 cash forecast: manual verification

Tasks 1–8 are implemented on branch `TPC-31`, based on the completed `TPC-33` branch. Neither branch was pushed. The recurring work's completed handoff is [here](2026-11-02-tpc-33-manual-verification.md).

The dashboard now forecasts checking cash as a typical range, shows bill markers and a daily table, and saves a buffer. Daily low-balance alerts name the bill; Goals uses the same forecast surplus with a measured fallback and reports its source. The debt planner shows a suggested extra without changing the input. Nothing moves money.

## Setup

Use a throwaway database, audit and checkpoints. From the repo root, terminal 1:

```bash
cd backend
export TPC31_CHECK_DIR="$(mktemp -d /tmp/tpc31-manual.XXXXXX)"
export DATABASE_URL="sqlite:///${TPC31_CHECK_DIR}/forecast.db"
export PROMPT_AUDIT_PATH="${TPC31_CHECK_DIR}/audit.jsonl"
export BUDGET_CHECKPOINT_PATH="${TPC31_CHECK_DIR}/budget.db"
export CHAT_CHECKPOINT_PATH="${TPC31_CHECK_DIR}/chat.db"
export FERNET_KEY="$(../venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
../venv/bin/alembic upgrade head
../venv/bin/python scripts/seed_budget_user.py --profile forecast-dip --replace --start 2026-09-30
DEV_USER_EMAIL=forecast-dip@eval.local ENABLE_ADMIN=1 ../venv/bin/uvicorn app.main:app
```

Start Ollama with the configured model for interactive advice. In terminal 2 run `npm run dev` from `frontend/`, then open localhost:3000. Advance with the Alerts page, or use `scripts/simulate.py --user budget-forecast-dip --days 12` from `backend/` with the same exported variables. Do not advance concurrently through both interfaces.

Migration head is `d5f9a3b2c7e4`, adding nullable `alerts.signal_cents` and `signal_days`. SQLite upgrade and downgrade are tested. `dev.db` was not migrated or used for verification.

## Checks

1. Advance to Oct 10. In Cash forecast, the range falls below the $100 buffer at rent on Oct 13. The Landlord marker and daily table identify the scheduled $1,500 bill before payday on Oct 15. A low-balance alert already exists and names Landlord. `GET /forecast` returns the simulated date, ordered low/expected/high cents and the bill event.
2. Change Keep at least $ to 250 and Save. The card refetches and uses $250; the earlier alert remains pending. The API rejects negative, over-$10,000, boolean, string and floating-point cents values. No account or alert is changed by GET.
3. Advance through payday. The same warning does not repeat daily. **The approved repeat policy allows growth and urgency reminders:** in the scripted October replay the initial Landlord warning is Oct 1 ($28.54 shortfall, 12 days ahead), a qualifying deeper warning is Oct 3 ($84.73, 10 days), and one urgency reminder is Oct 11 ($62.67, two days). No more warning is created through Oct 16. These are intentional policy repeats, with the same stable bill key. Dismissal does not suppress a qualifying later change.
4. Continue into the next month's rent window. November's dip uses its own date key and one initial warning; the same growth/urgency policy applies to it. Bill attribution stays stable when the band crossing slides by a day. A bill within five days remains the cause until a paycheck intervenes, even just after it posts, without counting the charge again.
5. A user with no paycheck pattern sees `No paycheck found, so no income is assumed.` The graph may still warn but only with action `alert`, never a savings-move or spending-delay suggestion. A user with no checking account or under 28 history days sees an explained empty path.
6. On the goals-card-24 profile, generate goals, accept one, and generate again. The run reports `surplus_source: forecast`; the cap applies max_share to gross surplus and subtracts commitments once. The UI says `Based on your cash-flow forecast.` Unsupported history uses the measured source. The debt comparison hint does not change the extra input.
7. In chat ask `Will I run short before payday?`. Check the `get_cash_forecast` chip, a range in the answer, and the no-income assumption when applicable. Live selection among seven tools has not been run here.
8. Inspect the chart at 375 px: expected line, shaded typical range, dashed buffer, readable rent marker, and no page-level horizontal scroll. Expand Show as a table. **This browser viewport check remains unrun in this environment.**

## Automated evidence

Final offline backend: **1,278 passed, 22 live/sandbox tests excluded**. Frontend: **135 passed across 13 files**. Lint, TypeScript (`npx tsc --noEmit`) and `git diff --check` passed.

The HTTP tests drive the real simulated advance endpoint, inspect alerts/forecast/buffer behavior, continue through payday and the next month, and accept/regenerate a goal through the API. Existing exact-number goal regressions stay on the measured path via `force_measured_surplus`; marked tests exercise the forecast. Legacy budget profiles have static $100 balances, so `isolate_static_balance_fixtures` isolates their budget-graph cash snapshot; marked cash-forecast tests use moving balances.

Checkpoint tests inspect SQLite rows, graph values and file bytes for both paused and errored runs. Save-on-exit alone was insufficient because LangGraph also checkpoints exceptions; `BudgetCheckpoints` filters transient signal, prompt and error text at the write boundary. The summary is stored in application rows before approval. CLI report facts remain only in process memory.

`venv/bin/python eval/forecast_accuracy.py` replayed 79 dates:

| Profile | MAE d+7 | MAE d+14 | Coverage d+7 | Coverage d+14 | Flat baseline d+14 | Mean band width |
|---|---:|---:|---:|---:|---:|---:|
| forecast-dip / pooled | $7.31 | $10.07 | 68.4% | 79.7% | $587.09 | $22.96 |

The Landlord dip was named 14 days ahead. Results are under `eval/results/2026-11-02/forecast_accuracy/` and a repeat under `forecast_accuracy-2/`, intentionally uncommitted. Existing budget accuracy remains pooled 7.1% median / 17.7% mean. These are synthetic results for one forecast profile, not a general coverage guarantee.

Data model, budget loop, evaluation flow and lifecycle diagrams were rendered to SVG and PNG with PlantUML. The unrelated pre-existing README, guide and diagram edits remain unstaged; only this work's additions were committed.

## Build and live limits

`npm run build` was retried after the final UI changes. Turbopack still fails because its CSS worker cannot bind a local port (`Operation not permitted`). An earlier escalated attempt had the same result. The webpack fallback fails resolving `fonts.googleapis.com` for the existing Geist fonts. Production build is therefore **not verified**. No font or build configuration was changed to conceal the failures.

Not run: live seven-tool selection on qwen2.5:14b, browser 375 px inspection, Postgres migration, real Plaid balances/history, Sandbox recurring agreement or the external PR/Jira warning from the earlier handoff. These remain explicit verification items, not implementation gaps in the approved offline plan.

Known assumptions: overdue grace is five days; missing card due dates count in surplus but not as dated events; a goal's scheduled payment is a plan rather than contribution tracking; annual detection is deferred. The fixture is simple and exact-date-heavy. Card-payment matching, nullable balances and real bank timing still require linked-data validation. No foreign-currency conversion is implemented.

## Plan conflicts resolved during implementation

- The approved growth/urgency rule takes precedence over the later outline asking for exactly one alert through payday. Tests pin each permitted repeat and the quiet period after it.
- A crossing within five days of a bill retains that bill's identity until income intervenes. This fixes an end-to-end case where a one-day band shift otherwise created an everyday-spending key for the same trough.
- Goal scheduling projects from the stream's first date to find the first paycheck in each month; projecting only from the latest paycheck could schedule the same goal twice.
- Checkpoint filtering is enforced on every write, because an exception can exit before log_advice clears sensitive fields.

The approved implementation chain is complete. The remaining work is the environment-dependent verification listed above; no merge, push, PR edit or Jira transition was performed.
