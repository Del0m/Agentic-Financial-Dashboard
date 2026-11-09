from datetime import date

import pytest

from app.api import debt_plan
from app.budget_engine.payoff import compare
from app.budget_service.debts import load_debts
from tests.factories import add_account
from tests.test_dashboard import dev_user_id
from app.db.models import PlaidItem

PLAN_START = date(2026, 12, 1)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(debt_plan, "_today", lambda: date(2026, 11, 1))


def seed_debts(db, *cards):
    uid = dev_user_id(db)
    with db() as s:
        item = PlaidItem(user_id=uid, plaid_item_id="item-dev", access_token_enc="x")
        s.add(item)
        s.flush()
        for name, balance, liability in cards:
            add_account(s, item, name, type_="credit", balance=balance, liability=liability)
        s.commit()
    return uid


CARD = ("Platinum Card", 320000, {"kind": "credit", "apr": 24.99, "min_payment_cents": 9500})
LOAN = ("Student Loan", 1840000, {"kind": "student", "apr": 5.5, "min_payment_cents": 21000})


def test_plan_matches_compare(client, db):
    uid = seed_debts(db, CARD, LOAN)
    r = client.post("/debt-plan", json={"extra_monthly_cents": 20000})
    assert r.status_code == 200
    body = r.json()
    with db() as s:
        expected = compare(load_debts(s, uid), 20000, start=PLAN_START)
    for strategy in ("avalanche", "snowball"):
        plan, want = body[strategy], expected[strategy]
        assert (plan["strategy"], plan["months"], plan["total_interest_cents"], plan["debt_free"]) == \
            (strategy, want.months, want.total_interest_cents, want.debt_free.isoformat())
        assert [(p["name"], p["month"], p["interest_cents"]) for p in plan["order"]] == \
            [(p.name, p.month, p.interest_cents) for p in want.order]
        assert body["baseline"][strategy]["months"] == expected[f"baseline_{strategy}"].months
    assert body["start"] == "2026-12-01" and body["avalanche"]["order"][0]["name"] == "Platinum Card"
    assert body["excluded"] == []


@pytest.mark.parametrize("extra", [-1, 100_000_001])
def test_extra_out_of_bounds_is_422(client, db, extra):
    dev_user_id(db)
    assert client.post("/debt-plan", json={"extra_monthly_cents": extra}).status_code == 422


def test_no_debts_gives_empty_plans(client, db):
    dev_user_id(db)
    body = client.post("/debt-plan", json={"extra_monthly_cents": 0}).json()
    assert (body["avalanche"]["months"], body["avalanche"]["order"], body["avalanche"]["never"], body["excluded"]) == \
        (0, [], [], [])
    assert body["avalanche"]["debt_free"] is None


def test_excluded_named(client, db):
    seed_debts(db, CARD, ("Store Card", 50000, {"kind": "credit", "apr": None, "min_payment_cents": 2500}))
    body = client.post("/debt-plan", json={"extra_monthly_cents": 0}).json()
    assert [(e["name"], e["reason"]) for e in body["excluded"]] == [("Store Card", "no_apr")]
    assert [p["name"] for p in body["avalanche"]["order"]] == ["Platinum Card"]


# a debt that's never paid off isn't in the payoff order, so the plan names it itself
def test_never_debt_named(client, db):
    seed_debts(db, ("Old Card", 1000000, {"kind": "credit", "apr": 30.0, "min_payment_cents": 10000}))
    body = client.post("/debt-plan", json={"extra_monthly_cents": 0}).json()
    assert (body["avalanche"]["months"], body["avalanche"]["total_interest_cents"]) == (None, None)
    assert [(n["name"], n["account_id"] > 0) for n in body["avalanche"]["never"]] == [("Old Card", True)]


def add_goal(db, uid, **overrides):
    from app.db.models import Goal
    values = dict(user_id=uid, kind='debt_payoff', status='accepted', priority=1, monthly_cents=60000,
                  target_cents=240000, start_date=date(2026,12,1), target_date=date(2027,3,1), rationale='test')
    values.update(overrides)
    with db() as s:
        goal = Goal(**values);s.add(goal);s.commit();return goal.id


def test_accepted_goal_is_included_once_and_extra_is_additional(client, db):
    from app.budget_engine.payoff import ScheduledExtra
    uid = seed_debts(db, ('Demo Card',240000, {'kind':'credit','apr':24,'min_payment_cents':7500}))
    gid = add_goal(db, uid)
    base = client.post('/debt-plan', json={'extra_monthly_cents':0}).json()
    assert base['goal_monthly_cents'] == 60000
    assert base['minimum_monthly_cents'] == 7500
    assert base['additional_extra_monthly_cents'] == 0
    assert base['avalanche']['months'] == 4
    assert base['goal_contributions'][0]['goal_id'] == gid
    extra = client.post('/debt-plan', json={'extra_monthly_cents':10000}).json()
    with db() as s:
        expected = compare(load_debts(s,uid), 10000, start=PLAN_START,
            scheduled_extras=[ScheduledExtra(60000,PLAN_START,date(2027,3,1))])
    assert extra['avalanche']['months'] == expected['avalanche'].months
    assert extra['avalanche']['total_interest_cents'] == expected['avalanche'].total_interest_cents
    assert extra['baseline']['avalanche'] == base['avalanche']


@pytest.mark.parametrize('overrides', [
    {'status':'proposed'}, {'status':'dismissed'}, {'status':'stopped'}, {'status':'completed'},
    {'kind':'emergency_fund'}, {'kind':'savings'}, {'target_date':date(2026,11,30)},
])
def test_non_funding_goals_are_not_included(client, db, overrides):
    uid = seed_debts(db, CARD)
    add_goal(db, uid, **overrides)
    body = client.post('/debt-plan', json={'extra_monthly_cents':0}).json()
    assert body['goal_monthly_cents'] == 0
    assert body['goal_contributions'] == []


def test_future_goal_is_scheduled_but_not_added_to_the_first_month(client, db):
    uid = seed_debts(db, CARD)
    add_goal(db, uid, start_date=date(2027,1,1))
    body = client.post('/debt-plan', json={'extra_monthly_cents':0}).json()
    assert body['goal_monthly_cents'] == 0
    assert body['goal_contributions'][0]['start_date'] == '2027-01-01'


def test_other_users_debt_goal_is_not_used(client, db):
    from app.db.models import User
    seed_debts(db, CARD)
    with db() as s:
        s.add(User(id='other-goal-owner',email='other-goal-owner@example.com',password_hash='!'));s.commit()
    add_goal(db,'other-goal-owner')
    body = client.post('/debt-plan',json={'extra_monthly_cents':0}).json()
    assert body['goal_contributions'] == []
