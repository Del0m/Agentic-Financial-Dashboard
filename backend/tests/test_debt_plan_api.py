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
