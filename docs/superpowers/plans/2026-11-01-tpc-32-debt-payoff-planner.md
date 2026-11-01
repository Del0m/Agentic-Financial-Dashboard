# TPC-32 Debt-Payoff Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simulate paying off the user's debts month by month (avalanche vs snowball, with an optional extra), and expose it through `POST /debt-plan`, a dashboard card and a `plan_debt_payoff` chat tool.

**Architecture:** A pure engine module (`budget_engine/payoff.py`) does all the arithmetic in integer cents. One loader (`budget_service/debts.py`) turns DB rows into engine `Debt`s for both the API and the privacy gateway. The gateway's new `debt_plan` purpose runs the planner and sends only scrubbed, computed results to the model; the chat tool is a thin closure over it.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.1, LangChain `StructuredTool`, pytest; Next.js 16, React, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-11-01-tpc-32-debt-payoff-planner-design.md`

## Global Constraints

- Money is integer cents everywhere in the engine and API; dollars appear only in the gateway payload and the tool's `extra_monthly` argument. Rounding is `txns.round_cents` (half-up).
- Interest per month = balance × APR / 100 / 12, rounded to the cent each month. Minimums held at today's figure. Stop at 600 months.
- Avalanche ties by `finance.avalanche_key(apr, balance)`; snowball by smallest balance, then higher APR, then key.
- Excluded reasons, checked per debt in this order: `mortgage` (unless `include_mortgage`), `no_apr`, `no_minimum`; `Plan.excluded` lists them in input order. Balance ≤ 0 is left out silently. Only included debts' minimums count toward the monthly budget.
- The plan starts on the 1st of the month after `date.today()` (the gateway's `_today()` in the gateway; a module-level `_today()` in the API so tests can patch it).
- Extra bounds: 0 ≤ `extra_monthly_cents` ≤ 100_000_000 (API, `422` outside); 0 ≤ `extra_monthly` ≤ 1,000,000 dollars (tool, `ToolException` outside).
- Assumptions string, verbatim: `"Interest is charged monthly at APR/12; minimum payments are held at today's amounts; not financial advice."`
- Every test function gets an entry in `docs/tests/<module>.md` (what it does, why it's there) in the same task that adds it.
- Run backend tests from `backend/` with `../venv/bin/python -m pytest`; frontend from `frontend/` with `npm test`, `npm run lint`, `npm run build`.
- Commits: conventional prefix with the ticket, e.g. `feat(TPC-32): ...`. **No `Co-Authored-By` or "Generated with Claude Code" lines** (the user's standing rule).

## Review Focus

1. **A 0% APR debt** (promo card): `apr == 0.0` is a real rate, not missing. It must be included with zero interest, never excluded as `no_apr` (a falsy check would). Test in Task 1.
2. **A credit card with a credit balance** (negative `current_balance_cents`, an overpayment): left out silently, no excluded entry, no negative interest. Test in Task 1 (engine) and Task 2 (loader keeps it so the engine decides).
3. **An extra big enough to clear everything in month 1:** `months == 1`, every payoff in month 1, no leftover carried. Test in Task 1.
4. **Every debt excluded** (no APRs on file): the plans are empty with `months == 0`, and the excluded list carries every debt. The card shows only the excluded list, not "debt-free now". Tests in Task 1 and Task 6.
5. **Typing fast in the extra input:** an older response arriving after a newer one must not overwrite it. Test in Task 6.

---

### Task 1: Payoff engine

**Files:**
- Create: `backend/app/budget_engine/payoff.py`
- Test: `backend/tests/test_payoff.py`, `docs/tests/test_payoff.md`

**Interfaces:**
- Consumes: `app.budget_engine.txns.round_cents(Decimal) -> int`, `app.budget_engine.txns.add_months(date, int) -> date`, `app.finance.avalanche_key(apr, balance) -> tuple`.
- Produces (frozen dataclasses and functions, exactly as in the spec's "Budget engine" section):
  - `Debt(key: int, name: str, kind: Literal["credit","student","mortgage"], apr: Optional[float], balance_cents: int, min_payment_cents: Optional[int])`
  - `Payoff(key: int, name: str, month: int, date: date, interest_cents: int)`
  - `Plan(strategy, extra_monthly_cents: int, months: Optional[int], debt_free: Optional[date], total_interest_cents: int, order: tuple[Payoff, ...], never: tuple[int, ...], excluded: tuple[tuple[int, str], ...])`
  - `Strategy = Literal["avalanche", "snowball"]`
  - `simulate(debts: Iterable[Debt], extra_monthly_cents: int, strategy: Strategy, *, start: date, include_mortgage: bool = False) -> Plan`
  - `compare(debts, extra_monthly_cents, *, start, include_mortgage=False) -> dict[str, Plan]` with keys `"avalanche"`, `"snowball"`, `"baseline_avalanche"`, `"baseline_snowball"`.
  - `MAX_MONTHS = 600`

Helpers in the test file: `D(key, apr, balance_dollars, min_dollars, kind="credit", name=None)` builds a `Debt` in cents; `START = date(2026, 12, 1)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_hand_computed_avalanche():
    # A: $150 at 12%, min $100. B: $300 at 24%, min $50. Extra $100, so the budget is $250 a month
    plan = simulate([D(1, 12, 150, 100), D(2, 24, 300, 50)], 10000, "avalanche", start=START)
    assert (plan.months, plan.debt_free, plan.total_interest_cents) == (2, date(2027, 1, 1), 1114)
    assert [(p.key, p.month, p.interest_cents) for p in plan.order] == [(1, 2, 202), (2, 2, 912)]

def test_hand_computed_snowball():
    plan = simulate([D(1, 12, 150, 100), D(2, 24, 300, 50)], 10000, "snowball", start=START)
    assert (plan.months, plan.total_interest_cents) == (2, 1165)
    assert [(p.key, p.month, p.interest_cents) for p in plan.order] == [(1, 1, 150), (2, 2, 1015)]

def test_strategies_target_different_debts():
    # a big high-APR card and a small low-APR loan: avalanche clears the card first, snowball the loan
    debts = [D(1, 24.99, 3200, 95), D(2, 5.5, 900, 50)]
    assert simulate(debts, 20000, "avalanche", start=START).order[0].key == 1
    assert simulate(debts, 20000, "snowball", start=START).order[0].key == 2

def test_rollover_speeds_up_the_next_debt():
    debts = [D(1, 20, 500, 100), D(2, 10, 2000, 60)]
    alone = simulate([debts[1]], 0, "avalanche", start=START)
    both = simulate(debts, 0, "avalanche", start=START)
    assert next(p.month for p in both.order if p.key == 2) < alone.months

def test_spill_over_in_the_clearing_month():
    # month 1: the card ($40.80 after interest, $15.80 after its minimum) is cleared by the pool, and the pool's
    # other $184.20 reaches the loan that same month. with spill-over the loan clears in month 4; without, month 5
    plan = simulate([D(1, 24, 40, 25), D(2, 6, 1000, 50)], 20000, "avalanche", start=START)
    assert [(p.key, p.month) for p in plan.order] == [(1, 1), (2, 4)]
```

```python
def test_never_names_the_debt():
    # $10,000 at 30%: $250 of interest a month, a $100 minimum
    plan = simulate([D(1, 30, 10000, 100)], 0, "avalanche", start=START)
    assert (plan.months, plan.debt_free, plan.never) == (None, None, (1,))
    assert simulate([D(1, 30, 10000, 100)], 30000, "avalanche", start=START).months is not None

def test_excluded_with_reasons():
    debts = [D(1, None, 500, 25), D(2, 7, 900, None), D(3, 4, 200000, 1200, kind="mortgage"), D(4, 18, 800, 40)]
    plan = simulate(debts, 0, "avalanche", start=START)
    assert plan.excluded == ((1, "no_apr"), (2, "no_minimum"), (3, "mortgage"))
    assert [p.key for p in plan.order] == [4]
    with_mortgage = simulate(debts, 0, "avalanche", start=START, include_mortgage=True)
    assert (3, "mortgage") not in with_mortgage.excluded

def test_zero_apr_is_a_rate():                                   # review focus 1
    plan = simulate([D(1, 0.0, 300, 100)], 0, "avalanche", start=START)
    assert plan.excluded == () and (plan.months, plan.total_interest_cents) == (3, 0)

def test_credit_balance_left_out_silently():                     # review focus 2
    plan = simulate([D(1, 20, -50, 25), D(2, 20, 100, 25)], 0, "avalanche", start=START)
    assert plan.excluded == () and [p.key for p in plan.order] == [2]

def test_extra_clears_everything_in_month_one():                 # review focus 3
    plan = simulate([D(1, 20, 300, 25), D(2, 10, 200, 25)], 100000, "avalanche", start=START)
    assert plan.months == 1 and all(p.month == 1 for p in plan.order)

def test_no_debts_and_all_excluded():                            # review focus 4
    assert simulate([], 0, "avalanche", start=START).months == 0
    plan = simulate([D(1, None, 500, 25)], 0, "snowball", start=START)
    assert (plan.months, plan.order, plan.excluded) == (0, (), ((1, "no_apr"),))

def test_minimum_capped_at_balance():
    plan = simulate([D(1, 12, 30, 100)], 0, "avalanche", start=START)
    assert (plan.months, plan.total_interest_cents) == (1, 30)

def test_compare_baselines_and_extra_never_hurts():
    debts = [D(1, 24.99, 3200, 95), D(2, 5.5, 18400, 210)]
    plans = compare(debts, 20000, start=START)
    assert plans["baseline_avalanche"] == simulate(debts, 0, "avalanche", start=START)
    for s in ("avalanche", "snowball"):
        assert plans[s].months <= plans[f"baseline_{s}"].months
        assert plans[s].total_interest_cents <= plans[f"baseline_{s}"].total_interest_cents
```

(The two hand-computed cases, worked: avalanche month 1 interest A 150, B 600; minimums leave A $51.50, B $256; the $100 goes to B → $156. Month 2 interest A 52 (51.50 × 1% = 0.515, half-up), B 312; A's minimum clears it ($52.02); the pool $250 − $102.02 = $147.98 clears B ($109.12). Snowball month 1: after minimums A $51.50 is the smaller, gets $51.50 of the $100, B gets $48.50 → $207.50; month 2 B interest 415 → $211.65, minimum $50, pool $200 clears it.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ../venv/bin/python -m pytest tests/test_payoff.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.budget_engine.payoff'`

- [ ] **Step 3: Implement `payoff.py`**

The month loop, as the spec's "Each month" section fixes it (this is the algorithm the tests don't pin alone):

```
budget = extra + sum(min_payment of included debts)
for month in 1..MAX_MONTHS while any balance > 0:
    for each open debt: interest = round_cents(Decimal(balance) * Decimal(str(apr)) / 1200); balance += interest
    paid = 0; for each open debt: pay = min(minimum, balance); balance -= pay; paid += pay
    pool = budget - paid
    while pool > 0 and an open debt remains: target = first open debt by strategy order (balances after minimums)
        pay = min(pool, target.balance); target.balance -= pay; pool -= pay
    record a Payoff for each debt that reached 0 this month (in the order it reached 0)
never = keys still open after MAX_MONTHS
```

`interest_cents` per `Payoff` is that debt's accrued interest summed. `debt_free = add_months(start, months - 1)`; `Payoff.date` likewise. With nothing included, `months = 0`.

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && ../venv/bin/python -m pytest tests/test_payoff.py -v`
Expected: all PASS

- [ ] **Step 5: Document the tests** in `docs/tests/test_payoff.md` (shared setup: `D`, `START`; one entry per test; the hand-computed worked numbers in the two hand-computed entries).

- [ ] **Step 6: Commit**

```bash
git add backend/app/budget_engine/payoff.py backend/tests/test_payoff.py docs/tests/test_payoff.md
git commit -m "feat(TPC-32): payoff simulation, avalanche and snowball"
```

### Task 2: Debt loader

**Files:**
- Create: `backend/app/budget_service/debts.py`
- Test: `backend/tests/test_debts.py`, `docs/tests/test_debts.md`

**Interfaces:**
- Consumes: `payoff.Debt`; models `Liability`, `Account`.
- Produces: `load_debts(session: Session, user_id: str) -> list[Debt]`, ordered by account id. `key` = `Account.id`, `name` = `Account.name`, `balance_cents` = `Account.current_balance_cents or 0`, `apr`/`min_payment_cents`/`kind` from `Liability`.

- [ ] **Step 1: Write the failing tests** (use `tests.factories.add_user/add_account`; `add_account(..., liability={...})` sets `liabilities.balance_cents` to the same balance, so change the account balance afterwards)

```python
def test_balance_comes_from_the_account(db):
    # liability snapshot $3,000, account now $3,200
    ...
    assert [(d.name, d.balance_cents, d.apr, d.min_payment_cents) for d in load_debts(s, "u1")] == \
        [("Platinum Card", 320000, 24.99, 9500)]

def test_other_users_debts_not_loaded(db): ...   # u2's card exists; load_debts(s, "u1") == [] for a u1 with none

def test_credit_balance_kept_for_the_engine(db):  # review focus 2: a -$50 balance loads as -5000
```

- [ ] **Step 2: Run, verify FAIL** (`ModuleNotFoundError`): `../venv/bin/python -m pytest tests/test_debts.py -v`
- [ ] **Step 3: Implement `load_debts`** (one `select(Liability, Account).join(...)` filtered by `Liability.user_id`, ordered by `Account.id`).
- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: Document** in `docs/tests/test_debts.md`.
- [ ] **Step 6: Commit** `feat(TPC-32): load debts for the planner from accounts and liabilities`

### Task 3: `POST /debt-plan`

**Files:**
- Create: `backend/app/api/debt_plan.py`
- Modify: `backend/app/main.py` (add `app.include_router(debt_plan.router)` after `transactions.router`, and the import)
- Test: `backend/tests/test_debt_plan_api.py`, `docs/tests/test_debt_plan_api.md`

**Interfaces:**
- Consumes: `load_debts` (Task 2), `payoff.compare` (Task 1), `deps.get_current_user`, `budget_hooks` session pattern from `api/transactions.py`.
- Produces: the route and its Pydantic models, used by the frontend (Task 6):
  - `DebtPlanRequest(extra_monthly_cents: int = Field(ge=0, le=100_000_000), include_mortgage: bool = False)`
  - `PayoffOut(account_id, name, month, date, interest_cents)`, `PlanOut(strategy, extra_monthly_cents, months: Optional[int], debt_free: Optional[date], total_interest_cents, order: list[PayoffOut], never: list[int])`, `ExcludedOut(account_id, name, reason)`
  - `DebtPlanResponse(avalanche: PlanOut, snowball: PlanOut, baseline: BaselineOut{avalanche, snowball}, excluded: list[ExcludedOut], start: date)`
  - `_today() -> date` (module level, patched in tests); `start = add_months(month_start(_today()), 1)`.

- [ ] **Step 1: Write the failing tests** (the `client` fixture from `conftest.py`; seed the dev user's debts the way `test_dashboard.py` does; patch `app.api.debt_plan._today` to `date(2026, 11, 1)`)

```python
def test_plan_matches_compare(client, db):
    r = client.post("/debt-plan", json={"extra_monthly_cents": 20000})
    assert r.status_code == 200
    body = r.json()
    expected = compare(load_debts(...), 20000, start=date(2026, 12, 1))
    assert body["avalanche"]["months"] == expected["avalanche"].months
    assert body["avalanche"]["total_interest_cents"] == expected["avalanche"].total_interest_cents
    assert body["baseline"]["snowball"]["months"] == expected["baseline_snowball"].months
    assert body["start"] == "2026-12-01" and body["avalanche"]["order"][0]["name"] == "Platinum Card"

@pytest.mark.parametrize("extra", [-1, 100_000_001])
def test_extra_out_of_bounds_is_422(client, extra): ...

def test_no_debts_gives_empty_plans(client):
    body = client.post("/debt-plan", json={"extra_monthly_cents": 0}).json()
    assert (body["avalanche"]["months"], body["avalanche"]["order"], body["excluded"]) == (0, [], [])

def test_excluded_named(client, db): ...   # a card with no APR comes back as {"name": ..., "reason": "no_apr"}
```

- [ ] **Step 2: Run, verify FAIL** (404): `../venv/bin/python -m pytest tests/test_debt_plan_api.py -v`
- [ ] **Step 3: Implement the route** (`excluded` names come from the loaded debts by key).
- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: Document** in `docs/tests/test_debt_plan_api.md`.
- [ ] **Step 6: Commit** `feat(TPC-32): POST /debt-plan`

### Task 4: Gateway purpose `debt_plan`

**Files:**
- Modify: `backend/app/privacy/gateway.py` (`Purpose` gains `"debt_plan"`; `build_context` gains keyword args `extra_monthly_cents: Optional[int] = None, strategy: Optional[str] = None`; new `_debt_plan`)
- Test: `backend/tests/test_gateway.py`, `docs/tests/test_gateway.md`

**Interfaces:**
- Consumes: `load_debts`, `payoff.compare`, existing `scrub_account_name`, `_dollars`, `_today`.
- Produces: `build_context("debt_plan", extra_monthly_cents=int, strategy=None|"avalanche"|"snowball") -> str`, the JSON shape in the spec's "Gateway" section: `data.extra_monthly`, `data.start` (`YYYY-MM`), `data.plans[]` each with `strategy, months, debt_free (YYYY-MM|null), total_interest, order[{name, paid_off, interest}], without_extra{months, total_interest}, difference{months_sooner, interest_saved}`, then `data.never` (scrubbed names), `data.excluded[{name, reason}]`, `data.assumptions` (the verbatim string). Reason text: `no_apr` → `"no APR on file"`, `no_minimum` → `"no minimum payment on file"`, `mortgage` → `"mortgage (left out)"`. `difference` is `null` when either plan is `never`. Audit params: `extra_monthly_cents`, and `strategy` when given.

- [ ] **Step 1: Write the failing tests** (the file's `fixed_today` pins today to 2026-10-21, so `start` is `"2026-11"`)

```python
def test_debt_plan_matches_compare_and_scrubs(db, tmp_path): ...
    # a card named "Jane Doe Visa 4421": the payload has the scrubbed name only; months and interest equal compare's
def test_debt_plan_one_strategy(db, tmp_path):  # strategy="snowball" → exactly one plan, strategy "snowball"
def test_debt_plan_difference_and_assumptions(db, tmp_path):
    # difference.months_sooner == baseline months - plan months; interest_saved likewise, in dollars; assumptions verbatim
def test_debt_plan_audited_with_params(db, tmp_path):  # one audit line, purpose "debt_plan", params {"extra_monthly_cents": 20000}
def test_debt_plan_unknown_strategy_raises(db, tmp_path):  # ValueError
```

- [ ] **Step 2: Run, verify FAIL:** `../venv/bin/python -m pytest tests/test_gateway.py -k debt_plan -v`
- [ ] **Step 3: Implement `_debt_plan`.**
- [ ] **Step 4: Run the whole gateway file, verify PASS:** `../venv/bin/python -m pytest tests/test_gateway.py -v`
- [ ] **Step 5: Document** the new tests in `docs/tests/test_gateway.md`, and add `debt_plan` to its purpose list.
- [ ] **Step 6: Commit** `feat(TPC-32): gateway purpose debt_plan`

### Task 5: Chat tool, live done-when and eval scenario

**Files:**
- Modify: `backend/app/agents/tools.py` (a fifth tool in `make_tools`)
- Create: `eval/scenarios/debt-plan-extra-200.json`
- Test: `backend/tests/test_tools.py`, `docs/tests/test_tools.md`

**Interfaces:**
- Consumes: `gateway.build_context("debt_plan", ...)` (Task 4); existing `_blank`; `eval.fixtures.load_scenario/seed` for the live test.
- Produces: tool `plan_debt_payoff(extra_monthly: float = 0, strategy: Optional[str] = None) -> str`, with the description verbatim from the spec's "Chat tool" section. Dollars → cents with `round_cents(Decimal(str(extra_monthly)) * 100)`.

- [ ] **Step 1: Write the failing tests** (the file's `tools` and `seeded` fixtures)

```python
def test_plan_debt_payoff_matches_gateway(tools, seeded, tmp_path):
    out = tools["plan_debt_payoff"].invoke({"extra_monthly": 200})
    # same JSON as build_context("debt_plan", extra_monthly_cents=20000) on a fresh gateway with the same pseudonym
def test_plan_debt_payoff_blank_strategy_means_both(tools):     # strategy "" → two plans
def test_plan_debt_payoff_strategy_case_insensitive(tools):     # "Avalanche" → one avalanche plan
@pytest.mark.parametrize("extra", [-5, 1_000_001])
def test_plan_debt_payoff_bad_extra(tools, extra):              # ToolException naming extra_monthly
def test_plan_debt_payoff_unknown_strategy(tools):              # ToolException containing "'cheapest'"
def test_tool_names_include_planner(tools):                     # the five names, planner last
```

Live done-when, next to `test_live_model_picks_get_debts` and skipped the same way:

```python
@pytest.mark.skipif(not ollama_has(config.OLLAMA_MODEL), reason="needs ollama running with OLLAMA_MODEL pulled")
def test_live_debt_free_with_extra(db, tmp_path):
    # seed eval/scenarios/debt-highest-apr.json's user with eval.fixtures.seed(session, scenario, today=date.today())
    # ask "How fast am I debt-free if I add $200 a month?"
    # require an audit line with purpose "debt_plan"
    # expected = payoff.compare(load_debts(s, uid), 20000, start=...)
    # pass if the answer contains, for avalanche or for snowball, its months as a whole number and its total
    # interest within $1 (parse dollar amounts from the text)
```

- [ ] **Step 2: Run, verify FAIL:** `../venv/bin/python -m pytest tests/test_tools.py -k "plan_debt_payoff or planner" -v`
- [ ] **Step 3: Implement the tool** (validate, then `gateway.build_context("debt_plan", ...)`; strategy normalized with `_blank(...).lower()`).
- [ ] **Step 4: Run the whole file offline, verify PASS:** `../venv/bin/python -m pytest tests/test_tools.py -v -k "not live"`
- [ ] **Step 5: Run the live test** with `ollama serve` running: `../venv/bin/python -m pytest tests/test_tools.py::test_live_debt_free_with_extra -v`. Expected: PASS. If it fails, record the answer and stop to report; don't loosen the check.
- [ ] **Step 6: Write the eval scenario.** Copy `debt-highest-apr.json`'s `user`, set `id: "debt-plan-extra-200"`, `question: "How fast am I debt-free if I add $200 a month?"`, `expect.tools: ["plan_debt_payoff"]`, `expect.forbid: ["http", "nerdwallet"]`. Compute `expect.facts` by running `payoff.compare` on that user's debts with extra 20000 (months and interest don't depend on the start date): `{"number": <avalanche months>, "tol": 0}` and `{"number": <avalanche total interest in dollars>, "tol": 1}`. Write a `golden_answer` from those numbers. Check it loads, from the repo root: `PYTHONPATH=backend:. venv/bin/python -c "from pathlib import Path; from eval.fixtures import load_scenario; print(load_scenario(Path('eval/scenarios/debt-plan-extra-200.json')).id)"` prints `debt-plan-extra-200`.
- [ ] **Step 7: Document** the new tests in `docs/tests/test_tools.md` (and update its "four tools" intro to five).
- [ ] **Step 8: Commit** `feat(TPC-32): plan_debt_payoff chat tool, live check and eval scenario`

### Task 6: Frontend

**Files:**
- Modify: `frontend/lib/api.ts`, `frontend/lib/api.test.ts`, `frontend/app/dashboard/page.tsx` (render `<DebtPlanCompare />` right after `<DebtsTable debts={data.debts} />` when `data.debts.length > 0`)
- Create: `frontend/components/DebtPlanCompare.tsx`, `frontend/components/DebtPlanCompare.test.tsx`
- Docs: `docs/tests/DebtPlanCompare.test.md`, `docs/tests/api.test.md`

**Interfaces:**
- Consumes: `POST /api/debt-plan` (Task 3's response shape); `formatCents`, `formatDate`, `errorMessage`.
- Produces: `debtPlan(extraMonthlyCents: number, includeMortgage = false): Promise<DebtPlan>` and types `DebtPlan`, `Plan`, `PlanPayoff`, `PlanExcluded` mirroring Task 3's models. Component `DebtPlanCompare({ fetchPlan = debtPlan }: { fetchPlan?: typeof debtPlan })` (the prop is for tests).

Copy fixed by the spec: input label "Extra per month"; tag "saves the most interest" on the lower-interest strategy (none on a tie); difference line `+$<extra>/mo: <n> months sooner, <$x> less interest` (hidden at $0 extra); never line `<name>'s minimum payment doesn't cover its interest.`; footnote with the assumptions and "Not financial advice."; excluded reasons `no APR on file` / `no minimum payment on file` / `mortgage (left out)`.

- [ ] **Step 1: Write the failing tests**

```tsx
test("renders both plans and marks the cheaper one")            // avalanche 1114 vs snowball 1165 → tag on avalanche
test("refetches 300 ms after the input settles")                // vi.useFakeTimers; type 2, 20, 200 → one call with 20000
test("ignores a stale response")                                // review focus 5: first call resolves after second; shows second's months
test("shows the difference line only with an extra")
test("lists excluded debts and a never plan")
test("all debts excluded shows only the excluded list")         // review focus 4: no "debt-free" text
// api.test.ts
test("debtPlan posts the body and parses the response")         // fetch called with /api/debt-plan, {extra_monthly_cents, include_mortgage}
```

- [ ] **Step 2: Run, verify FAIL:** `cd frontend && npm test -- DebtPlanCompare api`
- [ ] **Step 3: Implement** `debtPlan` and the component (a request counter in a ref; only the latest request's response is applied).
- [ ] **Step 4: Run all checks, verify PASS:** `npm test && npm run lint && npm run build`
- [ ] **Step 5: Document** both test files in `docs/tests/`.
- [ ] **Step 6: Commit** `feat(TPC-32): DebtPlanCompare card on the dashboard`

### Task 7: Docs and full verification

**Files:**
- Modify: `docs/architecture.md` (6a.3: the fixed monthly budget with minimums first, spill-over, missing minimum excluded, start month, `load_debts`; the API table's `POST /debt-plan` line to the real request/response; decision-log rows dated 2026-11-01 for the spec's decisions that differ from or add to 6a.3), `README.md` (a short "Debt payoff" paragraph: the card, the chat question, the two simplifications)

- [ ] **Step 1: Edit the docs.**
- [ ] **Step 2: Full backend suite:** `cd backend && ../venv/bin/python -m pytest -q -k "not live and not sandbox"`. Expected: all pass (636 before this ticket plus the new tests).
- [ ] **Step 3: Frontend:** `cd frontend && npm test && npm run lint && npm run build`. Expected: all pass.
- [ ] **Step 4: Manual check:** run the API and the frontend, link Sandbox or seed a user with debts, open the dashboard, type 200 in "Extra per month", and confirm the card's avalanche numbers equal `POST /debt-plan`'s.
- [ ] **Step 5: Commit** `docs(TPC-32): architecture 6a.3, API table, decision log and readme`
