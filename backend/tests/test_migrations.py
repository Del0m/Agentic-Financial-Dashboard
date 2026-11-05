import importlib.util
import io
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.orm import Session

from app.db.base import Base, UTCDateTime
from app.db.models import KGEdge, KGNode

import app.db.session as db_session

BACKEND = Path(__file__).resolve().parents[1]
BEFORE = "e0cdfdbdab45"
VERSIONS = BACKEND / "alembic" / "versions"


# no alembic.ini: its logging config would switch off every logger the other tests rely on
def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return cfg


def test_upgrade_backfills_and_downgrades(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setattr(db_session, "DATABASE_URL", url)      # env.py reads it from here
    cfg = alembic_config()
    command.upgrade(cfg, BEFORE)
    engine = sa.create_engine(url)
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO users (id, email, password_hash, created_at, prefs_json) "
                          "VALUES ('u1', 'a@b.c', '!', '2026-10-01 00:00:00', '{}')"))
        c.execute(sa.text("INSERT INTO budget_periods (id, user_id, start_date, end_date, status, created_at) "
                          "VALUES (1, 'u1', '2026-10-01', '2026-10-31', 'open', '2026-10-01 00:00:00')"))
        for i, cat in ((1, "FOOD_AND_DRINK"), (2, "ENTERTAINMENT")):
            c.execute(sa.text("INSERT INTO budget_lines (id, period_id, category, kind, limit_cents, spent_cents, "
                              "source) VALUES (:i, 1, :cat, 'flexible', 10000, 0, 'init')"), {"i": i, "cat": cat})

    command.upgrade(cfg, "head")
    with engine.connect() as c:
        rows = c.execute(sa.text('SELECT category, name, "group" FROM budget_lines ORDER BY id')).all()
        tables = sa.inspect(c).get_table_names()
        period_cols = {col["name"] for col in sa.inspect(c).get_columns("budget_periods")}
        txn_cols = {col["name"] for col in sa.inspect(c).get_columns("transactions")}
        goal_cols = {col["name"] for col in sa.inspect(c).get_columns("goals")}
    assert rows == [("FOOD_AND_DRINK", "Food and drink", "needs"), ("ENTERTAINMENT", "Entertainment", "wants")]
    assert "category_rules" in tables and "goals" in tables and "user_run_claims" in tables
    assert {"income_cents", "total_cents"} <= period_cols and "user_category" in txn_cols
    assert "start_date" in goal_cols

    command.downgrade(cfg, "8f4b2a7c901e")
    with engine.connect() as c:
        assert "start_date" not in {col["name"] for col in sa.inspect(c).get_columns("goals")}

    command.downgrade(cfg, BEFORE)
    with engine.connect() as c:
        assert not {"category_rules", "goals", "user_run_claims"} & set(sa.inspect(c).get_table_names())
        assert "name" not in {col["name"] for col in sa.inspect(c).get_columns("budget_lines")}
    engine.dispose()


def load(name):
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# the sql a migration's downgrade would run on postgres, rendered offline (no server or driver needed)
def postgres_downgrade_sql(name) -> str:
    out = io.StringIO()
    ctx = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": out})
    with Operations.context(ctx):
        load(name).downgrade()
    return out.getvalue()


# review on #11: postgres keeps native enum types after their tables are dropped, so a later upgrade would fail
def test_budget_tables_downgrade_drops_enum_types():
    sql = postgres_downgrade_sql("9e005ad8cd4c_budget_tables")

    for enum in ("budget_period_status", "budget_line_kind", "budget_line_source"):
        assert f"DROP TYPE IF EXISTS {enum}" in sql


def test_recurring_streams_upgrade_and_downgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setattr(db_session, "DATABASE_URL", url)
    cfg = alembic_config()
    command.upgrade(cfg, "c4e8f2a1b6d3")
    engine = sa.create_engine(url)
    inspector = sa.inspect(engine)
    assert "recurring_streams" in inspector.get_table_names()
    assert {"stream_id", "signal_key"} <= {c["name"] for c in inspector.get_columns("alerts")}
    assert "ix_alerts_user_signal_key" in {i["name"] for i in inspector.get_indexes("alerts")}
    engine.dispose()

    command.downgrade(cfg, "a3d9e1b7c2f5")
    engine = sa.create_engine(url)
    inspector = sa.inspect(engine)
    assert "recurring_streams" not in inspector.get_table_names()
    assert not {"stream_id", "signal_key"} & {c["name"] for c in inspector.get_columns("alerts")}
    engine.dispose()


def test_low_balance_signal_upgrade_and_downgrade(tmp_path, monkeypatch):
    url=f"sqlite:///{tmp_path / 'forecast.db'}"
    monkeypatch.setattr(db_session,'DATABASE_URL',url)
    cfg=alembic_config();command.upgrade(cfg,'head')
    engine=sa.create_engine(url)
    assert {'signal_cents','signal_days'} <= {c['name'] for c in sa.inspect(engine).get_columns('alerts')}
    engine.dispose();command.downgrade(cfg,'c4e8f2a1b6d3')
    engine=sa.create_engine(url)
    assert not {'signal_cents','signal_days'} & {c['name'] for c in sa.inspect(engine).get_columns('alerts')}
    engine.dispose()


# TPC-35: tests use only disposable file-backed SQLite databases.

KG_REVISION = "f6a2b8c4d901"
KG_MIGRATION = "f6a2b8c4d901_temporal_knowledge_graph"


@pytest.fixture(params=["migration", "metadata"])
def kg_schema(request, tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'kg.db'}"
    if request.param == "migration":
        monkeypatch.setattr(db_session, "DATABASE_URL", url)
        command.upgrade(alembic_config(), KG_REVISION)
    engine = sa.create_engine(url)

    @sa.event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    if request.param == "metadata":
        Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id,email,password_hash,created_at,prefs_json) "
                                   "VALUES ('u1','kg@example.org','!', '2026-01-01','{}')"))
        connection.execute(KGNode.__table__.insert(), [
            {"id": 1, "user_id": "u1", "kind": "merchant", "key": "gym", "props_json": {}},
            {"id": 2, "user_id": "u1", "kind": "category", "key": "health", "props_json": {}},
            {"id": 3, "user_id": "u1", "kind": "category", "key": "other", "props_json": {}},
        ])
    yield engine
    engine.dispose()


def test_kg_upgrade_downgrade_and_metadata_parity(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'kg-migrate.db'}"
    monkeypatch.setattr(db_session, "DATABASE_URL", url)
    cfg = alembic_config()
    command.upgrade(cfg, "d5f9a3b2c7e4")
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO users (id,email,password_hash,created_at,prefs_json) "
                                   "VALUES ('existing','existing@example.org','!', '2026-01-01','{}')"))
        connection.execute(sa.text("INSERT INTO category_rules "
                                   "(user_id,merchant_name,category,created_at) VALUES "
                                   "('existing','gym','HEALTH','2026-01-01')"))
    command.upgrade(cfg, KG_REVISION)
    with engine.begin() as connection:
        connection.execute(KGNode.__table__.insert(), [
            {"id": 1, "user_id": "existing", "kind": "user", "key": "existing", "props_json": {}},
            {"id": 2, "user_id": "existing", "kind": "merchant", "key": "gym", "props_json": {}},
        ])
        connection.execute(KGEdge.__table__.insert(), _kg_edge(user_id="existing"))
    with engine.connect() as connection:
        assert {"kg_nodes", "kg_edges"} <= set(sa.inspect(connection).get_table_names())
        ctx = MigrationContext.configure(connection, opts={
            "compare_type": True,
            "include_object": lambda obj, name, type_, reflected, compare_to:
                name.startswith("kg_") if type_ == "table" else True,
        })
        assert compare_metadata(ctx, Base.metadata) == []
        for table in (KGNode.__table__, KGEdge.__table__):
            reflected = {index["name"]: index for index in sa.inspect(connection).get_indexes(table.name)}
            assert set(reflected) == {index.name for index in table.indexes}
            for index in table.indexes:
                actual = reflected[index.name]
                assert actual["column_names"] == [column.name for column in index.columns]
                assert bool(actual["unique"]) == index.unique
                assert str(actual.get("dialect_options", {}).get("sqlite_where")) == str(index.dialect_options["sqlite"].get("where"))
    command.downgrade(cfg, "d5f9a3b2c7e4")
    with engine.connect() as connection:
        assert not {"kg_nodes", "kg_edges"} & set(sa.inspect(connection).get_table_names())
        assert connection.scalar(sa.text("SELECT id FROM users")) == "existing"
        assert connection.execute(sa.text(
            "SELECT user_id,merchant_name,category FROM category_rules"
        )).all() == [("existing", "gym", "HEALTH")]
    command.upgrade(cfg, KG_REVISION)
    with engine.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(KGEdge)) == 0
        assert connection.scalar(sa.select(sa.func.count()).select_from(KGNode)) == 0
        assert connection.execute(sa.text(
            "SELECT user_id,merchant_name,category FROM category_rules"
        )).all() == [("existing", "gym", "HEALTH")]
    engine.dispose()


def _kg_edge(**overrides):
    row = dict(user_id="u1", src_id=1, rel="SPENDS_AT", dst_id=2,
               value_json={"cents": 3500}, valid_from=date(2026, 1, 1), valid_to=None,
               recorded_at=datetime(2026, 1, 10, tzinfo=timezone.utc), expired_at=None,
               source="projection", evidence_json={})
    row.update(overrides)
    return row


@pytest.mark.parametrize("overrides", [
    {"user_id": "missing"}, {"src_id": 999}, {"dst_id": 999},
    {"valid_to": date(2026, 1, 1)}, {"valid_to": date(2025, 12, 31)},
    {"expired_at": datetime(2026, 1, 10, tzinfo=timezone.utc)},
    {"expired_at": datetime(2026, 1, 9, tzinfo=timezone.utc)},
])
def test_kg_foreign_keys_and_interval_checks(kg_schema, overrides):
    with pytest.raises(sa.exc.IntegrityError), kg_schema.begin() as connection:
        connection.execute(KGEdge.__table__.insert(), _kg_edge(**overrides))


@pytest.mark.parametrize("user_id", [None, "u1"])
def test_kg_node_identity_unique_with_nullable_owner(kg_schema, user_id):
    row = dict(user_id=user_id, kind="concept", key="ira", props_json={})
    with kg_schema.begin() as connection:
        connection.execute(KGNode.__table__.insert(), row)
    with pytest.raises(sa.exc.IntegrityError), kg_schema.begin() as connection:
        connection.execute(KGNode.__table__.insert(), row)
    with pytest.raises(sa.exc.IntegrityError), kg_schema.begin() as connection:
        connection.execute(KGNode.__table__.insert(), dict(row, user_id="missing"))


@pytest.mark.parametrize("relation", ["SPENDS_AT", "CATEGORIZED_AS"])
@pytest.mark.parametrize("bounded", [False, True])
def test_kg_current_cardinality_and_interval_start_constraints(kg_schema, relation, bounded):
    row = _kg_edge(rel=relation, valid_to=date(2026, 2, 1) if bounded else None)
    with kg_schema.begin() as connection:
        connection.execute(KGEdge.__table__.insert(), row)
    duplicate = dict(row)
    if not bounded:
        duplicate["valid_from"] = date(2026, 1, 2)
    if relation == "CATEGORIZED_AS":
        duplicate["dst_id"] = 3
    with pytest.raises(sa.exc.IntegrityError), kg_schema.begin() as connection:
        connection.execute(KGEdge.__table__.insert(), duplicate)
    with kg_schema.begin() as connection:
        connection.execute(KGEdge.__table__.insert(), dict(duplicate,
            expired_at=datetime(2026, 2, 10, tzinfo=timezone.utc)))
    if bounded:
        with kg_schema.begin() as connection:
            connection.execute(KGEdge.__table__.insert(), dict(row,
                valid_from=date(2026, 2, 1), valid_to=date(2026, 3, 1)))


def test_kg_foreign_keys_preserve_history(kg_schema):
    with kg_schema.begin() as connection:
        connection.execute(KGEdge.__table__.insert(), _kg_edge())
    for statement in ("DELETE FROM kg_nodes WHERE id=1", "DELETE FROM users WHERE id='u1'"):
        with pytest.raises(sa.exc.IntegrityError), kg_schema.begin() as connection:
            connection.execute(sa.text(statement))


def test_kg_utc_mapping_and_immutable_node_identity(kg_schema):
    assert isinstance(KGEdge.__table__.c.recorded_at.type, UTCDateTime)
    assert isinstance(KGEdge.__table__.c.expired_at.type, UTCDateTime)
    offset_time = datetime(2026, 1, 10, 8, tzinfo=timezone(timedelta(hours=-4)))
    with Session(kg_schema) as session:
        edge = KGEdge(**_kg_edge(recorded_at=offset_time))
        session.add(edge)
        session.commit()
        session.expire_all()
        assert edge.recorded_at == datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
        assert edge.recorded_at.tzinfo is timezone.utc
    with pytest.raises(sa.exc.StatementError), kg_schema.begin() as connection:
        connection.execute(KGEdge.__table__.insert(), _kg_edge(recorded_at=datetime(2026, 2, 1)))
    for attribute, replacement in (("key", "renamed"), ("kind", "concept"), ("user_id", None)):
        with Session(kg_schema) as session:
            node = session.get(KGNode, 1)
            setattr(node, attribute, replacement)
            with pytest.raises(ValueError, match="immutable"):
                session.flush()
            session.rollback()
    with Session(kg_schema) as session:
        node = session.get(KGNode, 1)
        node.label = "Updated label"
        node.props_json = {"display": "updated"}
        session.commit()


def test_kg_postgres_ddl_matches_metadata_and_predicates():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable

    output = io.StringIO()
    ctx = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(ctx):
        load(KG_MIGRATION).upgrade()
    sql = output.getvalue()
    normalize = lambda value: re.sub(r"\s+", " ", str(value)).strip().rstrip(";")
    for table in (KGNode.__table__, KGEdge.__table__):
        assert normalize(CreateTable(table).compile(dialect=postgresql.dialect())) in normalize(sql)
        for index in table.indexes:
            assert normalize(CreateIndex(index).compile(dialect=postgresql.dialect())) in normalize(sql)
    assert sql.count("TIMESTAMP WITH TIME ZONE") == 2
    assert "ON DELETE CASCADE" not in sql
    down = postgres_downgrade_sql(KG_MIGRATION)
    assert down.index("DROP TABLE kg_edges") < down.index("DROP TABLE kg_nodes")


def test_rule_effective_date_migration_preserves_existing_rows(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'rule-date.db'}"
    monkeypatch.setattr(db_session, "DATABASE_URL", url)
    cfg = alembic_config()
    command.upgrade(cfg, KG_REVISION)
    engine = sa.create_engine(url)
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO users (id,email,password_hash,created_at,prefs_json) "
                          "VALUES ('u','u@example.test','!', '2026-01-01','{}')"))
        c.execute(sa.text("INSERT INTO category_rules (user_id,merchant_name,category,created_at) "
                          "VALUES ('u','cafe','FOOD_AND_DRINK','2026-01-01')"))
    command.upgrade(cfg, "head")
    with engine.begin() as c:
        assert c.execute(sa.text("SELECT category,effective_from FROM category_rules")).one() == ("FOOD_AND_DRINK", None)
        ctx = MigrationContext.configure(c, opts={
            "compare_type": True,
            "include_object": lambda obj, name, type_, reflected, compare_to:
                name == "category_rules" if type_ == "table" else True,
        })
        assert compare_metadata(ctx, Base.metadata) == []
        c.execute(sa.text("UPDATE category_rules SET effective_from='2026-09-15'"))
    command.downgrade(cfg, KG_REVISION)
    with engine.connect() as c:
        assert "effective_from" not in {col["name"] for col in sa.inspect(c).get_columns("category_rules")}
        assert c.scalar(sa.text("SELECT category FROM category_rules")) == "FOOD_AND_DRINK"
    engine.dispose()
