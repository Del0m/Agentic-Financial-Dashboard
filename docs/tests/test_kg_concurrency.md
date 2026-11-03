# Temporal graph concurrency tests

`backend/tests/test_kg_concurrency.py` creates a disposable **file-backed SQLite**
database for every case, enables foreign keys on every connection, and uses
independent SQLAlchemy sessions and DBAPI connections. It explicitly verifies
that at least two physical connections participated. It does not use StaticPool,
Python-only locks as the serialization mechanism, timing sleeps, external APIs,
or the application's developer database.

## Synchronization and assertions

A database event pauses writer one immediately after its source-node UPDATE has
acquired the real database write lock. A second worker then attempts the same
source write while the first holds the lock. The 200 ms SQLite timeout produces
an explicit lock conflict, which the losing caller rolls back. Events have
bounded waits, and futures have bounded result timeouts.

Both owned and shared sources exercise:

1. Identical concurrent assertions: one commit wins; an explicit retry after
   rollback returns its existing ID. Reopening a fresh session finds exactly one
   assertion with the original recording time.
2. Corrections of the same target: one replacement wins; retrying the old ID after
   rollback raises `StaleTargetError`. Exact raw results contain the original
   expired assertion and the sole current replacement.
3. Reader visibility during a version transition: the writer pauses after expiry
   is flushed and before replacement insertion. An independent reader sees the
   complete committed original belief. After writer commit it sees all three
   transition rows and two adjacent current intervals; after rollback it sees
   only the intact original. Both outcomes are separately parameterized.

Each final inspection opens a fresh session, checks the literal retained rows,
checks at most one current open version, and independently checks every pair of
current intervals for overlap. No production overlap or temporal-query helper is
used to derive expected outcomes.

## Run and result

From `backend/`, run:

```bash
/Users/armindelmo/Documents/College/Fall26/COP6938/Plaid-Agent-CAP6938/venv/bin/python -m pytest tests/test_kg_store.py tests/test_kg_concurrency.py -q
```

On 2026-11-03 all **59 tests** passed, including **8 concurrency cases**, with no
skips or xfails, in 2.62 seconds. This proves the SQLite locking and transaction
behavior exercised above. PostgreSQL runtime locking remains unverified; its
SQL compatibility/DDL is covered separately by migration tests.
