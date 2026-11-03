# Temporal graph store tests

`backend/tests/test_kg_store.py` uses its own temporary SQLite file and SQLAlchemy
sessions, with foreign keys enabled on every connection. It exercises public
store functions and independently inspects persisted ORM rows. No live database,
API, LLM, or projection service is used.

## Covered behavior

- Scoped stable node identity, merchant trimming/casefolding, preservation of
  external ID case, shared null-owner uniqueness, immutable identity fields, and
  mutable display metadata.
- The architecture's scope/kind matrix: concept/source identities are shared;
  all other kinds are owned. Malformed raw endpoints cannot bypass store checks.
  An owned account can link to a shared concept.
- Complete open and bounded retries, canonical JSON object equality, unchanged
  later observations, distinct equal monthly facts, and bounded historical
  retries after a later interval has been accepted. JSON booleans and numbers
  remain distinct at any nesting depth. Exact identity also distinguishes an
  integer from its floating-point representation.
- Explicit targeted correction of open/bounded facts, destination changes only
  for category corrections, stale/missing/altered target rejection, unchanged
  current-target retries, category cardinality, ordered inserts with gaps, and
  overlap/backdated/same-date rejection.
- Real changes, corrections, and closure preserve the superseded version's valid
  bounds and value. The literal five-row historical oracle checks every retained
  value, valid bound, recording timestamp, and expiry independently.
- Closure idempotency, malformed/nonpositive ends, aware UTC clocks, same/backward
  replacement clocks, and source/evidence-only changes requiring corrections.
- Invalid ontology/endpoints, direct FK/unique/temporal database constraints,
  non-UTC aware timestamp round trips, and naive timestamp rejection.
- Caller rollback of successful writes. An injected failure after expiry has
  flushed but before replacements are inserted proves the nested transaction
  restores the old belief even before the caller rolls back.

## Run and result

From `backend/`, run:

```bash
/Users/armindelmo/Documents/College/Fall26/COP6938/Plaid-Agent-CAP6938/venv/bin/python -m pytest tests/test_kg_store.py tests/test_kg_concurrency.py -q
```

On 2026-11-03 the combined command passed **59 tests** (51 store cases and
8 concurrency cases), with no skips or xfails, in 2.62 seconds. A later integration
run may include these cases together with query and migration tests.

Scope: SQLite runtime behavior is verified. The source lock uses a SQL UPDATE
compatible with PostgreSQL row locking, but these tests do not prove PostgreSQL
runtime concurrency. Raw SQL can bypass ORM identity immutability; callers must
use the store API for graph mutations. Display metadata is current, not temporal.
