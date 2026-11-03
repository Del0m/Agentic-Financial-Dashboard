# TPC-31 Cash-Flow Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Forecast the checking balance as a range, raise a `low_balance` alert that names the bill when the low path dips under the user's buffer, and feed a balance-independent monthly surplus to the Goal Agent.

**Architecture:** A pure `budget_engine/forecast.py` turns streams (from TPC-33's detector), card minimums, accepted goals and 90 days of everyday spending into a day-by-day path, a dip and a surplus. One function, `forecast_service.snapshot`, loads the inputs and is called by the goal graph, the budget run's `forecast_cash` node and `GET /forecast`, so the three always agree. The low-balance alert reuses TPC-33's `Signal`, `decide_signals` and node routing.

**Tech Stack:** Python, FastAPI, SQLAlchemy, Alembic, LangGraph 1.2.18, pytest, Next.js 16, React, recharts 3.10.1, vitest.

**Spec:** `docs/superpowers/specs/2026-11-02-tpc-33-31-recurring-and-forecast-design.md`. The user must have approved its "Departures from the tickets" table, and TPC-33 must be finished (its Task 8 committed).

## Global Constraints

- Create branch `TPC-31` from `TPC-33`, not from `main`: `git switch -c TPC-31 TPC-33`. The PR's base is `TPC-33` until that merges; then `git rebase --onto origin/main TPC-33`.
- The TPC-31 PR description must not mention TPC-33 (or TPC-25) by key; call them "the recurring-streams work" and "goal approval". Jira closed TPC-33 seven seconds after three merged PRs that only mentioned it (see the index handoff).
- Money is integer cents, rounded with `round_cents`. Dates are passed in (`today`); never `date.today()`. Synthetic users follow `sim_today`.
- Commit messages carry no `Co-Authored-By` or "Generated with Claude Code" lines (the user's standing rule for this repo).
- Every new or changed test gets an entry in `docs/tests/<test file>.md`, in that folder's existing format.
- Frontend: read the relevant guides in `frontend/node_modules/next/dist/docs/` before writing page code (`frontend/AGENTS.md`).
- Don't commit `eval/results/2026-10-29/` or `eval/results/2026-10-31/`. Add files by name, never `git add -A`.
- Match the surrounding style: lowercase `#` comments that explain why.
- Backend commands run from `backend/`; the fast suite is `../venv/bin/python -m pytest -q -k 'not live and not sandbox'`. Frontend commands run from `frontend/`: `npm test`, `npm run lint`, `npm run build`.
- Never copy balances, bill names or merchant names into `summary_json`, logs or checkpoints. Ids and cents only.
- The last task writes `docs/handoffs/2026-11-02-tpc-31-manual-verification.md` and updates `docs/architecture.md`.

## Review Focus

1. **Surplus is a flow:** the same user with $100 or $20,000 in checking gets the same surplus, and the cap never lets accepted goals pass `max_share` of the gross surplus. Pinned in Tasks 1 and 3.
2. **Existing goal tests stay on the measured path:** the 21 tests that assert exact measured numbers pass unchanged; only new, marked tests use the forecast. Pinned in Task 3.
3. **One alert per dip:** the same dip on three days alerts once; +25% and +$20 or "within 2 days" alerts again; a dip that slides later within 3 days does not. Pinned in Task 4.
4. **The alert names the bill:** the model cannot hide or invent it. A message without the bill name is replaced. Pinned in Task 4.
5. **No paycheck stream:** the forecast says it assumes no income, the surplus falls back, and the only action is `alert`. Pinned in Tasks 1 and 4.
6. **Checkpoint:** a paused run holds no bill name or balance from the forecast. Pinned in Task 4.

---

### Task 1: The forecast engine

**Files:**
- Create: `backend/app/budget_engine/forecast.py`, `backend/tests/test_forecast_engine.py`, `docs/tests/test_forecast_engine.md`
- Modify: `backend/app/budget_engine/goals.py` (`goal_cap` only), `backend/tests/test_goals.py`

**Interfaces:**
- Produces, in `budget_engine/forecast.py` (pure, no DB, no clock; uses `Stream`, `project`, `monthly_cents` from `recurring.py` and `CommittedGoal` from `goals.py`):

```python
@dataclass(frozen=True)
class CardIn:  account_id: int; name: str; min_cents: Optional[int]; due_date: Optional[date]
@dataclass(frozen=True)
class ForecastInput:
    today: date; start_balance_cents: Optional[int]           # None -> no_checking
    streams: Sequence[Stream]; cards: Sequence[CardIn]; goals: Sequence[CommittedGoal]
    everyday_daily_cents: Sequence[int]                       # 90 values, oldest first, zero days included
    history_days: int; inflow_transactions: int; buffer_cents: int = 10_000
@dataclass(frozen=True)
class Event:   date: date; name: str; cents: int; source: Literal["stream", "card_min", "goal"]; key: str   # negative = out; key: merchant_key, "card:<account_id>" or "goal:<id>"
@dataclass(frozen=True)
class Day:     date: date; expected_cents: int; low_cents: int; high_cents: int
@dataclass(frozen=True)
class Dip:     date: date; days_ahead: int; shortfall_cents: int; cause: Optional[Event]     # cause None means everyday spending
@dataclass(frozen=True)
class ForecastResult:
    status: Literal["ok", "no_checking", "no_history"]
    income: Literal["paycheck", "none"]
    horizon_days: int; days: tuple[Day, ...]; events: tuple[Event, ...]; dip: Optional[Dip]
    monthly_surplus_cents: Optional[int]                      # None whenever surplus_fallback is set
    committed_goal_cents: int; cash_above_buffer_cents: Optional[int]
    surplus_fallback: Optional[Literal["no_inflow_stream", "short_history", "stale_paycheck", "no_checking"]]
    assumptions: tuple[str, ...]                              # "no_income_stream", "card_due_date_unknown:<name>", "bill_overdue:<name>"
def forecast(inp: ForecastInput) -> ForecastResult
```

- Produces, in `budget_engine/goals.py`: `goal_cap(surplus_cents: int, committed_cents: int, max_share: float, *, net_of_goals: bool) -> int`. `net_of_goals=True`: `max(round_cents(max_share * (surplus + committed)) - committed, 0)`. `False` (the measured path): `max(round_cents(max_share * surplus) - committed, 0)`.
- Rules, from the spec "Design: Forecast": events in `(today, today + horizon]`; `horizon_days = min(max(30, days to the next active INCOME occurrence + 1), 45)`, 30 with no income stream; stale streams dropped; an overdue active stream lands tomorrow if overdue by 5 days or less, otherwise rolls to its next date with `bill_overdue:<name>`; a card is covered by a card-payment outflow stream whose key contains the normalised account name (or the only card and only such stream) and then adds nothing; an uncovered card puts its minimum on its due date (a past due date rolls forward by whole months; a null one adds `card_due_date_unknown:<name>` and counts only in the surplus); a goal's event is the day after the first `INCOME` occurrence in each month where `budgeted_in(goal, month)`, or the 1st with no income stream, and only if after `today`; an outflow stream is skipped as a goal payment when its category is `SAVINGS_TRANSFER` or its detailed category starts with `LOAN_PAYMENTS` and its `first_date` is on or after an active goal's `start_date`; `no_history` below 28 days. Everyday cost on day `k`: expected `round(mean * k)`, low `round(mean * k + 1.2816 * sd * sqrt(k/7))`, high `round(max(mean * k - 1.2816 * sd * sqrt(k/7), 0))`, with `sd` the sample deviation of the last twelve 7-day totals (0 with fewer than 2). Surplus: `sum(monthly_cents)` of active inflow streams minus active outflow streams (goal payments skipped) minus goals' monthly cents minus uncovered cards' minimums minus `30.4375 * mean`, rounded to the nearest $10 (1000 cents). The fallback applies, in order: `no_checking`, `no_inflow_stream` (no active `INCOME` stream), `stale_paycheck` (every `INCOME` stream stale), `short_history` (`history_days < 65` or `inflow_transactions < 3`).

- [ ] **Step 1: Write the failing tests**

Shared helper: `today = date(2026, 10, 10)`, `buffer 10_000`, `everyday_daily_cents=[0]*90`, rent = outflow `Stream` (typical 150000, monthly, next Oct 13), pay = inflow `INCOME` stream (typical 420000, monthly, next Oct 15), `history_days=120`, `inflow_transactions=4`.

```python
def test_rent_before_payday_is_a_dip_that_names_rent():            # start 155000: dip.date Oct 13, days_ahead 3, shortfall 5000, cause.name "Landlord"; day Oct 15 expected 425000
def test_a_low_path_equal_to_the_buffer_is_not_a_dip():            # start 160000 -> low on Oct 13 is 10000 -> dip None
def test_the_deepest_low_in_14_days_sets_the_shortfall():          # start 155000, plus a 3000 bill on Oct 14 -> shortfall 8000, dip.date still Oct 13
def test_an_event_dated_today_is_not_applied_twice():
def test_everyday_spending_falls_by_the_mean_not_the_median():     # [0,0,0,400]*22 + [0,0]: mean 8800/90; day 1 expected == start - 98; the median day is 0
def test_constant_spending_has_no_band():                          # [100]*90: sd 0 -> low == expected == high; day 14 expected == start - 1400
def test_the_band_orders_and_is_symmetric_until_clamped():         # noisy series: low <= expected <= high on every day
def test_no_checking_account():                                    # start None -> status no_checking, days (), dip None, fallback "no_checking", surplus None
def test_under_28_days_of_history_is_no_history():
def test_no_income_stream_assumes_no_income():                     # income "none", "no_income_stream" in assumptions, fallback "no_inflow_stream", horizon_days 30
def test_a_stale_paycheck_falls_back():                            # fallback "stale_paycheck"; the stale stream makes no event
def test_short_history_falls_back():                               # history_days 60 -> "short_history"; inflow_transactions 2 -> same
def test_an_overdue_bill_lands_tomorrow():                         # rent next_date Oct 8, active -> event Oct 11
def test_a_bill_overdue_by_more_than_5_days_rolls_forward():       # next_date Oct 1 -> event Nov 1 and "bill_overdue:Landlord" in assumptions
def test_card_minimum_lands_on_its_due_date_without_a_stream():    # CardIn(7, "Platinum Card", 9500, Oct 20) -> Event(Oct 20, "Platinum Card", -9500, "card_min", "card:7")
def test_a_card_with_a_payment_stream_adds_no_minimum():           # outflow stream "Platinum Card", detailed LOAN_PAYMENTS_CREDIT_CARD_PAYMENT, 9500
def test_a_card_with_no_due_date_counts_in_the_surplus_only():     # assumption "card_due_date_unknown:Platinum Card"; no event; surplus 9500 lower
def test_a_past_due_date_rolls_forward_by_months():                # Sep 20 -> Oct 20
def test_goal_pays_the_day_after_payday():                         # CommittedGoal(1, "debt_payoff", 58300, Oct 1, 2027-03-01) -> Event(Oct 16, "Debt payoff", -58300, "goal", "goal:1")
def test_goal_with_no_paycheck_pays_on_the_first():                # start Nov 1, no income stream -> Event(Nov 1, ...)
def test_a_goal_not_yet_started_has_no_event_but_is_in_the_surplus():
def test_goal_payments_already_in_history_are_not_counted_twice():  # a SAVINGS_TRANSFER stream with first_date Oct 1 and an emergency_fund goal starting Oct 1 is skipped; one with first_date Aug 1 is not
def test_surplus_is_a_flow_and_ignores_the_balance():              # streams: pay 420000, rent 150000, Savings 50000 (SAVINGS_TRANSFER), Streamly 1599; everyday [100]*90 -> 420000-150000-50000-1599-3044 = 215357 -> 215000, for start 10_000 and for 2_000_000
def test_accepted_goals_lower_the_surplus_by_exactly_their_amount():   # goals 58300 + 19440 -> 215357 - 77740 = 137617 -> 138000; committed_goal_cents 77740
def test_cash_above_buffer_is_reported_separately():               # start 155000 -> 145000
def test_monthly_bill_on_the_31st_is_clamped():
# test_goals.py
def test_goal_cap_on_the_forecast_path():                          # goal_cap(50000, 50000, 0.8, net_of_goals=True) == 30000; (44200, 58300, ...) == 23700; (24760, 77740, ...) == 4260; (97175, 0, ...) == 77740; (-5000, 0, ...) == 0
def test_goal_cap_on_the_measured_path_is_unchanged():             # goal_cap(97175, 58300, 0.8, net_of_goals=False) == 19440
```

- [ ] **Step 2: Run them to verify they fail**

Run: `../venv/bin/python -m pytest tests/test_forecast_engine.py tests/test_goals.py -q`
Expected: FAIL (module missing; `goal_cap` undefined).

- [ ] **Step 3: Implement**, **Step 4: Run, then the whole suite.** Expected: PASS.
- [ ] **Step 5: Tests docs and commit**

```bash
git add backend/app/budget_engine/forecast.py backend/app/budget_engine/goals.py backend/tests/test_forecast_engine.py backend/tests/test_goals.py docs/tests/test_forecast_engine.md docs/tests/test_goals.md
git commit -m "feat(TPC-31): forecast the checking balance as a range, with a flow-based surplus"
```

---

### Task 2: Inputs, the snapshot, the buffer and the dip user

**Files:**
- Create: `backend/app/budget_service/forecast_data.py`, `backend/app/budget_service/forecast_service.py`, `backend/app/budget_service/forecast_prefs.py`, `backend/tests/test_forecast_service.py`
- Modify: `eval/budget_users.py` (`FORECAST_USERS`), `backend/app/budget_service/simulate.py` and `backend/scripts/seed_budget_user.py` (registries)

**Interfaces:**
- `forecast_prefs.py` (copy `settings.py`): `DEFAULT_BUFFER_CENTS = 10_000`; `user_buffer(session, user_id) -> int` (reads `prefs_json["forecast"]["low_balance_buffer_cents"]`, else the flat key, else the default); `set_user_buffer(session, user_id, cents: int) -> None`: `ValueError` unless `isinstance(cents, int)`, not `bool`, and `0 <= cents <= 1_000_000`; replaces the dict whole; the caller commits.
- `forecast_data.py`:
  - `forecast_accounts(session, user_id) -> list[Account]`: `type == "depository"` and `subtype in ("checking", None)`
  - `start_balance(accounts) -> Optional[int]`: sum of `available_balance_cents`, else `current_balance_cents`, per account; `None` for no accounts
  - `everyday_series(session, user_id, today, accounts) -> list[int]`: 90 daily outflow totals, oldest first, from transactions on those accounts only, belonging to no stream (stale ones too), through `spending()`
  - `load_card_obligations(session, user_id) -> list[CardIn]` (a null minimum stays null; a null balance is not needed)
  - `inflow_count(session, user_id, today) -> int`; `history_days(session, user_id, today) -> int`
- `forecast_service.py`: `@dataclass(frozen=True) ForecastSnapshot(result: ForecastResult, buffer_cents: int, history_days: int)`; `snapshot(session, user_id, today: date) -> ForecastSnapshot`: runs `recurring.current_streams` in memory (no writes), accepted goals from `goal_lines.committed_goals`, then `forecast()`.
- `eval/budget_users.py`: `FORECAST_USERS = (forecast-dip,)`. Profile `forecast-dip` (`Profile.id` `forecast-dip`; the user is `budget-forecast-dip`), `start=2026-06-01`, `end=2026-11-30`, `seed=31`, `today=run_date=2026-10-10`, `opening_cents=190000`: `Monthly(RENT_AND_UTILITIES, "Landlord", 150000, day=13)`, `Monthly(INCOME, "Employer", -220000, day=15)`, `Monthly(ENTERTAINMENT, "Streamly", 1549, day=5)`, `Monthly(PERSONAL_CARE, "FitClub Gym", 4000, day=3)`, `Weekly(FOOD_AND_DRINK, "Grocer", 11000)`, `Weekly(TRANSPORTATION, "Fuel", 4000)`. Rent lands 2 days before payday; the trough is the 13th.

- [ ] **Step 1: Write the failing tests**

```python
def test_buffer_defaults_to_100_dollars_and_reads_either_key():
def test_set_user_buffer_rejects_bad_values():                     # -1, 1_000_001, True, "100", 100.5 -> ValueError; 0 and 1_000_000 ok
def test_setting_the_buffer_replaces_the_forecast_dict():
def test_two_checking_accounts_are_summed_and_a_card_purchase_is_ignored():   # third account of type "credit" with a 5000 purchase: not in everyday_series
def test_available_balance_wins_over_current():                    # 90000 vs 100000 -> 90000; available None -> current
def test_a_user_with_no_depository_account_has_no_start_balance():
def test_everyday_series_leaves_out_stream_charges_and_transfers():  # forecast-dip: Landlord, Streamly, FitClub Gym and the paycheck are absent; Grocer and Fuel are in
def test_everyday_series_has_90_entries_ending_today_with_zero_days():
def test_card_obligations_keep_a_null_minimum_null():
def test_snapshot_writes_nothing():                                 # row counts before and after
def test_snapshot_follows_the_date_it_is_given():                   # forecast-dip at 2026-10-10: status ok, dip.date 2026-10-13, cause.name "Landlord", income "paycheck"
def test_the_dip_user_dips_every_month():                           # as of 2026-09-10 and 2026-11-10: dip on the 13th
def test_the_dip_user_has_a_checking_balance_that_moves():          # start_balance on Oct 10 == 190000 - sum(amounts to Oct 10)
```

- [ ] **Steps 2 to 4:** run `tests/test_forecast_service.py tests/test_budget_users.py` to fail, implement, run, then the whole suite. `test_budget_users.py` must still pass unchanged apart from the new tuple being absent from `PROFILES` and `PLANTED`.
- [ ] **Step 5:** `docs/tests/test_forecast_service.md`, `test_budget_users.md`, then

```bash
git commit -m "feat(TPC-31): load forecast inputs from the checking accounts, buffer setting, dip user"
```

---

### Task 3: The Goal Agent uses the forecast

**Files:**
- Modify: `backend/app/agents/goal_graph.py` (`load_facts`, `_summary`, a module flag), `backend/app/budget_engine/goals.py` (`GoalFacts`), `backend/app/privacy/gateway.py` (`_goals`, `GOAL_ASSUMPTIONS`), `backend/app/api/goals.py` (`GoalRunOut`), `backend/tests/conftest.py`
- Test: `backend/tests/test_goal_forecast.py`, `backend/tests/test_gateway.py`, `backend/tests/test_goals_api.py`

**Interfaces:**
- `goal_graph.USE_FORECAST: bool = True`. `load_facts`: calls `snapshot(s, user_id, today)`. `source = "forecast"` when `USE_FORECAST` and `snap.result.surplus_fallback is None`, else `"measured"`. Forecast: `surplus = result.monthly_surplus_cents`, `cap = goal_cap(surplus, result.committed_goal_cents, prefs.max_share, net_of_goals=True)`; no `card_minimums` subtraction. Measured: today's `monthly_surplus(...) - card_minimums` and `goal_cap(..., net_of_goals=False)`. `no_surplus` with a paycheck stream and a surplus at or under 0 is real, not a fallback. `incomplete_card_obligations` stays an error on both paths.
- `_summary` adds `surplus_source`, `surplus_fallback_reason` (or null), `forecast_surplus_cents` and `measured_surplus_cents` (both always), `forecast_as_of`. `GoalFacts.surplus_source: str = "measured"` (defaulted). Gateway `_goals` adds `"surplus_basis"`; the forecast assumption text reads "monthly_surplus is the forecast's average inflow minus scheduled bills, accepted goals and everyday spending". `GoalRunOut` gains `surplus_source: Optional[str] = None`.
- `conftest.py`: an autouse fixture `force_measured_surplus` sets `goal_graph.USE_FORECAST = False` for every test unless the test has `@pytest.mark.forecast_surplus`; register the marker. This keeps the 21 tests that assert exact measured figures (9 in `test_goal_graph.py`, 3 in `test_goal_approval_graph.py`, 8 in `test_goal_validation_graph.py`, 1 in `test_goal_loop.py`) unchanged.

- [ ] **Step 1: Write the failing tests** (all `@pytest.mark.forecast_surplus`, on `goals-card-24`, `TODAY = 2026-11-02`)

```python
def test_a_paycheck_stream_makes_the_forecast_the_source():        # summary surplus_source "forecast", measured_surplus_cents == 97175, abs(forecast_surplus_cents - 97175) / 97175 <= 0.10
def test_the_cap_follows_the_gross_form_around_an_accepted_goal(): # accept a 58300 debt goal first: cap_cents == goal_cap(surplus, 58300, 0.8, net_of_goals=True); with a gross of 100000 and a 50000 goal the cap is 30000
def test_a_balance_of_100_dollars_or_20000_gives_the_same_surplus():
def test_the_card_minimum_is_not_subtracted_twice():              # forecast surplus == snapshot.monthly_surplus_cents exactly
def test_short_history_falls_back_to_the_measured_figure():       # seed until 2026-12-31 minus 60 days; source "measured", reason "short_history", cap as today
def test_no_paycheck_falls_back():                                 # reason "no_inflow_stream"
def test_a_stale_paycheck_falls_back():                            # reason "stale_paycheck"
def test_the_simulated_date_is_used():                             # a run on 2026-12-01 forecasts as of that date
def test_a_paused_budget_run_with_an_empty_summary_does_not_matter(): # the goal run still reports "forecast"
def test_a_surplus_at_or_under_zero_with_a_paycheck_is_no_surplus_not_a_fallback():
# test_gateway.py
def test_goal_context_names_its_surplus_basis():                   # "forecast" and "measured"; no balances or merchant names
def test_existing_goal_facts_tests_still_pass_with_the_defaulted_field():
# test_goals_api.py
def test_run_out_reports_the_surplus_source():
# unchanged: test_goal_graph.py, test_goal_approval_graph.py, test_goal_validation_graph.py, test_goal_loop.py (they run on the measured path)
```

- [ ] **Step 2: Run them to verify they fail**, **Step 3: Implement**, **Step 4:** run the new file, then the four listed goal files **unchanged** (21 exact-number tests must still pass), then the whole suite.
- [ ] **Step 5:** `docs/tests/test_goal_forecast.md`, `test_gateway.md`, `test_goals_api.md`; mention in `test_goal_graph.md` that the file runs on the measured path through `force_measured_surplus`. Commit:

```bash
git commit -m "feat(TPC-31): goals draw on the forecast surplus, with the measured one as the fallback"
```

---

### Task 4: `forecast_cash` and the low-balance alert

**Files:**
- Create: `backend/alembic/versions/<rev>_low_balance_signal.py` (down revision is the TPC-33 migration; confirm with `../venv/bin/alembic heads`), `backend/tests/test_forecast_graph.py`
- Modify: `backend/app/db/models.py` (`Alert.signal_cents`, `Alert.signal_days`), `backend/app/agents/budget_graph.py`, `backend/app/agents/budget_llm.py`, `backend/app/budget_engine/signals.py`, `backend/app/privacy/gateway.py` (`cash_flow`), `backend/tests/test_migrations.py`, `backend/tests/test_signals_engine.py`

**Interfaces:**
- Migration: `alerts.signal_cents` (int, nullable, the shortfall) and `alerts.signal_days` (int, nullable, days ahead at alert time).
- Graph: node `forecast_cash` after `detect_recurring` on both routes, deterministic, wrapped like `detect_recurring` (failure into `signals_error`). It calls `snapshot`; if `result.dip` exists and `repeat_decision(...)` allows, appends `Signal("low_balance", key, None, None, numbers)` with `numbers = {date, days_ahead, shortfall_cents, buffer_cents, bill (scrubbed merchant or "credit card minimum"), low_below_zero}`. `persist_state` adds `as_of` (already) and `forecast: {ok, low_balance: {date, shortfall_cents}, monthly_surplus_cents, surplus_source, committed_goals_cents, assumed_no_income}`: cents and dates only, no names.
- `signals.py` adds: `repeat_decision(previous: Optional[PreviousDip], dip_date: date, shortfall_cents: int, days_ahead: int) -> bool` where `PreviousDip(date: date, shortfall_cents: int, days_ahead: int)` comes from the user's latest alert with the same bill; `low_balance_key(bill_key: str, dip_date: date) -> str` = `f"low_balance|{bill_key}|{dip_date.isoformat()}"`; `move_amount(shortfall_cents: int, buffer_cents: int) -> int` = `shortfall + buffer` rounded up to the next 500. `allowed_actions` for `low_balance` is `alert`, `delay_spend`, `move_from_savings`; `choose` downgrades `move_from_savings` with no savings account that covers `move_amount` or with `assumed_no_income`, and `delay_spend` with no budget or no flexible category at pace 1.0 or more. `fallback_message(low_balance)` names the bill, date, shortfall.
- `decide_signals` for `low_balance`: if the model's message does not contain `numbers["bill"]`, use `fallback_message`. Severity `high` when the low path goes under $0, else `medium`. The alert row: `kind="low_balance"`, `category=None`, `signal_key`, `signal_cents`, `signal_days`; a pending `move_from_savings` or `delay_spend` proposal is a plain alert, nothing moves, the run does not pause.
- Gateway purpose `cash_flow` with a `CASH_FIELDS` allowlist: dip date, days ahead, shortfall, buffer, the bill label, other events as category and amount, whether savings cover it (yes or no) and the amount code proposes, flexible categories ahead of pace (name and dollars). No account names; no balance beyond shortfall and "starting available" rounded to dollars.

- [ ] **Step 1: Write the failing tests**

```python
# test_signals_engine.py
def test_repeat_rule():            # prev (Oct 13, 5000, 5): 5000 again -> False; 6250 (+$12.50) -> False; 7000 -> True; days_ahead 2 after prev 5 -> True; days_ahead 2 after prev 2 -> False
def test_a_dip_that_slides_later_by_3_days_does_not_repeat_and_by_4_does():
def test_move_amount():            # (5000, 10000) -> 15000; (9700, 10000) -> 20000
def test_move_from_savings_is_downgraded_without_cover_or_with_no_income():
def test_delay_spend_is_downgraded_without_a_budget():
# test_forecast_graph.py  (forecast-dip, scripted model)
def test_the_dip_alert_names_rent_and_fires_at_least_3_days_ahead():   # day by day from 2026-09-30: a low_balance alert exists by the Oct 10 run; its message contains "Landlord"; signal_key ends "|2026-10-13"; signal_days >= 3
def test_the_same_dip_on_three_days_alerts_once():
def test_a_deeper_dip_alerts_again_and_a_small_change_does_not():  # buffer unchanged: balance lowered by 2000 -> +25%/+$20 rule decides
def test_a_dismissed_dip_stays_quiet_unless_it_grows():
def test_a_model_message_without_the_bill_is_replaced():
def test_severity_is_high_below_zero_and_medium_under_the_buffer():
def test_a_user_with_no_budget_gets_the_alert_with_no_period():
def test_a_user_with_no_paycheck_stream_only_gets_an_alert():     # model returns move_from_savings -> stored action "alert"; the alert text says no income is assumed
def test_a_forecast_failure_does_not_stop_overspend_alerts():     # summary signals_error
def test_raising_the_buffer_leaves_a_pending_alert_pending():
def test_summary_forecast_holds_cents_and_dates_only():
def test_a_paused_checkpoint_holds_no_bill_name_or_balance():     # plant "ZZ-UNIQUE-LANDLORD" and balance 424242; pause on an adjust_next_budget; grep the checkpoint -> absent
def test_a_normal_day_adds_no_model_call():
# test_gateway.py
def test_cash_flow_context_has_no_account_names_or_balances():
# test_migrations.py: signal_cents and signal_days are columns of alerts; downgrade removes them
```

- [ ] **Steps 2 to 4:** run to fail, implement, run `tests/test_forecast_graph.py tests/test_signals_engine.py tests/test_recurring_graph.py tests/test_budget_graph.py`, then the suite. TPC-33's graph tests must pass unchanged.
- [ ] **Step 5:** `docs/tests/test_forecast_graph.md`, `test_signals_engine.md`, `test_gateway.md`, `test_migrations.md`. Commit:

```bash
git commit -m "feat(TPC-31): low-balance alert that names the bill, once per dip"
```

---

### Task 5: `GET /forecast`, the buffer route and the chat tool

**Files:**
- Create: `backend/app/api/forecast.py`, `backend/tests/test_forecast_api.py`
- Modify: `backend/app/main.py`, `backend/app/agents/tools.py`, `backend/app/agents/chat_graph.py:17-25`, `backend/tests/test_chat_graph.py`, the chat eval scenarios

**Interfaces:**
- `GET /forecast?days=` (`Query(default=None, ge=1, le=90)`): `today = sim_today(s, user.id) or _today()`; calls `snapshot`; maps to `ForecastOut` per the spec's API section (`days` holds `today+1 .. horizon` truncated to `days`; `events[].kind` is `bill`, `card_min`, `income` or `goal`; `dip.bill_label`; `suggested_extra_cents = goal_cap(surplus, committed, max_share, net_of_goals=True)` or null; `notes` code-written, e.g. "No paycheck found, so no income is assumed."). `no_checking` and `no_history` are 200. Writes nothing.
- `PUT /forecast/buffer` `{buffer_cents}` -> `{buffer_cents, default_cents: 10000}`; 422 "The buffer is $0 to $10,000." on `ValueError`.
- Chat tool `get_cash_forecast()`, no arguments, through the gateway purpose `cash_flow`. Tool count 6 to 7. Prompt: add the tool, "state the forecast as a range" and "say when income is assumed to be none".

- [ ] **Step 1: Write the failing tests**

```python
def test_forecast_for_the_dip_user():                              # as of 2026-10-10: status "ok", income "paycheck", dip.date "2026-10-13", dip.bill_label "Landlord", buffer_cents 10000, every day low_cents <= expected_cents <= high_cents
def test_days_param_bounds():                                      # days=0 and days=91 -> 422; days=7 -> 7 entries
def test_no_checking_account_is_200_with_an_empty_path():          # start_cents None, days []
def test_no_history_is_200():
def test_no_paycheck_says_so():                                    # income "none", monthly_surplus_cents None, a note naming "no income"
def test_the_get_writes_nothing():
def test_a_simulated_user_gets_the_simulated_date():
def test_another_users_data_is_not_visible():
def test_buffer_put_rejects_bad_values():                          # -1, 1000001, True, "100", 100.5 -> 422
def test_buffer_put_persists_and_the_get_echoes_it():              # 25000
def test_a_pending_alert_survives_a_buffer_change():
def test_suggested_extra_uses_the_goal_cap():
def test_requires_auth():
# test_chat_graph.py
def test_chat_binds_seven_tools():                                 # get_recurring and get_cash_forecast among them; the prompt names both
def test_get_cash_forecast_returns_a_range_and_no_account_names():
```

- [ ] **Steps 2 to 4:** run to fail, implement, run. Add chat-eval scenarios: "will I run short before payday" expects `get_cash_forecast`. Live tool choice with 7 tools on `qwen2.5:14b` is not run here; say so.
- [ ] **Step 5:** `docs/tests/test_forecast_api.md`, `test_chat_graph.md`, then

```bash
git commit -m "feat(TPC-31): GET /forecast, the buffer setting and the get_cash_forecast chat tool"
```

---

### Task 6: The Cash forecast card, buffer input, Goals page and debt hint

**Files:**
- Create: `frontend/components/ForecastChart.tsx`, `frontend/components/ForecastChart.test.tsx`
- Modify: `frontend/lib/api.ts`, `frontend/app/dashboard/page.tsx`, `frontend/app/dashboard/page.test.tsx`, `frontend/app/goals/page.tsx`, `frontend/app/goals/page.test.tsx`, `frontend/components/DebtPlanCompare.tsx`, `frontend/components/DebtPlanCompare.test.tsx`

**Interfaces:**
- `api.ts`: `Forecast`, `ForecastDay`, `ForecastEvent`, `getForecast(days?: number)`, `setForecastBuffer(cents: number)`; `GoalRun.surplus_source`.
- `ForecastChart({ data, onSaveBuffer }: { data: Forecast; onSaveBuffer: (cents: number) => Promise<void> })`: recharts `ComposedChart` in `ResponsiveContainer width="100%" height={240}`: `Area dataKey="band"` (low to high, 20% opacity), `Line dataKey="expected"`, dashed `ReferenceLine y={buffer}`, a `ReferenceDot` per bill or card-minimum event (label only on the dip's bill at 375 px), `accessibilityLayer`, wrapped in `role="img"` with an `aria-label` built from the data ("Cash forecast, Oct 11 to Nov 9. Lowest likely balance $5.00 on Oct 13; buffer $100.00."); `minTickGap` and `$k` ticks so a 343 px card has no horizontal scroll. Headline "between $X and $Y on <date>". A `<details><summary>Show as a table</summary>` with columns Date, Low, Expected, High, Events. Statuses: `no_checking` "Link a checking account to see your forecast."; `no_history` "We need about a month of transactions to forecast."; `income === "none"` shows "No paycheck found, so no income is assumed." Below: "Keep at least $" input and Save (client check 0 to 10,000 with "The buffer is $0 to $10,000."), then refetch.
- Dashboard: a "Cash forecast" card above Recurring charges, with its own `forecast` and `forecastError` state; a failure stays in the card. Goals page: after a run, "Based on your cash-flow forecast." or "Based on your last 3 months (no paycheck pattern found yet)."; the `no_surplus` copy no longer says "in a typical month". `DebtPlanCompare`: "You could add about $X" under the extra input from `suggested_extra_cents`; it does not change the input.

- [ ] **Step 1: Read the Next.js docs the page uses** (`frontend/node_modules/next/dist/docs/`), then write the failing tests

```tsx
// ForecastChart.test.tsx  (recharts is mocked: jsdom gives ResponsiveContainer zero size)
it("headlines a range, never the expected value alone")           // "between $5.00 and $412.00 on Oct 13"
it("labels the chart with the lowest likely balance and the buffer")
it("lists every day in the table and names the dip's bill")       // "Landlord"
it("explains no_checking, no_history and no income")
it("rejects a buffer outside 0 to 10,000 before calling the api") // "The buffer is $0 to $10,000."
it("saves a valid buffer and refetches")                          // setForecastBuffer(25000)
// page.test.tsx (add getForecast to the mock list and beforeEach; mock ForecastChart as () => null)
it("shows the Cash forecast card")
it("keeps a forecast failure inside its card")                     // Recurring and Goals still render
// goals/page.test.tsx
it("says which surplus a run used")                                // both sentences
// DebtPlanCompare.test.tsx
it("shows the suggested extra without changing the input")         // "You could add about $230.00"; input still "0"
```

- [ ] **Steps 2 to 4:** `npm test -- components/ForecastChart.test.tsx app/dashboard/page.test.tsx app/goals/page.test.tsx components/DebtPlanCompare.test.tsx` to fail, implement, then `npm test && npm run lint && npm run build`. Check the 375 px layout in the browser pane and record it; if it cannot be checked, say so in the handoff.
- [ ] **Step 5:** `docs/tests/ForecastChart.test.md`; update `dashboard-page.test.md`, `goals-page.test.md`, `DebtPlanCompare.test.md`. Commit:

```bash
git commit -m "feat(TPC-31): Cash forecast card with the buffer setting, source line on Goals, suggested extra"
```

---

### Task 7: The forecast eval

**Files:**
- Create: `eval/forecast_accuracy.py`, `backend/tests/test_forecast_accuracy.py`, `docs/tests/test_forecast_accuracy.md`

**Interfaces:**
- `balance_on(profile, day: date) -> int`: `opening_cents` minus the sum of the profile's amounts dated on or before `day` (the profile is the ground truth).
- `run(profiles, out_root, day) -> Path`: for each profile in `FORECAST_USERS` and each replay day `d` from `profile.start + 90 days` to `profile.end - 14 days`, `seed_profile(s, profile, until=d)` into a `temp_db()`, call `snapshot(s, user, d)`, compare `expected_cents` at `d+7` and `d+14` with `balance_on(profile, d+h)`. Per profile and pooled: `n`, `mae_d7_cents`, `mae_d14_cents`, `coverage_d7`, `coverage_d14` (actual inside `[low, high]`), `mean_band_width_cents`, the naive baseline (balance stays flat) `baseline_mae_d7_cents` and `baseline_mae_d14_cents`, and the dip check: `dip_lead_days` (days between the first replay day whose snapshot has a `dip` and the dip date, Oct 13), `dip_bill` and `dip_pass` (`lead >= 3` and bill `"Landlord"`). Prints a table, writes `eval/results/<day>/forecast_accuracy/results.json`; `main() -> int` returns 0. Offline, no model. Run: `venv/bin/python eval/forecast_accuracy.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_planted_dip_is_found_at_least_3_days_ahead_and_names_rent():    # dip_pass True, dip_lead_days >= 3, dip_bill "Landlord"
def test_balance_on_matches_the_seeded_account():                           # balance_on(profile, d) == the seeded current_balance_cents for until=d
def test_mae_is_finite_and_coverage_is_a_share():                           # 0 <= coverage <= 1
def test_the_forecast_beats_a_flat_balance_at_d14_on_the_dip_user():        # mae_d14_cents <= baseline_mae_d14_cents
def test_results_json_has_the_documented_keys():
def test_only_forecast_users_are_scored():                                  # PROFILES and PLANTED are not in the table
def test_existing_budget_accuracy_numbers_are_unchanged():                  # steady 5.8/4.0 ... pooled 7.1 / 17.7 as in the TPC-33 baseline run
```

Absolute MAE and coverage are reported, not thresholded (departure 9; measured coverage on stand-in prototypes was 41% to 53%).

- [ ] **Steps 2 to 4:** run to fail, implement, run; then run the script and paste the table into the commit body and the handoff.
- [ ] **Step 5:** commit

```bash
git commit -m "feat(TPC-31): forecast error, band coverage and the planted dip in the eval"
```

---

### Task 8: The done-when, docs and handoff

**Files:**
- Create: `backend/tests/test_forecast_loop.py`, `docs/tests/test_forecast_loop.md`, `docs/handoffs/2026-11-02-tpc-31-manual-verification.md`
- Modify: `docs/architecture.md`, `README.md`, `docs/agent-pipelines.md`, `docs/system-walkthrough.md`, `docs/diagrams/budget-agent-loop.puml`, `docs/diagrams/evaluation-flow.puml`, `docs/diagrams/data-model.puml`, `backend/tests/test_m3_demo.py`, the spec's status line

- [ ] **Step 1: Write the failing end-to-end tests**

Through the HTTP API on `forecast-dip`, a scripted model, `advance_days` one day at a time from 2026-09-30.

```python
def test_rent_before_payday_alerts_at_least_3_days_ahead_and_names_rent():   # a low_balance alert exists by the Oct 10 run, none stored for dip date Oct 13 twice; message contains "Landlord"; GET /alerts shows kind "low_balance"
def test_the_alert_does_not_repeat_through_payday():                          # still one alert on Oct 16
def test_the_next_months_dip_alerts_once_more():                              # Nov 13 dip -> one more alert, different signal_key
def test_the_chart_data_shows_the_band_under_the_buffer_at_the_rent_marker(): # GET /forecast on Oct 10: the Oct 13 day has low_cents < buffer_cents, an event "Landlord"
def test_raising_the_buffer_redraws_the_forecast_and_keeps_the_alert():       # PUT 25000; GET /forecast buffer_cents 25000; the existing alert is still pending
def test_goals_use_the_forecast_and_say_so():                                 # goals-card-24 end to end: the run response surplus_source "forecast"; accept a goal; generate again: cap follows goal_cap on the gross form
def test_a_user_with_no_paycheck_gets_no_dip_alert_other_than_alert():
```

- [ ] **Step 2: Run, fix what they find.** A failure means an earlier task's seam is wrong: fix it there and add the missing test.
- [ ] **Step 3: Update the docs.** `architecture.md`: 6a.2 as built (the TPC-31 rows of the departures table), 6a.3 (the debt planner's suggested extra; the `extra_payment` proposal deferred), 6a.5 (the known limit at the cap is resolved on the forecast path), section 5 (`alerts.signal_cents`, `signal_days`), section 9 (`GET /forecast`, `PUT /forecast/buffer`), section 10.2 (MAE, coverage, the planted dip, as measured), section 16 (a decision-log row per spec decision). `README.md`: a forecast section and the buffer. `docs/system-walkthrough.md:29`. `agent-pipelines.md` and the `.puml` files. Re-render PNG and SVG only if the tool that made them is available here; otherwise say so. `docs/tests/`: confirm every new and changed test has its entry.
- [ ] **Step 4: Write the handoff.** `docs/handoffs/2026-11-02-tpc-31-manual-verification.md`, in the format of the TPC-25 handoff: a disposable database, `scripts/seed_budget_user.py --profile forecast-dip --replace --start 2026-09-30` so the user is simulated, then `scripts/simulate.py --user budget-forecast-dip --days 12`. Checks: the Cash forecast card shows the band dipping under the buffer line at the rent marker; the alert appears by Oct 10 and names rent; raising the buffer and saving redraws the card and leaves the alert pending; advancing past payday adds no alert for the same dip; a user with no paycheck sees the no-income note; the Goals page says which source a run used; `DebtPlanCompare` shows the hint; the chart has no horizontal scroll at 375 px; `venv/bin/python eval/forecast_accuracy.py` table recorded. Say what was not run (live chat tool choice, Postgres, real Plaid balances).
- [ ] **Step 5: Run everything.** `../venv/bin/python -m pytest -q -k 'not live and not sandbox'`, then from `frontend/`: `npm test && npm run lint && npm run build`. Record the counts in the handoff.
- [ ] **Step 6: Commit** (only these files)

```bash
git commit -m "docs(TPC-31): forecast end to end, docs and manual verification"
```
