# TPC-33 Recurring Streams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect repeating charges and paychecks in our own transactions, alert on new subscriptions, price increases and income changes, show them on the dashboard and in chat, and let an accepted `suggest_cancel` lower next month's budget line.

**Architecture:** A pure detector in `budget_engine/recurring.py` feeds a `recurring_streams` table that the daily run refreshes and diffs. Stream changes become `Signal`s, decided by a new `decide_signals` node that never pauses the run. `GET /recurring` reads the stored rows. Task 1 and 2 build the data and the detector and end at an acceptance gate; nothing after them starts until it passes. TPC-31 reuses the detector, `project`, the seeding, `Signal` and the node routing built here.

**Tech Stack:** Python, FastAPI, SQLAlchemy, Alembic, LangGraph 1.2.18, pytest, Next.js 16, React, vitest.

**Spec:** `docs/superpowers/specs/2026-11-02-tpc-33-31-recurring-and-forecast-design.md`. The user must have approved its "Departures from the tickets" table before Task 1 starts.

## Global Constraints

- Work on branch `TPC-33`, already cut from `TPC-25` (PR #23). Don't switch branches. When #23 merges, `git rebase --onto origin/main TPC-25`.
- The TPC-33 PR description must not mention TPC-31, and no commit message may name a ticket other than TPC-33: Jira closed TPC-33 seven seconds after three merged PRs that only mentioned it (see the index handoff).
- Money is integer cents. Dates passed to every function are explicit (`today`), never `date.today()`; synthetic users follow `sim_today`.
- Commit messages carry no `Co-Authored-By` or "Generated with Claude Code" lines (the user's standing rule for this repo).
- Every new or changed test gets an entry in `docs/tests/<test file>.md`, in that folder's existing format (header, run command, shared setup, one section per test).
- Frontend: read the relevant guides in `frontend/node_modules/next/dist/docs/` before writing page code (`frontend/AGENTS.md`).
- Don't commit `eval/results/2026-10-29/` or `eval/results/2026-10-31/`; they aren't part of this work. Add files by name, never `git add -A`.
- Match the surrounding style: lowercase `#` comments that explain why, code about as dense as its neighbours.
- Backend commands run from `backend/`; the fast suite is `../venv/bin/python -m pytest -q -k 'not live and not sandbox'`. Frontend commands run from `frontend/`: `npm test`, `npm run lint`, `npm run build`.
- The last task writes `docs/handoffs/2026-11-02-tpc-33-manual-verification.md` and updates `docs/architecture.md`.

## Review Focus

1. **The gate (Task 2):** the noisy profile yields at most 1 false stream, and Grocer, Fuel, Corner Shop and Airline are never streams. Everything downstream trusts this.
2. **First run flood and replays:** a user's first run raises no `new_subscription`; a second run the same day raises nothing; a stream that returns from `stale` is silent. Pinned in Tasks 3 and 4.
3. **Stream flags beside overspend flags:** same category, same day, two alerts of the right kinds, no `KeyError`. A detector failure never stops overspend alerts. Pinned in Task 4.
4. **Checkpoint:** a paused run holds no bill name or amount from a signal. Pinned in Task 4.
5. **`suggest_cancel` at close:** applies after `adjust_next_budget`, never below $0, never on a missing line, and mutes the stream. Pinned in Task 5.

---

### Task 1: Pattern classes, the noisy profile and balances in the seed

**Files:**
- Modify: `eval/budget_users.py`, `backend/app/budget_service/simulate.py` (`_inject`), `backend/scripts/seed_budget_user.py`, `backend/scripts/simulate.py` if it lists profiles
- Test: `backend/tests/test_budget_users.py`, `backend/tests/test_simulate.py`

**Interfaces:**
- Produces, in `eval/budget_users.py` (all new fields defaulted so existing profiles generate identical lists):
  - `Every(category: str, merchant: str, cents: int, days: int, first: date, last: Optional[date] = None, steps: tuple[tuple[date, int], ...] = ())`
  - `Scatter(category: str, merchants: tuple[str, ...], rate: float, per_day: tuple[int, int], cents: tuple[int, int])`: on each day, with probability `rate`, 1 to `per_day[1]` purchases at random merchants, seeded by `Profile.seed`
  - `Monthly` gains `last: Optional[date] = None` and `steps: tuple[tuple[date, int], ...] = ()` (from each date, the amount is the step's cents)
  - `Profile.opening_cents: Optional[int] = None`: balance on `Profile.start`
  - `transactions()` has an explicit branch per class and raises `TypeError` for an unknown one
  - tuples `RECURRING_USERS = (recurring-planted, recurring-noisy)`
- Produces: `seed_profile(..., until)` creates the checking account with `subtype="checking"` and `current_balance_cents == available_balance_cents == opening_cents - sum(amounts dated <= until)` when `opening_cents` is set; unchanged otherwise (`subtype=None`, 10000, `available` unset).
- Produces: `_inject` subtracts each injected amount from that account when the profile has `opening_cents` (amounts are Plaid-signed: positive is out).
- Profile `recurring-planted` (`Profile.id` `recurring-planted`; the user is `budget-recurring-planted`), `start=2026-05-01`, `end=2026-11-30`, `today=run_date=2026-10-25`: `Monthly(RENT_AND_UTILITIES, "Landlord", 150000, day=1)`; `Every(INCOME, "Employer", -185000, 14, first=2026-05-01)`; `Monthly(ENTERTAINMENT, "Streamly", 1549, day=5)`; `Monthly(PERSONAL_CARE, "FitClub Gym", 4000, day=3, steps=((2026-10-03, 4300),))` (+7.5%); `Monthly(ENTERTAINMENT, "Clipflix", 1549, day=20, steps=((2026-10-20, 1799),))` (+16%, documents departure 1); `Monthly(ENTERTAINMENT, "NewFlix", 999, day=12, first=2026-08-12)` (its third charge is Oct 12); two `Once(GENERAL_SERVICES, "Pottery Studio", 6500, ...)` on Aug 10 and Sep 9; `Weekly` Grocer and Fuel.
- Profile `recurring-noisy` (`Profile.id` `recurring-noisy`), `start=2026-01-01`, `end=2026-10-31`, `today=run_date=2026-10-20`, `seed=33`: planted rent (day 1), phone `Monthly(RENT_AND_UTILITIES, "Mobile Co", 6500, day=9)`, gym `Monthly(PERSONAL_CARE, "FitClub Gym", 4000, day=3)`, `Every(INCOME, "Employer", -185000, 14, first=2026-01-02)`, and three `Scatter` patterns, one each for `FOOD_AND_DRINK`, `GENERAL_MERCHANDISE` and `TRANSPORTATION`, with five distinct merchants each, `rate=0.2`, `per_day=(1, 3)`, `cents=(300, 4500)` (about 49% of days have a purchase).

- [ ] **Step 1: Write the failing tests**

```python
# test_budget_users.py
HASHES = {"steady": "c46034002c6f", "vacation": "1f32f65b528d", "early-rent": "f54a9cbf30f1", "rising": "dfb7a42a181d",
          "occasional": "5cffd523cfcd", "overspend-one-time": "50d7cc005186", "overspend-recurring": "7ebed8d37d7c",
          "overspend-seasonal": "a9e3ec12934c", "overspend-known-merchant": "056ac49b1a45", "goals-card-24": "2bcfb8ca8436"}
def test_new_fields_leave_existing_profiles_identical():
    for p in (*PROFILES, *PLANTED, *GOAL_USERS):          # keyed by Profile.id; transactions() returns (date, cents, category, merchant) tuples
        assert sha256(repr(transactions(p)).encode()).hexdigest()[:12] == HASHES[p.id]

def test_every_generates_biweekly_dates():
    p = replace(PROFILES[0], patterns=(Every("INCOME", "Employer", -185000, 14, date(2026, 5, 1)),), start=date(2026, 5, 1), end=date(2026, 6, 12))
    assert [(t[0], t[1]) for t in transactions(p)] == [(date(2026, 5, 1), -185000), (date(2026, 5, 15), -185000), (date(2026, 5, 29), -185000), (date(2026, 6, 12), -185000)]

def test_monthly_steps_change_the_amount_from_a_date():     # Sep 3 is 4000, Oct 3 is 4300
def test_monthly_last_stops_the_pattern():
def test_unknown_pattern_class_raises_type_error():          # pytest.raises(TypeError)
def test_scatter_is_repeatable_and_stays_inside_its_ranges():  # same seed, same list; every amount in 300..4500
def test_opening_balance_is_applied_up_to_until():
    # profile with opening_cents=190000: balance == 190000 - sum(amounts dated <= until); account.subtype == "checking"
    # and available_balance_cents == current_balance_cents
def test_profiles_without_opening_keep_the_old_account():     # subtype None, current 10000, available None
def test_recurring_users_are_not_in_the_accuracy_tuples():    # ids absent from PROFILES and PLANTED
# test_simulate.py
def test_inject_moves_the_checking_balance():                 # opening profile: injected [4500, -420000] changes balance by +415500; a profile without opening: still 10000
```

- [ ] **Step 2: Run them to verify they fail**

Run: `../venv/bin/python -m pytest tests/test_budget_users.py tests/test_simulate.py -q -k 'not live'`
Expected: FAIL (new classes undefined). `test_new_fields_leave_existing_profiles_identical` guards behaviour that must stay: if it fails after Step 3, the new fields changed an old profile.

- [ ] **Step 3: Implement**

Add the classes and fields, the explicit branches in `transactions()`, the balance in `seed_profile`, and the `_inject` update (read the profile through the same lookup `simulate.py:83` already uses; add `RECURRING_USERS` to it and to `seed_budget_user.py:33,66,94`, including its "run it" hint).

- [ ] **Step 4: Run, then the whole backend suite**

Expected: PASS. The existing `test_budget_users.py` assertions on `PROFILES` ids and `PLANTED` stay untouched.

- [ ] **Step 5: Tests doc and commit**

Update `docs/tests/test_budget_users.md` and `docs/tests/test_simulate.md`.

```bash
git add eval/budget_users.py backend/app/budget_service/simulate.py backend/scripts backend/tests/test_budget_users.py backend/tests/test_simulate.py docs/tests/test_budget_users.md docs/tests/test_simulate.md
git commit -m "test(TPC-33): biweekly pay, price steps, noisy purchases and a moving checking balance in the synthetic users"
```

---

### Task 2: The detector, and the gate

**Files:**
- Create: `backend/app/budget_engine/recurring.py`, `eval/recurring_accuracy.py`, `backend/tests/test_recurring_engine.py`, `backend/tests/test_recurring_accuracy.py`
- Test docs: `docs/tests/test_recurring_engine.md`, `docs/tests/test_recurring_accuracy.md`

**Interfaces** (spec "Design": detector). All pure, no DB, no clock.

```python
Frequency = Literal["weekly", "biweekly", "semi_monthly", "monthly"]          # "annual" is never produced
EVERYDAY_CATEGORIES = frozenset({"FOOD_AND_DRINK", "GENERAL_MERCHANDISE", "TRANSPORTATION", "PERSONAL_CARE", "ENTERTAINMENT", "TRAVEL"})
@dataclass(frozen=True)
class RTxn:    date: date; amount_cents: int; merchant: Optional[str]; category: str; detailed: Optional[str]
@dataclass(frozen=True)
class Stream:  direction: Literal["in", "out"]; merchant_key: str; merchant_name: str; category: str; detailed: Optional[str]
               frequency: Frequency; typical_cents: int; last_cents: int; previous_cents: Optional[int]; amount_varies: bool
               first_date: date; last_date: date; next_date: date; gap_days: int; anchors: tuple[int, ...]; n: int
               status: Literal["active", "stale"]
def merchant_key(name: Optional[str]) -> Optional[str]       # lowercase, digits and punctuation removed, spaces collapsed; None for None, "" or all digits
def detect(txns: Sequence[RTxn], today: date, *, window_days: int = 180) -> list[Stream]
def project(stream: Stream, through: date) -> list[date]     # next_date, then each cadence step up to and including `through`
def monthly_cents(typical_cents: int, frequency: Frequency) -> int   # monthly x1, semi_monthly x2, biweekly x26/12, weekly x52/12, rounded
```

Rules: spec "Design". `anchors` holds the day-of-month (monthly: one, the median day of the last 3 charges; semi-monthly: two). `project` steps monthly and semi-monthly streams from the anchors, clamped to month end, so Feb 28 does not drag a 31st bill to the 28th.

- [ ] **Step 1: Write the failing engine tests**

```python
def test_two_visits_make_no_stream():                          # gym Sep 3, Oct 3 -> []
def test_three_monthly_charges_make_a_stream():                # Streamly 1599 on Aug 5, Sep 5, Oct 5; today Oct 20
    # (frequency, n, typical_cents, last_cents, amount_varies, next_date, status) == ("monthly", 3, 1599, 1599, False, date(2026, 11, 5), "active")
def test_gaps_of_5_and_80_days_make_no_stream():
def test_next_date_uses_the_median_day_of_the_month():         # Jul 31, Aug 31, Sep 30 -> anchors (31,), next_date Oct 31
def test_a_weekend_shifted_bill_keeps_its_day():               # Aug 3, Sep 1, Oct 1 (the first was shifted) -> anchors (1,), next Nov 1
def test_biweekly_pay():                                       # six -185000 from May 1, every 14 days -> biweekly, gap_days 14, typical 185000, direction "in"
def test_one_weekday_means_biweekly_and_two_anchors_mean_semi_monthly():   # Fridays 14 days apart -> biweekly; Jun 1, Jun 15, Jul 1, Jul 15, Aug 1 -> semi_monthly, anchors (1, 15)
def test_price_rise_of_6_7_percent_keeps_the_stream_fixed():   # 3000 x3 then 3200 -> previous_cents 3000, last_cents 3200, amount_varies False
def test_price_rise_of_15_percent_keeps_the_stream_fixed():    # 3000 x3 then 3450 on the cadence -> typical 3000, last 3450, previous 3000, amount_varies False
def test_second_charge_at_the_new_price_is_not_a_rise():       # 3000, 3000, 3450, 3450 -> previous_cents is None
def test_a_one_off_extra_is_dropped():                         # 12000 x4 monthly plus 52000 on Oct 12 -> n 4, typical 12000, last 12000
def test_a_small_extra_between_bills_is_dropped():             # 1599 monthly plus 500 mid-month -> last_cents 1599, n 3
def test_one_offs_a_month_apart_are_not_a_stream():            # Airline 20000 (May 12), 120000 (Jun 10), 25000 (Jul 12)
def test_a_utility_with_varying_amounts_is_a_stream():         # RENT_AND_UTILITIES 8000, 11500, 9400, 10200 -> amount_varies True, typical 10200
def test_weekly_food_with_fixed_amounts_is_not_a_stream():     # 13 x FOOD_AND_DRINK 11000 every 7 days
def test_a_weekly_service_with_fixed_amounts_is_a_stream():    # 6 x GENERAL_SERVICES 6000 -> weekly, n 6
def test_a_weekly_outflow_with_varying_amounts_is_not():
def test_a_weekly_gap_that_slips_by_3_days_is_not_a_stream():  # gaps 7, 7, 10, 7
def test_a_cafe_visited_at_random_is_not_a_stream():           # gaps 9, 9, 8, 10 in FOOD_AND_DRINK
def test_charges_after_today_and_before_the_window_are_ignored():
def test_a_null_merchant_paycheck_is_a_stream():               # 3 INCOME_WAGES credits, merchant None -> key "~INCOME_WAGES"
def test_null_merchant_restaurant_charges_are_not():           # 12 purchases
def test_raw_descriptors_share_a_key():                        # merchant_key("NETFLIX.COM 8443") == merchant_key("Netflix.com 1123") == "netflixcom"; "7-Eleven" -> "eleven"; "12345", "", None -> None
def test_paycheck_goes_stale_after_65_days():                  # last May 15 monthly: Jun 20 and Jul 19 active, Jul 20 stale
def test_a_roommate_transfer_is_an_inflow_stream_but_not_income():   # TRANSFER_IN x3 monthly -> direction "in", category "TRANSFER_IN"
def test_a_refund_is_its_own_direction():                      # a negative Netflix charge leaves the outflow stream unchanged
def test_project_clamps_a_31st_bill():                         # anchors (31,), next_date Jan 31 2027 -> [Jan 31, Feb 28, Mar 31, Apr 30] through Apr 30
def test_project_semi_monthly_uses_both_anchors():             # anchors (1, 15), next_date Jun 15 -> [Jun 15, Jul 1, Jul 15] through Jul 15
def test_monthly_cents_normalises():                           # (1000, "biweekly") -> 2167; (1000, "semi_monthly") -> 2000; (1000, "weekly") -> 4333
```

And the accuracy script and its test:

`eval/recurring_accuracy.py`: `planted_streams(profile, as_of) -> set[tuple[str, str, str]]` (direction, `merchant_key`, frequency) from `Monthly` and `Every` patterns (and `Once` patterns are never streams, `Weekly` and `Scatter` are everyday) with 3 or more occurrences in the 180 days before `as_of`; `run(profiles, out_root, day) -> Path` seeds each into `temp_db()`, runs `detect(load_rtxns(...))` on `as_of = profile.run_date`, prints precision, recall, false positives and misses by name per profile and pooled, writes `eval/results/<day>/recurring_accuracy/results.json`; `main() -> int` returns 0. Same shape as `eval/budget_accuracy.py`. `load_rtxns` does not exist yet: this task builds the profile's `RTxn`s from `transactions(profile)` filtered to `date <= as_of`, with categories as planted.

```python
# test_recurring_accuracy.py: the gate
def test_planted_users_are_found_exactly():          # recurring-planted at 2026-10-25: precision == 1.0 and recall == 1.0; Pottery Studio absent; Streamly, Landlord, Employer (biweekly), FitClub Gym, Clipflix, NewFlix present; Grocer and Fuel absent
def test_the_noisy_profile_has_at_most_one_false_stream():     # recurring-noisy at 2026-10-20: len(false_positives) <= 1 and recall == 1.0
def test_the_noisy_profile_keeps_its_everyday_spending():      # days in the last 90 with a purchase that belongs to no detected stream >= 0.85 x days left when only the planted streams are removed
def test_every_profile_scores_at_least_0_95():                 # pooled over PROFILES, PLANTED, GOAL_USERS, RECURRING_USERS: precision >= 0.95 and recall >= 0.95
def test_weekly_habits_and_one_offs_are_never_streams():       # Grocer, Fuel, Corner Shop, Airline, Diner absent in every profile
def test_monthly_next_dates_are_close():                       # the 372-case monthly sweep (a bill on each day 1 to 31, paid the next business day, 3 charges, 12 start months): share with abs(next_date - true next) <= 1 day >= 0.75, max error <= 4
def test_results_json_has_the_documented_keys():
```

- [ ] **Step 2: Run them to verify they fail**

Run: `../venv/bin/python -m pytest tests/test_recurring_engine.py tests/test_recurring_accuracy.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `recurring.py` and the eval script**

- [ ] **Step 4: The gate**

Run the tests, then `venv/bin/python eval/recurring_accuracy.py`. Paste the table into the task's commit body. **If a gate test fails, change only the thresholds named in the spec's departure 2 and 4 rules (gap spread, minimum charges, category set), re-run, and if it still fails, stop and report the numbers to the user. Don't start Task 3.** The measured baselines for comparison: ticket rules gave 11 of 17 false streams on a noisy profile and monthly precision 0.73.

- [ ] **Step 5: Tests docs and commit**

```bash
git add backend/app/budget_engine/recurring.py eval/recurring_accuracy.py backend/tests/test_recurring_engine.py backend/tests/test_recurring_accuracy.py docs/tests/test_recurring_engine.md docs/tests/test_recurring_accuracy.md
git commit -m "feat(TPC-33): detect repeating charges and paychecks, measured on planted and noisy users"
```

---

### Task 3: Stored streams, the refresh, and 180 days

**Files:**
- Create: `backend/alembic/versions/<rev>_recurring_streams.py` (down revision `a3d9e1b7c2f5`; confirm with `../venv/bin/alembic heads`), `backend/app/budget_service/recurring.py`, `backend/tests/test_recurring_service.py`
- Modify: `backend/app/db/models.py`, `backend/app/plaid_service/client.py:50,65`, `backend/tests/test_migrations.py`; the existing Plaid client test (or a new `test_plaid_days_requested.py`)

**Interfaces:**
- Table `recurring_streams`: `id`, `user_id` (FK, index), `direction`, `merchant_key`, `merchant_name`, `category`, `detailed` (nullable), `frequency`, `typical_cents`, `last_cents`, `previous_cents` (nullable), `amount_varies`, `first_date`, `last_date`, `next_date`, `gap_days`, `status`, `first_seen_run_id` (FK `agent_runs.id`, nullable), `first_seen_date`, `updated_at`. Unique `(user_id, direction, merchant_key)`; index `(user_id, status)`. `alerts.stream_id` (nullable FK, named constraint, SQLite batch mode as in `e0cdfdbdab45_advice_alert_link.py`) and `alerts.signal_key` (text, nullable, index `(user_id, signal_key)`). Model `RecurringStream`; `Alert.stream_id`, `Alert.signal_key`.
- Produces, in `budget_service/recurring.py`:
  - `load_rtxns(session, user_id, today) -> list[RTxn]`: non-removed, non-pending, `today - 180 <= date <= today`, categories from `initialize.resolve_category`
  - `history_start(session, user_id) -> Optional[date]`: earliest non-removed transaction date
  - `current_streams(session, user_id, today) -> list[Stream]`: `detect(load_rtxns(...), today)`
  - `@dataclass(frozen=True) StreamChange(kind: Literal["new_subscription","price_increase","income_change"], stream_id: int, previous_cents: Optional[int], token: str)`
  - `refresh_streams(session, user_id, today, run_id: Optional[int]) -> list[StreamChange]`: reads stored rows, runs `detect`, then updates, inserts and stales rows in one transaction and returns the changes (rules: spec "Design": signals). Commits nothing; the caller does.
  - `stored_streams(session, user_id) -> list[RecurringStream]`
- `days_requested` becomes 180 at both places.

- [ ] **Step 1: Write the failing tests**

```python
def test_first_refresh_stores_streams_and_raises_nothing(...):    # goals-card-24 at 2026-11-02: 6 rows (Landlord, Employer, Savings, Streamly, Platinum Card, Student Loan); changes == []
def test_a_second_refresh_the_same_day_changes_nothing():          # same rows, changes == []
def test_a_subscription_that_starts_inside_the_history_is_new():   # history from Jan 1; NewFlix Mar 10, Apr 10, May 10; refresh May 11 -> one new_subscription
def test_a_subscription_older_than_the_history_is_not_new():       # first_date - gap_days <= history_start -> none
def test_a_savings_transfer_is_stored_but_never_new():
def test_price_increase_fires_once_per_new_price():                # recurring-planted day by day: Oct 3 -> one price_increase (previous 4000, token "4300"); Oct 4..Nov 3 -> none
def test_a_rise_of_16_percent_also_fires():                        # Clipflix on Oct 20, previous 1549
def test_a_charge_under_5_percent_or_under_a_dollar_does_not():    # 3000 -> 3100; 500 -> 560
def test_income_change_when_the_paycheck_goes_stale():             # last May 15: refresh Jul 19 -> none; Jul 20 -> one income_change; Jul 21 -> none
def test_a_returning_paycheck_is_the_same_row_and_silent():
def test_a_missing_stream_is_marked_stale_not_deleted():
def test_a_roommate_transfer_going_stale_raises_no_income_change():
def test_pending_removed_and_future_rows_are_ignored():            # 3 charges, the third pending -> no stream until it posts
def test_signal_key_tokens():                                      # new: "first_seen"; price: str(last_cents); income: stale date iso
def test_plaid_asks_for_180_days():                                # create_link_token and sandbox_public_token both send days_requested == 180
# test_migrations.py: upgrade creates recurring_streams, alerts.stream_id and alerts.signal_key; downgrade removes them
```

- [ ] **Step 2: Run them to verify they fail**, **Step 3: Implement**, **Step 4: Run, then the whole suite.** Expected: PASS.
- [ ] **Step 5: Tests docs (`test_recurring_service.md`, `test_migrations.md`) and commit**

```bash
git commit -m "feat(TPC-33): store recurring streams and diff them on each run, ask Plaid for 180 days"
```

---

### Task 4: Signals in the daily run

**Files:**
- Create: `backend/app/budget_engine/signals.py`, `backend/tests/test_signals_engine.py`, `backend/tests/test_recurring_graph.py`
- Modify: `backend/app/agents/budget_graph.py`, `backend/app/agents/budget_llm.py`, `backend/app/privacy/gateway.py`, `backend/app/budget_service/alerts.py` (alert out only if needed), `backend/tests/test_gateway.py`, `backend/tests/test_budget_graph.py` (only if route assertions change)

**Interfaces:**
- Produces, in `budget_engine/signals.py` (pure):
  - `SIGNAL_CAP = 3`; `RANK = {"low_balance": 0, "income_change": 1, "price_increase": 2, "new_subscription": 3}`
  - `NOT_CANCELLABLE = frozenset({"RENT_AND_UTILITIES", "LOAN_PAYMENTS", "MEDICAL", "GOVERNMENT_AND_NON_PROFIT", "TRANSFER_IN", "TRANSFER_OUT", "SAVINGS_TRANSFER"})`
  - `@dataclass(frozen=True) Signal(kind: str, key: str, stream_id: Optional[int], category: Optional[str], numbers: dict[str, int | str | bool])`
  - `rank_signals(signals: Sequence[Signal]) -> list[Signal]`: by `RANK`, then larger amount first
  - `allowed_actions(signal: Signal) -> tuple[str, ...]`: the spec's "Actions" table; `choose(signal, proposed: str) -> str` downgrades to `"alert"`
  - `cancel_limit(limit_cents: int, monthly_cents: int) -> Optional[int]`: `None` when `limit - monthly <= 0`, else `min(ceil_to_500(limit - monthly), limit)`
  - `fallback_message(signal: Signal) -> str`
- Produces, in `budget_llm.py`: `SignalAction(action: Literal["alert", "suggest_cancel", "delay_spend", "move_from_savings"], message: str)`, `SIGNAL_PROMPT`, `decide_signal(model, context: str) -> SignalAction` through `invoke_structured`.
- Produces, in `budget_graph.py`: nodes `detect_recurring` and `decide_signals`; `BudgetState` gains `signals: list[dict]`, `signal_actions: dict[str, dict]`, `signals_error: Optional[str]`. Edges, from the spec's Lifecycle: with a budget, `update_totals -> detect_recurring -> detect_overspend`; for `no_budget` and `period_ended`, `load_state -> detect_recurring -> decide_signals`; `decide_signals` runs after the overspend branch and before `log_advice`; `log_advice` loops over overspend flags and signals (signal rows: `kind=signal.kind`, `stream_id`, `category`, `signal_key`, `AdviceLog.category`); it clears `signals` and `signal_actions` before `await_approval`. Both new nodes catch exceptions into `signals_error` (type name only). `persist_state` adds `as_of` and `streams: {count, new, price_up, income_change}` (ids only) to `summary_json`.
- Gateway: `Purpose` gains `"recurring"`; `RECURRING_FIELDS` allowlist; `build_context` row for an outflow is `{merchant: scrub_merchant(name), category, frequency, typical, last, previous, next_date}`, for an inflow `{category: "INCOME", frequency, typical}` with no merchant field; one `_audit` line per signal.

- [ ] **Step 1: Write the failing tests**

```python
# test_signals_engine.py
def test_cancel_limit():            # (10000, 1599) -> 8500; (1599, 1599) -> None; (1000, 1599) -> None; (600, 100) -> 500; (10000, 9996) -> 500
def test_rank_puts_low_balance_then_income_then_price_then_new():
def test_rent_and_transfers_cannot_be_cancelled():                 # choose(price_increase on RENT_AND_UTILITIES, "suggest_cancel") == "alert"
def test_income_change_only_alerts():                              # choose(income_change, "suggest_cancel") == "alert"
# test_recurring_graph.py
def test_a_normal_day_makes_no_model_call():                       # recurring-planted on a quiet day: scripted model call count 0
def test_a_stream_flag_and_an_overspend_flag_in_one_category_both_alert():   # PERSONAL_CARE: kinds == {"overspend", "price_increase"}, no KeyError
def test_a_user_with_no_budget_gets_signal_alerts():              # period_id None; same for period_ended
def test_a_detector_failure_does_not_stop_overspend_alerts():     # run status "ok", overspend alert present, summary signals_error == "RuntimeError"
def test_running_twice_on_the_same_day_alerts_once():
def test_five_signals_make_three_model_calls():                   # the other two use fallback_message
def test_the_models_cancel_of_rent_is_downgraded():
def test_new_subscription_alert_carries_stream_id_and_key():
def test_price_alert_message_quotes_the_old_and_new_price():      # "$40.00" and "$43.00" in the message (code figures, even when the model's message is replaced)
def test_a_paused_checkpoint_holds_no_signal_names_or_amounts():  # plant merchant "ZZ-UNIQUE-GYM" and cents 4321; pause on an adjust_next_budget; grep the checkpoint file bytes -> absent. Model on test_goal_approval_graph.py::test_paused_checkpoint_holds_no_debts_or_facts
def test_summary_json_holds_ids_and_cents_only():
# test_gateway.py
def test_inflow_streams_have_no_merchant_field():
def test_a_merchant_with_five_digits_is_scrubbed():
def test_one_audit_line_per_signal():
```

- [ ] **Step 2: Run them to verify they fail**, **Step 3: Implement**, **Step 4: Run, then the whole suite.** The existing graph tests must pass unchanged; a route assertion that must change is updated with its test doc entry.
- [ ] **Step 5: Tests docs and commit**

```bash
git commit -m "feat(TPC-33): alert on new subscriptions, price increases and income changes"
```

---

### Task 5: `suggest_cancel` at close

**Files:**
- Modify: `backend/app/budget_service/close.py` (`gather_month`), `backend/app/budget_engine/close.py` if the apply order lives there, `backend/app/budget_service/recurring.py` (`muted_stream_ids`)
- Test: `backend/tests/test_recurring_close.py`

**Interfaces:**
- Produces: `muted_stream_ids(session, user_id) -> set[int]`: streams with an accepted `suggest_cancel` alert. `refresh_streams` (Task 3) and `decide_signals` skip them.
- Close: after the accepted `adjust_next_budget` values, for each accepted `suggest_cancel` alert apply `cancel_limit(limit, monthly_cents(stream.typical_cents, stream.frequency))` to the stream category's line. `suggest_cancel` is not in `pending_proposals`.

- [ ] **Step 1: Write the failing tests**

```python
def test_accepting_suggest_cancel_lowers_the_line_at_close():       # ENTERTAINMENT limit 10000, Streamly 1599 -> 8500 next month
def test_an_accepted_adjust_and_an_accepted_cancel_both_apply_in_order():   # adjust to 12000, then cancel 1599 -> 10500
def test_a_category_with_no_line_is_unchanged_and_the_outcome_is_recorded():
def test_a_limit_the_cancel_would_take_to_zero_is_left_alone():    # limit 1000, monthly 1599
def test_a_pending_suggest_cancel_survives_month_end():           # still pending_user after close
def test_a_muted_stream_raises_no_more_signals():                 # after accept, a further price rise on the stream makes no alert
def test_decline_changes_nothing():
```

- [ ] **Steps 2 to 4:** run to fail, implement, run (`tests/test_recurring_close.py tests/test_budget_close.py tests/test_close.py`), then the suite.
- [ ] **Step 5:** `docs/tests/test_recurring_close.md`, then

```bash
git commit -m "feat(TPC-33): an accepted cancel suggestion lowers next month's line"
```

---

### Task 6: `GET /recurring` and the chat tool

**Files:**
- Create: `backend/app/api/recurring.py`, `backend/tests/test_recurring_api.py`
- Modify: `backend/app/main.py`, `backend/app/api/alerts.py` (`AlertOut.stream_id`), `backend/app/agents/tools.py`, `backend/app/agents/chat_graph.py:17-25`, `backend/tests/test_chat_graph.py`, `eval` chat scenarios (tool-choice cases)

**Interfaces:**
- `GET /recurring -> RecurringOut` per the spec's API section. `change` is `"new"`, `"price_increase"` or `"income_change"` when the stream has an alert from the last 30 days with that kind; `previous_cents` from the stored row. Inflow `label` is `"Income"`. Reads rows; writes nothing.
- Chat tool `get_recurring()` (no arguments) through the gateway purpose `recurring`. Tool count 5 to 6. Prompt: names it and says "recurring charges, subscriptions, how much do I pay monthly".

- [ ] **Step 1: Write the failing tests**

```python
def test_recurring_lists_changed_then_active_then_stale():
def test_an_inflow_label_is_income_and_never_the_employer():
def test_a_user_with_no_history_gets_an_empty_list():              # 200, {"as_of": None, "streams": []}
def test_the_get_writes_nothing():                                 # row counts before and after
def test_another_users_streams_are_not_visible():
def test_a_price_increase_alert_marks_the_stream():                # change == "price_increase", previous_cents 4000, last_cents 4300
def test_requires_auth():                                          # 401
def test_alerts_out_carries_stream_id():
# test_chat_graph.py
def test_chat_binds_six_tools_and_the_prompt_names_get_recurring():
def test_get_recurring_returns_scrubbed_rows():
```

- [ ] **Steps 2 to 4:** run to fail, implement, run. Add chat-eval scenarios: "what subscriptions do I have" expects `get_recurring` (live behaviour with `qwen2.5:14b` is not run here; say so in the handoff).
- [ ] **Step 5:** `docs/tests/test_recurring_api.md`, `docs/tests/test_chat_graph.md`, then

```bash
git commit -m "feat(TPC-33): GET /recurring and the get_recurring chat tool"
```

---

### Task 7: The Recurring card and the new alert kinds

**Files:**
- Create: `frontend/components/RecurringList.tsx`, `frontend/components/RecurringList.test.tsx`
- Modify: `frontend/lib/api.ts`, `frontend/components/AlertCard.tsx`, `frontend/components/AlertCard.test.tsx`, `frontend/app/dashboard/page.tsx`, `frontend/app/dashboard/page.test.tsx`

**Interfaces:**
- `api.ts`: `RecurringStream`, `RecurringResponse`, `getRecurring(): Promise<RecurringResponse>`; `Alert` gains `stream_id: number | null`.
- `RecurringList({ data }: { data: RecurringResponse })`: rows of label, frequency ("Monthly"), amount (`typical_cents`, "about" prefix when `amount_kind === "varies"`), next date or "Stopped"; badges "New", "Price up $40.00 to $43.00", "Income down" from `change` and `previous_cents`; stale streams in a muted "No longer seen" group; empty text "No repeating charges found yet."; `as_of === null` text "We need about 3 months of transactions to spot repeating charges." No actions.
- `AlertCard`: `KINDS` gains `new_subscription: "New subscription"`, `price_increase: "Price increase"`, `income_change: "Income change"`, `low_balance: "Low balance"`; "Proposed limit" shows for `adjust_next_budget` and `suggest_cancel` when a limit exists.
- Dashboard: a "Recurring charges" card between Goals and Transactions with its own `recurring` and `recurringError` state and a `cancelled` flag in the existing effect; a failure sets only the card's error ("Couldn't load your recurring charges: ...").

- [ ] **Step 1: Read the Next.js docs the page uses** (`frontend/node_modules/next/dist/docs/`, as `frontend/AGENTS.md` says), then write the failing tests

```tsx
// RecurringList.test.tsx
it("shows a monthly subscription with its next date")           // "Streamly", "Monthly", "$15.49", "Nov 5"
it("prefixes about when the amount varies")                      // "about $102.00"
it("badges a price increase with both prices")                   // "Price up $40.00 to $43.00"
it("shows an inflow as Income with no employer")                 // queryByText("Employer") is null
it("groups stopped streams under No longer seen")
it("shows the empty text and the too-little-history text")
// AlertCard.test.tsx
it("reads Price increase for kind price_increase, not Over limit")
it("shows the proposed limit for suggest_cancel")                // "$85.00"
it("shows no proposed limit when there is none")
// page.test.tsx: add getRecurring to the vi.mock list and beforeEach (resolved empty list)
it("shows the Recurring charges card")
it("keeps a recurring failure inside its card")                  // the Goals and Transactions cards still render; the page error is not set
it("shows the empty text for a user with no streams")
```

- [ ] **Steps 2 to 4:** `npm test -- components/RecurringList.test.tsx components/AlertCard.test.tsx app/dashboard/page.test.tsx` to fail, implement, then `npm test && npm run lint && npm run build`.
- [ ] **Step 5:** `docs/tests/RecurringList.test.md`, update `AlertCard.test.md` and `dashboard-page.test.md`, then

```bash
git commit -m "feat(TPC-33): Recurring charges card and alert kinds on the dashboard"
```

---

### Task 8: The done-when, docs and handoff

**Files:**
- Create: `backend/tests/test_recurring_loop.py`, `docs/tests/test_recurring_loop.md`, `docs/handoffs/2026-11-02-tpc-33-manual-verification.md`
- Modify: `docs/architecture.md`, `README.md`, `docs/agent-pipelines.md`, `docs/system-walkthrough.md`, `docs/diagrams/data-model.puml`, `docs/diagrams/budget-agent-loop.puml`, `docs/diagrams/remaining-lifecycle.puml`, the spec's status line, `backend/tests/test_m3_demo.py`

- [ ] **Step 1: Write the failing end-to-end tests**

All on `recurring-planted`, a scripted model, `advance_days` one day at a time from 2026-09-30 to 2026-10-25.

```python
def test_every_planted_stream_is_detected():                      # Landlord, Employer (biweekly, direction in), Streamly, FitClub Gym, Clipflix, NewFlix are in GET /recurring; Pottery Studio, Grocer, Fuel are not
def test_the_gym_price_alert_fires_on_the_run_after_the_higher_charge():   # no price_increase alert on Oct 2; exactly one after the Oct 3 run, naming FitClub Gym, $40.00 and $43.00; still one on Oct 25
def test_a_rise_over_ten_percent_also_alerts():                   # Clipflix: none on Oct 19, one on Oct 20
def test_the_new_subscription_alert_fires_when_its_third_charge_posts():   # NewFlix: none on Oct 11, one on Oct 12; none for Streamly, Landlord or Employer at any date
def test_a_merchant_with_two_visits_is_never_a_stream():
def test_accepting_a_cancel_suggestion_reaches_next_months_line():   # accept the Clipflix suggest_cancel (scripted), advance past month end, ENTERTAINMENT line drops by $17.99 rounded
```

- [ ] **Step 2: Run, fix what they find.** A failure means an earlier task's seam is wrong: fix it there and add the missing test.
- [ ] **Step 3: The Plaid comparison is not built.** Record in the handoff the manual steps from the spec's out-of-scope list: Sandbox credentials, an Item created after `days_requested` became 180, a client wrapper for `/transactions/recurring/get`, a script matching on merchant key and frequency. Existing Items stay at 90 days until relinked.
- [ ] **Step 4: Update the docs.** `architecture.md`: 6a.1 as built (departures 1 to 8 of the spec), 6a.4 (`detect_recurring`, `decide_signals`, no pause), section 9 (response shape), section 5 (`recurring_streams`, `alerts.stream_id`, `alerts.signal_key`), section 10 (precision and recall as built and the gate numbers), section 16 (a decision-log row per spec decision), 6.6 (one line: `suggest_cancel` does not pause), the changelog line about `days_requested`. `README.md`: API list, a Recurring section, the seed commands. `docs/system-walkthrough.md:29-30,68`. `agent-pipelines.md` and the three `.puml` files: recurring is no longer "planned". Re-render PNG and SVG only if the tool that made them is available here; otherwise say so in the handoff. `docs/tests/`: confirm every new and changed test has its entry.
- [ ] **Step 5: Write the handoff.** `docs/handoffs/2026-11-02-tpc-33-manual-verification.md`, in the format of `2026-11-02-tpc-25-manual-verification.md`: a disposable database, `scripts/seed_budget_user.py --profile recurring-planted --replace --start 2026-09-30` so the user is simulated, then `scripts/simulate.py --user budget-recurring-planted --days 25`. Checks: the Recurring card lists the planted streams; the gym row carries "Price up $40.00 to $43.00" after Oct 3; Pottery Studio is absent; a fresh user shows the empty text; a user with under 3 months shows the history text; the Alerts page shows "Price increase" and "New subscription"; accept a `suggest_cancel` and close the month; run the noisy profile (`venv/bin/python eval/recurring_accuracy.py`) and record the table. Say that live chat tool choice and Postgres were not run.
- [ ] **Step 6: Run everything.** `../venv/bin/python -m pytest -q -k 'not live and not sandbox'`, then from `frontend/`: `npm test && npm run lint && npm run build`. Record the counts in the handoff.
- [ ] **Step 7: Commit** (only these files)

```bash
git commit -m "docs(TPC-33): recurring streams end to end, docs and manual verification"
```
