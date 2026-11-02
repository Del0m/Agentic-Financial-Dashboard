import json
import sqlite3
from datetime import date

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from app import config
from app.agents import goal_graph
from app.agents.budget_graph import close_interrupted_runs
from app.agents.goal_graph import (build_goal_graph, finish_paused_run, goal_waiting_for, resume_goal_run, run_goals,
                                   run_goals_locked)
from app.budget_service.daily import running_elsewhere, user_lock
from app.budget_service.goal_lines import expire_goal_proposals
from app.db.models import Account, AgentRun, Goal
from eval.budget_users import GOAL_USERS, seed_profile
from scripts.seed_budget_user import delete_user
from tests.factories import add_txn
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


def stored(db, status="proposed"):
    with db() as s:
        return s.query(Goal).filter_by(user_id=UID, status=status).order_by(Goal.priority).all()


def run_row(db, run_id):
    with db() as s:
        return s.get(AgentRun, run_id)


def set_status(db, goal_id, status):
    with db() as s:
        s.get(Goal, goal_id).status = status
        s.commit()


def thread_of(run_id):
    return {"configurable": {"thread_id": f"goals-{run_id}"}}


# spending that leaves nothing in a typical month, so the next run withdraws
def go_red(db):
    with db() as s:
        s.info["n"] = 100000     # add_txn numbers plaid ids per session; keep clear of the seeded ones
        acct = s.query(Account).filter_by(user_id=UID, type="depository").first()
        for m in (8, 9, 10):
            add_txn(s, acct, 1000000, "FOOD_AND_DRINK", day=date(2026, m, 3))
        s.commit()


# the click lands after await_approval read the rows and before the run says it's awaiting_user
def click_before_the_mark(db, monkeypatch):
    real = goal_graph._mark_paused

    def clicked_first(session_factory, run_id, result):
        set_status(db, result["goal_ids"][0], "accepted")
        real(session_factory, run_id, result)

    monkeypatch.setattr(goal_graph, "_mark_paused", clicked_first)


def test_run_pauses_after_persisting(user, ck):
    r = run_goals(user, UID, TODAY, model=model(g("debt_payoff", 58300, "avalanche")), checkpointer=ck)
    assert r["status"] == "awaiting_user" and r["waiting_for"] == r["goal_ids"]
    assert goal_waiting_for(user, ck, r["run_id"]) == r["goal_ids"]
    assert stored(user)[0].status == "proposed"
    row = run_row(user, r["run_id"])
    assert row.status == "awaiting_user" and row.finished_at is None and row.summary_json["goal_ids"] == r["goal_ids"]
    assert row.summary_json["outcome"] == "awaiting_user" and row.summary_json["surplus_cents"] == 97175


def test_no_checkpointer_does_not_pause(user):
    r = run_goals(user, UID, TODAY, model=model(g("savings", 1000)))
    assert r["status"] == "ok" and r["waiting_for"] == []
    assert run_row(user, r["run_id"]).status == "ok"
    assert [p.kind for p in r["plans"]] == ["savings"]      # the cli prints them


def test_no_goals_means_no_pause(user, ck):
    go_red(user)
    r = run_goals(user, UID, TODAY, model=ScriptedChatModel(replies=[]), checkpointer=ck)
    assert (r["status"], r["waiting_for"]) == ("no_surplus", [])
    assert run_row(user, r["run_id"]).status == "ok"
    assert goal_waiting_for(user, ck, r["run_id"]) == []
    assert list(ck.list(thread_of(r["run_id"]))) == []


def test_goals_changed_ends_without_pausing(user, ck, monkeypatch):
    real = goal_graph.invoke_structured

    # the user accepts a goal after the facts were read and before the run persists
    def accepting(*args, **kwargs):
        with user() as s:
            s.add(Goal(user_id=UID, kind="debt_payoff", priority=1, monthly_cents=1000, target_cents=12000,
                       start_date=date(2026, 12, 1), target_date=date(2027, 11, 1), rationale="x", status="accepted"))
            s.commit()
        return real(*args, **kwargs)

    monkeypatch.setattr(goal_graph, "invoke_structured", accepting)
    r = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    assert (r["status"], r["reason"], r["waiting_for"]) == ("error", "goals_changed", [])
    assert run_row(user, r["run_id"]).status == "error"
    assert list(ck.list(thread_of(r["run_id"]))) == []


def test_pause_releases_the_lock(user, ck):
    run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    assert not user_lock(UID).locked() and not running_elsewhere(user, UID)


# debt names and the model's words stay in the database's own rows; the checkpoint file has no business holding them
def test_paused_checkpoint_holds_no_debts_or_facts(user, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "ck.db"), check_same_thread=False)
    saver = SqliteSaver(conn)
    fake = model(g("debt_payoff", 58300, "avalanche", "Secret rationale text"))
    fake.replies.insert(0, AIMessage(content=json.dumps({"goals": [g("savings", 99999900, why="Secret rationale text")]})))
    r = run_goals(user, UID, TODAY, model=fake, checkpointer=saver)
    assert r["status"] == "awaiting_user" and r["attempts"] == 2      # a correction round ran first

    blobs = b""
    for table in ("checkpoints", "writes"):
        for row in conn.execute(f"select * from {table}"):
            blobs += b"".join(c if isinstance(c, bytes) else str(c).encode() for c in row)
    assert b"waiting_for" in blobs      # the bytes really are the paused state, and they're readable
    for secret in (b"Platinum Card", b"Student Loan", b"Secret rationale text"):
        assert secret not in blobs

    values = build_goal_graph(user, model=None, gateway=None, checkpointer=saver).get_state(thread_of(r["run_id"])).values
    assert values["debts"] == [] and values["plans"] == [] and values["context"] == ""
    assert values["allocations"] == [] and values["proposal"] == []
    assert values["goal_ids"] == r["goal_ids"]

    # and a real saver resumes from that
    set_status(user, r["goal_ids"][0], "accepted")
    assert resume_goal_run(user, r["run_id"], r["goal_ids"][0], "accepted", checkpointer=saver)["status"] == "ok"
    assert conn.execute("select count(*) from checkpoints").fetchone()[0] == 0
    assert conn.execute("select count(*) from writes").fetchone()[0] == 0


@pytest.mark.parametrize("raised", [RuntimeError("boom"), KeyboardInterrupt()], ids=["error", "keyboard_interrupt"])
def test_failed_run_leaves_no_checkpoint(user, ck, monkeypatch, raised):
    def explode(*args, **kwargs):
        raise raised

    monkeypatch.setattr(goal_graph, "invoke_structured", explode)
    if isinstance(raised, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            run_goals(user, UID, TODAY, model=ScriptedChatModel(replies=[]), checkpointer=ck)
    else:
        assert run_goals(user, UID, TODAY, model=ScriptedChatModel(replies=[]), checkpointer=ck)["status"] == "error"
    with user() as s:
        row = s.query(AgentRun).filter_by(user_id=UID, graph="goals").one()
        run_id, status = row.id, row.status
    assert status == "error"
    assert list(ck.list(thread_of(run_id))) == []


def test_finished_run_deletes_its_checkpoint(user, ck):
    r = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    assert list(ck.list(thread_of(r["run_id"]))) != []      # paused: it has to keep it
    set_status(user, r["goal_ids"][0], "accepted")
    resume_goal_run(user, r["run_id"], r["goal_ids"][0], "accepted", checkpointer=ck)
    assert list(ck.list(thread_of(r["run_id"]))) == []


def test_resume_waits_for_every_goal(user, ck):
    r = run_goals(user, UID, TODAY, model=model(g("debt_payoff", 58300, "avalanche"), g("savings", 10000)), checkpointer=ck)
    first, second = r["goal_ids"]
    set_status(user, first, "accepted")
    mid = resume_goal_run(user, r["run_id"], first, "accepted", checkpointer=ck)
    assert (mid["status"], mid["waiting_for"]) == ("awaiting_user", [second])
    assert goal_waiting_for(user, ck, r["run_id"]) == [second]
    assert run_row(user, r["run_id"]).status == "awaiting_user"
    set_status(user, second, "dismissed")
    done = resume_goal_run(user, r["run_id"], second, "dismissed", checkpointer=ck)
    assert done["status"] == "ok" and done["waiting_for"] == []
    row = run_row(user, r["run_id"])
    assert row.status == "ok" and row.finished_at is not None
    summary = row.summary_json
    assert summary["decisions"] == {str(first): "accepted", str(second): "dismissed"}
    assert summary["surplus_cents"] == 97175 and summary["attempts"] == 1
    assert summary["outcome"] == "ok" and summary["goal_ids"] == r["goal_ids"]


# a goal's row is what counts: a resume whose row still says proposed decides nothing
def test_rows_decide_not_the_resume_payload(user, ck):
    r = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    [goal_id] = r["goal_ids"]
    again = resume_goal_run(user, r["run_id"], goal_id, "accepted", checkpointer=ck)
    assert (again["status"], again["waiting_for"]) == ("awaiting_user", [goal_id])
    assert stored(user)[0].id == goal_id
    set_status(user, goal_id, "dismissed")
    done = resume_goal_run(user, r["run_id"], goal_id, "accepted", checkpointer=ck)
    assert done["status"] == "ok"
    assert run_row(user, r["run_id"]).summary_json["decisions"] == {str(goal_id): "dismissed"}


def test_resume_never_calls_a_model(user, ck, monkeypatch):
    r = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    monkeypatch.setattr(goal_graph, "get_chat_model", lambda **kw: pytest.fail("no model after the pause"))
    set_status(user, r["goal_ids"][0], "accepted")
    assert resume_goal_run(user, r["run_id"], r["goal_ids"][0], "accepted", checkpointer=ck)["status"] == "ok"


# another resume finished the run while this one waited for the lock: it reports, and changes nothing
def test_a_second_resume_of_a_finished_run_reports_it(user, ck):
    r = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    set_status(user, r["goal_ids"][0], "accepted")
    resume_goal_run(user, r["run_id"], r["goal_ids"][0], "accepted", checkpointer=ck)
    again = resume_goal_run(user, r["run_id"], r["goal_ids"][0], "accepted", checkpointer=ck)
    assert again == {"run_id": r["run_id"], "status": "ok", "waiting_for": []}


# still paused means nothing was lost: the run keeps waiting and the next resume finishes it
def test_failed_resume_keeps_waiting(user, ck):
    r = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    [goal_id] = r["goal_ids"]
    set_status(user, goal_id, "accepted")
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 2:     # the first is the waiting check, the second await_approval's read
            raise RuntimeError("database is locked")
        return user()

    with pytest.raises(RuntimeError):
        resume_goal_run(flaky, r["run_id"], goal_id, "accepted", checkpointer=ck)
    assert run_row(user, r["run_id"]).status == "awaiting_user"
    assert goal_waiting_for(user, ck, r["run_id"]) == [goal_id]
    assert resume_goal_run(user, r["run_id"], goal_id, "accepted", checkpointer=ck)["status"] == "ok"
    assert run_row(user, r["run_id"]).summary_json["decisions"] == {str(goal_id): "accepted"}


def test_decision_before_the_pause_is_mark_still_finishes(user, ck, monkeypatch):
    click_before_the_mark(user, monkeypatch)
    r = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    assert (r["status"], r["waiting_for"]) == ("ok", [])
    row = run_row(user, r["run_id"])
    assert row.status == "ok" and row.summary_json["decisions"] == {str(r["goal_ids"][0]): "accepted"}
    assert list(ck.list(thread_of(r["run_id"]))) == []


def test_early_decision_on_one_goal_keeps_waiting_for_the_other(user, ck, monkeypatch):
    click_before_the_mark(user, monkeypatch)
    r = run_goals(user, UID, TODAY, model=model(g("debt_payoff", 58300, "avalanche"), g("savings", 10000)), checkpointer=ck)
    assert (r["status"], r["waiting_for"]) == ("awaiting_user", [r["goal_ids"][1]])
    assert run_row(user, r["run_id"]).status == "awaiting_user"


# the early resume failing doesn't lose the run: it stays paused for the next decision
def test_failed_early_resume_still_reports_the_pause(user, ck, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    click_before_the_mark(user, monkeypatch)
    monkeypatch.setattr(goal_graph, "resume_goal_run", broken)
    r = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    assert (r["status"], r["waiting_for"]) == ("awaiting_user", r["goal_ids"])
    assert run_row(user, r["run_id"]).status == "awaiting_user"


def test_regenerating_expires_the_paused_run(user, ck):
    r1 = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    r2 = run_goals(user, UID, TODAY, model=model(g("savings", 2000)), checkpointer=ck)
    row = run_row(user, r1["run_id"])
    assert row.status == "expired" and run_row(user, r2["run_id"]).status == "awaiting_user"
    assert row.finished_at is not None and row.summary_json["expired_goal_ids"] == r1["goal_ids"]
    assert list(ck.list(thread_of(r1["run_id"]))) == []
    assert goal_waiting_for(user, ck, r2["run_id"]) == r2["goal_ids"]


def test_a_checkpoint_that_will_not_delete_is_only_logged(user, caplog):
    r1_thread = {}

    class Stuck(InMemorySaver):
        def delete_thread(self, thread_id):
            if thread_id == r1_thread.get("id"):
                raise RuntimeError("database is locked")
            super().delete_thread(thread_id)

    saver = Stuck()
    r1 = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=saver)
    r1_thread["id"] = f"goals-{r1['run_id']}"
    r2 = run_goals(user, UID, TODAY, model=model(g("savings", 2000)), checkpointer=saver)
    assert r2["status"] == "awaiting_user"
    assert run_row(user, r1["run_id"]).status == "expired"
    assert any(r1_thread["id"] in rec.getMessage() for rec in caplog.records)


def test_failed_regeneration_keeps_the_paused_run(user, ck):
    r1 = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    bad = model(g("savings", 99999900))
    bad.replies *= 2
    r2 = run_goals(user, UID, TODAY, model=bad, checkpointer=ck)
    assert r2["status"] == "error"
    assert run_row(user, r1["run_id"]).status == "awaiting_user"
    assert [x.id for x in stored(user)] == r1["goal_ids"]
    assert goal_waiting_for(user, ck, r1["run_id"]) == r1["goal_ids"]


def test_withdrawing_expires_the_paused_run(user, ck):
    r1 = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    go_red(user)
    r2 = run_goals(user, UID, TODAY, model=ScriptedChatModel(replies=[]), checkpointer=ck)
    assert (r2["status"], r2["withdrawn"]) == ("no_surplus", 1)
    assert run_row(user, r1["run_id"]).status == "expired"
    assert list(ck.list(thread_of(r1["run_id"]))) == []


# close can't reach the checkpointer, so an expired run's checkpoint stays behind. it can't bring the run back
def test_a_leftover_checkpoint_can_not_revive_an_expired_run(user, ck):
    r = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    with user() as s:
        expire_goal_proposals(s, r["goal_ids"])
        s.commit()
    assert list(ck.list(thread_of(r["run_id"]))) != []
    assert goal_waiting_for(user, ck, r["run_id"]) == []
    late = resume_goal_run(user, r["run_id"], r["goal_ids"][0], "accepted", checkpointer=ck)
    assert (late["status"], late["waiting_for"]) == ("expired", [])
    assert run_row(user, r["run_id"]).status == "expired"


def test_restart_cleanup_leaves_a_paused_run(user, ck):
    r = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    assert close_interrupted_runs(user) == 0
    assert run_row(user, r["run_id"]).status == "awaiting_user"


def test_reused_run_id_starts_clean(user, ck):
    # seed --replace deletes the runs and sqlite reuses the id: the new run mustn't start on the old thread
    first = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    with user() as s:
        delete_user(s, UID)
        seed_profile(s, PROFILE)
    seen = []
    lock = user_lock(UID)
    assert lock.acquire(blocking=False, session_factory=user)
    try:
        again = run_goals_locked(user, UID, TODAY, model=model(g("savings", 2000)), claim=lock, checkpointer=ck,
                                 on_start=lambda run_id: seen.append(list(ck.list(thread_of(run_id)))))
    finally:
        lock.release()
    assert again["run_id"] == first["run_id"]       # the id really is reused
    assert seen == [[]]
    assert again["status"] == "awaiting_user" and again["waiting_for"] == again["goal_ids"]
    assert stored(user)[0].monthly_cents == 2000


# setup that fails before the graph starts still marks the run, not leaves it "running"
def test_checkpointer_failure_marks_error(user):
    class Broken(InMemorySaver):
        def delete_thread(self, thread_id):
            raise RuntimeError("database is locked")

    r = run_goals(user, UID, TODAY, model=ScriptedChatModel(replies=[]), checkpointer=Broken())
    assert (r["status"], r["reason"]) == ("error", "RuntimeError")
    assert run_row(user, r["run_id"]).status == "error"


def test_finish_paused_run_from_rows(user, ck):
    r = run_goals(user, UID, TODAY, model=model(g("debt_payoff", 58300, "avalanche"), g("savings", 10000)), checkpointer=ck)
    first, second = r["goal_ids"]
    set_status(user, first, "accepted")
    finish_paused_run(user, r["run_id"])        # one is still proposed: it isn't over
    assert run_row(user, r["run_id"]).status == "awaiting_user"
    set_status(user, second, "dismissed")
    finish_paused_run(user, r["run_id"])
    row = run_row(user, r["run_id"])
    assert row.status == "ok" and row.finished_at is not None
    assert row.summary_json["decisions"] == {str(first): "accepted", str(second): "dismissed"}
    assert row.summary_json["outcome"] == "ok"
    assert row.summary_json["surplus_cents"] == 97175 and row.summary_json["goal_ids"] == r["goal_ids"]


def test_finish_paused_run_leaves_other_statuses(user, ck):
    r1 = run_goals(user, UID, TODAY, model=model(g("savings", 1000)), checkpointer=ck)
    run_goals(user, UID, TODAY, model=model(g("savings", 2000)), checkpointer=ck)
    finish_paused_run(user, r1["run_id"])
    assert run_row(user, r1["run_id"]).status == "expired"
