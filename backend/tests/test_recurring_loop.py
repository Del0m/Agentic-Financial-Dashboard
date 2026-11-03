from datetime import date

import pytest

from app import config
from app.api import budget_hooks
from app.budget_engine.recurring import monthly_cents
from app.budget_engine.signals import cancel_limit
from app.budget_service.alerts import decide_alert
from app.budget_service.simulate import advance_days
from app.db.models import Alert, BudgetPeriod
from eval.budget_users import RECURRING_USERS, seed_profile
from tests.fakes import SchemaReplyModel


@pytest.fixture
def month(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(config, "DEV_USER_EMAIL", "recurring-planted@eval.local")
    monkeypatch.setattr(budget_hooks, "session_factory", db)
    with db() as s:
        uid = seed_profile(s, RECURRING_USERS[0], until=date(2026, 9, 30))
    model, days = SchemaReplyModel(signal_action="suggest_cancel"), {}
    def record(day):
        with db() as s:
            days[day["date"]] = [(a.kind, a.message) for a in s.query(Alert).filter_by(user_id=uid)]
    advance_days(db, uid, 25, model=model, checkpointer=None, on_day=record)
    return uid, model, days


def test_every_planted_stream_is_detected(client, month):
    body = client.get("/recurring").json()
    assert {r["label"] for r in body["streams"]} == {"Landlord", "Income", "Streamly", "FitClub Gym", "Clipflix", "NewFlix"}
    assert next(r for r in body["streams"] if r["direction"] == "in")["frequency"] == "biweekly"


def test_the_gym_price_alert_fires_on_the_run_after_the_higher_charge(month):
    _, _, days = month
    assert not any(k == "price_increase" for k, _ in days["2026-10-02"])
    for day in ("2026-10-03", "2026-10-25"):
        messages = [m for k, m in days[day] if k == "price_increase" and "FitClub Gym" in m]
        assert len(messages) == 1 and "$40.00" in messages[0] and "$43.00" in messages[0]


def test_a_rise_over_ten_percent_also_alerts(month):
    _, _, days = month
    assert not any(k == "price_increase" and "Clipflix" in m for k, m in days["2026-10-19"])
    assert len([m for k, m in days["2026-10-20"] if k == "price_increase" and "Clipflix" in m]) == 1


def test_the_new_subscription_alert_fires_when_its_third_charge_posts(month):
    _, _, days = month
    assert not any(k == "new_subscription" for k, _ in days["2026-10-11"])
    [message] = [m for k, m in days["2026-10-12"] if k == "new_subscription"]
    assert "NewFlix" in message
    assert len([m for k, m in days["2026-10-25"] if k == "new_subscription"]) == 1


def test_a_merchant_with_two_visits_is_never_a_stream(client, month):
    assert not any(name in client.get("/recurring").text for name in ("Pottery Studio", "Grocer", "Fuel"))


def test_accepting_a_cancel_suggestion_reaches_next_months_line(client, db, month):
    uid, model, _ = month
    with db() as s:
        alert = next(a for a in s.query(Alert).filter_by(user_id=uid, kind="price_increase") if "Clipflix" in a.message)
        assert alert.action == "suggest_cancel"
        alert_id = alert.id
    decide_alert(db, uid, alert_id, "accepted")
    advance_days(db, uid, 7, model=model, checkpointer=None)
    with db() as s:
        period = s.query(BudgetPeriod).filter_by(user_id=uid, start_date=date(2026, 11, 1)).one()
        line = next(ln for ln in period.lines if ln.category == "ENTERTAINMENT")
        from app.db.models import AgentRun
        [outcome] = s.query(AgentRun).filter_by(user_id=uid, graph="close").one().summary_json["cancel_outcomes"]
        assert line.limit_cents == outcome["new_cents"] == cancel_limit(outcome["old_cents"], monthly_cents(1799, "monthly"))
