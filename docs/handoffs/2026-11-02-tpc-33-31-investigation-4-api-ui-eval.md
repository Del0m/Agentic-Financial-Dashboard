# TPC-33 / TPC-31 investigation 4: API, UI and eval

Scope: API shapes, the buffer setting, `ForecastChart`, the Recurring card, the dashboard, the Goals page, the eval harness, planted profiles, the demo, docs and Plaid. Detector and forecast math are other concerns; I state assumptions where I depend on them. Per the coordinator's request for lighter usage I kept experiments small; skipped checks are in section 8.

## 1. Summary

- Both endpoints fit the `GET /budget` pattern: compute live from the DB, write nothing, follow `sim_today`. `GET /forecast` returns expected, low and high on every day, so it is a range per day. `GET /recurring` reads `recurring_streams` (stored by the daily run), never recomputes.
- The buffer is `PUT /budget/style`'s twin: `PUT /forecast/buffer` writing `prefs_json["forecast"]["low_balance_buffer_cents"]`, bounds $0 to $10,000. The input sits on the forecast card. A changed buffer does not touch existing alerts. The next run decides by the repeat rule.
- `recharts` 3.10.1 is already installed and exports everything `ForecastChart` needs. A `<details>` table is the text alternative.
- The biggest risk is not UI. **Synthetic users have no usable checking balance**: `seed_profile` creates a checking account with `subtype=None`, balance $100, `available_balance_cents=None`, and `advance_days` never updates the balance. Without a fix, TPC-31's forecast, the dip profile and the "advance days to see an alert" demo cannot work (section 3.1).
- Planted profiles need new pattern classes (`Every`, plus `steps` and `last` on `Monthly`) and a new profile tuple per ticket. Adding to `PLANTED` would break four existing tests. Existing profiles' transactions and accuracy numbers stay identical.
- One conflict to raise to the detector concern: the ticket's price-increase rule cannot fire on a hike over 10% (3.8).

## 2. What exists today

- `backend/app/api/budget.py:103-105` `get_budget`: live, writes nothing, `sim_today(s, user.id) or _today()`, opens one session via `budget_hooks.session_factory`. `:157` `put_style`: validate via `set_user_style`, 422 on `ValueError`, commit, return effective date.
- `backend/app/budget_service/settings.py:12,19`: `user_style` / `set_user_style` store in `prefs_json["budget"]`, replacing the dict whole (new dict so the JSON column notices).
- `backend/app/budget_service/daily.py` `sim_today`: `prefs_json["sim"]["date"]`.
- `backend/app/main.py:34`: routers are included by hand; `models.py:167` says `Alert.stream_id` "arrives with TPC-33". `Alert.kind` exists; `stream_id` does not.
- `backend/app/agents/budget_graph.py:81-100,189-218`: repeat rule is memory-based: `_this_month_alerts` (kind `overspend`, same period and category) feeds `decide_action`, and code refuses a `no_op` unless the user was already told at the same or higher severity (`escalated`). `persist_state` writes `summary_json` (`:256`).
- `backend/app/agents/budget_graph.py:277`: `after_load` sends `no_budget` / `period_ended` straight to `persist_state`.
- `frontend/components/BalanceChart.tsx`: `recharts` `BarChart` in a `ResponsiveContainer`, 40 px per row. `frontend/package.json`: `recharts ^3.10.1` (installed 3.10.1), vitest, jsdom.
- `frontend/app/dashboard/page.tsx`: `Card` helper (`:20`), one `useEffect` fires `getBudget`, `getGoals`, `getAlerts`, `getTransactions` (`:55-84`), each with its own error state. `DebtPlanCompare` fetches inside itself (mocked in the test, `page.test.tsx:19`).
- `frontend/lib/api.ts`: typed `request<T>`, `errorMessage`. Goals: `GoalRunOut` at `backend/app/api/goals.py:61`; the run summary stores `surplus_cents` and `cap_cents` (`goal_graph.py:278-290`) but neither reaches `GoalRunOut` nor the page. The page only maps `outcome` to text (`goals/page.tsx:21-56`).
- Eval: `eval/run_eval.py` is the chat eval (needs Ollama, `preflight` exit 2). `eval/budget_accuracy.py` is the model-free pattern to copy: `run(profiles, out_root, day)` seeds a `temp_db()` per profile (`eval/fixtures.py:167`), prints, writes `eval/results/<date>/budget_accuracy/results.json` via `results_dir`/`_write`. `eval/metrics.py:mape`. Tests: `backend/tests/test_budget_accuracy.py` checks structure and a few exact values, never a global threshold.
- Planted users: `eval/budget_users.py` `Profile`, `Weekly`/`Monthly`/`Once`, `transactions()` (`:65`, final `else` assumes `Weekly`), `seed_profile` (`:91`), tuples `PROFILES`, `PLANTED`, `GOAL_USERS`.
- Plaid: `backend/app/plaid_service/client.py:50` (link token) and `:65` (`sandbox_public_token`) both say `days_requested: 90`. Architecture says 180 (`architecture.md:270,1019`), so the code is behind the docs. No wrapper for `/transactions/recurring/get` exists; `Transaction.is_recurring` (`models.py:94`) is never set.

## 3. Findings

### 3.1 Synthetic users have no usable checking balance (blocks TPC-31)
Evidence: `eval/budget_users.py:94` `add_account(session, item, "Checking")`; `tests/factories.py:20-28` sets `type_="depository"`, `subtype=None`, `current_balance_cents=10000`, `available_balance_cents` unset. `simulate.py:44` `_inject` adds `Transaction` rows only. The forecast starts from "checking" accounts' available balance falling back to current, so a synthetic user would start at $100 and never move.
Fix (needed by both the eval and the demo): add `Profile.opening_cents: Optional[int] = None` (balance on `start`). When set, `seed_profile` creates the checking account with `subtype="checking"` and balance = `opening - sum(amounts <= until)`, setting `available_balance_cents` equal. `_inject` then subtracts each injected amount from the same account (Plaid sign: positive = out) and `profile.opening_cents is None` keeps today's behaviour for every existing profile. Dependency on `sim` profile lookup: `simulate.py:83` and `seed_budget_user.py:66,94` list the tuples explicitly and must include the new tuples.

### 3.2 Planted dip profile (prototype, throwaway)
Method: `apiui/proto_users.py` adds `Monthly2` (with `last`, `steps`), `Every(days, first)` and a `balance_on`. Profile: rent $1,500 on the 13th, pay $2,200 on the 15th, Netflix $15.49, gym $40, weekly groceries $110 and fuel $40 (jittered), 2026-06-01 to 2026-11-30, opening balance 0.

| Month | Balance 12th | After rent (13th) | Payday (15th) |
|---|---|---|---|
| Jun | -$235.82 | -$1,837.29 | $319.08 |
| Jul | -$280.80 | -$1,823.49 | $376.51 |
| Aug | -$281.26 | -$1,781.26 | $305.17 |
| Sep | -$343.20 | -$1,843.20 | $319.25 |
| Oct | -$331.24 | -$1,831.24 | $368.76 |
| Nov | -$272.96 | -$1,772.96 | $307.03 |

Reading: net drift is about zero, the trough is always the 13th (2 days before payday) and stable within about $120. Setting `opening_cents = 190000` puts the trough at about +$70 in October and about -$3 in November, below the $100 buffer every month. A run on Oct 10 is 3 days before rent; a lookahead of 14 days sees it from Sep 29. History 4 months is plenty for the forecast's 90-day spending window. (The expected value of one dip per month also tests "one alert per dip".) Old `transactions()` given an unknown pattern class raises `AttributeError: ... has no attribute 'weekday'` (final `else`), so new classes need explicit branches.

### 3.3 Existing numbers that must not change
Run today (`baseline.py`), accuracy eval over `PROFILES`: steady 5.8/4.0, vacation 9.4/58.8, early-rent 5.8/4.6, rising 9.1/7.7, occasional 4.8/3.0 (median/mean MAPE), pooled 7.1 / 17.7, 0.2 s. Transaction-list hashes (sha256 of `repr(transactions(p))`, first 12 hex): steady c46034002c6f, vacation 1f32f65b528d, early-rent f54a9cbf30f1, rising dfb7a42a181d, occasional 5cffd523cfcd, overspend-one-time 50d7cc005186, overspend-recurring 7ebed8d37d7c, overspend-seasonal a9e3ec12934c, overspend-known-merchant 056ac49b1a45, goals-card-24 2bcfb8ca8436. A plan test can pin these.

### 3.4 What guards the tuples
`test_budget_users.py`: `:56,115` assert `PROFILES` ids exactly; `:90` loops all `PLANTED` and demands exactly one pace flag; `:101` asserts the exact `PLANTED` id/run_date/cause dict; `:202` asserts goal user absent. So: new profiles go in new tuples (`RECURRING_USERS`, `FORECAST_USERS`), as `GOAL_USERS` did, never in `PLANTED` or `PROFILES`.

### 3.5 Chart
`recharts` exports `ComposedChart`, `Area`, `Line`, `ReferenceLine`, `ReferenceDot`, `ReferenceArea`, `Tooltip`, `ResponsiveContainer`. `accessibilityLayer` is a documented chart prop in the installed types (`util/types.d.ts`). The Next docs guide `01-app/02-guides/lazy-loading.md` covers `next/dynamic` for client-only components; it is optional here since `BalanceChart` is imported directly and `"use client"` is already used. I did not render it (see 8).

### 3.6 Dashboard tests
`page.test.tsx` mocks `@/lib/api` with `importOriginal` spread, so an unmocked `getRecurring`/`getForecast` calls real `fetch("/api/...")` (relative URL fails in jsdom) and the card shows its error text. Assertions I read use `findByRole("heading", {name:"Goals"})` and card-scoped queries, so they probably still pass, but unmocked calls make them noisy and nondeterministic. Not run (see 8).

## 4. Recommended design

### 4.1 `GET /recurring` (`backend/app/api/recurring.py`, `router = APIRouter(tags=["recurring"])`, register in `main.py`)
Reads `recurring_streams` for the user, stored by the last daily run. No recompute, no write. Response `RecurringOut`:
```
{ "as_of": "2026-10-10"|null,           // date of the run that stored them; null = no run yet
  "streams": [ { "id": int, "direction": "out"|"in", "label": str,   // merchant for out; category label for in ("Income")
                 "frequency": "weekly"|"biweekly"|"semimonthly"|"monthly"|"yearly",
                 "amount_kind": "fixed"|"varies", "typical_cents": int, "last_cents": int,
                 "last_date": date, "next_date": date|null, "status": "active"|"stale",
                 "change": null|"new"|"price_increase"|"income_change",
                 "previous_cents": int|null, "first_seen": date } ] }
```
`label` for inflows must be the category label only (matches the privacy rule in 6a.1; the API is the user's own data so it could show the merchant, but the card and chat tool should not differ, so omit it). Order: changed streams first, then active by `next_date`, stale last. 200 with `streams: []` for no history; no 404s. `change` is set when the stream has an unresolved alert from the last 30 days (via `alerts.stream_id`). Auth via `get_current_user` (401 as elsewhere). Why stored: the detector needs the previous run to flag changes, and the chat tool must show what the alerts were based on.

### 4.2 `GET /forecast?days=` (`backend/app/api/forecast.py`)
Computed live (as `GET /budget`), writes nothing, so it is current right after a sync. Today = `sim_today(s, user.id) or _today()`. `days`: `Query(default=None, ge=1, le=90)`; default = the engine's horizon (through next paycheck, min 30). Response `ForecastOut`:
```
{ "today": date, "status": "ok"|"no_checking"|"no_history",
  "start_cents": int|null, "buffer_cents": int, "horizon_days": int,
  "income": "paycheck"|"none",           // "none": no inflow stream; the forecast assumes no income
  "days": [ {"date","expected_cents","low_cents","high_cents"} ... ],   // today+1 .. horizon
  "events": [ {"date","label","cents","kind":"bill"|"card_min"|"income"|"goal"} ],
  "dip": null | {"date","shortfall_cents","bill_label"},                // low path below buffer within 14 days
  "monthly_surplus_cents": int|null, "surplus_source": "forecast"|"measured"|null,
  "notes": [str] }                       // code-written, e.g. "No paycheck found, so no income is assumed."
```
Range-only: every day carries `low <= expected <= high`; the UI headlines "between $X and $Y on <date>", never the expected value alone. `monthly_surplus_cents` is the one single number (the Goal Agent needs it); show it only as "about $X a month" on the Goals page and in the chart tooltip, not as a headline. Error cases return 200 with a `status` so the card can explain: `no_checking` (no depository checking account with a balance; `start_cents: null`, `days: []`), `no_history` (fewer than the engine's minimum days of transactions; same). No paycheck stream: `status: "ok"`, `income: "none"`, the note says so, `monthly_surplus_cents: null`. Real failures (DB error) are 500 and stay inside the card. `surplus` and `dip` come from the same engine call as the daily run, so the card and the alert agree (one `forecast.py` function, one loader `budget_service/forecast.load_inputs`).
Open dependency: `days[]` content depends on the forecast-math concern; the shape above is independent of it.

### 4.3 Buffer endpoint
`PUT /forecast/buffer` body `{ "buffer_cents": int }` -> `{ "buffer_cents": int, "default_cents": 10000 }`. Stored at `prefs_json["forecast"]["low_balance_buffer_cents"]` (nested, like `budget`; the ticket's `prefs_json.low_balance_buffer_cents` is flat, which is a minor departure that avoids clashing with `goals`/`budget` namespaces; the loader may also read a flat key if one exists). Put it in `budget_service/forecast_prefs.py` with `user_buffer(session, user_id) -> int` and `set_user_buffer(session, user_id, cents)`, copying `settings.py` (validate, replace the dict whole, caller commits). Validation: `isinstance(int)` and not bool, `0 <= cents <= 1_000_000` ($0 to $10,000); otherwise 422 "The buffer is $0 to $10,000." Bounds reason: $0 is a legitimate "warn only when I'd overdraw"; above $10,000 exceeds most checking balances and would alert forever. `GET /forecast` also returns `buffer_cents` so the card needs one fetch. UI: a number input under the chart ("Keep at least $[100]") with a Save button; on success refetch the forecast. Existing alerts: unchanged; a pending alert stays pending and the next run applies the repeat rule against the new buffer (a dip that no longer breaches simply produces no new alert; a deeper breach may re-alert under "materially larger"). Do not auto-dismiss: the user's outcome is data.

### 4.4 `ForecastChart` (`frontend/components/ForecastChart.tsx`)
`ComposedChart` data `[{date, expected, band:[low, high]}]` in dollars: `Area dataKey="band"` (range area) for low/high, `Line dataKey="expected"`, `ReferenceLine y={buffer}` dashed with a label, one `ReferenceDot` or vertical `ReferenceLine` per event with `kind` bill/card_min (labelled on tap/hover only; at 375 px show at most the dip's bill label and put the rest in the table). `ResponsiveContainer width="100%" height={240}` (card width at 375 px is about 343 px minus padding, so no horizontal scroll; use `minTickGap`/`interval="preserveStartEnd"` on the XAxis and a `$k` tick format). Keep the chart `accessibilityLayer` on and wrap it in `role="img"` with an `aria-label` generated from the data: "Cash forecast, <start> to <end>. Lowest likely balance $X on <date>; buffer $Y." Text alternative: a `<details><summary>Show as a table</summary>` with a table (`Date | Low | Expected | High | Events`), which is also what the component test asserts since jsdom gives `ResponsiveContainer` zero size (the dashboard test mocks charts for that reason, `page.test.tsx:17`). Colors: reuse `ASSET_COLOR`/`DEBT_COLOR` style constants; band at 20% opacity.

### 4.5 Recurring card (`frontend/components/RecurringList.tsx`)
Rows: label, frequency ("Monthly"), amount (`typical_cents`, "about" prefix when `varies`), next date or "Stopped" when stale. A changed stream carries a badge ("New", "Price up $40.00 to $43.00", "Income down") taken from `change`/`previous_cents`. Active streams first (sorted as the API), stale ones in a muted "No longer seen" group. Inflow streams show "Income" plus the frequency and amount (no employer). Empty list: "No repeating charges found yet." Too little history (`as_of` null or fewer than 3 months of data, per the detector): "We need about 3 months of transactions to spot repeating charges." Actions: none on the card. Accepting or dismissing (`suggest_cancel`, `price_increase`) stays on Alerts, which already owns pending decisions; a badge links to `/alerts` when its alert is pending.

### 4.6 Dashboard (`frontend/app/dashboard/page.tsx`)
Add two `Card`s between Goals and Transactions: "Cash forecast" and "Recurring charges". Fetches: 2 more (`getForecast()`, `getRecurring()`) in the same effect with their own `forecast`/`forecastError` and `recurring`/`recurringError` state, copying the goals pattern (`cancelled` flag, `errorMessage`). Page goes from 4 to 6 parallel calls (DebtPlanCompare makes a 7th). Failures stay in the card ("Couldn't load your forecast: ...") and do not set the page `error`. Buffer save calls `load()` or a local refetch. Tests to change in `page.test.tsx`: add `getForecast` and `getRecurring` to the `vi.mock` list and `beforeEach` (resolved defaults: empty recurring, `status: "ok"` forecast), mock `@/components/ForecastChart` as `() => null`, and add: forecast failure stays in its card; recurring failure stays in its card; empty-state text; both cards present. No existing assertion should need to change (3.6, unverified).

### 4.7 Goals page and run response
Yes, report the source. Add `surplus_source: "forecast"|"measured"` (and `surplus_cents`, `cap_cents`) to the goals run `summary_json` (`goal_graph.py:_summary`) and to `GoalRunOut` (`Optional[str] = None`); `frontend/lib/api.ts` `GoalRun` gets the same. The page shows one sentence after a run: "Based on your cash-flow forecast." or "Based on your last 3 months (no paycheck pattern found yet)." Also update the `no_surplus` copy, which says "in a typical month" (`goals/page.tsx:46`), to fit both sources. `GET /goals` needs no change. The facts sent to the model should include the source in words only if the goal concern wants it.

### 4.8 Eval: `eval/forecast_accuracy.py` and `eval/recurring_accuracy.py`
Model-free, same shape as `budget_accuracy.py`: `run(profiles, out_root, day) -> Path`, per profile a `temp_db()`, prints a table, writes `eval/results/<date>/forecast_accuracy/results.json`, `main()` returns 0. Offline: yes, no LLM (the engines are pure; the daily run's model only explains). Run: `venv/bin/python eval/forecast_accuracy.py`.
- Replay: for each `FORECAST_USERS` profile and each replay day `d` from `start + 90 days` to `end - 14`, seed with `seed_profile(s, profile, until=d)` (balance set per 3.1), call the service forecast, compare `expected_cents` at `d+7`, `d+14` with `balance_on(profile, d+h)` computed from the profile's transactions (the profile is the ground truth).
- Metrics per profile and pooled: `mae_d7_cents`, `mae_d14_cents` (mean absolute error of expected), `coverage_d7`, `coverage_d14` (share where actual is in [low, high]), `mean_band_width_cents`, `n`, and the dip check: `dip_lead_days` (days between the first replay day whose forecast has `dip`, and the dip date) with a pass flag `lead >= 3` and `dip.bill_label == "Landlord"`.
- Add a naive baseline beside it ("balance stays flat"), reported as MAE d+7/d+14, so a number means something. Band coverage is reported with its width; do not threshold at 50% (architecture `10.2` says "≈ 50%"; per-day percentile bands summed over 7 days are not a 50% interval, so coverage depends on the math concern).
- `recurring_accuracy.py`: for `RECURRING_USERS` run the detector on the data up to a day and compare with `planted_streams(profile, as_of)` derived from the patterns (`Monthly`, `Every`, `Weekly` with at least 3 occurrences in 180 days; match on normalized merchant + frequency). Report precision, recall, the false positives and misses by name. Ground truth must use distinct merchant names per pattern (the base profiles' two `Diner` weekly patterns would be ambiguous), so existing `PROFILES` are not scored.
- Thresholds vs reports: tests assert structure and planted facts (dip found >= 3 days ahead naming Landlord; every planted stream detected; the 2-visit merchant absent; MAE is finite, `0 <= coverage <= 1`; forecast MAE does not exceed the flat baseline on the dip profile), not a fixed MAE. Absolute MAE and coverage are reported, like MAPE today.
- Output example (shape): `profile  n  MAE d+7  MAE d+14  cov d+7  cov d+14  band  baseline d+7` then `pooled`.

### 4.9 Planted profiles
In `eval/budget_users.py`: add `Every(category, merchant, cents, days, first, last=None, steps=())`; extend `Monthly` with `last: Optional[date] = None` and `steps: tuple = ()` (defaulted, so existing profiles generate identical lists; pin with the hashes in 3.3); add `Profile.opening_cents`. `transactions()` gets explicit branches for each class and a `TypeError` for unknown classes instead of the silent `Weekly` fallthrough. `Monthly.moved` already handles day-shifted bills. Weekly and price changes: `Weekly` already exists; a price change is `steps`.
- `RECURRING_USERS`: `recurring-planted`: Netflix-like $15.49 monthly (day 5); gym $40 -> $43 from Oct 3 (+7.5%, the only size the literal 5%-and-10% rule can flag); a second subscription $15.49 -> $17.99 (+16%) to document the departure; biweekly pay $1,850 `Every(14)`; rent monthly; a merchant with exactly 2 visits (`Once` x2, 30 days apart); weekly fuel. Start 2026-05-01 so the gym has 5 charges at $40 before the rise. History length: monthly needs 3 charges (about 60 days), biweekly 3 (28 days), price-increase needs 3 at the old price then 1 higher.
- `FORECAST_USERS`: `forecast-dip` per 3.2 with `opening_cents=190000`, `run_date=2026-10-10`.
- Registries to update: `simulate.py:83`, `seed_budget_user.py:33,66,94` (lookup and the "run it" hint), and `test_goal_user_not_in_the_accuracy_eval`-style tests that new users are absent from `PROFILES`/`PLANTED`.

### 4.10 Demo and manual verification
`test_m3_demo.py` (existing): seeds `overspend-recurring` until `default_start`, `advance_days(31)` with `SchemaReplyModel`, finds the first flagged day. A second test `test_m3_forecast_demo` in the same style: seed `forecast-dip` until Sep 30, `advance_days` to Oct 12 with a fake model, assert an alert with `kind="low_balance"` exists, created on a day <= Oct 10, whose message or `category` names "Landlord". By hand (`scripts/simulate.py --user budget-forecast-dip --days 12`, or the Alerts page's simulation panel, `api.ts:297-310`): the dashboard forecast card shows the band dipping under the buffer line at the rent marker; after the day the dip enters the 14-day window an alert appears; raise the buffer and save, the card redraws; advance past payday, no new alert for the same dip. Checks per plan: TPC-33: planted streams listed on the Recurring card, gym badge after the higher charge, 2-visit merchant absent, empty state for a fresh user, sync without 180 days gives "need about 3 months". TPC-31: card range, buffer change, no-paycheck note, dip alert text, Goals page source sentence. Per memory, write `docs/handoffs/` verification docs at the end.

### 4.11 Docs to update (each plan)
- TPC-33: `architecture.md` 6a.1 (as built), 6a.4 (`detect_recurring`), section 9 (`GET /recurring` line already exists; add shape), 5 data model (`recurring_streams`, `alerts.stream_id`), 10 (precision/recall as built), 16 decision log; `docs/system-walkthrough.md:29-30,68` (status row "Planned" and the fixed-line note); `README.md` (API list, a Recurring section, seed commands); `docs/diagrams/data-model.puml`, `budget-agent-loop.puml`, and `remaining-lifecycle.puml` (its recurring/forecast boxes are labelled PLANNED at lines 11-12; the diagram is stale as soon as TPC-33 lands). `docs/tests/<file>.md` for every new test file (one doc per test file, in the format of `docs/tests/dashboard-page.test.md`: header, run command, shared setup, one section per test), plus `dashboard-page.test.md` and `test_budget_users.md`, `test_m3_demo.md`, `test_simulate*.md` for changed ones.
- TPC-31: `architecture.md` 6a.2, 6a.5 (the "known limit" at `:125` is resolved), 9, 10.2, 16; walkthrough line 29; README forecast section and buffer; `budget-agent-loop.puml`; `evaluation-flow.puml`; `docs/tests/` as above plus `goals-page.test.md`, `test_goal_graph.md` (source recorded).

### 4.12 Plaid
`days_requested` is set at `client.py:50` (`create_link_token`) and `:65` (`sandbox_public_token`); both say 90 and both need 180. It is fixed when the Item is created (comment `:49`), so already-linked Items stay at 90 until relinked; the plan should say so. The ticket's Sandbox comparison needs, later and by hand: Sandbox credentials in `.env`, a Sandbox Item created after the change (`sandbox_link.py` uses `sandbox_public_token`), a new thin client function calling `plaid_post("/transactions/recurring/get", {"access_token", "account_ids"})` (it does not exist), the Item's `transactions` product already enabled, and a script that matches Plaid's streams to ours on normalized merchant + frequency and prints agreement. Whether Plaid's recurring endpoint needs extra product enablement or minimum history is not verified.

## 5. Departures from the ticket
- **Buffer key nested** (`prefs_json["forecast"]["low_balance_buffer_cents"]`), not flat. Cosmetic; the loader can read both.
- **`GET /forecast` returns `monthly_surplus_cents` as one number** while 6a.2 says "never a single number". Recommendation: keep it for the Goal Agent but never headline it.
- **Price-increase rule cannot fire above +10%** (cross-concern): 6a.1 marks a stream `fixed` only if every amount is within 10% of the median, and `price_increase` is defined on fixed streams. A $40 -> $46 gym (+15%) puts the new charge outside the band, so the stream becomes `amount_varies` and no alert fires. Real hikes (streaming +16%) are usually larger. For the detector concern to settle; I plant both sizes.

## 6. Decisions for the user
1. **First run after history is loaded**: every stream is "new". Recommend a baseline run that stores streams without alerts (cost if wrong: dozens of `new_subscription` alerts on first sync). Not my call to specify the graph.
2. **Simulated balance** (3.1): update the checking balance in `_inject`. Alternative: no balance in sim (forecast unusable in demos). Recommend the update; cost if wrong: demos lie about money.
3. **Forecast needs a budget?** `after_load` ends the run at `persist_state` for `no_budget`/`period_ended`, so a user without a budget would get no forecast alerts if the new nodes sit after `update_totals`. Recommend running `detect_recurring`/`forecast_cash` regardless; cost: a new user gets no warning until a month is fitted.

## 7. Test ideas
- API: `/recurring` and `/forecast` for no checking account, empty history, no paycheck stream, simulated user date, another user's data not visible; `days=0` and `days=91` -> 422; `PUT /forecast/buffer` with -1, 1_000_001, `True`, `"100"`, `100.5` -> 422; success persists and `GET /forecast` echoes it; a pending alert survives a buffer change.
- Both GETs write nothing (row counts before/after, like `GET /budget`).
- Frontend: card failure isolation; empty and too-little-history texts; chart table lists every day and the dip bill; stale streams grouped; buffer input validation message.
- Eval: dip lead >= 3 and names Landlord; flat-baseline comparison; profile hashes in 3.3 unchanged; budget accuracy numbers in 3.3 unchanged; unknown pattern class raises `TypeError`.

## 8. Unverified
Not run: any frontend test (so 3.6 is by reading), a rendered `ForecastChart` or the 375 px layout, `accessibilityLayer` behaviour, the Next docs beyond the lazy-loading index, the sim/demo flow end to end, the dip profile through a real forecast (no engine exists yet, so MAE, coverage and the flat baseline numbers are not measured), whether `days_requested` 180 changes what Plaid's Sandbox returns, Plaid's recurring endpoint requirements, and `docs/system-walkthrough.md`/README sections beyond the lines cited. Experiment files: `/private/tmp/claude-501/-Users-armindelmo-Documents-College-Fall26-COP6938-Plaid-Agent-CAP6938/67a20a0f-f748-42f9-8d6c-4dd81dce6637/scratchpad/tpc31/apiui/`.
