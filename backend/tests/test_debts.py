from app.budget_service.debts import load_debts
from app.db.models import Account
from tests.factories import add_account, add_user

CARD = {"kind": "credit", "apr": 24.99, "min_payment_cents": 9500}


# the liability row keeps plaid's snapshot ($3,000); the account's current balance moved to $3,200
def test_balance_comes_from_the_account(db):
    with db() as s:
        item = add_user(s, "u1", "u1@x")
        add_account(s, item, "Checking", balance=50000)
        card = add_account(s, item, "Platinum Card", type_="credit", balance=300000, liability=CARD)
        s.get(Account, card.id).current_balance_cents = 320000
        s.commit()
        debts = load_debts(s, "u1")
    assert [(d.key, d.name, d.kind, d.balance_cents, d.apr, d.min_payment_cents) for d in debts] == \
        [(card.id, "Platinum Card", "credit", 320000, 24.99, 9500)]


def test_other_users_debts_not_loaded(db):
    with db() as s:
        add_user(s, "u1", "u1@x")
        add_account(s, add_user(s, "u2", "u2@x"), "Their Card", type_="credit", balance=100000, liability=CARD)
        s.commit()
        assert load_debts(s, "u1") == []


# an overpaid card loads as it is; the planner decides it has nothing to pay off
def test_credit_balance_kept_for_the_engine(db):
    with db() as s:
        add_account(s, add_user(s, "u1", "u1@x"), "Card", type_="credit", balance=-5000, liability=CARD)
        s.commit()
        assert [d.balance_cents for d in load_debts(s, "u1")] == [-5000]
