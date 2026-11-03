import json
from datetime import date
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from app import config
from app.agents.budget_graph import close_interrupted_runs
from app.api import budget_hooks
from app.api import goals as goals_api
from app.budget_service.daily import user_lock
from app.db.models import Account, AgentRun, Goal, User
from eval.budget_users import GOAL_USERS, seed_profile
from tests.factories import add_txn, add_user
from tests.fakes import ScriptedChatModel

PROFILE = GOAL_USERS[0]
UID = "budget-goals-card-24"


def goal(s, user_id, priority, status="proposed", **over):
    fields = dict(kind="savings", monthly_cents=10000, target_cents=120000, target_date=date(2027, 11, 1),
                  start_date=date(2026, 12, 1), strategy=None, rationale="Save a little.")
    s.add(Goal(user_id=user_id, priority=priority, status=status, **{**fields, **over}))


def g(kind, cents, strategy=None, why="Because it fits."):
    return {"kind": kind, "monthly_dollars": cents / 100, "strategy": strategy, "rationale": why}


def model(*goals, times=1):
    reply = AIMessage(content=json.dumps({"goals": list(goals)}))
    return ScriptedChatModel(replies=[reply] * times)


# routes read the hooks at call time: the test db, one in-memory checkpointer, and scripted models in order. the user is
# the seeded goals-card-24 profile (a 24.99% card and a student loan, with money left each month)
@pytest.fixture
def api(client, db, monkeypatch, tmp_path):
    ck, models = InMemorySaver(), []
    with db() as s:
        seed_profile(s, PROFILE)
    monkeypatch.setattr(config, "DEV_USER_EMAIL", "goals-card-24@eval.local")
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(budget_hooks, "session_factory", db)
    monkeypatch.setattr(budget_hooks, "get_checkpointer", lambda: ck)
    monkeypatch.setattr(budget_hooks, "make_model", lambda: models.pop(0) if models else ScriptedChatModel(replies=[]))
    # runs finish before POST /goals/generate returns: the in-memory test db is one sqlite connection, which a
    # background thread and the polling requests can't use at the same time
    monkeypatch.setattr(goals_api, "_spawn", lambda work: work())
    monkeypatch.setattr(goals_api, "_today", lambda: PROFILE.today)
    return SimpleNamespace(client=client, db=db, ck=ck, models=models, mp=monkeypatch)


def generate(api, *goals, **body):
    api.models.append(model(*goals))
    r = api.client.post("/goals/generate", json=body)
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def run_of(api, run_id):
    return api.client.get(f"/goals/runs/{run_id}").json()


@pytest.mark.forecast_surplus
def test_run_out_reports_the_surplus_source(api):
    rid = generate(api, g("savings", 1000))
    assert run_of(api, rid)["surplus_source"] == "forecast"


def status_of(api, goal_id):
    with api.db() as s:
        return s.get(Goal, goal_id).status


# --- listing ---

def test_own_proposed_goals_by_priority(api):
    with api.db() as s:
        goal(s, UID, 2)
        goal(s, UID, 1, kind="debt_payoff", monthly_cents=20000, target_cents=2160000, target_date=None,
             strategy="avalanche", rationale="Pay the card first.")
        s.commit()
    body = api.client.get("/goals").json()
    assert [x["priority"] for x in body] == [1, 2]
    first = {k: v for k, v in body[0].items() if k != "id"}
    assert first == {"kind": "debt_payoff", "priority": 1, "monthly_cents": 20000, "target_cents": 2160000,
                     "target_date": None, "start_date": "2026-12-01", "strategy": "avalanche",
                     "rationale": "Pay the card first.", "status": "proposed"}
    assert body[1]["target_date"] == "2027-11-01"


def test_other_users_and_superseded_hidden(api):
    with api.db() as s:
        add_user(s, "u2", "u2@x")
        goal(s, "u2", 1)
        goal(s, UID, 1, status="superseded")
        s.commit()
    assert api.client.get("/goals").json() == []


def test_no_goals_is_an_empty_list(api):
    r = api.client.get("/goals")
    assert (r.status_code, r.json()) == (200, [])


def test_get_goals_status_filter(api):
    with api.db() as s:
        add_user(s, "u2", "u2@x")
        goal(s, "u2", 1, status="accepted")
        goal(s, UID, 1, status="accepted", kind="debt_payoff", monthly_cents=30000)
        goal(s, UID, 2, kind="emergency_fund")
        goal(s, UID, 3)
        for status in ("superseded", "dismissed", "completed"):
            goal(s, UID, 1, status=status)
        s.commit()

    def listed(**params):
        return [(x["kind"], x["status"]) for x in api.client.get("/goals", params=params).json()]

    assert listed() == [("emergency_fund", "proposed"), ("savings", "proposed")]       # TPC-23's contract
    assert listed(status="proposed") == listed()
    assert listed(status="accepted") == [("debt_payoff", "accepted")]
    # proposals first, then accepted goals; each by priority
    assert listed(status="current") == [("emergency_fund", "proposed"), ("savings", "proposed"),
                                        ("debt_payoff", "accepted")]
    assert api.client.get("/goals", params={"status": "all"}).status_code == 422


def test_get_goals_finishes_a_decided_run(api):
    run_id = generate(api, g("debt_payoff", 58300, "avalanche"))
    [goal_id] = run_of(api, run_id)["goal_ids"]
    with api.db() as s:     # the click was saved but the resume failed
        s.get(Goal, goal_id).status = "accepted"
        s.commit()
    assert run_of(api, run_id)["status"] == "awaiting_user"
    api.client.get("/goals")
    body = run_of(api, run_id)
    assert (body["status"], body["waiting_for"]) == ("ok", [])


# --- generating ---

def test_generate_then_poll_reports_waiting(api):
    run_id = generate(api, g("debt_payoff", 58300, "avalanche"))
    body = api.client.get(f"/goals/runs/{run_id}").json()
    assert body["status"] == "awaiting_user" and body["waiting_for"] == body["goal_ids"] != []
    assert (body["run_id"], body["attempts"], body["issues"], body["error"]) == (run_id, 1, [], None)
    assert [x["id"] for x in api.client.get("/goals").json()] == body["goal_ids"]
    assert not user_lock(UID).locked()


def test_generate_uses_the_simulated_date(api):
    api.mp.setattr(goals_api, "_today", lambda: date(2027, 3, 5))
    with api.db() as s:
        s.get(User, UID).prefs_json = {"sim": {"profile": PROFILE.id, "date": "2026-11-20"}}
        s.commit()
    run_id = generate(api, g("savings", 5000))
    [goal_id] = run_of(api, run_id)["goal_ids"]
    assert api.client.get("/goals").json()[0]["start_date"] == "2026-12-01"      # not the clock's April
    assert status_of(api, goal_id) == "proposed"


def test_generate_uses_the_request_date(api):
    run_id = generate(api, g("savings", 5000), date="2026-11-25")
    assert run_of(api, run_id)["status"] == "awaiting_user"
    assert api.client.get("/goals").json()[0]["start_date"] == "2026-12-01"


def test_generate_busy_is_409_and_keeps_the_lock_clean(api):
    lock = user_lock(UID)
    assert lock.acquire(blocking=False, session_factory=api.db)
    try:
        r = api.client.post("/goals/generate", json={})
        assert r.status_code == 409 and r.json()["detail"]
    finally:
        lock.release()
    with api.db() as s:
        s.add(AgentRun(user_id=UID, graph="budget", status="running"))
        s.commit()
    r = api.client.post("/goals/generate", json={})
    assert r.status_code == 409 and not lock.locked()
    with api.db() as s:
        assert s.query(AgentRun).filter_by(graph="goals").count() == 0


def test_generate_start_failure_releases_the_lock(api):
    def boom(work):
        raise RuntimeError("no threads")

    api.mp.setattr(goals_api, "_spawn", boom)
    r = api.client.post("/goals/generate", json={})
    assert r.status_code == 500 and not user_lock(UID).locked()
    api.mp.setattr(goals_api, "_spawn", lambda work: work())
    assert generate(api, g("savings", 5000))


def test_generate_that_dies_before_its_row_is_500_and_releases_the_lock(api):
    def broken():
        raise RuntimeError("no model")

    api.mp.setattr(budget_hooks, "make_model", broken)
    r = api.client.post("/goals/generate", json={})
    assert r.status_code == 500 and not user_lock(UID).locked()


# --- polling ---

def go_red(db):
    with db() as s:
        s.info["n"] = 100000     # add_txn numbers plaid ids per session; keep clear of the seeded ones
        acct = s.query(Account).filter_by(user_id=UID, type="depository").first()
        for m in (8, 9, 10):
            add_txn(s, acct, 1000000, "FOOD_AND_DRINK", day=date(2026, m, 3))
        s.commit()


def test_run_reports_outcomes(api):
    go_red(api.db)
    r = api.client.post("/goals/generate", json={})
    body = run_of(api, r.json()["run_id"])
    assert (body["status"], body["outcome"], body["goal_ids"], body["waiting_for"]) == ("ok", "no_surplus", [], [])
    assert body["error"] is None


def test_run_reports_validation_failure(api):
    api.models.append(model(g("savings", 99999999, why="RAW-MODEL-TEXT"), times=2))
    r = api.client.post("/goals/generate", json={})
    assert r.status_code == 202
    resp = api.client.get(f"/goals/runs/{r.json()['run_id']}")
    body = resp.json()
    assert (body["status"], body["attempts"], body["goal_ids"]) == ("error", 2, [])
    assert body["issues"] and all(set(i) == {"message", "index", "kind"} for i in body["issues"])
    assert "RAW-MODEL-TEXT" not in resp.text


def test_run_of_another_user_or_graph_is_404(api):
    with api.db() as s:
        add_user(s, "u2", "u2@x")
        s.add_all([AgentRun(user_id="u2", graph="goals", status="ok"), AgentRun(user_id=UID, graph="budget", status="ok")])
        s.commit()
        theirs, budget = [r.id for r in s.query(AgentRun).order_by(AgentRun.id)]
    for run_id in (theirs, budget, 9999):
        r = api.client.get(f"/goals/runs/{run_id}")
        assert (r.status_code, r.json()["detail"]) == (404, "Run not found.")


def test_waiting_for_survives_a_lost_checkpoint(api):
    run_id = generate(api, g("debt_payoff", 58300, "avalanche"))
    api.ck.delete_thread(f"goals-{run_id}")
    body = run_of(api, run_id)
    assert body["status"] == "awaiting_user" and body["waiting_for"] == body["goal_ids"] != []


def test_waiting_for_lists_only_goals_still_proposed(api):
    run_id = generate(api, g("debt_payoff", 58300, "avalanche"), g("savings", 5000))
    first, second = run_of(api, run_id)["goal_ids"]
    with api.db() as s:
        s.get(Goal, first).status = "accepted"
        s.commit()
    body = run_of(api, run_id)
    assert (body["goal_ids"], body["waiting_for"]) == ([first, second], [second])


def test_interrupted_run_reports_its_error(api):
    with api.db() as s:
        s.add(AgentRun(user_id=UID, graph="goals", status="running"))
        s.commit()
    assert close_interrupted_runs(api.db) == 1
    with api.db() as s:
        run_id = s.query(AgentRun).one().id
    body = run_of(api, run_id)
    assert (body["status"], body["error"], body["goal_ids"], body["issues"], body["attempts"]) == (
        "error", "Interrupted", [], [], None)


def test_a_running_row_has_no_summary_to_read(api):
    with api.db() as s:
        s.add(AgentRun(user_id=UID, graph="goals", status="running"))
        s.commit()
        run_id = s.query(AgentRun).one().id
    body = run_of(api, run_id)
    assert (body["status"], body["outcome"], body["goal_ids"], body["waiting_for"]) == ("running", None, [], [])


# --- deciding ---

def test_accept_returns_the_goal(api):
    run_id = generate(api, g("debt_payoff", 58300, "avalanche"))
    [goal_id] = run_of(api, run_id)["goal_ids"]
    r = api.client.post(f"/goals/{goal_id}/accept")
    assert r.status_code == 200
    assert (r.json()["id"], r.json()["status"], r.json()["start_date"]) == (goal_id, "accepted", "2026-12-01")
    body = run_of(api, run_id)       # the last proposal was decided, so the paused run finished
    assert (body["status"], body["waiting_for"]) == ("ok", [])


def test_accept_decided_is_409_other_users_404(api):
    with api.db() as s:
        add_user(s, "u2", "u2@x")
        goal(s, "u2", 1)
        goal(s, UID, 1)
        s.commit()
        theirs, mine = [x.id for x in s.query(Goal).order_by(Goal.id)]
    assert api.client.post(f"/goals/{mine}/accept").status_code == 200
    again = api.client.post(f"/goals/{mine}/accept")
    assert (again.status_code, again.json()["detail"]) == (409, "This goal was already decided.")
    for route in ("accept", "dismiss"):
        r = api.client.post(f"/goals/{theirs}/{route}")
        assert (r.status_code, r.json()["detail"]) == (404, "Goal not found.")
    assert api.client.post("/goals/9999/accept").status_code == 404
    assert status_of(api, theirs) == "proposed"


def test_accept_expired_proposal_says_it_expired(api):
    with api.db() as s:
        goal(s, UID, 1, status="superseded")
        s.commit()
        goal_id = s.query(Goal).one().id
    r = api.client.post(f"/goals/{goal_id}/accept")
    assert (r.status_code, r.json()["detail"]) == (409, "This proposal expired. Generate goals again.")
    assert status_of(api, goal_id) == "superseded"
    r = api.client.post(f"/goals/{goal_id}/dismiss")      # nothing for them to dismiss: it's not "expired" to them
    assert (r.status_code, r.json()["detail"]) == (409, "This goal was already decided.")


# the decision commits before any resume, so a checkpointer that won't open can't lose the click: GET /goals heals later
def test_accept_commits_when_the_checkpointer_cannot_open(api):
    run_id = generate(api, g("debt_payoff", 58300, "avalanche"))
    [goal_id] = run_of(api, run_id)["goal_ids"]

    def broken():
        raise OSError("unable to open database file")

    api.mp.setattr(budget_hooks, "get_checkpointer", broken)
    r = api.client.post(f"/goals/{goal_id}/accept")
    assert (r.status_code, r.json()["status"]) == (200, "accepted")
    assert status_of(api, goal_id) == "accepted"
    assert run_of(api, run_id)["status"] == "awaiting_user"
    api.mp.setattr(budget_hooks, "get_checkpointer", lambda: api.ck)
    assert api.client.get("/goals?status=accepted").status_code == 200       # the page heals it
    assert run_of(api, run_id)["status"] == "ok"


def test_accept_without_start_date_is_409(api):
    with api.db() as s:
        goal(s, UID, 1, start_date=None)
        s.commit()
        goal_id = s.query(Goal).one().id
    r = api.client.post(f"/goals/{goal_id}/accept")
    assert r.status_code == 409 and "generate" in r.json()["detail"].lower()
    assert status_of(api, goal_id) == "proposed"


def test_accept_second_of_a_kind_is_409(api):
    with api.db() as s:
        goal(s, UID, 1, status="accepted")
        goal(s, UID, 2)
        s.commit()
        second = s.query(Goal).filter_by(status="proposed").one().id
    r = api.client.post(f"/goals/{second}/accept")
    assert r.status_code == 409
    assert r.json()["detail"] == ("This proposal is older, or a goal of that kind is already accepted. "
                                  "Generate goals again.")
    assert status_of(api, second) == "proposed"


def test_dismiss_proposed_and_stop_accepted(api):
    with api.db() as s:
        goal(s, UID, 1)
        goal(s, UID, 2, status="accepted", kind="emergency_fund")
        goal(s, UID, 3, status="completed", kind="debt_payoff")
        s.commit()
        proposed, accepted, completed = [x.id for x in s.query(Goal).order_by(Goal.id)]
    for goal_id in (proposed, accepted):
        r = api.client.post(f"/goals/{goal_id}/dismiss")
        assert (r.status_code, r.json()["status"]) == (200, "dismissed")
    assert api.client.post(f"/goals/{proposed}/dismiss").status_code == 409
    assert api.client.post(f"/goals/{completed}/dismiss").status_code == 409
