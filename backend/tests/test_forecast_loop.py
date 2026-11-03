from datetime import date

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app import config
from app.api import budget as budget_api, budget_hooks
from app.budget_engine.goals import goal_cap
from app.budget_service.forecast_service import snapshot
from app.db.models import AdviceLog, AgentRun, Alert, Transaction
from eval.budget_users import FORECAST_USERS, seed_profile
from tests.fakes import SchemaReplyModel
from tests.test_goals_api import api, g, generate, run_of, UID

pytestmark = [pytest.mark.cash_forecast, pytest.mark.forecast_surplus]


@pytest.fixture
def cash_api(client, db, monkeypatch, tmp_path):
    with db() as s: uid = seed_profile(s, FORECAST_USERS[0], until=date(2026,9,30))
    monkeypatch.setattr(config,'DEV_USER_EMAIL','forecast-dip@eval.local')
    monkeypatch.setattr(config,'ENABLE_ADMIN',True)
    monkeypatch.setattr(config,'PROMPT_AUDIT_PATH',tmp_path/'audit.jsonl')
    monkeypatch.setattr(budget_hooks,'session_factory',db)
    ck=InMemorySaver();m=SchemaReplyModel()
    monkeypatch.setattr(budget_hooks,'get_checkpointer',lambda:ck)
    monkeypatch.setattr(budget_hooks,'make_model',lambda:m)
    monkeypatch.setattr(budget_api,'_spawn',lambda work:work())
    return client,uid


def advance(client,days):
    r=client.post('/admin/sandbox/advance',json={'days':days})
    assert r.status_code==202,r.text
    job=client.get(f"/admin/sandbox/advance/{r.json()['job_id']}").json()
    assert job['status']=='ok' and job['days_done']==days,job


def low(db):
    with db() as s:return s.query(Alert).filter_by(kind='low_balance').order_by(Alert.id).all()


def test_rent_before_payday_alerts_at_least_3_days_ahead_and_names_rent(cash_api,db):
    client,uid=cash_api;advance(client,10)
    alert=low(db)[0]
    assert 'Landlord' in alert.message and alert.signal_days>=3 and alert.signal_key.endswith('|2026-10-13')
    assert any(a['kind']=='low_balance' for a in client.get('/alerts').json())


def test_the_alert_does_not_repeat_after_the_single_urgency_reminder_through_payday(cash_api,db):
    client,_=cash_api;advance(client,10)
    alerts=low(db)
    assert len(alerts)==2
    assert alerts[1].signal_cents*4>=alerts[0].signal_cents*5 and alerts[1].signal_cents-alerts[0].signal_cents>=2000
    advance(client,1);assert len(low(db))==3 and low(db)[-1].signal_days==2
    advance(client,5);assert len(low(db))==3


def test_the_next_months_dip_has_a_new_key_and_one_initial_warning(cash_api,db):
    client,_=cash_api;advance(client,31);advance(client,10)
    keys=[a.signal_key for a in low(db)]
    assert any(k.endswith('|2026-10-13') for k in keys)
    assert len([k for k in keys if k.endswith('|2026-11-13')])==1


def test_the_chart_data_shows_the_band_under_the_buffer_at_the_rent_marker(cash_api):
    client,_=cash_api;advance(client,10)
    b=client.get('/forecast').json()
    assert next(d for d in b['days'] if d['date']=='2026-10-13')['low_cents']<b['buffer_cents']
    assert any(e['date']=='2026-10-13' and e['label']=='Landlord' for e in b['events'])


def test_raising_the_buffer_redraws_the_forecast_and_keeps_the_alert(cash_api,db):
    client,_=cash_api;advance(client,10)
    before=low(db)[0].id
    assert client.put('/forecast/buffer',json={'buffer_cents':25000}).status_code==200
    assert client.get('/forecast').json()['buffer_cents']==25000
    assert low(db)[0].id==before and low(db)[0].status=='pending_user'


def test_goals_use_the_forecast_and_say_so(api):
    rid=generate(api,g('debt_payoff',58300,'avalanche'))
    r=run_of(api,rid);assert r['surplus_source']=='forecast'
    assert api.client.post(f"/goals/{r['goal_ids'][0]}/accept").status_code==200
    rid=generate(api,g('savings',1000))
    with api.db() as s:
        summary=s.get(AgentRun,rid).summary_json
        snap=snapshot(s,UID,date(2026,11,2)).result
    assert summary['surplus_source']=='forecast'
    assert summary['cap_cents']==goal_cap(snap.monthly_surplus_cents,58300,.8,net_of_goals=True)


def test_a_user_with_no_paycheck_gets_no_dip_alert_other_than_alert(cash_api,db):
    client,_=cash_api
    with db() as s:
        s.query(Transaction).filter_by(pf_category_primary='INCOME').update({'pf_category_primary':'TRANSFER_IN'});s.commit()
    advance(client,10)
    assert low(db) and all(a.action=='alert' and 'no income is assumed' in a.message for a in low(db))
