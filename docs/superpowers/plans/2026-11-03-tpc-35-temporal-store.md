# TPC-35 — Temporal graph store and query API

Date: 2026-11-03
Status: Implemented and verified locally on 2026-11-03 in the isolated TPC-35 worktree; ready for review. Not merged or applied to a live database. Armin selected Option A and approved this plan and its file boundaries before implementation.

## Overview

Build the deterministic persistence and query layer for the financial knowledge graph described in `docs/architecture.md` sections 6b.1–6b.2. Store entities and relationships in the existing application database, preserving both when a relationship was true and when the application believed it. This is coursework infrastructure for the later graph projection and agent integration tickets.

TPC-29 remains Armin's writing task; Codex will verify that writeup against the implementation and results when requested.

## Constraints and boundaries

- User decision: **Option A — chronological changes, explicit corrections to existing intervals, and rejection of ambiguous overlaps.** No automatic splitting of arbitrary backdated edits.
- Keep Python, SQLAlchemy, Alembic, SQLite, and the existing PostgreSQL-compatible schema approach. Add no graph database service or LLM dependency.
- TPC-35 supplies models, migration, store, queries, tests, and test documentation. TPC-36 supplies projection, backfill, and budget-loop hooks; TPC-38 supplies guidance extraction; TPC-37/TPC-39 supply agent integration; TPC-40 supplies ablation evaluation.
- Planning occurs alongside active TPC-27 work. Implementation should use a separate worktree and `codex/tpc-35-temporal-store` branch from an agreed stable base. Recheck the migration head then; do not switch the active benchmark checkout or run migrations against its database.
- Tests use temporary databases and deterministic clocks. No model calls are needed.

## Decisions

| Area | Decision | Why | Alternative | Reversibility |
|---|---|---|---|---|
| Historical edits | Chronological changes plus corrections to a named existing interval (user selected) | Meets projection needs without arbitrary interval surgery | Automatic overlap splitting | Medium |
| Storage | Existing SQLAlchemy models and Alembic | Fits deployment and test infrastructure | Separate graph database | Hard after data accumulates |
| Valid time | Calendar dates; half-open intervals `[valid_from, valid_to)` | Monthly budgets, tax years, and daily simulation share this granularity | Timestamp-level valid time | Medium |
| Transaction time | Aware UTC datetimes through existing `UTCDateTime`; half-open intervals | Supports historical belief replay on SQLite | Naive times or simulation dates | Hard |
| Versioning | Expire superseded assertions and insert replacements atomically, including when closing an interval | Changing `valid_to` in place would rewrite past beliefs | In-place interval mutation | Hard |
| API | Internal Python functions receiving a caller-owned SQLAlchemy session | Later projection can commit data and graph changes together | HTTP routes or independent commits | Easy |
| Ownership | Explicit user scope for every read/write; shared guidance is separately writable | Prevents cross-user traversal and shared-data mutation through user operations | Implicit ownership inferred from node IDs | Hard |
| Node metadata | Stable identity; current display metadata only | Ticket temporalizes relationships, not every node property | Fully versioned nodes | Medium |

Only the first row is an explicit user choice. Other rows retain the existing design or are defaults proposed for implementation, not separately approved decisions.

## File edit boundaries

This is the edit contract for TPC-35. Paths are relative to the repository root in the isolated TPC-35 worktree. Reading other source files for context is allowed.

### Existing files allowed to change

| File | Allowed change |
|---|---|
| `backend/app/db/models.py` | Add `KGNode`/`KGEdge`, their constraints/indexes, and necessary imports. Preserve existing models' behavior and schema. |
| `backend/tests/test_migrations.py` | Add KG upgrade/downgrade, constraints, metadata-parity, and PostgreSQL DDL checks; preserve existing migration assertions. |
| `docs/architecture.md` | Update sections 6b.1–6b.2 to match the settled TPC-35 contract and link this plan. Keep unrelated architecture sections and the personal writeup unchanged. |
| `docs/tests/test_migrations.md` | Document the new migration cases, setup, commands, and verification limits. |
| `docs/superpowers/plans/2026-11-03-tpc-35-temporal-store.md` | Maintain decisions, file inventory, test plan, and actual implementation/verification status. |

Do not edit any other existing file under this plan. If implementation reveals a necessary exception, identify the file and reason and obtain a scope expansion before editing it. A file already present when implementation begins counts as existing even if this plan originally expected to create it; inspect the worktree before writing.

### New files allowed

Armin permits creating new files as needed. The proposed structure below is a starting inventory, not an exhaustive allowlist. Additional KG modules, test helpers, tests, test documentation, and handoff notes may be created wherever the task needs them. Record each additional file and its purpose in this plan before final review. Creating a file must not overwrite or replace an existing file to bypass the edit boundary.

Only add a new Alembic revision; never rewrite an existing migration. Tests may create disposable files under their temporary directories. New-file permission does not authorize modifying the live application database, benchmark results, credentials, or unrelated work.

### Existing files to reuse without modifying

- `backend/app/db/base.py`, `backend/app/db/session.py`, `backend/alembic/env.py`: reuse UTC types, session conventions, and existing model discovery.
- `backend/tests/conftest.py`, `backend/tests/fakes.py`, `backend/pytest.ini`: use current infrastructure; put KG-specific fixtures/helpers in new files or the new test modules.
- Existing migrations, dependency manifests, Docker configuration, frontend files, agent code, privacy gateway, scheduler, and source-data projection code.
- All `eval/` files and existing evaluation tests, including active TPC-27 work; TPC-29 writing and unrelated working-tree changes.

Before review, compare the branch against its recorded base: every modified/deleted/renamed existing path must satisfy this allowlist (no deletions or renames are planned), and every new path must appear in the final file inventory. Review only changes attributable to TPC-35; do not stage or undo another task's changes.

## Proposed structure

```text
backend/app/db/models.py             # KGNode and KGEdge, registered with existing Base
backend/alembic/versions/<new>.py     # additive tables, indexes, checks
backend/app/kg/__init__.py
backend/app/kg/types.py              # node kinds, relations, sources, result/error types
backend/app/kg/store.py              # upsert_node, assert_edge, close_edge
backend/app/kg/query.py              # scoped temporal queries and triples
backend/tests/test_kg_store.py
backend/tests/test_kg_query.py
backend/tests/test_kg_isolation.py
backend/tests/test_kg_concurrency.py
backend/tests/test_kg_integration.py
backend/tests/kg_query_fixtures.py   # independent raw-row query/isolation fixtures
backend/tests/test_migrations.py     # extend existing migration coverage
docs/tests/test_kg_store.md
docs/tests/test_kg_query.md
docs/tests/test_kg_isolation.md
docs/tests/test_kg_concurrency.md
docs/tests/test_kg_integration.md
docs/tests/test_migrations.md        # extend existing documentation
docs/handoffs/TPC-35-temporal-store.md # contract for later graph producers
```

Keep type definitions independent of the models to avoid circular imports. Use the existing UTC mapping in `backend/app/db/base.py`. Query code must not import agent graphs or the scheduler to resolve a simulated date; read the existing `users.prefs_json["sim"]["date"]` convention through a small dependency-free helper.

## Schema and invariants

Use the fields already specified in architecture.md:

- Nodes: `id`, nullable `user_id`, `kind`, `key`, `label`, `props_json`.
- Edges: `id`, nullable `user_id`, `src_id`, `rel`, `dst_id`, `value_json`, `valid_from`, nullable `valid_to`, `recorded_at`, nullable `expired_at`, `source`, `evidence_json`.
- Foreign keys reference users and nodes. No cascading deletion of graph history through these relationships.
- Preserve `(user_id, kind, key)` uniqueness for owned nodes; add a partial unique index on `(kind, key)` for `user_id IS NULL`. A nullable column in a normal unique constraint does not by itself prevent duplicate shared nodes.
- Include the three ticket indexes: `(user_id, rel, src_id)`, `(user_id, dst_id)`, `(user_id, valid_from)`.
- Check `valid_to > valid_from` when bounded and `expired_at > recorded_at` when expired.
- Enforce one current-belief open interval per `(src_id, rel, dst_id)`, except `CATEGORIZED_AS`, which permits only one per `(src_id, rel)`. Use partial unique indexes, excluding expired rows and bounded intervals. Also prevent duplicate current interval starts; validate all remaining interval overlaps in the store under serialization.
- Interpret "current belief" as `expired_at IS NULL`, including beliefs about already-ended historical intervals. An ended valid interval is not automatically an expired belief.
- Owned edges may connect their user's nodes to shared guidance nodes. They may never connect to another user's nodes. Shared edges connect shared nodes only. Queries must enforce edge and endpoint visibility even when passed arbitrary IDs.
- Reuse the documented ontology. Merchant keys follow existing trimming and casefolding; other identity keys follow explicit kind-specific rules, not blanket lowercasing of external IDs.
- Node keys, kind, and ownership are immutable after creation. Mutable labels/props must not carry time-sensitive facts needed for replay; those belong on versioned edges.

## Store contract

All methods take `session` and explicit ownership context. They flush as needed but never commit the caller's transaction. Validation failures leave no partially applied change. Typed errors distinguish overlap, invalid interval, stale correction target, and inaccessible nodes.

### Idempotency

An exact retry of an existing current-belief assertion is a no-op, including a retry of a bounded historical interval. Identity includes the relationship, destination, valid interval, value, source, and evidence. Compare normalized JSON data rather than serialization key order.

An unchanged observation of an ongoing open fact at a later date does not create another interval. Explicit bounded observations are different: equal spending totals in January and February create two monthly facts. A change only to evidence/source requires an explicit correction rather than silent overwrite or an invented real-world change.

### Chronological change

For a changed value effective on a date strictly after the current open interval's start:

1. Lock/serialize the source before reading its current assertions.
2. Validate ownership, relation cardinality, and absence of conflicting intervals.
3. Capture one UTC transaction timestamp `T` for the entire operation.
4. Expire the original open assertion at `T`, retaining its original value and valid interval.
5. Insert its replacement with the same value and a valid end at the change date, recorded at `T`.
6. Insert the new value starting on the change date, also recorded at `T`.

The current-belief timeline has two adjacent intervals; the audit table contains three rows. An older `known_at` still sees the original open assertion. All steps succeed or roll back together.

Backfill creates bounded intervals oldest-first. Gaps are allowed. New non-idempotent assertions before the latest accepted interval, overlaps with bounded intervals, or multiple real changes at the same valid date raise an error. A mistaken existing fact is handled through explicit correction.

### Correction

Retain the ticket's `correct=True` concept, but require a `target_edge_id` to remove ambiguity. The target must be a current-belief assertion in the caller's scope; it may describe a past bounded interval.

- Preserve its valid start/end exactly.
- Expire it at `T` and insert the corrected value/evidence at `T`.
- A category correction may change the destination while preserving source, relation, ownership, and valid interval. Revalidate cardinality and ownership.
- Reject expired/stale targets and attempts to change the target's interval. Caller must reload before retrying a stale correction.
- Reject conflicting replacements; do not split neighboring intervals.
- A current target already containing the requested correction is a no-op.

### Ending a relationship

Provide `close_edge(target_edge_id, valid_to)` as a small supporting primitive. TPC-36 needs to end removed category rules and disconnected relationships. Apply the same expire-and-replace pattern, preserving historical beliefs. Require the target to be current and open and the end to be later than its start. A repeated close to the same end is a no-op; conflicting closes fail. No edge is physically deleted by store operations.

### Serialization and clocks

Serialize writes per source inside the same database transaction, including shared sources. Proposed mechanism: an update of the existing source-node row before reading assertions, which obtains the relevant database write/row lock. Do not rely only on Python locks or SQLite in-memory tests. Confirm behavior using separate connections to a temporary file-backed SQLite database; retain database uniqueness constraints as a second guard.

The caller owns rollback/retry of lock or uniqueness conflicts. No hidden partial retries inside a failed session. Transaction timestamps are actual UTC recording times, never simulated valid dates. Inject the clock for tests. Require a replacement timestamp strictly later than the versions it expires; report a clock-order conflict instead of storing an empty transaction interval.

## Query contract

All database queries take an explicit user scope, optional `known_at`, and a defined valid-date/window argument. User queries can read their own graph plus shared guidance; shared maintenance queries use an explicit shared scope.

For a snapshot at date `D` and knowledge time `K`, use:

```text
valid_from <= D and (valid_to is null or D < valid_to)
recorded_at <= K and (expired_at is null or K < expired_at)
```

Without `known_at`, select current-belief rows (`expired_at IS NULL`). Without an explicit valid date, use the user's simulated today if configured, otherwise the application's existing real-date convention. Future valid facts are not visible before their start.

| Function | Contract |
|---|---|
| `as_of` | Visible edges and endpoint nodes at the requested date and knowledge time |
| `history` | Time-ordered valid intervals for a node, optionally filtered by relation, at the requested knowledge time; expose expired versions only through an explicit audit option |
| `months_active` | Distinct calendar months with positive net `SPENDS_AT` spending in the specified window; corrected/expired duplicate versions do not inflate counts |
| `same_month_last_year` | Merchants and spending from that calendar month one year before the reference date, filtered using the category stored in the spending fact; do not apply today's merchant category retroactively |
| `changes` | Valid-time starts/ends in `[start, end)` using the requested knowledge view; corrections are visible in audit history, not mislabeled as new valid-time events |
| `neighborhood` | Bounded traversal of visible edges in both directions, visited-set cycle protection, stable ordering, maximum hops and total returned edge limit; apply ownership and temporal filters at every hop |
| `triples` | Deterministic text from already-scoped query results, including validity and provenance; integer cents formatted as money, no raw private IDs or arbitrary metadata dump |

Historical replay covers edge facts, not historical node display labels. Carry time-dependent explanatory content on edges. Return a clear truncation indicator for limited neighborhood results rather than implying the graph is complete.

## Verification plan

The cases below are planned tests, not results. Implement them with pytest; parameterize related inputs where that makes failures clearer. Test names may be refined, but preserve each stated behavioral assertion. Assert public query results and persisted rows independently so a bug shared by the store and query code cannot make the suite falsely pass.

### Fixtures and independent expected results

New `backend/tests/kg_query_fixtures.py` supplies independent raw-row query/isolation fixtures with two users, owned nodes, shared guidance, and literal recording timestamps. Store, concurrency, and integration modules use local temporary database fixtures and explicit clocks so the readers and writers do not share a fixture algorithm. KG-specific engines enable SQLite foreign keys on every connection. Use isolated in-memory databases for query tests and temporary file databases with separate connections for store, migration, durability, and contention tests. Do not rely on the existing shared `db` fixture to prove foreign-key behavior.

Use this fixed example as the main historical oracle, with all transaction times at 12:00 UTC:

1. On January 10 (`T1`), record gym cost **3,500 cents**, valid from January 1, 2026 onward.
2. On April 10 (`T2`), record a real change to **4,500 cents**, effective April 1.
3. On May 10 (`T3`), correct the April interval to **4,000 cents**, retaining its valid bounds.
4. On June 10 (`T4`), close that interval effective June 1.

Expected results are literal fixtures, not computed with production temporal helpers:

| Valid date | Knowledge time | Expected cost |
|---|---|---|
| January 5 | Before `T1` | No fact |
| March 31 | After `T4` | 3,500 cents |
| April 2 | Between `T1` and `T2` | 3,500 cents: that was the belief before learning of the increase |
| April 2 | Exactly `T2` | 4,500 cents |
| April 2 | Exactly `T3` | 4,000 cents |
| June 2 | Between `T3` and `T4` | 4,000 cents: the closure was not yet known |
| June 2 | Exactly `T4` | No fact |

Without `known_at`, April 2 returns 4,000 cents and June 2 returns no fact. After step 2, raw storage has three rows but two current-belief intervals. After all four steps it has five retained rows and two current-belief intervals. No historical value or valid bound is overwritten on a superseded row; only its transaction expiry is set.

### Store tests — `backend/tests/test_kg_store.py`

| Planned case | Setup and required outcome |
|---|---|
| `test_upsert_node_identity_and_normalization` | Reinsert the same node and variants of a merchant's whitespace/case; obtain one identity. The same key for another user remains a separate node. External IDs are not accidentally casefolded. |
| `test_shared_node_unique_with_null_owner` | Repeat a shared concept upsert, then attempt a duplicate direct insert; the upsert is idempotent and the database rejects the duplicate despite null ownership. |
| `test_node_identity_is_immutable` | Attempt to change kind, key, or owner on an existing identity; reject it. Updating permitted display metadata preserves its identity and relationships. |
| `test_assertion_retry_and_json_key_order` | Repeat a complete assertion with reordered JSON object keys; keep the same row and recording time. Parameterize open and bounded intervals. |
| `test_unchanged_open_observation_is_noop` | Observe the same ongoing fact at a later valid date; retain one interval. Evidence/source changes alone require an explicit correction. |
| `test_equal_values_in_distinct_months` | Insert equal January and February spending totals; keep two bounded facts. Retrying January after February exists is a no-op, not a rejected new backdated insert. |
| `test_change_preserves_original_belief` | Execute oracle steps 1–2; inspect all three stored rows, adjacent current intervals, shared transaction timestamp, and the unchanged original valid bounds. |
| `test_correction_preserves_valid_interval` | Correct open and bounded targets; expire exactly the targeted version and insert one replacement with identical valid bounds. Neighboring intervals remain unchanged. |
| `test_correction_rejects_missing_stale_or_altered_target` | Missing target ID, expired target, or altered valid bounds causes the documented typed error and no writes. Equal or backward transaction clocks are rejected too. |
| `test_correction_retry_current_target` | Repeat a correction against the current replacement with identical content; no new row. Retrying the expired original ID remains a stale-target error. |
| `test_category_replacement_cardinality` | A chronological merchant category change closes the old category before the new one starts; an explicit correction may change destination within the same interval. A conflicting simultaneous category is rejected. |
| `test_chronological_intervals_and_rejected_overlaps` | Accept ordered bounded intervals and gaps. Parameterize rejected out-of-order inserts, partial overlaps, enclosing overlaps, zero/negative intervals, and same-day real changes; compare stored state before/after each rejection. |
| `test_close_preserves_belief_and_is_idempotent` | Close an open assertion through versioning; its prior version stays intact. Retry against the current bounded replacement with the same end as a no-op; conflicting ends and stale IDs fail. |
| `test_invalid_ontology_and_endpoints` | Unknown kind/relation/source, nonexistent endpoints, and invalid endpoint combinations fail before a partial edge is stored. |
| `test_caller_controls_commit_and_rollback` | A successful store call followed by caller rollback leaves no rows. Inject failure after expiry and before replacement insertion; after rollback, a fresh session sees the intact original belief and no replacement. |
| `test_database_constraints_and_utc_round_trip` | Direct inserts prove FK, uniqueness, and interval checks independently of store validation. Round-trip non-UTC aware datetimes as UTC; reject naive datetimes. |

### Query tests — `backend/tests/test_kg_query.py`

| Planned case | Setup and required outcome |
|---|---|
| `test_as_of_historical_oracle` | Parameterize every row of the oracle above, plus the current-belief answers. Check returned endpoints as well as edge values. |
| `test_half_open_valid_and_transaction_boundaries` | Check exactly at each start/end, just before recording/expiry, and exactly at expiry. Only one version is visible at a transaction transition; a fact is absent at its exclusive valid end. |
| `test_empty_future_and_gapped_snapshots` | Empty graph, dates before a future assertion, and gaps between intervals return no invented facts. |
| `test_default_date_uses_simulation_and_explicit_override` | Set a simulated date different from the wall clock; omitted date uses simulation, explicit date overrides it. A real user uses the frozen application date. Recording time still uses UTC wall time. |
| `test_history_filters_order_and_audit` | Current history contains valid intervals, not duplicate expired beliefs; `known_at` reconstructs prior history. Audit mode includes retained versions in stable order and honors relation filters. |
| `test_months_active_distinct_positive_net_months` | Positive January spending, refund-only February, zero-net March, corrected April, and multiple facts in one month produce exactly the expected distinct positive months inside the requested window. |
| `test_same_month_last_year_uses_historical_category` | A merchant recategorized today remains in the category recorded on last year's spending fact. Parameterize January year rollover and a February 29 reference; select the prior calendar month correctly. |
| `test_changes_uses_valid_boundaries_not_recording_dates` | Return starts/ends inside `[start, end)` exactly once per event. A correction's recording date alone does not create a world-change event. Parameterize current and historical knowledge views. |
| `test_neighborhood_cycles_limits_and_order` | Traverse a cyclic graph in both directions; no repeated edges, no infinite expansion, exact hop/edge caps, deterministic order. Truncation is true only when eligible results are omitted; validate zero and invalid bounds. |
| `test_neighborhood_applies_time_at_every_hop` | A reachable first hop must not expose an expired, future, or not-yet-recorded second hop. |
| `test_triples_value_validity_and_provenance` | Assert expected readable lines for money (including zero/negative values), tax years, and evidence labels. Output is stable and omits internal IDs and arbitrary metadata fields containing sentinel private strings. |

### Isolation tests — `backend/tests/test_kg_isolation.py`

- `test_user_queries_cannot_read_other_user`: parameterize every public database query, including history, calendar helpers, and audit mode, using foreign node IDs. Results contain no foreign node, edge, label, value, or evidence; direct-ID operations follow the documented inaccessible-object error contract.
- `test_writes_require_consistent_ownership`: attempt foreign-source, foreign-destination, cross-user correction, cross-user close, and shared writes through user scope. Each fails without changing either user's rows. A valid owned-to-shared bridge succeeds.
- `test_shared_hub_cannot_bridge_users`: connect both users to the same shared concept, then traverse from user A. Shared guidance is visible; user B's branch is not, including when multiple hops and audit views are requested.
- `test_shared_scope_reads_and_writes_only_shared_graph`: shared maintenance can create/query guidance; user-owned endpoints and user-only data remain inaccessible through that scope.

### Concurrency tests — `backend/tests/test_kg_concurrency.py`

Use separate sessions/connections to one `tmp_path` SQLite file, explicit synchronization events/barriers, and bounded lock timeouts. Do not use a shared `StaticPool` connection or timing sleeps as proof of concurrency. Reopen a fresh session to inspect the committed result.

- `test_concurrent_identical_assertions`: force contention on the same source. Accept either two successful calls yielding one assertion or one successful call plus an explicit lock/uniqueness conflict; after rollback and retry, still exactly one assertion.
- `test_concurrent_corrections_of_same_target`: two writers correct the same target. Exactly one replacement wins; the other gets a retriable lock conflict or, after reloading, a stale-target error. Never accept two current replacements.
- `test_reader_never_observes_partial_version_transition`: pause a writer between expiry and replacement inside its transaction. A reader sees a complete committed view or a bounded database lock response, never a committed missing/half-replaced timeline. After commit the complete new view is visible; after rollback the old view is intact.
- Parameterize owned and shared sources where applicable. After each test, inspect row counts, active-version uniqueness, and absence of overlapping current intervals. Record that these prove SQLite behavior, not PostgreSQL runtime locking.

### Migration tests — additions to `backend/tests/test_migrations.py`

- `test_kg_upgrade_preserves_existing_data`: upgrade a disposable database to the actual parent revision, seed representative existing app rows, then apply the KG revision. Existing rows are unchanged; both new tables, FKs, checks, and expected indexes exist.
- `test_kg_migrated_constraints_match_models`: exercise duplicate shared nodes, duplicate open edges/category assignments, invalid temporal bounds, and missing endpoints on the migrated database. Compare KG model metadata and migrated schema with dialect normalization; do not treat table-name checks as schema parity.
- `test_kg_downgrade_and_reupgrade`: insert KG rows, downgrade to the parent, verify only KG schema/data was removed and app rows survived, then upgrade successfully again.
- `test_kg_postgres_ddl_compiles`: render upgrade and downgrade for PostgreSQL and assert partial-index predicates and dependency-safe drop order. Report this as SQL compilation only. Runtime PostgreSQL tests are conditional on a disposable service being available.

### End-to-end store test — `backend/tests/test_kg_integration.py`

`test_migrated_store_survives_reopen_and_replays_history` starts with an Alembic-migrated temporary SQLite file, creates nodes through the public API, executes all four oracle steps in separately committed sessions, disposes the engine, and reconnects. It verifies the literal historical/current query results, the five retained rows, two current-belief intervals, ownership, and human-readable triples. This proves that migrations, store operations, persistence, and queries work together without a running agent or LLM.

### Execution and acceptance gates

Run from `backend/` using the repository's existing interpreter (currently `../venv/bin/python`; verify its location in the implementation worktree):

```bash
../venv/bin/python -m pytest tests/test_kg_store.py tests/test_kg_query.py tests/test_kg_isolation.py tests/test_kg_concurrency.py tests/test_kg_integration.py tests/test_migrations.py -q
```

After focused checks pass, run the backend's offline regression suite:

```bash
../venv/bin/python -m pytest tests -k 'not live and not sandbox' -q
```

Before running that broader command, inspect the selected tests on the implementation base for external calls: name filtering is not a network sandbox, and existing collection-time readiness probes may contact Ollama. Explicitly exclude any newly identified external test and record the exact selection. Do not run live model benchmarks, real Plaid calls, or migrations against the developer database. Keep all KG cases offline and independent of credentials.

Acceptance requires all new KG cases and the migration cases to pass with no unexplained skips or xfails. Investigate any regression; record pre-existing failures against the base rather than weakening tests or changing excluded files. Document actual commands, pass/fail/skip counts, exclusions, and database coverage in the corresponding `docs/tests/` files. Check the file allowlist against the final diff. No tests are claimed to have run until implementation exists and these commands have actually completed.

## Implementation sequence

1. Create the isolated worktree; read its instructions and current migration head. Reconcile this contract with architecture.md sections 6b.1–6b.2 in the TPC-35 branch.
2. Add ontology/types, models, and migration with schema/ownership tests.
3. Implement node upsert, serialization, idempotent assertions, chronological versioning, explicit corrections, and close operations. Prove historical replay before adding higher-level queries.
4. Implement snapshots/history, then calendar helpers, changes, neighborhoods, and triples on shared scope/time predicates.
5. Run focused tests, migration checks, contention checks, and relevant backend regression tests; document what was actually verified.
6. Prepare a reviewable TPC-35 change with the interface contract for TPC-36. Graph population and agent wiring remain subsequent work.

## Defaults and remaining limits

- No UI, public REST API, new deployment service, LLM extraction, or source-data projection in this ticket.
- No arbitrary interval splitting, interval-boundary correction, or time-versioned node metadata. If a later producer requires these, stop at a typed error and design that extension deliberately.
- A migrated store may be empty until TPC-36/TPC-38 populate it; synthetic graph fixtures prove its behavior independently.
- Historical backfill records what the system learns at backfill time. It cannot reconstruct past recording times absent evidence. Simulation replay must not pretend otherwise.
- PostgreSQL runtime concurrency remains unverified unless a disposable PostgreSQL test service is actually available during implementation.
- “Never delete history” applies to application graph operations; a deliberate migration downgrade is destructive to the new graph tables and is tested only on disposable databases.

## Completion criteria

TPC-35 is ready when the schema, complete query surface, and explicit temporal mutation contract are implemented and verified; original ticket acceptance cases pass; temporal and ownership edge cases above pass; and test documentation plus the TPC-36 handoff are included. Planning alone does not mark the Jira ticket complete.

## Implementation record — 2026-11-03

Implemented in `/private/tmp/plaid-tpc35`, branch `codex/tpc-35-temporal-store`, based on committed revision `f452af7`. Three Sol subagents handled schema (medium reasoning), store/concurrency (high), and queries/isolation (high). The coordinating agent added the independent migrated/reopened-database integration case, reviewed integration, reconciled architecture docs, and ran combined verification. The schema agent also independently reviewed the store/query code.

The original TPC-27 checkout, its uncommitted changes, evaluation files/results, and services were not modified. No live database migration or model evaluation was run. Existing tests were executed from the isolated checkout with `OLLAMA_BASE_URL=http://127.0.0.1:1` and `ENABLE_SCHEDULER=0`, so collection-time model-readiness probes did not contact the TPC-27 Ollama service.

### Final implementation details

- Added revision `f6a2b8c4d901`, parent `d5f9a3b2c7e4`, with model/migration parity and partial indexes.
- Shared node kinds are strictly `concept` and `source`; other kinds are owned. An owned assertion must start at an owned node. This prevents invalid shared merchants from creating cross-user category-cardinality conflicts. Both upsert and endpoint validation enforce the rule.
- JSON assertion equality preserves types, including nested boolean/integer distinctions and integer/float representation, while ignoring object key order. Review identified and fixed Python equality's `True == 1` pitfall, with regression cases.
- Version transitions hold a source-row database write lock and use a nested transaction for expiry/replacement atomicity. Callers retain responsibility for committing and retrying database conflicts.
- The reader fixture helper is `kg_query_fixtures.py`; the originally proposed generic `kg_helpers.py` was unnecessary. Store and integration fixtures remain independent.
- The [TPC-36 handoff](../../handoffs/TPC-35-temporal-store.md) documents all APIs, producer obligations, and limits.

### File inventory and boundary audit

Existing modified files are exactly the implementation allowlist's four code/documentation paths, plus this approved plan (which was untracked at the recorded base):

- `backend/app/db/models.py`
- `backend/tests/test_migrations.py`
- `docs/architecture.md`
- `docs/tests/test_migrations.md`
- `docs/superpowers/plans/2026-11-03-tpc-35-temporal-store.md`

New implementation files:

- `backend/alembic/versions/f6a2b8c4d901_temporal_knowledge_graph.py`
- `backend/app/kg/__init__.py`
- `backend/app/kg/types.py`
- `backend/app/kg/store.py`
- `backend/app/kg/query.py`
- `backend/tests/kg_query_fixtures.py`
- `backend/tests/test_kg_store.py`
- `backend/tests/test_kg_query.py`
- `backend/tests/test_kg_isolation.py`
- `backend/tests/test_kg_concurrency.py`
- `backend/tests/test_kg_integration.py`
- `docs/tests/test_kg_store.md`
- `docs/tests/test_kg_query.md`
- `docs/tests/test_kg_isolation.md`
- `docs/tests/test_kg_concurrency.md`
- `docs/tests/test_kg_integration.md`
- `docs/handoffs/TPC-35-temporal-store.md`

No existing files were deleted or renamed. Existing migration history, test configuration, shared fixtures, dependencies, runtime configuration, and agent code were unchanged. `git diff --check` passed.

### Executed verification

Commands below ran from `/private/tmp/plaid-tpc35/backend` with `/Users/armindelmo/Documents/College/Fall26/COP6938/Plaid-Agent-CAP6938/venv/bin/python` as `python`. Both final combined runs used the environment overrides above.

| Command/coverage | Result |
|---|---|
| `python -m pytest tests/test_kg_store.py tests/test_kg_query.py tests/test_kg_isolation.py tests/test_kg_concurrency.py tests/test_kg_integration.py tests/test_migrations.py -q` | **180 passed**, 5.84 seconds; no skips/xfails |
| `python -m pytest tests -k 'not live and not sandbox' -q` | **1,647 passed, 22 deselected**, 43.82 seconds; no failures/skips/xfails |

The focused total comprises 51 store, 41 query, 43 isolation, 8 concurrency, 1 integrated persistence/replay, and 36 migration cases (including existing migration checks). The broader suite includes these; the totals are not additive. Live model and Plaid sandbox cases were deliberately deselected, not counted as passes.

SQLite behavior was exercised on disposable databases, including independent connections and populated migration downgrade/reupgrade. PostgreSQL table/index DDL was rendered and compared to model definitions; PostgreSQL runtime behavior remains unverified. Historical labels are current display metadata. Projection, extraction, and agent integration are intentionally later tickets. Jira status was not changed by this implementation.
