from datetime import date

from app.db.models import Goal
from tests.factories import add_user
from tests.test_dashboard import dev_user_id


def goal(s, user_id, priority, status="proposed", **over):
    fields = dict(kind="savings", monthly_cents=10000, target_cents=120000, target_date=date(2027, 11, 1),
                  strategy=None, rationale="Save a little.")
    s.add(Goal(user_id=user_id, priority=priority, status=status, **{**fields, **over}))


def test_own_proposed_goals_by_priority(client, db):
    uid = dev_user_id(db)
    with db() as s:
        goal(s, uid, 2)
        goal(s, uid, 1, kind="debt_payoff", monthly_cents=20000, target_cents=2160000, target_date=None,
             strategy="avalanche", rationale="Pay the card first.")
        s.commit()
    body = client.get("/goals").json()
    assert [g["priority"] for g in body] == [1, 2]
    first = {k: v for k, v in body[0].items() if k != "id"}
    assert first == {"kind": "debt_payoff", "priority": 1, "monthly_cents": 20000, "target_cents": 2160000,
                     "target_date": None, "strategy": "avalanche", "rationale": "Pay the card first.",
                     "status": "proposed"}
    assert body[1]["target_date"] == "2027-11-01"


def test_other_users_and_superseded_hidden(client, db):
    uid = dev_user_id(db)
    with db() as s:
        add_user(s, "u2", "u2@x")
        goal(s, "u2", 1)
        goal(s, uid, 1, status="superseded")
        s.commit()
    assert client.get("/goals").json() == []


def test_no_goals_is_an_empty_list(client, db):
    dev_user_id(db)
    r = client.get("/goals")
    assert (r.status_code, r.json()) == (200, [])
