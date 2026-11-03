import json
import sqlite3
from dataclasses import replace
from datetime import date

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.agents import budget_graph
from app.agents.budget_graph import resume_budget_run, run_budget
from app.budget_service.recurring import StreamChange, refresh_streams, stored_streams
from app.db.models import Account, AdviceLog, AgentRun, Alert, BudgetLine, BudgetPeriod, RecurringStream
from eval.budget_users import RECURRING_USERS, seed_profile
from tests.factories import add_account, add_txn, add_user
from tests.fakes import ScriptedChatModel

UID = "recurring-user"
DAY = date(2026, 10, 3)


def model(*actions):
    return ScriptedChatModel(replies=[AIMessage(content=json.dumps(a)) for a in actions])


def run(db, tmp_path, day=DAY, m=None, **kwargs):
    return run_budget(db, UID, day, model=m or model(), audit_path=tmp_path / "audit.jsonl", **kwargs)


def rows(db, cls):
    with db() as s:
        return s.query(cls).all()


def seed(db, *, merchant="FitClub Gym", cents=4000, previous=True, budget=True, category="PERSONAL_CARE"):
    with db() as s:
        acct = add_account(s, add_user(s, UID, "recurring@x"), "Checking")
        for month in (7, 8, 9):
            add_txn(s, acct, cents, category, merchant=merchant, day=date(2026, month, 3))
        if previous:
            refresh_streams(s, UID, date(2026, 10, 2), None)
        add_txn(s, acct, 4300 if cents == 4000 else cents, category, merchant=merchant, day=DAY)
        if budget:
            period = BudgetPeriod(user_id=UID, start_date=date(2026, 10, 1), end_date=date(2026, 10, 31),
                                  status="open", total_cents=10000)
            period.lines = [BudgetLine(category=category, name="Care", kind="flexible", group="wants",
                                       limit_cents=10000, source="user")]
            s.add(period)
        s.commit()


def quiet_pace(monkeypatch):
    evaluate = budget_graph.evaluate
    monkeypatch.setattr(budget_graph, "evaluate", lambda *a: [replace(r, flagged=False) for r in evaluate(*a)])


def test_a_normal_day_makes_no_model_call(db, tmp_path, monkeypatch):
    with db() as s:
        p = RECURRING_USERS[0]
        uid = seed_profile(s, p, until=date(2026, 10, 2))
    m = model()
    quiet_pace(monkeypatch)
    state = run_budget(db, uid, date(2026, 10, 2), model=m, audit_path=tmp_path / "audit.jsonl")
    assert state["status"] == "ok" and m.seen == []
    assert len(rows(db, RecurringStream)) == 5 and rows(db, Alert) == []


def test_a_stream_flag_and_an_overspend_flag_in_one_category_both_alert(db, tmp_path):
    seed(db)
    with db() as s:
        s.query(BudgetLine).one().limit_cents = 1000
        s.commit()
    m = model({"cause": "one_time", "evidence": "A charge."}, {"action": "alert", "message": "Over budget."},
              {"action": "alert", "message": "Price up."})
    state = run(db, tmp_path, m=m)
    assert state["status"] == "ok" and {a.kind for a in rows(db, Alert)} == {"overspend", "price_increase"}
    assert {a.category for a in rows(db, AdviceLog)} == {"PERSONAL_CARE"}


@pytest.mark.parametrize("status", ["no_budget", "period_ended"])
def test_a_user_without_a_current_budget_gets_signal_alerts(db, tmp_path, monkeypatch, status):
    seed(db, budget=False)
    if status == "no_budget":
        monkeypatch.setattr(budget_graph, "initialize_budget", lambda *a: None)
    else:
        with db() as s:
            s.add(BudgetPeriod(user_id=UID, start_date=date(2026, 9, 1), end_date=date(2026, 9, 30),
                               status="open", total_cents=10000))
            s.commit()
    state = run(db, tmp_path, m=model({"action": "suggest_cancel", "message": "Cancel?"}))
    assert state["status"] == status
    [alert] = rows(db, Alert)
    assert alert.kind == "price_increase" and alert.action == "alert" and alert.proposed_limit_cents is None
    if status == "no_budget":
        assert alert.period_id is None


def test_a_detector_failure_does_not_stop_overspend_alerts(db, tmp_path, monkeypatch):
    seed(db)
    with db() as s:
        s.query(BudgetLine).one().limit_cents = 1000
        s.commit()
    def fail(*a):
        raise RuntimeError("secret detector details")
    monkeypatch.setattr(budget_graph, "refresh_streams", fail)
    state = run(db, tmp_path, m=model({"cause": "one_time", "evidence": "Charge."},
                                    {"action": "alert", "message": "Over budget."}))
    assert state["status"] == "ok" and [a.kind for a in rows(db, Alert)] == ["overspend"]
    summary = rows(db, AgentRun)[0].summary_json
    assert summary["signals_error"] == "RuntimeError" and "secret" not in json.dumps(summary)


def test_running_twice_on_the_same_day_alerts_once(db, tmp_path, monkeypatch):
    seed(db)
    quiet_pace(monkeypatch)
    run(db, tmp_path, m=model({"action": "alert", "message": "Price up."}))
    m = model()
    run(db, tmp_path, m=m)
    assert len(rows(db, Alert)) == 1 and m.seen == []


def test_an_existing_signal_key_is_not_written_again(db, tmp_path, monkeypatch):
    seed(db)
    quiet_pace(monkeypatch)
    run(db, tmp_path, m=model({"action": "alert", "message": "Price up."}))
    stream_id = rows(db, RecurringStream)[0].id
    monkeypatch.setattr(budget_graph, "refresh_streams", lambda *a: [StreamChange("price_increase", stream_id, 4000, DAY.isoformat())])
    m = model()
    run(db, tmp_path, m=m)
    assert len(rows(db, Alert)) == 1 and m.seen == []


def test_five_signals_make_three_model_calls(db, tmp_path, monkeypatch):
    seed(db)
    with db() as s:
        base = stored_streams(s, UID)[0]
        for i in range(4):
            s.add(RecurringStream(user_id=UID, direction="out", merchant_key=f"stream{i}", merchant_name=f"Stream {i}",
                                  first_date=base.first_date, first_seen_date=base.first_seen_date,
                                  **{f: getattr(base, f) for f in ("category", "detailed", "frequency", "typical_cents",
                                     "last_cents", "previous_cents", "amount_varies", "last_date", "next_date", "gap_days", "status")}))
        s.commit()
    changes = [StreamChange("price_increase", r.id, 4000, DAY.isoformat()) for r in rows(db, RecurringStream)]
    monkeypatch.setattr(budget_graph, "refresh_streams", lambda *a: changes)
    quiet_pace(monkeypatch)
    m = model(*[{"action": "alert", "message": "Price up."}] * 3)
    run(db, tmp_path, m=m)
    assert len(m.seen) == 3 and len(rows(db, Alert)) == len(rows(db, AdviceLog)) == 5
    audit = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert len(audit) == 5 and {a["purpose"] for a in audit} == {"recurring"}


def test_the_models_cancel_of_rent_is_downgraded(db, tmp_path, monkeypatch):
    seed(db, category="RENT_AND_UTILITIES")
    quiet_pace(monkeypatch)
    run(db, tmp_path, m=model({"action": "suggest_cancel", "message": "Cancel rent."}))
    [alert] = rows(db, Alert)
    assert alert.action == "alert" and "Cancel rent" not in alert.message


def test_new_subscription_alert_carries_stream_id_and_key(db, tmp_path, monkeypatch):
    seed(db, previous=False)
    with db() as s:
        add_txn(s, s.query(Account).one(), 100, "GENERAL_SERVICES", merchant="History", day=date(2026, 5, 1))
        s.commit()
    quiet_pace(monkeypatch)
    state = run(db, tmp_path, m=model({"action": "suggest_cancel", "message": "Cancel this."}), checkpointer=InMemorySaver())
    [alert] = rows(db, Alert)
    assert alert.kind == "new_subscription" and alert.stream_id == rows(db, RecurringStream)[0].id
    assert alert.signal_key == f"new_subscription|{alert.stream_id}|first_seen"
    assert alert.action == "suggest_cancel" and alert.proposed_limit_cents == 6000
    assert state["status"] == "ok" and state["waiting_for"] == []


def test_price_alert_message_quotes_the_old_and_new_price(db, tmp_path, monkeypatch):
    seed(db)
    quiet_pace(monkeypatch)
    run(db, tmp_path, m=model({"action": "alert", "message": "Changed."}))
    assert all(word in rows(db, Alert)[0].message for word in ("FitClub Gym", "$40.00", "$43.00"))


def test_a_paused_checkpoint_holds_no_signal_names_or_amounts(db, tmp_path):
    seed(db, merchant="ZZ-UNIQUE-GYM", cents=4321, previous=False)
    with db() as s:
        add_txn(s, s.query(Account).one(), 100, "GENERAL_SERVICES", merchant="History", day=date(2026, 5, 1))
        s.query(BudgetLine).one().limit_cents = 1000
        s.commit()
    path = tmp_path / "ck.db"
    with sqlite3.connect(path, check_same_thread=False) as conn:
        saver = SqliteSaver(conn)
        m = model({"cause": "recurring", "evidence": "ZZ-UNIQUE-GYM costs $43.21."},
                  {"action": "adjust_next_budget", "message": "ZZ-UNIQUE-GYM costs $43.21."},
                  {"action": "alert", "message": "ZZ-UNIQUE-GYM costs $43.21."})
        state = run(db, tmp_path, m=m, checkpointer=saver)
        assert state["status"] == "awaiting_user"
        blobs = b"".join(c if isinstance(c, bytes) else str(c).encode()
                         for table in ("checkpoints", "writes") for row in conn.execute(f"select * from {table}") for c in row)
        assert b"waiting_for" in blobs
        assert b"ZZ-UNIQUE-GYM" not in blobs and b"43.21" not in blobs
        assert state["signals"] == [] and state["signal_actions"] == {}
        snapshot = budget_graph.build_budget_graph(db, model=None, gateway=None, checkpointer=saver).get_state(
            budget_graph._thread(state["run_id"])).values
        assert snapshot["signals"] == [] and snapshot["signal_actions"] == {}
        assert snapshot["facts"] == snapshot["contexts"] == {} and snapshot["flags"] == snapshot["results"] == []
        assert "4321" not in json.dumps(snapshot)
        alert_id = state["waiting_for"][0]
        assert resume_budget_run(db, state["run_id"], alert_id, "accepted", checkpointer=saver)["status"] == "ok"
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("ck.db*"))
    assert b"ZZ-UNIQUE-GYM" not in raw and b"43.21" not in raw


def test_summary_json_holds_ids_and_cents_only(db, tmp_path, monkeypatch):
    seed(db)
    quiet_pace(monkeypatch)
    run(db, tmp_path, m=model({"action": "alert", "message": "Secret words."}))
    summary = rows(db, AgentRun)[0].summary_json
    assert summary["as_of"] == DAY.isoformat()
    assert summary["streams"] == {"count": 1, "new": [], "price_up": [rows(db, RecurringStream)[0].id], "income_change": []}
    assert "FitClub" not in json.dumps(summary) and "Secret words" not in json.dumps(summary)


@pytest.mark.parametrize("reply", [AIMessage(content="not json"), ConnectionError("private outage details")])
def test_a_bad_or_unreachable_model_still_alerts_without_retry(db, tmp_path, monkeypatch, reply):
    seed(db)
    quiet_pace(monkeypatch)
    m = ScriptedChatModel(replies=[reply])
    state = run(db, tmp_path, m=m)
    [alert] = rows(db, Alert)
    assert state["status"] == "ok" and state["llm_failures"] == 1 and len(m.seen) == 1
    assert alert.action == "alert" and "$40.00" in alert.message and "$43.00" in alert.message


def test_a_signal_decision_exception_still_writes_a_plain_alert(db, tmp_path, monkeypatch):
    seed(db)
    quiet_pace(monkeypatch)
    def fail(*a, **kw):
        raise RuntimeError("private decision details")
    monkeypatch.setattr(budget_graph, "decide_signal", fail)
    state = run(db, tmp_path)
    [alert] = rows(db, Alert)
    assert state["status"] == "ok" and alert.action == "alert"
    summary = rows(db, AgentRun)[0].summary_json
    assert summary["signals_error"] == "RuntimeError" and "private" not in json.dumps(summary)


def test_signal_preparation_failure_does_not_stop_either_alert(db, tmp_path, monkeypatch):
    seed(db)
    with db() as s:
        s.query(BudgetLine).one().limit_cents = 1000
        s.commit()
    def fail(*a):
        raise RuntimeError("private ranking details")
    monkeypatch.setattr(budget_graph, "rank_signals", fail)
    state = run(db, tmp_path, m=model({"cause": "one_time", "evidence": "Charge."},
                                    {"action": "alert", "message": "Over budget."}))
    assert state["status"] == "ok" and {a.kind for a in rows(db, Alert)} == {"overspend", "price_increase"}
    assert rows(db, AgentRun)[0].summary_json["signals_error"] == "RuntimeError"


def test_price_message_uses_the_change_even_after_the_stored_previous_is_cleared(db, tmp_path, monkeypatch):
    seed(db)
    quiet_pace(monkeypatch)
    with db() as s:
        stream = stored_streams(s, UID)[0]
        stream.previous_cents, stream.last_cents = None, 4300
        stream_id = stream.id
        s.commit()
    monkeypatch.setattr(budget_graph, "refresh_streams", lambda *a: [StreamChange("price_increase", stream_id, 4000, DAY.isoformat())])
    run(db, tmp_path, m=model({"action": "alert", "message": "Changed."}))
    assert "$40.00" in rows(db, Alert)[0].message and "$43.00" in rows(db, Alert)[0].message


def test_a_valid_model_message_is_kept(db, tmp_path, monkeypatch):
    seed(db)
    quiet_pace(monkeypatch)
    message = "FitClub Gym now costs $43.00 instead of $40.00. Check whether you still use it."
    run(db, tmp_path, m=model({"action": "alert", "message": message}))
    assert rows(db, Alert)[0].message == message


def test_an_income_change_alert_never_names_the_employer_or_suggests_cancel(db, tmp_path, monkeypatch):
    seed(db, category="INCOME")
    quiet_pace(monkeypatch)
    with db() as s:
        stream = stored_streams(s, UID)[0]
        stream.direction, stream.merchant_name, stream.last_cents = "in", "Secret Employer", 3000
        stream_id = stream.id
        s.commit()
    monkeypatch.setattr(budget_graph, "refresh_streams", lambda *a: [StreamChange("income_change", stream_id, 4000, DAY.isoformat())])
    run(db, tmp_path, m=model({"action": "suggest_cancel", "message": "Cancel Secret Employer."}))
    [alert] = rows(db, Alert)
    assert alert.kind == "income_change" and alert.action == "alert"
    assert "$40.00" in alert.message and "$30.00" in alert.message and "Secret Employer" not in alert.message
    assert "Secret Employer" not in (tmp_path / "audit.jsonl").read_text()
