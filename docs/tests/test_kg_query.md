# Temporal graph query tests

`backend/tests/test_kg_query.py` verifies the complete internal query API without
LLMs, projections, a service, credentials, or the application's live database.
`backend/tests/kg_query_fixtures.py` builds a new in-memory SQLite database per
test, enables foreign keys on its connection, and inserts ORM rows directly.
These query expectations do not depend on the store implementation.

The literal historical oracle retains all five versions of the gym subscription:
3,500 cents at January 10 recording time; a 4,500-cent April increase recorded
April 10; a correction to 4,000 cents recorded May 10; and a June closure
recorded June 10. Cases assert the exact snapshots and endpoints at every row
of the implementation plan's oracle, plus raw values, valid bounds, recording
times, and expiration times. Additional cases check transaction and valid-time
boundaries to the microsecond, current-belief defaults, future facts and gaps,
simulation-date resolution, and explicit valid-date overrides.

History cases cover incoming edges, relation filters, current and historical
knowledge, audit recording cutoffs, and stable ordering. Spending cases cover
positive net months, refund-only and zero-net months, retained corrected
versions, multiple facts within a month, exclusive windows, historical category
values, January rollover, December rollover, and February 29 references.
`months_active` groups monthly facts by their `valid_from` calendar month and
returns distinct positive month starts in `[start, end)`. It never reapplies the
merchant's current category. Changes inspect valid boundaries, deduplicate an
edge with both boundaries in the window, and exclude corrections whose only
event in the window was their recording time.

Neighborhood tests assert deterministic breadth-first traversal in both
directions, cycle protection, requested roots, no repeated edges, zero and
invalid bounds, and exact edge caps. `truncated` means eligible edges inside the
requested hop horizon were omitted by the edge limit; reaching the requested
hop horizon alone is not truncation. Each hop reapplies valid time, transaction
time, and ownership predicates. Triples tests assert exact money formatting
(positive, zero, negative), validity, tax years, and safe provenance labels;
sentinel private keys, properties, and arbitrary evidence/value fields never
appear. Missing display labels fall back to node kinds rather than identity
keys. Validation also covers naive knowledge times and an unrepresentable prior
year.

Verified from the isolated worktree's `backend/` directory on 2026-11-03:

```bash
/Users/armindelmo/Documents/College/Fall26/COP6938/Plaid-Agent-CAP6938/venv/bin/python -m pytest tests/test_kg_query.py tests/test_kg_isolation.py -q
```

Result: **84 passed**, comprising 41 query cases and 43 isolation cases; no skips
or xfails. This proves SQLite query behavior. It does not test PostgreSQL runtime
execution, historical node display metadata, or source-data projection.
