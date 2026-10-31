from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import app.db.session as db_session

BACKEND = Path(__file__).resolve().parents[1]
BEFORE = "e0cdfdbdab45"


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
    assert rows == [("FOOD_AND_DRINK", "Food and drink", "needs"), ("ENTERTAINMENT", "Entertainment", "wants")]
    assert "category_rules" in tables
    assert {"income_cents", "total_cents"} <= period_cols and "user_category" in txn_cols

    command.downgrade(cfg, BEFORE)
    with engine.connect() as c:
        assert "category_rules" not in sa.inspect(c).get_table_names()
        assert "name" not in {col["name"] for col in sa.inspect(c).get_columns("budget_lines")}
    engine.dispose()
