from datetime import date, timedelta

from sqlalchemy import select

from app.budget_service.recurring import load_rtxns, refresh_streams, stored_streams
from app.db.models import Account, RecurringStream, Transaction
from eval.budget_users import GOAL_USERS, RECURRING_USERS, seed_profile
from tests.factories import add_account, add_txn, add_user

UID = "u1"


def account(s):
    return add_account(s, add_user(s, UID, f"{UID}@test.local"), "Checking")


# one charge per month on `day`, from `first`; `amounts` are plaid-signed
def monthly(db, amounts, first=date(2026, 1, 5), merchant="NewFlix", category="ENTERTAINMENT", detailed=None):
    days = []
    with db() as s:
        acct = s.scalar(select(Account).where(Account.user_id == UID)) or account(s)
        year, month = first.year, first.month
        for cents in amounts:
            day = date(year, month, first.day)
            add_txn(s, acct, cents, category, merchant=merchant, day=day, detailed=detailed)
            days.append(day)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        s.commit()
    return days


def refresh(db, day):
    with db() as s:
        changes = refresh_streams(s, UID, day, None)
        s.commit()
    return changes


def refresh_on_each(db, days):
    return [c for day in days for c in refresh(db, day)]


def row(db, merchant_key="newflix", direction="out", uid=UID):
    with db() as s:
        return s.scalar(select(RecurringStream).where(RecurringStream.user_id == uid,
                                                      RecurringStream.direction == direction,
                                                      RecurringStream.merchant_key == merchant_key))


def seed(db, profile):
    with db() as s:
        return seed_profile(s, profile)


def test_first_refresh_stores_streams_and_raises_nothing(db):
    uid = seed(db, GOAL_USERS[0])
    with db() as s:
        assert refresh_streams(s, uid, date(2026, 11, 2), None) == []
        s.commit()
        names = {r.merchant_name for r in stored_streams(s, uid)}
    assert names == {"Landlord", "Employer", "Savings", "Streamly", "Platinum Card", "Student Loan"}


def test_a_second_refresh_the_same_day_changes_nothing(db):
    uid = seed(db, GOAL_USERS[0])
    day = date(2026, 11, 2)
    with db() as s:
        refresh_streams(s, uid, day, None)
        s.commit()
        before = [(r.id, r.last_date, r.status) for r in stored_streams(s, uid)]
        assert refresh_streams(s, uid, day, None) == []
        s.commit()
        assert [(r.id, r.last_date, r.status) for r in stored_streams(s, uid)] == before


def test_a_subscription_that_starts_inside_the_history_is_new(db):
    monthly(db, [1000], first=date(2026, 1, 1), merchant="Shop", category="GENERAL_SERVICES")
    monthly(db, [999, 999, 999], first=date(2026, 3, 10))
    changes = refresh(db, date(2026, 5, 11))
    assert [c.kind for c in changes] == ["new_subscription"]
    assert changes[0].stream_id == row(db).id


def test_a_subscription_older_than_the_history_is_not_new(db):
    monthly(db, [999, 999, 999], first=date(2026, 3, 10))
    assert refresh(db, date(2026, 5, 11)) == []


def test_a_savings_transfer_is_stored_but_never_new(db):
    monthly(db, [1000], first=date(2026, 1, 1), merchant="Shop", category="GENERAL_SERVICES")
    monthly(db, [50000] * 3, first=date(2026, 3, 16), merchant="Savings", category="TRANSFER_OUT",
            detailed="TRANSFER_OUT_SAVINGS")
    assert refresh(db, date(2026, 5, 20)) == []
    assert row(db, "savings").category == "SAVINGS_TRANSFER"


def _gym_changes(db, uid, first, last):
    out, day = [], first
    with db() as s:
        gym = None
        while day <= last:
            changes = refresh_streams(s, uid, day, None)
            s.commit()
            gym = gym or s.scalar(select(RecurringStream.id).where(RecurringStream.user_id == uid,
                                                                   RecurringStream.merchant_key == "fitclub gym"))
            out += [(day, c) for c in changes if c.stream_id == gym]
            day += timedelta(days=1)
    return out


def test_price_increase_fires_once_per_new_price(db):
    uid = seed(db, RECURRING_USERS[0])
    changes = _gym_changes(db, uid, date(2026, 10, 2), date(2026, 11, 3))
    assert [(d, c.kind, c.previous_cents, c.token) for d, c in changes] == [
        (date(2026, 10, 3), "price_increase", 4000, "2026-10-03")]


def test_a_rise_of_16_percent_also_fires(db):
    uid = seed(db, RECURRING_USERS[0])
    with db() as s:
        refresh_streams(s, uid, date(2026, 10, 19), None)
        s.commit()
        clipflix = s.scalar(select(RecurringStream.id).where(RecurringStream.merchant_key == "clipflix"))
        changes = refresh_streams(s, uid, date(2026, 10, 20), None)
    assert [(c.kind, c.stream_id, c.previous_cents) for c in changes] == [("price_increase", clipflix, 1549)]


def test_a_charge_under_5_percent_or_under_a_dollar_does_not(db):
    days = monthly(db, [3000, 3000, 3000, 3100])
    assert refresh_on_each(db, days) == []
    days = monthly(db, [500, 500, 500, 560], merchant="Tiny")
    assert refresh_on_each(db, days) == []


def test_a_price_step_stays_active_and_alerts_once(db):
    changes = refresh_on_each(db, monthly(db, [3000] * 4 + [3450, 3450]))
    assert [c.kind for c in changes] == ["price_increase"]
    assert (row(db).status, row(db).typical_cents, row(db).last_cents) == ("active", 3450, 3450)


def test_a_price_that_falls_back_and_rises_again_alerts_twice(db):
    changes = refresh_on_each(db, monthly(db, [3000, 3000, 3000, 3450, 3000, 3000, 3000, 3450]))
    assert [(c.kind, c.token) for c in changes] == [("price_increase", "2026-04-05"), ("price_increase", "2026-08-05")]


def test_a_raise_on_a_paycheck_is_not_a_price_increase(db):
    days = monthly(db, [-200000, -200000, -200000, -212000], merchant="Employer", category="INCOME")
    assert refresh_on_each(db, days) == []


def test_a_growing_card_payment_is_not_a_price_increase(db):
    days = monthly(db, [20000, 20000, 20000, 30000], merchant="Platinum Card", category="LOAN_PAYMENTS",
                   detailed="LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
    assert refresh_on_each(db, days) == []


def test_a_winter_electric_bill_is_not_a_price_increase(db):
    days = monthly(db, [9000, 9200, 9100, 11500], merchant="City Power", category="RENT_AND_UTILITIES",
                   detailed="RENT_AND_UTILITIES_GAS_AND_ELECTRICITY")
    assert refresh_on_each(db, days) == []


def test_a_phone_bill_rise_and_a_rent_rise_still_fire(db):
    phone = monthly(db, [6500, 6500, 6500, 7500], merchant="Mobile Co", category="RENT_AND_UTILITIES",
                    detailed="RENT_AND_UTILITIES_TELEPHONE")
    rent = monthly(db, [150000, 150000, 150000, 165000], first=date(2026, 1, 6), merchant="Landlord",
                   category="RENT_AND_UTILITIES", detailed="RENT_AND_UTILITIES_RENT")
    assert [c.kind for c in refresh_on_each(db, sorted(phone + rent))] == ["price_increase", "price_increase"]


def _pay(db, months, day=15, cents=-420000):
    days = []
    with db() as s:
        acct = s.scalar(select(Account).where(Account.user_id == UID)) or account(s)
        for m in months:
            add_txn(s, acct, cents, "INCOME", merchant="Employer", day=date(2026, m, day))
            days.append(date(2026, m, day))
        s.commit()
    return days


def test_a_skipped_paycheck_month_raises_nothing(db):
    days = _pay(db, (3, 4, 5, 7, 8))
    changes = refresh_on_each(db, [days[2], date(2026, 6, 20), days[3], days[4]])
    assert changes == []
    assert row(db, "employer", "in").status == "active"


def test_income_change_when_the_paycheck_goes_stale(db):
    days = _pay(db, (3, 4, 5))
    refresh(db, days[2])
    assert refresh(db, date(2026, 7, 19)) == []
    changes = refresh(db, date(2026, 7, 20))
    assert [(c.kind, c.token) for c in changes] == [("income_change", "2026-07-20")]
    assert refresh(db, date(2026, 7, 21)) == []


def test_a_pay_cut_is_an_income_change_on_its_second_charge(db):
    days = monthly(db, [-420000] * 4 + [-370000, -370000], merchant="Employer", category="INCOME")
    changes = refresh_on_each(db, days)
    assert [(c.kind, c.previous_cents, c.token) for c in changes] == [("income_change", 420000, days[-1].isoformat())]


def test_a_returning_paycheck_is_the_same_row_and_silent(db):
    days = _pay(db, (3, 4, 5))
    refresh(db, days[2])
    first_id = row(db, "employer", "in").id
    assert len(refresh(db, date(2026, 7, 20))) == 1
    back = _pay(db, (8, 9, 10), day=1)
    assert refresh(db, back[0]) == []
    assert refresh(db, back[1]) == []
    assert row(db, "employer", "in").status == "stale"
    assert refresh(db, back[2]) == []
    returned = row(db, "employer", "in")
    assert (returned.id, returned.status) == (first_id, "active")


def test_a_missing_stream_is_marked_stale_not_deleted(db):
    days = monthly(db, [999] * 3, first=date(2026, 3, 10))
    refresh(db, days[-1])
    with db() as s:
        for t in s.scalars(select(Transaction).where(Transaction.user_id == UID)):
            t.removed = True
        s.commit()
    assert refresh(db, days[-1] + timedelta(days=1)) == []
    assert row(db).status == "stale"


def test_a_roommate_transfer_going_stale_raises_no_income_change(db):
    days = monthly(db, [-60000] * 3, first=date(2026, 3, 1), merchant="Sam", category="TRANSFER_IN")
    refresh(db, days[-1])
    assert refresh(db, date(2026, 7, 20)) == []
    assert row(db, "sam", "in").status == "stale"


def test_pending_removed_and_future_rows_are_ignored(db):
    days = monthly(db, [999] * 3, first=date(2026, 3, 10))
    with db() as s:
        t = s.scalar(select(Transaction).where(Transaction.date == days[2]))
        t.pending = True
        s.commit()
        assert len(load_rtxns(s, UID, days[2])) == 2
    assert refresh(db, days[2]) == []
    assert row(db) is None
    with db() as s:
        s.scalar(select(Transaction).where(Transaction.date == days[2])).pending = False
        s.scalar(select(Transaction).where(Transaction.date == days[1])).removed = True
        s.commit()
        assert len(load_rtxns(s, UID, days[2])) == 2
        s.scalar(select(Transaction).where(Transaction.date == days[1])).removed = False
        s.commit()
        assert len(load_rtxns(s, UID, days[1])) == 2


def test_signal_key_tokens(db):
    monthly(db, [1000], first=date(2026, 1, 1), merchant="Shop", category="GENERAL_SERVICES")
    days = monthly(db, [3000, 3000, 3000, 3450], first=date(2026, 3, 10))
    changes = refresh_on_each(db, days)
    assert [(c.kind, c.token) for c in changes] == [("new_subscription", "first_seen"),
                                                    ("price_increase", days[-1].isoformat())]


def test_plaid_asks_for_180_days(monkeypatch):
    from app.plaid_service import client
    bodies = []
    monkeypatch.setattr(client, "plaid_post", lambda path, body: bodies.append(body) or {"public_token": "t",
                                                                                          "link_token": "t"})
    client.create_link_token("u1")
    client.sandbox_public_token()
    assert bodies[0]["transactions"]["days_requested"] == 180
    assert bodies[1]["options"]["transactions"]["days_requested"] == 180


def test_price_increase_fires_at_exactly_5_percent_and_1_dollar(db):
    days = monthly(db, (2000, 2000, 2000))
    refresh(db, days[-1])
    day = monthly(db, (2100,), first=date(2026, 4, 5))[0]
    assert [(c.kind, c.previous_cents) for c in refresh(db, day)] == [("price_increase", 2000)]
