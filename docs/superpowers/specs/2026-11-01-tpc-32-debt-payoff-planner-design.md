# TPC-32: Debt-payoff planner (avalanche vs snowball)

Jira: TPC-32 (epic TPC-5, M4). Branch: `TPC-32`, cut from `TPC-22` after #17 merged (main plus the `updated architecture` commit).

## Goal

Answer "how long until I'm debt-free, and what does paying more change?" with exact numbers code computes, everywhere the user can ask:
- **Planner.** `budget_engine/payoff.py` simulates paying off every debt month by month, under the avalanche and snowball strategies, with an optional extra monthly amount.
- **API and dashboard.** `POST /debt-plan` and a `DebtPlanCompare` card under the Debts card, with an "extra per month" input.
- **Chat.** A `plan_debt_payoff` tool through a new gateway purpose `debt_plan`. The model gets computed results only, with names scrubbed, and never does the amortization itself.

TPC-23 builds on this next: its `debt_payoff` goals come from the planner, with `goals.strategy`.

**Done when:**
- Unit tests cover the avalanche and snowball orders, minimum rollover, `never`, and debts without an APR.
- In chat, "How fast am I debt-free if I add $200 a month?" returns the same months and interest (within $1) as `payoff.py` run directly.
- Every test is documented in `docs/tests/`.

**Out of scope:**
- goals (TPC-23) and the Goal Agent's use of the planner;
- the daily run's `extra_payment` alert (section 6a.3, after goals exist);
- the forecast's `monthly_surplus_cents` (TPC-31);
- daily compounding, minimums that shrink with the balance, promotional APRs and fees.

## Decisions

| Decision | Why |
|---|---|
| The balance is the account's `current_balance_cents`, not `liabilities.balance_cents` | The dashboard and the `debts` gateway purpose already use it (review fix on #6), so every view of a debt agrees |
| A debt with no APR, or no minimum payment, is left out and listed with its reason | Inventing a rate or a minimum would make the plan look exact when it isn't. Same rule section 6a.3 sets for a missing APR |
| Mortgages are left out unless `include_mortgage` | Section 6a.3. A 30-year mortgage dominates "debt-free" and is rarely what the user means |
| Interest is monthly: balance × APR / 12, rounded to the cent each month | Section 6a.3. Close enough for planning; the output says so |
| Minimums stay at today's figure | Section 6a.3. Real card minimums shrink with the balance; holding them is the conservative choice, and the output says so |
| A paid-off debt's minimum rolls into the extra, and money left after clearing a debt spills to the next target in the same month | What makes both methods "snowball"; spill-over means no payment is lost in the month a debt clears |
| Avalanche targets the highest APR (ties by `finance.avalanche_key`); snowball targets the smallest balance (ties: higher APR, then key) | The two methods' definitions, with deterministic ties |
| `never` when a debt's minimum doesn't cover its interest and nothing else reaches it; the simulation stops at 600 months | Section 6a.3. The plan names the debt instead of looping forever |
| The plan starts the month after today | The current month's payment may already be made; payoff dates read as whole months (`YYYY-MM`) |
| `compare` always includes a $0-extra baseline | So every answer can state the difference an extra amount makes |
| One loader, `budget_service/debts.load_debts`, for the API and the gateway | They can never plan from different numbers |
| The gateway runs the planner for `debt_plan`, like `debts` reads its rows | The tool stays a thin closure over one gateway; only computed, scrubbed results reach the prompt and the audit log |
| `get_debts` stays as it is | It answers "what do I owe"; the planner answers "how long, and what if" |

## Files

**New:**
- `backend/app/budget_engine/payoff.py`: `Debt`, `Payoff`, `Plan`, `simulate`, `compare`.
- `backend/app/budget_service/debts.py`: `load_debts`.
- `backend/app/api/debt_plan.py`: `POST /debt-plan`.
- `frontend/components/DebtPlanCompare.tsx` (+ `.test.tsx`).
- `eval/scenarios/debt-plan-extra-200.json`.
- Tests: `backend/tests/test_payoff.py`, `test_debts.py`, `test_debt_plan_api.py`, with docs in `docs/tests/`.

**Changed:**
- `backend/app/privacy/gateway.py`: the `debt_plan` purpose.
- `backend/app/agents/tools.py`: the `plan_debt_payoff` tool.
- `backend/app/main.py`: register the router.
- `frontend/lib/api.ts` (+ test): `debtPlan()` and its types.
- `frontend/app/dashboard/page.tsx`: render the card when there are debts.
- Tests: `test_gateway.py`, `test_tools.py`, `test_chat_graph.py` (the live done-when).
- Docs: `docs/architecture.md` (6a.3, API table, decision log), `README.md`.

## Budget engine (`budget_engine/payoff.py`)

Pure functions, no DB, integer cents throughout.

```python
@dataclass(frozen=True)
class Debt:
    key: int                       # the account id
    name: str
    kind: Literal["credit", "student", "mortgage"]
    apr: Optional[float]           # percent, e.g. 24.99
    balance_cents: int
    min_payment_cents: Optional[int]

@dataclass(frozen=True)
class Payoff:
    key: int
    name: str
    month: int                     # 1 = the first planned month
    date: date                     # first of that month
    interest_cents: int            # interest this debt accrued until paid off

@dataclass(frozen=True)
class Plan:
    strategy: Literal["avalanche", "snowball"]
    extra_monthly_cents: int
    months: Optional[int]          # None when never
    debt_free: Optional[date]
    total_interest_cents: int      # up to the stop (600 months) when never
    order: tuple[Payoff, ...]      # in payoff order
    never: tuple[int, ...]         # keys whose minimum doesn't cover their interest and nothing reaches them
    excluded: tuple[tuple[int, str], ...]   # (key, "no_apr" | "no_minimum" | "mortgage")

def simulate(debts, extra_monthly_cents, strategy, *, start, include_mortgage=False) -> Plan
def compare(debts, extra_monthly_cents, *, start, include_mortgage=False) -> dict[str, Plan]
    # {"avalanche": ..., "snowball": ..., "baseline_avalanche": ..., "baseline_snowball": ...}
```

`start` is the first planned month (the 1st of the month after today; callers pass it, so the function stays pure).

**Setup:**
- Leave out excluded debts (reason order: `mortgage` when not included, then `no_apr`, then `no_minimum`).
- Debts with a balance of $0 or less are left out silently: there's nothing to pay.
- With no debts left, the plan has `months = 0`, `debt_free = None` and no interest.

**Each month:**
1. **Interest:** every open debt's balance grows by `round_half_up(balance × apr / 100 / 12)`.
2. **Minimums:** each open debt gets `min(minimum, balance)`.
3. **Extra:** the pool is the extra plus the minimums of debts already paid off in earlier months. It goes to the target (by strategy, among open debts), capped at the target's balance. What's left spills to the next target, until the pool is empty or every debt is clear.
4. A debt whose balance reaches 0 this month gets a `Payoff` with this month's number and date.

**Never:** the simulation stops after 600 months. Any debt still open then is in `never` (typically one whose minimum doesn't cover its interest and that the extra never reaches), and `months`/`debt_free` are `None`.

**Targets:**
- avalanche: sort by `finance.avalanche_key(apr, balance)`, highest APR first, larger balance on a tie.
- snowball: smallest current balance first, then higher APR, then key.

## Loading debts (`budget_service/debts.py`)

`load_debts(session, user_id) -> list[Debt]`: joins `liabilities` to `accounts` for this user only. The balance is `accounts.current_balance_cents` (`None` is treated as $0, so the debt is left out). The name is the account's real name; only the API returns it unscrubbed.

## API (`POST /debt-plan`)

```
POST /debt-plan  { extra_monthly_cents: int (0 ≤ x ≤ 100_000_000), include_mortgage: bool = false }
-> { avalanche: PlanOut, snowball: PlanOut, baseline: { avalanche: PlanOut, snowball: PlanOut },
     excluded: [{ account_id, name, reason }], start: "YYYY-MM-DD" }
PlanOut = { strategy, extra_monthly_cents, months, debt_free, total_interest_cents,
            order: [{ account_id, name, month, date, interest_cents }], never: [account_id] }
```

- The dev user (as every other route), `422` outside the bounds.
- A user with no debts gets plans with `months: 0` and empty lists, not an error.
- `start` is the 1st of the month after today (`date.today()`, the same date the gateway uses).

## Gateway (`debt_plan` purpose)

`build_context("debt_plan", extra_monthly_cents=..., strategy=None | "avalanche" | "snowball")`:
- loads with `load_debts` and runs `compare` with `start` from `_today()`;
- returns the asked strategy (or both), each with its baseline and the difference. The shape (the numbers are illustrative, not computed):

```json
{"purpose": "debt_plan", "data": {
  "extra_monthly": 200.00, "start": "2026-12",
  "plans": [{"strategy": "avalanche", "months": 38, "debt_free": "2030-01", "total_interest": 4211.07,
             "order": [{"name": "Platinum Card", "paid_off": "2028-03", "interest": 1302.55}, ...],
             "without_extra": {"months": 52, "total_interest": 6140.12},
             "difference": {"months_sooner": 14, "interest_saved": 1929.05}}],
  "never": [], "excluded": [{"name": "...", "reason": "no APR on file"}],
  "assumptions": "Interest is charged monthly at APR/12; minimum payments are held at today's amounts; not financial advice."
}}
```

- Names go through `scrub_account_name`; money is dollars to the cent; months are `YYYY-MM`.
- The extra and the strategy are audit params, like `start`/`end` for spending.
- An unknown strategy raises `ValueError` (the tool turns bad input into a `ToolException` before this).

## Chat tool (`plan_debt_payoff`)

```python
def plan_debt_payoff(extra_monthly: float = 0, strategy: Optional[str] = None) -> str
```

- `extra_monthly` in dollars, ≥ 0 and ≤ 1,000,000, converted to cents with `Decimal`; otherwise a `ToolException`.
- `strategy` is blank-or-`avalanche`-or-`snowball`, case-insensitive; anything else is a `ToolException` echoing the value (like `get_spending`'s category).
- Description: "Simulate paying off the user's debts month by month (avalanche: highest APR first; snowball: smallest balance first), optionally with an extra amount per month. Returns months to debt-free, total interest, payoff dates and what the extra saves. Use for how long until debt-free, what paying more changes, or avalanche vs snowball. Quote its numbers exactly."
- The 6-round cap and the chat prompt are unchanged.

## Frontend

**`DebtPlanCompare`**, under `DebtsTable` on the dashboard, shown when `data.debts` is not empty:
- An "Extra per month" dollar input (default $0, whole dollars, ≥ 0). Changes are debounced by 300 ms; each calls `debtPlan(extraCents)`. A stale response (an older input) is ignored.
- Two columns, avalanche and snowball: debt-free month and months, total interest, then the payoff order with each debt's month. The lower-interest strategy gets a "saves the most interest" tag (ties: none).
- Each column ends with its difference against $0 extra, e.g. "+$200/mo: 14 months sooner, $1,929.05 less interest" (hidden at $0 extra).
- Excluded debts listed with their reason; a `never` plan reads "<name>'s minimum payment doesn't cover its interest."
- Footnote: the two simplifications and "Not financial advice."
- Errors use `errorMessage()` like the other cards.

## Tests

**`test_payoff.py` (pure):**
- avalanche pays the high-APR card first, snowball the small balance first, on the same debts;
- rollover: once the first debt clears, the second clears sooner than on its own minimum;
- spill-over: in the month a debt clears, the leftover reaches the next debt that month;
- a hand-computed two-debt, few-month case, checked to the cent (interest per month, payoff months, totals);
- `never`: a minimum below the interest with $0 extra stops at 600 months and names the debt; with enough extra it pays off;
- excluded: no APR, no minimum, mortgage (and included with `include_mortgage`), each with its reason; $0 balances left out;
- no debts: `months = 0`;
- `compare`'s baselines equal `simulate` with extra 0; an extra never makes a plan slower or costlier.

**`test_debts.py`:** `load_debts` uses the account balance, not `liabilities.balance_cents`; another user's debts aren't loaded.

**`test_debt_plan_api.py`:** response shape and values match `compare`; `422` for a negative or too-large extra; no debts → empty plans.

**`test_gateway.py`:** `debt_plan` scrubs names, matches `compare`, reports the difference and assumptions, writes one audit entry with its params; one strategy returns one plan.

**`test_tools.py`:** the tool's output equals the gateway's for the same arguments; negative/too-large extra and unknown strategy are `ToolException`s; blank strategy means both.

**Live done-when (`@pytest.mark.live`, `qwen2.5:14b`), in `test_chat_graph.py`:** seed the `debt-highest-apr` scenario's user, ask "How fast am I debt-free if I add $200 a month?", require a `plan_debt_payoff` call, and check the answer contains `payoff.compare`'s months and total interest (within $1) for the strategy it reports.

**Eval scenario `debt-plan-extra-200.json`:** the same user and question; `expect.facts` holds the months and interest computed by `payoff.compare` for the scenario's fixed date (filled in by running the planner when the scenario is written, never by hand); `expect.tools: ["plan_debt_payoff"]`.

**Frontend:** `DebtPlanCompare.test.tsx` (renders both plans, debounced refetch on input, stale response ignored, excluded and `never` messages); `api.test.ts` (`debtPlan` posts the body and parses the response).

## Docs

- `docs/architecture.md`: 6a.3 updated with the decisions above (spill-over, missing minimum, start month, the loader); the API table's `/debt-plan` line; decision-log rows.
- `README.md`: a short "Debt payoff" paragraph.
- `docs/tests/`: an entry for every new or changed test.

## Risks

| Risk | Mitigation |
|---|---|
| The model restates the plan with different numbers | The tool returns exact, labeled figures and the description says to quote them; the live test and the eval scenario check them by rule |
| Monthly interest differs from what the card statement shows | Stated in the output and the card's footnote; the planner is for comparing strategies, not predicting a statement |
| A debt missing its APR or minimum silently changes the answer | It's listed as excluded, in the API, the card and the chat payload |
| Eval scenario numbers drift if the planner changes | The scenario is generated from the planner at a fixed date; a planner change that moves them must regenerate it and say why in the decision log |
