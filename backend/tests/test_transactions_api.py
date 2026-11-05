from datetime import date

import pytest

from app import config
from app.api import budget_hooks
from app.api import transactions as txn_api
from app.budget_service.close import close_due
from app.budget_service.initialize import initialize_budget
from app.db.models import Account, BudgetLine, BudgetPeriod, CategoryRule, Transaction
from eval.budget_users import PLANTED, seed_profile
from tests.factories import add_account, add_txn, add_user

OCT20 = date(2026, 10, 20)
GM = "GENERAL_MERCHANDISE"


@pytest.fixture
def api(client, db, monkeypatch):
    monkeypatch.setattr(budget_hooks, "session_factory", db)
    monkeypatch.setattr(config, "DEV_USER_EMAIL", "overspend-one-time@eval.local")
    monkeypatch.setattr(txn_api, "_today", lambda: OCT20)
    return client


# the planted one-time user (a $600 ElectroMart purchase on Oct 12, weekly Corner Shop), with October's budget
def seed(db, until=None):
    with db() as s:
        uid = seed_profile(s, next(p for p in PLANTED if p.id == "overspend-one-time"), until=until)
        initialize_budget(s, uid, until or OCT20)
        return uid


# the merchant's first transaction on or after `since`
def txn_id(db, merchant, since=date(2026, 10, 1)):
    with db() as s:
        return s.query(Transaction).filter(Transaction.merchant_name == merchant, Transaction.date >= since) \
            .order_by(Transaction.date).first().id


def by_merchant(rows):
    return {(r["merchant"], r["date"]): r for r in rows}


def test_list_month_with_sources(api, db):
    seed(db)
    with db() as s:
        s.add(CategoryRule(user_id="budget-overspend-one-time", merchant_name="corner shop", category=GM))
        s.commit()
    rows = api.get("/transactions", params={"month": "2026-10"}).json()
    assert [r["date"] for r in rows] == sorted((r["date"] for r in rows), reverse=True)
    assert all(r["date"].startswith("2026-10") for r in rows)
    got = by_merchant(rows)
    electro = got[("ElectroMart", "2026-10-12")]
    assert (electro["category"], electro["line_name"], electro["source"], electro["amount_cents"]) == (
        GM, "General merchandise", "plaid", 60000)
    corner = next(r for r in rows if r["merchant"] == "Corner Shop")
    assert (corner["source"], corner["line_name"]) == ("rule", "General merchandise")
    saving = next(r for r in rows if r["merchant"] == "Savings")
    assert (saving["category"], saving["line_name"]) == ("SAVINGS_TRANSFER", "Savings")
    pay = next(r for r in rows if r["merchant"] == "Employer")
    assert (pay["category"], pay["line_name"]) == ("INCOME", None)        # not spending: counts against no line


def test_list_defaults_to_sim_month(api, db):
    seed(db, until=date(2026, 10, 15))
    rows = api.get("/transactions").json()
    assert rows and all("2026-10-01" <= r["date"] <= "2026-10-15" for r in rows)


def test_correct_one(api, db):
    seed(db)
    tid = txn_id(db, "ElectroMart")
    r = api.patch(f"/transactions/{tid}", json={"category": "FREE"})
    assert r.status_code == 200
    assert (r.json()["category"], r.json()["line_name"], r.json()["source"]) == ("FREE", "Free", "user")
    with db() as s:
        assert s.get(Transaction, tid).user_category == "FREE"
        assert s.query(CategoryRule).count() == 0


def test_remember_creates_and_updates_rule(api, db):
    seed(db)
    tid = txn_id(db, "Corner Shop")
    api.patch(f"/transactions/{tid}", json={"category": "FREE", "remember": True})
    rows = api.get("/transactions", params={"month": "2026-10"}).json()
    others = [r for r in rows if r["merchant"] == "Corner Shop" and r["id"] != tid]
    assert others and all((r["category"], r["source"]) == ("FREE", "rule") for r in others)
    api.patch(f"/transactions/{tid}", json={"category": GM, "remember": True})
    with db() as s:
        [rule] = s.query(CategoryRule).all()
        assert (rule.merchant_name, rule.category) == ("corner shop", GM)


def test_override_beats_rule(api, db):
    seed(db)
    with db() as s:
        s.add(CategoryRule(user_id="budget-overspend-one-time", merchant_name="corner shop", category="FREE"))
        s.commit()
    tid = txn_id(db, "Corner Shop")
    row = api.patch(f"/transactions/{tid}", json={"category": GM}).json()
    assert (row["category"], row["source"]) == (GM, "user")


def test_category_must_be_a_line(api, db):
    seed(db)
    r = api.patch(f"/transactions/{txn_id(db, 'ElectroMart')}", json={"category": "TRAVEL"})
    assert (r.status_code, r.json()["detail"]) == (422, "Pick one of your budget lines.")


def test_remember_without_merchant(api, db):
    uid = seed(db)
    with db() as s:
        s.info["n"] = 100000
        add_txn(s, s.query(Account).filter_by(user_id=uid).one(), 1200, GM, merchant=None, day=date(2026, 10, 18))
        s.commit()
        tid = s.query(Transaction).filter_by(merchant_name=None).one().id
    r = api.patch(f"/transactions/{tid}", json={"category": "FREE", "remember": True})
    assert (r.status_code, r.json()["detail"]) == (422, "This transaction has no merchant to remember.")


# a correction to a month already closed is saved, but neither reopens that month nor moves the open one
def test_correct_closed_month_txn(api, db):
    uid = seed(db)
    close_due(db, uid, date(2026, 11, 1))
    with db() as s:
        before = {(p.start_date, ln.category): ln.limit_cents
                  for p in s.query(BudgetPeriod).all() for ln in p.lines}
    tid = txn_id(db, "ElectroMart")
    r = api.patch(f"/transactions/{tid}", json={"category": "FREE"})
    assert r.status_code == 200 and r.json()["category"] == "FREE"
    with db() as s:
        assert s.get(Transaction, tid).user_category == "FREE"
        periods = s.query(BudgetPeriod).order_by(BudgetPeriod.start_date).all()
        assert [p.status for p in periods] == ["closed", "open"]
        assert {(p.start_date, ln.category): ln.limit_cents for p in periods for ln in p.lines} == before


def test_other_users_txn_404(api, db):
    seed(db)
    with db() as s:
        acct = add_account(s, add_user(s, "u2", "u2@x"), "Checking")
        s.info["n"] = 100000
        add_txn(s, acct, 500, GM, day=date(2026, 10, 3))
        s.commit()
        theirs = s.query(Transaction).filter_by(user_id="u2").one().id
    r = api.patch(f"/transactions/{theirs}", json={"category": "FREE"})
    assert (r.status_code, r.json()["detail"]) == (404, "Not found.")


# a goal line has no spending to hold, so it isn't a place to put a purchase
def test_a_goal_line_is_not_a_correction_target(api, db):
    seed(db)
    with db() as s:
        period = s.query(BudgetPeriod).filter_by(status="open").one()
        s.add(BudgetLine(period_id=period.id, category="GOAL_DEBT_PAYOFF", name="Debt payoff", group="goal",
                         kind="fixed", limit_cents=58300, source="init"))
        s.commit()
    tid = txn_id(db, "ElectroMart")
    r = api.patch(f"/transactions/{tid}", json={"category": "GOAL_DEBT_PAYOFF"})
    assert (r.status_code, r.json()["detail"]) == (422, "Pick one of your budget lines.")
    with db() as s:
        assert s.get(Transaction, tid).user_category is None


@pytest.mark.parametrize("simulated", [False, True])
def test_rule_edit_date_survives_later_projection_retry(api, db, monkeypatch, simulated):
    from app.db.models import KGEdge, KGNode, User
    from app.kg import project
    from datetime import datetime, timezone
    uid = seed(db)
    tid = txn_id(db, "Corner Shop")
    created, edited, retried = date(2026, 9, 1), date(2026, 10, 15), date(2026, 10, 17)
    with db() as s:
        s.add(CategoryRule(user_id=uid, merchant_name="corner shop", category="FREE",
                           created_at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
        s.flush()
        project.project_rules(s, uid, created)
        project.project_run(s, uid, edited)
        if simulated:
            s.get(User, uid).prefs_json = {"sim": {"profile": "overspend-one-time", "date": edited.isoformat()}}
        s.commit()
    monkeypatch.setattr(project, "utcnow", lambda: datetime(2026, 10, 15, tzinfo=timezone.utc))
    with monkeypatch.context() as patch:
        patch.setattr(project, "safely", lambda *args: False)
        assert api.patch(f"/transactions/{tid}", json={"category": GM, "remember": True}).status_code == 200
    with db() as s:
        rule = s.query(CategoryRule).filter_by(user_id=uid, merchant_name="corner shop").one()
        assert rule.created_at.date() == created and rule.effective_from == edited
        project.project_rules(s, uid, retried)
        edges = s.query(KGEdge).filter_by(user_id=uid, rel="CATEGORIZED_AS", expired_at=None).order_by(KGEdge.valid_from).all()
        assert [(s.get(KGNode, e.dst_id).key, e.valid_from, e.valid_to) for e in edges] == [
            ("FREE", created, edited), (GM, edited, None)]
