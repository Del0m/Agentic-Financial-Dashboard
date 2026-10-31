from datetime import date, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app import config
from app.budget_engine.pace import BudgetLine as EngineLine, evaluate
from app.budget_service.alerts import decide_alert
from app.budget_service.initialize import load_txns
from app.budget_service.simulate import advance_days
from app.db.models import AdviceLog, Alert, BudgetPeriod
from eval.budget_users import PLANTED, default_start, seed_profile
from tests.fakes import SchemaReplyModel

GYM = "PERSONAL_CARE"
OCT, NOV = date(2026, 10, 1), date(2026, 11, 1)


@pytest.fixture(autouse=True)
def audit_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", tmp_path / "audit.jsonl")


def period(s, uid, start):
    return s.query(BudgetPeriod).filter_by(user_id=uid, start_date=start).one()


# the oracle: the first october day the engine itself flags the gym's category, from that day's transactions
def first_flagged_day(db, uid) -> date:
    with db() as s:
        lines = [EngineLine(ln.category, ln.kind, ln.limit_cents, ln.group) for ln in period(s, uid, OCT).lines]
        day = OCT
        while day.month == 10:
            if any(r.flagged for r in evaluate(lines, load_txns(s, uid, OCT, day), day) if r.category == GYM):
                return day
            day += timedelta(days=1)
    raise AssertionError("the planted overspend never crossed 10%")


# the ticket's done-when (TPC-22, M3): a simulated month with a planted overspend alerts within one run of crossing
# 10%, with the right cause, and next month's budget adapts
def test_m3_demo(db):
    profile = next(p for p in PLANTED if p.id == "overspend-recurring")
    with db() as s:
        uid = seed_profile(s, profile, until=default_start(profile))
    model, ck, days = SchemaReplyModel(cause="recurring", first_action="adjust_next_budget"), InMemorySaver(), []
    advance_days(db, uid, 31, model=model, checkpointer=ck, on_day=days.append)
    run_day = {d["run_id"]: date.fromisoformat(d["date"]) for d in days}

    with db() as s:
        alert = s.query(Alert).filter_by(user_id=uid, category=GYM).order_by(Alert.id).first()
        advice = s.query(AdviceLog).filter_by(alert_id=alert.id).one()
        october = {ln.category: ln for ln in period(s, uid, OCT).lines}
        oct_actuals = {ln.category: ln.spent_cents for ln in october.values()}
    assert run_day[advice.run_id] <= first_flagged_day(db, uid) + timedelta(days=1)
    assert (alert.cause, alert.action) == ("recurring", "adjust_next_budget")

    decide_alert(db, uid, alert.id, "accepted", checkpointer=ck, model=model)
    advance_days(db, uid, 1, model=model, checkpointer=ck)

    with db() as s:
        november = {ln.category: ln for ln in period(s, uid, NOV).lines}
        total = period(s, uid, NOV).total_cents
    assert (november[GYM].limit_cents, november[GYM].source) == (alert.proposed_limit_cents, "user")
    for cat, ln in november.items():
        old = october.get(cat)
        if ln.group in ("needs", "wants") and ln.kind == "flexible" and cat != GYM:
            assert abs(ln.limit_cents - old.limit_cents) <= old.limit_cents * 0.15
        elif ln.kind == "fixed" and old is not None:
            assert ln.limit_cents == oct_actuals[cat]
    others = sum(ln.limit_cents for c, ln in november.items() if c != "FREE")
    assert november["FREE"].limit_cents == max(total - others, 0)
