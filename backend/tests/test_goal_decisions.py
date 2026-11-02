import json
from datetime import date
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from app import config
from app.agents.goal_graph import goal_waiting_for, run_goals
from app.budget_service import goal_decisions
from app.budget_service.goal_decisions import (GoalAlreadyDecided, GoalNotAcceptable, GoalNotFound, decide_goal,
                                               finish_decided_runs)
from app.db.models import AgentRun, Goal
from eval.budget_users import GOAL_USERS, seed_profile
from tests.fakes import ScriptedChatModel

PROFILE = GOAL_USERS[0]
TODAY = PROFILE.today
UID = "budget-goals-card-24"


@pytest.fixture(autouse=True)
def audit_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", tmp_path / "audit.jsonl")


@pytest.fixture
def user(db):
    with db() as s:
        seed_profile(s, PROFILE)
    return db


@pytest.fixture
def ck():
    return InMemorySaver()


def g(kind, cents, strategy=None, why="Because it fits."):
    return {"kind": kind, "monthly_dollars": cents / 100, "strategy": strategy, "rationale": why}


def model(*goals):
    return ScriptedChatModel(replies=[AIMessage(content=json.dumps({"goals": list(goals)}))])


def paused(user, ck, *goals):
    return run_goals(user, UID, TODAY, model=model(*goals), checkpointer=ck)


def goal(db, goal_id):
    with db() as s:
        return s.get(Goal, goal_id)


def run_row(db, run_id):
    with db() as s:
        return s.get(AgentRun, run_id)


def add_goal(db, kind="debt_payoff", status="proposed", start=date(2026, 12, 1), target=date(2027, 11, 1)):
    with db() as s:
        row = Goal(user_id=UID, kind=kind, priority=1, monthly_cents=1000, target_cents=12000, start_date=start,
                   target_date=target, rationale="x", status=status)
        s.add(row)
        s.commit()
        return row.id


def test_accept_marks_the_row_and_resumes(user, ck):
    r = paused(user, ck, g("debt_payoff", 58300, "avalanche"))
    gid = r["goal_ids"][0]
    result = decide_goal(user, UID, gid, "accepted", checkpointer=ck)
    assert result.id == gid and result.status == "accepted"
    assert goal(user, gid).status == "accepted"
    row = run_row(user, r["run_id"])
    assert row.status == "ok" and row.finished_at is not None
    assert goal_waiting_for(user, ck, r["run_id"]) == []


def test_other_users_goal_is_not_found(user):
    gid = add_goal(user)
    with pytest.raises(GoalNotFound):
        decide_goal(user, "someone-else", gid, "accepted")
    with pytest.raises(GoalNotFound):
        decide_goal(user, UID, gid + 100, "dismissed")
    assert goal(user, gid).status == "proposed"


def test_accept_twice_is_already_decided(user):
    gid = add_goal(user)
    decide_goal(user, UID, gid, "accepted")
    with pytest.raises(GoalAlreadyDecided):
        decide_goal(user, UID, gid, "accepted")
    assert goal(user, gid).status == "accepted"


def test_accept_after_dismiss_is_already_decided(user):
    gid = add_goal(user)
    decide_goal(user, UID, gid, "dismissed")
    with pytest.raises(GoalAlreadyDecided):
        decide_goal(user, UID, gid, "accepted")
    assert goal(user, gid).status == "dismissed"


def test_superseded_goal_cannot_be_accepted(user):
    gid = add_goal(user, status="superseded")
    with pytest.raises(GoalAlreadyDecided):
        decide_goal(user, UID, gid, "accepted")
    assert goal(user, gid).status == "superseded"


def test_completed_goal_cannot_be_dismissed(user):
    gid = add_goal(user, status="completed")
    with pytest.raises(GoalAlreadyDecided):
        decide_goal(user, UID, gid, "dismissed")
    assert goal(user, gid).status == "completed"


def test_dismiss_stops_an_accepted_goal(user):
    gid = add_goal(user, status="accepted")
    assert decide_goal(user, UID, gid, "dismissed").status == "dismissed"
    assert goal(user, gid).status == "dismissed"


def test_row_without_start_date_cannot_be_accepted(user):
    gid = add_goal(user, start=None)
    with pytest.raises(GoalNotAcceptable):
        decide_goal(user, UID, gid, "accepted")
    assert goal(user, gid).status == "proposed"
    assert decide_goal(user, UID, gid, "dismissed").status == "dismissed"


def test_row_without_target_date_cannot_be_accepted(user):
    gid = add_goal(user, target=None)
    with pytest.raises(GoalNotAcceptable):
        decide_goal(user, UID, gid, "accepted")
    assert goal(user, gid).status == "proposed"
    assert decide_goal(user, UID, gid, "dismissed").status == "dismissed"


def test_second_goal_of_an_accepted_kind_is_refused(user):
    a = add_goal(user, status="accepted")
    b = add_goal(user)
    with pytest.raises(GoalNotAcceptable):
        decide_goal(user, UID, b, "accepted")
    assert goal(user, a).status == "accepted" and goal(user, b).status == "proposed"
    # another kind is still fine
    c = add_goal(user, kind="savings")
    assert decide_goal(user, UID, c, "accepted").status == "accepted"


def test_a_failing_resume_keeps_the_decision(user, ck, monkeypatch, caplog):
    r = paused(user, ck, g("debt_payoff", 58300, "avalanche"))
    gid = r["goal_ids"][0]

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    with monkeypatch.context() as m:
        m.setattr(goal_decisions, "resume_goal_run", broken)
        assert decide_goal(user, UID, gid, "accepted", checkpointer=ck).status == "accepted"
    assert goal(user, gid).status == "accepted"
    assert run_row(user, r["run_id"]).status == "awaiting_user"
    assert any("resume failed" in rec.getMessage() for rec in caplog.records)
    finish_decided_runs(user, UID, checkpointer=ck)
    assert run_row(user, r["run_id"]).status == "ok"


def test_lost_checkpoint_keeps_the_decision_and_ends_the_run(user, ck):
    r = paused(user, ck, g("debt_payoff", 58300, "avalanche"))
    ck.delete_thread(f"goals-{r['run_id']}")
    gid = r["goal_ids"][0]
    assert decide_goal(user, UID, gid, "accepted", checkpointer=ck).status == "accepted"
    row = run_row(user, r["run_id"])
    assert row.status == "ok" and row.summary_json["decisions"] == {str(gid): "accepted"}


def test_lost_checkpoint_waits_for_the_other_goals(user, ck):
    r = paused(user, ck, g("debt_payoff", 58300, "avalanche"), g("savings", 10000))
    assert len(r["goal_ids"]) == 2
    ck.delete_thread(f"goals-{r['run_id']}")
    decide_goal(user, UID, r["goal_ids"][0], "accepted", checkpointer=ck)
    assert run_row(user, r["run_id"]).status == "awaiting_user"
    decide_goal(user, UID, r["goal_ids"][1], "dismissed", checkpointer=ck)
    assert run_row(user, r["run_id"]).status == "ok"


def test_only_goal_runs_are_resumed(user, ck, monkeypatch):
    r = paused(user, ck, g("debt_payoff", 58300, "avalanche"))
    with user() as s:
        other = AgentRun(user_id=UID, graph="budget", status="awaiting_user")
        s.add(other)
        s.commit()
        other_id = other.id
    seen = []
    real = goal_decisions.goal_waiting_for
    monkeypatch.setattr(goal_decisions, "goal_waiting_for",
                        lambda sf, c, run_id: seen.append(run_id) or real(sf, c, run_id))
    decide_goal(user, UID, r["goal_ids"][0], "accepted", checkpointer=ck)
    assert seen == [r["run_id"]]
    assert run_row(user, other_id).status == "awaiting_user"


def test_the_conditional_update_lets_one_decision_through(user):
    gid = add_goal(user)
    stale = SimpleNamespace(id=gid, user_id=UID, kind="debt_payoff", status="proposed")

    # the second request read the row as proposed before the first one committed
    def stale_factory():
        s = user()
        real = s.get
        s.get = lambda model_, pk, *a, **k: stale if (model_, pk) == (Goal, gid) else real(model_, pk, *a, **k)
        return s

    decide_goal(user, UID, gid, "accepted")
    with pytest.raises(GoalAlreadyDecided):
        decide_goal(stale_factory, UID, gid, "accepted")
    assert goal(user, gid).status == "accepted"
    # a stale dismiss of a stopped goal changes nothing either
    decide_goal(user, UID, gid, "dismissed")
    with pytest.raises(GoalAlreadyDecided):
        decide_goal(stale_factory, UID, gid, "dismissed")
    assert goal(user, gid).status == "dismissed"
