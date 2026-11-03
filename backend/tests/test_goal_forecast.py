from dataclasses import replace
from datetime import date

import pytest

from app.agents import goal_graph
from app.agents.goal_graph import run_goals
from app.budget_engine.goals import goal_cap
from app.budget_service.forecast_service import snapshot
from app.db.models import Account, AgentRun, Goal
from tests.test_goal_graph import UID, TODAY, g, model, run_row, user, audit_in_tmp

pytestmark = pytest.mark.forecast_surplus


def run(db):
    return run_goals(db,UID,TODAY,model=model(g("savings",1000)))


def test_a_paycheck_stream_makes_the_forecast_the_source(user):
    r=run(user); summary=run_row(user,r["run_id"]).summary_json
    assert summary["surplus_source"] == "forecast" and summary["measured_surplus_cents"] == 97175
    assert abs(summary["forecast_surplus_cents"]-97175)/97175 <= .10


def test_the_cap_follows_the_gross_form_around_an_accepted_goal(user):
    with user() as s:
        s.add(Goal(user_id=UID,kind="debt_payoff",priority=1,monthly_cents=58300,target_cents=2160000,
                   start_date=date(2026,12,1),target_date=date(2030,1,1),strategy="avalanche",rationale="r",status="accepted"))
        s.commit()
    r=run(user)
    assert r["cap_cents"] == goal_cap(r["surplus_cents"],58300,.8,net_of_goals=True)


def test_a_balance_of_100_dollars_or_20000_gives_the_same_surplus(user):
    values=[]
    for cents in (10000,2000000):
        with user() as s:
            a=s.query(Account).filter_by(user_id=UID,name="Checking").one()
            a.current_balance_cents=cents; s.commit()
        values.append(run(user)["surplus_cents"])
    assert values[0] == values[1]


def test_the_card_minimum_is_not_subtracted_twice(user):
    with user() as s:
        expected=snapshot(s,UID,TODAY).result.monthly_surplus_cents
    assert run(user)["surplus_cents"] == expected


@pytest.mark.parametrize("reason", ["short_history","no_inflow_stream","stale_paycheck"])
def test_an_unavailable_forecast_falls_back_to_the_measured_figure(user,monkeypatch,reason):
    with user() as s:
        snap=snapshot(s,UID,TODAY)
    monkeypatch.setattr(goal_graph,"snapshot",lambda *a:replace(snap,result=replace(snap.result,surplus_fallback=reason,monthly_surplus_cents=None)))
    r=run(user)
    assert r["surplus_source"] == "measured" and r["surplus_fallback_reason"] == reason
    assert r["surplus_cents"] == 97175 and r["cap_cents"] == 77740


def test_the_simulated_date_is_used(user):
    r=run_goals(user,UID,date(2026,12,1),model=model(g("savings",1000)))
    assert run_row(user,r["run_id"]).summary_json["forecast_as_of"] == "2026-12-01"


def test_a_paused_budget_run_with_an_empty_summary_does_not_matter(user):
    with user() as s:
        s.add(AgentRun(user_id=UID,graph="budget",status="awaiting_user",summary_json={}))
        s.commit()
    assert run(user)["surplus_source"] == "forecast"


def test_a_surplus_at_or_under_zero_with_a_paycheck_is_no_surplus_not_a_fallback(user,monkeypatch):
    with user() as s:
        snap=snapshot(s,UID,TODAY)
    monkeypatch.setattr(goal_graph,"snapshot",lambda *a:replace(snap,result=replace(snap.result,monthly_surplus_cents=-1000)))
    r=run(user)
    assert r["status"] == "no_surplus" and r["surplus_source"] == "forecast" and r["surplus_fallback_reason"] is None
