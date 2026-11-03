"""Exercise the public KG API against an Alembic-created, reopened database."""
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

import app.db.session as db_session
from app.db.models import KGEdge, User
from app.kg.query import as_of, history, triples
from app.kg.store import assert_edge, close_edge, upsert_node
from app.kg.types import ScopeError


def test_migrated_store_survives_reopen_and_replays_history(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'kg.db'}"
    monkeypatch.setattr(db_session, "DATABASE_URL", url)
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.upgrade(config, "head")

    def connect():
        engine = create_engine(url)

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        return engine

    engine = connect()
    times = [datetime(2026, month, 10, 12, tzinfo=timezone.utc) for month in (1, 4, 5, 6)]
    value = lambda cents: {"typical_cents": cents, "frequency": "monthly"}
    evidence = {"label": "Gym statement"}
    try:
        with Session(engine) as session, session.begin():
            session.add_all([User(id="alice", email="alice@kg.test", password_hash="!"),
                             User(id="bob", email="bob@kg.test", password_hash="!")])
            session.flush()
            user = upsert_node(session, user_id="alice", kind="user", key="alice", label="Alice")
            stream = upsert_node(session, user_id="alice", kind="stream", key="gym", label="Gym")
            user_id, stream_id = user.id, stream.id
            first = assert_edge(session, user_id="alice", src_id=user_id, rel="SUBSCRIBES",
                                dst_id=stream_id, value=value(3500), valid_from=date(2026, 1, 1),
                                evidence=evidence, now=times[0])
            original_id = first.id

        with Session(engine) as session, session.begin():
            changed = assert_edge(session, user_id="alice", src_id=user_id, rel="SUBSCRIBES",
                                  dst_id=stream_id, value=value(4500), valid_from=date(2026, 4, 1),
                                  evidence=evidence, now=times[1])
            changed_id = changed.id
            rows = session.scalars(select(KGEdge)).all()
            assert len(rows) == 3
            assert len([row for row in rows if row.expired_at is None]) == 2

        with Session(engine) as session, session.begin():
            corrected = assert_edge(session, user_id="alice", src_id=user_id, rel="SUBSCRIBES",
                                    dst_id=stream_id, value=value(4000), valid_from=date(2026, 4, 1),
                                    evidence=evidence, correct=True, target_edge_id=changed_id,
                                    now=times[2])
            corrected_id = corrected.id

        with Session(engine) as session, session.begin():
            close_edge(session, user_id="alice", target_edge_id=corrected_id,
                       valid_to=date(2026, 6, 1), now=times[3])
    finally:
        engine.dispose()

    engine = connect()
    try:
        with Session(engine) as session:
            oracle = [
                (date(2026, 1, 5), datetime(2026, 1, 9, tzinfo=timezone.utc), []),
                (date(2026, 3, 31), datetime(2026, 7, 1, tzinfo=timezone.utc), [3500]),
                (date(2026, 4, 2), datetime(2026, 3, 1, tzinfo=timezone.utc), [3500]),
                (date(2026, 4, 2), times[1], [4500]),
                (date(2026, 4, 2), times[2], [4000]),
                (date(2026, 6, 2), datetime(2026, 6, 1, tzinfo=timezone.utc), [4000]),
                (date(2026, 6, 2), times[3], []),
                (date(2026, 4, 2), None, [4000]),
                (date(2026, 6, 2), None, []),
            ]
            for valid_date, known_at, expected in oracle:
                snapshot = as_of(session, user_id="alice", on_date=valid_date, known_at=known_at)
                assert [edge.value_json["typical_cents"] for edge in snapshot.edges] == expected
                assert {node.id for node in snapshot.nodes} == ({user_id, stream_id} if expected else set())

            rows = session.scalars(select(KGEdge).order_by(KGEdge.id)).all()
            assert len(rows) == 5
            assert all(row.user_id == "alice" for row in rows)
            current = sorted((row for row in rows if row.expired_at is None), key=lambda row: row.valid_from)
            assert [(r.valid_from, r.valid_to, r.value_json["typical_cents"]) for r in current] == [
                (date(2026, 1, 1), date(2026, 4, 1), 3500),
                (date(2026, 4, 1), date(2026, 6, 1), 4000),
            ]
            original = session.get(KGEdge, original_id)
            assert original.valid_to is None and original.value_json == value(3500)
            assert original.recorded_at == times[0] and original.expired_at == times[1]
            assert session.get(KGEdge, changed_id).value_json == value(4500)
            assert len(history(session, user_id="alice", node_id=stream_id)) == 2
            assert len(history(session, user_id="alice", node_id=stream_id, audit=True)) == 5
            assert as_of(session, user_id="bob", on_date=date(2026, 4, 2)).edges == []
            with pytest.raises(ScopeError):
                history(session, user_id="bob", node_id=stream_id)
            lines = triples(as_of(session, user_id="alice", on_date=date(2026, 4, 2)))
            assert len(lines) == 1
            assert "Gym" in lines[0] and "$40.00" in lines[0] and "SUBSCRIBES" in lines[0]
            assert "2026-04-01" in lines[0] and "2026-06-01" in lines[0]
    finally:
        engine.dispose()
