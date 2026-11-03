"""Real SQLite lock contention: independent sessions and file connections."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Event, current_thread

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import KGEdge, KGNode, User
from app.kg.store import assert_edge, upsert_node
from app.kg.types import StaleTargetError

JAN, APR = date(2026, 1, 1), date(2026, 4, 1)
T1 = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
T2 = datetime(2026, 4, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "concurrency.db"}',
                           connect_args={'timeout': 0.2, 'check_same_thread': False})
    connections = set()
    @event.listens_for(engine, 'connect')
    def configure(dbapi, _):
        dbapi.execute('PRAGMA foreign_keys=ON')
        connections.add(id(dbapi))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as s:
        s.add(User(id='a', email='a@tests.local', password_hash='x'))
    yield engine, factory, connections
    engine.dispose()


def seed(factory, shared, initial=False):
    owner = None if shared else 'a'
    with factory.begin() as s:
        src = upsert_node(s, user_id=owner, kind='concept' if shared else 'user', key='source')
        dst = upsert_node(s, user_id=owner, kind='concept' if shared else 'stream', key='destination')
        args = dict(user_id=owner, src_id=src.id, dst_id=dst.id,
                    rel='HAS_LIMIT' if shared else 'SUBSCRIBES',
                    value={'cents': 3500}, valid_from=JAN, now=T1)
        target = assert_edge(s, **args).id if initial else None
    return args, target


def inspect(factory):
    with factory() as s:
        all_rows = list(s.scalars(select(KGEdge).order_by(KGEdge.id)))
        current = [r for r in all_rows if r.expired_at is None]
        # Independent overlap and open-version assertions, not store helpers.
        for i, left in enumerate(current):
            for right in current[i + 1:]:
                assert ((left.valid_to is not None and left.valid_to <= right.valid_from)
                        or (right.valid_to is not None and right.valid_to <= left.valid_from))
        assert sum(r.valid_to is None for r in current) <= 1
        return [(r.value_json, r.valid_from, r.valid_to, r.recorded_at, r.expired_at)
                for r in all_rows]


def contend(engine, factory, first, second):
    locked, release, second_started = Event(), Event(), Event()
    first_thread = []
    def hold_source_lock(_conn, _cursor, statement, _params, _context, _many):
        if (current_thread().name == first_thread[0]
                and statement.lstrip().upper().startswith('UPDATE KG_NODES')):
            locked.set()
            assert release.wait(10), 'main test failed to release source lock'
    event.listen(engine, 'after_cursor_execute', hold_source_lock)
    def writer_one():
        first_thread.append(current_thread().name)
        with factory.begin() as s:
            return first(s).id
    def writer_two():
        assert locked.wait(10)
        second_started.set()
        with factory.begin() as s:
            return second(s).id
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(writer_one)
            assert locked.wait(10)
            two = pool.submit(writer_two)
            assert second_started.wait(10)
            try:
                with pytest.raises(OperationalError, match='locked'):
                    two.result(timeout=10)
            finally:
                release.set()
            winner = one.result(timeout=10)
    finally:
        release.set()
        event.remove(engine, 'after_cursor_execute', hold_source_lock)
    return winner


@pytest.mark.parametrize('shared', [False, True])
def test_concurrent_identical_assertions(database, shared):
    engine, factory, connections = database
    args, _ = seed(factory, shared)
    winner = contend(engine, factory,
                     lambda s: assert_edge(s, **args), lambda s: assert_edge(s, **args))
    # Failed writer's context rolled back; a fresh session may explicitly retry.
    with factory.begin() as s:
        assert assert_edge(s, **args).id == winner
    assert inspect(factory) == [({'cents': 3500}, JAN, None, T1, None)]
    assert len(connections) >= 2


@pytest.mark.parametrize('shared', [False, True])
def test_concurrent_corrections_of_same_target(database, shared):
    engine, factory, connections = database
    args, target = seed(factory, shared, initial=True)
    first = dict(args, value={'cents': 4000}, correct=True, target_edge_id=target, now=T2)
    second = dict(args, value={'cents': 4500}, correct=True, target_edge_id=target, now=T2)
    contend(engine, factory, lambda s: assert_edge(s, **first), lambda s: assert_edge(s, **second))
    with factory() as s:
        with pytest.raises(StaleTargetError):
            assert_edge(s, **second)
        s.rollback()
    assert inspect(factory) == [({'cents': 3500}, JAN, None, T1, T2),
                                ({'cents': 4000}, JAN, None, T2, None)]
    assert len(connections) >= 2


@pytest.mark.parametrize('shared', [False, True])
@pytest.mark.parametrize('commit', [False, True])
def test_reader_never_observes_partial_version_transition(database, shared, commit):
    engine, factory, connections = database
    args, _ = seed(factory, shared, initial=True)
    expired, release = Event(), Event()
    def hold_expiry(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith('UPDATE KG_EDGES'):
            expired.set()
            assert release.wait(10), 'main test failed to release expiry pause'
    event.listen(engine, 'after_cursor_execute', hold_expiry)
    def writer():
        with factory() as s:
            assert_edge(s, **dict(args, value={'cents': 4500}, valid_from=APR, now=T2))
            s.commit() if commit else s.rollback()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            assert expired.wait(10)
            try:
                # A second connection reads the still-complete committed belief
                # while the writer has flushed expiry but not the replacements.
                assert inspect(factory) == [({'cents': 3500}, JAN, None, T1, None)]
            finally:
                release.set()
            future.result(timeout=10)
    finally:
        release.set()
        event.remove(engine, 'after_cursor_execute', hold_expiry)
    expected = [({'cents': 3500}, JAN, None, T1, None)]
    if commit:
        expected = [({'cents': 3500}, JAN, None, T1, T2),
                    ({'cents': 3500}, JAN, APR, T2, None),
                    ({'cents': 4500}, APR, None, T2, None)]
    assert inspect(factory) == expected
    assert len(connections) >= 2
