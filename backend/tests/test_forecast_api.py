from datetime import date

import pytest
from fastapi import HTTPException

from app import config
from app.api import forecast as forecast_api
from app.api.deps import get_current_user
from app.budget_engine.goals import goal_cap
from app.budget_service.forecast_service import snapshot
from app.db.models import Account, AgentRun, Alert, RecurringStream, Transaction, User
from app.main import app
from eval.budget_users import FORECAST_USERS, seed_profile
from tests.factories import add_account, add_user

DAY=date(2026,10,10)


@pytest.fixture(autouse=True)
def user(db,monkeypatch):
    with db() as s:uid=seed_profile(s,FORECAST_USERS[0],until=DAY)
    monkeypatch.setattr(config,'DEV_USER_EMAIL','forecast-dip@eval.local')
    monkeypatch.setattr(forecast_api,'_today',lambda:date(2026,11,2))
    return uid


def test_forecast_for_the_dip_user(client):
    r=client.get('/forecast');assert r.status_code==200
    b=r.json()
    assert b['status']=='ok' and b['income']=='paycheck' and b['buffer_cents']==10000
    assert b['dip']['date']=='2026-10-13' and b['dip']['bill_label']=='Landlord'
    assert all(d['low_cents']<=d['expected_cents']<=d['high_cents'] for d in b['days'])


def test_days_param_bounds(client):
    assert client.get('/forecast?days=0').status_code==422 and client.get('/forecast?days=91').status_code==422
    assert len(client.get('/forecast?days=7').json()['days'])==7


def test_no_checking_account_is_200_with_an_empty_path(client,db):
    with db() as s:s.query(Account).update({'type':'credit'});s.commit()
    b=client.get('/forecast').json()
    assert b['status']=='no_checking' and b['start_cents'] is None and b['days']==[]


def test_no_history_is_200(client,db):
    with db() as s:s.query(Transaction).delete();s.commit()
    b=client.get('/forecast').json()
    assert b['status']=='no_history' and b['days']==[]


def test_no_paycheck_says_so(client,db):
    with db() as s:s.query(Transaction).filter_by(pf_category_primary='INCOME').update({'pf_category_primary':'TRANSFER_IN'});s.commit()
    b=client.get('/forecast').json()
    assert b['income']=='none' and b['monthly_surplus_cents'] is None and any('no income' in n for n in b['notes'])


def test_the_get_writes_nothing(client,db):
    with db() as s:before=[s.query(c).count() for c in (RecurringStream,AgentRun,Alert)]
    assert client.get('/forecast').status_code==200
    with db() as s:assert before==[s.query(c).count() for c in (RecurringStream,AgentRun,Alert)]


def test_a_simulated_user_gets_the_simulated_date(client):
    assert client.get('/forecast').json()['today']==DAY.isoformat()


def test_another_users_data_is_not_visible(client,db):
    before=client.get('/forecast').json()
    with db() as s:
        add_account(s,add_user(s,'other','other@x'),'Secret checking',balance=99999999,subtype='checking');s.commit()
    assert client.get('/forecast').json()==before


@pytest.mark.parametrize('value',[-1,1000001,True,'100',100.5])
def test_buffer_put_rejects_bad_values(client,value):
    assert client.put('/forecast/buffer',json={'buffer_cents':value}).status_code==422


def test_buffer_put_persists_and_the_get_echoes_it(client):
    assert client.put('/forecast/buffer',json={'buffer_cents':25000}).json()=={'buffer_cents':25000,'default_cents':10000}
    assert client.get('/forecast').json()['buffer_cents']==25000


def test_a_pending_alert_survives_a_buffer_change(client,db,user):
    with db() as s:
        s.add(Alert(user_id=user,kind='low_balance',severity='medium',action='alert',message='Landlord',status='pending_user'));s.commit()
    client.put('/forecast/buffer',json={'buffer_cents':25000})
    with db() as s:assert s.query(Alert).one().status=='pending_user'


def test_suggested_extra_uses_the_goal_cap(client,db,user):
    with db() as s:r=snapshot(s,user,DAY).result
    assert client.get('/forecast').json()['suggested_extra_cents']==goal_cap(r.monthly_surplus_cents,r.committed_goal_cents,.8,net_of_goals=True)


def test_requires_auth(client):
    def denied():raise HTTPException(401,'Not authenticated')
    app.dependency_overrides[get_current_user]=denied
    assert client.get('/forecast').status_code==401 and client.put('/forecast/buffer',json={'buffer_cents':10000}).status_code==401


def test_get_cash_forecast_returns_a_range_and_no_account_names(db,user,tmp_path):
    import json
    from app.agents.tools import make_tools
    from app.privacy.gateway import PrivacyGateway
    from tests.fakes import FakeKB
    with db() as s:
        tools={t.name:t for t in make_tools(PrivacyGateway(s,user,audit_path=tmp_path/'audit.jsonl'),FakeKB())}
        text=tools['get_cash_forecast'].invoke({})
    d=json.loads(text)['data']
    assert d['days'] and all(day['low']<=day['expected']<=day['high'] for day in d['days'])
    assert 'Checking' not in text and 'Employer' not in text
