from datetime import date

import pytest

from app.budget_engine.groups import default_group, default_name
from app.budget_service import close as close_mod
from app.budget_service.alerts import AlertAlreadyDecided, decide_alert
from app.budget_service.close import close_due
from app.budget_service.goal_lines import committed_goals, expire_goal_proposals, stale_proposals
from app.budget_service.settings import set_user_style
from app.db.models import Account, AdviceLog, AgentRun, Alert, BudgetLine, BudgetPeriod, Goal
from tests.factories import add_account, add_txn, add_user

OCT, NOV1 = date(2026, 10, 1), date(2026, 11, 1)
LINES = (("RENT_AND_UTILITIES", "fixed", 150000), ("FOOD_AND_DRINK", "flexible", 60000),
         ("ENTERTAINMENT", "flexible", 20000), ("SAVINGS_TRANSFER", "fixed", 50000), ("FREE", "flexible", 140000))


def month_txns(s, acct, month, year=2026, rent=150000, food=70000, fun=10000, travel=5000, saved=50000):
    day = lambda d: date(year, month, d)
    add_txn(s, acct, rent, "RENT_AND_UTILITIES", merchant="Landlord", day=day(1))
    add_txn(s, acct, food, "FOOD_AND_DRINK", merchant="Grocer", day=day(10))
    add_txn(s, acct, fun, "ENTERTAINMENT", merchant="Cinema", day=day(12))
    if travel:
        add_txn(s, acct, travel, "TRAVEL", merchant="Airline", day=day(14))
    if saved:
        add_txn(s, acct, saved, "TRANSFER_OUT", merchant="Savings", day=day(16), detailed="TRANSFER_OUT_SAVINGS")


# u1 with an open period for `start`'s month, paychecks from July on and that month's spending
def setup(db, lines=LINES, start=OCT, style=None, **spend):
    with db() as s:
        acct = add_account(s, add_user(s, "u1", "u1@x"), "Checking")
        end = date(start.year, start.month, 31) if start.month in (10, 12) else date(start.year, start.month, 30)
        p = BudgetPeriod(user_id="u1", start_date=start, end_date=end, income_cents=420000, total_cents=420000)
        p.lines = [BudgetLine(category=c, name=default_name(c), group=default_group(c), kind=k, limit_cents=cents,
                              source="init") for c, k, cents in lines]
        s.add(p)
        for m in range(7, 13):
            add_txn(s, acct, -420000, "INCOME", merchant="Employer", day=date(2026, m, 15))
        add_txn(s, acct, -420000, "INCOME", merchant="Employer", day=date(2027, 1, 15))
        month_txns(s, acct, start.month, start.year, **spend)
        if style:
            set_user_style(s, "u1", style)
        s.commit()
        return p.id, acct.id


def period_at(db, start):
    with db() as s:
        p = s.query(BudgetPeriod).filter_by(user_id="u1", start_date=start).one_or_none()
        if p is not None:
            p.lines            # load before the session closes
        return p


def lines_of(p):
    return {ln.category: ln for ln in p.lines}


def close_runs(db):
    with db() as s:
        return s.query(AgentRun).filter_by(graph="close").order_by(AgentRun.id).all()


def add_alert(db, pid, category, cents, status, run_status=None):
    with db() as s:
        alert = Alert(user_id="u1", period_id=pid, kind="overspend", category=category, severity="high",
                      cause="recurring", action="adjust_next_budget", proposed_limit_cents=cents,
                      message="Raise it.", status=status)
        s.add(alert)
        s.flush()
        run_id = None
        if run_status:
            run = AgentRun(user_id="u1", graph="budget", status=run_status, summary_json={"period_id": pid})
            s.add(run)
            s.flush()
            run_id = run.id
        s.add(AdviceLog(user_id="u1", run_id=run_id, alert_id=alert.id, category=category, advice_text="Raise it."))
        s.commit()
        return alert.id, run_id


def test_close_opens_adapted_month(db):
    pid, _ = setup(db)
    [summary] = close_due(db, "u1", NOV1)
    old, new = period_at(db, OCT), period_at(db, NOV1)
    assert old.status == "closed"
    assert (new.start_date, new.end_date, new.status, new.fit_json) == (NOV1, date(2026, 11, 30), "open", None)
    lines = lines_of(new)
    assert {c: ln.limit_cents for c, ln in lines.items()} == {
        "RENT_AND_UTILITIES": 150000, "FOOD_AND_DRINK": 63000, "ENTERTAINMENT": 17000, "SAVINGS_TRANSFER": 50000,
        "FREE": 140000}
    assert (new.income_cents, new.total_cents) == (420000, 420000)
    assert all((ln.name, ln.group) == (default_name(c), default_group(c)) for c, ln in lines.items())
    assert all(ln.source == "adapted" for ln in new.lines)
    assert lines["FOOD_AND_DRINK"].fit_json == {"old_cents": 60000, "actual_cents": 70000, "rule": "smoothed"}
    assert lines["FREE"].fit_json == {"old_cents": 140000, "actual_cents": 5000, "rule": "remainder"}
    [run] = close_runs(db)
    assert (run.status, {**run.summary_json, "run_id": run.id}) == ("ok", summary)
    assert summary["month"] == "2026-10-01" and summary["new_period_id"] == new.id
    assert (summary["income_cents"], summary["total_cents"], summary["free_cents"]) == (420000, 420000, 140000)
    assert (summary["style"], summary["unbudgeted"], summary["expired_alert_ids"], summary["alert_ids"]) == \
        ("caps", ["TRAVEL"], [], [])
    assert {ln["category"]: ln["rule"] for ln in summary["lines"]} == {
        "RENT_AND_UTILITIES": "fixed", "FOOD_AND_DRINK": "smoothed", "ENTERTAINMENT": "smoothed",
        "SAVINGS_TRANSFER": "fixed", "FREE": "remainder"}


def test_accepted_applied_latest_wins(db):
    pid, _ = setup(db)
    add_alert(db, pid, "FOOD_AND_DRINK", 80000, "accepted")
    add_alert(db, pid, "FOOD_AND_DRINK", 90000, "accepted")
    close_due(db, "u1", NOV1)
    food = lines_of(period_at(db, NOV1))["FOOD_AND_DRINK"]
    assert (food.limit_cents, food.source, food.fit_json["rule"]) == (90000, "user", "accepted")


def test_accepted_after_rename(db):
    pid, _ = setup(db)
    with db() as s:
        food = s.query(BudgetLine).filter_by(period_id=pid, category="FOOD_AND_DRINK").one()
        food.name, food.group = "Groceries", "wants"
        s.commit()
    add_alert(db, pid, "FOOD_AND_DRINK", 90000, "accepted")
    close_due(db, "u1", NOV1)
    food = lines_of(period_at(db, NOV1))["FOOD_AND_DRINK"]
    assert (food.name, food.group, food.limit_cents) == ("Groceries", "wants", 90000)


def test_pending_proposal_expires(db):
    pid, _ = setup(db)
    alert_id, run_id = add_alert(db, pid, "FOOD_AND_DRINK", 90000, "pending_user", run_status="awaiting_user")
    [summary] = close_due(db, "u1", NOV1)
    with db() as s:
        alert, run = s.get(Alert, alert_id), s.get(AgentRun, run_id)
        advice = s.query(AdviceLog).filter_by(alert_id=alert_id).one()
        assert (alert.status, advice.outcome) == ("dismissed", "ignored")
        assert (run.status, run.summary_json["expired_alert_ids"]) == ("expired", [alert_id])
        assert run.finished_at is not None
    assert summary["expired_alert_ids"] == [alert_id]
    assert lines_of(period_at(db, NOV1))["FOOD_AND_DRINK"].limit_cents == 63000      # not the proposal
    with pytest.raises(AlertAlreadyDecided):
        decide_alert(db, "u1", alert_id, "accepted")


def test_nothing_due_writes_nothing(db):
    setup(db)
    assert close_due(db, "u1", date(2026, 10, 31)) == []
    assert close_due(db, "nobody", NOV1) == []
    assert close_runs(db) == [] and period_at(db, NOV1) is None


def test_rerun_writes_nothing(db):
    setup(db)
    close_due(db, "u1", NOV1)
    assert close_due(db, "u1", NOV1) == []
    assert len(close_runs(db)) == 1


# another close opened november between this close's read and its commit: nothing is half-written
def test_lost_race_rolls_back(db, monkeypatch):
    setup(db)
    adapt = close_mod.adapt_month

    def racing(gathered):
        with db() as s:
            s.add(BudgetPeriod(user_id="u1", start_date=NOV1, end_date=date(2026, 11, 30)))
            s.commit()
        return adapt(gathered)

    monkeypatch.setattr(close_mod, "adapt_month", racing)
    assert close_due(db, "u1", NOV1) == []
    assert close_runs(db) == [] and period_at(db, OCT).status == "open"
    assert period_at(db, NOV1).lines == []


def test_two_missed_months(db):
    _, acct_id = setup(db)
    with db() as s:
        s.info["n"] = 1000       # add_txn numbers plaid ids per session; keep clear of setup's
        month_txns(s, s.get(Account, acct_id), 11, food=50000, fun=20000, travel=0)
        s.commit()
    first, second = close_due(db, "u1", date(2026, 12, 3))
    assert (first["month"], second["month"]) == ("2026-10-01", "2026-11-01")
    assert [r.summary_json["month"] for r in close_runs(db)] == ["2026-10-01", "2026-11-01"]
    assert period_at(db, NOV1).status == "closed"
    dec = period_at(db, date(2026, 12, 1))
    assert (dec.end_date, dec.status) == (date(2026, 12, 31), "open")
    # november's food from october's $630 and november's own $500: 0.7 x 630 + 0.3 x 500
    assert lines_of(dec)["FOOD_AND_DRINK"].limit_cents == 59100


def test_failure_rolls_back(db, monkeypatch):
    setup(db)

    def boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(close_mod, "close_period", boom)
    assert close_due(db, "u1", NOV1) == []
    [run] = close_runs(db)
    assert (run.status, run.summary_json) == ("error", {"month": "2026-10-01", "error": "RuntimeError"})
    assert period_at(db, OCT).status == "open" and period_at(db, NOV1) is None


# a budget from before tpc-22 picks up the model at its first close
def test_legacy_period_gains_free_and_saving(db):
    setup(db, lines=LINES[:2])
    close_due(db, "u1", NOV1)
    lines = lines_of(period_at(db, NOV1))
    saving, free = lines["SAVINGS_TRANSFER"], lines["FREE"]
    assert (saving.limit_cents, saving.group, saving.name, saving.fit_json["rule"]) == (50000, "saving", "Savings",
                                                                                         "fixed")
    assert (free.group, free.limit_cents) == ("free", 420000 - 150000 - 63000 - 50000)


def test_503020_close_alerts(db):
    setup(db, style="50_30_20", rent=180000)
    [summary] = close_due(db, "u1", NOV1)
    nov = period_at(db, NOV1)
    assert lines_of(nov)["SAVINGS_TRANSFER"].limit_cents == 84000
    assert lines_of(nov)["SAVINGS_TRANSFER"].fit_json["rule"] == "target"
    with db() as s:
        [alert] = s.query(Alert).all()
    # needs: rent $1,800 + food $630 = $2,430 of $4,200
    assert (alert.kind, alert.period_id, alert.action, alert.severity, alert.status) == (
        "group_over_target", nov.id, "alert", "medium", "pending_user")
    assert alert.message == "Needs are 58% of income, above the 50% target."
    assert summary["alert_ids"] == [alert.id] and summary["style"] == "50_30_20"


def test_over_income_alert(db):
    setup(db, rent=400000)
    [summary] = close_due(db, "u1", NOV1)
    with db() as s:
        [alert] = s.query(Alert).all()
    assert alert.kind == "over_income"
    assert alert.message == ("Your budget lines add up to $5,300.00, $1,100.00 more than your typical income of "
                             "$4,200.00.")
    assert (summary["free_cents"], summary["total_cents"]) == (0, 530000)


# a month is judged against the budget it started with; the new style starts with the next one
def test_style_change_applies_at_close(db):
    pid, _ = setup(db)
    with db() as s:
        set_user_style(s, "u1", "50_30_20")
        s.commit()
    assert lines_of(period_at(db, OCT))["SAVINGS_TRANSFER"].limit_cents == 50000
    close_due(db, "u1", NOV1)
    assert lines_of(period_at(db, NOV1))["SAVINGS_TRANSFER"].limit_cents == 84000


def test_close_across_new_year(db):
    setup(db, start=date(2026, 12, 1))
    [summary] = close_due(db, "u1", date(2027, 1, 1))
    jan = period_at(db, date(2027, 1, 1))
    assert (jan.start_date, jan.end_date) == (date(2027, 1, 1), date(2027, 1, 31))
    assert summary["month"] == "2026-12-01"


# the user accepts while close is running (after it listed the pending proposals): their answer stands and is
# applied, instead of being overwritten as expired (tpc-22 review)
def test_decision_during_close_is_kept(db, monkeypatch):
    pid, _ = setup(db)
    alert_id, run_id = add_alert(db, pid, "FOOD_AND_DRINK", 90000, "pending_user", run_status="awaiting_user")
    gather = close_mod.gather_month

    def user_clicks(session, period_id):
        decide_alert(db, "u1", alert_id, "accepted")
        return gather(session, period_id)

    monkeypatch.setattr(close_mod, "gather_month", user_clicks)
    [summary] = close_due(db, "u1", NOV1)
    with db() as s:
        assert s.get(Alert, alert_id).status == "accepted"
        assert s.query(AdviceLog).filter_by(alert_id=alert_id).one().outcome == "accepted"
        assert s.get(AgentRun, run_id).status == "awaiting_user"
    assert summary["expired_alert_ids"] == []
    assert lines_of(period_at(db, NOV1))["FOOD_AND_DRINK"].limit_cents == 90000



def rent_paid_on(db, day, **spend):
    pid, acct_id = setup(db, **spend)
    with db() as s:
        add_txn(s, s.get(Account, acct_id), 150000, "RENT_AND_UTILITIES", merchant="Landlord", day=day)
        s.commit()
    [summary] = close_due(db, "u1", NOV1)
    rent = lines_of(period_at(db, NOV1))["RENT_AND_UTILITIES"]
    actual = next(ln for ln in summary["lines"] if ln["category"] == "RENT_AND_UTILITIES")["actual_cents"]
    return rent.limit_cents, rent.fit_json["actual_cents"], actual


# next month's rent paid in a month's last days is next month's (the tpc-17 review fix, kept through tpc-22's
# close): november doesn't adopt a doubled rent
def test_early_rent_not_adopted_doubled(db):
    assert rent_paid_on(db, date(2026, 10, 30)) == (150000, 150000, 150000)


# and october's rent paid on sep 30 still counts for october
def test_rent_paid_early_counts_for_its_month(db):
    assert rent_paid_on(db, date(2026, 9, 30), rent=0) == (150000, 150000, 150000)


# august's first transaction is the 15th paycheck, and july's history shows august is complete: income is the
# median of aug, sep and oct. without august it would be halfway between sep's $4,200 and oct's $6,200
def test_close_fits_income_on_all_three_months(db):
    _, acct_id = setup(db)
    with db() as s:
        add_txn(s, s.get(Account, acct_id), -200000, "INCOME", merchant="Employer", day=date(2026, 10, 20))
        s.commit()
    close_due(db, "u1", NOV1)
    assert period_at(db, NOV1).income_cents == 420000


# --- goals (tpc-25) ---

DEC1, JAN1 = date(2026, 12, 1), date(2027, 1, 1)
INCOME, RENT, LOAN, SAVED = 420000, 150000, 21000, 50000
FREE_NO_GOAL = INCOME - RENT - LOAN - SAVED     # $1,990.00
NOV_LINES = (("RENT_AND_UTILITIES", "fixed", RENT), ("LOAN_PAYMENTS", "fixed", LOAN),
             ("SAVINGS_TRANSFER", "fixed", SAVED), ("FREE", "flexible", FREE_NO_GOAL))
NO_LOAN = tuple(ln for ln in NOV_LINES if ln[0] != "LOAN_PAYMENTS")
NO_SAVING = tuple(ln for ln in NOV_LINES if ln[0] != "SAVINGS_TRANSFER")


# u1's open november, with $4,200 of income and only these three things spent
def november(db, lines=NOV_LINES, loan=LOAN, saved=SAVED, style=None):
    pid, acct_id = setup(db, lines=lines, start=NOV1, style=style, rent=RENT, food=0, fun=0, travel=0, saved=saved)
    with db() as s:
        if loan:
            add_txn(s, s.get(Account, acct_id), loan, "LOAN_PAYMENTS", merchant="Lender", day=date(2026, 11, 5),
                    detailed="LOAN_PAYMENTS_PERSONAL_LOAN_PAYMENT")
        s.commit()
    return pid, acct_id


def add_goal(db, kind="debt_payoff", cents=58300, status="accepted", start=DEC1, target=date(2027, 6, 1),
             run_status=None, priority=1):
    with db() as s:
        run_id = None
        if run_status:
            run = AgentRun(user_id="u1", graph="goals", status=run_status, summary_json={"outcome": "proposed"})
            s.add(run)
            s.flush()
            run_id = run.id
        goal = Goal(user_id="u1", run_id=run_id, kind=kind, priority=priority, monthly_cents=cents,
                    target_cents=cents * 6, target_date=target, start_date=start, rationale="Because.", status=status)
        s.add(goal)
        s.commit()
        return goal.id, run_id


def goal_status(db, goal_id):
    with db() as s:
        return s.get(Goal, goal_id).status


def set_goal_status(db, goal_id, status):
    with db() as s:
        s.get(Goal, goal_id).status = status
        s.commit()


def test_accepted_goal_becomes_a_line_in_the_next_period(db):
    november(db)
    add_goal(db)
    [summary] = close_due(db, "u1", DEC1)
    line = lines_of(period_at(db, DEC1))["GOAL_DEBT_PAYOFF"]
    assert (line.name, line.group, line.kind, line.limit_cents, line.source) == ("Debt payoff", "goal", "fixed", 58300,
                                                                                 "user")
    assert line.fit_json == {"old_cents": 0, "actual_cents": 0, "rule": "goal"}
    assert {"category": "GOAL_DEBT_PAYOFF", "name": "Debt payoff", "group": "goal", "old_cents": 0, "actual_cents": 0,
            "new_cents": 58300, "rule": "goal"} in summary["lines"]
    assert "GOAL_DEBT_PAYOFF" not in lines_of(period_at(db, NOV1))      # the month under way never changes


def test_goal_line_takes_its_amount_from_free_only(db):
    november(db)
    add_goal(db)
    [summary] = close_due(db, "u1", DEC1)
    dec = period_at(db, DEC1)
    assert {c: ln.limit_cents for c, ln in lines_of(dec).items()} == {
        "RENT_AND_UTILITIES": RENT, "LOAN_PAYMENTS": LOAN, "SAVINGS_TRANSFER": SAVED, "GOAL_DEBT_PAYOFF": 58300,
        "FREE": INCOME - RENT - LOAN - SAVED - 58300}
    assert lines_of(dec)["FREE"].limit_cents == 140700
    assert (dec.income_cents, dec.total_cents, summary["free_cents"], summary["alert_ids"]) == (INCOME, INCOME, 140700,
                                                                                                [])


def test_goal_over_free_keeps_other_lines_and_raises_over_income_alert(db):
    november(db)
    add_goal(db, cents=250000)         # free is only $1,990
    [summary] = close_due(db, "u1", DEC1)
    dec = period_at(db, DEC1)
    lines = lines_of(dec)
    assert {c: ln.limit_cents for c, ln in lines.items()} == {
        "RENT_AND_UTILITIES": RENT, "LOAN_PAYMENTS": LOAN, "SAVINGS_TRANSFER": SAVED, "GOAL_DEBT_PAYOFF": 250000,
        "FREE": 0}
    assert dec.total_cents == 471000
    with db() as s:
        [alert] = s.query(Alert).all()
    assert (alert.kind, alert.period_id, summary["alert_ids"]) == ("over_income", dec.id, [alert.id])
    assert alert.message == ("Your budget lines add up to $4,710.00, $510.00 more than your typical income of "
                             "$4,200.00.")


# in 50/30/20 the goal comes out of the 20%, and free and needs don't move
def test_503020_goal_line_comes_out_of_saving_target(db):
    november(db, style="50_30_20")
    add_goal(db)
    close_due(db, "u1", DEC1)
    lines = lines_of(period_at(db, DEC1))
    assert (lines["GOAL_DEBT_PAYOFF"].limit_cents, lines["SAVINGS_TRANSFER"].limit_cents) == (58300, 84000 - 58300)
    assert lines["FREE"].limit_cents == 126000          # the wants target, with no wants lines


def test_goal_starting_later_gets_no_line_yet(db):
    november(db)
    add_goal(db, start=JAN1)
    close_due(db, "u1", DEC1)
    dec = lines_of(period_at(db, DEC1))
    assert "GOAL_DEBT_PAYOFF" not in dec and dec["FREE"].limit_cents == FREE_NO_GOAL
    close_due(db, "u1", JAN1)
    jan = lines_of(period_at(db, JAN1))
    assert jan["GOAL_DEBT_PAYOFF"].limit_cents == 58300 and jan["FREE"].limit_cents == 140700


def test_goal_line_carries_user_rename(db):
    november(db)
    add_goal(db)
    close_due(db, "u1", DEC1)
    with db() as s:
        p = s.query(BudgetPeriod).filter_by(start_date=DEC1).one()
        s.query(BudgetLine).filter_by(period_id=p.id, category="GOAL_DEBT_PAYOFF").one().name = "Card payoff"
        s.commit()
    close_due(db, "u1", JAN1)
    line = lines_of(period_at(db, JAN1))["GOAL_DEBT_PAYOFF"]
    assert (line.name, line.limit_cents) == ("Card payoff", 58300)


def test_goal_line_never_smoothed_or_adopted(db):
    november(db)
    add_goal(db)
    assert len(close_due(db, "u1", JAN1)) == 2    # december's actual on the goal line is $0
    jan = lines_of(period_at(db, JAN1))
    assert jan["GOAL_DEBT_PAYOFF"].limit_cents == 58300
    assert jan["GOAL_DEBT_PAYOFF"].fit_json == {"old_cents": 58300, "actual_cents": 0, "rule": "goal"}
    assert jan["GOAL_DEBT_PAYOFF"].source == "user"
    assert jan["FREE"].limit_cents == 140700


def test_finished_goal_completes_and_frees_its_line(db):
    november(db)
    goal_id, _ = add_goal(db, target=DEC1)
    first, second = close_due(db, "u1", JAN1)
    assert lines_of(period_at(db, DEC1))["GOAL_DEBT_PAYOFF"].limit_cents == 58300      # its target month still has it
    assert first["finished_goal_ids"] == []
    jan = lines_of(period_at(db, JAN1))
    assert "GOAL_DEBT_PAYOFF" not in jan and jan["FREE"].limit_cents == FREE_NO_GOAL
    assert goal_status(db, goal_id) == "completed" and second["finished_goal_ids"] == [goal_id]
    with db() as s:
        assert committed_goals(s, "u1") == []    # the kind can be offered again


def test_stopped_goal_line_ends_at_close(db):
    november(db)
    goal_id, _ = add_goal(db)
    close_due(db, "u1", DEC1)
    set_goal_status(db, goal_id, "dismissed")
    [summary] = close_due(db, "u1", JAN1)
    assert lines_of(period_at(db, DEC1))["GOAL_DEBT_PAYOFF"].limit_cents == 58300
    jan = lines_of(period_at(db, JAN1))
    assert "GOAL_DEBT_PAYOFF" not in jan and jan["FREE"].limit_cents == FREE_NO_GOAL
    assert goal_status(db, goal_id) == "dismissed" and summary["finished_goal_ids"] == []


# the user stops a finished goal while close is running: their answer stands, and it isn't reported as completed
def test_stop_during_close_stands(db, monkeypatch):
    november(db)
    goal_id, _ = add_goal(db, target=DEC1)
    close_due(db, "u1", DEC1)
    gather = close_mod.gather_month

    def user_clicks(session, period_id):
        gathered = gather(session, period_id)
        set_goal_status(db, goal_id, "dismissed")
        return gathered

    monkeypatch.setattr(close_mod, "gather_month", user_clicks)
    [summary] = close_due(db, "u1", JAN1)
    assert goal_status(db, goal_id) == "dismissed" and summary["finished_goal_ids"] == []


def test_saving_line_ignores_goal_transfers(db):
    november(db, lines=(*NOV_LINES, ("GOAL_SAVINGS", "fixed", 19440)), saved=69440)
    add_goal(db, kind="savings", cents=19440, start=NOV1)
    close_due(db, "u1", DEC1)
    lines = lines_of(period_at(db, DEC1))
    assert lines["SAVINGS_TRANSFER"].limit_cents == 50000
    assert lines["SAVINGS_TRANSFER"].fit_json == {"old_cents": 50000, "actual_cents": 50000, "rule": "fixed"}
    assert lines["GOAL_SAVINGS"].limit_cents == 19440
    assert lines["FREE"].limit_cents == INCOME - RENT - LOAN - SAVED - 19440


# both saving goals are transfers to savings, so they're netted against the one saving line together
def test_saving_line_ignores_both_saving_goals(db):
    november(db, lines=(*NOV_LINES, ("GOAL_SAVINGS", "fixed", 19440), ("GOAL_EMERGENCY_FUND", "fixed", 10000)),
              saved=79440)
    close_due(db, "u1", DEC1)
    assert lines_of(period_at(db, DEC1))["SAVINGS_TRANSFER"].limit_cents == 50000


def test_goal_transfer_alone_adds_no_saving_line(db):
    november(db, lines=(*NO_SAVING, ("GOAL_SAVINGS", "fixed", 19440)), saved=19440)
    add_goal(db, kind="savings", cents=19440, start=NOV1)
    close_due(db, "u1", DEC1)
    assert "SAVINGS_TRANSFER" not in lines_of(period_at(db, DEC1))


# saving more than the goal, with no saving line: the rest is the user's own saving, and it gets a line
def test_saving_above_goal_adds_a_saving_line_for_the_rest(db):
    november(db, lines=(*NO_SAVING, ("GOAL_SAVINGS", "fixed", 19440)), saved=30000)
    close_due(db, "u1", DEC1)
    assert lines_of(period_at(db, DEC1))["SAVINGS_TRANSFER"].limit_cents == 10560


def test_loan_line_ignores_debt_goal(db):
    november(db, lines=(*NOV_LINES, ("GOAL_DEBT_PAYOFF", "fixed", 58300)), loan=79300)
    add_goal(db, start=NOV1)
    [summary] = close_due(db, "u1", DEC1)
    lines = lines_of(period_at(db, DEC1))
    assert lines["LOAN_PAYMENTS"].limit_cents == 21000
    assert lines["LOAN_PAYMENTS"].fit_json == {"old_cents": 21000, "actual_cents": 21000, "rule": "fixed"}
    assert lines["FREE"].limit_cents == 140700
    assert lines["FREE"].fit_json["actual_cents"] == 0 and summary["unbudgeted"] == []


def test_debt_goal_paid_to_a_card_leaves_the_loan_line(db):
    _, acct_id = november(db, lines=(*NOV_LINES, ("GOAL_DEBT_PAYOFF", "fixed", 20000)))
    with db() as s:
        add_txn(s, s.get(Account, acct_id), 20000, "LOAN_PAYMENTS", merchant="Card", day=date(2026, 11, 6),
                detailed="LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
        s.commit()
    close_due(db, "u1", DEC1)
    loan = lines_of(period_at(db, DEC1))["LOAN_PAYMENTS"]
    assert (loan.limit_cents, loan.fit_json["actual_cents"]) == (21000, 21000)


def test_goal_paid_to_an_unlined_loan_is_not_free_spending(db):
    november(db, lines=(*NO_LOAN, ("GOAL_DEBT_PAYOFF", "fixed", 58300)), loan=58300)
    [summary] = close_due(db, "u1", DEC1)
    lines = lines_of(period_at(db, DEC1))
    assert "LOAN_PAYMENTS" not in lines
    assert lines["FREE"].fit_json["actual_cents"] == 0 and summary["unbudgeted"] == []


# only the goal's share comes off: the rest of an unlined loan payment is still free spending
def test_unlined_loan_above_goal_stays_free_spending(db):
    november(db, lines=(*NO_LOAN, ("GOAL_DEBT_PAYOFF", "fixed", 58300)), loan=70000)
    [summary] = close_due(db, "u1", DEC1)
    assert lines_of(period_at(db, DEC1))["FREE"].fit_json["actual_cents"] == 11700
    assert summary["unbudgeted"] == ["LOAN_PAYMENTS"]


# what the closed month budgeted is what nets it: stopping the goal doesn't make its payment look like overspending
def test_stopped_goal_still_nets_its_closing_month(db):
    november(db, lines=(*NOV_LINES, ("GOAL_DEBT_PAYOFF", "fixed", 58300)), loan=79300)
    add_goal(db, status="dismissed", start=NOV1)
    close_due(db, "u1", DEC1)
    lines = lines_of(period_at(db, DEC1))
    assert lines["LOAN_PAYMENTS"].limit_cents == 21000 and "GOAL_DEBT_PAYOFF" not in lines


def test_undecided_proposal_expires_with_its_start_month(db):
    november(db)
    goal_id, run_id = add_goal(db, status="proposed", run_status="awaiting_user")
    [summary] = close_due(db, "u1", DEC1)
    assert goal_status(db, goal_id) == "superseded" and summary["expired_goal_ids"] == [goal_id]
    with db() as s:
        run = s.get(AgentRun, run_id)
        assert (run.status, run.summary_json) == ("expired", {"outcome": "proposed", "expired_goal_ids": [goal_id]})
        assert run.finished_at is not None
    assert "GOAL_DEBT_PAYOFF" not in lines_of(period_at(db, DEC1))


def test_later_proposal_survives_close(db):
    november(db)
    goal_id, run_id = add_goal(db, status="proposed", start=JAN1, run_status="awaiting_user")
    [summary] = close_due(db, "u1", DEC1)
    assert goal_status(db, goal_id) == "proposed" and summary["expired_goal_ids"] == []
    with db() as s:
        assert s.get(AgentRun, run_id).status == "awaiting_user"


def test_null_start_proposal_expires(db):
    november(db)
    goal_id, _ = add_goal(db, status="proposed", start=None)
    [summary] = close_due(db, "u1", DEC1)
    assert goal_status(db, goal_id) == "superseded" and summary["expired_goal_ids"] == [goal_id]


# the user accepts after close read the rows: the answer stands, and the line arrives a month later
def test_answer_during_close_stands(db, monkeypatch):
    november(db)
    goal_id, run_id = add_goal(db, status="proposed", run_status="awaiting_user")
    gather = close_mod.gather_month

    def user_clicks(session, period_id):
        gathered = gather(session, period_id)
        set_goal_status(db, goal_id, "accepted")
        return gathered

    monkeypatch.setattr(close_mod, "gather_month", user_clicks)
    [summary] = close_due(db, "u1", DEC1)
    assert goal_status(db, goal_id) == "accepted" and summary["expired_goal_ids"] == []
    with db() as s:
        assert s.get(AgentRun, run_id).status == "awaiting_user"
    assert "GOAL_DEBT_PAYOFF" not in lines_of(period_at(db, DEC1))
    monkeypatch.setattr(close_mod, "gather_month", gather)
    close_due(db, "u1", JAN1)
    assert lines_of(period_at(db, JAN1))["GOAL_DEBT_PAYOFF"].limit_cents == 58300


# one commit: another close winning the race leaves every goal and its run as they were
def test_lost_race_leaves_goals_alone(db, monkeypatch):
    november(db)
    finished_id, _ = add_goal(db, target=NOV1, start=NOV1)
    proposed_id, run_id = add_goal(db, kind="savings", status="proposed", run_status="awaiting_user")
    adapt = close_mod.adapt_month

    def racing(gathered):
        with db() as s:
            s.add(BudgetPeriod(user_id="u1", start_date=DEC1, end_date=date(2026, 12, 31)))
            s.commit()
        return adapt(gathered)

    monkeypatch.setattr(close_mod, "adapt_month", racing)
    assert close_due(db, "u1", DEC1) == []
    assert (goal_status(db, finished_id), goal_status(db, proposed_id)) == ("accepted", "proposed")
    with db() as s:
        assert s.get(AgentRun, run_id).status == "awaiting_user"


def test_committed_goals_are_accepted_by_priority_then_id(db):
    november(db)
    second, _ = add_goal(db, kind="savings", priority=2)
    third, _ = add_goal(db, kind="emergency_fund", priority=2)
    first, _ = add_goal(db, priority=1)
    add_goal(db, status="proposed")
    add_goal(db, status="dismissed")
    with db() as s:
        goals = committed_goals(s, "u1")
    assert [g.id for g in goals] == [first, second, third]
    assert (goals[0].kind, goals[0].monthly_cents, goals[0].start_date, goals[0].target_date) == (
        "debt_payoff", 58300, DEC1, date(2027, 6, 1))


def test_stale_proposals_are_due_or_undated_and_proposed(db):
    november(db)
    due, _ = add_goal(db, status="proposed")
    undated, _ = add_goal(db, status="proposed", start=None)
    add_goal(db, status="proposed", start=JAN1)
    add_goal(db)
    with db() as s:
        assert stale_proposals(s, "u1", DEC1) == [due, undated]
        assert stale_proposals(s, "nobody", DEC1) == []


# a row answered since the caller listed it keeps its answer, and only a paused goal run is touched
def test_expire_goal_proposals_rechecks_and_leaves_the_commit_to_the_caller(db):
    november(db)
    proposed, run_id = add_goal(db, status="proposed", run_status="awaiting_user")
    accepted, ok_run = add_goal(db, run_status="ok")
    with db() as s:
        assert expire_goal_proposals(s, []) == []
        assert expire_goal_proposals(s, [accepted, proposed]) == [proposed]
        s.rollback()
    assert (goal_status(db, proposed), goal_status(db, accepted)) == ("proposed", "accepted")
    with db() as s:
        expire_goal_proposals(s, [accepted, proposed])
        s.commit()
    assert (goal_status(db, proposed), goal_status(db, accepted)) == ("superseded", "accepted")
    with db() as s:
        assert (s.get(AgentRun, run_id).status, s.get(AgentRun, ok_run).status) == ("expired", "ok")
