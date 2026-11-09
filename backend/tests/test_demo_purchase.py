from datetime import date

import pytest

from app.budget_service.alerts import SEED_TOKEN
from app.db.models import PlaidItem, Transaction, User
from app.plaid_service.client import PlaidError
from app.security import encrypt
from eval.budget_users import PLANTED, seed_profile
from scripts import demo_purchase


@pytest.fixture
def plaid_user(db):
    with db() as s:
        s.add(User(id="u-dev", email="dev@localhost", password_hash="!"))
        s.commit()
    return "u-dev"


def link(db, user_id, n, token_enc=None, status="active"):
    with db() as s:
        item = PlaidItem(user_id=user_id, plaid_item_id=f"item-{n}", access_token_enc=token_enc or encrypt(f"access-{n}"),
                         institution_name="First Platypus Bank", status=status)
        s.add(item)
        s.commit()
        return item.id


@pytest.fixture
def plaid(monkeypatch):
    calls, replies = [], []

    def fake_post(path, body):
        calls.append((path, body))
        reply = replies.pop(0) if replies else {}
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr("app.plaid_service.client.plaid_post", fake_post)
    return calls, replies


def test_push_sends_the_purchase_to_the_bank_that_takes_it(db, plaid_user, plaid):
    calls, replies = plaid
    old = link(db, plaid_user, 1)
    new = link(db, plaid_user, 2)
    replies.append(PlaidError(400, "INVALID_FIELD", "not a dynamic user"))   # the newest bank refuses
    days = [date(2026, 9, 7), date(2026, 10, 7), date(2026, 11, 7)]
    assert demo_purchase.push_to_plaid(db, plaid_user, "Streamly subscription", 1549, days) == (old, "First Platypus Bank")
    assert [path for path, _ in calls] == ["/sandbox/transactions/create"] * 2
    tokens = [body["access_token"] for _, body in calls]
    assert tokens == ["access-2", "access-1"]                               # newest first, decrypted
    # every date in one call: plaid rate-limits this endpoint
    assert calls[1][1]["transactions"] == [{"date_transacted": d.isoformat(), "date_posted": d.isoformat(), "amount": 15.49,
                                            "description": "Streamly subscription", "iso_currency_code": "USD"} for d in days]
    assert new != old


def test_push_skips_seeded_and_inactive_banks(db, plaid_user, plaid):
    link(db, plaid_user, 1, token_enc=SEED_TOKEN)
    link(db, plaid_user, 2, status="sync_failed")
    with pytest.raises(demo_purchase.PlaidPushError, match="no bank linked through Plaid"):
        demo_purchase.push_to_plaid(db, plaid_user, "Barber haircut", 3500, [date(2026, 11, 7)])
    assert plaid[0] == []


def test_push_explains_when_no_bank_takes_new_transactions(db, plaid_user, plaid):
    calls, replies = plaid
    link(db, plaid_user, 1)
    link(db, plaid_user, 2, token_enc="not-a-fernet-token")
    replies.append(PlaidError(400, "INVALID_FIELD", "not a dynamic user"))
    with pytest.raises(demo_purchase.PlaidPushError) as err:
        demo_purchase.push_to_plaid(db, plaid_user, "Barber haircut", 3500, [date(2026, 11, 7)])
    assert "InvalidToken" in str(err.value) and "INVALID_FIELD" in str(err.value) and "--dynamic" in str(err.value)
    assert len(calls) == 1          # the undecryptable token never reached plaid


def test_push_stops_at_the_rate_limit(db, plaid_user, plaid):
    calls, replies = plaid
    link(db, plaid_user, 1)
    link(db, plaid_user, 2)
    replies.append(PlaidError(429, "SANDBOX_TRANSACTIONS_CREATE_LIMIT", "rate limit exceeded"))
    with pytest.raises(demo_purchase.PlaidPushError, match="rate limit"):
        demo_purchase.push_to_plaid(db, plaid_user, "Barber haircut", 3500, [date(2026, 11, 7)])
    assert len(calls) == 1


def test_main_plaid_sends_a_preset_for_the_plaid_user_and_writes_nothing(db, plaid_user, plaid, capsys):
    calls, _ = plaid
    link(db, plaid_user, 1)
    assert demo_purchase.main(["laptop", "--plaid", "--date", "2026-11-07"], session_factory=db) == 0
    body = calls[0][1]
    assert (body["transactions"][0]["description"], body["transactions"][0]["amount"]) == ("ElectroHub laptop", 1200.0)
    assert body["transactions"][0]["date_posted"] == "2026-11-07"
    out = capsys.readouterr().out
    assert "sent to Plaid Sandbox: ElectroHub laptop $1,200.00 on 2026-11-07" in out
    with db() as s:
        assert s.query(Transaction).count() == 0          # the app's sync brings it in, not this script


def test_main_plaid_custom_purchase_needs_no_category(db, plaid_user, plaid, capsys):
    link(db, plaid_user, 1)
    assert demo_purchase.main(["--plaid", "--merchant", "Sushi Go", "--amount", "64.20", "--date", "2026-11-07"],
                              session_factory=db) == 0
    assert plaid[0][0][1]["transactions"][0]["description"] == "Sushi Go"


def test_main_plaid_without_a_bank_says_how_to_link(db, plaid_user, plaid, capsys):
    assert demo_purchase.main(["laptop", "--plaid"], session_factory=db) == 1
    assert "sandbox_link.py --dynamic" in capsys.readouterr().out


def test_main_plaid_cannot_undo(db, plaid_user, plaid, capsys):
    assert demo_purchase.main(["--plaid", "--undo"], session_factory=db) == 1
    assert "can't take a transaction back" in capsys.readouterr().out


def test_main_without_plaid_still_writes_to_the_seeded_user(db, plaid, capsys):
    with db() as s:
        uid = seed_profile(s, next(p for p in PLANTED if p.id == "overspend-recurring"))
    assert demo_purchase.main(["coffee", "--user", uid, "--date", "2026-10-05"], session_factory=db) == 0
    with db() as s:
        row = s.query(Transaction).filter(Transaction.plaid_txn_id.like("demo-purchase-%")).one()
        assert (row.merchant_name, row.amount_cents, row.date) == ("Blue Bottle Cafe", 650, date(2026, 10, 5))
    assert plaid[0] == []

