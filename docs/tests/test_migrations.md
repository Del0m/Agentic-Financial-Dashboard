# Tests: `backend/tests/test_migrations.py`

These tests run Alembic revisions against a throwaway SQLite file, so a migration that works on an empty test database (`Base.metadata.create_all`) but not on real data is caught.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_migrations.py -v
```

## Shared setup

- `alembic_config()` points Alembic at `backend/alembic` without `alembic.ini`. The ini's logging setup would switch off every logger the rest of the suite relies on.
- Each test patches `app.db.session.DATABASE_URL` (which `alembic/env.py` reads) to a file under `tmp_path`.

---

## `test_upgrade_backfills_and_downgrades`

**What it does:** Upgrades to the revision before TPC-22, inserts a user, a period and two lines (`FOOD_AND_DRINK`, `ENTERTAINMENT`), then upgrades to `head`. The lines get the names "Food and drink" and "Entertainment" and the groups `needs` and `wants`. `category_rules` exists, periods have `income_cents` and `total_cents`, and transactions have `user_category`. Downgrading removes them all again.

**Why it's there:** TPC-22 makes `budget_lines.name` and `.group` required. Existing budgets (the demo and seeded users) must be backfilled from the category, using the migration's own copy of the default mapping, or the upgrade fails on their rows.
