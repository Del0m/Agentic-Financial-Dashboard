from datetime import date, timedelta
import sqlite3

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import config
from app.db.models import (Base, Account, AdviceLog, AgentRun, Alert, BudgetLine, BudgetPeriod,
                           CategoryRule, Goal, KGEdge, KGNode, Liability, PlaidItem, RecurringStream,
                           Transaction, User, UserRunClaim)
from app.budget_service.run_lock import now
from app.security import encrypt
from scripts import reset_plaid


@pytest.fixture
def populated(db, monkeypatch):
    monkeypatch.setattr(config, 'PLAID_ENV', 'sandbox')
    with db() as s:
        s.connection().exec_driver_sql('PRAGMA foreign_keys=ON')
        s.add_all([User(id='plaid-old', email='dev@localhost', password_hash='!', prefs_json={}),
                   User(id='seeded', email='demo@eval.local', password_hash='!', prefs_json={'sim': {'date':'2026-10-08'}})])
        s.flush()
        item=PlaidItem(user_id='plaid-old', plaid_item_id='item', access_token_enc=encrypt('access-sandbox-test'))
        s.add(item); s.flush()
        account=Account(user_id='plaid-old',item_id=item.id,plaid_account_id='acc',name='Checking',type='depository')
        period=BudgetPeriod(user_id='plaid-old',start_date=date(2026,11,1),end_date=date(2026,11,30))
        run=AgentRun(user_id='plaid-old',graph='budget',status='awaiting_user')
        s.add_all([account,period,run]); s.flush()
        stream=RecurringStream(user_id='plaid-old',direction='out',merchant_key='shop',merchant_name='Shop',
            category='ENTERTAINMENT',detailed=None,frequency='monthly',typical_cents=1000,last_cents=1000,
            previous_cents=None,amount_varies=False,first_date=date(2026,9,1),last_date=date(2026,11,1),
            next_date=date(2026,12,1),gap_days=30,status='active',first_seen_run_id=run.id,first_seen_date=date(2026,11,1))
        s.add(stream); s.flush()
        alert=Alert(user_id='plaid-old',period_id=period.id,stream_id=stream.id,kind='overspend',severity='high',message='test')
        s.add(alert); s.flush()
        s.add_all([
            AdviceLog(user_id='plaid-old',run_id=run.id,alert_id=alert.id,advice_text='test'),
            Goal(user_id='plaid-old',run_id=run.id,kind='savings',priority=1,monthly_cents=1000,target_cents=10000,rationale='test',status='proposed'),
            BudgetLine(period_id=period.id,category='FREE',name='Free',group='free',kind='flexible',limit_cents=1000,source='init'),
            CategoryRule(user_id='plaid-old',merchant_name='shop',category='ENTERTAINMENT'),
            Transaction(user_id='plaid-old',account_id=account.id,plaid_txn_id='txn',date=date(2026,11,8),amount_cents=120000),
            Liability(user_id='plaid-old',account_id=account.id,kind='credit'),
        ])
        node=KGNode(user_id='plaid-old',kind='merchant',key='shop')
        shared=KGNode(user_id=None,kind='concept',key='guidance')
        s.add_all([node,shared]);s.flush()
        s.add(KGEdge(user_id='plaid-old',src_id=node.id,dst_id=shared.id,rel='test',valid_from=date(2026,11,1)))
        s.commit()
    return db


def test_reset_clears_owned_data_with_foreign_keys_and_keeps_other_users(populated):
    with populated() as s:
        fresh=reset_plaid.reset_user(s);s.commit()
        assert fresh != 'plaid-old'
        assert s.get(User,'plaid-old') is None
        assert s.get(User,fresh).prefs_json == {}
        assert s.get(User,'seeded').prefs_json['sim']['date']=='2026-10-08'
        assert s.scalar(select(KGNode).where(KGNode.user_id.is_(None))) is not None
        for cls in (Account,AdviceLog,AgentRun,Alert,BudgetPeriod,CategoryRule,Goal,KGEdge,KGNode,Liability,PlaidItem,RecurringStream,Transaction,UserRunClaim):
            assert s.scalar(select(cls).where(cls.user_id=='plaid-old')) is None,cls.__name__
        assert s.scalar(select(BudgetLine)) is None


def test_preview_changes_nothing(populated):
    assert reset_plaid.main([],session_factory=populated)==0
    with populated() as s: assert s.get(User,'plaid-old') is not None


@pytest.mark.parametrize('mode',['production','development'])
def test_non_sandbox_is_refused(populated,monkeypatch,mode):
    monkeypatch.setattr(config,'PLAID_ENV',mode)
    assert reset_plaid.main(['--yes'],session_factory=populated)==1
    with populated() as s: assert s.get(User,'plaid-old') is not None


@pytest.mark.parametrize('case',['claim','running','simulated','wrong_token'])
def test_unsafe_target_is_refused(populated,case):
    with populated() as s:
        if case=='claim': s.add(UserRunClaim(user_id='plaid-old',owner='other',expires_at=now()+timedelta(minutes=5)))
        if case=='running': s.add(AgentRun(user_id='plaid-old',graph='budget',status='running'))
        if case=='simulated': s.get(User,'plaid-old').prefs_json={'sim':{'date':'2026-10-08'}}
        if case=='wrong_token': s.scalar(select(PlaidItem)).access_token_enc=encrypt('access-production-test')
        s.commit()
    assert reset_plaid.main(['--yes'],session_factory=populated)==1
    with populated() as s: assert s.get(User,'plaid-old') is not None


def test_backup_failure_prevents_deletion(populated):
    # An in-memory database cannot satisfy the mandatory backup requirement.
    assert reset_plaid.main(['--yes'],session_factory=populated)==1
    with populated() as s: assert s.get(User,'plaid-old') is not None


def test_apply_creates_private_recoverable_backup(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'PLAID_ENV','sandbox')
    path=tmp_path/'demo.db'
    engine=create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(engine)
    factory=sessionmaker(bind=engine)
    with factory() as s:
        s.add(User(id='old',email='dev@localhost',password_hash='!',prefs_json={'note':'before'}));s.commit()
    assert reset_plaid.main(['--yes'],session_factory=factory)==0
    backup,=tmp_path.glob('*.before-plaid-reset-*.db')
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as c: assert c.execute('select id from users').fetchone()[0]=='old'
    with factory() as s: assert s.scalar(select(User).where(User.email=='dev@localhost')).id!='old'
    engine.dispose()
