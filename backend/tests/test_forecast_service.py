from datetime import date, timedelta

import pytest

from app.budget_service.forecast_data import everyday_series, forecast_accounts, load_card_obligations, start_balance
from app.budget_service.forecast_prefs import set_user_buffer, user_buffer
from app.budget_service.forecast_service import snapshot
from app.db.models import Account, AgentRun, Alert, RecurringStream, Transaction, User
from eval.budget_users import FORECAST_USERS, seed_profile, transactions
from tests.factories import add_account, add_txn, add_user

TODAY = date(2026,10,10)


def seed(db, today=TODAY):
    with db() as s:
        return seed_profile(s, FORECAST_USERS[0], until=today)


def test_buffer_defaults_to_100_dollars_and_reads_either_key(db):
    uid=seed(db)
    with db() as s:
        assert user_buffer(s,uid) == 10000
        s.get(User,uid).prefs_json={"low_balance_buffer_cents":25000}
        assert user_buffer(s,uid) == 25000
        s.get(User,uid).prefs_json={"low_balance_buffer_cents":25000,"forecast":{"low_balance_buffer_cents":5000}}
        assert user_buffer(s,uid) == 5000


@pytest.mark.parametrize("value", [-1,1000001,True,"100",100.5])
def test_set_user_buffer_rejects_bad_values(db,value):
    uid=seed(db)
    with db() as s, pytest.raises(ValueError):
        set_user_buffer(s,uid,value)


def test_setting_the_buffer_replaces_the_forecast_dict(db):
    uid=seed(db)
    with db() as s:
        before=s.get(User,uid).prefs_json
        set_user_buffer(s,uid,0)
        assert before is not s.get(User,uid).prefs_json and user_buffer(s,uid) == 0
        set_user_buffer(s,uid,1000000)
        s.commit()
    with db() as s:
        assert user_buffer(s,uid) == 1000000 and s.get(User,uid).prefs_json["sim"]["date"] == TODAY.isoformat()


def test_two_checking_accounts_are_summed_and_a_card_purchase_is_ignored(db):
    with db() as s:
        item=add_user(s,"u1","u1@x")
        a=add_account(s,item,"Checking",balance=100000,subtype="checking")
        add_account(s,item,"Legacy checking",balance=20000)
        card=add_account(s,item,"Card",type_="credit",balance=50000)
        add_account(s,item,"Savings",balance=90000,subtype="savings")
        add_txn(s,card,5000,"FOOD_AND_DRINK",day=TODAY)
        add_txn(s,a,1000,"FOOD_AND_DRINK",day=TODAY)
        s.commit()
        accounts=forecast_accounts(s,"u1")
        assert start_balance(accounts) == 120000
        assert sum(everyday_series(s,"u1",TODAY,accounts)) == 1000


def test_available_balance_wins_over_current(db):
    uid=seed(db)
    with db() as s:
        accounts=forecast_accounts(s,uid)
        accounts[0].current_balance_cents,accounts[0].available_balance_cents=100000,90000
        assert start_balance(accounts) == 90000
        accounts[0].available_balance_cents=None
        assert start_balance(accounts) == 100000


def test_a_user_with_no_depository_account_has_no_start_balance():
    assert start_balance([]) is None


def test_everyday_series_leaves_out_stream_charges_and_transfers(db):
    uid=seed(db)
    with db() as s:
        values=everyday_series(s,uid,TODAY,forecast_accounts(s,uid))
    expected=sum(c for d,c,cat,_ in transactions(FORECAST_USERS[0]) if date(2026,7,13)<=d<=TODAY and cat in ("FOOD_AND_DRINK","TRANSPORTATION"))
    assert sum(values) == expected and len(values) == 90


def test_everyday_series_has_90_entries_ending_today_with_zero_days(db):
    uid=seed(db)
    with db() as s:
        values=everyday_series(s,uid,TODAY,forecast_accounts(s,uid))
    assert len(values) == 90 and 0 in values


def test_everyday_series_covers_only_the_days_with_history(db):
    with db() as s:
        acct=add_account(s,add_user(s,"u1","u1@x"),"Checking")
        for k in range(65):
            add_txn(s,acct,10000,"FOOD_AND_DRINK",merchant=f"Shop {k}",day=TODAY-timedelta(days=k))
        s.commit()
        values=everyday_series(s,"u1",TODAY,forecast_accounts(s,"u1"))
    assert values == [10000]*65


def test_everyday_series_is_empty_with_no_history(db):
    with db() as s:
        add_account(s,add_user(s,"u1","u1@x"),"Checking")
        s.commit()
        assert everyday_series(s,"u1",TODAY,forecast_accounts(s,"u1")) == []


def test_card_obligations_keep_a_null_minimum_null(db):
    with db() as s:
        add_account(s,add_user(s,"u1","u1@x"),"Card",type_="credit",balance=5000,liability={"kind":"credit"})
        s.commit()
        assert load_card_obligations(s,"u1")[0].min_cents is None


def test_snapshot_writes_nothing(db):
    uid=seed(db)
    with db() as s:
        classes=(Transaction,Account,RecurringStream,AgentRun,Alert)
        before=[s.query(c).count() for c in classes]
        snapshot(s,uid,TODAY)
        assert before == [s.query(c).count() for c in classes] and not s.new and not s.dirty


def test_snapshot_follows_the_date_it_is_given(db):
    uid=seed(db)
    with db() as s:
        r=snapshot(s,uid,TODAY).result
    assert (r.status,r.dip.date,r.dip.cause.name,r.income) == ("ok",date(2026,10,13),"Landlord","paycheck")


@pytest.mark.parametrize("month", [9,11])
def test_the_dip_user_dips_every_month(db,month):
    day=date(2026,month,10); uid=seed(db,day)
    with db() as s:
        r=snapshot(s,uid,day).result
    assert r.dip.date == date(2026,month,13)


def test_the_dip_user_has_a_checking_balance_that_moves(db):
    uid=seed(db)
    with db() as s:
        balance=start_balance(forecast_accounts(s,uid))
    assert balance == 190000-sum(c for d,c,_,_ in transactions(FORECAST_USERS[0]) if d<=TODAY)
