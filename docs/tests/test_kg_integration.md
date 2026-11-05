# Tests: `backend/tests/test_kg_integration.py`

`test_migrated_store_survives_reopen_and_replays_history` checks the complete TPC-35 foundation without invoking an agent, model, Plaid connection, or application service.

## Setup

The test runs Alembic through the current head on a temporary SQLite file. It creates two users and a gym subscription through the public node/store API. Each mutation commits in a separate session; every test-engine connection enables foreign keys. It then disposes the engine and opens a new one before reading the results.

## Temporal oracle

The subscription starts at $35 on January 1, recorded January 10. A change to $45 effective April 1 is recorded April 10. A correction to $40 for that same interval is recorded May 10. Closure effective June 1 is recorded June 10. All recording times are explicit aware-UTC datetimes.

Literal assertions verify that an April 2 query returns $35 before the increase was known, $45 at its recording time, and $40 at the correction time. A June 2 query still returns $40 before the closure is known, then returns no fact at the closure's recording time. Current queries return $40 for April and no fact for June.

## Additional assertions

- Storage retains five audit rows and two current-belief intervals. The original value and open valid interval survive unchanged on the expired first version.
- Current history returns two rows; explicit audit history returns five.
- Snapshot endpoints match the visible edges.
- The other user's snapshot is empty, and direct history access to the first user's stream raises `ScopeError`.
- Triples include the relationship, gym label, $40.00 value, and exact valid bounds.

## Run

From `backend/`, use the project's Python environment:

```bash
python -m pytest tests/test_kg_integration.py -q
```

Verified on 2026-11-03 in `/private/tmp/plaid-tpc35` with the existing project venv interpreter: **1 passed**. The database was created under pytest's temporary directory. This proves SQLite persistence and replay, not PostgreSQL runtime behavior or later agent integration.
