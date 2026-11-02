import json
from datetime import date, datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage

from app import config
from app.agents import goal_graph
from app.agents.budget_graph import close_interrupted_runs
from app.agents.goal_graph import main, run_goals
from app.budget_engine.goals import SAVINGS_MONTHS
from app.budget_engine.payoff import simulate
from app.budget_service.daily import running_elsewhere, user_lock
from app.budget_service.debts import load_debts
from app.budget_service.goal_prefs import set_goal_prefs
from app.db.models import Account, AgentRun, BudgetLine, BudgetPeriod, Goal, User
from eval.budget_users import GOAL_USERS, seed_profile
from tests.factories import add_account, add_txn, add_user
from tests.fakes import ScriptedChatModel, ollama_has

PROFILE = GOAL_USERS[0]
TODAY = PROFILE.today
START = date(2026, 12, 1)
UID = "budget-goals-card-24"


@pytest.fixture(autouse=True)
def audit_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", tmp_path / "audit.jsonl")


@pytest.fixture
def user(db):
    with db() as s:
        seed_profile(s, PROFILE)
    return db


def g(kind, cents, strategy=None, why="Because it fits."):
    return {"kind": kind, "monthly_dollars": cents / 100, "strategy": strategy, "rationale": why}


def model(*goals):
    return ScriptedChatModel(replies=[AIMessage(content=json.dumps({"goals": list(goals)}))])


def stored(db, status="proposed"):
    with db() as s:
        return s.query(Goal).filter_by(user_id=UID, status=status).order_by(Goal.priority).all()


def run_row(db, run_id):
    with db() as s:
        return s.get(AgentRun, run_id)


def set_prefs(db, raw):
    with db() as s:
        set_goal_prefs(s, UID, raw)
        s.commit()


def test_valid_proposal_stored_from_code(user):
    state = run_goals(user, UID, TODAY, model=model(g("debt_payoff", 20000, "avalanche", "Card first."),
                                                    g("savings", 10000)))
    assert state["status"] == "ok"
    debt, saving = stored(user)
    with user() as s:
        want = simulate(load_debts(s, UID), 20000, "avalanche", start=START)
    assert (debt.kind, debt.priority, debt.monthly_cents, debt.strategy, debt.rationale) == \
        ("debt_payoff", 1, 20000, "avalanche", "Card first.")
    assert (debt.target_cents, debt.target_date) == (2160000, want.debt_free)
    assert (saving.priority, saving.target_cents, saving.target_date) == (2, 10000 * SAVINGS_MONTHS, date(2027, 11, 1))
    run = run_row(user, state["run_id"])
    assert (run.graph, run.status) == ("goals", "ok") and run.finished_at is not None
    assert run.summary_json["goal_ids"] == [debt.id, saving.id] == state["goal_ids"]
    assert run.summary_json["surplus_cents"] == state["surplus_cents"] == 97175
    assert run.summary_json["cap_cents"] == state["cap_cents"] == 77740
    assert debt.run_id == saving.run_id == state["run_id"]


def test_second_run_supersedes_the_first(user):
    run_goals(user, UID, TODAY, model=model(g("savings", 10000)))
    run_goals(user, UID, TODAY, model=model(g("savings", 20000)))
    assert [x.monthly_cents for x in stored(user)] == [20000]
    assert [x.monthly_cents for x in stored(user, "superseded")] == [10000]


# nothing left to allocate: the model mustn't be asked to invent room
def test_no_surplus_never_calls_the_model(db):
    with db() as s:
        acct = add_account(s, add_user(s, "u1", "u1@x"), "Checking")
        for m in (8, 9, 10):
            add_txn(s, acct, -100000, "INCOME", day=date(2026, m, 1))
            add_txn(s, acct, 200000, "FOOD_AND_DRINK", day=date(2026, m, 2))
        s.commit()
    fake = ScriptedChatModel(replies=[])
    state = run_goals(db, "u1", TODAY, model=fake)
    assert (state["status"], state["goal_ids"], fake.seen) == ("no_surplus", [], [])
    assert state["surplus_cents"] == -100000
    assert run_row(db, state["run_id"]).status == "ok"


def test_no_history_is_no_surplus(db):
    with db() as s:
        add_user(s, "u1", "u1@x")
        s.commit()
    fake = ScriptedChatModel(replies=[])
    state = run_goals(db, "u1", TODAY, model=fake)
    assert (state["status"], state["surplus_cents"], fake.seen) == ("no_surplus", None, [])


def test_nothing_offered(user):
    set_prefs(user, {"skip_kinds": ["debt_payoff", "emergency_fund", "savings"]})
    fake = ScriptedChatModel(replies=[])
    state = run_goals(user, UID, TODAY, model=fake)
    assert (state["status"], fake.seen) == ("nothing_offered", [])


def test_unusable_goals_dropped_and_listed(user):
    set_prefs(user, {"skip_kinds": ["savings"]})
    state = run_goals(user, UID, TODAY, model=model(g("savings", 5000), g("emergency_fund", 30000)))
    assert state["status"] == "ok"
    assert [(x.kind, x.priority) for x in stored(user)] == [("emergency_fund", 1)]
    assert state["dropped"] == [{"kind": "savings", "reason": "not_offered"}]
    assert run_row(user, state["run_id"]).summary_json["dropped"] == state["dropped"]


# live, qwen2.5:14b left the strategy out of 1 run in 25. dropping the goal put the emergency fund first; the prompt's
# default applies instead
def test_debt_goal_without_a_strategy_is_avalanche(user):
    state = run_goals(user, UID, TODAY, model=model(g("debt_payoff", 20000), g("savings", 5000)))
    assert state["dropped"] == []
    debt = stored(user)[0]
    with user() as s:
        want = simulate(load_debts(s, UID), 20000, "avalanche", start=START)
    assert (debt.kind, debt.priority, debt.strategy, debt.target_date) == \
        ("debt_payoff", 1, "avalanche", want.debt_free)


def test_repeated_kind_dropped(user):
    state = run_goals(user, UID, TODAY, model=model(g("savings", 5000), g("savings", 9000)))
    assert [x.monthly_cents for x in stored(user)] == [5000]
    assert state["dropped"] == [{"kind": "savings", "reason": "repeat"}]


def test_nothing_usable_is_an_error(user):
    run_goals(user, UID, TODAY, model=model(g("savings", 5000)))
    set_prefs(user, {"skip_kinds": ["savings"]})
    state = run_goals(user, UID, TODAY, model=model(g("savings", 9000)))
    assert (state["status"], state["reason"]) == ("error", "no_usable_goals")
    assert run_row(user, state["run_id"]).status == "error"
    assert [x.monthly_cents for x in stored(user)] == [5000]        # the earlier proposal stands


def test_bad_json_twice_is_an_error(user):
    fake = ScriptedChatModel(replies=[AIMessage(content="not json"), AIMessage(content="still not")])
    state = run_goals(user, UID, TODAY, model=fake)
    assert (state["status"], state["reason"]) == ("error", "bad_model_output")
    assert len(fake.seen) == 2 and stored(user) == []
    assert run_row(user, state["run_id"]).status == "error"


def test_model_outage_recorded(user, caplog):
    fake = ScriptedChatModel(replies=[ConnectionError("ollama is down")])
    state = run_goals(user, UID, TODAY, model=fake)
    assert (state["status"], state["reason"]) == ("error", "ConnectionError")
    assert len(fake.seen) == 1 and "ollama is down" in caplog.text
    run = run_row(user, state["run_id"])
    assert run.status == "error" and run.summary_json["reason"] == "ConnectionError"
    assert stored(user) == []


def test_prompt_gets_the_gateway_context(user, tmp_path):
    fake = model(g("savings", 5000))
    run_goals(user, UID, TODAY, model=fake)
    system, human = fake.seen[0]
    context = json.loads(human.content.split("\n", 1)[1])
    assert context["purpose"] == "goals" and context["data"]["cap"] == 777.4
    assert "debt_payoff" in context["data"]["offered_kinds"] and "8%" in system.content
    lines = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [line["purpose"] for line in lines] == ["goals"]


# plan review focus 1: a linked user who never ran a budget still gets goals
def test_no_budget_uses_default_needs(user):
    with user() as s:
        assert s.query(BudgetPeriod).filter_by(user_id=UID).count() == 0
    state = run_goals(user, UID, TODAY, model=model(g("emergency_fund", 30000)))
    assert state["status"] == "ok" and state["gap_cents"] > 0


def test_open_period_needs_lines_used(user):
    without = run_goals(user, UID, TODAY, model=model(g("savings", 5000)))["gap_cents"]
    with user() as s:
        p = BudgetPeriod(user_id=UID, start_date=date(2026, 11, 1), end_date=date(2026, 11, 30))
        p.lines = [BudgetLine(category="ENTERTAINMENT", name="Fun", group="needs", kind="flexible", limit_cents=2000,
                              source="init")]
        s.add(p)
        s.commit()
    with_line = run_goals(user, UID, TODAY, model=model(g("savings", 5000)))["gap_cents"]
    # only entertainment counts now: 3 × the $15.99 streaming bill, less than $1,000 saved, so nothing's missing
    assert without > 0 and with_line == 0


# plan review focus 5: a savings account with no balance on file counts as $0
def test_none_savings_balance_counts_zero(user):
    with_savings = run_goals(user, UID, TODAY, model=model(g("savings", 5000)))["gap_cents"]
    with user() as s:
        s.query(Account).filter_by(user_id=UID, subtype="savings").one().current_balance_cents = None
        s.commit()
    without = run_goals(user, UID, TODAY, model=model(g("savings", 5000)))["gap_cents"]
    assert without == with_savings + 100000


# plan review focus 3: the model picks the amount, so a plan that never ends can happen
def test_never_goal_stored_without_a_date(db):
    with db() as s:
        item = add_user(s, "u1", "u1@x")
        acct = add_account(s, item, "Checking")
        add_account(s, item, "Card", type_="credit", balance=1000000,
                    liability={"kind": "credit", "apr": 30.0, "min_payment_cents": 10000})
        for m in (8, 9, 10):
            add_txn(s, acct, -100000, "INCOME", day=date(2026, m, 1))
        s.commit()
    state = run_goals(db, "u1", TODAY, model=model(g("debt_payoff", 100, "avalanche")))
    assert state["status"] == "ok"
    with db() as s:
        [goal] = s.query(Goal).filter_by(user_id="u1", status="proposed").all()
    assert goal.target_date is None and goal.target_cents == 1000000


# plan review focus 4: a budget run holds the user's lock, so a goal run waits its turn
def test_busy_when_the_lock_is_held(user):
    lock = user_lock(UID)
    lock.acquire()
    try:
        state = run_goals(user, UID, TODAY, model=ScriptedChatModel(replies=[]))
    finally:
        lock.release()
    assert state["status"] == "busy"
    with user() as s:
        assert s.query(AgentRun).filter_by(user_id=UID, graph="goals").count() == 0


def test_lock_released_after_a_run(user):
    run_goals(user, UID, TODAY, model=model(g("savings", 5000)))
    assert not user_lock(UID).locked()


# --- the cli ---

def cli(db, *args, fake=None):
    return main(["--user", UID, "--date", TODAY.isoformat(), *args], session_factory=db, model=fake)


def test_cli_prints_goals(user, capsys):
    code = cli(user, fake=model(g("debt_payoff", 20000, "avalanche", "Card first."), g("savings", 10000)))
    out = capsys.readouterr().out
    assert code == 0
    assert "$971.75" in out and "$95.00 of card minimums" in out and "$777.40" in out
    assert "debt_payoff" in out and "$200.00" in out and "$21,600.00" in out and "avalanche" in out
    assert "Card first." in out and "2027-11" in out and "over cap" not in out


def test_cli_marks_goals_over_the_cap(user, capsys):
    cli(user, fake=model(g("debt_payoff", 70000, "avalanche"), g("savings", 10000)))
    lines = capsys.readouterr().out.splitlines()
    assert "over cap" not in next(line for line in lines if "debt_payoff" in line)
    assert "over cap" in next(line for line in lines if "savings" in line)


def test_cli_says_when_a_goal_has_no_end(db, capsys):
    with db() as s:
        item = add_user(s, UID, "u1@x")
        acct = add_account(s, item, "Checking")
        add_account(s, item, "Card", type_="credit", balance=1000000,
                    liability={"kind": "credit", "apr": 30.0, "min_payment_cents": 10000})
        for m in (8, 9, 10):
            add_txn(s, acct, -100000, "INCOME", day=date(2026, m, 1))
        s.commit()
    assert cli(db, fake=model(g("debt_payoff", 100, "avalanche"))) == 0
    assert "no end date" in capsys.readouterr().out


# plan review focus 2: a simulated user's history stops at their simulated date, not the real one
def test_cli_defaults_to_the_simulated_date(user, monkeypatch):
    with user() as s:
        u = s.get(User, UID)
        u.prefs_json = {"sim": {"profile": PROFILE.id, "date": "2026-10-15"}}
        s.commit()
    seen = {}

    def fake_run(session_factory, user_id, today, *, model=None, audit_path=None):
        seen["today"] = today
        return {"status": "no_surplus", "surplus_cents": None}

    monkeypatch.setattr(goal_graph, "run_goals", fake_run)
    assert main(["--user", UID], session_factory=user, model=ScriptedChatModel(replies=[])) == 0
    assert seen["today"] == date(2026, 10, 15)


@pytest.mark.parametrize("prefs", ['{"max_share": 2}', "not json"])
def test_cli_bad_prefs_exit_1(user, capsys, prefs):
    assert cli(user, "--prefs", prefs, fake=ScriptedChatModel(replies=[])) == 1
    assert "prefs" in capsys.readouterr().out.lower()
    with user() as s:
        assert s.query(AgentRun).filter_by(user_id=UID, graph="goals").count() == 0


def test_cli_good_prefs_stored(user):
    assert cli(user, "--prefs", '{"skip_kinds": ["savings"]}', fake=model(g("emergency_fund", 30000))) == 0
    with user() as s:
        assert s.get(User, UID).prefs_json["goals"] == {"skip_kinds": ["savings"]}


def test_cli_error_exit_1(user, capsys):
    fake = ScriptedChatModel(replies=[AIMessage(content="no"), AIMessage(content="no")])
    assert cli(user, fake=fake) == 1
    assert "bad_model_output" in capsys.readouterr().out


def test_cli_busy_exit_1(user, capsys):
    lock = user_lock(UID)
    lock.acquire()
    try:
        assert cli(user, fake=ScriptedChatModel(replies=[])) == 1
    finally:
        lock.release()
    assert "in progress" in capsys.readouterr().out


# the ticket's done-when: a user with a 24% card gets a debt-payoff goal first. the total against the cap is printed,
# not asserted: tpc-24 enforces it
@pytest.mark.skipif(not ollama_has(config.OLLAMA_MODEL), reason="needs ollama running with OLLAMA_MODEL pulled")
def test_live_card_debt_goal_first(user):
    state = run_goals(user, UID, TODAY)
    assert state["status"] == "ok", state
    goals = stored(user)
    print(f"\ncap {state['cap_cents']}, total {sum(x.monthly_cents for x in goals)}, dropped {state['dropped']}")
    for x in goals:
        print(x.priority, x.kind, x.strategy, x.monthly_cents, x.target_cents, x.target_date, "|", x.rationale)
    assert goals[0].kind == "debt_payoff" and goals[0].strategy in ("avalanche", "snowball")


# --- final review ---

def red_user(db):
    with db() as s:
        acct = add_account(s, add_user(s, UID, "u1@x"), "Checking")
        for m in (8, 9, 10):
            add_txn(s, acct, -100000, "INCOME", day=date(2026, m, 1))
            add_txn(s, acct, 200000, "GENERAL_MERCHANDISE", day=date(2026, m, 2))
        s.add(Goal(user_id=UID, kind="savings", priority=1, monthly_cents=5000, target_cents=60000, rationale="x"))
        s.commit()


# a completed "nothing to propose" means the last proposal no longer fits; GET /goals must stop showing it
def test_no_surplus_withdraws_the_last_proposal(db, capsys):
    red_user(db)
    assert main(["--user", UID, "--date", TODAY.isoformat()], session_factory=db,
                model=ScriptedChatModel(replies=[])) == 0
    assert stored(db) == [] and len(stored(db, "superseded")) == 1
    assert "withdrawn" in capsys.readouterr().out


def test_nothing_offered_withdraws_the_last_proposal(user):
    run_goals(user, UID, TODAY, model=model(g("savings", 5000)))
    set_prefs(user, {"skip_kinds": ["debt_payoff", "emergency_fund", "savings"]})
    state = run_goals(user, UID, TODAY, model=ScriptedChatModel(replies=[]))
    assert (state["status"], state["withdrawn"]) == ("nothing_offered", 1)
    assert stored(user) == [] and len(stored(user, "superseded")) == 1


# the window has to reach past the months it measures, or its first transaction looks like the start of the history
def test_long_history_keeps_all_three_months(db):
    with db() as s:
        acct = add_account(s, add_user(s, UID, "u1@x"), "Checking")
        for m in (5, 6, 7):
            add_txn(s, acct, -100000, "INCOME", day=date(2026, m, 15))
        for m in (8, 9, 10):
            add_txn(s, acct, -300000, "INCOME", day=date(2026, m, 5))
        add_txn(s, acct, 290000, "GENERAL_MERCHANDISE", day=date(2026, 9, 6))
        s.commit()
    state = run_goals(db, UID, TODAY, model=model(g("savings", 5000)))
    assert state["surplus_cents"] == 300000      # $3,000, $100, $3,000; without August it was $1,550


@pytest.mark.parametrize("dollars", [0.001, 0.5, 1e16])
def test_unusable_amount_gets_a_retry(user, dollars):
    bad = {"goals": [{**g("emergency_fund", 0), "monthly_dollars": dollars}]}
    good = {"goals": [g("emergency_fund", 50000)]}
    fake = ScriptedChatModel(replies=[AIMessage(content=json.dumps(bad)), AIMessage(content=json.dumps(good))])
    state = run_goals(user, UID, TODAY, model=fake)
    assert state["status"] == "ok" and len(fake.seen) == 2
    assert [x.monthly_cents for x in stored(user)] == [50000]


def test_running_budget_elsewhere_is_busy(user):
    with user() as s:
        s.add(AgentRun(user_id=UID, graph="budget", status="running"))
        s.commit()
    fake = model(g("savings", 5000))
    state = run_goals(user, UID, TODAY, model=fake)
    assert (state["status"], fake.seen) == ("busy", [])
    assert stored(user) == []


def test_stale_running_row_does_not_block(user):
    with user() as s:
        s.add(AgentRun(user_id=UID, graph="budget", status="running",
                       started_at=datetime.now(timezone.utc) - timedelta(hours=3)))
        s.commit()
    assert run_goals(user, UID, TODAY, model=model(g("savings", 5000)))["status"] == "ok"


def test_a_goal_run_counts_as_running_elsewhere(user):
    with user() as s:
        s.add(AgentRun(user_id=UID, graph="goals", status="running"))
        s.commit()
    assert running_elsewhere(user, UID)


def test_interrupted_run_closes_its_row(user, monkeypatch):
    def stop(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(goal_graph, "invoke_structured", stop)
    with pytest.raises(KeyboardInterrupt):
        run_goals(user, UID, TODAY, model=ScriptedChatModel(replies=[]))
    with user() as s:
        run = s.query(AgentRun).filter_by(user_id=UID, graph="goals").one()
        assert (run.status, run.summary_json["reason"]) == ("error", "Interrupted")
    assert not user_lock(UID).locked()


def test_startup_closes_a_dead_goal_run(user):
    with user() as s:
        s.add(AgentRun(user_id=UID, graph="goals", status="running"))
        s.commit()
    assert close_interrupted_runs(user) == 1


@pytest.mark.parametrize("prefs", ["[]", '""', "5"])
def test_cli_refuses_prefs_that_are_not_an_object(user, capsys, prefs):
    assert main(["--user", UID, "--date", TODAY.isoformat(), "--prefs", prefs], session_factory=user,
                model=ScriptedChatModel(replies=[])) == 1
    assert "bad --prefs" in capsys.readouterr().out


def test_cli_unknown_user(db, capsys):
    assert main(["--user", "nobody", "--date", TODAY.isoformat()], session_factory=db,
                model=ScriptedChatModel(replies=[])) == 1
    assert "no user" in capsys.readouterr().out.lower()


def card_user(db, spend, minimum=9500):
    with db() as s:
        item = add_user(s, UID, "u1@x")
        acct = add_account(s, item, "Checking")
        add_account(s, item, "Card", type_="credit", balance=320000,
                    liability={"kind": "credit", "apr": 24.99, "min_payment_cents": minimum})
        for m in (8, 9, 10):
            add_txn(s, acct, -300000, "INCOME", day=date(2026, m, 1))
            add_txn(s, acct, spend, "GENERAL_MERCHANDISE", day=date(2026, m, 2))
            add_txn(s, acct, minimum, "LOAN_PAYMENTS", day=date(2026, m, 3),
                    detailed="LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
        s.commit()


# final review: goals are on top of the minimums, and a card's isn't in spending. $300 measured left $205 in hand
def test_card_minimums_come_out_of_the_surplus(db, capsys):
    card_user(db, 270000)
    assert main(["--user", UID, "--date", TODAY.isoformat()], session_factory=db, model=model(g("savings", 5000))) == 0
    assert "surplus $205.00 a month after $95.00 of card minimums; goals may use $164.00" in capsys.readouterr().out
    with db() as s:
        summary = s.query(AgentRun).filter_by(user_id=UID, graph="goals").one().summary_json
    assert (summary["surplus_cents"], summary["cap_cents"], summary["card_minimums_cents"]) == (20500, 16400, 9500)


def test_card_minimums_can_leave_no_surplus(db):
    card_user(db, 295000)      # $50 measured, a $95 minimum
    fake = ScriptedChatModel(replies=[])
    state = run_goals(db, UID, TODAY, model=fake)
    assert (state["status"], state["surplus_cents"], fake.seen) == ("no_surplus", -4500, [])
