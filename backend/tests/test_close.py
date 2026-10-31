from datetime import date

import pytest

from app.budget_engine.groups import default_group, default_name
from app.budget_service import close as close_mod
from app.budget_service.alerts import AlertAlreadyDecided, decide_alert
from app.budget_service.close import close_due
from app.budget_service.settings import set_user_style
from app.db.models import Account, AdviceLog, AgentRun, Alert, BudgetLine, BudgetPeriod
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
