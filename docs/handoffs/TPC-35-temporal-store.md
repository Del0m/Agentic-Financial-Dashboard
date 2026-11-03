# TPC-35 handoff to graph producers

TPC-35 provides the schema and internal Python API. TPC-36 can now project source rows into it; TPC-38 can load shared guidance. Agents do not read or populate this graph until their integration tickets are implemented.

## Transaction and identity contract

Import `upsert_node`, `assert_edge`, and `close_edge` from `app.kg.store`. Every call accepts a SQLAlchemy session and explicit keyword-only `user_id`. The caller authenticates that scope and owns commit/rollback. The store flushes; it never commits. Update source data and its graph projection in the same transaction when they must remain consistent.

`user_id=None` is shared maintenance, not an unrestricted administrative query. Shared nodes have kind `concept` or `source`; all other kinds belong to a user. Owned edges start at an owned node and may end at that user's node or a shared concept. Shared edges connect shared nodes only. Identity is `(user_id, kind, key)`; merchant keys are trimmed and casefolded. Other external keys retain case.

`upsert_node` returns a `KGNode`; `assert_edge` and `close_edge` return the current `KGEdge`, including on an idempotent retry. Node labels/props are current metadata. Store all facts needed for temporal replay on edges.

## Projection rules

- Valid bounds are Python `date` objects with an exclusive end. Convert an inclusive budget-period end to the following day. Monthly facts use the first day of the month through the first day of the next month.
- Backfill each relationship in chronological order. An exact retry of an older bounded assertion is safe; a new out-of-order or overlapping assertion raises `OverlapError`.
- A later real-world change to an ongoing fact uses `assert_edge` with the new value and later `valid_from`. This expires the old belief and writes its bounded replacement plus the new value atomically.
- For changed monthly totals, evidence, or a retroactive categorization, load the current-belief interval and call `assert_edge(correct=True, target_edge_id=...)`, preserving its exact valid bounds. Changing a bounded interval in place or reasserting a different value without an explicit correction is rejected.
- A category correction may change its destination. Other corrections preserve source, relation, and destination. A real category replacement follows chronological change semantics.
- End a removed rule or relationship using `close_edge(target_edge_id=..., valid_to=...)`. Reload a current version after a stale-target error. Repeating the same close against the bounded current replacement is a no-op.
- JSON identity is type-sensitive: booleans, integers, and floating-point representations remain distinct. Use integer cents consistently and stable evidence payloads; JSON object key order does not matter.
- `now` is an optional aware-UTC recording timestamp for controlled tests. Normal producers omit it. Never pass the simulated date as recording time or pretend a backfill was known before it was performed.

## Queries and errors

`app.kg.query` exports `as_of`, `history`, `months_active`, `same_month_last_year`, `changes`, `neighborhood`, and `triples`. Database functions require a session and explicit user scope. User reads include their own graph plus shared guidance, while shared-scope reads include shared guidance only.

- `as_of(..., on_date=..., known_at=...)` returns `GraphSnapshot(nodes, edges)`. Omitting `known_at` selects current beliefs; omitting `on_date` uses the user's simulation date or the application's real calendar date.
- `history(..., node_id=..., audit=True)` explicitly includes retained expired versions. `known_at` in audit mode is a recording cutoff.
- `months_active` returns first-of-month dates in `[start, end)` with positive net monthly spending. `SPENDS_AT` payloads use `cents`, `count`, and `category`.
- `same_month_last_year` returns spending edges using the category stored on each historical fact. It does not apply today's category rules to last year's facts.
- `changes` returns each edge once if its valid start or end falls in the window. A correction's recording time is not a new valid-time event.
- `neighborhood` includes requested root nodes and returns `truncated=True` when its edge limit omits eligible edges within the requested hop horizon. Traversal is bidirectional and scoped at every hop.
- `triples(snapshot)` formats readable lines with selected value/provenance fields. It is not an authentication boundary: pass a scoped snapshot. Agent integration should still use its normal privacy gateway.

Public validation errors live in `app.kg.types`: `ScopeError`, `InvalidAssertionError`, `InvalidIntervalError`, `OverlapError`, `StaleTargetError`, and `ClockOrderError`, all under `KGError`. Database lock/uniqueness exceptions remain database exceptions. On contention, roll back the caller's transaction and retry explicitly after reloading state; do not continue with a failed session. Source-row writes serialize assertion changes across database connections.

## Evidence and limits

The focused suite covers persisted historical replay, atomic rollback, ownership, separate-connection SQLite contention, Alembic migration parity, and reopening a migrated database. See the TPC-35 plan's implementation record and `docs/tests/test_kg_*.md` for actual commands/results.

PostgreSQL DDL renders correctly, but PostgreSQL runtime behavior has not been tested. Raw SQL can bypass ontology, scope, and ORM identity validation; producers must use this store. Historical node labels are not versioned. Projection/backfill, guidance extraction, agent wiring, and ablation evaluation remain their own tickets.
