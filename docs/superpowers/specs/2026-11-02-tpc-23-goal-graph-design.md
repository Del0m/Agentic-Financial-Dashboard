# TPC-23: Goal graph that proposes goals within the monthly surplus

Jira: TPC-23 (epic TPC-5, M4). Branch: `TPC-23`, cut from `TPC-32` (it needs the debt-payoff planner).

## Goal

Work out in code what the user has left each month, then let the LLM propose 1-3 goals (debt payoff, emergency fund, savings) that fit within it and respect the user's preferences. Code computes every target and date. The LLM chooses which goals, their order, their monthly amounts, and explains why.

**Done when:** a synthetic user with a 24% APR card gets a debt-payoff goal first (`goals-card-24`, live on `qwen2.5:14b`, 3 runs recorded in the architecture doc). Every test is documented in `docs/tests/`.

**Out of scope:**
- rejecting unaffordable amounts and retrying with a reason (TPC-24, which adds a step between `propose` and `build`);
- `POST /goals/generate`, accepting or dismissing goals, a Goals page, goals becoming budget lines, and `interrupt()` for approval (TPC-25);
- the forecast's `monthly_surplus_cents` (TPC-31 replaces the surplus here);
- the daily run's `extra_payment` alert;
- running the graph after period close.

## Decisions

| Decision | Why |
|---|---|
| The surplus is measured: the median, over the last 3 complete months, of income − spending − transfers to savings | The user's choice. It's what they actually have left. The Free line also has to cover spending with no line, so using it would double-count, and income minus the budget total is only the plan |
| A partial first month isn't counted, the same as `fit` | Consistent with the budget's view of the same months |
| A negative month counts as negative, not $0 | A month in the red is real evidence against committing money |
| Surplus ≤ $0: no goals, no LLM call, and the run records why | Nothing to allocate, and a model shouldn't be asked to invent room |
| The LLM picks kinds, order, monthly amounts and strategy; code computes each target and date | The user's choice. It gives TPC-24's check something real to catch ("constrained generation"), and keeps the rule that the LLM never does arithmetic that matters |
| Preferences are structured fields plus a free-text note, in `prefs_json["goals"]` | The user's choice. Code can enforce `skip_kinds` and `max_share`; the note covers what the fields can't |
| Goals are only funded from the surplus and never cut a budget line | "Don't touch my gym budget" holds by construction. `protected_categories` is stored and shown to the model for when goals become lines (TPC-25) |
| A `debt_payoff` goal's monthly amount is an extra on top of the minimums | The budget's lines already pay the minimums, so counting them would double-count against the surplus |
| High-interest rule in the prompt: any debt at or above 8% APR means `debt_payoff` comes first | The done-when, and the CFPB guidance in the knowledge base. The model still makes the call; the live test measures whether it follows the rule |
| `emergency_fund` target: 3 × the median month's `needs` spending, minus savings account balances. Not offered when that's ≤ $0 | Code knows both numbers; a common rule of thumb on the low end for a first goal |
| `savings` target: 12 × the monthly amount, 12 months out | There's no named purchase to save for, and TPC-24 requires every goal to be dated |
| A new run marks the user's earlier `proposed` goals `superseded`, in one commit with the new ones | The user only ever sees one current proposal, and the history stays |
| No checkpointer and no `interrupt()` in TPC-23 | Nothing pauses until TPC-25 adds approval |

## Files

**New:**
- `backend/app/budget_engine/goals.py`: surplus, emergency-fund gap, building goals from a proposal. Pure.
- `backend/app/budget_service/goal_prefs.py`: read, validate and set `prefs_json["goals"]`.
- `backend/app/agents/goal_llm.py`: the prompt and the Pydantic models.
- `backend/app/agents/goal_graph.py`: the graph, `run_goals`, and the CLI.
- `backend/app/api/goals.py`: `GET /goals`.
- `backend/alembic/versions/<rev>_goals.py`: the table.
- Tests: `backend/tests/test_goals.py`, `test_goal_prefs.py`, `test_goal_graph.py`, `test_goals_api.py`, with docs in `docs/tests/`.

**Changed:**
- `backend/app/db/models.py`: the `Goal` model.
- `backend/app/privacy/gateway.py`: the `goals` purpose.
- `backend/app/main.py`: register the router.
- `eval/budget_users.py`: `Profile` gets `accounts`; the `goals-card-24` profile.
- Tests: `test_gateway.py`, `test_migrations.py`, `test_budget_users.py`.
- Docs: `docs/architecture.md`, `README.md`.

## Budget engine (`budget_engine/goals.py`)

Pure functions in integer cents.

```python
GoalKind = Literal["debt_payoff", "emergency_fund", "savings"]
EMERGENCY_MONTHS = 3
SAVINGS_MONTHS = 12

def history_months(txns, today) -> list[date]
    # complete months only, at most fit.HISTORY_MONTHS, none before the history starts (fit's FIRST_MONTH_DAY rule)
def monthly_surplus(txns, today) -> Optional[int]
    # median over history_months of income − spending − savings; None with no complete month
def emergency_gap(txns, today, needs: frozenset[str], savings_balance_cents: int) -> int
    # EMERGENCY_MONTHS × the median month's spending in `needs` categories − savings_balance_cents, floored at 0

@dataclass(frozen=True)
class GoalPlan:
    kind: GoalKind
    priority: int
    monthly_cents: int
    target_cents: int
    target_date: Optional[date]    # None only for a debt plan the simulation says never ends
    strategy: Optional[str]
    rationale: str

def build_goal(kind, priority, monthly_cents, strategy, rationale, *, debts, gap_cents, start) -> GoalPlan
```

- `monthly_surplus` reuses `spending`, `savings`, `income_by_month` and `category_totals`. Totals per category are floored at $0 as in the budget, so a refund nets its category.
- `start` is the 1st of the month after `today`.

**`build_goal` per kind:**
- `debt_payoff`: `target_cents` is the total balance of the debts the planner includes (no mortgages). `target_date` is `payoff.simulate(debts, monthly_cents, strategy, start=start).debt_free`.
- `emergency_fund`: `target_cents = gap_cents`. `target_date` is `start` plus `ceil(gap / monthly) − 1` months, the month the last payment lands.
- `savings`: `target_cents = monthly × 12`, `target_date` is `start` plus 11 months.

**`offered_kinds(debts, gap_cents, skip_kinds) -> list[GoalKind]`:**
- `debt_payoff` when the planner would include at least one debt (`simulate(...).order` or `.never` is non-empty);
- `emergency_fund` when the gap is above $0;
- `savings` always;
- then minus `skip_kinds`.

## Preferences (`budget_service/goal_prefs.py`)

`prefs_json["goals"]`:

```json
{"skip_kinds": ["savings"], "max_share": 0.8, "protected_categories": ["PERSONAL_CARE"], "note": "emergency fund matters most"}
```

```python
@dataclass(frozen=True)
class GoalPrefs:
    skip_kinds: tuple[str, ...] = ()
    max_share: float = 0.8
    protected_categories: tuple[str, ...] = ()
    note: Optional[str] = None

def user_goal_prefs(session, user_id) -> GoalPrefs
def set_goal_prefs(session, user_id, raw: dict) -> GoalPrefs
```

`set_goal_prefs` raises `ValueError` for an unknown kind, a `max_share` outside (0, 1], a non-category in `protected_categories`, or a note over 500 characters. It stores a new dict so the JSON column sees the change, like `set_user_style`.

## Gateway (`goals` purpose)

`build_context("goals", goal_facts=GoalFacts(...))`, where `GoalFacts` holds the numbers the graph computed. The gateway only reads debts itself (`load_debts`) to scrub their names. Shape:

```json
{"purpose": "goals", "data": {
  "monthly_surplus": 900.00, "cap": 720.00, "offered_kinds": ["debt_payoff", "emergency_fund", "savings"],
  "debts": [{"name": "Platinum Card", "kind": "credit", "apr": 24.99, "balance": 3200.00, "min_payment": 95.00}],
  "emergency_fund_gap": 8200.00,
  "debt_plan_options": [{"extra_monthly": 180.00, "avalanche": {"months": 52, "total_interest": 5012.11},
                         "snowball": {"months": 52, "total_interest": 5012.11}}],
  "protected_categories": ["PERSONAL_CARE"], "note": "emergency fund matters most",
  "assumptions": "Debt plans charge interest monthly at APR/12 and hold minimum payments at today's amounts."
}}
```

(The numbers are illustrative.)

- Debts are sorted highest APR first, the planner's includable debts only, names through `scrub_account_name`.
- `debt_plan_options` are `payoff.compare` at 25%, 50% and 75% of the cap, rounded to the dollar. Only present when `debt_payoff` is offered.
- `note` goes through `scrub_text`.
- One audit entry, with params `{"cap_cents": ...}`.

## Model (`agents/goal_llm.py`)

```python
class ProposedGoal(BaseModel):
    kind: Literal["debt_payoff", "emergency_fund", "savings"]
    monthly_dollars: float = Field(gt=0)     # code converts to cents (changed after the live runs: see the risk below)
    strategy: Optional[Literal["avalanche", "snowball"]] = None
    rationale: Text               # the budget_llm whitespace-rejecting string

class GoalProposal(BaseModel):
    goals: list[ProposedGoal] = Field(min_length=1, max_length=3)    # in priority order
```

The prompt is built like `budget_llm`'s: checked rules first, facts as JSON. It says:
- propose 1-3 goals, most important first, using only `offered_kinds`, at most one of each kind;
- if any debt has an APR of 8% or more, `debt_payoff` comes first, with avalanche unless the note asks otherwise;
- the monthly amounts together must not exceed `cap`, and should use most of it;
- respect the note and don't propose anything that touches a protected category;
- `rationale` is one or two plain sentences to the user, using only numbers in the facts, with no financial advice beyond the plan.

## Graph (`agents/goal_graph.py`)

`load_facts → propose → build → persist`, a plain LangGraph `StateGraph` with no checkpointer. Conditional edges end the run early.

- **`load_facts`:**
  - loads transactions for the history window with `load_txns`, then computes the surplus, the debts (`load_debts`), the savings balance, the gap and the prefs;
  - `needs` is the categories in `needs` lines of the user's open period when there is one, otherwise `groups.NEEDS_DEFAULT`;
  - the savings balance is the sum of `current_balance_cents` on `depository` accounts with subtype `savings`;
  - a surplus of `None` or ≤ 0 ends the run with status `no_surplus`, without calling the model;
  - no offered kinds ends it with status `nothing_offered`.
- **`propose`:** `invoke_structured(model, GoalProposal, messages)`. `None` (bad JSON twice) ends the run with status `error`, reason `bad_model_output`. A connection error propagates, as in the budget graph, and the run row records it as `error`.
- **`build`:** keeps each proposed goal in order, dropping one whose kind wasn't offered or is a repeat. A `debt_payoff` with no strategy means avalanche, the prompt's default. Dropped goals are listed in the run summary. Nothing left ends the run with status `error`, reason `no_usable_goals`. Priorities are renumbered 1..n.
- **`persist`:** one commit: earlier `proposed` goals for the user become `superseded`, and the new ones are written as `proposed` with the run id.

`run_goals(session_factory, user_id, today, *, model) -> dict` opens an `agent_runs` row (`graph="goals"`) and returns the state. The summary holds `surplus_cents`, `cap_cents`, `goal_ids` and `dropped`, plus `reason` on an early stop.

It takes the per-user lock from `budget_service.daily.user_lock`, so it can't overlap a budget run.

**CLI:** `python -m app.agents.goal_graph --user <id> [--date YYYY-MM-DD] [--model NAME] [--prefs '<json>']`.
- `--prefs` calls `set_goal_prefs`; a `ValueError` prints the message and exits 1.
- It prints the surplus and the cap, then each goal: kind, monthly amount, target, date, strategy and rationale. A goal whose monthly amount pushes the total over the cap gets a `(over cap)` note, since TPC-24 isn't there yet.
- It exits 1 when the run's status is `error`.

## Data model

```
goals  id, user_id (fk, indexed), run_id (fk agent_runs, nullable), kind, priority, monthly_cents,
       target_cents, target_date (nullable), strategy (nullable), rationale,
       status (proposed | superseded | accepted | dismissed), created_at
```

`kind` and `status` are plain strings with `Literal` aliases, like alert kinds (decision log, 2026-10-28). The migration adds the table and drops it on downgrade.

## API

```
GET /goals -> [{ id, kind, priority, monthly_cents, target_cents, target_date, strategy, rationale, status }]
```

Only the current user's `proposed` goals, by priority. `[]` when there are none.

## Synthetic user

`eval/budget_users.py`:
- `Profile` gets `accounts: tuple = ()`, rows of `(name, type, subtype, balance_cents, liability_or_none)`. `seed_profile` adds them with `add_account`. Existing profiles are unchanged.
- A new `goals-card-24` profile, kept out of the budget accuracy eval's `PROFILES`:
  - income of $4,200 a month, $500 to savings, rent and everyday spending leaving a positive surplus;
  - a $3,200 card at 24.99% ($95 minimum) and an $18,400 student loan at 5.5% ($210 minimum), the same debts as the `debt-highest-apr` chat scenario;
  - a $1,000 savings account.

## Tests

**`test_goals.py` (pure):**
- `monthly_surplus`: the median of three known months; a refund nets its category; savings transfers subtract; a partial first month is skipped; a negative month stays negative; no complete month gives `None`.
- `emergency_gap`: 3 × the median needs month minus savings; floored at 0.
- `build_goal`: `debt_payoff` matches `payoff.simulate` directly, and mortgages stay out of the target; `emergency_fund` rounds up (a $1,000 gap at $300 is the 4th month); `savings` is 12 months; a never plan has no date.
- `offered_kinds`: no debts, or all debts excluded, means no `debt_payoff`; a $0 gap means no `emergency_fund`; `skip_kinds` removes a kind.

**`test_goal_prefs.py`:** defaults; round trip; each invalid value raises; other `prefs_json` keys survive.

**`test_gateway.py`:** `goals` scrubs debt names and the note; the cap and the options match `compare`; no options without `debt_payoff`; audited once.

**`test_goal_graph.py`** (scripted fake model):
- no surplus: status `no_surplus`, model never called;
- a valid proposal: priorities, targets and dates from code, `proposed` status, run summary;
- a second run supersedes the first;
- a kind not offered and a repeat are dropped and listed; `debt_payoff` without a strategy means avalanche;
- nothing usable: `error`, nothing written;
- bad JSON twice: `error`, nothing written;
- the CLI prints the goals, marks `(over cap)`, exits 1 on error, and refuses bad `--prefs`;
- live done-when (skipped without Ollama): seed `goals-card-24`, run the graph; the first goal is `debt_payoff`. The total's relation to the cap is printed, not asserted (TPC-24 enforces it).

**`test_budget_users.py`:** `goals-card-24` seeds its debts and savings account; `PROFILES` doesn't include it, so the accuracy eval is unchanged.

**`test_goals_api.py`:** own proposed goals by priority; another user's and superseded goals are hidden; empty list.

**`test_migrations.py`:** the upgrade test also checks the `goals` table, and the downgrade removes it.

## Docs

- `docs/architecture.md`: the Goal Agent row in section 4, the `goals` data model, a new goal-graph section (surplus, the split between code and the LLM, the live result), 6a.3's Goal Agent bullet, the API table, decision-log rows.
- `README.md`: a short "Goals" paragraph with the CLI.
- `docs/tests/`: an entry for every test.

## Risks

| Risk | Mitigation |
|---|---|
| The model exceeds the cap | Expected until TPC-24. The CLI marks it; nothing acts on goals until TPC-25 |
| The model gets amounts wrong (dollars vs cents) | Settled by the live runs: asked for cents, it copied the prompt's example and left most of the cap unused, so the field is in dollars and code converts |
| Irregular income makes the median misleading | The same limit as the budget's income. TPC-31's forecast replaces this figure |
| The surplus doesn't count a new bill that started this month | History only looks at complete months; accepted as a known limit until the forecast |
