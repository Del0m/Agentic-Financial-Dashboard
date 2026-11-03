"""Independent storage assertions for the temporal mutation contract."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import KGEdge, KGNode, User
from app.kg.store import assert_edge, close_edge, upsert_node
from app.kg.types import (ClockOrderError, InvalidAssertionError, InvalidIntervalError,
                          OverlapError, ScopeError, StaleTargetError)

D = date.fromisoformat
T1 = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
T2 = datetime(2026, 4, 10, 12, tzinfo=timezone.utc)
T3 = datetime(2026, 5, 10, 12, tzinfo=timezone.utc)
T4 = datetime(2026, 6, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "store.db"}')
    @event.listens_for(engine, 'connect')
    def foreign_keys(dbapi, _):
        dbapi.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as s:
        s.add_all([User(id=u, email=f'{u}@test.local', password_hash='x') for u in ('a', 'b')])
    yield factory
    engine.dispose()


@pytest.fixture
def s(factory):
    with factory() as session:
        yield session


def nodes(s, user='a', guidance=False):
    if guidance:
        return (upsert_node(s, user_id=None, kind='concept', key='Roth'),
                upsert_node(s, user_id=None, kind='concept', key='Limit'))
    return (upsert_node(s, user_id=user, kind='user', key=user),
            upsert_node(s, user_id=user, kind='stream', key='Gym'))


def fact(s, pair, **kwargs):
    args = dict(user_id=pair[0].user_id, src_id=pair[0].id,
                dst_id=pair[1].id, rel='SUBSCRIBES' if pair[0].user_id else 'HAS_LIMIT',
                value={'typical_cents': 3500}, valid_from=D('2026-01-01'), now=T1)
    args.update(kwargs)
    return assert_edge(s, **args)


def rows(s):
    return list(s.scalars(select(KGEdge).order_by(KGEdge.id)))


def state(s):
    return [(e.id, e.dst_id, e.value_json, e.valid_from, e.valid_to,
             e.recorded_at, e.expired_at, e.source, e.evidence_json) for e in rows(s)]


def test_upsert_node_identity_and_normalization(s):
    a = upsert_node(s, user_id='a', kind='merchant', key='  GYM  ')
    assert upsert_node(s, user_id='a', kind='merchant', key='gym').id == a.id
    assert upsert_node(s, user_id='b', kind='merchant', key='Gym').id != a.id
    upper = upsert_node(s, user_id='a', kind='account', key='ABC')
    lower = upsert_node(s, user_id='a', kind='account', key='abc')
    assert upper.id != lower.id
    assert a.key == 'gym'


def test_shared_node_unique_with_null_owner(s):
    a = upsert_node(s, user_id=None, kind='concept', key='IRA')
    assert upsert_node(s, user_id=None, kind='concept', key='IRA').id == a.id
    with pytest.raises(IntegrityError), s.begin_nested():
        s.add(KGNode(user_id=None, kind='concept', key='IRA', props_json={}))
        s.flush()
    assert s.scalar(select(KGNode.id).where(KGNode.key == 'IRA')) == a.id


@pytest.mark.parametrize('field,value', [('kind', 'merchant'), ('key', 'other'), ('user_id', 'b')])
def test_node_identity_is_immutable(s, field, value):
    pair = nodes(s)
    edge = fact(s, pair)
    node = pair[1]
    with pytest.raises(ValueError), s.begin_nested():
        setattr(node, field, value)
        s.flush()
    refreshed = upsert_node(s, user_id='a', kind='stream', key='Gym', label='New label', props={'display': 'new'})
    assert refreshed.id == node.id
    assert (refreshed.kind, refreshed.key, refreshed.user_id) == ('stream', 'Gym', 'a')
    assert (refreshed.label, refreshed.props_json) == ('New label', {'display': 'new'})
    assert rows(s)[0].dst_id == edge.dst_id


@pytest.mark.parametrize('end', [None, D('2026-02-01')])
def test_assertion_retry_and_json_key_order(s, end):
    pair = nodes(s)
    original = fact(s, pair, valid_to=end, value={'a': 1, 'b': {'x': 2, 'y': 3}}, evidence={'one': 1, 'two': 2})
    retry = fact(s, pair, valid_to=end, value={'b': {'y': 3, 'x': 2}, 'a': 1}, evidence={'two': 2, 'one': 1}, now=T2)
    assert retry.id == original.id
    assert retry.recorded_at == T1
    assert len(rows(s)) == 1


def test_unchanged_open_observation_is_noop(s):
    pair = nodes(s)
    original = fact(s, pair)
    assert fact(s, pair, valid_from=D('2026-03-01'), now=T2).id == original.id
    for override in ({'evidence': {'ref': 'new'}}, {'source': 'user'}):
        before = state(s)
        with pytest.raises(InvalidAssertionError):
            fact(s, pair, valid_from=D('2026-03-01'), now=T2, **override)
        assert state(s) == before


def test_equal_values_in_distinct_months(s):
    pair = (upsert_node(s, user_id='a', kind='user', key='a'),
            upsert_node(s, user_id='a', kind='merchant', key='Gym'))
    value = {'cents': 3500, 'count': 1, 'category': 'fitness'}
    jan = fact(s, pair, rel='SPENDS_AT', value=value, valid_to=D('2026-02-01'))
    feb = fact(s, pair, rel='SPENDS_AT', value=value, valid_from=D('2026-02-01'), valid_to=D('2026-03-01'), now=T2)
    assert fact(s, pair, rel='SPENDS_AT', value=value, valid_to=D('2026-02-01'), now=T3).id == jan.id
    assert [(e.id, e.valid_from, e.valid_to) for e in rows(s)] == [
        (jan.id, D('2026-01-01'), D('2026-02-01')),
        (feb.id, D('2026-02-01'), D('2026-03-01'))]


def test_change_preserves_original_belief(s):
    pair = nodes(s)
    original = fact(s, pair)
    changed = fact(s, pair, value={'typical_cents': 4500}, valid_from=D('2026-04-01'), now=T2)
    assert [(e.value_json, e.valid_from, e.valid_to, e.recorded_at, e.expired_at) for e in rows(s)] == [
        ({'typical_cents': 3500}, D('2026-01-01'), None, T1, T2),
        ({'typical_cents': 3500}, D('2026-01-01'), D('2026-04-01'), T2, None),
        ({'typical_cents': 4500}, D('2026-04-01'), None, T2, None)]
    assert changed.id != original.id


@pytest.mark.parametrize('end', [None, D('2026-02-01')])
def test_correction_preserves_valid_interval(s, end):
    pair = nodes(s)
    target = fact(s, pair, valid_to=end)
    if end:
        neighbor = fact(s, pair, valid_from=D('2026-03-01'), valid_to=D('2026-04-01'), now=T2)
        neighbor_state = (neighbor.id, neighbor.recorded_at, neighbor.expired_at)
    corrected = fact(s, pair, valid_to=end, correct=True, target_edge_id=target.id,
                     value={'typical_cents': 4000}, evidence={'reason': 'receipt'}, source='user', now=T3)
    assert (corrected.valid_from, corrected.valid_to, corrected.recorded_at) == (D('2026-01-01'), end, T3)
    assert (target.valid_from, target.valid_to, target.value_json, target.expired_at) == (D('2026-01-01'), end, {'typical_cents': 3500}, T3)
    if end:
        assert (neighbor.id, neighbor.recorded_at, neighbor.expired_at) == neighbor_state


def test_correction_rejects_missing_stale_or_altered_target(s):
    pair = nodes(s)
    target = fact(s, pair)
    invalid = [({'correct': True}, StaleTargetError),
               ({'correct': True, 'target_edge_id': 99999}, ScopeError),
               ({'correct': True, 'target_edge_id': target.id, 'valid_to': D('2026-02-01')}, InvalidIntervalError),
               ({'correct': True, 'target_edge_id': target.id, 'now': T1}, ClockOrderError),
               ({'correct': True, 'target_edge_id': target.id, 'now': T1 - timedelta(seconds=1)}, ClockOrderError)]
    for overrides, error in invalid:
        before = state(s)
        args = dict(value={'typical_cents': 4000}, now=T2)
        args.update(overrides)
        with pytest.raises(error):
            fact(s, pair, **args)
        assert state(s) == before
    fact(s, pair, correct=True, target_edge_id=target.id, value={'typical_cents': 4000}, now=T2)
    with pytest.raises(StaleTargetError):
        fact(s, pair, correct=True, target_edge_id=target.id, value={'typical_cents': 4000}, now=T3)


def test_correction_retry_current_target(s):
    pair = nodes(s)
    old = fact(s, pair)
    current = fact(s, pair, correct=True, target_edge_id=old.id, value=4000, now=T2)
    assert fact(s, pair, correct=True, target_edge_id=current.id, value=4000, now=T3).id == current.id
    assert len(rows(s)) == 2
    with pytest.raises(StaleTargetError):
        fact(s, pair, correct=True, target_edge_id=old.id, value=4000, now=T3)


def test_category_replacement_cardinality(s):
    merchant = upsert_node(s, user_id='a', kind='merchant', key='Gym')
    categories = [upsert_node(s, user_id='a', kind='category', key=k) for k in ('health', 'fitness', 'other')]
    old = fact(s, (merchant, categories[0]), rel='CATEGORIZED_AS', value={})
    new = fact(s, (merchant, categories[1]), rel='CATEGORIZED_AS', value={}, valid_from=D('2026-04-01'), now=T2)
    current = fact(s, (merchant, categories[2]), rel='CATEGORIZED_AS', value={}, valid_from=D('2026-04-01'), correct=True, target_edge_id=new.id, now=T3)
    assert [(e.dst_id, e.valid_from, e.valid_to) for e in rows(s) if e.expired_at is None] == [
        (categories[0].id, D('2026-01-01'), D('2026-04-01')),
        (categories[2].id, D('2026-04-01'), None)]
    before = state(s)
    with pytest.raises(OverlapError):
        fact(s, (merchant, categories[1]), rel='CATEGORIZED_AS', value={}, valid_from=D('2026-04-01'), now=T4)
    assert state(s) == before
    assert current.id != old.id


@pytest.mark.parametrize('start,end,error', [
    ('2025-12-01', '2025-12-10', OverlapError),
    ('2026-01-15', '2026-03-15', OverlapError),
    ('2025-12-01', '2026-05-01', OverlapError),
    ('2026-02-10', '2026-02-20', OverlapError),
    ('2026-04-01', '2026-04-01', InvalidIntervalError),
    ('2026-04-02', '2026-04-01', InvalidIntervalError),
])
def test_chronological_intervals_and_rejected_overlaps(s, start, end, error):
    pair = nodes(s)
    fact(s, pair, valid_to=D('2026-02-01'))
    fact(s, pair, valid_from=D('2026-03-01'), valid_to=D('2026-04-01'), now=T2)
    before = state(s)
    with pytest.raises(error):
        fact(s, pair, value=999, valid_from=D(start), valid_to=D(end), now=T3)
    assert state(s) == before


def test_close_preserves_belief_and_is_idempotent(s):
    pair = nodes(s)
    old = fact(s, pair)
    closed = close_edge(s, user_id='a', target_edge_id=old.id, valid_to=D('2026-06-01'), now=T4)
    assert (old.valid_to, old.expired_at, closed.valid_to, closed.expired_at) == (None, T4, D('2026-06-01'), None)
    assert close_edge(s, user_id='a', target_edge_id=closed.id, valid_to=D('2026-06-01'), now=T4).id == closed.id
    before = state(s)
    with pytest.raises(InvalidIntervalError):
        close_edge(s, user_id='a', target_edge_id=closed.id, valid_to=D('2026-07-01'), now=T4)
    with pytest.raises(StaleTargetError):
        close_edge(s, user_id='a', target_edge_id=old.id, valid_to=D('2026-06-01'), now=T4)
    assert state(s) == before


@pytest.mark.parametrize('override,error', [({'rel': 'UNKNOWN'}, InvalidAssertionError),
    ({'source': 'UNKNOWN'}, InvalidAssertionError), ({'src_id': 999}, ScopeError),
    ({'dst_id': 999}, ScopeError), ({'rel': 'HAS_ACCOUNT'}, InvalidAssertionError)])
def test_invalid_ontology_and_endpoints(s, override, error):
    pair = nodes(s)
    with pytest.raises(error):
        fact(s, pair, **override)
    assert rows(s) == []
    with pytest.raises(InvalidAssertionError):
        upsert_node(s, user_id='a', kind='unknown', key='x')


def test_caller_controls_commit_and_rollback(factory):
    with factory() as s:
        pair = nodes(s)
        fact(s, pair)
        s.rollback()
    with factory() as s:
        assert rows(s) == []
        assert list(s.scalars(select(KGNode))) == []
        pair = nodes(s)
        old = fact(s, pair)
        s.commit()
        old_id, pair_ids = old.id, [n.id for n in pair]
    def fail_replacement(session, _context, _instances):
        if any(isinstance(row, KGEdge) for row in session.new):
            raise RuntimeError('injected after expiry')
    with factory() as s:
        pair = [s.get(KGNode, i) for i in pair_ids]
        event.listen(s, 'before_flush', fail_replacement)
        with pytest.raises(RuntimeError, match='injected'):
            fact(s, pair, value=4500, valid_from=D('2026-04-01'), now=T2)
        event.remove(s, 'before_flush', fail_replacement)
        # Even without caller rollback, transition's savepoint restored expiry.
        assert [(e.id, e.expired_at) for e in rows(s)] == [(old_id, None)]
        s.rollback()
    with factory() as s:
        assert [(e.id, e.valid_to, e.expired_at) for e in rows(s)] == [(old_id, None, None)]


def test_database_constraints_and_utc_round_trip(s):
    pair = nodes(s)
    def direct(**overrides):
        kwargs = dict(user_id='a', src_id=pair[0].id, dst_id=pair[1].id, rel='SUBSCRIBES',
                      value_json={}, source='projection', evidence_json={},
                      valid_from=D('2026-01-01'), recorded_at=T1)
        kwargs.update(overrides)
        s.add(KGEdge(**kwargs))
        s.flush()
    for overrides in ({'dst_id': 999}, {'user_id': 'missing'},
                      {'valid_to': D('2026-01-01')}, {'expired_at': T1}):
        with pytest.raises(IntegrityError), s.begin_nested():
            direct(**overrides)
    local = T1.astimezone(timezone(timedelta(hours=-5)))
    direct(recorded_at=local)
    for overrides in ({}, {'recorded_at': T2}):
        with pytest.raises(IntegrityError), s.begin_nested():
            direct(**overrides)
    s.expire_all()
    assert rows(s)[0].recorded_at == T1
    assert rows(s)[0].recorded_at.utcoffset() == timedelta(0)
    with pytest.raises(StatementError), s.begin_nested():
        direct(valid_from=D('2026-02-01'), valid_to=D('2026-03-01'), recorded_at=datetime(2026, 2, 10))


def test_full_oracle_raw_versions(s):
    pair = nodes(s)
    original = fact(s, pair)
    increase = fact(s, pair, value={'typical_cents': 4500}, valid_from=D('2026-04-01'), now=T2)
    corrected = fact(s, pair, value={'typical_cents': 4000}, valid_from=D('2026-04-01'),
                     correct=True, target_edge_id=increase.id, now=T3)
    closed = close_edge(s, user_id='a', target_edge_id=corrected.id, valid_to=D('2026-06-01'), now=T4)
    assert [(r.value_json['typical_cents'], r.valid_from, r.valid_to, r.recorded_at, r.expired_at)
            for r in rows(s)] == [
        (3500, D('2026-01-01'), None, T1, T2),
        (3500, D('2026-01-01'), D('2026-04-01'), T2, None),
        (4500, D('2026-04-01'), None, T2, T3),
        (4000, D('2026-04-01'), None, T3, T4),
        (4000, D('2026-04-01'), D('2026-06-01'), T4, None)]
    assert original.id != closed.id


@pytest.mark.parametrize('end', [None, D('2025-12-31'), D('2026-01-01')])
def test_close_invalid_bounds_leave_original_unchanged(s, end):
    pair = nodes(s)
    old = fact(s, pair)
    before = state(s)
    with pytest.raises(InvalidIntervalError):
        close_edge(s, user_id='a', target_edge_id=old.id, valid_to=end, now=T2)
    assert state(s) == before


@pytest.mark.parametrize('stamp', [T1, T1 - timedelta(seconds=1), datetime(2026, 4, 10)])
def test_changes_and_close_reject_invalid_recording_clocks(s, stamp):
    pair = nodes(s)
    old = fact(s, pair)
    before = state(s)
    with pytest.raises(ClockOrderError):
        fact(s, pair, value=4500, valid_from=D('2026-04-01'), now=stamp)
    assert state(s) == before
    with pytest.raises(ClockOrderError):
        close_edge(s, user_id='a', target_edge_id=old.id, valid_to=D('2026-06-01'), now=stamp)
    assert state(s) == before


@pytest.mark.parametrize('override,error', [
    ({'target_edge_id': 123}, InvalidAssertionError),
    ({'correct': True, 'valid_from': D('2026-01-02')}, InvalidIntervalError),
    ({'correct': True, 'rel': 'CONNECTED'}, InvalidAssertionError),
    ({'correct': True, 'dst_id': 'other'}, InvalidAssertionError),
])
def test_correction_identity_guards(s, override, error):
    pair = nodes(s)
    target = fact(s, pair)
    other = upsert_node(s, user_id='a', kind='stream', key='Other')
    before = state(s)
    args = dict(value=4000, now=T2)
    args.update(override)
    if args.get('correct'):
        args['target_edge_id'] = target.id
    if args.get('dst_id') == 'other':
        args['dst_id'] = other.id
    with pytest.raises(error):
        fact(s, pair, **args)
    assert state(s) == before


@pytest.mark.parametrize('field', ['value', 'evidence'])
def test_json_boolean_and_integer_corrections_are_distinct(s, field):
    pair = nodes(s)
    initial = {field: {'nested': [{'flag': True}]}}
    changed = {field: {'nested': [{'flag': 1}]}}
    original = fact(s, pair, **initial)
    corrected = fact(s, pair, correct=True, target_edge_id=original.id, now=T2, **changed)
    assert corrected.id != original.id
    assert len(rows(s)) == 2
    assert original.expired_at == T2
    assert corrected.expired_at is None
    json_field = 'value_json' if field == 'value' else 'evidence_json'
    assert type(getattr(original, json_field)['nested'][0]['flag']) is bool
    assert type(getattr(corrected, json_field)['nested'][0]['flag']) is int


def test_json_boolean_to_integer_real_change_versions_value(s):
    pair = nodes(s)
    original = fact(s, pair, value={'nested': {'flag': True}})
    changed = fact(s, pair, value={'nested': {'flag': 1}}, valid_from=D('2026-04-01'), now=T2)
    assert changed.id != original.id
    assert len(rows(s)) == 3
    assert type(original.value_json['nested']['flag']) is bool
    assert type(changed.value_json['nested']['flag']) is int


def test_json_integer_and_float_are_exactly_distinct(s):
    pair = nodes(s)
    original = fact(s, pair, value={'amount': 1})
    corrected = fact(s, pair, value={'amount': 1.0}, correct=True, target_edge_id=original.id, now=T2)
    assert corrected.id != original.id
    assert type(original.value_json['amount']) is int
    assert type(corrected.value_json['amount']) is float


@pytest.mark.parametrize('user_id,kind', [(None, 'merchant'), (None, 'user'),
                                         ('a', 'concept'), ('a', 'source')])
def test_node_kind_requires_documented_scope(s, user_id, kind):
    with pytest.raises(ScopeError):
        upsert_node(s, user_id=user_id, kind=kind, key='invalid')
    assert list(s.scalars(select(KGNode))) == []


@pytest.mark.parametrize('user_id,src_kind,dst_kind,rel', [
    ('a', 'concept', 'concept', 'IS_A'),
    (None, 'merchant', 'category', 'CATEGORIZED_AS'),
])
def test_malformed_raw_endpoint_scope_cannot_be_asserted(s, user_id, src_kind, dst_kind, rel):
    src = KGNode(user_id=user_id, kind=src_kind, key='raw-src', props_json={})
    dst = KGNode(user_id=user_id, kind=dst_kind, key='raw-dst', props_json={})
    s.add_all([src, dst])
    s.flush()
    with pytest.raises(ScopeError):
        assert_edge(s, user_id=user_id, src_id=src.id, dst_id=dst.id,
                    rel=rel, value={}, valid_from=D('2026-01-01'), now=T1)
    assert rows(s) == []


def test_owned_to_shared_guidance_bridge(s):
    account = upsert_node(s, user_id='a', kind='account', key='external-ID')
    concept = upsert_node(s, user_id=None, kind='concept', key='IRA')
    edge = assert_edge(s, user_id='a', src_id=account.id, dst_id=concept.id,
                       rel='IS_A', value={}, valid_from=D('2026-01-01'), now=T1)
    assert (edge.user_id, edge.src_id, edge.dst_id) == ('a', account.id, concept.id)
