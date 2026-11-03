# TPC-33 / TPC-31 investigation 3: wiring into the agents

Scope: budget graph, decide/action, alert repeats, goal graph, debt planner, gateway, chat tools, close and simulation. Mostly read from code; one experiment (goal-test sensitivity). Synthetic users only.

## 1. Summary

- Put `detect_recurring` and `forecast_cash` after `update_totals` and before `detect_overspend`, and also run them on the `no_budget` and `period_ended` paths. A user with no budget still has transactions and a balance, and the forecast does not need budget lines. Flags from the new nodes must not share `state["flags"]`; the overspend loop is hard-wired to category and `PaceResult`.
- Do not push stream or low-balance flags through `diagnose`. Add one new node `decide_signals` (one model call per signal, no diagnose call) and a separate `signals` list in state. A normal day adds 0 calls, a bad day adds 1 per signal.
- `suggest_cancel` should not pause the run. Close can apply it from alert rows exactly as it applies `adjust_next_budget`. Pausing is not what makes either work, and it would put forecast and stream data into the checkpoint.
- The goal graph should compute the forecast itself through one shared function, not read a stored figure. A stored figure can be missing (paused runs have an empty `summary_json`), days old, or from another simulated date. The budget run still stores the figure for audit and the UI.
- The ticket's surplus formula (expected balance at paycheck minus buffer) depends on the current balance. It must be redefined as a flow before goals use it (**Departure 1**). `committed_cents` must stay, as an add-back-and-subtract (see 4.6).
- The debt planner does not use surplus at all today. "Used by the debt planner" can only mean a suggested default for the extra-payment input (**Departure 2**).
- 21 existing goal/gateway/user tests assert exact measured-surplus numbers and will change; 45 are surplus-sensitive in total (experiment 3.1).

## 2. What exists today

- Graph: `backend/app/agents/budget_graph.py:287-305` (edges), `load_state` :121-129, `update_totals` :152-161, `detect_overspend` :163-164, `diagnose` :166-187, `decide_action` :189-218, `log_advice` :220-239, `await_approval` :245-254, `persist_state` :256-275, `after_load` :277-278, `after_advice` :284-285.
- `no_budget` / `period_ended` jump `load_state -> persist_state` (:296); `persist_state` writes `summary_json` (:259-270).
- `Action` literal: `budget_llm.py:64-66`. Prompts: :17-47. `invoke_structured` :73-89. `_RANK` :108.
- Repeat memory: `_this_month_alerts` :81-85 (hard-filters `kind == "overspend"`, `category`), `_past_advice` :90-100, `escalated` rule :209-214.
- Close applies accepted proposals: `budget_service/close.py:100-104` (queries `action == "adjust_next_budget"`, keyed by category), `budget_engine/close.py:25-28` (accepted value used as-is). Expiry of pending proposals: `close.py:38-62`.
- Decisions: `budget_service/alerts.py:77-91` (`decide_alert` only sets status and advice outcome; no side effect). Alerts API `api/alerts.py:19-37`.
- Model: `Alert` (kind non-null already exists; no `stream_id`), `AdviceLog`, `AgentRun`, `User.prefs_json` at `db/models.py:16-23,168-230`. `Transaction.is_recurring` exists (:20 of Transaction, unused by us).
- Goal graph: `load_facts` `goal_graph.py:120-154`; surplus = `monthly_surplus(...) - card_minimums` (:131-132); cap = `round(surplus*max_share) - committed` (:143); `persist` checks `committed_ids` unchanged (:206). `GoalFacts` `budget_engine/goals.py:78-86`. Gateway `_goals` `privacy/gateway.py:206-233`, `GOAL_ASSUMPTIONS` :49.
- Debt planner: `api/debt_plan.py:25-27,81-95` takes `extra_monthly_cents` from the request; `plan_debt_payoff` `agents/tools.py:82-92` takes `extra_monthly` from the model; `DebtPlanCompare.tsx` has an "extra" text box defaulting to "0". `grep -i surplus` finds no use outside goals.
- Chat: 5 tools today (`tools.py:94-128`: accounts, debts, spending, search_kb, plan_debt_payoff). System prompt names three (`chat_graph.py:17-25`).
- Gateway purposes: `Purpose` literal `gateway.py:25`; `build_context` dispatch :90-125; scrubbing `privacy/scrub.py`; audit `_audit` :305-329.
- Alerts UI: `AlertCard.tsx:7-9` `KINDS` (unknown kind falls back to "Over limit"/"Ahead of pace" by severity), heading uses `alert.category` else "Budget", "Proposed limit" shown only for `adjust_next_budget` (:30-32). `lib/api.ts:133-146` `Alert` type.
- Sim: `budget_service/simulate.py:78-105` injects transactions only; `daily.py:51-69` `run_daily_locked`. Seeded accounts: `add_account` sets `current_balance_cents=10000`, `available_balance_cents=None`, never updated by simulation.

## 3. Findings

### 3.1 Goal tests that depend on the measured surplus (experiment)
Method: a pytest plugin with an autouse fixture wrapping `goal_graph.monthly_surplus` (+1 cent, x0.5, x2), run over `test_goals, test_goal_graph, test_goal_approval_graph, test_goal_validation_graph, test_goal_validation, test_goal_decisions, test_goals_api, test_goal_loop, test_gateway, test_budget_users` (263 tests, all pass unperturbed, 28 s).

| Perturbation | Failed of 263 | Meaning |
|---|---|---|
| +1 cent | 21 | tests asserting exact surplus/cap (97175, 77740, 16400, etc.) or exact messages ("$777.40") |
| x0.5 | 45 | plus tests whose scripted model amounts no longer fit the cap |
| x2 | 33 | the same, other direction |

Files hit by +1 cent: `test_goal_graph.py` 9, `test_goal_approval_graph.py` 3, `test_goal_validation_graph.py` 8 (5 are the "live" trials, which run scripted offline), `test_goal_loop.py` 1. Tests that assert only `GoalFacts` formatting (`test_gateway.py:544-574`, 2 tests) call the gateway directly and are unaffected by source, but change if `GoalFacts` gains fields with defaults only if the assertion lists the dict exactly (it checks named keys, so likely fine; unverified). Because the plan keeps the measured path as the fallback and these seeded users have no stream until the detector sees 3 paychecks, the plan should add a test hook (a `surplus_source` override) rather than rewrite 21 tests. Note `goals-card-24` and all `BASE` users have monthly pay on the 15th (`eval/budget_users.py:129`), so once the detector exists they get a paycheck stream and switch to the forecast path unless the hook forces fallback.

### 3.2 Why the literal surplus formula breaks goals (reasoning from the ticket text and the profile)
6a.2: "expected balance at the next paycheck, minus the buffer, minus events ... scaled to a month." A balance is a stock. `goals-card-24`'s checking account is $100 (factory default), so the literal figure would be near $0 or negative and the TPC-23/25 live result ($971.75 surplus, $777.40 cap) disappears. A user holding $20,000 in checking would get a huge "monthly" surplus. Required: `monthly_surplus_cents` = average monthly inflow minus average monthly outflow, from the forecast's own components, independent of the opening balance and the buffer. Everyday spending in that sum must be the mean of the 90-day non-recurring daily total (median day is $0 for most users, as the brief warns).

### 3.3 Paused runs have no summary
`await_approval` runs before `persist_state` (:301-304) and `_mark(..., finished=False)` passes no summary (:390), so an `awaiting_user` budget run has `summary_json == {}` until resumed. Anything that reads "the latest budget run's forecast" must skip those rows. Also `AgentRun.started_at` is wall clock; a simulated run records no date unless we add one.

### 3.4 Flags in the overspend loop
Reading the code: `state["flags"]` items are `PaceResult` dicts (fields `category, kind, limit_cents, spent_cents, days_elapsed, days_in_period, pace, flagged, reason, group`). `diagnose` does `PaceResult(**flag)` (:179) and keys `facts/contexts/diagnoses/memory` by `flag["category"]` (:180-187); `decide_action` and `log_advice` index by category and hard-code `kind="overspend"` (:227). A stream flag has none of those fields, so `PaceResult(**flag)` raises `TypeError`. Worse, the key collides: the gym that raises its price is in `PERSONAL_CARE`, which is also the category that overspends, so one dict slot would hold two different things. `_severity` (:76-77) only knows `over_limit`. `_this_month_alerts` filters `kind == "overspend"` so a stream alert would never count as memory; `_past_advice` joins `AdviceLog.category` (null for category-less alerts).

## 4. Recommended design

### 4.1 Graph order (item 1)
```
sync -> load_state -> [ok] categorize -> update_totals -> detect_recurring -> forecast_cash -> detect_overspend
                      [no_budget | period_ended] -> detect_recurring -> forecast_cash -> decide_signals -> log_signals -> persist_state
```
- Reason for after `update_totals`/before `detect_overspend`: both new nodes need fresh synced transactions (sync is first) and are independent of budget lines; placing them before `detect_overspend` lets `diagnose` read `state["stream_changes"]` for the `recurring` cause (4.9). They are deterministic (no model).
- Shape: `after_load` routes `no_budget`/`period_ended` to `detect_recurring` instead of `persist_state`; after `forecast_cash`, a conditional edge sends those two statuses to `decide_signals`, others to `detect_overspend`. After the overspend branch, `decide_signals` runs before `log_advice` (one `log_advice` writes both kinds; see 4.2). `after_advice`/`await_approval` unchanged: only `adjust_next_budget` alerts pause.
- A user with no budget does get a forecast and stream alerts: nothing in either node needs a period. Their alerts have `period_id = None` (column is nullable; alerts API outer-joins the period, `api/alerts.py:34`). Repeat keys (4.5) therefore must not depend on `period_id`.
- Both nodes are wrapped so a failure becomes `{"signals_error": type}` in state and the summary, never an `error` run; the overspend path must still run.
- `persist_state` adds to `summary_json`: `as_of` (the run's `today`), `streams` {count, new, price_up, income_change ids}, `forecast` {`ok`, `low_balance` (date, shortfall_cents, bill), `monthly_surplus_cents`, `surplus_source` ("forecast"|"measured"|None), `committed_goals_cents`, `assumed_no_income` bool}, `signals_error`. Never put merchant names or balances in `summary_json`; ids and cents only. This is also what the ForecastChart reads if the endpoint chooses the stored copy.
- `BudgetState` additions: `streams_changed: list[dict]` (stream id, change kind, cents only), `low_balance: Optional[dict]`, `signals: list[dict]`, `signal_contexts`, `signal_actions`. Keep rows and full forecast paths out of state; store them in tables or recompute (checkpoint, 4.8).

### 4.2 Signals and decisions (item 2)
A `Signal` dict: `{"kind": "new_subscription"|"price_increase"|"income_change"|"low_balance", "key": str, "stream_id": Optional[int], "category": Optional[str], ...numbers}`. `key` is unique per signal (see 4.5), so there is no category collision.
- `decide_signals` (new node, replaces diagnose for signals): for each signal, build a context with the new gateway purposes, call `invoke_structured(model, SignalAction, ...)` once. Code supplies the numbers and the fallback message (as `_fallback_message` does today).
- `log_advice` becomes one loop over two lists. Overspend rows are written as today; signal rows are written with `kind=signal["kind"]`, `stream_id`, `category=stream category or None`, `AdviceLog.category = category`.
- Why not diagnose: nothing to classify (cause is known: `recurring` for streams; a low-balance dip's cause is "the bill" which code names). A diagnose call would add model work with no decision value.

Model calls per run (counting `invoke_structured` successes; each can retry once on bad JSON, so max is 2x):

| Day | Overspend flags | Signals | Calls |
|---|---|---|---|
| Normal (nothing flagged) | 0 | 0 | 0 today, 0 after |
| Bad day: 1 overspend, 1 new stream, 1 low_balance | 1 (2 calls: diagnose+decide) | 2 (1 each) | 2 + 2 = 4 (today 2) |
| Cap | n flags | s signals | 2n + s |

Code should cap signals per run at 3 (highest severity: low_balance, then income_change, price_increase, new_subscription by amount); the rest get code-written messages with no model call. First detector run for a user with 180 days of history can mark many existing streams "new": the first run for a user must be a baseline (store streams, emit no `new_subscription`). **Decision for user: none; this is required.** Test it.

### 4.3 Actions (item 3)
Do not extend the overspend `Action` literal; add `SignalAction(action: Literal[...], message)` in `budget_llm.py`, with per-kind allowed sets enforced by code, using the same downgrade-to-alert pattern as :204-207:

| Signal | Model may pick | Code downgrades to `alert` when |
|---|---|---|
| new_subscription, price_increase | `alert`, `suggest_cancel` | stream category in `NOT_CANCELLABLE` (`RENT_AND_UTILITIES`, `LOAN_PAYMENTS`, `MEDICAL`, `GOVERNMENT_AND_NON_PROFIT`, `TRANSFER_*`) or stream not an outflow |
| income_change | `alert` only | always |
| low_balance | `alert`, `delay_spend`, `move_from_savings` | `move_from_savings`: no savings account whose `current_balance_cents >= amount`; `delay_spend`: no flexible category with `last_pace >= 1.0` (reuse `BudgetLine.last_pace`) or no budget (no period) |

Amounts by code, never the model: `move_from_savings` amount = `shortfall_cents + buffer_cents` rounded up to $5 (same step as `PROPOSAL_STEP`, `diagnose.py:16`), capped by the savings balance (above that, downgrade). `delay_spend`: category list and amounts = `min(spent over pace, shortfall)` per category, from `PaceResult`. `suggest_cancel`: `proposed_limit_cents` per 4.4. The model's message may only quote numbers given in the context; if the model returns a message, the code checks nothing more than today's `Text` validation, so the prompt must include the exact figures. The bill name for the dip comes from the forecast event (code), not the model (a model picking the bill would be a free-text hazard).

### 4.4 `suggest_cancel` (item 4)
Compare with `adjust_next_budget`: both are accept-to-change-next-month proposals with a code-computed `proposed_limit_cents` that close applies as-is (`close.py:100-104`); neither needs the run alive when applied. The pause (`await_approval`) exists only to hold the run open, and the repeated runs show it costs: architecture decision log line "a paused run per day would pile up"; a stream alert fires once per change, but pausing also keeps `facts`/`contexts` in the checkpoint (4.8).
- **Recommend: no pause.** `suggest_cancel` is a plain `pending_user` alert. Accept and Dismiss are recorded by `decide_alert` as today. Close reads accepted rows. This matches the user's decision for low balance and avoids new `after_advice` logic. Cost if wrong: if the user expects the agent to "wait", nothing visible changes; only if a later requirement says all budget-changing proposals must `interrupt()` (architecture 6.6) would this need revisiting. 6.6 should be amended by one line.
- Apply at close, in `gather_month`, by extending the `accepted` query to `action.in_(("adjust_next_budget","suggest_cancel"))`, ordered by `Alert.id` so the latest wins per category. This is a conflict: a category with both an accepted `adjust_next_budget` and an accepted `suggest_cancel` in one period gets only the later one. Resolve by computing `suggest_cancel`'s proposed limit at close time instead of at alert time: `new_limit = max(line.limit_cents - stream_monthly_cents, 0)` using the then-current line, then apply after the adjust value (`accepted` first, then subtract). Store `proposed_limit_cents` on the alert for display only (current line limit minus monthly amount, rounded up to $5 like proposals).
- Monthly amount of a stream: `typical_cents * (30.44 / gap_days)` for weekly/biweekly, `typical_cents` for monthly, `/12` for yearly (code in `recurring.py`).
- **Missing category line** (new category, flexible line skipped when median is $0, `fit.py` skipped list): there is nothing to lower; the spending lands in the Free line. Then `suggest_cancel` stays valid but `proposed_limit_cents = None`, the message says "this will be covered by your Free line, no budget line to change", and accepting only records the outcome and mutes the stream. Do not invent a line. If the line exists but `limit < monthly amount`, a $0 flexible limit flags every purchase (see the "occasional purchase" comment in `fit.py`), so the rule is: if the result would be $0 or less, leave the line unchanged and say so in the message.
- **Muting**: derive it from rows, no column. A stream signal is suppressed when any alert with that `stream_id` has `action = 'suggest_cancel'` and `status = 'accepted'`. A single query; no second source of truth.
- Expiry: a pending `suggest_cancel` should stay pending until the user answers (it is not tied to the month the way a limit proposal is). Do not add it to `pending_proposals` (`close.py:38-42` selects `adjust_next_budget` only). If it expired with the month it would be re-created the next day only if the detector re-emits it, which it does not (one alert per change), so expiry would lose it.

### 4.5 Repeat alerts (item 5)
Current rule is overspend-specific: `_this_month_alerts` keys on `(period_id, kind='overspend', category)`, the model sees only severity/action/status, and `escalated` (:209-214) lets `no_op` stand only if an earlier alert at least as severe exists. Do not reuse these functions; reuse the principle (code decides repeats, the model cannot hide a signal).
- **Low balance key**: `("low_balance", dip_date_iso, bill_key)` where `bill_key` is the normalized merchant key of the largest scheduled event on the dip date (card minimum: `"card:<account_id>"`). Stored as `alerts.signal_key` (text, nullable, indexed with `user_id`).
- **Repeat rule** (code, before any model call): find the user's latest alert with the same `signal_key`. Suppress unless the new dip is materially worse: `shortfall_cents >= prev_shortfall_cents * 1.25 and shortfall_cents - prev_shortfall_cents >= 2000` (25% and $20; **a starting value, tune on the eval**), or the dip is closer: `days_until < prev_days_until` where previous is measured from the new run date (store `days_until` at alert time, compare `dip_date - today` to `dip_date - prev_alert_run_date`, i.e. less than 3 days away and previously 3+ days is the useful tier: **unverified, simpler**: re-alert only when `days_until <= 2` and the earlier alert was for `days_until > 2`). Because the key contains the date, a dip that slides one day is a new key; accept this (it moves closer or further, and a key change is exactly "moved") but suppress if the new dip date is later than a previous alert's date for the same bill within 3 days (it moved away, not closer). A dismissed alert is not re-sent unless materially larger.
- Store on the alert: `signal_key`, plus numbers in two nullable ints `signal_cents` (shortfall) and `signal_days` (days ahead at alert time). Three new `alerts` columns total, plus `stream_id`.
- **Stream key**: `("<kind>", stream_id, change_token)` where `change_token` is `first_seen` for `new_subscription` (once ever per stream), the new `last_cents` for `price_increase` (a further rise makes a new key; same rise does not repeat), and the stale date for `income_change`. One alert per change; the detector itself diffs against the last stored stream so the diff is the primary guard and the key is the second guard against a failed or replayed run (a rerun the same day must not double-alert, as `run_daily` is once per user per day only by convention).
- `AdviceLog` rows are kept for stream alerts for outcome reads; signal memory goes to the model only as the single code-built line "already told: yes/no, outcome".

### 4.6 Goal graph (item 6)
- **Single entry**: `budget_service/forecast_service.py` (new) with `snapshot(session, user_id, today) -> ForecastSnapshot` (runs the detector in memory from `load_txns`, reads balances/liabilities/accepted goals, runs `forecast.py`). Budget `forecast_cash` and `GET /forecast` and the goal graph all call it. The budget run persists streams (upsert) separately.
- Goal `load_facts`: call `snapshot(s, user_id, today)` where `today` is the run's date (`sim_today` or wall clock, already how `run_goals` is called, `goal_graph.py:504`). Surplus = `snapshot.monthly_surplus_cents` if `snapshot.has_income_stream and snapshot.history_days >= 60`, else `monthly_surplus(...) - card_minimums` as today. Record `surplus_source` ("forecast"|"measured") and `forecast_as_of` in `_summary` (:278-287) and `GoalState`. The decided requirement ("run records which was used") is met.
- **Lock**: goal runs hold `user_lock` and check `running_elsewhere` (:379-386), so they cannot overlap a budget run; computing in the goal run needs no extra coordination. If it read stored data instead, a paused budget run would leave no summary (3.3) and a day-old run would be stale; the staleness test would need `as_of == today`.
- **Card minimums**: the forecast already schedules card minimums, so when source is "forecast" do not subtract `card_minimums` again. Keep `card_minimums_cents` in the summary for the measured path.
- **`committed_cents`** stays. Let the snapshot expose `committed_goals_cents` (the accepted-goal outflows it netted). Define `gross = surplus + committed_goals_cents` and `cap = max(round(gross * max_share) - committed_cents, 0)`. Example: gross $1,000, one $500 goal, share 0.8. Today after the goal's payments reach history: measured $500, cap = 400 - 500 = 0 (the TPC-25 understatement). Subtract-only variant `round(net*share)` = $400, total goals reach 90% of gross, breaking `max_share` as a total. Recommended variant gives $300, total 80%. When no goals are committed all variants agree. Fallback (measured) path keeps today's formula and its known limit.
- **`GoalFacts`** (`goals.py:78-86`): add `surplus_source: str = "measured"` (default keeps the existing 2 gateway tests passing). **Gateway `_goals`** adds `"surplus_basis": "forecast"|"measured"` and, for forecast, `"assumptions"` text: `GOAL_ASSUMPTIONS` currently says "left after minimum payments" (:49); with the forecast, say "monthly_surplus is the forecast's average inflow minus scheduled bills, accepted goals and everyday spending". The model sees no balances or merchant names. The audit logs `cap_cents` already (:119).
- `no_surplus` branch: forecast surplus <= 0 with a paycheck stream is a real "no surplus", not a fallback trigger.

### 4.7 Debt planner (item 7)
Today: no use of surplus. `debt_plan.py` and `plan_debt_payoff` take `extra` as user input; `DebtPlanCompare` starts at "0". "Used by the debt planner" can mean only: (a) prefill the extra input with a suggested amount, or (b) the daily-run `extra_payment` proposal in 6a.3 (accepted debt-payoff goal + more surplus than assumed -> alert). Recommend (a) only, for TPC-31: add `suggested_extra_cents` = `floor(max_share * monthly_surplus)` to `GET /forecast`, shown as a hint ("You could add about $X") in `DebtPlanCompare`, not a prefill that changes results. (b) is out of scope (needs the `extra_payment` kind and an accepted debt goal; it duplicates the goal graph). **Departure 2**: the ticket line should read "the Goal Agent; the debt planner shows it as a suggested extra".

### 4.8 Gateway and checkpoint (item 8)
- New purposes `recurring` and `cash_flow` (low-balance) in `Purpose` (:25) and `build_context` (:90-125), fed with plain dataclasses like `GoalFacts`, built field by field from allowlists (`RECURRING_FIELDS`, `CASH_FIELDS`).
- `recurring` rows: outflow stream: `{merchant: scrub_merchant(name), category, frequency, typical, last, change, next_date}`. Inflow stream: `{category: "INCOME", frequency, typical}` only (no merchant field exists in the row). Merchant strings use `scrub_merchant` (:33-39, strips emails, 4+ digit runs, P2P counterparties). Normalized merchant keys (digits/punctuation removed) are for grouping only and are not sent.
- `cash_flow` (low-balance): the dip `date`, `days_ahead`, `shortfall`, `buffer`, `bill` as `{merchant scrubbed | "credit card minimum", amount, date}`, other events as category + amount, the savings coverage as a yes/no and the amount code proposes, flexible categories ahead of pace (category name + dollars). No account names, no balances beyond "shortfall" and "starting available" rounded to dollars. The card minimum label uses `scrub_account_name` or just "credit card" (account names can be personal).
- Audit: `_audit(purpose, params, prompt)` per context (params: signal kind, stream id as a number-less key like `key`, not names). One audit line per signal (matches `diagnose`).
- **Checkpoint rule (TPC-25) extension**: `run_budget` writes a checkpoint at every step (no `durability="exit"`, `budget_graph.py:378-380`), so every key in `BudgetState` reaches the file when a run pauses, and a budget run already keeps `facts`/`contexts` (merchant names, scrubbed) across the pause. New state would add forecast numbers and bill names. Mitigations: (1) keep signals and contexts out of the checkpoint by not pausing for them (4.4) and by keeping the `adjust_next_budget` pause path as the only pause; (2) in `log_advice`'s return, clear `signal_contexts`, `signal_actions`, `low_balance`, `streams_changed` (as `forget_facts` does in the goal graph, `goal_graph.py:234-235`) before `await_approval`; (3) add a test modelled on `test_goal_approval_graph.py::test_paused_checkpoint_holds_no_debts_or_facts` that plants a distinctive bill name and balance and greps the checkpoint file after a pause. The existing budget pause still leaves its overspend `contexts` in the file; that is existing behaviour and not changed here.

### 4.9 Chat (item 9)
- Add `get_recurring()` and `get_cash_forecast()` (no arguments; both read through the gateway with the purposes above, and `get_cash_forecast` accepts nothing so a small model can't send a bad date). Tool count goes from 5 to 7, which matches 6a.4's total of 7 (`plan_debt_payoff` already exists). Description rules: say when to use ("subscriptions, recurring charges, how much do I pay monthly", "will I run low, forecast, when is my next bill") and what is excluded ("not general spending").
- Prompt rule edit (`chat_graph.py:17-25`): add the two tools to the sentence naming tools and "state the forecast as a range; say when income is assumed to be none".
- Eval: add scenarios asserting tool choice (6a.4 already says the eval checks each). `qwen2.5:14b` live behaviour not run (unverified).

### 4.10 Close and fit (item 10)
- `FIXED_DEFAULT` (`fit.py:13`) is a category set. Section 6.3 says subscriptions found by the detector become fixed, but a subscription is a merchant inside `ENTERTAINMENT`, which holds other spending. Moving the category to fixed would make fit use "lower median of months with a bill" and judge it by limit not pace. **Recommendation**: leave `fit_budget` as is and do not claim 6.3/6.4 are fixed by TPC-33 (the fix would need merchant-level fixed handling in fit and pace, a larger change); the false pace flag on a day-5 subscription remains. Cost if wrong: that alert noise continues, tracked as a follow-up for tuning in M5.
- `diagnose`'s `recurring` cause: no change to the prompt rules; `started_recently` already finds the gym. Optionally pass the stream id/frequency into `MerchantFact` later; not needed to meet the done-when.
- `close_due` needs: `gather_month` extension for `suggest_cancel` (4.4) only. Close's own alerts keep `category=None`.
- Simulation: `_inject` adds transactions, then `run_daily_locked` runs the budget graph, so the detector runs each simulated day with no change. But balances do not move in simulation (seeded `current_balance_cents` fixed, `available` None), so the forecast's opening balance is the seed value forever, and a low-balance dip on a simulated user depends on that constant. The planted rent-before-payday user needs a profile with a chosen opening balance, and the simulation should recompute `current_balance_cents` as `seed balance - sum(injected net amounts)` per injected day (a small `_inject` change). Dates: pass `today` (the simulated date) into the snapshot; never `date.today()`.
- Run time per simulated day: not measured (see section 8). The detector reads 180 days (about 90-300 rows per synthetic user) once per run; the budget graph already loads 3 months twice.

### 4.11 Alerts UI (item 11)
`KINDS` needs `new_subscription: "New subscription"`, `price_increase: "Price increase"`, `income_change: "Income change"`, `low_balance: "Low balance"` (otherwise they read "Ahead of pace"/"Over limit"). The heading uses `alert.category` (e.g. "Personal care") which works for streams; low balance has `category` null and shows "Budget": set `category=None` and add heading override by kind ("Cash flow"). Show "Proposed limit next month" for `suggest_cancel` too (condition `adjust` -> `action in (adjust_next_budget, suggest_cancel)`), and when `suggest_cancel` has no limit show nothing. Add `stream_id` to `AlertOut`/the TS `Alert` type. Severity: `low_balance` high when the low path goes below $0, medium when only under the buffer; others medium.

## 5. Departures from the ticket

1. **Surplus is a flow, not "balance at paycheck minus buffer".** See 3.2: the literal formula makes surplus depend on the checking balance ($100 for `goals-card-24`), so its $971.75 surplus would vanish. Use average monthly inflow minus average monthly scheduled bills and mean everyday spending; the buffer is for the low-balance trigger only. Needs the forecast investigation's agreement.
2. **Debt planner "uses" surplus only as a displayed suggestion.** It uses nothing today (4.7). The `extra_payment` daily proposal in 6a.3 is deferred.
3. **Goal graph computes the forecast through a shared function instead of reading `agent_runs.summary_json`.** The budget run still writes the figure there (4.1).
4. **`suggest_cancel` and low-balance alerts do not pause the run** (user already decided this for low balance; `suggest_cancel` is the extension). Architecture 6.6 and the decision-log line need one amendment.

## 6. Decisions for the user

- **Pause for `suggest_cancel`?** Recommend no (4.4). If wrong: a stricter reading of 6.6 would call for a pause, and it would add one paused run per accepted-pending cancel with its checkpoint data.
- **Thresholds for "materially larger"**: 25% and $20, and "closer" = within 2 days when previously further (4.5). If wrong: too many repeat alerts or a missed worsening; tune against the planted-dip eval.
- **Baseline run**: first detector run for a user emits no `new_subscription` (4.2). If wrong: a first run floods the alerts page with every existing subscription.
- **Cap formula** (4.6): recommended `round(gross*share) - committed`.

## 7. Test ideas

- Stream flag and overspend flag in the same category on one day: two alerts, correct kinds, no `KeyError`.
- User with no budget and a paycheck stream plus a dip: `low_balance` alert with `period_id None`; `period_ended` day behaves the same.
- Detector/forecast raising: run ends `ok`, overspend alert still written, `signals_error` in summary.
- Same dip on 3 consecutive days: 1 alert. Dip grows 30% and >$20: second alert. Dip moves from 5 days to 2 days: second alert. Dismissed then unchanged: none.
- `suggest_cancel` accept: next close lowers the category line by the stream's monthly amount; alerts for that stream stop; category with no line: no change, outcome recorded; same category also has an accepted `adjust_next_budget`: both apply, in order. Income-change model returns `suggest_cancel`: downgraded to `alert`. Model returns `move_from_savings` with no savings account: downgraded.
- Goal run: forecast source when a paycheck stream and 60+ days exist; fallback otherwise; summary records `surplus_source`; no double minimum subtraction; accepted goal does not understate the cap (the $1,000/$500 example gives $300); balance $100 vs $20,000 gives the same surplus; simulated date uses the simulated `today`.
- Paused budget run (with an `adjust_next_budget`) holding a low-balance signal: checkpoint file contains no bill name or balance.
- Stale paused run: goal graph never reads a stored figure from a run whose summary is empty.
- Chat: both tools in `bind_tools` (7 names); the prompt names them; scenario asserting `get_recurring` for "what subscriptions do I have" and `get_cash_forecast` for "will I run short before payday".
- Gateway: inflow stream has no merchant key; merchant with 5 digits scrubbed; audit line per signal.

## 8. Unverified

- Run time per simulated day with a daily detector; model-call retry counts on a live model; live `qwen2.5:14b` tool choice with 7 tools.
- What the budget checkpoint file actually holds after a pause (read from code only; `run_budget` has no `durability="exit"`); no test written.
- Whether `test_gateway.py` goal tests break when `GoalFacts` gains a defaulted field.
- The exact count of tests that change if the forecast replaces measured surplus (21 is the +1-cent sensitivity, not a forecast run); `goals-card-24` switching to the forecast path assumes the detector finds its Aug-Oct paychecks.
- The `suggest_cancel` floor rule when the lowered limit would be $0 (the 4.4 text hedges); `delay_spend` category selection uses `last_pace` without testing.
- The Plaid-Sandbox cross-check and `days_requested = 180` (not my concern).
