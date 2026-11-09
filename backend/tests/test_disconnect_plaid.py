from datetime import timedelta

import pytest
from sqlalchemy import select

from app import config
from app.api import plaid as api
from app.api import chat
from app.budget_service.run_lock import now
from app.db.models import User, PlaidItem, Account, Transaction, Goal, UserRunClaim
from app.plaid_service.client import PlaidError
from app.plaid_service.sync import item_lock
from tests.test_reset_plaid import populated  # rich FK-enforced fixture shared with the CLI reset tests


@pytest.fixture
def disconnect(populated, client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'ENABLE_ADMIN', True)
    calls = []
    monkeypatch.setattr(api, 'backup_database', lambda engine: tmp_path / 'backup.db')
    monkeypatch.setattr(api.plaid, 'plaid_post', lambda path, body: calls.append((path, body)) or {})
    return client, populated, calls


def request(client):
    return client.post('/plaid/disconnect', json={'confirmed': True})


def test_disconnect_clears_only_current_demo_and_returns_to_unlinked(disconnect):
    client, db, calls = disconnect
    assert client.get('/dashboard').json()['can_disconnect'] is True
    response = request(client)
    assert response.status_code == 200
    assert response.json() == {'disconnected': True}
    assert len(calls) == 1 and calls[0][0] == '/item/remove'
    with db() as s:
        assert s.get(User, 'seeded') is not None
        assert s.get(User, 'plaid-old') is None
        assert s.scalar(select(User).where(User.email == 'dev@localhost')).id != 'plaid-old'
        for cls in (PlaidItem, Account, Transaction, Goal):
            assert s.scalar(select(cls).where(cls.user_id == 'plaid-old')) is None
    dashboard = client.get('/dashboard').json()
    assert dashboard['bank_linked'] is False and dashboard['can_disconnect'] is False
    assert dashboard['accounts'] == []


@pytest.mark.parametrize('body', [{}, {'confirmed': False}])
def test_confirmation_is_required(disconnect, body):
    client, db, calls = disconnect
    assert client.post('/plaid/disconnect', json=body).status_code == 422
    assert calls == []
    with db() as s: assert s.get(User, 'plaid-old') is not None


@pytest.mark.parametrize('setting,value', [('ENABLE_ADMIN', False), ('PLAID_ENV', 'production')])
def test_disconnect_is_sandbox_admin_only(disconnect, monkeypatch, setting, value):
    client, db, calls = disconnect
    monkeypatch.setattr(config, setting, value)
    assert request(client).status_code == 404
    assert calls == []
    assert client.get('/dashboard').json()['can_disconnect'] is False


def test_disconnect_backup_failure_preserves_connection(disconnect, monkeypatch):
    client, db, calls = disconnect
    def failed(engine): raise OSError('disk full')
    monkeypatch.setattr(api, 'backup_database', failed)
    assert request(client).status_code == 503
    assert calls == []
    with db() as s: assert s.get(User, 'plaid-old') is not None


def test_provider_failure_preserves_local_data(disconnect, monkeypatch):
    client, db, calls = disconnect
    def failed(path, body): raise PlaidError(502, 'INTERNAL_SERVER_ERROR', 'temporary')
    monkeypatch.setattr(api.plaid, 'plaid_post', failed)
    assert request(client).status_code == 502
    with db() as s: assert s.get(User, 'plaid-old') is not None


def test_already_revoked_item_can_complete_local_disconnect(disconnect, monkeypatch):
    client, db, calls = disconnect
    def revoked(path, body): raise PlaidError(400, 'INVALID_ACCESS_TOKEN', 'already removed')
    monkeypatch.setattr(api.plaid, 'plaid_post', revoked)
    assert request(client).status_code == 200
    assert client.get('/dashboard').json()['bank_linked'] is False


def test_active_agent_refuses_disconnect(disconnect):
    client, db, calls = disconnect
    with db() as s:
        s.add(UserRunClaim(user_id='plaid-old', owner='other', expires_at=now()+timedelta(minutes=5)));s.commit()
    assert request(client).status_code == 409
    assert calls == []


def test_active_chat_refuses_disconnect(disconnect):
    client, db, calls = disconnect
    mutex = chat._user_lock('plaid-old')
    mutex.acquire()
    try:
        assert request(client).status_code == 409
        assert calls == []
    finally:
        mutex.release()


def test_active_sync_refuses_disconnect(disconnect):
    client, db, calls = disconnect
    with db() as s:
        item = s.scalar(select(PlaidItem).where(PlaidItem.user_id == 'plaid-old'))
        mutex = item_lock(s, item.id)
    mutex.acquire()
    try:
        assert request(client).status_code == 409
        assert calls == []
    finally:
        mutex.release()


def test_disconnect_only_allows_current_plaid_demo_user(disconnect, monkeypatch):
    client, db, calls = disconnect
    monkeypatch.setattr(config, 'DEV_USER_EMAIL', 'demo@eval.local')
    assert request(client).status_code == 404
    assert calls == []


def test_missing_credentials_preserve_connection(disconnect, monkeypatch):
    client, db, calls = disconnect
    def missing(path, body): raise api.plaid.PlaidNotConfigured()
    monkeypatch.setattr(api.plaid, 'plaid_post', missing)
    assert request(client).status_code == 503
    with db() as s: assert s.get(User, 'plaid-old') is not None


def test_disconnect_with_real_file_backup(tmp_path, monkeypatch):
    import sqlite3
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base
    from app.db.session import get_session
    from app.main import app
    from app.security import encrypt

    monkeypatch.setattr(config, 'ENABLE_ADMIN', True)
    monkeypatch.setattr(config, 'PLAID_ENV', 'sandbox')
    path = tmp_path / 'demo.db'
    engine = create_engine(f'sqlite:///{path}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    from app.api import budget_hooks
    monkeypatch.setattr(budget_hooks, 'session_factory', factory)
    monkeypatch.setattr(config, 'ENABLE_SCHEDULER', False)
    with factory() as s:
        s.add(User(id='file-user', email='dev@localhost', password_hash='!', prefs_json={}))
        s.flush()
        s.add(PlaidItem(user_id='file-user', plaid_item_id='file-item', access_token_enc=encrypt('access-sandbox-file')))
        s.commit()
    def sessions():
        with factory() as s: yield s
    previous = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = sessions
    def remove(path_, body):
        # The backup must exist before the remote Item is revoked.
        assert list(tmp_path.glob('*.before-plaid-reset-*.db'))
        return {}
    monkeypatch.setattr(api.plaid, 'plaid_post', remove)
    try:
        with TestClient(app) as c:
            assert request(c).status_code == 200
            assert c.get('/dashboard').json()['bank_linked'] is False
        backup, = tmp_path.glob('*.before-plaid-reset-*.db')
        with sqlite3.connect(backup) as conn:
            assert conn.execute('select id from users').fetchone()[0] == 'file-user'
            assert conn.execute('select count(*) from plaid_items').fetchone()[0] == 1
    finally:
        if previous is None: app.dependency_overrides.pop(get_session, None)
        else: app.dependency_overrides[get_session] = previous
        engine.dispose()
