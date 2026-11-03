import json
from datetime import date
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from app import config
from app.api import budget as budget_api
from app.api import budget_hooks
from app.api import goals as goals_api
from app.budget_service.simulate import advance_days
from app.db.models import Goal, User
from eval.budget_users import GOAL_USERS, seed_profile
from tests.fakes import ScriptedChatModel, SchemaReplyModel

PROFILE = GOAL_USERS[0]
UID = "budget-goals-card-24"
NOVEMBER, DECEMBER = date(2026, 11, 2), date(2026, 12, 1)


def g(kind, cents, strategy=None):
    return {"kind": kind, "monthly_dollars": cents / 100, "strategy": strategy, "rationale": "Because it fits."}


def model(*goals, times=1):
    reply = AIMessage(content=json.dumps({"goals": list(goals)}))
    return ScriptedChatModel(replies=[reply] * times)


# the same wiring as test_goals_api's api fixture, plus the budget routes' clock: the whole loop goes through HTTP on the
# seeded goals-card-24 user, whose first budget is built from the history that ends on 2026-10-31
@pytest.fixture
def api(client, db, monkeypatch, tmp_path):
    ck, models = InMemorySaver(), []
    with db() as s:
        seed_profile(s, PROFILE)
    monkeypatch.setattr(config, "DEV_USER_EMAIL", "goals-card-24@eval.local")
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(budget_hooks, "session_factory", db)
    monkeypatch.setattr(budget_hooks, "get_checkpointer", lambda: ck)
    monkeypatch.setattr(budget_hooks, "make_model", lambda: models.pop(0) if models else SchemaReplyModel())
    # runs finish before the POST returns: the in-memory test db is one sqlite connection threads can't share
    monkeypatch.setattr(goals_api, "_spawn", lambda work: work())
    monkeypatch.setattr(budget_api, "_spawn", lambda work: work())
    return SimpleNamespace(client=client, db=db, ck=ck, models=models, mp=monkeypatch)


def set_today(api, day):
    api.mp.setattr(goals_api, "_today", lambda: day)
    api.mp.setattr(budget_api, "_today", lambda: day)


def line(budget, category):
    return next((x for x in budget["lines"] if x["category"] == category), None)


def run_budget(api, day):
    r = api.client.post("/budget/run", json={"date": day.isoformat()})
    assert r.status_code == 202, r.text
    assert api.client.get(f"/budget/runs/{r.json()['run_id']}").json()["status"] == "ok"


# the goal run's body, after a scripted model proposes these goals (no date: the route follows the clock or the simulation)
def propose(api, *goals, times=1, **body):
    api.models.append(model(*goals, times=times))
    r = api.client.post("/goals/generate", json=body)
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    return run_id, api.client.get(f"/goals/runs/{run_id}").json()


def accept(api, goal_id):
    r = api.client.post(f"/goals/{goal_id}/accept")
    assert r.status_code == 200, r.text
    return r.json()


# --- the ticket's done-when: accepting a goal shows it on the dashboard and in next month's budget ---

def test_accepting_a_goal_reaches_the_dashboard_and_next_months_budget(api):
    set_today(api, NOVEMBER)
    run_budget(api, NOVEMBER)       # november's budget
    free_november = line(api.client.get("/budget").json(), "FREE")["limit_cents"]
    _, run = propose(api, g("debt_payoff", 58300, "avalanche"))
    [goal_id] = run["waiting_for"]
    assert accept(api, goal_id)["status"] == "accepted"

    dashboard = api.client.get("/goals?status=accepted").json()       # what the dashboard's card shows: it asks for `current` and keeps the accepted ones
    assert [(x["id"], x["kind"], x["monthly_cents"], x["start_date"]) for x in dashboard] == [
        (goal_id, "debt_payoff", 58300, "2026-12-01")]
    now = api.client.get("/budget").json()
    assert line(now, "GOAL_DEBT_PAYOFF") is None                        # november is untouched
    assert line(now, "FREE")["limit_cents"] == free_november

    set_today(api, DECEMBER)
    run_budget(api, DECEMBER)       # closes november
    december = api.client.get("/budget").json()
    goal_line = line(december, "GOAL_DEBT_PAYOFF")
    assert (goal_line["name"], goal_line["limit_cents"], goal_line["group"], goal_line["flagged"]) == (
        "Debt payoff", 58300, "goal", False)
    assert goal_line["spent_cents"] == 0 and goal_line["pace"] is None
    # the goal comes out of the free line, so the lines still add up to the total
    assert sum(x["limit_cents"] for x in december["lines"]) == december["total_cents"]
    assert line(december, "FREE")["limit_cents"] < free_november


def test_the_paused_run_finishes_and_a_lost_checkpoint_changes_nothing(api):
    set_today(api, NOVEMBER)
    run_id, run = propose(api, g("debt_payoff", 58300, "avalanche"), g("emergency_fund", 5000))
    debt, fund = run["goal_ids"]
    assert (run["status"], run["waiting_for"]) == ("awaiting_user", [debt, fund])
    accept(api, debt)
    body = api.client.get(f"/goals/runs/{run_id}").json()
    assert (body["status"], body["waiting_for"]) == ("awaiting_user", [fund])
    api.ck.delete_thread(f"goals-{run_id}")       # the checkpoint is lost with one proposal left
    body = api.client.get(f"/goals/runs/{run_id}").json()
    assert (body["status"], body["waiting_for"]) == ("awaiting_user", [fund])       # the rows still say so
    assert api.client.post(f"/goals/{fund}/dismiss").json()["status"] == "dismissed"
    body = api.client.get(f"/goals/runs/{run_id}").json()
    assert (body["status"], body["waiting_for"]) == ("ok", [])      # finished from the rows, with no checkpoint
    with api.db() as s:
        assert {x.kind: x.status for x in s.query(Goal)} == {"debt_payoff": "accepted", "emergency_fund": "dismissed"}


def test_a_regenerated_proposal_fits_around_the_accepted_goal(api):
    set_today(api, NOVEMBER)
    _, run = propose(api, g("debt_payoff", 58300, "avalanche"))
    accept(api, run["waiting_for"][0])
    # the cap is 77740 - 58300 = 19440: one cent more is rejected twice, so nothing is stored
    _, over = propose(api, g("emergency_fund", 19441), times=2)
    assert (over["status"], over["goal_ids"]) == ("error", [])
    _, fits = propose(api, g("emergency_fund", 19440))
    [fund] = fits["goal_ids"]
    with api.db() as s:
        rows = s.query(Goal).filter(Goal.status.in_(("accepted", "proposed"))).all()
        assert {x.kind: (x.status, x.monthly_cents) for x in rows} == {
            "debt_payoff": ("accepted", 58300), "emergency_fund": ("proposed", 19440)}
    assert accept(api, fund)["status"] == "accepted"


# --- a simulated user ---

def test_a_simulated_user_sees_the_line_without_patching_the_clock(api):
    with api.db() as s:       # no patched clock below: every route follows sim_today
        s.get(User, UID).prefs_json = {"sim": {"profile": PROFILE.id, "date": NOVEMBER.isoformat()}}
        s.commit()
    run_budget(api, NOVEMBER)
    _, run = propose(api, g("debt_payoff", 58300, "avalanche"), g("emergency_fund", 5000))
    debt, fund = run["goal_ids"]
    accept(api, debt)       # the emergency fund proposal is left undecided over the month end
    assert api.client.get("/budget").json()["period"]["start"] == "2026-11-01"

    advance_days(api.db, UID, 29, model=SchemaReplyModel(), checkpointer=api.ck)
    with api.db() as s:
        assert s.get(User, UID).prefs_json["sim"]["date"] == DECEMBER.isoformat()
    december = api.client.get("/budget").json()
    assert december["period"]["start"] == "2026-12-01"
    goal_line = line(december, "GOAL_DEBT_PAYOFF")
    assert (goal_line["name"], goal_line["limit_cents"], goal_line["group"]) == ("Debt payoff", 58300, "goal")
    assert line(december, "GOAL_EMERGENCY_FUND") is None
    with api.db() as s:
        assert {x.kind: x.status for x in s.query(Goal)} == {"debt_payoff": "accepted", "emergency_fund": "superseded"}
    assert api.client.post(f"/goals/{fund}/accept").status_code == 409       # too late to accept it
    assert api.client.get(f"/goals/runs/{run['run_id']}").json()["status"] == "expired"
