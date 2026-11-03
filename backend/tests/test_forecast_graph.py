import json
import sqlite3
from dataclasses import replace
from datetime import date

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from app import config
from app.agents import budget_graph
from app.agents.budget_graph import run_budget
from app.budget_service.forecast_service import snapshot
from app.db.models import Account, AgentRun, Alert, Transaction
from eval.budget_users import FORECAST_USERS, seed_profile
from tests.fakes import SchemaReplyModel

pytestmark = pytest.mark.cash_forecast
DAY = date(2026,10,10)


def seed(db,day=DAY):
    with db() as s:
        return seed_profile(s,FORECAST_USERS[0],until=day)


def run(db,tmp_path,uid,day=DAY,model=None,**kw):
    return run_budget(db,uid,day,model=model or SchemaReplyModel(),audit_path=tmp_path/'audit.jsonl',**kw)


def low_alerts(db):
    with db() as s:
        return s.query(Alert).filter_by(kind='low_balance').all()


def test_the_dip_alert_names_rent_and_fires_at_least_3_days_ahead(db,tmp_path):
    uid=seed(db); run(db,tmp_path,uid)
    [a]=low_alerts(db)
    assert 'Landlord' in a.message and a.signal_key.endswith('|2026-10-13') and a.signal_days>=3


def test_the_same_dip_on_three_days_alerts_once(db,tmp_path,monkeypatch):
    uid=seed(db)
    with db() as s: snap=snapshot(s,uid,DAY)
    monkeypatch.setattr(budget_graph,'snapshot',lambda *a:replace(snap,result=replace(snap.result,dip=replace(snap.result.dip,days_ahead=5))))
    for day in (7,8,9):run(db,tmp_path,uid,date(2026,10,day))
    assert len(low_alerts(db)) == 1


def test_a_deeper_dip_alerts_again_and_a_small_change_does_not(db,tmp_path,monkeypatch):
    uid=seed(db)
    with db() as s: snap=snapshot(s,uid,DAY)
    values=[snap.result.dip.shortfall_cents, snap.result.dip.shortfall_cents+1000, snap.result.dip.shortfall_cents*2+2000]
    for value in values:
        monkeypatch.setattr(budget_graph,'snapshot',lambda *a:replace(snap,result=replace(snap.result,dip=replace(snap.result.dip,shortfall_cents=value))))
        run(db,tmp_path,uid)
    assert len(low_alerts(db)) == 2


def test_a_dismissed_dip_stays_quiet_unless_it_grows(db,tmp_path):
    uid=seed(db); run(db,tmp_path,uid)
    with db() as s:
        s.query(Alert).filter_by(kind='low_balance').one().status='dismissed'; s.commit()
    run(db,tmp_path,uid)
    assert len(low_alerts(db)) == 1


def test_a_model_message_without_the_bill_is_replaced(db,tmp_path):
    uid=seed(db); run(db,tmp_path,uid)
    assert 'Landlord' in low_alerts(db)[0].message


def test_severity_is_high_below_zero_and_medium_under_the_buffer(db,tmp_path,monkeypatch):
    uid=seed(db)
    with db() as s: snap=snapshot(s,uid,DAY)
    days=tuple(replace(d,low_cents=5000) for d in snap.result.days)
    monkeypatch.setattr(budget_graph,'snapshot',lambda *a:replace(snap,result=replace(snap.result,days=days)))
    run(db,tmp_path,uid); assert low_alerts(db)[0].severity=='medium'
    days=tuple(replace(d,low_cents=-1000) for d in snap.result.days)
    with db() as s: s.query(Alert).filter_by(kind='low_balance').delete();s.commit()
    run(db,tmp_path,uid); assert low_alerts(db)[0].severity=='high'


def test_a_user_with_no_budget_gets_the_alert_with_no_period(db,tmp_path,monkeypatch):
    uid=seed(db);monkeypatch.setattr(budget_graph,'initialize_budget',lambda *a:None)
    r=run(db,tmp_path,uid)
    assert r['status']=='no_budget' and low_alerts(db)[0].period_id is None


def test_a_user_with_no_paycheck_stream_only_gets_an_alert(db,tmp_path):
    uid=seed(db)
    with db() as s:
        for txn in s.query(Transaction).filter_by(user_id=uid,pf_category_primary='INCOME'): txn.pf_category_primary='TRANSFER_IN'
        s.commit()
    run(db,tmp_path,uid,model=SchemaReplyModel(signal_action='move_from_savings'))
    [a]=low_alerts(db)
    assert a.action=='alert' and 'no income is assumed' in a.message


def test_a_forecast_failure_does_not_stop_overspend_alerts(db,tmp_path,monkeypatch):
    uid=seed(db)
    def fail(*a):raise RuntimeError('private details')
    monkeypatch.setattr(budget_graph,'snapshot',fail)
    r=run(db,tmp_path,uid)
    with db() as s:
        assert r['status']=='ok' and s.query(Alert).filter_by(kind='overspend').count()>0
        assert s.get(AgentRun,r['run_id']).summary_json['signals_error']=='RuntimeError'


def test_summary_forecast_holds_cents_and_dates_only(db,tmp_path):
    uid=seed(db);r=run(db,tmp_path,uid)
    with db() as s: summary=s.get(AgentRun,r['run_id']).summary_json
    assert summary['forecast']['ok'] and summary['forecast']['low_balance']['date']=='2026-10-13'
    assert 'Landlord' not in json.dumps(summary) and 'Employer' not in json.dumps(summary)


def test_a_paused_checkpoint_holds_no_bill_name_or_balance(db,tmp_path):
    uid=seed(db)
    with db() as s:
        s.query(Account).filter_by(user_id=uid).one().current_balance_cents=424242
        s.query(Account).filter_by(user_id=uid).one().available_balance_cents=424242
        for t in s.query(Transaction).filter_by(user_id=uid,merchant_name='Landlord'):
            t.merchant_name='ZZ-UNIQUE-LANDLORD';t.amount_cents=450000
        s.commit()
    path=tmp_path/'ck.db'
    with sqlite3.connect(path,check_same_thread=False) as conn:
        saver=SqliteSaver(conn)
        r=run(db,tmp_path,uid,model=SchemaReplyModel(cause='recurring',first_action='adjust_next_budget'),checkpointer=saver)
        assert r['status']=='awaiting_user' and low_alerts(db)
        snap=budget_graph.build_budget_graph(db,model=None,gateway=None,checkpointer=saver).get_state(budget_graph._thread(r['run_id'])).values
        assert snap['signals']==[] and snap['signal_actions']=={}
        assert 'ZZ-UNIQUE-LANDLORD' not in json.dumps(snap) and '424242' not in json.dumps(snap)
    raw=b''.join(p.read_bytes() for p in tmp_path.glob('ck.db*'))
    assert b'ZZ-UNIQUE-LANDLORD' not in raw and b'424242' not in raw


def test_a_normal_day_adds_no_model_call(db,tmp_path,monkeypatch):
    uid=seed(db)
    with db() as s: snap=snapshot(s,uid,DAY)
    monkeypatch.setattr(budget_graph,'snapshot',lambda *a:replace(snap,result=replace(snap.result,dip=None)))
    from tests.test_recurring_graph import quiet_pace
    quiet_pace(monkeypatch)
    m=SchemaReplyModel();run(db,tmp_path,uid,model=m)
    assert m.seen==[] and low_alerts(db)==[]


def test_an_errored_run_never_writes_bill_payloads_to_the_checkpoint_file(db,tmp_path,monkeypatch):
    uid=seed(db)
    with db() as s:
        for t in s.query(Transaction).filter_by(user_id=uid,merchant_name='Landlord'):
            t.merchant_name='ZZ-PRIVATE-RENT'
        s.commit()
    def fail(*a,**kw):raise RuntimeError('ZZ-PRIVATE-ERROR')
    monkeypatch.setattr(budget_graph,'invoke_structured',fail)
    path=tmp_path/'error-ck.db'
    with sqlite3.connect(path,check_same_thread=False) as conn, pytest.raises(RuntimeError):
        run(db,tmp_path,uid,checkpointer=SqliteSaver(conn))
    raw=b''.join(p.read_bytes() for p in tmp_path.glob('error-ck.db*'))
    assert b'ZZ-PRIVATE-RENT' not in raw and b'ZZ-PRIVATE-ERROR' not in raw
