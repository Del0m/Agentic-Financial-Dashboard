# TPC-23 Goal Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A goal graph that measures the user's monthly surplus in code, asks the LLM for 1-3 goals within it, computes every target and date in code, and stores them as `proposed`.

**Architecture:** Pure arithmetic in `budget_engine/goals.py`. A LangGraph graph `load_facts → propose → build → persist` in `agents/goal_graph.py`, with the LLM's contract in `agents/goal_llm.py` and facts leaving through a new gateway purpose `goals`. Stored in a new `goals` table, read by `GET /goals`, and run from a CLI.

**Tech Stack:** Python 3.12, SQLAlchemy 2.1 + Alembic, LangGraph, Pydantic, FastAPI, pytest, Ollama (`qwen2.5:14b`).

**Spec:** `docs/superpowers/specs/2026-11-02-tpc-23-goal-graph-design.md`

## Global Constraints

- Money is integer cents in the engine, the models and the API. Dollars appear only in the gateway payload. Rounding is `txns.round_cents` (half-up).
- `EMERGENCY_MONTHS = 3`, `SAVINGS_MONTHS = 12`, default `max_share = 0.8`, note limit 500 characters.
- History is `fit.HISTORY_MONTHS` complete months, with `fit.FIRST_MONTH_DAY`'s rule for a partial first month.
- `start` (the first planned month) is the 1st of the month after `today`.
- The model's output goes through `budget_llm.invoke_structured` (Ollama `format` + Pydantic + one retry on bad JSON).
- All user data reaches the model through `PrivacyGateway.build_context("goals", ...)`, never directly.
- Goal runs hold `budget_service.daily.user_lock(user_id)`, non-blocking.
- `AgentRun.graph` is `"goals"`. `Goal.status` is `proposed | superseded | accepted | dismissed`; only `proposed` and `superseded` are set here.
- Every test function gets an entry in `docs/tests/<module>.md` in the task that adds it.
- Backend tests run from `backend/` with `../venv/bin/python -m pytest`. Docker isn't needed. If the containers are up they serve `dev.db` too, so never run the host servers alongside them.
- Commits: `feat(TPC-23): ...`, with **no `Co-Authored-By` or "Generated with Claude Code" lines** (the user's rule).

## Review Focus

1. **A user with no budget yet** (linked, never ran a budget): the graph works, `needs` falls back to `groups.NEEDS_DEFAULT`. Test in Task 6.
2. **A simulated user with no `--date`:** "today" is their simulated date (`daily.sim_today`), not the real one, or their history looks empty. Test in Task 6.
3. **A debt plan that never ends at the extra the model chose:** the goal is stored with no date, and the CLI prints "no end date" rather than crashing on `None`. Tests in Tasks 1 and 6.
4. **A run while a budget run holds the user's lock:** status `busy`, nothing written, CLI exits 1 with a message. Test in Task 6.
5. **A savings account with a `None` balance:** counts as $0 in the savings balance. Test in Task 6.

---

### Task 1: Goal arithmetic

**Files:**
- Create: `backend/app/budget_engine/goals.py`
- Test: `backend/tests/test_goals.py`, `docs/tests/test_goals.md`

**Interfaces:**
- Consumes: `txns.spending/savings/income_by_month/category_totals/add_months/month_start/month_end/round_cents`, `fit.HISTORY_MONTHS/FIRST_MONTH_DAY`, `payoff.Debt/simulate`.
- Produces:
  - `GoalKind = Literal["debt_payoff", "emergency_fund", "savings"]`, `GOAL_KINDS` (that tuple), `EMERGENCY_MONTHS`, `SAVINGS_MONTHS`
  - `history_months(txns, today) -> list[date]`
  - `monthly_surplus(txns, today) -> Optional[int]`
  - `emergency_gap(txns, today, needs: frozenset[str], savings_balance_cents: int) -> int`
  - `offered_kinds(debts, gap_cents, skip_kinds, *, start) -> list[str]`, in `GOAL_KINDS` order
  - frozen dataclass `GoalPlan(kind, priority, monthly_cents, target_cents, target_date: Optional[date], strategy: Optional[str], rationale)`
  - `build_goal(kind, priority, monthly_cents, strategy, rationale, *, debts, gap_cents, start) -> GoalPlan`
  - frozen dataclass `GoalFacts(surplus_cents: int, cap_cents: int, offered: tuple[str, ...], gap_cents: int, start: date, protected_categories: tuple[str, ...], note: Optional[str])`, the gateway's input (Task 4)

Test helpers: `T(y, m, d, cents, cat)` builds a `Txn`; `TODAY = date(2026, 11, 2)`, `START = date(2026, 12, 1)`; `months(spend=(300000, 330000, 280000))` gives Aug-Oct with $4,200 of `INCOME` (negative cents) on the 1st, that spending in `FOOD_AND_DRINK`, and $500 of `SAVINGS` per month.

- [ ] **Step 1: Write the failing tests**

```python
def test_surplus_is_the_median_month():            # 70000, 40000, 90000 left → 70000
    assert monthly_surplus(months(), TODAY) == 70000

def test_refund_nets_its_category():               # a -$100 food refund in Aug: 80000, 40000, 90000 → 80000
def test_savings_transfers_subtract():             # no SAVINGS rows: every month $500 more → 120000
def test_partial_first_month_skipped():            # first txn Aug 15: only Sep and Oct count → median(40000, 90000) = 65000
def test_negative_month_stays_negative():          # spend 500000 in all three → -130000
def test_no_complete_month_is_none():              # only txns in the current month → None

def test_gap_is_three_needs_months_minus_savings():
    # needs months $1,200 / $1,500 / $1,300 → median 1,300 × 3 = 3,900, minus $1,000 saved
    assert emergency_gap(txns, TODAY, frozenset({"RENT_AND_UTILITIES"}), 100000) == 290000
def test_gap_floored_at_zero():                    # savings above 3 months → 0

def test_debt_goal_matches_the_planner():
    g = build_goal("debt_payoff", 1, 20000, "avalanche", "why", debts=DEBTS, gap_cents=0, start=START)
    want = simulate(DEBTS, 20000, "avalanche", start=START)
    assert (g.target_date, g.target_cents, g.strategy) == (want.debt_free, 2160000, "avalanche")
def test_debt_target_leaves_out_mortgages():       # a mortgage in DEBTS isn't in target_cents
def test_never_debt_goal_has_no_date():            # $10,000 at 30%, $100 min, $1 extra: target_date None
def test_emergency_date_rounds_up():               # gap $1,000 at $300: Mar 1 2027 (the 4th month), target 100000
def test_savings_is_a_year():                      # $100: target 120000 (12 × monthly), date Nov 1 2027

def test_offered_kinds():
    # debts + gap → all three; no debts → no debt_payoff; only an APR-less debt → no debt_payoff;
    # gap 0 → no emergency_fund; skip_kinds=("savings",) → no savings
```

- [ ] **Step 2: Run, verify FAIL** (`ModuleNotFoundError`): `../venv/bin/python -m pytest tests/test_goals.py -q`
- [ ] **Step 3: Implement `goals.py`.** `history_months` uses the same rule as `fit_budget` (`start = month_start(earliest)` if `earliest.day <= FIRST_MONTH_DAY`, else the next month; at most `HISTORY_MONTHS` back; up to but not including the current month). A month's surplus is `income_by_month` minus `sum(category_totals(spending(txns), m, month_end(m)).values())` minus the same for `savings(txns)`. Medians use `statistics.median` through `round_cents(Decimal(str(...)))`. Debt targets include a debt when `payoff.simulate` wouldn't exclude it and its balance is above 0: use `simulate(...)`'s `excluded` and each debt's `balance_cents`.
- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: Document** in `docs/tests/test_goals.md`.
- [ ] **Step 6: Commit** `feat(TPC-23): measure the surplus and compute goal targets and dates`

### Task 2: Preferences

**Files:**
- Create: `backend/app/budget_service/goal_prefs.py`
- Test: `backend/tests/test_goal_prefs.py`, `docs/tests/test_goal_prefs.md`

**Interfaces:**
- Consumes: `goals.GOAL_KINDS`, `agents.tools.VALID_CATEGORIES` if importing it causes no cycle; otherwise define the check from `PFC_CATEGORIES` the way the tool does.
- Produces: frozen dataclass `GoalPrefs(skip_kinds: tuple[str, ...] = (), max_share: float = 0.8, protected_categories: tuple[str, ...] = (), note: Optional[str] = None)`; `user_goal_prefs(session, user_id) -> GoalPrefs`; `set_goal_prefs(session, user_id, raw: dict) -> GoalPrefs` (doesn't commit, like `set_user_style`).

- [ ] **Step 1: Write the failing tests**

```python
def test_defaults_with_nothing_stored(db): ...     # GoalPrefs() for a user with empty prefs_json
def test_round_trip(db): ...                       # the spec's JSON example comes back equal
def test_other_prefs_survive(db): ...              # prefs_json["budget"] and ["sim"] are untouched
@pytest.mark.parametrize("raw", [{"skip_kinds": ["yacht"]}, {"max_share": 0}, {"max_share": 1.5},
                                 {"protected_categories": ["NOPE"]}, {"note": "x" * 501}, {"max_shar": 0.5}])
def test_invalid_raises(db, raw): ...              # ValueError, and nothing stored
```

- [ ] **Step 2: Run, verify FAIL.** `../venv/bin/python -m pytest tests/test_goal_prefs.py -q`
- [ ] **Step 3: Implement** (a new dict on `user.prefs_json`, so the JSON column sees the change; unknown keys raise).
- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: Document** in `docs/tests/test_goal_prefs.md`.
- [ ] **Step 6: Commit** `feat(TPC-23): goal preferences in prefs_json`

### Task 3: `goals` table and `GET /goals`

**Files:**
- Modify: `backend/app/db/models.py` (`Goal`), `backend/app/main.py` (router)
- Create: `backend/alembic/versions/<rev>_goals.py` (`down_revision = '5b7c2e9d4a10'`), `backend/app/api/goals.py`
- Test: `backend/tests/test_goals_api.py`, `backend/tests/test_migrations.py`, with docs

**Interfaces:**
- Produces: model `Goal(id, user_id, run_id: Optional[int], kind: str, priority: int, monthly_cents: int, target_cents: int, target_date: Optional[Date], strategy: Optional[str], rationale: str, status: str = "proposed", created_at)`; route `GET /goals`, Pydantic `GoalOut` with the spec's fields.

- [ ] **Step 1: Write the failing tests**

```python
def test_own_proposed_goals_by_priority(client, db): ...     # priorities 2, 1 stored → returned 1, 2, with every field
def test_other_users_and_superseded_hidden(client, db): ...
def test_no_goals_is_an_empty_list(client, db): ...
# test_migrations.py, in test_upgrade_backfills_and_downgrades:
#   assert "goals" in tables after upgrade; "goals" not in tables after the downgrade to BEFORE
```

- [ ] **Step 2: Run, verify FAIL.** `../venv/bin/python -m pytest tests/test_goals_api.py tests/test_migrations.py -q`
- [ ] **Step 3: Implement** the model, the migration (`create_table` with an index on `user_id`, `drop_table` on downgrade) and the route. Check with `../venv/bin/alembic heads` that there's one head.
- [ ] **Step 4: Run, verify PASS.**
- [ ] **Step 5: Document** in `docs/tests/test_goals_api.md` and `docs/tests/test_migrations.md`.
- [ ] **Step 6: Commit** `feat(TPC-23): goals table and GET /goals`

### Task 4: Gateway purpose `goals`

**Files:**
- Modify: `backend/app/privacy/gateway.py` (`Purpose`; `build_context` keyword `goal_facts: Optional[GoalFacts] = None`; `_goals`)
- Test: `backend/tests/test_gateway.py`, `docs/tests/test_gateway.md`

**Interfaces:**
- Consumes: `goals.GoalFacts`, `load_debts`, `payoff.compare`, `scrub_account_name`, `scrub_text`, `_dollars`.
- Produces: `build_context("goals", goal_facts=facts) -> str`, the spec's "Gateway" shape. Keys: `monthly_surplus`, `cap`, `offered_kinds`, `debts[{name, kind, apr, balance, min_payment}]` (includable debts, highest APR first), `emergency_fund_gap`, `debt_plan_options[{extra_monthly, avalanche{months, total_interest}, snowball{...}}]` (extras are 25%, 50%, 75% of the cap, rounded to the dollar; omitted unless `debt_payoff` is offered; a `never` plan's `total_interest` is `null`, as in `debt_plan`), `protected_categories`, `note`, `assumptions`. The assumptions string, verbatim: `"Debt plans charge interest monthly at APR/12 and hold minimum payments at today's amounts."` Audit params: `{"cap_cents": facts.cap_cents}`.

- [ ] **Step 1: Write the failing tests** (the file's `fixed_today`, `seed_debts`, `audit_lines`)

```python
def test_goals_scrubs_debts_and_note(db, tmp_path): ...     # "Jane" appears nowhere, with note "Jane Doe's card first"
def test_goals_cap_and_options_match_compare(db, tmp_path): ...  # cap dollars; 3 options; each matches compare at that extra
def test_goals_no_options_without_debt_offered(db, tmp_path): ...  # offered without debt_payoff → no key, no debts
def test_goals_includable_debts_only(db, tmp_path): ...     # the APR-less store card isn't listed
def test_goals_audited_once(db, tmp_path): ...              # one line, purpose "goals", params {"cap_cents": ...}
```

- [ ] **Step 2: Run, verify FAIL:** `../venv/bin/python -m pytest tests/test_gateway.py -k goals -q`
- [ ] **Step 3: Implement `_goals`** next to `_debt_plan`.
- [ ] **Step 4: Run the whole file, verify PASS.**
- [ ] **Step 5: Document** the tests, and add `goals` to the doc's purpose list.
- [ ] **Step 6: Commit** `feat(TPC-23): gateway purpose goals`

### Task 5: The `goals-card-24` synthetic user

**Files:**
- Modify: `eval/budget_users.py`
- Test: `backend/tests/test_budget_users.py`, `docs/tests/test_budget_users.md`

**Interfaces:**
- Produces: `Profile.accounts: tuple = ()`, rows `(name, type, subtype, balance_cents, liability: Optional[dict])`, added by `seed_profile` with `add_account`; a module constant `GOAL_USERS = (Profile("goals-card-24", ...),)`, not in `PROFILES` or `PLANTED`. `today = date(2026, 11, 2)`, `start = date(2026, 8, 1)`, `end = date(2026, 10, 31)`.

Profile, from the spec: `Monthly("INCOME", "Employer", -420000, day=15)`, `Monthly("TRANSFER_OUT", "Savings", 50000, day=16)`, `RENT`, `GROCER`, `*BASE`; accounts: `("Platinum Card", "credit", "credit card", 320000, {"kind": "credit", "apr": 24.99, "min_payment_cents": 9500})`, `("Student Loan", "loan", "student", 1840000, {"kind": "student", "apr": 5.5, "min_payment_cents": 21000})`, `("Savings", "depository", "savings", 100000, None)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_goal_user_seeds_debts_and_savings(db): ...    # load_debts has the two debts at their balances; the savings account exists
def test_goal_user_has_a_surplus(db): ...              # monthly_surplus(load_txns(...), profile.today) > 0, printed in the failure message
def test_goal_user_not_in_the_accuracy_eval(): ...     # its id isn't in PROFILES or PLANTED
```

- [ ] **Step 2: Run, verify FAIL.** `../venv/bin/python -m pytest tests/test_budget_users.py -q`
- [ ] **Step 3: Implement.** Adjust the spending patterns if the surplus isn't clearly positive; it should leave several hundred dollars.
- [ ] **Step 4: Run the file and `tests/test_budget_accuracy.py`, verify PASS** (existing profiles unchanged).
- [ ] **Step 5: Document** in `docs/tests/test_budget_users.md`.
- [ ] **Step 6: Commit** `feat(TPC-23): goals-card-24 synthetic user`

### Task 6: The graph and its CLI

**Files:**
- Create: `backend/app/agents/goal_llm.py`, `backend/app/agents/goal_graph.py`
- Test: `backend/tests/test_goal_graph.py`, `docs/tests/test_goal_graph.md`

**Interfaces:**
- Consumes: everything above; `budget_llm.invoke_structured` and its `Text`; `initialize.load_txns`; `daily.user_lock/sim_today`; `llm.get_chat_model`.
- Produces:
  - `goal_llm`: `ProposedGoal`, `GoalProposal` (spec's "Model"), `GOAL_PROMPT`, `goal_messages(context: str) -> list`
  - `goal_graph`: `build_goal_graph(session_factory, *, model, gateway)`, `run_goals(session_factory, user_id, today, *, model=None, audit_path=None) -> dict` returning `{"status", "run_id", "goal_ids", "surplus_cents", "cap_cents", "dropped", "reason"}`; `main(argv=None, session_factory=None, model=None) -> int`
  - statuses: `ok`, `no_surplus`, `nothing_offered`, `error`, `busy`. `AgentRun.status` is `ok` for `ok`, `no_surplus` and `nothing_offered`, and `error` for `error`; `busy` writes no row.

Tests use `ScriptedChatModel(replies=[AIMessage(json)])` from `tests/fakes.py`, the `goals-card-24` user, and a `proposal(*goals)` JSON helper.

- [ ] **Step 1: Write the failing tests**

```python
def test_valid_proposal_stored_from_code(db, tmp_path):
    # debt_payoff $200 avalanche + savings $100: two proposed goals, priorities 1, 2;
    # target_date equals payoff.simulate's and the savings 12-month date; run ok; summary has surplus, cap, goal_ids
def test_second_run_supersedes_the_first(db, tmp_path): ...
def test_no_surplus_never_calls_the_model(db, tmp_path): ...  # a user spending more than earning: no_surplus, model.seen == []
def test_nothing_offered(db, tmp_path): ...                   # prefs skip every kind offered: nothing_offered, no model call
def test_unusable_goals_dropped_and_listed(db, tmp_path): ... # a skipped kind, a repeat, debt_payoff with no strategy: dropped, the rest stored
def test_nothing_usable_is_an_error(db, tmp_path): ...        # status error, reason no_usable_goals, nothing written, earlier proposals untouched
def test_bad_json_twice_is_an_error(db, tmp_path): ...        # reason bad_model_output, nothing written
def test_model_outage_recorded(db, tmp_path): ...             # a model raising ConnectionError: run_goals returns status error, reason "ConnectionError"; the run row is error; nothing written
def test_prompt_gets_the_gateway_context(db, tmp_path): ...   # the human message holds the scrubbed context; a goals audit line exists

# review focus
def test_no_budget_uses_default_needs(db, tmp_path): ...      # no BudgetPeriod: gap uses NEEDS_DEFAULT
def test_open_period_needs_lines_used(db, tmp_path): ...      # a needs line the defaults don't have counts
def test_none_savings_balance_counts_zero(db, tmp_path): ...  # current_balance_cents None on the savings account
def test_never_goal_stored_without_a_date(db, tmp_path): ...  # a $1 extra on a never debt: target_date None
def test_busy_when_the_lock_is_held(db, tmp_path): ...        # user_lock held: status busy, no run row, no goals

# cli
def test_cli_prints_goals(db, tmp_path, capsys): ...          # surplus, cap, each goal's fields; "(over cap)" when the total exceeds the cap; "no end date" for a never goal
def test_cli_defaults_to_the_simulated_date(db, tmp_path, monkeypatch): ...  # sim prefs date is passed as today
def test_cli_bad_prefs_exit_1(db, capsys): ...                # --prefs '{"max_share": 2}' prints the message, exit 1, no run
def test_cli_error_exit_1(db, capsys): ...
```

- [ ] **Step 2: Run, verify FAIL:** `../venv/bin/python -m pytest tests/test_goal_graph.py -q`
- [ ] **Step 3: Implement `goal_llm.py`.** The prompt carries the spec's rules in the spec's order (the 8% APR rule right after "most important first"), names amounts as cents, and is written in the style of `budget_llm.DECIDE_PROMPT`.
- [ ] **Step 4: Implement `goal_graph.py`.** Facts are computed in one helper from `load_txns` over the history window. `cap_cents = round_cents(Decimal(surplus) * Decimal(str(max_share)))`. Run setup follows `run_budget`: a `running` row, one `PrivacyGateway` per run, status and `finished_at` set in a `finally`-safe way. The CLI mirrors `budget_graph`'s (`--user`, `--date`, `--model`, plus `--prefs`), with `--date` defaulting to `sim_today(s, user) or date.today()`.
- [ ] **Step 5: Run, verify PASS.**
- [ ] **Step 6: Document** in `docs/tests/test_goal_graph.md`.
- [ ] **Step 7: Commit** `feat(TPC-23): goal graph and CLI`

### Task 7: Live done-when, docs and verification

**Files:**
- Modify: `backend/tests/test_goal_graph.py`, `docs/tests/test_goal_graph.md`, `docs/architecture.md`, `README.md`

- [ ] **Step 1: Write the live test** (skipped like `test_live_debt_free_with_extra`, via `ollama_has(config.OLLAMA_MODEL)`):

```python
def test_live_card_debt_goal_first(db, tmp_path):
    # seed goals-card-24; run_goals on the profile's today with the real model
    # status ok; first goal's kind is debt_payoff with a strategy; print total monthly vs cap
```

- [ ] **Step 2: Run it 3 times:** `../venv/bin/python -m pytest tests/test_goal_graph.py::test_live_card_debt_goal_first -q -s`. Expected: PASS 3/3. Read the printed amounts. If the model mixes up dollars and cents (amounts around 100× off), that's the spec's named risk: change `ProposedGoal` to `monthly_dollars`, convert in code, and rule it in the ledger. If `debt_payoff` isn't first, record the answer and stop to report; don't loosen the check.
- [ ] **Step 3: Update docs.** `docs/architecture.md`: section 4's Goal Agent row, the `goals` model, a new goal-graph section with the surplus, the code/LLM split and the 3 live runs, 6a.3's Goal Agent bullet, the API table, decision-log rows dated 2026-11-02, and M4's step 1 (surplus source). `README.md`: a "Goals" paragraph with the CLI.
- [ ] **Step 4: Full suite:** `../venv/bin/python -m pytest -q -k "not live and not sandbox"`. Expected: all pass (675 before this ticket plus the new tests). No frontend changes, so `npm test` isn't required.
- [ ] **Step 5: Commit** `docs(TPC-23): the goal graph in the architecture doc and readme; the live done-when`
