# Temporal graph ownership tests

`backend/tests/test_kg_isolation.py` uses its own fresh SQLite database through
`backend/tests/kg_query_fixtures.py`; foreign keys are enabled per connection.
Raw fixtures contain two users, private account/user/merchant nodes, a shared
concept hub, and shared guidance. Both users reach the same hub. Deliberately
malformed rows independently challenge every read guard: an owned edge with a
foreign destination, an owned edge with a foreign source, a shared edge with a
foreign endpoint, and a foreign edge with otherwise visible endpoints. These
fixtures bypass store validation so a shared bug in reads and writes cannot
make isolation appear correct.

The suite parameterizes every database query at current belief and two explicit
knowledge times. Snapshots, histories, audit histories, spending-month helpers,
prior-year category spending, changes, and multi-hop neighborhoods exclude the
other user's edges, nodes, values, labels, and evidence. Direct-ID functions
reject foreign and nonexistent IDs with the same `ScopeError`, including mixed
neighborhood roots and zero-hop/zero-limit traversal. A shared hub exposes
shared guidance without exposing the other user's branch. Triples from that
scoped traversal contain no foreign sentinel strings.

Mutation cases use the public store API and compare a copied persisted state
before and after each rejected write. They cover foreign source/destination,
foreign correction/close targets, attempts to write shared relationships in
user scope, and attempts to correct or close shared guidance in user scope.
A valid owned account-to-shared-concept bridge succeeds. Explicit shared
maintenance can create and read guidance but cannot read owned IDs or mutate
relationships whose endpoints are owned by either user.

Verified on 2026-11-03 from the isolated worktree's `backend/`:

```bash
/Users/armindelmo/Documents/College/Fall26/COP6938/Plaid-Agent-CAP6938/venv/bin/python -m pytest tests/test_kg_query.py tests/test_kg_isolation.py -q
```

Result: **84 passed**, including 43 isolation cases; no skips or xfails. All
databases are disposable and no services are started. These tests verify the
internal API's explicit scope argument; authenticating that argument remains
the caller's responsibility. PostgreSQL runtime isolation is not covered.
