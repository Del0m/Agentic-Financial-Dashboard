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

## `test_recurring_streams_upgrade_and_downgrade`

**What it does:** Upgrades a SQLite file to `c4e8f2a1b6d3` and checks for `recurring_streams`, `alerts.stream_id`, `alerts.signal_key` and its index. Downgrades, and checks they're gone.

**Why it's there:** `alerts` gains a foreign key, which SQLite can only do in batch mode, and the test suite builds its schema from the models, so it wouldn't catch a broken migration.

## `test_low_balance_signal_upgrade_and_downgrade`

**What it does:** Low balance signal upgrade and downgrade.

**Why it's there:** Pins the forecast signal guard, privacy boundary or reversible migration behavior.


## TPC-35 temporal knowledge graph (`f6a2b8c4d901`)

`kg_schema` runs each direct-constraint case against both an Alembic-upgraded
SQLite file and an independently metadata-created SQLite file. Each database
lives under pytest's `tmp_path`; SQLite foreign keys are enabled on every test
connection. No application database or running service is used.

- `test_kg_upgrade_downgrade_and_metadata_parity` upgrades from the previous head
  (`d5f9a3b2c7e4`), preserves an existing user and category rule, compares KG metadata with the
  migrated schema, checks every index's columns, uniqueness and partial predicate,
  inserts real graph nodes and an edge, downgrades the populated KG revision,
  and upgrades again. It verifies the recreated graph tables are empty and the
  existing application data survives both operations.
- `test_kg_foreign_keys_and_interval_checks` bypasses store validation and checks
  nonexistent users/endpoints, empty or reversed valid intervals, and empty or
  reversed transaction intervals.
- `test_kg_node_identity_unique_with_nullable_owner` checks both owned identity
  uniqueness and the shared partial unique index, where a normal SQL unique
  constraint would permit duplicate null owners. It also checks the node-user FK.
- `test_kg_current_cardinality_and_interval_start_constraints` checks open-edge
  cardinality and duplicate bounded interval starts. `CATEGORIZED_AS` enforces
  these across different destinations; other relations use the full triple.
  Expired versions and adjacent bounded intervals remain insertable.
- `test_kg_foreign_keys_preserve_history` checks that deleting a referenced node
  or user fails instead of cascading through history.
- `test_kg_utc_mapping_and_immutable_node_identity` checks both UTC column types,
  normalizes an offset timestamp on roundtrip, rejects a naive timestamp, rejects
  ORM changes to ownership/kind/key, and permits current display metadata updates.
- `test_kg_postgres_ddl_matches_metadata_and_predicates` renders the new migration
  offline and compares both tables and all indexes with PostgreSQL ORM DDL,
  including timezone-aware timestamps and partial predicates. It verifies the
  downgrade drops edges before nodes.

Run from `backend/` with the project's Python environment:

```bash
../venv/bin/python -m pytest tests/test_migrations.py -q
```

Verified on November 3, 2026: **36 passed**, including the existing migration
cases. PostgreSQL coverage is offline SQL rendering only; no PostgreSQL server
was used. Interval overlaps beyond duplicate starts/open cardinality and ownership
compatibility between graph endpoints are store invariants, covered in the KG
store tests. Node identity immutability is an ORM guard; direct SQL writers remain
responsible for preserving that identity.
