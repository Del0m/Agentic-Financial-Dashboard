# TPC-22: Period close, budget styles and category corrections

Jira: TPC-22 (epic TPC-4, M3). Branch: `TPC-22`, cut from `TPC-21`.

## Goal

Close each month and open the next one, adapted, and give the budget a shape the user can bend:
- **Period close.** A LangGraph close graph (no LLM) compares each line's limit with the month's actuals, applies the `adjust_next_budget` proposals the user accepted, smooths the rest (the existing `close_period`), closes the old period and opens the next one, in one commit. Missed months are closed one by one.
- **Income, total and free.** A period knows the user's typical income and its planned total. A **free** line is the wiggle room left after the other lines, never below $0. A **saving** line is fitted from transfers to savings.
- **Styles.** Lines belong to groups (`needs`, `wants`, `saving`, `free`) and carry a user-editable name. The rules read groups, never names. Two styles: `caps` (default) and `50_30_20`, chosen per user (CLI and API; no UI in this ticket).
- **Category corrections.** Plaid's category applies automatically; the user can correct a transaction from a Transactions table on the dashboard and, if they choose, remember the correction for that merchant.

**Done when:** the M3 demo passes. A simulated month with a planted overspend alerts within one run of crossing 10%, with the right cause, and next month's budget adapts (`test_m3_demo.py`, deterministic with the fake model; the live `qwen2.5:14b` run is recorded in the architecture doc).

**Out of scope:**
- UI to rename or regroup lines and to switch styles (the routes exist; the UI is a follow-up ticket);
- LLM categorization (`our_category` stays as the slot for it);
- more than one saving line, or saving lines tied to specific accounts or goals (the Goal Agent);
- adding lines for new categories at close (their spending counts against free);
- reopening a closed month when a transaction in it is corrected;
- rollover/envelope and zero-based styles.

## Decisions

| Decision | Why |
|---|---|
| Close is a LangGraph graph with no LLM and no checkpointer | Matches the architecture's four graphs; every number is code's. Nothing pauses, so nothing needs resuming |
| `close_due(session_factory, user_id, today)` closes the oldest open period that ended before `today`, then loops while another has | One entry point heals a missed slot and a multi-month gap; each month is closed with its own actuals |
| `run_daily_locked` calls `close_due` before the budget graph | The scheduler, the run button and each simulated day all close first, so a missed month-end heals on the next run of any of them. Keeps "close before the 1st's run" |
| A close is one commit; the `(user_id, start_date)` unique constraint guards reruns and races | Close can never half-write or open two Novembers |
| Undecided `adjust_next_budget` proposals expire at close: alert `dismissed`, advice outcome `ignored`, paused budget run `expired`; a late decision gets the existing 409 | User's choice (A). One rule, no hidden state; memory can tell "said no" from "never answered" |
| Several accepted proposals for one category: the latest (highest alert id) wins | The user's most recent answer |
| A line's `group` decides the rules; its `name` is a label; its `category` is the internal key for what it tracks | User's requirement: user-nameable lines, adaptable to several budget types |
| Free = `max(total − other lines, 0)`; when lines exceed the total, the total rises to cover them and close writes a plain alert | User's choice: free only exists when there is room in net income; code never cuts a line the user didn't ask to cut (architecture 6.6) |
| `caps` total = `max(income, sum of lines)`; income = median of the last 3 complete months of income | User's choice (A): the total is income; saving is a budget line, not a hidden reserve |
| No income history → the total falls back to the median month of total spending + 5% | Seeded or new users without paychecks still get a total and a free line |
| `50_30_20` sets group targets from income (adjustable percentages); free is the room inside wants; a group over target gets a plain alert | User's requirement: executable without UI. Same "never cut" rule as caps |
| The saving line tracks `TRANSFER_OUT` with detailed `TRANSFER_OUT_SAVINGS`; fitted like a fixed line; never flagged for overspending | User's choice (B). Plaid already labels the transfer; other transfers stay excluded |
| A transaction's category resolves user override > merchant rule > LLM (`our_category`) > Plaid, in `load_txns` only | One place decides, so fit, pace, close and the API agree |
| Corrections offer "Always for <merchant>?"; only a click saves the rule | User's choice (A): no repeated fixes, and one unusual purchase doesn't move a merchant |
| Style changes take effect at the next init or close | A month is judged against the budget it started with |
| Existing periods aren't rewritten; their first close adds the free line (always) and a saving line (if the month had savings transfers) | No re-seed needed; demo users pick up the model from their next month |

## Files

```
backend/alembic/versions/<rev>_period_close_and_styles.py
                                               budget_lines.name, .group; budget_periods.income_cents, .total_cents;
                                               transactions.user_category; category_rules
backend/app/db/models.py                       the new columns, CategoryRule, LineGroup / BudgetStyle literals
backend/app/budget_engine/txns.py              SAVINGS / FREE keys, savings(), income_by_month(); spending() respects overrides
backend/app/budget_engine/fit.py               saving line, income, fallback total
backend/app/budget_engine/groups.py            default category → group mapping, default line names
backend/app/budget_engine/styles.py            apply_style(): caps and 50_30_20
backend/app/budget_engine/pace.py              unlined spending counts against the free line; saving lines never flagged
backend/app/budget_engine/diagnose.py          facts for the free line draw on unlined categories
backend/app/budget_engine/close.py             close_period skips free (style sets it); unchanged arithmetic
backend/app/budget_service/initialize.py       load_txns precedence; init writes groups, names, income, total, free
backend/app/budget_service/close.py            close_due, expire_proposals
backend/app/agents/close_graph.py              the graph
backend/app/agents/budget_graph.py             free/saving lines never get adjust_next_budget
backend/app/budget_service/daily.py            run_daily_locked closes first; close_month removed
backend/app/budget_service/simulate.py         drops the explicit close call
backend/app/scheduler.py                       month_end_job → close_due, non-blocking lock; catch-up scan removed
backend/app/api/budget.py                      GET /budget fields; PATCH /budget/lines/{id}; PUT /budget/style
backend/app/api/transactions.py                GET /transactions; PATCH /transactions/{id}
backend/app/main.py                            transactions router
eval/budget_users.py                           savings transfers get detailed TRANSFER_OUT_SAVINGS
eval/budget_accuracy.py                        scores only needs/wants lines
backend/scripts/seed_budget_user.py, scripts/simulate.py   --style
frontend/lib/api.ts                            the new types and calls
frontend/components/TransactionsTable.tsx      table, category button, "Always for …?" prompt
frontend/components/BudgetPaceBar.tsx          shows the line's name
frontend/app/dashboard/page.tsx                the Transactions table
```

## Data model

One Alembic revision (batch mode, as before):
- **`budget_lines.name`** (string, not null; backfilled from the category, e.g. `FOOD_AND_DRINK` → "Food and drink").
- **`budget_lines.group`** (plain string, not null; backfilled from the default mapping). Values in `LineGroup = Literal["needs", "wants", "saving", "free"]`. A plain string, like the alert columns, so a new group never needs a migration.
- **`budget_periods.income_cents`**, **`budget_periods.total_cents`** (nullable int; `null` on periods that predate this ticket, and `income_cents` is `null` when there is no income history).
- **`transactions.user_category`** (nullable string): the user's correction for one transaction.
- **`category_rules`**: `id`, `user_id` (indexed), `merchant_name`, `category`, `created_at`; unique `(user_id, merchant_name)`.
- `agent_runs.status` gains `expired` (budget runs) and `nothing_to_close` is never written (close writes no row when there is nothing to do). Plain strings already.

Reserved category keys (constants in `txns.py`): `SAVINGS = "SAVINGS_TRANSFER"` and `FREE = "FREE"`.

Default names: `SAVINGS_TRANSFER` → "Savings", `FREE` → "Free", others title-cased with `_` → space and `AND` → "and".

Default groups (`groups.py`): `needs` = `RENT_AND_UTILITIES`, `LOAN_PAYMENTS`, `TRANSPORTATION`, `MEDICAL`, `FOOD_AND_DRINK` (food is one Plaid primary, so it goes in needs; the user can move it); `wants` = every other spending category; `SAVINGS_TRANSFER` → `saving`; `FREE` → `free`.

User setting: `prefs_json["budget"] = {"style": "caps" | "50_30_20", "targets": {"needs": 50, "wants": 30, "saving": 20}}`. Missing means `caps` and the default targets. Targets must be non-negative integers summing to 100.

## Transactions and categories

**`load_txns`** resolves each transaction's category:
1. `transactions.user_category`;
2. the user's `category_rules` row for `merchant_name`;
3. `our_category`;
4. `TRANSFER_OUT` with detailed `TRANSFER_OUT_SAVINGS` → `SAVINGS_TRANSFER`;
5. `pf_category_primary`, or `UNCATEGORIZED`.

`Txn` gains `overridden: bool` (steps 1–2). **`spending()`** excludes `INCOME`, `TRANSFER_IN`, `TRANSFER_OUT` and `SAVINGS_TRANSFER` by category, and the credit-card-payment detailed category only when the transaction isn't overridden (a user who moves a payment into a line means it). **`savings(txns)`** returns the `SAVINGS_TRANSFER` transactions. **`income_by_month(txns, months)`** sums `INCOME` per month as positive cents.

Corrections pick one of the user's lines by its category key. Picking the saving line makes the transaction count as saving (and leaves spending); picking Free makes it count against the free line explicitly.

## Budget engine

**Income:** the median of `income_by_month` over the fit window (the same complete months the fit uses, up to 3). `None` when every month is $0.

**Fit (`fit_budget`)** additionally returns:
- a saving line (`kind="fixed"`, `group="saving"`), the lower median of the months with savings transfers, when there are any;
- `income_cents`;
- `spending_total_cents`: the median month of total spending × 1.05, for the fallback.

**`apply_style(style, lines, income_cents, fallback_total_cents, targets) -> StyleResult`** (pure, in `styles.py`), called by init and close after the per-line limits are known. `lines` excludes free. Returns the free limit, `total_cents`, and a list of plain alert facts.

- **`caps`:**
  - `total = max(income or fallback_total, sum(lines))`;
  - `free = total − sum(lines)` (≥ 0 by construction);
  - if `sum(lines) > (income or fallback_total)`, one `over_income` fact with the gap and the lines that changed most this period.
- **`50_30_20`** (needs income; without it, apply `caps` and add `style_fallback: "no_income"` to the summary):
  - group targets = `round_cents(income × pct / 100)`;
  - the saving line's limit is set to the saving target (created if missing);
  - `free = max(wants target − sum(wants lines), 0)`;
  - `total = max(income, sum(all lines including free))`;
  - for each of needs and wants whose lines exceed the target, one `group_over_target` fact (group, share of income, target).

**Pace (`evaluate`):** a free line's spent amount is the sum of all spending in categories without a line (plus anything corrected to `FREE`). Those categories are still reported as `unbudgeted` rows (never flagged) so the run summary can say what free covered. Lines in the `saving` group are never flagged. `BudgetLine` gains `group`.

**Diagnosis:** `overspend_facts` for the free line uses the transactions of the unlined categories. In `decide_action`, a flagged free or saving line can't get `adjust_next_budget` (downgraded to `alert`, the same mechanism as a non-recurring cause), since free is computed and saving isn't overspending.

**Close (`close_period`):** unchanged arithmetic; the caller passes needs, wants and saving lines (the saving line is `fixed`, so it follows the actual amount and keeps its limit on a $0 month). Free is never passed in; `apply_style` sets it.

## Initialization

`initialize_budget` (still only when the user has no period):
- fits as before, plus the saving line and income;
- assigns each line its default group and name;
- reads the user's style and runs `apply_style`;
- stores `income_cents`, `total_cents` and the free line (`kind="flexible"`, `group="free"`, `source="init"`, `fit_json={"rule": "remainder"}`).

## Close graph (`agents/close_graph.py`)

`close_due(session_factory, user_id, today) -> list[dict]` (in `budget_service/close.py`) runs the graph; the caller holds the user's lock. It returns one summary per month closed (empty when nothing was due).

State: `user_id`, `today`, `period_id`, `month`, `actuals`, `accepted`, `income_cents`, `adjustments`, `style_result`, `expired_alert_ids`, `new_period_id`, `run_id`, `status`, `closed` (list of summaries).

1. **`load_period`:** the oldest period with `status="open"` and `end_date < today`. None → end. (A month with no budget, or one already closed, writes nothing.)
2. **`expire_proposals`:** on that period, every `adjust_next_budget` alert with status `pending_user` → `dismissed`; its `advice_log` rows' outcome → `ignored`; budget runs reached through those rows' `run_id` with status `awaiting_user` → `expired`, `finished_at` set, `summary_json["expired_alert_ids"]` added. Committed with the close in step 5.
3. **`gather`:**
   - `load_txns` for the closed month; actuals per line category via `category_totals(spending(...))`; the free line's actual = unlined spending; the saving line's actual = `savings(...)` total;
   - accepted: alerts on the period with `action="adjust_next_budget"`, `status="accepted"`, `proposed_limit_cents` not null; latest id per category;
   - income as of the new month (the 3 complete months before it) and the spending fallback;
   - the user's style and targets.
4. **`adapt`:** `close_period` over needs, wants and saving lines (accepted values only for categories that still have a line); add a saving line if the period had none and the month had savings transfers (limit = that month's savings); `apply_style` for free, total and alert facts.
5. **`persist`** (one commit, with step 2):
   - old period `status="closed"`;
   - new period: `start_date = old.end_date + 1 day`, `end_date` = end of that month, `status="open"`, `fit_json=None`, `income_cents`, `total_cents`;
   - lines: same `category`, `kind`, `name`, `group` as the old line; `limit_cents` from the adjustment; `source="user"` for accepted, `"adapted"` otherwise (`"init"`-style evidence isn't copied); `fit_json={"old_cents", "actual_cents", "rule": "accepted" | "fixed" | "smoothed" | "remainder" | "target"}`;
   - alerts from the style facts: `kind="over_income"` or `"group_over_target"`, `action="alert"`, `severity="medium"`, `status="pending_user"`, `period_id` = the **new** period, a message built by code (amounts in dollars);
   - an `agent_runs` row: `graph="close"`, `status="ok"`, `summary_json = {"month": "YYYY-MM-01", "new_period_id", "lines": [{category, name, group, old_cents, actual_cents, new_cents, rule}], "expired_alert_ids", "income_cents", "total_cents", "free_cents", "style", "alert_ids", "unbudgeted": [categories counted into free]}`.
   
   An `IntegrityError` on the new period (another close won) → rollback; the graph ends without writing a run row.
6. **`route`:** back to `load_period` (another month may be due); otherwise end.

**Failure:** any other exception rolls the month back, writes `agent_runs(graph="close", status="error", summary_json={"month", "error": <exception type>})` in a fresh session, and ends. The period stays open; the next daily run retries.

## Wiring

- **`run_daily_locked`:** `close_due(session_factory, user_id, today)` first (exceptions are already recorded by the graph and logged here; the budget run continues and ends `period_ended` if the close failed), then the budget graph as before. Its return value gains `close_run_ids`.
- **`month_end_job`:** for each eligible real user, `user_lock(user_id).acquire(blocking=False)`; busy → skip (the 06:00 run closes). Otherwise `close_due(today)`. Returns `{"closed": [...], "skipped": [...], "failed": [...]}`. The `skip_done` scan of `agent_runs` summaries is removed (`close_due` is idempotent); the startup catch-up keeps calling the job on the 1st.
- **`advance_days`:** the explicit close on the 1st is removed; `run_daily_locked` does it. `close_month` is deleted.
- **Budget graph:** `period_ended` routing stays as the safety net.

## API

- **`GET /budget`** adds `income_cents`, `total_cents`, `style`, and per line `id`, `name`, `group`.
- **`PATCH /budget/lines/{id}`** `{name?, group?}`: only on the user's open period (404 otherwise). `name` 1–40 characters after trimming. `group` may move a line only between `needs` and `wants` (422 otherwise; saving and free can be renamed, not regrouped). Close carries the change forward.
- **`PUT /budget/style`** `{style, targets?}`: stores the setting; 422 on an unknown style or targets that don't sum to 100. Response: `{style, targets, effective_from: "YYYY-MM-01"}` (the next month).
- **`GET /transactions?month=YYYY-MM`** (default: the user's current or simulated month): rows `{id, date, merchant, amount_cents, category, line_name, source: "plaid" | "rule" | "user"}`, newest first, removed excluded (the same rows `load_txns` reads). `line_name` is `null` when the category has no line in that month's period (it counts against free).
- **`PATCH /transactions/{id}`** `{category, remember: bool}`: `category` must be a line category in the user's open period, or `FREE` (422 otherwise). Sets `user_category`. With `remember`, upserts the `category_rules` row for the transaction's merchant (422 if it has no merchant). Returns the updated row.

## Frontend

- **`TransactionsTable`** on the dashboard: date, merchant, amount, and a category button showing `line_name` (or "Free"). The button opens a list of the user's line names. After a pick, the row updates and an inline "Always for <merchant>?" button appears; clicking it repeats the PATCH with `remember: true`. Rows from a rule show a small "rule" marker.
- **`BudgetPaceBar`** shows the line's `name`.
- The Alerts page renders the two new kinds through the existing message display (checked by a test).

## CLIs

- `seed_budget_user.py --style caps|50_30_20` (default `caps`) writes `prefs_json["budget"]` before the budget is initialized.
- `scripts/simulate.py --style …` sets it on an existing simulated user (takes effect at the next close).

## Tests

Pure engine (no database):
- `test_budget_txns.py`: precedence is the loader's job, but `spending()` keeps an overridden card payment and drops `SAVINGS_TRANSFER`; `savings()`; `income_by_month()`.
- `test_budget_fit.py`: saving line (lower median of months with transfers; none without); income median; no income → `None`; spending fallback total.
- `test_budget_styles.py` (new):
  - caps: free = income − lines; free bottoms out at $0 and the total rises, with an `over_income` fact; fallback total without income;
  - 50/30/20: targets from income, the saving line set to its target, free = room in wants, `group_over_target` for needs and wants, custom targets, fallback to caps without income.
- `test_budget_pace.py`: unlined spending counts against free (and is still listed as unbudgeted); no free line → behavior unchanged; saving lines never flagged.
- `test_budget_diagnose.py`: facts for the free line come from unlined categories.
- `test_budget_close.py`: existing tests unchanged.

Close (database, `tests/factories.py`), `test_close.py` (new):
- a normal close: old period closed, new period with adapted lines, names and groups carried, `income_cents`, `total_cents`, free line, run summary;
- an accepted proposal applied as-is (`source="user"`), the latest of two winning;
- a pending proposal expired: alert `dismissed`, advice `ignored`, paused run `expired`; `decide_alert` then raises `AlertAlreadyDecided`;
- no open period that ended → nothing written;
- a rerun → nothing written; a lost race (period inserted between check and commit) → rollback, no run row;
- two missed months → two periods, two runs, each from its own actuals;
- a failure in `gather` → rollback, `error` run, period still open;
- a period from before this ticket (no free, no saving) gains a free line, and a saving line when the month had transfers;
- `50_30_20` user: targets applied, `group_over_target` alert on the new period;
- lines over income → `over_income` alert, free $0.

Wiring:
- `test_daily.py`: `run_daily_locked` closes before the budget run; a failed close still runs the budget graph (ends `period_ended`).
- `test_scheduler.py`: `month_end_job` closes, skips a locked user, reports failures; the catch-up still runs it on the 1st.
- `test_simulate.py`: advancing across the 1st closes the month before that day's run.

API and frontend:
- `test_budget_api.py`: the new `GET /budget` fields; `PATCH /budget/lines/{id}` (rename, needs↔wants, 422 for saving/free regroup, 404 for a closed period's line); `PUT /budget/style`.
- `test_transactions_api.py` (new): list with `source`; a correction; `remember` creates and updates a rule; precedence user > rule > Plaid; a category that isn't a line → 422.
- `TransactionsTable.test.tsx`, `BudgetPaceBar.test.tsx`, `AlertCard.test.tsx`: the button, the "Always for …?" prompt, names, the new alert kinds.

**The done-when, `test_m3_demo.py`:** seed `overspend-recurring` from its default start (2026-09-30) and advance day by day through 2026-11-01 with the schema-aware fake model. Assert:
- the first `PERSONAL_CARE` alert's date is no later than one day after the first day its pace reaches 1.10 (or it goes over its limit), with `cause="recurring"`;
- accepting its `adjust_next_budget` proposal and advancing to Nov 1 opens November with `PERSONAL_CARE` at exactly `proposed_limit_cents` (`source="user"`);
- the other flexible lines move by at most 15%, the fixed lines equal October's actuals, and the free line equals the total minus the other lines.

The eval (`budget_accuracy.py`) scores only `needs` and `wants` lines, so its numbers stay comparable.

## Docs

- `docs/architecture.md`: data model (section 5), 6.3 (saving line, income, total), 6.4 (free in pace), 6.5 (close as built), 6.6 (expiry), the scheduler notes, the API inventory, the live M3 demo run, and decision-log rows for the decisions above.
- `docs/tests/test_close.md`, `docs/tests/test_budget_styles.md`, `docs/tests/test_transactions_api.md`, in the style of the existing ones.
- `README.md` and `.env.example`: the `--style` flags and the new routes.

## Risks

- **Scope against the sprint (ends 2026-11-02).** The plan orders work so the M3 demo path (model, close, wiring, demo test) lands before corrections and the frontend.
- **Food in `needs`.** Plaid's `FOOD_AND_DRINK` mixes groceries and dining, which 50/30/20 splits. The user can move the line; a per-detailed-category split is a follow-up.
- **The free line's pace.** Free covers lumpy one-off spending, so it may flag more than category lines. A $0 free line (no room in income) flags `over_limit` on the first unlined purchase; that is the honest answer, and the decide prompt's memory keeps it to one alert per escalation. Thresholds are tuned in M5 with the rest.
- **Backfilled groups** on existing lines follow the default mapping, which may differ from what a user would choose; the line routes fix it.
- **SQLite batch migrations** rebuild `budget_lines` and `transactions`; the revision is tested up and down on a copy of the dev database.
