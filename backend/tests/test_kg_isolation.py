"""Read and mutation scope tests, including deliberately malformed raw edges."""
from datetime import date

import pytest
from sqlalchemy import select

from app.db.models import KGEdge, KGNode
from app.kg import query, store
from app.kg.types import ScopeError
from kg_query_fixtures import T1, T2, edge, node, qsession

PRIVATE = "BOB_PRIVATE_SENTINEL_934"
DAY = date(2026, 1, 1)


@pytest.fixture
def scoped_graph(qsession):
    a = node(qsession, kind="account", label="Alice account")
    b = node(qsession, owner="bob", kind="account", label=PRIVATE)
    au = node(qsession, kind="user", label="Alice")
    bu = node(qsession, owner="bob", kind="user", label=PRIVATE)
    am = node(qsession, kind="merchant", label="Alice merchant")
    bm = node(qsession, owner="bob", kind="merchant", label=PRIVATE)
    hub = node(qsession, owner=None, kind="concept", label="Shared hub")
    rule = node(qsession, owner=None, kind="concept", label="Shared rule")
    a_bridge = edge(qsession, a, hub, rel="IS_A")
    b_bridge = edge(qsession, b, hub, owner="bob", rel="IS_A", value={"text": PRIVATE}, evidence={"label": PRIVATE})
    guidance = edge(qsession, hub, rule, owner=None, rel="HAS_RULE", source="kb_extract", value={"text": "Visible guidance"})
    own_spend = edge(qsession, au, am, rel="SPENDS_AT", start=date(2025, 1, 1), end=date(2025, 2, 1),
                     value={"cents": 100, "count": 1, "category": "same"})
    foreign_spend = edge(qsession, bu, bm, owner="bob", rel="SPENDS_AT", start=date(2025, 1, 1), end=date(2025, 2, 1),
                         value={"cents": 938392, "count": 1, "category": "same"}, evidence={"label": PRIVATE})
    # Rows injected without the store expose missed endpoint/edge-owner guards.
    injected = [
        edge(qsession, a, b, rel="ALTERNATIVE_TO", value={"text": PRIVATE}),
        edge(qsession, b, rule, rel="ALTERNATIVE_TO", value={"text": PRIVATE}),
        edge(qsession, hub, b, owner=None, rel="ALTERNATIVE_TO", value={"text": PRIVATE}),
        edge(qsession, a, rule, owner="bob", rel="IS_A", value={"text": PRIVATE}),
    ]
    return {"a": a, "b": b, "au": au, "bu": bu, "am": am, "bm": bm,
            "hub": hub, "rule": rule, "a_bridge": a_bridge, "b_bridge": b_bridge,
            "guidance": guidance, "own_spend": own_spend, "foreign_spend": foreign_spend, "injected": injected}


@pytest.mark.parametrize("operation", ["snapshot", "history", "audit", "months", "last_year", "changes", "neighborhood"])
@pytest.mark.parametrize("known", [None, T1, T2])
def test_user_queries_cannot_read_other_user(qsession, scoped_graph, operation, known):
    g = scoped_graph
    if operation == "snapshot":
        result = query.as_of(qsession, user_id="alice", on_date=DAY, known_at=known)
        edges, nodes = result.edges, result.nodes
        assert [e.id for e in edges] == [g["a_bridge"].id, g["guidance"].id]
    elif operation in ("history", "audit"):
        edges = query.history(qsession, user_id="alice", node_id=g["hub"].id, audit=operation == "audit", known_at=known)
        nodes = []
        assert [e.id for e in edges] == [g["a_bridge"].id, g["guidance"].id]
    elif operation == "months":
        assert query.months_active(qsession, user_id="alice", merchant_id=g["am"].id,
                                   start=date(2025, 1, 1), end=date(2026, 1, 1), known_at=known) == [date(2025, 1, 1)]
        return
    elif operation == "last_year":
        edges = query.same_month_last_year(qsession, user_id="alice", category="same", on_date=DAY, known_at=known)
        nodes = []
        assert [e.id for e in edges] == [g["own_spend"].id]
    elif operation == "changes":
        edges = query.changes(qsession, user_id="alice", start=date(2025, 1, 1), end=date(2027, 1, 1), known_at=known)
        nodes = []
        assert {e.id for e in edges} == {g["a_bridge"].id, g["guidance"].id, g["own_spend"].id}
    else:
        result = query.neighborhood(qsession, user_id="alice", node_ids=[g["a"].id], on_date=DAY, known_at=known, hops=9)
        edges, nodes = result.edges, result.nodes
        assert [e.id for e in edges] == [g["a_bridge"].id, g["guidance"].id]
    assert all(e.user_id in (None, "alice") for e in edges)
    assert all(n.user_id in (None, "alice") for n in nodes)
    assert PRIVATE not in repr([(e.value_json, e.evidence_json) for e in edges])
    assert PRIVATE not in repr([n.label for n in nodes])


@pytest.mark.parametrize("operation", ["history", "audit", "months", "neighborhood", "mixed_roots", "zero_hops"])
@pytest.mark.parametrize("missing", [False, True])
def test_direct_ids_reject_foreign_and_nonexistent(qsession, scoped_graph, operation, missing):
    g = scoped_graph
    inaccessible = 999999 if missing else g["bm"].id
    with pytest.raises(ScopeError, match="unavailable in this scope"):
        if operation in ("history", "audit"):
            query.history(qsession, user_id="alice", node_id=inaccessible, audit=operation == "audit")
        elif operation == "months":
            query.months_active(qsession, user_id="alice", merchant_id=inaccessible, start=DAY, end=date(2027, 1, 1))
        else:
            ids = [g["a"].id, inaccessible] if operation == "mixed_roots" else [inaccessible]
            query.neighborhood(qsession, user_id="alice", node_ids=ids,
                               hops=0 if operation == "zero_hops" else 3, limit=0)


def _persisted_state(session):
    # Copy values so changes to ORM identity-map objects cannot hide mutations.
    return (
        [(n.id, n.user_id, n.kind, n.key, n.label, dict(n.props_json)) for n in session.scalars(select(KGNode).order_by(KGNode.id))],
        [(e.id, e.user_id, e.src_id, e.rel, e.dst_id, e.value_json, e.valid_from, e.valid_to,
          e.recorded_at, e.expired_at, e.source, dict(e.evidence_json)) for e in session.scalars(select(KGEdge).order_by(KGEdge.id))],
    )


@pytest.mark.parametrize("operation", ["foreign_source", "foreign_destination", "foreign_correction", "foreign_close", "shared_as_user", "shared_correction_as_user", "shared_close_as_user"])
def test_writes_require_consistent_ownership(qsession, scoped_graph, operation):
    g = scoped_graph
    before = _persisted_state(qsession)
    with pytest.raises(ScopeError):
        if operation == "foreign_close":
            store.close_edge(qsession, user_id="alice", target_edge_id=g["b_bridge"].id, valid_to=date(2026, 2, 1), now=T2)
        elif operation == "shared_close_as_user":
            store.close_edge(qsession, user_id="alice", target_edge_id=g["guidance"].id, valid_to=date(2026, 2, 1), now=T2)
        elif operation == "foreign_correction":
            store.assert_edge(qsession, user_id="alice", src_id=g["a"].id, rel="IS_A", dst_id=g["hub"].id,
                              value={"text": "replacement"}, valid_from=DAY, correct=True, target_edge_id=g["b_bridge"].id, now=T2)
        elif operation == "shared_correction_as_user":
            store.assert_edge(qsession, user_id="alice", src_id=g["hub"].id, rel="HAS_RULE", dst_id=g["rule"].id,
                              value={"text": "replacement"}, valid_from=DAY, correct=True, target_edge_id=g["guidance"].id, now=T2)
        else:
            src, dst = {"foreign_source": (g["b"], g["hub"]),
                        "foreign_destination": (g["a"], g["b"]),
                        "shared_as_user": (g["hub"], g["rule"])}[operation]
            store.assert_edge(qsession, user_id="alice", src_id=src.id, rel="IS_A", dst_id=dst.id,
                              value=None, valid_from=DAY, now=T2)
    assert _persisted_state(qsession) == before


def test_valid_owned_to_shared_bridge_succeeds(qsession):
    a = store.upsert_node(qsession, user_id="alice", kind="account", key="owned-account", label="Account")
    shared = store.upsert_node(qsession, user_id=None, kind="concept", key="ira", label="IRA")
    result = store.assert_edge(qsession, user_id="alice", src_id=a.id, rel="IS_A", dst_id=shared.id,
                               value=None, valid_from=DAY, now=T1)
    assert query.as_of(qsession, user_id="alice", on_date=DAY).edges == [result]
    assert query.as_of(qsession, user_id="bob", on_date=DAY).edges == []
    assert query.as_of(qsession, user_id=None, on_date=DAY).edges == []


def test_shared_hub_cannot_bridge_users(qsession, scoped_graph):
    g = scoped_graph
    result = query.neighborhood(qsession, user_id="alice", node_ids=[g["a"].id], on_date=DAY, hops=20)
    assert [n.id for n in result.nodes] == [g["a"].id, g["hub"].id, g["rule"].id]
    assert set(e.id for e in result.edges) == {g["a_bridge"].id, g["guidance"].id}
    assert not result.truncated
    assert PRIVATE not in "\n".join(query.triples(result))
    for audit in (False, True):
        assert [e.id for e in query.history(qsession, user_id="alice", node_id=g["hub"].id, audit=audit)] == [g["a_bridge"].id, g["guidance"].id]


def test_shared_scope_reads_and_writes_only_shared_graph(qsession, scoped_graph):
    g = scoped_graph
    guide = store.upsert_node(qsession, user_id=None, kind="concept", key="shared-extra", label="Extra")
    written = store.assert_edge(qsession, user_id=None, src_id=g["hub"].id, rel="IS_A", dst_id=guide.id,
                                value=None, valid_from=DAY, source="kb_extract", now=T2)
    expected = {g["guidance"].id, written.id}
    assert {e.id for e in query.as_of(qsession, user_id=None, on_date=DAY).edges} == expected
    assert {e.id for e in query.history(qsession, user_id=None, node_id=g["hub"].id, audit=True)} == expected
    assert {e.id for e in query.neighborhood(qsession, user_id=None, node_ids=[g["hub"].id], on_date=DAY, hops=9).edges} == expected
    assert {e.id for e in query.changes(qsession, user_id=None, start=DAY, end=date(2027, 1, 1))} == expected
    assert query.same_month_last_year(qsession, user_id=None, category="same", on_date=DAY) == []
    for target in (g["a"], g["b"]):
        with pytest.raises(ScopeError):
            query.history(qsession, user_id=None, node_id=target.id)
        with pytest.raises(ScopeError):
            query.neighborhood(qsession, user_id=None, node_ids=[target.id])
        with pytest.raises(ScopeError):
            store.assert_edge(qsession, user_id=None, src_id=target.id, rel="IS_A", dst_id=guide.id,
                              value=None, valid_from=DAY, now=T2)
    with pytest.raises(ScopeError):
        query.months_active(qsession, user_id=None, merchant_id=g["am"].id, start=DAY, end=date(2027, 1, 1))
    with pytest.raises(ScopeError):
        store.close_edge(qsession, user_id=None, target_edge_id=g["a_bridge"].id, valid_to=date(2026, 2, 1), now=T2)
