# Tests: `backend/tests/test_migrations.py`

These tests check Alembic migrations against databases the test suite doesn't run: real data on an older revision, run through a throwaway SQLite file, and Postgres, rendered as SQL. A migration that works on an empty test database (`Base.metadata.create_all`) can still fail on either.

Run them from `backend/`:

```bash
../venv/bin/python -m pytest tests/test_migrations.py -v
```

## Shared setup

- `alembic_config()` points Alembic at `backend/alembic` without `alembic.ini`. The ini's logging setup would switch off every logger the rest of the suite relies on.
- The upgrade test patches `app.db.session.DATABASE_URL` (which `alembic/env.py` reads) to a file under `tmp_path`.
- `load(name)` imports one file from `alembic/versions/` as a module.
- `postgres_downgrade_sql(name)` runs that migration's `downgrade()` in Alembic's offline mode with the `postgresql` dialect and returns the SQL it would run. It needs no Postgres server and no driver.

---

## `test_upgrade_backfills_and_downgrades`

**What it does:** Upgrades to the revision before TPC-22, inserts a user, a period and two lines (`FOOD_AND_DRINK`, `ENTERTAINMENT`), then upgrades to `head`. The lines get the names "Food and drink" and "Entertainment" and the groups `needs` and `wants`. `category_rules` and `goals` (TPC-23) exist, periods have `income_cents` and `total_cents`, and transactions have `user_category`. Goals (from TPC-25) also have a `start_date` column. Downgrading one step to the user-run-claims revision removes `start_date`, and downgrading to the start removes the rest.

**Why it's there:** TPC-22 makes `budget_lines.name` and `.group` required. Existing budgets (the demo and seeded users) must be backfilled from the category, using the migration's own copy of the default mapping, or the upgrade fails on their rows.

## `test_budget_tables_downgrade_drops_enum_types`

**What it does:** Renders the Postgres SQL for the budget-tables migration's (`9e005ad8cd4c`) downgrade, and checks that it drops the `budget_period_status`, `budget_line_kind` and `budget_line_source` types.

**Why it's there:** On Postgres an `Enum` column creates a native type, and dropping the table leaves the type behind. So downgrading and then upgrading again failed with `type "budget_period_status" already exists`. The decision log plans for Postgres, but the suite only runs SQLite, which has no enum types, so this could only be caught by rendering the Postgres SQL. The review on #11 found it. The downgrade now runs `DROP TYPE IF EXISTS` for each type on Postgres only. A real SQLite upgrade, downgrade and upgrade still succeeds.
