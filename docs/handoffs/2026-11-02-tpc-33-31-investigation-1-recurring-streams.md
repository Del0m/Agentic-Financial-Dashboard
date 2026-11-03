# TPC-33 / TPC-31 investigation 1: recurring streams

Scope: the detector, the data it runs on, and the `recurring_streams` table. Synthetic users only. Experiments are throwaway scripts (listed in section 3 so they can be rebuilt); the repo was not changed except this file.

## 1. Summary

The ticket's detector finds every monthly bill, savings transfer and paycheck in the existing synthetic profiles (recall 0.98 on monthly streams (the one miss is a 2-charge gym, correct); 3 charges is enough for all of them, but only just). Three of its rules do not survive contact with the data and need the user's approval to change: (a) `price_increase` can never fire for a rise of 10% or more, because such a rise makes the stream `amount_varies` and the alert is defined for fixed streams only; (b) annual streams cannot be found in a 180-day window; (c) `next_date = last + median gap` is off by up to 4 days for monthly bills, which matters for TPC-31's "rent 2 days before payday" test. The merchant key must handle null `merchant_name` and the table has no raw `name` column, so the ticket's "digits and punctuation removed" rule mostly has nothing to act on. `days_requested` is still 90 in the code (both places) although the architecture changelog says it was raised to 180. The synthetic profiles cannot test the ticket's done-when: nothing generates biweekly pay or a price change, so `eval/budget_users.py` needs two new pattern types.

## 2. What exists today

- `backend/app/budget_engine/txns.py:11-18` `Txn(date, amount_cents, category, detailed, merchant, overridden)`: no id, no pending flag, no account. `:38-44` `spending()` drops categories INCOME, TRANSFER_IN, TRANSFER_OUT, SAVINGS_TRANSFER and detailed `LOAN_PAYMENTS_CREDIT_CARD_PAYMENT` (unless the user overrode). `:47` `savings()`. `:58` `normalize_merchant(name)` is `name.strip().casefold()` and raises `AttributeError` on `None`.
- `backend/app/budget_service/initialize.py:19-35` `resolve_category` (user_category, rule, our_category, savings transfer, Plaid) and `:53-63` `load_txns(session, user_id, start, end)`. It filters `removed` but NOT `pending`, and returns `Txn`s with the resolved category. The detector should be fed from this so categories agree with the rest of the app.
- `backend/app/db/models.py:79-98` `Transaction`: stores `merchant_name` (nullable), `pf_category_primary/detailed`, `pending`, `removed`; there is no raw `name`/description column. `plaid_service/sync.py:136-142` writes only `merchant_name` from Plaid.
- `models.py:94` `is_recurring`: written nowhere and read nowhere (grep over `backend/app`, `backend/tests`, `eval`, `frontend/src`: only the model, the initial migration `e1f04fa8eb57:99` and `docs/architecture.md:150`). It is a dead column.
- `models.py:158-182` `Alert`: `kind` (plain string; `AlertKind` already lists `new_subscription`, `price_increase`, `income_change`, `low_balance`) and `AlertAction` already lists `suggest_cancel`. There is NO `stream_id` column; `models.py:167` and `architecture.md:197` say it arrives with TPC-33. `architecture.md:162-171` already sketches both `alerts.stream_id` and the `recurring_streams` columns.
- `backend/app/plaid_service/client.py:50` (link token) and `:65` (sandbox public token): `"days_requested": 90`. `architecture.md:270` says "at least 180" and `:1019` logs "raised from 90 to 180", but the code was never changed.
- Daily run: `budget_graph.py:121-` nodes; `log_advice` (`:220-243`) writes `Alert(kind="overspend")` + `AdviceLog`; `decide_action` (`:189`) is per flag and `no_op` is allowed only if the user was already told at that severity (`:206-211`). Alembic head dir: `backend/alembic/versions/` (latest `a3d9e1b7c2f5_goal_start_date.py`; find the real head with `alembic heads` before writing the migration).
- Synthetic data: `eval/budget_users.py` patterns are only `Weekly`, `Monthly` (fixed cents, optional `moved`, `first`), `Once`. Profiles are seeded in full (through `end`, which is after `today`/`run_date`), so the detector MUST ignore dates after `today`.

## 3. Findings

### 3.1 Prototype on the synthetic profiles (questions 1 and 2)

Method: `seed_profile` into an in-memory SQLite, `load_txns`, then the ticket's rules literally (window 180 days up to `today`/`run_date`, key = lowercased with digits and punctuation removed, n >= 3, median gap within 7/14/15/30/365 +-3 (+-5 monthly), amounts within 10% of the median = fixed). Truth = what the profile plants: Monthly patterns (and, as a second count, Weekly merchants that have one weekday).

| Profile (today) | Monthly streams found | Notes |
|---|---|---|
| 5 `PROFILES` (Aug 1) | Landlord n=4; Employer, Savings, Streamly n=3 each (all 5 profiles) | gap median 30.5 or 31; every stream active |
| `overspend-one-time` / `-seasonal` (Oct 20 / Dec 15) | Landlord, Employer, Savings (n=4; Savings n=3 in seasonal) | |
| `overspend-recurring` (Oct 20) | Landlord, Employer, Savings, Barber (n=4) | FitClub Gym has 2 charges (Sep 3, Oct 3): correctly no stream |
| `overspend-known-merchant` | MegaMart n=5 (4 x $120 + $520 one-off on Oct 12) | `last_cents` = 52000 (the one-off), `amount_varies` true |
| `goals-card-24` (Nov 2) | Landlord, Employer, Savings, Streamly, Platinum Card, Student Loan, all n=3 | 3 is the minimum, so one missing, pending or removed charge removes the stream |

Precision/recall, monthly truth only: 41 planted monthly streams across the 10 profiles, 40 found (the miss is FitClub Gym with 2 charges, correct by the ticket's rule), 15 extras: recall 0.98. Extra streams reported: the Weekly merchants (Grocer, Fuel, Corner Shop; 13-16 charges, median gap 7, often `amount_varies` because of the +-10% jitter) and, in `vacation`, `Airline` (a false stream). Counting weekly merchants as planted: 54 true positives, 1 false, 1 missed (the Airline one-offs on May 12, Jun 10, Jul 12 at $200, $1,200, $250 land 30.5 days apart and pass as an `amount_varies` monthly stream): precision 0.98, recall 0.98 (the Diner buys on Wed and Fri, so its median gap is not 7 and it is not found; it is not a clean stream). Without weekly truth, monthly precision is 0.73 (40 of 55 reported), so the eval must decide up front whether weekly spending habits are streams (see decision D3).

Income: every profile has `Employer`, `INCOME`, `-420000`, day 15, monthly (never biweekly); the merchant is the same string every time, so the key is stable. The paycheck stream is detected in all profiles (direction `in`, monthly). Rent (`Landlord`, day 1) is detected in all (n=4 in `PROFILES`/`PLANTED`, n=3 in `goals-card-24`). Real Plaid paychecks with a null `merchant_name` are unverified (no Sandbox call made).

History (question 2): at their run date the profiles hold 92 to 111 days of data (touching 3 to 4 months). Counts of a monthly merchant: day-1 rent 4, day 5 to 20 bills 3 (6 for `goals-card-24`: all 3). Over every end date of a year and four bill days, a 90-day window holds 3 monthly charges 95.3% of days, 2 charges on 4.4% (no stream), 4 on 0.3%; a 120-day window never holds fewer than 3. Weekly 90 days: 12+ charges; biweekly: 6+; 15-day: 5+. So the "3+ in 180 days" rule can pass for monthly streams with 90 days of data, but with a hole on 4.4% of days. `days_requested` is 90 at `client.py:50,65`. Raising it to 180 only helps items created afterwards (unverified: Plaid fixes it when the item is created). Raising only fixes the Plaid pull; the DB keeps rows, so a stream seen once does not later disappear from lack of history, only from rolling out of the 180-day window.

### 3.2 Merchant key (question 3)

Method: literal strings through the ticket rule and `normalize_merchant`.

| Input | Ticket key | `normalize_merchant` |
|---|---|---|
| `NETFLIX.COM 8443` / `Netflix.com 1123` | `netflixcom` (both) | `netflix.com 8443` / `netflix.com 1123` (differ) |
| `7-Eleven`, `3M`, `H&M`, `AT&T` | `eleven`, `m`, `hm`, `att` | kept as typed |
| `Amazon Prime*1A2B3` | `amazon primeab` (alphanumerics leave residue) | `amazon prime*1a2b3` |
| `12345`, `""`, `None` | `None` (no key) | `12345`, `""`, crashes with `AttributeError` |

Four Netflix charges with the four raw variants form one stream with the ticket key and none with `normalize_merchant`. But our table stores only Plaid's `merchant_name` (already clean, e.g. `Netflix`), so with real data the digit stripping matters only for unenriched descriptions; the ticket's own example (`NETFLIX.COM 8443`) is a raw `name` we do not store. Null merchants: with no key, the paycheck case (3 null-merchant `INCOME_WAGES` credits) yields no stream when nulls are skipped, and a stream when keyed `~INCOME_WAGES`. Keying all nulls by detailed category is dangerous for spending: 12 random restaurant purchases over 90 days gave a false stream in 424 of 500 seeds (374/500 even with a 25% amount band); skipping nulls gave 0/500. Eight named cafes (40 random purchases): 0/500 false streams.

### 3.3 Edge cases (question 4), literals through the prototype, today set just after the last charge

| Case | Result |
|---|---|
| 2 monthly visits (gym) | no stream (n < 3) |
| 3 visits, gaps 5 and 80 days | no stream (median 42.5 outside every band) |
| 3 monthly visits | stream, monthly, gap 30.5, fixed |
| Bill on every day 1-31, paid next business day, 372 start months, 3 charges | monthly in 372/372; but `next_date` vs real next charge: error 0 days in 37, 1 in 162, 2 in 68, 3 in 78, 4 in 27 (ticket rule with banker's `round`). With `ceil(median)`: 61, 138, 95, 51, 27. Calendar rule (same day-of-month as the latest 3 charges' max): 125, 105, 48, 51, 33, plus 10 cases off by 29-31 days (my month-end handling bug, not a keeper) |
| Biweekly pay from 14 start days | `biweekly` 14/14 |
| Semi-monthly 1st/15th (6 phases) | classified `biweekly` 6/6 (gaps 14,17,14,16..., median 15.5 or 14.x lands nearest 14); 15th/30th, 10th/25th, 5th/20th: `semi_monthly` 18/18 |
| Biweekly vs semi-monthly: "all dates share one weekday" rule | biweekly 14/14; semi-monthly 1/15 would be separated (not measured on the other pairs) |
| Gym $30 x3 then $32 (+6.7%) | fixed, typical 3000, last 3200: price_increase fires |
| Gym $30 x3 then $34.50 (+15%) | `amount_varies` = true: the stream is no longer "fixed", so the ticket's price_increase rule does not apply: NO ALERT |
| Gym 3000, 3000, 3450, 3450 | median 3225, all within 10%: fixed, last 3450 is 7% above typical: fires at every later run until the median catches up |
| Gym 3000 x3 then 3450 x3 | fixed (median 3225), still 7% above: still fires on every run |
| Netflix + $5 extra charge between bills | `amount_varies`, `last_cents` = 500 (a later date wins), so `last_cents`/`typical_cents` are wrong |
| Same, dominant-amount-cluster rule (drop charges more than 10% off a cluster of >= 3 that is more than half the group) | stream n=3, last 1599, fixed |
| MegaMart (4 x $120 + $520 on Oct 12, the planted profile) | ticket rule: `amount_varies`, `last_cents` 52000 (a 333% "price increase" if alerted); with the cluster rule: n=4, last 12000, fixed |
| Utility 8000/11500/9400/10200 | `amount_varies`, typical 10200 (cluster rule leaves it unchanged) |
| Refund of one Netflix charge (negative amount, same merchant) | out stream unaffected (refund is its own direction group, n=1) |
| Monthly cashback or roommate transfer, category TRANSFER_IN, n=3 | an inflow stream, monthly. It is not income: `income_change` must test the resolved category == `INCOME` |
| Paycheck last on May 15 (monthly), today Jun 20 / Jul 20 / Aug 1 / Aug 25 | active (next Jun 14) / stale / stale / stale. Stale after `last + 2 gaps + tolerance` = 65 days |
| Annual charge, 3 charges over 731 days, window 180 | no stream; window 800 finds it. In a 180-day window an annual charge appears at most once, so 365-day streams can never have 3 transactions |
| Biweekly pay x6 | `biweekly` 14, 185000 |
| Diner on Wed and Fri, 10 weeks | no stream (gaps alternate 2 and 5) |
| Airline one-offs ~30 days apart | `amount_varies` monthly stream (false positive) |

The overlap of `semi_monthly` (15 +-3) and `biweekly` (14 +-3) is real: median 14.5 or 15.5 matches both. Ties in my prototype went to whichever target was nearer, then `biweekly`.

### 3.4 Exclusions (question 5)

`spending()` removes transfers, income, savings transfers and card payments because they are not spending. For streams the question is different: the forecast needs every dated cash outflow from checking. The Platinum Card payment ($95), Student Loan ($210) and Savings transfer ($500) are real outflows in `goals-card-24` and all were detected as monthly streams. If streams excluded them, TPC-31 would lose them from the forecast entirely (everyday spending runs through `spending()`, which already drops them). So: keep every direction/category in `recurring_streams`, store the resolved `category`/`detailed`, and filter at the consumers.
- Alerts (`new_subscription`, `price_increase`) only for streams whose key passes `spending()` and are not goal-related; never for card payments, transfers, savings or loan payments.
- `income_change` only for inflow streams with resolved category `INCOME`.
- Goal contributions: an accepted goal pays through a savings transfer (`SAVINGS_TRANSFER`, a stream) or a loan payment (`LOAN_PAYMENTS`, a stream). TPC-31 will model each active goal as its own dated outflow, so those streams would be counted twice. Rule I recommend: the forecast skips streams whose resolved category is `SAVINGS_TRANSFER`, or whose category equals a `GOAL_*` category or an active debt goal's target, while an accepted goal exists; the exact skip rule is for the forecast concern. The pre-goal baseline savings transfer in `BASE` ($500) is such a stream too.
- "Non-recurring outflows" (TPC-31) = `spending(txns)` minus transactions of active outflow streams. Card payments and savings transfers sit in neither set, so they never reach the median day (correct) and reach the forecast only through their stream or the card minimum (as the ticket says).

## 4. Recommended design

**Files**: `backend/app/budget_engine/recurring.py` (pure), `backend/app/budget_service/recurring.py` (load, upsert, compare), `backend/alembic/versions/<new>_recurring_streams.py`, `models.py` (`RecurringStream`, `Alert.stream_id`), `eval/budget_users.py` additions.

```python
Frequency = Literal["weekly","biweekly","semi_monthly","monthly"]   # "annual" stays in the enum, never produced
@dataclass(frozen=True)
class RTxn: date: date; amount_cents: int; merchant: Optional[str]; category: str; detailed: Optional[str]
@dataclass(frozen=True)
class Stream:
    direction: Literal["in","out"]; merchant_key: str; merchant_name: str; category: str; frequency: Frequency
    typical_cents: int; last_cents: int; amount_varies: bool; first_date: date; last_date: date
    next_date: date; gap_days: int; n: int; status: Literal["active","stale"]
def merchant_key(name: Optional[str]) -> Optional[str]      # ticket rule + collapse spaces; None for None/""/all digits
def detect(txns: Sequence[RTxn], today: date, *, window_days: int = 180) -> list[Stream]
def project(stream: Stream, through: date) -> list[date]    # forecast: next_date, then each cadence step <= through
```

**Detector rules** (tested above unless noted):
1. Caller passes non-removed, non-pending rows (pending is not filtered by `load_txns`; add `pending == False` in the new loader). Keep `date <= today` and `today - 180d <= date`.
2. Key = `(direction, merchant_key)`; direction from the sign (positive = out). Null or empty merchant: skip, except inflow with detailed `INCOME_*`, keyed `~<detailed>` (so null-merchant paychecks work). Do not use a category fallback for outflows (424/500 false streams).
3. Within each group, form the cadence on the dominant amount cluster when one exists (amounts within 10% of an amount; >= 3 members and more than half of the group); extras are dropped from `last_cents`, `typical_cents`, gaps. Otherwise use the whole group (utilities).
4. Need >= 3 charges. Median gap -> frequency: 7, 14, 15 each +-3; 30 +-5. Between 14 and 15: biweekly if every date has the same weekday, else semi_monthly. Median of 2 gaps = their mean.
5. `amount_varies`: any amount more than 10% off the median. Departure D-2 below: also reject a varying group whose largest amount is more than 3x its smallest (kills Airline: 1200/200) unless category is a bill category (utilities).
6. `typical_cents`: median of all amounts (fixed) or of the last 3 (varies). `next_date = last_date + round-half-up(median gap)`; `stale` when `today > last_date + 2*gap + tol`.
7. `project(stream, through)`: dates `next_date + k*gap` (weekly/biweekly exact; monthly: same day-of-month as `next_date`, clamped to month end; semi_monthly: add 15 days then re-anchor to the stream's day pair). If `next_date < today` (late bill) the forecast concern decides; the detector keeps the raw `next_date` (the profile `goals-card-24` gave Landlord `next_date` Oct 31 on Nov 2, because the seeded Nov 1 rent does not exist).

**Table `recurring_streams`** (matches architecture 162-171, plus what the upsert needs): `id`, `user_id` (FK, index), `direction` ('in'|'out'), `merchant_key`, `merchant_name` (display), `category`, `detailed`, `frequency`, `typical_cents`, `last_cents`, `amount_varies`, `first_date`, `last_date`, `next_date`, `gap_days`, `status` ('active'|'stale'), `first_seen_run_id` (FK agent_runs, null), `first_seen_date`, `updated_at`, `alerted_price_cents` (null; the price last alerted), `dismissed` (bool, the accepted `suggest_cancel`). Unique `(user_id, direction, merchant_key)`; index `(user_id, status)`. `Alert.stream_id` nullable FK `recurring_streams.id` (with a named FK constraint; SQLite batch mode as in `e0cdfdbdab45_advice_alert_link.py`).

**Identity and upsert**: a stream is its unique key `(user_id, direction, merchant_key)`. A run loads the existing rows first, runs `detect`, then in one transaction: updates matching rows; inserts new ones (`first_seen_run_id`, `first_seen_date = today`); any stored row not returned by the detector is set `stale` (never deleted, so alerts and history keep their FK). A stale stream that returns to `active` is the same row and is not a `new_subscription`; it gets no alert unless `status` was `stale` and the stream is `income` (then no `income_change` either: the income came back). If the cadence changes (monthly -> biweekly), the same row's `frequency` updates; a different merchant key is a new stream.

**Alert rules** (run `detect_recurring` before diagnosing; comparison uses the rows read BEFORE the upsert):
- `new_subscription`: stream inserted in this run, outflow, passes `spending()` and is not `SAVINGS_TRANSFER`, and `first_date - gap_days > earliest transaction date of the user` (proves it did not exist when our history began). This stops the day-1 flood: the first run of a user with 8 streams must not raise 8 alerts. Latency is two cycles after the first charge (the 3rd charge makes the stream); for a monthly gym that is about 60 days.
- `price_increase`: `last_date` advanced since the previous run; the previous charges (up to 3, at least 2, excluding the latest) are within 10% of their median; latest charge >= baseline x 1.05 and >= baseline + 100 cents. Set `alerted_price_cents = last_cents`; do not alert again while `last_cents` is within 5% of `alerted_price_cents`. This replaces the ticket's "last vs typical on a fixed stream" (Departure D-1): it fires for any size of rise, once.
- `income_change`: inflow, category `INCOME`: `status` became `stale`, or latest amount >= 10% below the median of the earlier amounts. Once per transition (store nothing new: compare with the previous row's status/last_cents).
- `alerts.kind` already allows the three values; `stream_id` is the FK. `suggest_cancel` accepted -> set `dismissed = true` on the stream (no further alerts) and lower next period's line (specified in architecture 6a.1).

**Eval additions** (`eval/budget_users.py`): `Every(category, merchant, cents, every_days, start, ...)` (biweekly pay) and a price step on `Monthly` (e.g. `steps: dict[date,int]` cents from a date). Profile `streams-planted`: Netflix monthly $15.99, gym monthly that rises (from $30 to $34.50 on a later month), biweekly pay of -$1,850, a two-visit merchant, rent, one Once extra at a streaming merchant. Precision/recall over (direction, key, frequency) vs the planted list.

## 5. Departures from the ticket

- **D-1 Price increase:** ticket: alert on a "fixed" stream where `last_cents` is >= 5% and $1 above `typical_cents`. Evidence: +15% on a $30 gym gives `amount_varies`, no alert; +6.7% fires; 3000,3000,3450,3450 fires on every run. The done-when ("price-increase alert fires on the run after the higher charge") only holds if the planted rise is under 10%. Recommend the baseline-of-prior-charges rule in section 4 (any size, once).
- **D-2 Annual:** a 180-day window holds at most 1 annual charge, so the ticket's 365-day class can never reach 3 (it needs 731 days; I found a stream only at window 800). Recommend dropping annual from detection (keep the enum value). Cost: annual bills do not enter the forecast.
- **D-3 `next_date`:** `last + median gap` was off by 0 / 1 / 2 / 3 / 4 days in 37 / 162 / 68 / 78 / 27 of 372 monthly cases. Rent two days before payday (TPC-31's test) can flip on this. Recommend monthly = same day-of-month as the previous charge (clamped); I did not validate the replacement beyond the failed attempt above, so treat it as a recommendation to build and test, not a measurement.
- **D-4 Airline-style false stream:** the ticket rule has no sanity limit on `amount_varies` streams. Max/min ratio limit of 3x (not run) is proposed; a fix is to run the detector tests on `vacation` and expect no Airline stream.
- **D-5 `days_requested`:** the architecture changelog says it was raised; the code did not. Not a departure from the ticket, a defect: `client.py:50,65` must change to 180 (affects new items only, unverified).

## 6. Decisions for the user

- **D1 (price-increase rule, D-1):** adopt the prior-charges baseline. Cost if wrong: the ticket's literal rule silently misses rises over 10%, which fails the done-when for a gym going from $30 to $35.
- **D2 (first-run flood):** suppress `new_subscription` for streams that predate the user's history (section 4). Alternative: allow it but cap at one summary alert. Recommend suppression. Cost if wrong: either 8 alerts on day 1, or a real subscription that started in the first month is not announced.
- **D3 (weekly spending habits):** are Grocer, Fuel and Corner Shop streams? The ticket's 7-day class says yes. Recommend yes in the table, but flagged by an `is_fixed` view (amount_varies, category FOOD/TRANSPORT) so only `fixed` and bill-category streams drive alerts. If they are streams, TPC-31's "everyday spending" loses the groceries and fuel and models them as scheduled weekly events instead; if not, they stay in the median-day estimate. The forecast concern should say which is more accurate.
- **D4 (null merchants):** store the raw Plaid `name` on `transactions` (new column; sync only fills new rows, so old rows stay null unless re-synced). Recommend: not now; use the INCOME-only fallback key. Cost if wrong: real null-merchant bills (rent by check) are never streams.

## 7. Test ideas

- Exactly 3 charges, one of them pending or removed: no stream until it posts.
- Charge dated after `today` (the profiles seed future dates): ignored.
- Two visits, and three irregular visits: no stream.
- Bills moved by weekends and short months (31st): still monthly; `next_date` within 4 days.
- Price rise +6.7%, +15%, +15% repeated for three months: one alert each time, exactly one.
- Extra charge in a stream (Netflix +$5; MegaMart $520): stream stays fixed, no price alert on the extra.
- Biweekly pay vs 1st/15th pay vs 15th/30th pay: three correct frequencies.
- Paycheck stops: stale after 65 days (monthly), `income_change` once; paycheck returns: the same row goes active, no `new_subscription`.
- Run twice on the same day: no duplicate stream, no duplicate alert.
- First run for a user with existing streams: no `new_subscription`; a stream that starts mid-history: one.
- Roommate TRANSFER_IN monthly credit: stream stored, no `income_change`.
- Null-merchant paycheck: an inflow stream; null-merchant restaurant charges: none.
- Raw-descriptor merchants (`NETFLIX.COM 8443` / `Netflix.com 1123`): one key.
- Goal contribution and savings transfer: stored with their category, no `new_subscription`.

## 8. Unverified

- Real Plaid behaviour: whether paychecks and rent carry a stable or null `merchant_name`, and whether `days_requested` can be changed after item creation. No Sandbox call was made.
- The Sandbox comparison with `/transactions/recurring/get` (planned for later).
- Dominant-cluster rule beyond the three literals shown; the 3x max/min guard (D-4); the calendar `next_date` rule (my attempt had a month-end bug); the weekday rule for biweekly vs semi-monthly only on biweekly and 4 semi-monthly pairs for the median rule, not the weekday rule on semi-monthly.
- Cost of running the detector each day (not timed); migration on Postgres (SQLite only).
- Experiment scripts live in `/private/tmp/claude-501/-Users-armindelmo-Documents-College-Fall26-COP6938-Plaid-Agent-CAP6938/67a20a0f-f748-42f9-8d6c-4dd81dce6637/scratchpad/tpc31/recurring/` (`proto.py`, `run1.py`, `hist.py`, `keys.py`, `edge.py`, `edge2.py`); they are not kept.
