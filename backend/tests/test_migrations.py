import importlib.util
import io
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

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
