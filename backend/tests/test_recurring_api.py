from datetime import date, datetime, timezone

from fastapi import HTTPException

from app.api import budget_hooks
from app.api.deps import get_current_user
from app.db.models import AgentRun, Alert, RecurringStream, User
from app.main import app
from tests.test_dashboard import dev_user_id

DAY = date(2026, 10, 25)


def stream(s, merchant="Streamly", uid="dev", direction="out", status="active", next_day=date(2026, 11, 5)):
    row = RecurringStream(user_id=uid, direction=direction, merchant_key=merchant.lower(), merchant_name=merchant,
                          category="INCOME" if direction == "in" else "ENTERTAINMENT", frequency="monthly",
                          typical_cents=1549, last_cents=1799, previous_cents=1549, amount_varies=False,
                          first_date=date(2026, 5, 5), last_date=date(2026, 10, 5), next_date=next_day,
                          gap_days=31, status=status, first_seen_date=date(2026, 9, 1))
    s.add(row)
    s.flush()
    return row


def seed(db):
    dev_user_id(db)
    with db() as s:
        s.add(AgentRun(user_id="dev", graph="budget", status="ok", summary_json={"as_of": DAY.isoformat()}))
        row = stream(s)
        s.commit()
        return row.id


def test_recurring_lists_changed_then_active_then_stale(client, db):
    sid = seed(db)
    with db() as s:
        stream(s, "Early", next_day=date(2026, 11, 1))
        stream(s, "Stopped", status="stale")
        s.add(Alert(user_id="dev", stream_id=sid, kind="price_increase", severity="medium", action="alert",
                    message="Price up.", status="pending_user", created_at=datetime(2026, 10, 20, tzinfo=timezone.utc)))
        s.commit()
    body = client.get("/recurring").json()
    assert body["as_of"] == DAY.isoformat()
    assert [r["label"] for r in body["streams"]] == ["Streamly", "Early", "Stopped"]


def test_an_inflow_label_is_income_and_never_the_employer(client, db):
    seed(db)
    with db() as s:
        stream(s, "Secret Employer", direction="in")
        s.commit()
    body = client.get("/recurring").json()
    assert next(r for r in body["streams"] if r["direction"] == "in")["label"] == "Income"
    assert "Secret Employer" not in str(body)


def test_a_user_with_no_history_gets_an_empty_list(client, db):
    dev_user_id(db)
    r = client.get("/recurring")
    assert r.status_code == 200 and r.json() == {"as_of": None, "streams": []}


def test_the_get_writes_nothing(client, db):
    seed(db)
    with db() as s:
        before = [s.query(cls).count() for cls in (RecurringStream, AgentRun, Alert)]
    assert client.get("/recurring").status_code == 200
    with db() as s:
        assert before == [s.query(cls).count() for cls in (RecurringStream, AgentRun, Alert)]


def test_another_users_streams_are_not_visible(client, db):
    seed(db)
    with db() as s:
        s.add(User(id="other", email="other@x", password_hash="!"))
        stream(s, "Private stream", uid="other")
        s.commit()
    assert "Private stream" not in client.get("/recurring").text


def test_a_price_increase_alert_marks_the_stream(client, db):
    sid = seed(db)
    with db() as s:
        row = s.get(RecurringStream, sid)
        row.previous_cents, row.last_cents = 4000, 4300
        s.add(Alert(user_id="dev", stream_id=sid, kind="price_increase", severity="medium", action="alert",
                    message="Price up.", status="pending_user", created_at=datetime(2026, 10, 20, tzinfo=timezone.utc)))
        s.commit()
    [row] = client.get("/recurring").json()["streams"]
    assert (row["change"], row["previous_cents"], row["last_cents"]) == ("price_increase", 4000, 4300)


def test_requires_auth(client, db):
    def denied():
        raise HTTPException(401, "Not authenticated")
    app.dependency_overrides[get_current_user] = denied
    assert client.get("/recurring").status_code == 401


def test_alerts_out_carries_stream_id(client, db, monkeypatch):
    sid = seed(db)
    monkeypatch.setattr(budget_hooks, "session_factory", db)
    with db() as s:
        s.add(Alert(user_id="dev", stream_id=sid, kind="price_increase", severity="medium", action="alert",
                    message="Price up.", status="pending_user"))
        s.commit()
    assert client.get("/alerts").json()[0]["stream_id"] == sid


def test_change_badges_use_simulated_run_dates_and_expire(client, db):
    sid = seed(db)
    with db() as s:
        run = AgentRun(user_id="dev", graph="budget", status="ok", summary_json={"as_of": "2026-10-03"})
        s.add(run)
        s.flush()
        alert = Alert(user_id="dev", stream_id=sid, kind="price_increase", severity="medium", action="alert",
                      message="Price up.", status="pending_user")
        s.add(alert)
        s.flush()
        from app.db.models import AdviceLog
        s.add(AdviceLog(user_id="dev", run_id=run.id, alert_id=alert.id, advice_text="Price up."))
        s.add(AgentRun(user_id="dev", graph="budget", status="ok", summary_json={"as_of": "2026-11-10"}))
        s.commit()
    assert client.get("/recurring").json()["streams"][0]["change"] is None
