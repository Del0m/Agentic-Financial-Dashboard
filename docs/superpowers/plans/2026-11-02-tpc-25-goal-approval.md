# TPC-25 Goal Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate goals from the app, let the user accept or dismiss each one through a paused graph, and make accepted goals appear on the dashboard and as lines in next month's budget.

**Architecture:** Goal rows are the source of truth. `decide_goal` commits a decision, then resumes any paused run, and period close reads accepted rows to write `goal`-group lines. `interrupt()` only tracks the run's lifecycle, so a lost checkpoint can't lose a decision or block a budget. Pure rules live in `budget_engine`, DB work in `budget_service`, following `budget_service/alerts.py` and `api/budget.py`.

**Tech Stack:** Python, FastAPI, SQLAlchemy, Alembic, LangGraph 1.2.18 (`SqliteSaver`), pytest, Next.js 16, React, vitest.

**Spec:** `docs/superpowers/specs/2026-11-02-tpc-25-goal-approval-design.md`

## Global Constraints

- Branch `TPC-25` from `origin/main`, not the current checkout: `git fetch origin && git switch -c TPC-25 origin/main`. Local `main` and `codex/tpc-24-goal-validation` are both behind it.
- Money is integer cents, rounded with `round_cents`. No model call happens after the pause.
- Commit messages carry no `Co-Authored-By` or "Generated with Claude Code" lines (the user's standing rule for this repo).
- Every new or changed test gets an entry in `docs/tests/<test file>.md`, in that folder's existing format.
- Frontend: read the relevant guide in `frontend/node_modules/next/dist/docs/` before writing page code (`frontend/AGENTS.md`).
- Match the surrounding style: lowercase `#` comments that explain why, and code about as dense as its neighbours.
- Backend commands run from `backend/`; the fast suite is `../venv/bin/python -m pytest -q -k 'not live and not sandbox'`. Frontend commands run from `frontend/`: `npm test`, `npm run lint`.
- Don't commit `eval/results/2026-10-29/` or `eval/results/2026-10-31/`; they aren't part of this work.

## Review Focus

1. **Double click, or accepting a goal a newer run replaced:** exactly one decision lands, the second gets 409, and one budget line results. Pinned in Task 5.
2. **Generating again after accepting, or accepting during a generate:** the new cap excludes accepted money, the accepted kind isn't offered, and a run that was overtaken writes nothing, so the budget never holds two lines of a kind. Pinned in Tasks 3 and 5.
3. **A proposal left undecided over month end:** it's gone after close and can't be accepted on stale numbers. Pinned in Task 2.
4. **Server restart or lost checkpoint while paused:** accepting still works and the line still arrives at close. Pinned in Tasks 5 and 8.
5. **A goal bigger than the Free line:** other lines are untouched, the total rises, and the `over_income` alert fires. Pinned in Task 2.

---

### Task 1: Goal lines in the engine, and `start_date`

**Files:**
- Create: `backend/alembic/versions/<rev>_goal_start_date.py` (down revision `8f4b2a7c901e`)
- Modify: `backend/app/db/models.py` (`Goal`, `LineGroup`), `backend/app/budget_engine/txns.py`, `backend/app/budget_engine/groups.py`, `backend/app/budget_engine/goals.py`, `backend/app/budget_engine/pace.py`, `backend/app/budget_engine/styles.py`, `backend/app/agents/goal_graph.py` (`persist` only)
- Test: `backend/tests/test_goals.py`, `test_budget_txns.py`, `test_budget_pace.py`, `test_budget_styles.py`, `test_budget_groups.py`, `test_goal_graph.py`, `test_migrations.py`

**Interfaces:**
- Produces, in `budget_engine/txns.py`, beside `SAVINGS` and `FREE`:
  - `GOAL_DEBT_PAYOFF`, `GOAL_EMERGENCY_FUND`, `GOAL_SAVINGS`, and `GOAL_CATEGORIES: dict[str, str]` mapping `debt_payoff`, `emergency_fund`, `savings` to them
  - `LOAN_PAYMENTS = "LOAN_PAYMENTS"`
  - `net_of_goal(actual: int, limit: int, goal: int) -> int`: `actual - min(goal, max(actual - limit, 0))`
- Produces, in `budget_engine/groups.py`: `default_group` returns `"goal"` for the goal categories; `default_name` returns `"Debt payoff"`, `"Emergency fund"`, `"Savings goal"`.
- Produces, in `budget_engine/goals.py`:
  - `CommittedGoal(id: int, kind: GoalKind, monthly_cents: int, start_date: date, target_date: date)`, frozen dataclass
  - `budgeted_in(goal: CommittedGoal, month: date) -> bool`: `goal.start_date <= month <= month_start(goal.target_date)`
  - `finished_before(goal: CommittedGoal, month: date) -> bool`: `month_start(goal.target_date) < month`
  - `committed_cents(goals: Sequence[CommittedGoal]) -> int`
- Produces: `Goal.start_date: Mapped[Optional[Date]]`, written by `persist` from `state["start"]`.

- [ ] **Step 1: Write the failing tests**

```python
# test_goals.py
def test_budgeted_from_start_through_target_month():
    g = CommittedGoal(1, "savings", 10000, date(2026, 12, 1), date(2027, 11, 1))
    assert not budgeted_in(g, date(2026, 11, 1))
    assert budgeted_in(g, date(2026, 12, 1)) and budgeted_in(g, date(2027, 11, 1))
    assert not budgeted_in(g, date(2027, 12, 1))
    assert finished_before(g, date(2027, 12, 1)) and not finished_before(g, date(2027, 11, 1))

# test_budget_txns.py
def test_net_of_goal_only_attributes_what_is_above_the_limit():
    assert net_of_goal(21000, 21000, 20000) == 21000    # the goal went to a card; the loan payment is unchanged
    assert net_of_goal(79300, 21000, 58300) == 21000    # the whole goal went to the loan
    assert net_of_goal(30000, 21000, 58300) == 21000    # part of it did
    assert net_of_goal(10000, 21000, 58300) == 10000    # a smaller payment is still adopted
    assert net_of_goal(58300, 0, 58300) == 0            # no line: all of it is the goal

# test_budget_pace.py
# a guard: this already passes at Step 2, because a fixed line with no transactions reports nothing
def test_goal_line_has_no_spending_pace_or_flag():
    line = BudgetLine("GOAL_DEBT_PAYOFF", "fixed", 58300, "goal")
    r = evaluate([line], [Txn(date(2026, 12, 3), 90000, "LOAN_PAYMENTS")], date(2026, 12, 20))
    goal = next(x for x in r if x.category == "GOAL_DEBT_PAYOFF")
    assert (goal.spent_cents, goal.pace, goal.flagged, goal.group) == (0, None, False, "goal")

def test_goal_paid_to_a_loan_is_not_overspending():
    lines = [BudgetLine("LOAN_PAYMENTS", "fixed", 21000, "needs"), BudgetLine("GOAL_DEBT_PAYOFF", "fixed", 58300, "goal")]
    r = evaluate(lines, [Txn(date(2026, 12, 3), 79300, "LOAN_PAYMENTS")], date(2026, 12, 20))
    loan = next(x for x in r if x.category == "LOAN_PAYMENTS")
    assert (loan.spent_cents, loan.flagged) == (21000, False)

def test_goal_paid_to_an_unlined_loan_stays_out_of_free():
    lines = [BudgetLine("FREE", "flexible", 10000, "free"), BudgetLine("GOAL_DEBT_PAYOFF", "fixed", 58300, "goal")]
    r = evaluate(lines, [Txn(date(2026, 12, 3), 58300, "LOAN_PAYMENTS")], date(2026, 12, 20))
    free = next(x for x in r if x.category == "FREE")
    assert (free.spent_cents, free.flagged) == (0, False)

# test_budget_styles.py
def test_caps_goal_line_shrinks_free_by_exactly_its_amount():
    lines = [BudgetLine("RENT_AND_UTILITIES", "fixed", 200000, "needs")]
    goal = BudgetLine("GOAL_SAVINGS", "fixed", 30000, "goal")
    without, with_goal = (apply_style("caps", ls, 420000, 0, DEFAULT_TARGETS) for ls in (lines, [*lines, goal]))
    assert with_goal.limits["FREE"] == without.limits["FREE"] - 30000
    assert with_goal.limits["RENT_AND_UTILITIES"] == 200000 and with_goal.total_cents == 420000

def test_split_goal_lines_come_out_of_saving_target():
    goal = BudgetLine("GOAL_DEBT_PAYOFF", "fixed", 30000, "goal")
    assert apply_style("50_30_20", [goal], 400000, 0, DEFAULT_TARGETS).limits["SAVINGS_TRANSFER"] == 50000

def test_split_saving_target_never_negative():
    goal = BudgetLine("GOAL_DEBT_PAYOFF", "fixed", 90000, "goal")
    assert apply_style("50_30_20", [goal], 400000, 0, DEFAULT_TARGETS).limits["SAVINGS_TRANSFER"] == 0

def test_split_goal_past_income_is_reported():
    lines = [BudgetLine("RENT_AND_UTILITIES", "fixed", 200000, "needs"), BudgetLine("TRAVEL", "flexible", 120000, "wants"),
             BudgetLine("GOAL_DEBT_PAYOFF", "fixed", 90000, "goal")]
    r = apply_style("50_30_20", lines, 400000, 0, DEFAULT_TARGETS)
    assert r.total_cents == 410000 and [(f.kind, f.lines_cents) for f in r.facts] == [("over_income", 410000)]

# test_budget_groups.py
def test_goal_categories_have_group_and_name():
    assert default_group("GOAL_DEBT_PAYOFF") == "goal" and default_name("GOAL_DEBT_PAYOFF") == "Debt payoff"

# test_goal_graph.py
def test_persist_stores_the_plan_start(user):
    run_goals(user, UID, TODAY, model=model(g("savings", 10000)))
    assert stored(user)[0].start_date == START

# test_migrations.py: extend the upgrade test: "start_date" is a column of goals; downgrade removes it
```

- [ ] **Step 2: Run them to verify they fail**

Run: `../venv/bin/python -m pytest tests/test_goals.py tests/test_budget_txns.py tests/test_budget_pace.py tests/test_budget_styles.py tests/test_budget_groups.py tests/test_goal_graph.py tests/test_migrations.py -q -k 'not live'`
Expected: FAIL (`CommittedGoal` and `net_of_goal` not defined, no `start_date`, a goal paid to a loan is flagged). `test_goal_line_has_no_spending_pace_or_flag` and `test_caps_goal_line_shrinks_free_by_exactly_its_amount` pass already; they guard behaviour that must stay.

- [ ] **Step 3: Implement the migration, model and helpers**

The migration adds a nullable `start_date`. Add `"goal"` to `LineGroup`, and `completed` to the status comment if there is one. `evaluate` needs no goal branch. After `category_totals`, when a `GOAL_DEBT_PAYOFF` line exists and `LOAN_PAYMENTS` was spent, it replaces `spent[LOAN_PAYMENTS]` with `net_of_goal(spent, loan line's limit or 0, goal limit)`, so both the loan line and the unlined total Free absorbs are net. `_split` sets `limits[SAVINGS] = max(target["saving"] - goal_total, 0)`, where `goal_total` sums lines in group `goal`, and adds `StyleFact("over_income", None, sum(limits.values()), income_cents, None)` when `goal_total > 0` and the limits pass income. A budget with no goal lines behaves exactly as before. `_caps` needs no change: test it, don't edit it.

- [ ] **Step 4: Run them to verify they pass**

Same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend docs/tests
git commit -m "feat(TPC-25): goal budget lines in the engine and goals.start_date"
```

---

### Task 2: Budgets hold accepted goals

**Files:**
- Create: `backend/app/budget_service/goal_lines.py`
- Modify: `backend/app/budget_service/close.py`, `backend/app/budget_service/initialize.py`
- Test: `backend/tests/test_close.py`, `backend/tests/test_budget_service.py`

**Interfaces:**
- Consumes: Task 1's `CommittedGoal`, `budgeted_in`, `finished_before`, `GOAL_CATEGORIES`, `net_of_goal`.
- Produces, in `budget_service/goal_lines.py` (must not import `app.agents`, because `goal_graph` imports it):
  - `committed_goals(session, user_id) -> list[CommittedGoal]`: status `accepted`, ordered by priority then id
  - `stale_proposals(session, user_id, month: date) -> list[int]`: ids of `proposed` goals with `start_date` null or `<= month`
  - `expire_goal_proposals(session, goal_ids: list[int]) -> list[int]`: rechecks `proposed`, marks them `superseded`, marks their `awaiting_user` runs `expired` with `expired_goal_ids` in the summary. The caller commits. Returns the ids actually expired.
- Produces, in `gather_month`'s dict: `"goals": list[{"category", "name", "monthly_cents", "goal_id"}]`, `"finished_goal_ids": list[int]`, `"expired_goal_ids": list[int]`.
- Produces: goal lines in `adapt_month` with `rule == "goal"`, stored by `persist_month` with `source="user"`.

- [ ] **Step 1: Write the failing tests**

Use one fixture: an open November period with a rent line, a `LOAN_PAYMENTS` line, a `SAVINGS_TRANSFER` line and Free, closed with `close_due(db, uid, date(2026, 12, 1))`. An accepted goal starts on `date(2026, 12, 1)`.

```python
def test_accepted_goal_becomes_a_line_in_the_next_period(...):
    # debt_payoff at 58300 accepted during november
    line = lines_of(next_period)["GOAL_DEBT_PAYOFF"]
    assert (line.name, line.group, line.kind, line.limit_cents, line.source) == ("Debt payoff", "goal", "fixed", 58300, "user")

def test_goal_line_takes_its_amount_from_free_only(...):
    # known arithmetic, since the fixture's user id is fixed: free == income - the other new limits - 58300
    assert december["FREE"].limit_cents == INCOME - sum(other_limits) - 58300
    assert december["RENT_AND_UTILITIES"].limit_cents == RENT

def test_goal_over_free_keeps_other_lines_and_raises_over_income_alert(...):
    # goal larger than free: every other line unchanged, total_cents rises, one over_income alert on the new period

def test_goal_starting_later_gets_no_line_yet(...):        # start_date 2027-01-01: not in december, in january
def test_goal_line_carries_user_rename(...):               # old line renamed "Card payoff": the new line keeps the name
def test_goal_line_never_smoothed_or_adopted(...):         # limit stays 58300 over two closes with $0 actual

def test_finished_goal_completes_and_frees_its_line(...):
    # target_date 2026-12-01: closing december writes no goal line and sets status completed; the kind is free again
def test_stopped_goal_line_ends_at_close(...):             # status dismissed: december has its line, january doesn't

def test_saving_line_ignores_goal_transfers(...):
    # november: saving limit 50000, GOAL_SAVINGS 19440, 69440 saved: SAVINGS_TRANSFER adopts 50000, not 69440
def test_goal_transfer_alone_adds_no_saving_line(...):     # no saving line, saved equals the goal: none appears
def test_loan_line_ignores_debt_goal(...):
    # GOAL_DEBT_PAYOFF 58300 with LOAN_PAYMENTS actual 79300: the loan line adopts 21000
def test_debt_goal_paid_to_a_card_leaves_the_loan_line(...):
    # GOAL_DEBT_PAYOFF 20000, loan limit and actual 21000: the loan line stays 21000, not 1000
def test_goal_paid_to_an_unlined_loan_is_not_free_spending(...):
    # no loan line: the goal's payment is out of free_actual_cents and the unbudgeted list

def test_undecided_proposal_expires_with_its_start_month(...):
    # proposed, start 2026-12-01: superseded after the close of november; its awaiting_user run becomes expired
def test_later_proposal_survives_close(...):               # start 2027-01-01 stays proposed
def test_null_start_proposal_expires(...):
def test_answer_during_close_stands(...):
    # gather, then accept the goal, then persist: it stays accepted, not superseded

# test_budget_service.py
def test_first_budget_includes_goal_already_started(...):  # start <= current month: line present, free reduced
def test_first_budget_leaves_out_goal_starting_later(...): # start next month: no line
def test_first_budget_expires_a_stale_proposal(...):       # proposed, start <= the first budget's month: superseded
```

- [ ] **Step 2: Run them to verify they fail**

Run: `../venv/bin/python -m pytest tests/test_close.py tests/test_budget_service.py -q`
Expected: FAIL (no goal lines).

- [ ] **Step 3: Implement `goal_lines.py` and wire `gather_month`**

`gather_month` computes `new_start`, then `goals = committed_goals(...)`, keeps the `budgeted_in(g, new_start)` ones as `"goals"` with the name from the old line of that category or `default_name`, and collects `finished_before(g, new_start)` ids. Using the closed period's goal lines, it nets with `net_of_goal` before anything reads the values: `spent[LOAN_PAYMENTS]` against the `debt_payoff` line and the loan line's limit (0 with no line), and `saved` against the `emergency_fund` and `savings` lines together and the saving line's limit (0 with no line). So `unlined`, `free_actual`, `actual(ln)` and `"saved_cents"` are all net. No transaction carries a goal category, so `unbudgeted` needs no filter.

- [ ] **Step 4: Implement `adapt_month` and `persist_month`**

`adapt_month` filters `group == "goal"` out of the lines it passes to `close_period`, as it does for `free`, then appends one entry per `gathered["goals"]` with `category`, `name`, `group "goal"`, `kind "fixed"`, `limit_cents` and `old_cents` from the old line or 0, `actual_cents` 0, `new_cents` the goal's amount, `rule "goal"`, and passes them to `apply_style`. `persist_month` uses `source="user"` for rule `accepted` or `goal`, sets `finished_goal_ids` rows to `completed` where still `accepted`, calls `expire_goal_proposals`, and adds both id lists to the summary. Everything stays in its one commit.

- [ ] **Step 5: Implement `initialize_budget`**

Pass goal lines with `budgeted_in(g, current)` to `apply_style` and append them with `source="user"`. In the same commit, call `expire_goal_proposals(session, stale_proposals(session, user_id, current))`: with no period to close, nothing else would retire a proposal made before the first budget. A goal can only predate the first budget when the user generated before ever running a budget check.

- [ ] **Step 6: Run the budget suites**

Run: `../venv/bin/python -m pytest tests/test_close.py tests/test_budget_service.py tests/test_budget_close.py tests/test_simulate.py tests/test_m3_demo.py -q -k 'not live'`
Expected: PASS, with the existing close tests unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend docs/tests
git commit -m "feat(TPC-25): accepted goals become budget lines at close and in the first budget"
```

---

### Task 3: Generating around accepted goals

**Files:**
- Modify: `backend/app/agents/goal_graph.py` (`load_facts`, `_print_report`), `backend/app/budget_engine/goals.py` (`GoalFacts`), `backend/app/privacy/gateway.py` (`_goals`)
- Test: `backend/tests/test_goal_graph.py`, `test_goal_validation_graph.py`, `test_gateway.py`

**Interfaces:**
- Consumes: `committed_goals`, `committed_cents`.
- Produces: `GoalFacts.committed_cents: int = 0`; the gateway's `goals` payload gains `"already_committed"` in dollars only when nonzero; `GoalState.committed_cents` and `GoalState.committed_ids: list[int]`; run reason `goals_changed`.

- [ ] **Step 1: Write the failing tests**

```python
# goals-card-24: surplus 97175, default cap 77740
def test_cap_excludes_accepted_goals(user):
    accept(user, kind="debt_payoff", monthly_cents=58300)     # helper writes an accepted row
    r = run_goals(user, UID, TODAY, model=model(g("emergency_fund", 19440)))
    assert (r["cap_cents"], r["committed_cents"], r["status"]) == (19440, 58300, "ok")

def test_new_goal_cannot_spend_accepted_money(user):
    accept(user, kind="debt_payoff", monthly_cents=58300)
    over = model(g("emergency_fund", 19441)); over.replies.append(over.replies[0])
    r = run_goals(user, UID, TODAY, model=over)
    assert r["status"] == "error" and any(i["code"] == "over_cap" for i in r["validation_issues"])

def test_accepted_kind_is_not_offered(user):
    accept(user, kind="debt_payoff", monthly_cents=1000)
    r = run_goals(user, UID, TODAY, model=model(g("savings", 1000)))
    assert "debt_payoff" not in r["offered"]

def test_accepted_goals_fill_the_cap_stops_without_a_model_call(user):
    accept(user, kind="debt_payoff", monthly_cents=77700)     # 40 cents left
    r = run_goals(user, UID, TODAY, model=ScriptedChatModel(replies=[]))
    assert (r["status"], r["reason"], r.get("attempts", 0)) == ("no_affordable_goals", "cap_below_minimum", 0)
    assert stored(user, "accepted")      # accepted goals are never withdrawn

def test_accepted_goals_past_the_cap_clamp_to_zero(user):
    accept(user, kind="debt_payoff", monthly_cents=80000)
    assert run_goals(user, UID, TODAY, model=ScriptedChatModel(replies=[]))["cap_cents"] == 0

def test_goal_accepted_during_the_run_stops_it_writing(user, monkeypatch):
    # an earlier proposal is standing. patch goal_graph.invoke_structured to accept its debt goal, then return a
    # valid proposal at the full cap: the check at persist must see the change
    r = run_goals(user, UID, TODAY, model=model(g("emergency_fund", 77740)))
    assert (r["status"], r["reason"], r["goal_ids"]) == ("error", "goals_changed", [])
    assert [x.kind for x in stored(user, "accepted")] == ["debt_payoff"]
    assert not [x for x in stored(user) if x.kind == "emergency_fund"]      # nothing written, others left standing

def test_every_kind_accepted_says_so(user, capsys):
    # debt, emergency and savings all accepted: status nothing_offered, and the CLI's line mentions accepted goals

def test_withdraw_leaves_accepted_goals(user):             # a no_surplus run supersedes proposed rows only

# test_gateway.py
def test_goals_context_reports_committed_money_only_when_some(...):
    # committed_cents=58300: "already_committed" == 583.0; default: key absent
```

- [ ] **Step 2: Run them to verify they fail**

Run: `../venv/bin/python -m pytest tests/test_goal_graph.py tests/test_goal_validation_graph.py tests/test_gateway.py -q -k 'not live'`
Expected: FAIL (cap ignores accepted goals).

- [ ] **Step 3: Implement**

In `load_facts`: `committed = committed_goals(s, user_id)`, `skip = (*prefs.skip_kinds, *(g.kind for g in committed))`, `cap = max(round_cents(Decimal(surplus) * Decimal(str(prefs.max_share))) - committed_cents(committed), 0)`, and keep `committed_ids` in the state. The existing `cap < MIN_MONTHLY_CENTS` check handles a cap of 0. In `persist`, inside its transaction and before `_supersede`, compare the current accepted ids with `committed_ids`; if they differ, return `{"status": "error", "reason": "goals_changed", "plans": []}` and write nothing. `_print_report` needs no change for this: its error branch prints the reason and says the earlier proposal stands. Add one rule to `goal_llm`'s prompt only if `already_committed` reads ambiguously in a live run. Reword `nothing_offered`'s CLI line to "the preferences or goals you've accepted cover every kind that's available."

- [ ] **Step 4: Run them to verify they pass**

Same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend docs/tests
git commit -m "feat(TPC-25): new goals fit around accepted ones"
```

---

### Task 4: The graph pauses for approval

**Files:**
- Modify: `backend/app/agents/goal_graph.py`
- Test: `backend/tests/test_goal_approval_graph.py` (new)

**Interfaces:**
- Produces, in `agents/goal_graph.py`:
  - `build_goal_graph(session_factory, *, model, gateway, progress=None, claim=None, checkpointer=None)`
  - `run_goals_locked(session_factory, user_id, today, *, model, claim, checkpointer=None, audit_path=None, on_start=None) -> dict`: the caller holds the lock
  - `run_goals(session_factory, user_id, today, *, model=None, audit_path=None, checkpointer=None) -> dict`: same behaviour as today plus the pause
  - `resume_goal_run(session_factory, run_id: int, goal_id: int, decision: str, *, checkpointer) -> dict`
  - `goal_waiting_for(session_factory, checkpointer, run_id: int) -> list[int]`: `[]` when not paused or the checkpoint is gone
  - `finish_paused_run(session_factory, run_id: int) -> None`: finishes an `awaiting_user` run from its rows when none is `proposed` and no checkpoint can do it. Status `ok`, `finished_at` set, `decisions` read from rows, the rest of the summary kept
- `GoalState` gains `decisions: dict`, `waiting_for: list[int]`, `committed_cents: int`.
- Thread id: `goals-{run_id}`. Invoke with `durability="exit"`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.fixture
def ck():
    return InMemorySaver()

def test_run_pauses_after_persisting(user, ck):
    r = run_goals(user, UID, TODAY, model=model(g("debt_payoff", 58300, "avalanche")), checkpointer=ck)
    assert r["status"] == "awaiting_user" and r["waiting_for"] == r["goal_ids"]
    assert stored(user)[0].status == "proposed"
    row = run_row(user, r["run_id"])
    assert row.status == "awaiting_user" and row.finished_at is None and row.summary_json["goal_ids"] == r["goal_ids"]

def test_no_checkpointer_does_not_pause(user):             # CLI path: status ok, run row ok, plans still in the result
def test_no_goals_means_no_pause(user, ck):                # a no_surplus run ends ok; goal_waiting_for == []
def test_pause_releases_the_lock(user, ck):
    run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    assert not user_lock(UID).locked() and not running_elsewhere(user, UID)

def test_paused_checkpoint_holds_no_debts_or_facts(user, tmp_path):
    # a real SqliteSaver on tmp_path. every row of its checkpoints and writes tables, read as bytes:
    # no b"Platinum Card", no b"Student Loan", no rationale text; and graph.get_state(...).values has empty debts, plans, context
def test_failed_run_leaves_no_checkpoint(user, ck):
    # the model raises mid-run: nothing in list(ck.list(thread))
def test_finished_run_deletes_its_checkpoint(user, ck):    # decide every goal and resume: list(ck.list(thread)) == []

def test_resume_waits_for_every_goal(user, ck):
    r = run_goals(user, UID, TODAY, model=model(g("debt_payoff", 58300, "avalanche"), g("savings", 10000)), checkpointer=ck)
    first, second = r["goal_ids"]
    set_status(user, first, "accepted")
    assert resume_goal_run(user, r["run_id"], first, "accepted", checkpointer=ck)["waiting_for"] == [second]
    set_status(user, second, "dismissed")
    done = resume_goal_run(user, r["run_id"], second, "dismissed", checkpointer=ck)
    assert done["status"] == "ok" and done["waiting_for"] == []
    summary = run_row(user, r["run_id"]).summary_json
    assert summary["decisions"] == {str(first): "accepted", str(second): "dismissed"}
    assert summary["surplus_cents"] == 97175 and summary["attempts"] == 1

def test_decision_before_the_pause_is_mark_still_finishes(user, ck):
    # a decision landing between persist and the awaiting_user mark: run_goals resumes it, as run_budget does

def test_regenerating_expires_the_paused_run(user, ck):
    r1 = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    r2 = run_goals(user, UID, TODAY, model=model(g("savings", 2000)), checkpointer=ck)
    assert run_row(user, r1["run_id"]).status == "expired" and run_row(user, r2["run_id"]).status == "awaiting_user"
    assert list(ck.list(thread_of(r1["run_id"]))) == []

def test_failed_regeneration_keeps_the_paused_run(user, ck):  # both attempts rejected: r1 stays awaiting_user, its goals proposed
def test_withdrawing_expires_the_paused_run(user, ck):        # a later no_surplus run: expired
def test_restart_cleanup_leaves_a_paused_run(user, ck):       # close_interrupted_runs(user) == 0 and it stays awaiting_user
def test_reused_run_id_starts_clean(user, ck):                # a stale thread for the id is deleted before the run starts
```

- [ ] **Step 2: Run them to verify they fail**

Run: `../venv/bin/python -m pytest tests/test_goal_approval_graph.py -q`
Expected: FAIL (`checkpointer` is not a parameter).

- [ ] **Step 3: Implement the nodes**

`persist` routes to `forget_facts` when `checkpointer is not None and state["goal_ids"]`, otherwise `END`. `forget_facts` returns `{"debts": [], "plans": [], "allocations": [], "proposal": [], "context": ""}`. `await_approval` follows `budget_graph.await_approval`, but its pending list is the `goal_ids` rows still `proposed`, and it returns `{"decisions": {id: status}}` once none are; the resume payload is `[goal_id, decision]` and rows decide the outcome. It then ends; the caller writes the final row. `build_goal_graph` compiles with `checkpointer=checkpointer`. A `goals_changed` error from `persist` routes straight to `END`.

- [ ] **Step 4: Implement `_supersede` expiring paused runs**

`_supersede` also marks the superseded rows' `awaiting_user` runs `expired`, excluding `state["run_id"]`, reusing `expire_goal_proposals`' run handling so the two can't drift. It returns what the node needs to delete those threads after its commit; `delete_thread` failures are logged, never raised.

- [ ] **Step 5: Implement the run functions**

Split `run_goals` into lock handling and `run_goals_locked`. `run_goals_locked` deletes any stale thread, calls `on_start(run_id)`, and invokes with the thread config and `durability="exit"` when a checkpointer is given. If no `__interrupt__` came back, it deletes the thread (in a `finally`, so the error and `KeyboardInterrupt` paths are covered) and calls `_finish`. If paused, it writes the summary with `outcome: "awaiting_user"`, run status `awaiting_user` and no `finished_at`, then handles a decision that landed early. `resume_goal_run` mirrors `resume_budget_run`, including its per-run lock: a resume that still pauses returns `waiting_for`; one that finishes calls `_finish` with `decisions` included, then deletes the thread.

- [ ] **Step 6: Run the goal suites**

Run: `../venv/bin/python -m pytest tests/test_goal_approval_graph.py tests/test_goal_graph.py tests/test_goal_validation_graph.py -q -k 'not live'`
Expected: PASS, including every TPC-23 and TPC-24 test unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend docs/tests
git commit -m "feat(TPC-25): the goal graph pauses for approval with interrupt()"
```

---

### Task 5: Deciding on a goal

**Files:**
- Create: `backend/app/budget_service/goal_decisions.py`
- Test: `backend/tests/test_goal_decisions.py`

**Interfaces:**
- Consumes: `resume_goal_run`, `goal_waiting_for`.
- Produces:
  - exceptions `GoalNotFound`, `GoalAlreadyDecided`, `GoalNotAcceptable`
  - `decide_goal(session_factory, user_id: str, goal_id: int, decision: Literal["accepted", "dismissed"], *, checkpointer=None) -> Goal`
  - `finish_decided_runs(session_factory, user_id: str, *, checkpointer) -> None`: resumes any paused goal run with a decided goal; when its checkpoint is gone and no row is `proposed`, calls `finish_paused_run`. Failures are logged

- [ ] **Step 1: Write the failing tests**

```python
def test_accept_marks_the_row_and_resumes(...):            # the only goal accepted: row accepted, run ok
def test_other_users_goal_is_not_found(...):               # GoalNotFound
def test_accept_twice_is_already_decided(...):             # GoalAlreadyDecided, status unchanged
def test_accept_after_dismiss_is_already_decided(...):
def test_superseded_goal_cannot_be_accepted(...):          # GoalAlreadyDecided
def test_completed_goal_cannot_be_dismissed(...):          # GoalAlreadyDecided
def test_dismiss_stops_an_accepted_goal(...):              # accepted -> dismissed
def test_row_without_start_date_cannot_be_accepted(...):   # GoalNotAcceptable, still proposed; it can be dismissed
def test_second_goal_of_an_accepted_kind_is_refused(...):
    # debt goal A accepted, a debt goal B proposed by an overtaking run: GoalNotAcceptable, B stays proposed
    # and the budget can never hold two lines of a kind

def test_a_failing_resume_keeps_the_decision(monkeypatch, ...):
    # resume_goal_run raises: decide_goal returns, the row stays accepted
    # finish_decided_runs then resumes and the run ends ok

def test_lost_checkpoint_keeps_the_decision_and_ends_the_run(...):
    # delete the thread first: accept still works, nothing raises, and with no proposed row left the run ends ok
def test_lost_checkpoint_waits_for_the_other_goals(...):   # one of two decided: the run stays awaiting_user

def test_only_goal_runs_are_resumed(...):                  # a paused budget run for the user isn't touched
def test_the_conditional_update_lets_one_decision_through(...):
    # two decide_goal calls in sequence, the second built from a stale read: exactly one changes the row.
    # sequential because the in-memory test db is one connection that threads can't share
```

- [ ] **Step 2: Run them to verify they fail**

Run: `../venv/bin/python -m pytest tests/test_goal_decisions.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `goal_decisions.py`**

Follow `decide_alert`: one conditional `update(Goal)`, committed before any resume. Accept requires `status == "proposed"`, `start_date IS NOT NULL`, and `NOT EXISTS` an accepted goal of the same user and kind, all in that one statement, so the check can't race. When no row changes, re-read the goal to choose the exception. Dismiss allows `proposed` or `accepted`. Paused runs are `AgentRun.graph == "goals", status == "awaiting_user"`.

- [ ] **Step 4: Run them to verify they pass**

Same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend docs/tests
git commit -m "feat(TPC-25): accept, dismiss or stop a goal"
```

---

### Task 6: Goal routes

**Files:**
- Modify: `backend/app/api/goals.py`, `backend/app/api/transactions.py`
- Test: `backend/tests/test_goals_api.py`, `backend/tests/test_transactions_api.py`

**Interfaces:**
- Consumes: `run_goals_locked`, `decide_goal`, `finish_decided_runs`, `budget_hooks.session_factory`, `make_model`, `get_checkpointer`, `user_lock`, `running_elsewhere`, `sim_today`.
- Produces: the routes in the spec's API section; module-level `_spawn` and `_today` in `api/goals.py`, as in `api/budget.py`, so tests can run work inline; `GoalRunOut` with `issues: list[{message, index, kind}]`.

- [ ] **Step 1: Write the failing tests**

Add an `api` fixture like `test_budget_api.py`'s: `InMemorySaver`, scripted model queue, `_spawn` inline, `DEV_USER_EMAIL` set to the seeded `goals-card-24` user. Two existing tests must change, deliberately: move the `client, db` tests in `test_goals_api.py` onto this fixture, because `GET /goals` now reaches `budget_hooks` and would otherwise hit the real `SessionLocal`, and add `start_date` to the exact dict in `test_own_proposed_goals_by_priority`.

```python
def test_generate_then_poll_reports_waiting(api):
    run_id = client.post("/goals/generate", json={}).json()["run_id"]
    body = client.get(f"/goals/runs/{run_id}").json()
    assert body["status"] == "awaiting_user" and body["waiting_for"] == body["goal_ids"]

def test_generate_uses_the_simulated_date(api):            # prefs_json["sim"]: start_date follows it, not the clock
def test_generate_busy_is_409_and_keeps_the_lock_clean(api):
    # lock already held: 409; a recent running row: 409 and the lock is released afterwards
def test_generate_start_failure_releases_the_lock(api, monkeypatch):
    # _spawn raises: 500, and the next generate works
def test_run_reports_outcomes(api):                        # no_surplus: status ok, outcome no_surplus, goal_ids []
def test_run_reports_validation_failure(api):
    # exhausted: status error, issues carry code-written messages, and no raw model text anywhere in the response
def test_run_of_another_user_or_graph_is_404(api):
def test_waiting_for_survives_a_lost_checkpoint(api):      # read from rows
def test_interrupted_run_reports_its_error(api):           # close_interrupted_runs' summary["error"] reaches `error`

def test_accept_returns_the_goal(api):                     # 200, status accepted, start_date set
def test_accept_decided_is_409_other_users_404(api):
def test_accept_without_start_date_is_409(api):
def test_accept_second_of_a_kind_is_409(api):
def test_dismiss_proposed_and_stop_accepted(api):

def test_get_goals_status_filter(api):
    # no param: proposed only (TPC-23's contract); accepted; current = both, proposed first, then by priority
    # superseded, dismissed and completed never appear; other users' never appear
def test_get_goals_finishes_a_decided_run(api):            # resume failed earlier; GET /goals heals it

# test_transactions_api.py
def test_a_goal_line_is_not_a_correction_target(...):      # PATCH with GOAL_DEBT_PAYOFF: 422
```

- [ ] **Step 2: Run them to verify they fail**

Run: `../venv/bin/python -m pytest tests/test_goals_api.py tests/test_transactions_api.py -q`
Expected: FAIL (routes missing).

- [ ] **Step 3: Implement the routes**

`generate` follows `start_run` in `api/budget.py`: acquire the lock non-blocking; return 409 if busy or `running_elsewhere` (releasing first); resolve the date from `req.date or sim_today or _today`; spawn a worker that calls `run_goals_locked(..., claim=lock, checkpointer=budget_hooks.get_checkpointer(), on_start=...)`, releasing in `finally`. Wait up to `START_WAIT_SECONDS` for the run id. The `/goals/runs/{id}` route reads `outcome`, `reason`, `goal_ids`, `attempts`, `validation_issues` and `error` from `summary_json`, tolerating missing keys (`close_interrupted_runs` writes only `error`); its `waiting_for` is the summary's `goal_ids` still `proposed`, and only while `awaiting_user`. Register the literal path `/goals/generate` before `/goals/{id}/...`. In `PATCH /transactions`, the allowed categories exclude lines in group `goal`.

- [ ] **Step 4: Run them to verify they pass**

Same command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend docs/tests
git commit -m "feat(TPC-25): generate and decide goals over the API"
```

---

### Task 7: Goals page and dashboard

**Files:**
- Create: `frontend/app/goals/page.tsx`, `frontend/app/goals/page.test.tsx`, `frontend/components/GoalCard.tsx`, `frontend/components/GoalCard.test.tsx`
- Modify: `frontend/lib/api.ts`, `frontend/lib/api.test.ts`, `frontend/lib/format.ts`, `frontend/app/dashboard/page.tsx`, `frontend/components/BudgetPaceBar.tsx`, `frontend/components/BudgetPaceBar.test.tsx`, `frontend/components/TransactionsTable.tsx`, `frontend/components/TransactionsTable.test.tsx`
- Docs: `docs/tests/GoalCard.test.md` and the matching entries for each changed test file

**Interfaces:**
- Produces, in `lib/api.ts`: `type Goal`, `type GoalRun`, `getGoals(status?: "proposed" | "accepted" | "current"): Promise<Goal[]>`, `generateGoals(date?: string): Promise<{ run_id: number | null }>`, `getGoalRun(id: number): Promise<GoalRun>`, `acceptGoal(id)`, `dismissGoal(id)`; `"goal"` added to `BudgetLine.group`.
- Produces, in `lib/format.ts`: `formatMonth(iso: string): string`, formatted in UTC. In a US timezone, a plain `new Date("2026-12-01")` would print "November 2026".
- Produces: `GoalCard({ goal, busy, onAccept, onDismiss })`.

- [ ] **Step 1: Write the failing tests**

```tsx
// GoalCard.test.tsx
test("a proposed goal shows its numbers and both decisions");
// "Debt payoff (avalanche)", "$583.00/mo", target "$21,600.00", the date, the rationale, Accept, Dismiss
test("an accepted goal says when it joins the budget and offers Stop");
// "In your budget from December 2026"; Stop calls onDismiss; no Accept
test("buttons are disabled while busy");

// goals/page.test.tsx: mocks @/lib/api as alerts/page.test.tsx does
test("Generate polls until the run settles, then lists the proposal");
test.each([
  ["no_surplus", "No money left over in a typical month"],
  ["nothing_offered", "cover every kind"],
  ["no_affordable_goals", "under $1"],
])("outcome %s says why there's no proposal", ...);
test("a failed run says the earlier proposal stands and lists the reasons");
test("goals_changed asks the user to generate again");
test("a 409 on Generate shows the server's message and re-enables the button");
test("Accept moves the goal to Accepted after a reload");
test("a 409 on Accept shows the message and reloads, dropping the stale goal");
test("no goals yet shows the empty state");

// BudgetPaceBar.test.tsx
test("a goal line shows its monthly amount and no meter");
// "Debt payoff", "$583.00/mo", queryByRole("meter") null

// TransactionsTable.test.tsx
test("goal lines aren't offered as a category");

// dashboard: covered by a new frontend/app/dashboard/page.test.tsx
test("accepted goals appear in a Goals card with their start month");
test("a goals failure stays inside its card");
test("the header link counts proposed goals");
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npm test`
Expected: FAIL (modules missing).

- [ ] **Step 3: Implement**

Read `frontend/node_modules/next/dist/docs/` for client pages first. Build `/goals` on `alerts/page.tsx`'s structure, reusing its `POLL_MS` and `MAX_POLLS` values, `mounted` ref and `reloadKey`. Result copy is keyed on `outcome`, and the error line reuses `issues[].message`. Add a Goals link to the dashboard header next to Alerts. The dashboard calls `getGoals("current")` in the same effect as `getBudget`, with separate error state so a failure stays in its card. `BudgetPaceBar` returns early for `group === "goal"`. `TransactionsTable` filters `group === "goal"` out of `choices`.

- [ ] **Step 4: Run them to verify they pass**

Run: `npm test && npm run lint`
Expected: PASS, no lint errors.

- [ ] **Step 5: Check it in the browser**

Run the app with a simulated `goals-card-24` user (seeded as in Task 8's handoff step) and walk the page at desktop and 375 px widths, with no console errors. Fix what you find before committing.

- [ ] **Step 6: Commit**

```bash
git add frontend docs/tests
git commit -m "feat(TPC-25): Goals page, dashboard card and goal budget lines"
```

---

### Task 8: The done-when, docs and handoff

**Files:**
- Create: `backend/tests/test_goal_loop.py`, `docs/tests/test_goal_loop.md`, `docs/handoffs/2026-11-02-tpc-25-manual-verification.md`
- Modify: `docs/architecture.md`, `README.md`, `docs/agent-pipelines.md`, `docs/system-walkthrough.md`, `docs/diagrams/remaining-lifecycle.puml`, `docs/diagrams/data-model.puml`, `docs/diagrams/approval-lifecycle.puml`, and the spec's status line

- [ ] **Step 1: Write the failing end-to-end tests**

All through the HTTP API, on `goals-card-24`, with a scripted model and `budget._today` and `goals._today` patched.

```python
def test_accepting_a_goal_reaches_the_dashboard_and_next_months_budget(api):
    client.post("/budget/run", json={"date": "2026-11-02"})               # november's budget
    november = client.get("/budget").json()
    free_november = line(november, "FREE")["limit_cents"]
    run_id = client.post("/goals/generate", json={"date": "2026-11-02"}).json()["run_id"]
    goal_id = client.get(f"/goals/runs/{run_id}").json()["waiting_for"][0]
    assert client.post(f"/goals/{goal_id}/accept").json()["status"] == "accepted"

    assert [g["id"] for g in client.get("/goals?status=accepted").json()] == [goal_id]       # the dashboard's call
    assert "GOAL_DEBT_PAYOFF" not in categories(client.get("/budget").json())                # november is untouched
    assert line(client.get("/budget").json(), "FREE")["limit_cents"] == free_november

    set_today(date(2026, 12, 1))
    client.post("/budget/run", json={"date": "2026-12-01"})                                  # closes november
    december = client.get("/budget").json()
    goal_line = line(december, "GOAL_DEBT_PAYOFF")
    assert (goal_line["name"], goal_line["limit_cents"], goal_line["group"], goal_line["flagged"]) == \
        ("Debt payoff", 58300, "goal", False)

def test_the_paused_run_finishes_and_a_lost_checkpoint_changes_nothing(api): ...
def test_a_regenerated_proposal_fits_around_the_accepted_goal(api):      # cap 19440 from 77740 - 58300
def test_a_simulated_user_sees_the_line_without_patching_the_clock(api):
    # sim prefs on the user, advance_days(db, uid, 29, model=..., checkpointer=ck) to 2026-12-01, _today unpatched:
    # GET /budget has the goal line, because it follows sim_today
```

- [ ] **Step 2: Run them, and fix what they find**

Run: `../venv/bin/python -m pytest tests/test_goal_loop.py -q`
Expected: PASS once any integration gaps are fixed. A failure here means an earlier task's seam is wrong; fix it there and add the missing test.

- [ ] **Step 3: Live check**

With Ollama and `qwen2.5:14b` running, generate through the API on a disposable database and accept the first goal. Record the proposal, the cap and the resulting December line in `docs/architecture.md`, as TPC-23 did. Without Ollama, say explicitly that the live check was skipped.

- [ ] **Step 4: Update the docs**

- `architecture.md`: section 4's Goal Agent row, the `goals` data model (`start_date`, `completed`), section 6a.5 (the pause, approval, goal lines, double counting, the known limit), the API table, and decision-log rows for each spec decision.
- `README.md`: the Goals paragraph now leads with the page.
- `agent-pipelines.md`, `system-walkthrough.md` and the three `.puml` files: goals are no longer "planned". Re-render the diagrams only if the existing PNG and SVG files are produced by a tool available here; otherwise say so in the handoff.
- `docs/tests/`: confirm every new and changed test has its entry.

- [ ] **Step 5: Write the handoff**

`docs/handoffs/2026-11-02-tpc-25-manual-verification.md`, in the format of the TPC-24 handoff: a disposable database, then `scripts/seed_budget_user.py --profile goals-card-24 --replace --start 2026-11-02` so the user is simulated. A plain `--replace` seed won't work: `GET /budget` follows `sim_today` or the wall clock, so a December run on a non-simulated user leaves the dashboard saying "No budget yet". Then generate, accept, dismiss and stop on `/goals`, check the dashboard, advance with `scripts/simulate.py --user budget-goals-card-24 --days 29` to reach 2026-12-01, and check the budget's goal line. This profile's history ends on 2026-10-31, so advancing adds no transactions and flexible lines shrink through smoothing; say so, so it doesn't read as a bug. Include what each step should show, the 409 cases to try, cleanup, and the limits: no contribution tracking, the understated cap, and the debt goal's projected end date.

- [ ] **Step 6: Run everything**

Run: `../venv/bin/python -m pytest -q -k 'not live and not sandbox'`, then from `frontend/`: `npm test && npm run lint && npm run build`.
Expected: all pass. Record the counts in the handoff.

- [ ] **Step 7: Commit**

```bash
git add backend docs
git commit -m "docs(TPC-25): the done-when end to end, docs and manual verification"
```
