# TPC-31 investigation 2: forecast and surplus math

Scope: `budget_engine/forecast.py` and `monthly_surplus_cents`. Synthetic users only. All numbers come from throwaway prototypes (see Findings for the method); nothing in the repo was changed except this file.

## 1. Summary

- The ticket's everyday-spending method fails. On every accuracy profile the median day of non-recurring spending is $0 (26 of 90 days have a purchase), so "median day times 30" underestimates the next 30 days by 100%. The mean daily total is off by $34 to $170 on the same profiles. **Departure: mean daily total, with a band from weekly totals.**
- The ticket's surplus formula (expected balance at the next paycheck, minus buffer, minus events) counts the cash already in checking as monthly income. On `goals-card-24` it gives $9,353 to $11,024 a month (measured: $971.75) and moves by $1,671 inside one month. **Departure: a flow-based surplus** (monthly-normalised inflow streams, minus outflow streams, goals and everyday spending). It gives $905 to $1,085 (median $1,000), independent of balance, and within 5% of the measured figure.
- Forecast error is dominated by stream detection, not by everyday spending. With the stand-in detector, MAE is $81 at d+7 and $69 at d+14 (5 accuracy profiles); with perfect streams it is $21 and $38. Band coverage is 41 to 53%, far under any 80% target.
- The planted dip works: rent on the 13th, payday on the 15th, $1,650 in checking on Oct 9. The low path crosses $100 on Oct 13, first visible 7 days ahead, and names rent. False alarms exist only when the user's real minimum is within about $100 of the buffer.
- Goals as scheduled outflows makes surplus net of goals, but then `load_facts`'s `committed_cents` subtraction double counts. Keep a corrected form: `cap = share * (surplus_net + committed) - committed`.
- Synthetic accounts have `subtype=None`, `available_balance_cents=None`, a static $100 balance, and no card due dates. The forecast cannot read a balance from them without the seeding changes in section 4.

## 2. What exists today

- Measured surplus: `app/budget_engine/goals.py:149-158` (`monthly_surplus`, median of 3 complete months of income minus spending minus savings). Card minimums: `goals.py:163-164`. Used in `app/agents/goal_graph.py:120-154` (`load_facts`): `surplus = measured - card_minimums` (line 132), cap at line 143 `round(surplus * max_share) - committed`.
- Spending filter: `app/budget_engine/txns.py:38-44` drops INCOME, TRANSFER_*, SAVINGS and `LOAN_PAYMENTS_CREDIT_CARD_PAYMENT` unless overridden.
- Loader: `app/budget_service/initialize.py:53-63` `load_txns` reads transactions from **all** accounts (no account filter).
- Accounts: `app/db/models.py:44-62` (`current_balance_cents`, `available_balance_cents`, both nullable; `subtype` nullable). Liabilities: `models.py:65-77` (`min_payment_cents`, `next_due_date`, both nullable). Real sync fills them: `app/plaid_service/sync.py:75-76`, `211-222`.
- `load_debts`: `app/budget_service/debts.py:11-19` returns `Debt(key, name, kind, apr, balance_cents, min_payment_cents)`. It has **no due date**, and a null balance becomes 0. `incomplete_card_obligations` (`debts.py:22-29`) flags a card with unknown balance or minimum.
- Synthetic seeding: `eval/budget_users.py:91-105` `seed_profile` makes one `Checking` account via `tests/factories.py:20-28` (`type_="depository"`, `subtype=None`, `current_balance_cents=10000`, `available` unset). `GOAL_USERS` liabilities (`budget_users.py:172-176`) have no `next_due_date`. Simulated days only insert transactions (`app/budget_service/simulate.py:33-49`); balances never move.
- Alerts: `models.py:158-181`. `kind` exists with `low_balance` already allowed; there is no `stream_id` column yet.
- Goals: `models.py:202-218` (`monthly_cents`, `start_date`, `target_date`, `status`); `app/budget_service/goal_lines.py:14-17` `committed_goals`; `goals.py:99-100` `budgeted_in` = start month through the month of `target_date`.

## 3. Findings

**Method (all backtests).** Prototype in the scratchpad: a stand-in detector following the ticket's rules (180 days, 3+ txns, median gap 7/14/15/30/365 within 3 or 5 days, 10% amount rule, stale after two missed gaps) and a pure `forecast()`. As-of dates every 3 days starting 75 days into each history (monthly streams need 3 occurrences), using only transactions on or before the as-of date; the forecast runs 14 days. Because synthetic history never moves a balance, I rebuilt a ledger: closing balance(d) = B0 + cumulative inflow - outflow, for an arbitrary B0 ($3,000). The forecast starts from ledger(as-of) and is scored against ledger(as-of + 7) and (+14). "Stream stand-in" is therefore my own detector, not the profile definition, unless stated. Profiles: 5 accuracy, `goals-card-24`, 4 planted, plus my own `noisy-biweekly` (300 days, 55% of days with 1-3 random purchases at 15 merchants, rent, phone, gym, biweekly pay; not in the repo).

### Q1 and Q2: forecast error and the everyday method

MAE in dollars (mean absolute error of the expected balance), bias = forecast - actual, band = low/high path, coverage = actual balance inside it.

| Everyday method | Profiles | n | d+7 MAE | d+14 MAE | cov d+7 / d+14 | mean band width d+14 |
|---|---|---|---|---|---|---|
| ticket (median / p75 / p25 day) | 5 accuracy | 60 | $109 | $130 | 73% / 75% | n/a |
| mean + band from weekly totals | 5 accuracy | 60 | $81 | $69 | 48% / 53% | n/a |
| oracle streams (profile definition) | 5 accuracy | 60 | $21 | $38 | 0% (zero width) | |
| ticket | all 10 repo | 98 | $231 | $287 | 54% / 51% | $146 |
| mean + sd weekly | all 10 repo | 98 | $214 | $249 | 41% / 42% | $170 |
| weekly bootstrap (1,000 draws) | all 10 repo | 98 | $214 | $249 | 24% / 42% | $202 |
| median week / 7 | all 10 repo | 98 | $212 | $244 | 9% / 5% | 0 |
| ticket | noisy-biweekly | 71 | $134 | $230 | **0% / 0%** | **$0** |
| mean + sd weekly | noisy-biweekly | 71 | $138 | $229 | 14% / 15% | $97 |

The "all 10" rows include planted profiles with 17 to 37 transactions and a $600 one-off, which no method forecasts. Read the first block as the best case and the second as the realistic one.

Everyday spending itself (the residual after removing detected streams, 90 days, at one as-of per profile):
- `steady` at Aug 9: 26 of 90 days have a purchase; **median $0, p25 $0, p75 $23, mean $7.18**. `goals-card-24` at Oct 15: 24 of 90 days, median $0, p75 $23, mean $21.57. Median = $0 confirmed.
- Next-30-day actual vs prediction, mean bias / MAE over as-of dates:

| Profile | actual 30d | median*30 | p75*30 | mean*30 | median-week*30/7 |
|---|---|---|---|---|---|
| steady | $306 | -$306 / $306 | +$90 / $423 | -$63 / $69 | -$167 / $175 |
| vacation | $308 | -$308 / $308 | +$150 / $483 | +$170 / $170 | -$164 / $175 |
| rising | $306 | -$306 / $306 | +$98 / $431 | -$61 / $68 | -$161 / $174 |
| occasional | $310 | -$310 / $310 | +$149 / $483 | +$34 / $78 | -$168 / $175 |
| noisy-biweekly | $264 | -$264 / $264 | -$264 / $264 | -$138 / $145 | -$224 / $224 |

Median day times N underestimates by 100% everywhere. The p75 day times 30 overstates by $85 to $150 on steady profiles, so the "low path" is pessimistic by a made-up amount; on the noisy and planted profiles p75 is also $0. Mean and median-week are the only usable expected values; mean has the smallest error. The bootstrap gives the same MAE as mean + sd and costs a sampler, so use mean + sd.

Two cautions for the detector concern. (1) On `noisy-biweekly` the ticket's tolerances (7 +/- 3, 14, 15, 30 +/- 5) turned 11 of 17 "streams" into random merchants (e.g. a cafe with median gap 9, a bakery at 18). That emptied the residual (only 11 of 90 days had a purchase, p75 $0) and explains the 0% coverage. (2) Coverage is low for every method because bands only model spending noise; most error is stream timing and amounts (oracle MAE $21 vs detector $81). Do not promise 80% in the UI; report the measured coverage in the eval.

### Q3: starting balance
- `available_balance_cents` is None for every synthetic and seeded-test account (`factories.py:20-28` never sets it); `current_balance_cents` is $100 by default and is static during simulation. A real Plaid item fills both (`sync.py:75-76`), `available` can be None at some banks (nullable column).
- Synthetic `Checking` has `subtype=None`, so a filter `subtype == "checking"` would find no account for any synthetic user. Eval fixtures can set subtype (`eval/fixtures.py:194`), `seed_profile` cannot.
- Backtest handling: ledger reconstruction (above). The eval cannot compare against stored balances.
- Rule: forecast accounts = `type == "depository"` and `subtype in ("checking", None)`. Start = sum of `available`, else `current`, per account. **No such account** -> `ForecastResult(status="no_checking")`: no path, no alert, UI says "link a checking account". **Two** -> sum; transactions are also restricted to those two accounts (next point).
- **Card double count (verified in code, not run on Plaid data).** `load_txns` has no account filter. Purchases on a credit card are outflows in `transactions`, and the card payment from checking is a second outflow. Counting both would count card spending twice. The forecast must read only transactions whose `account_id` is a forecast account, so card spending reaches the path through the card payment (stream or minimum). Synthetic users have everything on checking, so this is invisible in the eval.

### Q4: card minimums
- `Liability.next_due_date` is populated by sync (`sync.py:221`) and **null** for every `GOAL_USERS` card. `load_debts` drops it, so the forecast needs a new loader (`load_card_obligations`) that returns `(account_id, name, min_cents, due_date, balance_cents)`. A null minimum stays null (`debts.py:19`); a null balance becomes 0, which is why `incomplete_card_obligations` exists.
- Null due date: do not invent one. Count the minimum in the surplus (monthly obligation), leave it off the path, add `assumptions: ["card_due_date_unknown:<name>"]`. A due date in the past (stale sync) rolls forward by whole months to the next one on or after today.
- "Covered by a stream" (my rule, unrun): an outflow stream covers card C when its `detailed == LOAN_PAYMENTS_CREDIT_CARD_PAYMENT` and its normalised merchant key contains C's normalised account name (synthetic: `Platinum Card` payments are merchant "Platinum Card"); if no name matches and the user has exactly one card and one card-payment stream, they pair. Leftover cards get the Liability minimum. A covered card uses the stream's amount (what the user really pays), not the minimum. On `goals-card-24` the $95 payment is a monthly stream (day 12, 3 occurrences), so no extra $95 is added; adding it would double count. Wrong pairing costs: false "covered" hides a minimum (optimistic); false "uncovered" doubles one (pessimistic). Choose pessimistic.

### Q5: surplus
Prototype on `goals-card-24` extended with the same patterns to Jan 31 (pay on the 15th; as-of every day Nov 1 to Dec 31, ledger B0 as shown). "Literal" = expected balance on payday (after it lands, from the forecast's expected path), minus $100 buffer, minus outflow-stream events in (next payday, the one after], times 30.4375 / cycle days. "+ everyday" also subtracts one cycle of mean everyday spending. "Flow" = sum over streams of typical * 30.4375 / gap (inflow positive) minus mean daily everyday * 30.4375.

| Profile, B0 | Definition | min | max | swing | median |
|---|---|---|---|---|---|
| goals-card-24, $3,000 | literal | $9,353 | $11,024 | $1,671 | $10,161 |
| goals-card-24, $3,000 | literal + everyday | $9,134 | $10,812 | $1,678 | $9,942 |
| goals-card-24, $8,000 | literal | $14,426 | $15,933 | $1,508 | $15,070 |
| steady, $3,000 | literal | $14,650 | $16,942 | $2,292 | $15,645 |
| goals-card-24 (either B0) | **flow** | $905 | $1,085 | **$180** | $1,000 |
| steady | **flow** | $1,266 | $1,445 | $179 | $1,308 |

Measured surplus (app code on the profile's 3 months, today Nov 2): $1,066.75, or **$971.75 after the $95 minimum**, matching the docs. The flow figure on the original history at Nov 2 is $1,025 (card payment already inside it), 5% above $971.75. Other profiles, flow vs measured at their own `today`: steady $1,395 / $1,331; vacation $909 / $1,034; early-rent $1,402 / $1,247; rising $1,315 / $1,278; occasional $1,319 / $1,214.

Why the literal formula fails: (1) it contains the stock of cash (balance ~$4,000 to $8,000), and "scaled to a month" multiplies it by 30/cycle, so every month looks like it has the whole balance spare (changing B0 from $3,000 to $8,000 adds $4,900); (2) it jumps by about $800 on payday because the balance steps; (3) it never subtracts the cycle's everyday spending unless added. "Scaled to a month" is undefined in the ticket; I used x 30.4375 / (paycheck-to-paycheck days).

**Departure from the ticket:** `monthly_surplus_cents` = flow definition above, clamped nothing (negative is real, as `monthly_surplus` says). The balance and buffer are reported separately as `cash_above_buffer_cents` for display, never fed to goals. Flow swings $180 over a month because weekly typical amounts drift; round to the nearest $10 and keep it from changing by more than 5% day to day (unverified rule) if the cap should not jump.

### Q6: accepted goals
- **Day.** Goal outflow date = the day after the user's first paycheck of each month (first inflow stream occurrence in the month + 1 day), else the 1st when there is no paycheck stream. Reason: the money leaves right after pay arrives, so a goal never lands on a low-balance day before payday (a goal on the 1st would stack with rent). Months: those where `budgeted_in(goal, month)` is true (`goals.py:99-100`): `start_date` (always the 1st of the next month) through the month of `target_date`. A goal accepted in November first appears in the December events, as the budget does. It is a plan; the app does not track real payments.
- **Net of goals.** `goals-card-24`: flow before goals $1,025 (includes the $95 card payment). Accept `debt_payoff` $583 and `emergency_fund` $194.40 = $777.40 committed. Forecast surplus with goal events = $247.60. Money is subtracted once.
- **`committed_cents` in `load_facts` (line 143).** Today `cap = round(surplus * 0.8) - committed`. With the forecast's net surplus and $583 accepted: net = $442; keeping the subtraction gives `0.8*442 - 583 = -$229` -> 0 (double count). Dropping it gives $353.60, and total goals reach 92% of the gross surplus, breaking `max_share`. The gross form `round(0.8 * (442 + 583)) - 583 = $237` equals what TPC-25 gives on a gross surplus. **Recommend the gross form**; it needs `ForecastResult.committed_goal_cents`. See Decisions.
- **Debt extras vs minimums.** `build_goal` treats the goal amount as an extra on top of minimums (`goals.py:196-197`). The path therefore has the minimum (card payment stream or Liability minimum) and the extra as a separate event: not double counted at acceptance. They are double counted once the user follows the plan, because the card-payment stream's typical rises to min + extra within 3 months. Rule (reasoned, not run): for `debt_payoff`, card/loan payment outflow = max(observed payment streams, minimums + extra) over the debts, i.e. `net_of_goal` logic (`txns.py:91-92`). A savings or emergency-fund goal pays into a savings transfer stream that may already exist (`profile BASE` has $500/month); the detector would see a second monthly transfer to the same merchant and mix amounts. I did not test this; see Unverified.

### Q7: fallback
Goals use the forecast's flow surplus unless one of these holds, in which case they use `monthly_surplus(...) - card_minimums` as today: (a) no active inflow stream; (b) fewer than 65 days of history before today (a monthly paycheck needs 3 occurrences) or fewer than 3 inflow transactions; (c) every inflow stream stale (`today > last + 2*gap`); (d) no forecast account (`no_checking`); (e) `incomplete_card_obligations` (already an error today, `goal_graph.py:126`). Record on the goal run summary: `surplus_source` ("forecast" or "measured"), `surplus_fallback_reason` (`no_inflow_stream`, `short_history`, `stale_paycheck`, `no_checking`), `forecast_surplus_cents`, `measured_surplus_cents` (both always, so the eval can compare). Compute the forecast inside `load_facts` with the same pure function rather than reading the last budget run, so goal runs on simulated users do not depend on a daily run.

### Q8: irregular income and paycheck detection
- Detector stand-in found `employer` correctly as monthly (gap 30) and biweekly (gap 14, noisy profile). Semi-monthly (1st and 15th) is unverified; a gap-based roll-forward drifts over 14/16-day pairs, so monthly-like streams use calendar day and the semi-monthly case needs two day-of-month anchors.
- Horizon = max(30 days, days to the next paycheck + 1), capped at 45.
- `steady` with the paycheck replaced by 2-4 random deposits a month from unique senders (~$4,200 a month): no inflow stream is found, the forecast assumes zero income, and at a $500 minimum cushion the 14-day low path alarmed in 3 of 17 as-of dates when no real dip followed (0 of 17 at $1,000 and $3,000). Small n; the point is that an irregular earner gets an alarm for money that arrives later. Set `assumptions: ["no_income_stream"]` on the result, show "assumes no income" on the chart, and let the run raise `low_balance` at most as an `alert`, never `move_from_savings`.

### Q9: planted dip
Profile `planted-dip` (id `budget-planted-dip`), `start=2026-07-01`, `end=2026-10-31`, `seed=31`, `run_date=2026-10-09`, `today=2026-10-01`, patterns:
`Monthly(RENT_AND_UTILITIES, "Landlord", 150000, day=13)`, `Monthly(INCOME, "Employer", -420000, day=15)`, `Monthly(TRANSFER_OUT, "Savings", 50000, day=16)`, `Monthly(ENTERTAINMENT, "Streamly", 1599, day=5)`, `Weekly(FOOD_AND_DRINK, "Grocer", 11000)`, `Weekly(TRANSPORTATION, "Fuel", 4000, weekday=0)`. Account `Checking`, `type="depository"`, `subtype="checking"`, `available_balance_cents = current_balance_cents = 165000` on the run date (needs a new profile field or seeding step; simulation will not move it). Prototype result (ledger closes $1,499 on Oct 12 and -$1 on Oct 13, 2 days before the 15th payday):

| As-of | First day low path < $100 | Shortfall | Event named |
|---|---|---|---|
| Oct 6 to Oct 9 | Oct 13 | $95 | landlord $1,500 |
| Oct 10, Oct 11 | Oct 13 | $97 | landlord $1,500 |
| Oct 12 | Oct 13 | $101 | landlord $1,500 |

Same for the ticket's low path and the mean + sd low path. Earliest lead is 7 days (Oct 6); the done-when needs 3 (Oct 10). Cause rule: the largest outflow event on or before the first dip day; if there is none, "everyday spending".

False alarms, prototype raised on the 9 non-planted repo profiles' ledgers, rescaled so the minimum ledger balance after day 75 is the cushion shown, alerting if any of the next 14 days' low path < $100, scored against the ledger (140 as-of dates per row):

| Cushion (true minimum) | method | TP | FP | FN | TN |
|---|---|---|---|---|---|
| $0 | ticket | 49 | 7 | 6 | 78 |
| $0 | mean+sd | 50 | 10 | 5 | 75 |
| $200 | ticket | 0 | 24 | 0 | 116 |
| $200 | mean+sd | 0 | 19 | 0 | 121 |
| $500 | ticket / mean+sd | 0 | 0 / 5 | 0 | 140 / 135 |
| $1,000 | both | 0 | 0 | 0 | 140 |

A false alarm is a low-path dip under $100 when the true balance stayed above it: it happens when the real minimum is about $100 to $400 above the buffer. None occur from $1,000 up. Misses (5 to 6 at $0) are mostly spending spikes and late detection.

## 4. Recommended design

**File:** `backend/app/budget_engine/forecast.py`, pure, no DB or clock.

```python
@dataclass(frozen=True)
class StreamIn:  key: str; direction: Literal["out","in"]; typical_cents: int; last_date: date
                 gap_days: int; next_date: date; stale: bool; detailed: Optional[str]; name: str
@dataclass(frozen=True)
class CardIn:    account_id: int; name: str; min_cents: Optional[int]; due_date: Optional[date]
@dataclass(frozen=True)
class GoalOut:   id: int; kind: str; monthly_cents: int; start_date: date; target_date: date
@dataclass(frozen=True)
class ForecastInput:
    today: date; start_balance_cents: Optional[int]       # None -> no_checking
    streams: Sequence[StreamIn]; cards: Sequence[CardIn]; goals: Sequence[GoalOut]
    everyday_daily_cents: Sequence[int]                    # 90 entries, oldest first, zero days included
    history_days: int; buffer_cents: int = 10_000
@dataclass(frozen=True)
class Event:     date: date; name: str; cents: int         # negative = out
                 source: Literal["stream","card_min","goal"]
@dataclass(frozen=True)
class Day:       date: date; expected_cents: int; low_cents: int; high_cents: int; events: tuple[Event, ...]
@dataclass(frozen=True)
class Dip:       date: date; shortfall_cents: int; cause: Optional[Event]; events: tuple[Event, ...]
@dataclass(frozen=True)
class ForecastResult:
    status: Literal["ok","no_checking","no_history"]
    days: tuple[Day, ...]; dip: Optional[Dip]                # first low-path day < buffer within 14 days
    monthly_surplus_cents: Optional[int]; committed_goal_cents: int
    cash_above_buffer_cents: Optional[int]
    surplus_fallback: Optional[str]                        # None | no_inflow_stream | short_history | stale_paycheck | no_checking
    assumptions: tuple[str, ...]                           # "no_income_stream", "card_due_date_unknown:<name>"
def forecast(inp: ForecastInput) -> ForecastResult
```
Service side (`budget_service/forecast_data.py`): `load_forecast_input(session, user_id, today)` restricts to forecast accounts, builds `everyday_daily_cents` from forecast-account outflow transactions in the last 90 days that belong to no stream (all streams, stale too), and `load_card_obligations`.

**Rules:** day 0 = today; events on dates in (today, today+horizon]; an overdue non-stale stream lands tomorrow; stale streams are dropped; monthly streams use calendar day-of-month (clamped), others `gap_days`. Everyday: expected = mean(daily); band at k days = mean*k +/- 1.2816 * sd(weekly totals) * sqrt(k/7), high clamped at the expected-minus value >= 0. Low path drives the alert; alert needs `low < buffer` on a day within 14. Surplus = flow definition (Q5); `committed_goal_cents` = sum of active goals' monthly. Goals' cap in `goal_graph.load_facts`: `cap = max(round(share * (S + C)) - C, 0)`, S = `monthly_surplus_cents`, C = `committed_goal_cents`, in place of line 143; remove `card_minimums` subtraction from the forecast path only (covered by streams or minimums inside the flow).
**Eval:** `eval/forecast_accuracy.py` with the backtest above: ledger from the profile (B0 chosen so the minimum is $1,000), MAE/bias/coverage at d+7 and d+14, plus the planted dip.
**Seeding:** add to `Profile` an `balance_cents: Optional[int]` and `subtype="checking"` for the checking account in `seed_profile`; add `next_due_date` to `GOAL_USERS` liabilities for a card-minimum test.

## 5. Departures from the ticket

1. **Everyday spending: mean daily total with a weekly-total band, not median/p75/p25 day.** Median day is $0; median*30 is -100% on all profiles, mean*30 within $34 to $170 (table in Q2). d+14 MAE falls from $130 to $69 on the accuracy profiles.
2. **Surplus: flow over monthly-normalised streams, not balance at the next paycheck.** Literal gives $9,353 to $11,024 (swing $1,671) against a measured $971.75; flow gives $905 to $1,085 (swing $180).
3. **Forecast reads only transactions on the forecast checking accounts** (the ticket says "last 90 days of non-recurring outflows" without a filter), to avoid counting card purchases and card payments both.
4. **Horizon capped at 45 days** and a minimum-history gate (65 days) for the surplus; the ticket gives neither.

## 6. Decisions for the user

- **Cap form.** (A) `share*(S+C) - C` (recommended; keeps `max_share` meaning, goals never exceed 80% of gross surplus). (B) `share*S_net` (simpler, total goals can reach 92%+ of gross). Costs if wrong: A looks like the subtraction survived; B lets repeated accepts commit nearly all surplus.
- **Null card due date.** Recommend minimum in surplus only, not on the path. If wrong the path is optimistic by one minimum and a dip near the due date is missed.
- **Goal day.** Recommend payday + 1. If goals are paid on the 1st the path shows an earlier trough; this is only a plan assumption since payments are untracked.
- **Band confidence.** Say "typical range", not "80%". Measured coverage is 41 to 53%.

## 7. Test ideas

- `forecast` with no checking account -> `no_checking`, no `dip`. Two checking accounts -> balances sum; a credit-card purchase on a third account is ignored.
- Median-$0 history (purchases on 25% of days): expected path falls by the mean, not zero.
- Rent 2 days before payday (the profile above): dip on rent's date, shortfall $95, cause rent, no alert when balance is $2,650.
- Paycheck stale (last pay 70 days ago, monthly): `surplus_fallback="stale_paycheck"`, no inflow events.
- Null `next_due_date`: assumption string present, minimum counted in surplus, none on the path. Past due date rolls forward.
- Card with a payment stream: no extra Liability minimum; card without one: minimum on due date.
- Goal with `start_date` next month: no event this month; `target_date` month is the last event; accepted goal reduces surplus by exactly its amount, cap follows the gross form.
- Monthly bill on the 31st in a 30-day month and on Feb 28: clamped.
- Overdue stream (next_date yesterday, not stale): lands tomorrow.

## 8. Unverified

- Anything on real Plaid data: balances (`available` often null), card-purchase double count, merchant names of card payments, `next_payment_due_date` quality.
- Goal payments appearing in history (savings transfers merging with an existing `Savings` stream, card-payment typical rising): reasoned only.
- Semi-monthly pay (1st and 15th) and weekly pay detection; streams with variable amounts such as utilities.
- The backtest used 98 samples at 3-day spacing (heavily autocorrelated) and one arbitrary B0 per scenario; the irregular-income test had 17 as-of dates. The noisy profile is my own, not a repo profile. The Plaid sandbox comparison (`/transactions/recurring/get`) was not touched.
- Alert repeat rule (material change, moving closer) and `decide_action` wiring: not examined, out of this concern.
- No unit tests were written or run against `forecast.py`; it does not exist yet. Prototype scripts are in the scratchpad `tpc31/forecast/` (not kept).
