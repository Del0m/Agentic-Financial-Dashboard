# TPC-22 Period Close, Budget Styles and Category Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close each month into an adapted next month (accepted proposals, smoothing, missed months), on a budget model with income, a total, a free line and a saving line, grouped lines the user can name, `caps` and `50_30_20` styles, and per-transaction category corrections.

**Architecture:**
- **`budget_engine/` (pure):** `txns.py` (reserved keys, savings, income), `groups.py` (default groups and names), `fit.py` (saving line, income, fallback total), `styles.py` (`apply_style`), `pace.py` / `diagnose.py` (the free line).
- **`budget_service/`:** `initialize.py` (category precedence, the full model at init), `settings.py` (the user's style), `close.py` (`close_due`, expiry, the close transaction), `daily.py` (closes before each run).
- **`agents/close_graph.py`:** `load_period → expire_proposals → gather → adapt → persist → route`, no LLM.
- **API:** `api/budget.py` (fields, line edits, style), new `api/transactions.py`. **Frontend:** `TransactionsTable`, names on `BudgetPaceBar`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.1, Alembic, LangGraph 1.2, APScheduler 3.x, pytest; Next.js 16.4, React 19, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-31-tpc-22-period-close-design.md`

## Global Constraints

- **Commands:** backend tests from `backend/` with `../venv/bin/python -m pytest` (full suite: `-k "not live"`); frontend from `frontend/` with `npm test`, `npm run lint`, `npm run build`. No new dependencies.
- **Reserved keys (exact):** `SAVINGS = "SAVINGS_TRANSFER"`, `FREE = "FREE"`; Plaid detailed `"TRANSFER_OUT_SAVINGS"`.
- **Groups:** `"needs" | "wants" | "saving" | "free"`. Styles: `"caps" | "50_30_20"`. Default targets `{"needs": 50, "wants": 30, "saving": 20}`. Setting: `prefs_json["budget"] = {"style", "targets"}`.
- **Default `needs`:** `RENT_AND_UTILITIES`, `LOAN_PAYMENTS`, `TRANSPORTATION`, `MEDICAL`, `FOOD_AND_DRINK`. Default names: `"Savings"`, `"Free"`, else title case with `_` → space and `And` → `and` (`FOOD_AND_DRINK` → `"Food and drink"`).
- **Rules from the spec:** `FLEX_MARKUP` (1.05) for the fallback total; free = `max(…, 0)`; close is one commit; a style change applies at the next init/close; categories resolve user > merchant rule > `our_category` > savings transfer > Plaid primary > `"UNCATEGORIZED"`; merchant rules match trimmed, case-insensitive.
- **Run statuses:** close runs `"ok"` / `"error"` (none written when nothing is due); expired budget runs `"expired"`.
- **Alert copy (exact; amounts `${:,.2f}`):**
  - `over_income`: `"Your budget lines add up to {lines}, {gap} more than your typical income of {income}."`;
  - `group_over_target`: `"{Group} are {share:.0%} of income, above the {target}% target."` (`Group` = `"Needs"` / `"Wants"`).
- **API errors (exact):** `"Only needs and wants lines can change group."` (422); `"Pick one of your budget lines."` (422); `"This transaction has no merchant to remember."` (422); `"Targets must add up to 100."` (422); `"Not found."` (404).
- **The working tree holds the user's uncommitted edits:** `docs/architecture.md`, `docs/agent-pipelines.md`, `docs/diagrams/`, `docs/system-walkthrough.md`, `eval/results/2026-10-29/`, `graphify-out/`. Never stage, modify or remove them. Commit `architecture.md` changes only as a patch built against `HEAD` and applied with `git apply --cached` plus `git apply`, as in TPC-20/21.
- **Test docs:** every new or changed test gets an entry in `docs/tests/<module>.md` (or `<Name>.test.md` for frontend) in the same commit.
- **Commits:** prefixes `feat(TPC-22):`, `docs(TPC-22):`, `fix(TPC-22):`. **No Co-Authored-By or "Generated with Claude Code" lines.**

## Review Focus

1. **Closing December** must open January of the next year (2027-01-01 → 2027-01-31), not December again. Pinned by `test_close_across_new_year` (Task 7).
2. **A style switched mid-month** keeps the current month on its old style; the next close uses the new one. Pinned by `test_style_change_applies_at_close` (Task 7).
3. **Correcting a transaction in a closed month** saves the correction and leaves the closed period and the open month's totals untouched. Pinned by `test_correct_closed_month_txn` (Task 11).
4. **A remembered merchant spelled differently** (`"Costco"` vs `"COSTCO "`) still matches. Pinned by `test_rule_matches_case_insensitively` (Task 2).
5. **An accepted proposal on a line the user renamed or regrouped** is still applied (by category), and the new line keeps the new name. Pinned by `test_accepted_after_rename` (Task 7).

---

### Task 1: Schema

**Files:**
- Create: `backend/alembic/versions/<rev>_period_close_and_styles.py` (`down_revision = "e0cdfdbdab45"`), `backend/tests/test_migrations.py`, `docs/tests/test_migrations.md`.
- Modify: `backend/app/db/models.py`.

**Interfaces:**
- Produces: `BudgetLine.name: str`, `BudgetLine.group: str`; `BudgetPeriod.income_cents`, `.total_cents: Optional[int]`; `Transaction.user_category: Optional[str]`; `CategoryRule(id, user_id, merchant_name, category, created_at)` with unique `uq_category_rules_user_merchant`; `LineGroup`, `BudgetStyle` literals.

- [ ] **Step 1: Failing test** `test_upgrade_backfills_and_downgrades` (monkeypatch `app.config.DATABASE_URL` to a `tmp_path` SQLite file, `alembic.command.upgrade(Config("alembic.ini"), "e0cdfdbdab45")`, insert a user, period and two lines `FOOD_AND_DRINK` / `ENTERTAINMENT`, upgrade `head`): names `"Food and drink"` / `"Entertainment"`, groups `"needs"` / `"wants"`, `category_rules` exists; `downgrade("e0cdfdbdab45")` succeeds.
- [ ] **Step 2: Run and confirm it fails.**
- [ ] **Step 3: Implement.** Batch mode; add `name`/`group` nullable, backfill, then make them non-null. The migration keeps its own copy of the default mapping and naming (migrations never import app code).
- [ ] **Step 4: Run it, then the full suite, and confirm they pass.** Also `../venv/bin/alembic upgrade head` on a copy of `dev.db`.
- [ ] **Step 5: Docs and commit.** `feat(TPC-22): line names and groups, period income and total, category corrections tables`.

---

### Task 2: Transaction categories

**Files:**
- Modify: `backend/app/budget_engine/txns.py`, `backend/app/budget_service/initialize.py` (`load_txns`).
- Tests: `backend/tests/test_budget_txns.py`, `backend/tests/test_budget_service.py`, their docs.

**Interfaces:**
- Consumes: Task 1's `Transaction.user_category`, `CategoryRule`.
- Produces: `SAVINGS`, `FREE`, `SAVINGS_DETAILED = "TRANSFER_OUT_SAVINGS"`; `Txn.overridden: bool = False`; `spending(txns)` (also drops `SAVINGS`; keeps an overridden card payment); `savings(txns) -> list[Txn]`; `income_by_month(txns, months: Sequence[date]) -> list[int]` (positive cents per month); `normalize_merchant(name: str) -> str` (strip + casefold); in `initialize.py`, `resolve_category(row: Transaction, rules: dict[str, str]) -> tuple[str, str]` returning `(category, source)` with source `"user" | "rule" | "plaid"` (LLM and savings-transfer count as `"plaid"`), used by `load_txns` (`overridden=True` when source isn't `"plaid"`) and later by the transactions API.

- [ ] **Step 1: Failing tests.**
  - `test_budget_txns.py`: `test_spending_drops_savings`, `test_spending_keeps_overridden_card_payment`, `test_savings_selects_transfers`, `test_income_by_month` (−420000 on the 15th of two months, none in the third → `[420000, 420000, 0]`).
  - `test_budget_service.py`: `test_load_txns_precedence` (four rows: user override, rule, `our_category`, plain Plaid; plus a `TRANSFER_OUT`/`TRANSFER_OUT_SAVINGS` row → `SAVINGS_TRANSFER`); `test_rule_matches_case_insensitively` (rule `"costco"`, merchant `"COSTCO "` → the rule's category, `overridden=True`).
- [ ] **Step 2: Run and confirm they fail.** **Step 3: Implement.** Rules load once per `load_txns` call into a dict keyed by `normalize_merchant`; `category_rules.merchant_name` is stored normalized.
- [ ] **Step 4: Run the two files and the full suite; confirm they pass.**
- [ ] **Step 5: Docs and commit.** `feat(TPC-22): one place resolves a transaction's category; savings and income`.

---

### Task 3: Groups and the fit

**Files:**
- Create: `backend/app/budget_engine/groups.py`, `backend/tests/test_budget_groups.py`, `docs/tests/test_budget_groups.md`.
- Modify: `backend/app/budget_engine/fit.py`, `backend/tests/test_budget_fit.py` (+ doc).

**Interfaces:**
- Consumes: Task 2's `savings`, `income_by_month`, `SAVINGS`.
- Produces: `default_group(category: str) -> str`, `default_name(category: str) -> str`, `NEEDS_DEFAULT: frozenset[str]`; `FitResult.income_cents: Optional[int]` and `FitResult.spending_total_cents: int` (both defaulted so existing constructions still work); the saving line as a `FittedLine(SAVINGS, "fixed", …)` in `lines`.

- [ ] **Step 1: Failing tests.**
  - `test_budget_groups.py`: `test_default_groups` (`FOOD_AND_DRINK` → needs, `ENTERTAINMENT` → wants, `SAVINGS_TRANSFER` → saving, `FREE` → free), `test_default_names` (`"Food and drink"`, `"Rent and utilities"`, `"Savings"`, `"Free"`).
  - `test_budget_fit.py`: `test_saving_line_lower_median` (transfers 50000 / 50000 / 0 → limit 50000, kind fixed), `test_no_transfers_no_saving_line`, `test_income_median`, `test_income_none_when_median_zero` (one paycheck in three months), `test_spending_total_fallback` (median monthly spending × 1.05, rounded half up).
- [ ] **Step 2: Run and confirm they fail.** **Step 3: Implement.**
- [ ] **Step 4: Run both files, `test_budget_accuracy.py` and the full suite; confirm they pass.** (Seeded transfers only become savings in Task 6, so the accuracy numbers can't move here.)
- [ ] **Step 5: Docs and commit.** `feat(TPC-22): default groups and names; the fit finds savings and income`.

---

### Task 4: Styles

**Files:**
- Create: `backend/app/budget_engine/styles.py`, `backend/tests/test_budget_styles.py`, `docs/tests/test_budget_styles.md`.
- Modify: `backend/app/budget_engine/pace.py` (`BudgetLine.group: str = "wants"`).

**Interfaces:**
- Consumes: Task 2's `SAVINGS`, `FREE`; `round_cents`.
- Produces:
  - `StyleFact(kind: Literal["over_income", "group_over_target"], group: Optional[str], lines_cents: int, income_cents: int, target_pct: Optional[int])`;
  - `StyleResult(limits: dict[str, int], total_cents: int, facts: tuple[StyleFact, ...], fallback: Optional[str])` — `limits` has every category's final limit, including `FREE` and a `SAVINGS` line `50_30_20` created;
  - `apply_style(style: str, lines: Sequence[BudgetLine], income_cents: Optional[int], fallback_total_cents: int, targets: Mapping[str, int]) -> StyleResult` (`lines` excludes free);
  - `DEFAULT_TARGETS`, `STYLES = ("caps", "50_30_20")`.

- [ ] **Step 1: Failing tests:**
  - `test_caps_free_is_income_minus_lines`; `test_caps_free_bottoms_out_and_total_rises` (lines 450000, income 420000 → free 0, total 450000, one `over_income` fact); `test_caps_without_income_uses_fallback`;
  - `test_503020_targets` (income 400000: saving line set to 80000, free = 120000 − wants lines, total ≥ income); `test_503020_creates_saving_line`; `test_503020_group_over_target` (needs 230000 of 400000 → fact `needs`, target 50); `test_503020_custom_targets`; `test_503020_without_income_falls_back` (`fallback == "no_income"`, caps numbers).
- [ ] **Step 2: Run and confirm they fail.** **Step 3: Implement.** **Step 4: Run them and `test_budget_pace.py`/`test_budget_close.py`; confirm they pass.**
- [ ] **Step 5: Docs and commit.** `feat(TPC-22): caps and 50/30/20 budget styles`.

---

### Task 5: The free and saving lines in the daily run

**Files:**
- Modify: `backend/app/budget_engine/pace.py`, `backend/app/budget_engine/diagnose.py`, `backend/app/agents/budget_graph.py`, `backend/app/api/budget.py` (pass `group` to `EngineLine`).
- Tests: `test_budget_pace.py`, `test_budget_diagnose.py`, `test_budget_graph.py`, their docs.

**Interfaces:**
- Consumes: Task 4's `BudgetLine.group`; Task 2's `FREE`.
- Produces: `evaluate` — a `group == "free"` line's spent = unlined spending + `FREE`-categorized; `unbudgeted` rows still emitted; `group == "saving"` never flagged. `overspend_facts(..., *, lined: frozenset[str] = frozenset())` — for `FREE` the period and history transactions are the ones whose category isn't in `lined`. In `decide_action`, `adjust_next_budget` on a free or saving line is downgraded like a non-recurring cause.

- [ ] **Step 1: Failing tests:** `test_free_line_counts_unlined_spending`, `test_no_free_line_unchanged`, `test_saving_line_never_flagged` (pace); `test_free_facts_use_unlined_categories` (diagnose); `test_free_line_never_proposes` (graph: a scripted `recurring` + `adjust_next_budget` for `FREE` → alert, `downgraded`, no pause).
- [ ] **Step 2: Run and confirm they fail.** **Step 3: Implement.** **Step 4: Run the three files and the full suite; confirm they pass.**
- [ ] **Step 5: Docs and commit.** `feat(TPC-22): the free line covers unlined spending; saving is never overspent`.

---

### Task 6: The full model at init

**Files:**
- Create: `backend/app/budget_service/settings.py`.
- Modify: `backend/app/budget_service/initialize.py`, `eval/budget_users.py` (savings transfers get `detailed="TRANSFER_OUT_SAVINGS"`), `eval/budget_accuracy.py` (score only `needs`/`wants`).
- Tests: `test_budget_service.py`, `test_budget_accuracy.py`, `test_budget_users.py`, their docs.

**Interfaces:**
- Consumes: Tasks 3–4.
- Produces: `user_style(session, user_id) -> tuple[str, dict[str, int]]`; `set_user_style(session, user_id, style, targets: Optional[dict] = None) -> None` (validates; raises `ValueError("Targets must add up to 100.")`); `initialize_budget` stores `name`, `group` per line, `income_cents`, `total_cents`, and the free line (`kind="flexible"`, `group="free"`, `source="init"`, `fit_json={"rule": "remainder"}`).

- [ ] **Step 1: Failing tests:** `test_init_writes_groups_names_income_total_free` (a planted user: Savings line 50000, income 420000, free = 420000 − other lines); `test_init_503020` (style set first: saving = 20%); `test_set_user_style_validates`; `test_planted_savings_detailed` (seeded transfers carry the detailed category); `test_accuracy_ignores_saving_and_free`.
- [ ] **Step 2: Run and confirm they fail.** **Step 3: Implement.** **Step 4: Run the files, the full suite and `../venv/bin/python -m eval.budget_accuracy` (numbers match `eval/results` from TPC-18).**
- [ ] **Step 5: Docs and commit.** `feat(TPC-22): init stores income, total, groups, names and the free and saving lines`.

---

### Task 7: The close graph

**Files:**
- Create: `backend/app/agents/close_graph.py`, `backend/app/budget_service/close.py`, `backend/tests/test_close.py`, `docs/tests/test_close.md`.
- Modify: `backend/app/budget_engine/close.py` (doc comment only: free isn't passed in).

**Interfaces:**
- Consumes: Tasks 2–6; `close_period`; `load_txns`; `user_style`.
- Produces: `close_due(session_factory, user_id: str, today: date) -> list[dict]` (one summary per month closed, keys as the spec's `summary_json`); `build_close_graph(session_factory)`; `expire_proposals(session, period_id: int) -> list[int]`; messages built by `_fact_message(fact) -> str` with the exact copy above.

- [ ] **Step 1: Failing tests** (seed with `tests/factories.py`; a period built directly):
  - `test_close_opens_adapted_month` (old `closed`, new Nov 1–30, lines smoothed/fixed per `close_period`, names and groups carried, `income_cents`, `total_cents`, free, one `ok` run with the summary keys);
  - `test_accepted_applied_latest_wins` (two accepted on one category → the higher id's value, `source="user"`);
  - `test_accepted_after_rename`;
  - `test_pending_proposal_expires` (alert `dismissed`, advice `ignored`, paused run `expired` with `expired_alert_ids`; `decide_alert` raises `AlertAlreadyDecided`);
  - `test_nothing_due_writes_nothing`; `test_rerun_writes_nothing`; `test_lost_race_rolls_back` (patch to insert November between `gather` and `persist` → no run row, old period still open in that attempt);
  - `test_two_missed_months` (`today` Dec 3 with only October open → Oct and Nov closed, December open, two runs, each from its own actuals);
  - `test_failure_rolls_back` (patch `close_period` to raise → `error` run with the type, period open, no new period);
  - `test_legacy_period_gains_free_and_saving`;
  - `test_503020_close_alerts` (`group_over_target` alert on the **new** period, exact message);
  - `test_over_income_alert` (exact message, free 0);
  - `test_style_change_applies_at_close`; `test_close_across_new_year`.
- [ ] **Step 2: Run and confirm they fail.** **Step 3: Implement.** `persist` runs `expire_proposals` and the writes in one session/commit; `IntegrityError` → rollback and end; other exceptions → rollback, then an `error` run in a fresh session.
- [ ] **Step 4: Run `test_close.py`, `test_budget_alerts.py` and the full suite; confirm they pass.**
- [ ] **Step 5: Docs and commit.** `feat(TPC-22): the period-close graph`.

---

### Task 8: Wiring

**Files:**
- Modify: `backend/app/budget_service/daily.py` (delete `close_month`), `backend/app/scheduler.py`, `backend/app/budget_service/simulate.py`.
- Tests: `test_daily.py`, `test_scheduler.py`, `test_simulate.py`, their docs.

**Interfaces:**
- Consumes: Task 7's `close_due`.
- Produces: `run_daily_locked` calls `close_due` first and adds `close_run_ids: list[int]` to its result (a close exception is logged; the budget run still runs). `month_end_job(session_factory, *, today=None) -> {"closed", "skipped", "failed"}` (no `skip_done`; non-blocking `user_lock`); `add_catch_up_jobs` calls it without `skip_done`.

- [ ] **Step 1: Failing/updated tests:**
  - `test_daily.py`: replace `test_close_month_placeholder_row` with `test_daily_closes_before_budget` (close run id < budget run id; budget run `ok` on the new period) and `test_failed_close_still_runs_budget` (patch `close_due` to raise → budget run `period_ended`).
  - `test_scheduler.py`: `test_month_end_job_closes_previous_month` (three users closed; October `closed`, November open); `test_month_end_skips_locked_user`; replace `test_month_end_catch_up_skips_closed` with `test_month_end_rerun_closes_nothing`.
  - `test_simulate.py`: `test_month_boundary_closes_first` and `test_close_across_new_year` assert on the real close (summary month, a new period) before the 1st's budget run.
- [ ] **Step 2: Run and confirm they fail.** **Step 3: Implement.** **Step 4: Run the three files and the full suite; confirm they pass.**
- [ ] **Step 5: Docs and commit.** `feat(TPC-22): each daily run closes due months first; the month-end slot closes for real`.

---

### Task 9: The M3 demo (done-when)

**Files:**
- Create: `backend/tests/test_m3_demo.py`, `docs/tests/test_m3_demo.md`.
- Modify: `backend/tests/fakes.py` (`SchemaReplyModel.cause: str = "one_time"`, `.first_action: str = "alert"`).

**Interfaces:**
- Consumes: `seed_profile(until=)`, `advance_days`, `decide_alert`, `evaluate`, `close_due` (through the daily run).

- [ ] **Step 1: Failing test** `test_m3_demo` (`overspend-recurring` from 2026-09-30, `SchemaReplyModel(cause="recurring", first_action="adjust_next_budget")`, `InMemorySaver`):
  - advance 31 days; the oracle is `evaluate` called directly for each day's transactions: the first `PERSONAL_CARE` alert's day ≤ the first flagged day + 1, `cause == "recurring"`;
  - `decide_alert(..., "accepted")` on its proposal; advance 1 day;
  - November: `PERSONAL_CARE.limit_cents == proposed_limit_cents`, `source == "user"`; every other flexible needs/wants line within ±15% of October's; fixed lines equal October's actuals; free == `total_cents` − the other lines (or 0).
- [ ] **Step 2: Run it.** It should fail only where the fake model needs the new fields; if it fails elsewhere, that is a real bug for Tasks 5–8 (fix there, with a test).
- [ ] **Step 3: Implement the fake model fields.** **Step 4: Run it and the full suite; confirm they pass.**
- [ ] **Step 5: Docs and commit.** `feat(TPC-22): the M3 demo passes end to end`.

---

### Task 10: Budget API

**Files:**
- Modify: `backend/app/api/budget.py`. Tests: `test_budget_api.py` (+ doc).

**Interfaces:**
- Consumes: Task 6's `set_user_style`, `user_style`.
- Produces: `LineOut` adds `id`, `name`, `group`; `BudgetOut` adds `income_cents`, `total_cents`, `style`; `PATCH /budget/lines/{id}` (`LineEdit{name: Optional[str], group: Optional[str]}`) → `LineOut`; `PUT /budget/style` (`StyleIn{style, targets: Optional[dict[str, int]]}`) → `{style, targets, effective_from}`.

- [ ] **Step 1: Failing tests:** `test_budget_has_model_fields`; `test_rename_line` (trimmed; 1–40 chars, else 422); `test_regroup_needs_wants`; `test_regroup_saving_rejected` (exact message); `test_edit_closed_period_line_404`; `test_put_style` (`effective_from` = next month's first, by the simulated date for a simulated user); `test_put_style_bad_targets` (exact message).
- [ ] **Step 2: Run and confirm they fail.** **Step 3: Implement.** **Step 4: Run the file and the full suite; confirm they pass.**
- [ ] **Step 5: Docs and commit.** `feat(TPC-22): budget fields, line edits and the style setting in the API`.

---

### Task 11: Transactions API

**Files:**
- Create: `backend/app/api/transactions.py`, `backend/tests/test_transactions_api.py`, `docs/tests/test_transactions_api.md`.
- Modify: `backend/app/main.py`.

**Interfaces:**
- Consumes: Task 2's `resolve_category` and `normalize_merchant`; `sim_today`.
- Produces: `GET /transactions?month=YYYY-MM` → `list[TxnOut{id, date, merchant, amount_cents, category, line_name, source}]`; `PATCH /transactions/{id}` (`TxnEdit{category: str, remember: bool = False}`) → `TxnOut`.

- [ ] **Step 1: Failing tests:** `test_list_month_with_sources`; `test_list_defaults_to_sim_month`; `test_correct_one`; `test_remember_creates_and_updates_rule`; `test_override_beats_rule`; `test_category_must_be_a_line` (exact message); `test_remember_without_merchant` (exact message); `test_correct_closed_month_txn`; `test_other_users_txn_404`.
- [ ] **Step 2: Run and confirm they fail.** **Step 3: Implement.** **Step 4: Run the file and the full suite; confirm they pass.**
- [ ] **Step 5: Docs and commit.** `feat(TPC-22): list and correct transactions`.

---

### Task 12: CLIs

**Files:**
- Modify: `backend/scripts/seed_budget_user.py`, `backend/scripts/simulate.py`. Tests: `test_seed_budget_user.py`, `test_simulate_cli.py`, their docs.

- [ ] **Step 1: Failing tests:** `test_seed_style_503020` (the budget is fitted as 50/30/20); `test_simulate_sets_style` (prefs updated, exit 0; an unknown style exits 2 via argparse `choices`).
- [ ] **Step 2–4:** fail, implement (`--style` with `choices=STYLES`, default `caps` for seeding; simulate sets it only when given), pass.
- [ ] **Step 5: Docs and commit.** `feat(TPC-22): --style for seeding and simulation`.

---

### Task 13: Frontend

**Files:**
- Create: `frontend/components/TransactionsTable.tsx`, `TransactionsTable.test.tsx`, `docs/tests/TransactionsTable.test.md`.
- Modify: `frontend/lib/api.ts`, `frontend/components/BudgetPaceBar.tsx` (+ test, doc), `frontend/app/dashboard/page.tsx`, `frontend/components/AlertCard.test.tsx` (+ doc).

**Interfaces:**
- Consumes: Tasks 10–11.
- Produces: `api.ts`: `Txn`, `getTransactions(month?)`, `correctTransaction(id, category, remember)`; `BudgetLine` gains `id`, `name`, `group`.

- [ ] **Step 1: Failing tests:** `renders rows with their line`; `picking a line corrects the row`; `offers "Always for Costco?" and remembers`; `marks rule rows`; `BudgetPaceBar shows the line name`; `AlertCard shows over_income and group_over_target messages`.
- [ ] **Step 2: Run and confirm they fail.** **Step 3: Implement** (the category button is a `<select>` of the user's line names plus "Free"; follow the existing components' styling). **Step 4: `npm test`, `npm run lint`, `npm run build` pass.**
- [ ] **Step 5: Docs and commit.** `feat(TPC-22): transactions table with category corrections; line names on the budget`.

---

### Task 14: Live demo and docs

**Files:**
- Modify: `docs/architecture.md` (patch method), `README.md`, `.env.example` (only if a setting changed), `eval/README.md` if accuracy output changed.

- [ ] **Step 1: Live run.** `seed_budget_user.py --profile overspend-recurring --replace --start default`, then `scripts/simulate.py --user budget-overspend-recurring --days 31` with `qwen2.5:14b`; accept the proposal on `/alerts`; advance 1 day. Record: runs, alerts, November's lines, total and free.
- [ ] **Step 2: Architecture doc** (as a patch against `HEAD`): data model; 6.3 (saving, income, total, styles); 6.4 (free in pace); 6.5 as built; 6.6 expiry; scheduler notes; API inventory; the live demo; decision-log rows for the spec's decisions table.
- [ ] **Step 3: README:** `--style`, the new routes, the Transactions table.
- [ ] **Step 4: Full verification:** backend `-k "not live"`, frontend test/lint/build, `alembic upgrade head` on a `dev.db` copy.
- [ ] **Step 5: Commit.** `docs(TPC-22): architecture, decision log, readme and the live M3 demo`.
