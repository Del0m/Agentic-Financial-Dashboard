"""Independent raw-row fixtures for query and isolation tests.

Nothing here calls the graph store or production temporal helpers.
"""
from datetime import date, datetime, timezone
from itertools import count

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import KGEdge, KGNode, User

T1 = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
T2 = datetime(2026, 4, 10, 12, tzinfo=timezone.utc)
T3 = datetime(2026, 5, 10, 12, tzinfo=timezone.utc)
T4 = datetime(2026, 6, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def qsession():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        session.add_all([
            User(id="alice", email="alice@example.test", password_hash="unused", prefs_json={}),
            User(id="bob", email="bob@example.test", password_hash="unused", prefs_json={}),
        ])
        session.flush()
        yield session
    engine.dispose()


_keys = count()


def node(session, *, owner="alice", kind="concept", label="Visible", key=None):
    row = KGNode(user_id=owner, kind=kind, key=key or f"raw-key-{next(_keys)}", label=label, props_json={})
    session.add(row)
    session.flush()
    return row


def edge(session, src, dst, *, owner="alice", rel="IS_A", value=None,
         start=date(2026, 1, 1), end=None, recorded=T1, expired=None,
         evidence=None, source="projection"):
    row = KGEdge(user_id=owner, src_id=src.id, dst_id=dst.id, rel=rel, value_json=value,
                 valid_from=start, valid_to=end, recorded_at=recorded, expired_at=expired,
                 source=source, evidence_json=evidence or {})
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def oracle(qsession):
    user = node(qsession, kind="user", label="Alice")
    stream = node(qsession, kind="stream", label="Gym")
    # Literal rows, not derived with the store/query temporal implementation.
    rows = [
        edge(qsession, user, stream, rel="SUBSCRIBES", value={"typical_cents": 3500},
             start=date(2026, 1, 1), recorded=T1, expired=T2),
        edge(qsession, user, stream, rel="SUBSCRIBES", value={"typical_cents": 3500},
             start=date(2026, 1, 1), end=date(2026, 4, 1), recorded=T2),
        edge(qsession, user, stream, rel="SUBSCRIBES", value={"typical_cents": 4500},
             start=date(2026, 4, 1), recorded=T2, expired=T3),
        edge(qsession, user, stream, rel="SUBSCRIBES", value={"typical_cents": 4000},
             start=date(2026, 4, 1), recorded=T3, expired=T4),
        edge(qsession, user, stream, rel="SUBSCRIBES", value={"typical_cents": 4000},
             start=date(2026, 4, 1), end=date(2026, 6, 1), recorded=T4),
    ]
    return user, stream, rows
