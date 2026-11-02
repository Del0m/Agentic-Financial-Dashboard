from datetime import date

import pytest
from sqlalchemy import select

from app import config
from app.api import plaid
from app.budget_service.simulate import NotSimulated, advance_days
from app.db.models import PlaidItem, User
from scripts.seed_budget_user import main as seed
from tests.fakes import SchemaReplyModel

UID = 'budget-overspend-recurring'


def simulated(db):
    seed(['--profile', 'overspend-recurring', '--start', 'default'], session_factory=db)


def test_link_endpoints_reject_simulated_users_before_plaid(client, db, monkeypatch):
    simulated(db)
    monkeypatch.setattr(config, 'DEV_USER_EMAIL', 'overspend-recurring@eval.local')
    def forbidden(*a, **k):
        pytest.fail('Plaid must not be called for a simulated user')
    monkeypatch.setattr(plaid.plaid, 'create_link_token', forbidden)
    monkeypatch.setattr(plaid.plaid, 'exchange_public_token', forbidden)
    assert client.post('/plaid/link-token').status_code == 409
    assert client.post('/plaid/exchange', json={'public_token': 'dummy'}).status_code == 409


def test_reset_and_advance_refuse_legacy_mixed_user(db):
    simulated(db)
    with db() as s:
        s.add(PlaidItem(user_id=UID, plaid_item_id='dummy-real-item', access_token_enc='dummy-encrypted-token'))
        s.commit()
        old_date = s.get(User, UID).prefs_json['sim']['date']
    assert seed(['--profile', 'overspend-recurring', '--replace', '--start', 'default'], session_factory=db) == 1
    with pytest.raises(NotSimulated):
        advance_days(db, UID, 1, model=SchemaReplyModel(), checkpointer=None)
    with db() as s:
        assert s.scalar(select(PlaidItem).where(PlaidItem.plaid_item_id == 'dummy-real-item')) is not None
        assert s.get(User, UID).prefs_json['sim']['date'] == old_date
