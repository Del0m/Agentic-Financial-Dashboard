"""Query tests seed raw rows independently of the mutation implementation."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import KGEdge, User
from app.kg import query
from app.kg.types import GraphSnapshot, InvalidAssertionError, InvalidIntervalError
from kg_query_fixtures import T1, T2, T3, T4, edge, node, oracle, qsession


@pytest.mark.parametrize("day,known,cents", [
    (date(2026, 1, 5), T1 - timedelta(microseconds=1), None),
    (date(2026, 3, 31), T4 + timedelta(days=1), 3500),
    (date(2026, 4, 2), T1 + timedelta(days=1), 3500),
    (date(2026, 4, 2), T2, 4500),
    (date(2026, 4, 2), T3, 4000),
    (date(2026, 6, 2), T3 + timedelta(days=1), 4000),
    (date(2026, 6, 2), T4, None),
    (date(2026, 4, 2), None, 4000),
    (date(2026, 6, 2), None, None),
])
def test_as_of_historical_oracle(qsession, oracle, day, known, cents):
    user, stream, rows = oracle
    snapshot = query.as_of(qsession, user_id="alice", on_date=day, known_at=known)
    assert [e.value_json["typical_cents"] for e in snapshot.edges] == ([] if cents is None else [cents])
    assert [n.id for n in snapshot.nodes] == ([] if cents is None else [user.id, stream.id])
    # Independent persisted-row oracle, including bounds preserved on old rows.
    raw = list(qsession.scalars(select(KGEdge).order_by(KGEdge.id)))
    assert [(e.value_json["typical_cents"], e.valid_from, e.valid_to, e.recorded_at, e.expired_at) for e in raw] == [
        (3500, date(2026, 1, 1), None, T1, T2),
        (3500, date(2026, 1, 1), date(2026, 4, 1), T2, None),
        (4500, date(2026, 4, 1), None, T2, T3),
        (4000, date(2026, 4, 1), None, T3, T4),
        (4000, date(2026, 4, 1), date(2026, 6, 1), T4, None),
    ]


@pytest.mark.parametrize("day,known,expected_id", [
    (date(2025, 12, 31), T1, None),
    (date(2026, 1, 1), T1, 0),
    (date(2026, 4, 1), T2 - timedelta(microseconds=1), 0),
    (date(2026, 4, 1), T2, 2),
    (date(2026, 4, 1), T3 - timedelta(microseconds=1), 2),
    (date(2026, 4, 1), T3, 3),
    (date(2026, 6, 1), T4 - timedelta(microseconds=1), 3),
    (date(2026, 6, 1), T4, None),
])
def test_half_open_valid_and_transaction_boundaries(qsession, oracle, day, known, expected_id):
    result = query.as_of(qsession, user_id="alice", on_date=day, known_at=known)
    assert [e.id for e in result.edges] == ([] if expected_id is None else [oracle[2][expected_id].id])


def test_empty_future_and_gapped_snapshots(qsession):
    assert query.as_of(qsession, user_id="alice", on_date=date(2026, 1, 1)) == GraphSnapshot([], [])
    a, b = node(qsession), node(qsession)
    edge(qsession, a, b, start=date(2026, 2, 1), end=date(2026, 3, 1))
    edge(qsession, a, b, start=date(2026, 4, 1), end=date(2026, 5, 1))
    for day in (date(2026, 1, 31), date(2026, 3, 1), date(2026, 3, 20), date(2026, 5, 1)):
        assert query.as_of(qsession, user_id="alice", on_date=day).edges == []


def test_default_date_uses_simulation_and_explicit_override(qsession, oracle, monkeypatch):
    qsession.get(User, "alice").prefs_json = {"sim": {"date": "2026-04-02"}}
    monkeypatch.setattr(query, "_today", lambda: date(2026, 6, 2))
    assert query.as_of(qsession, user_id="alice").edges[0].value_json == {"typical_cents": 4000}
    assert query.as_of(qsession, user_id="alice", on_date=date(2026, 6, 2)).edges == []
    qsession.get(User, "alice").prefs_json = {}
    assert query.as_of(qsession, user_id="alice").edges == []
    # A simulated valid date never changes the transaction recording cutoff.
    assert query.as_of(qsession, user_id="alice", on_date=date(2026, 4, 2), known_at=T1).edges[0].recorded_at == T1


def test_history_filters_order_and_audit(qsession, oracle):
    user, stream, rows = oracle
    other = node(qsession, kind="account")
    extra = edge(qsession, user, other, rel="HAS_ACCOUNT", start=date(2026, 2, 1))
    ids = lambda **kw: [e.id for e in query.history(qsession, user_id="alice", node_id=stream.id, **kw)]
    assert ids() == [rows[1].id, rows[4].id]
    assert ids(known_at=T1) == [rows[0].id]
    assert ids(known_at=T2) == [rows[1].id, rows[2].id]
    assert ids(audit=True) == [r.id for r in rows]
    assert ids(audit=True, known_at=T2) == [r.id for r in rows[:3]]
    assert ids(rel="HAS_ACCOUNT", audit=True) == []
    assert [e.id for e in query.history(qsession, user_id="alice", node_id=user.id, rel="HAS_ACCOUNT")] == [extra.id]
    assert ids(known_at=T2.astimezone(timezone(timedelta(hours=-5)))) == [rows[1].id, rows[2].id]


def test_months_active_distinct_positive_net_months(qsession):
    u = node(qsession, kind="user")
    m = node(qsession, kind="merchant")
    for start, end, cents in [
        (date(2026, 1, 1), date(2026, 1, 15), 100),
        (date(2026, 1, 15), date(2026, 2, 1), -20),
        (date(2026, 2, 1), date(2026, 3, 1), -100),
        (date(2026, 3, 1), date(2026, 3, 15), 100),
        (date(2026, 3, 15), date(2026, 4, 1), -100),
    ]:
        edge(qsession, u, m, rel="SPENDS_AT", start=start, end=end, value={"cents": cents, "count": 1, "category": "fitness"})
    edge(qsession, u, m, rel="SPENDS_AT", start=date(2026, 4, 1), end=date(2026, 5, 1),
         value={"cents": 700}, expired=T2)
    edge(qsession, u, m, rel="SPENDS_AT", start=date(2026, 4, 1), end=date(2026, 5, 1),
         value={"cents": -50}, recorded=T2)
    assert query.months_active(qsession, user_id="alice", merchant_id=m.id, start=date(2026, 1, 1), end=date(2026, 5, 1)) == [date(2026, 1, 1)]
    assert query.months_active(qsession, user_id="alice", merchant_id=m.id, start=date(2026, 1, 1), end=date(2026, 5, 1), known_at=T1) == [date(2026, 1, 1), date(2026, 4, 1)]
    assert query.months_active(qsession, user_id="alice", merchant_id=m.id, start=date(2026, 1, 2), end=date(2026, 4, 1), known_at=T1) == []


@pytest.mark.parametrize("reference,prior_start,prior_end", [
    (date(2027, 1, 8), date(2026, 1, 1), date(2026, 2, 1)),
    (date(2028, 2, 29), date(2027, 2, 1), date(2027, 3, 1)),
    (date(2027, 12, 31), date(2026, 12, 1), date(2027, 1, 1)),
])
def test_same_month_last_year_uses_historical_category(qsession, reference, prior_start, prior_end):
    u, m = node(qsession, kind="user"), node(qsession, kind="merchant")
    category = node(qsession, kind="category", label="Today's category")
    edge(qsession, m, category, rel="CATEGORIZED_AS", start=prior_end)
    old = edge(qsession, u, m, rel="SPENDS_AT", start=prior_start, end=prior_end,
               value={"cents": 900, "count": 1, "category": "historical"}, expired=T2)
    corrected = edge(qsession, u, m, rel="SPENDS_AT", start=prior_start, end=prior_end,
                     value={"cents": 700, "count": 1, "category": "historical"}, recorded=T2)
    edge(qsession, u, m, rel="SPENDS_AT", start=prior_end,
         value={"cents": 500, "count": 1, "category": "historical"})
    assert query.same_month_last_year(qsession, user_id="alice", category="historical", on_date=reference) == [corrected]
    assert query.same_month_last_year(qsession, user_id="alice", category="historical", on_date=reference, known_at=T1) == [old]
    assert query.same_month_last_year(qsession, user_id="alice", category="Today's category", on_date=reference) == []


@pytest.mark.parametrize("known,indexes", [(None, [1, 4]), (T1, []), (T2, [1, 2]), (T3, [1, 3])])
def test_changes_uses_valid_boundaries_not_recording_dates(qsession, oracle, known, indexes):
    rows = oracle[2]
    result = query.changes(qsession, user_id="alice", start=date(2026, 4, 1), end=date(2026, 4, 2), known_at=known)
    assert [e.id for e in result] == [rows[i].id for i in indexes]
    assert query.changes(qsession, user_id="alice", start=date(2026, 5, 10), end=date(2026, 5, 11), known_at=known) == []
    # Both boundaries in one window still returns a single row per edge.
    june = query.changes(qsession, user_id="alice", start=date(2026, 4, 1), end=date(2026, 6, 2))
    assert [e.id for e in june] == [rows[1].id, rows[4].id]
    assert query.changes(qsession, user_id="alice", start=date(2026, 5, 1), end=date(2026, 6, 1)) == []


def test_neighborhood_cycles_limits_and_order(qsession):
    a, b, c, d = [node(qsession) for _ in range(4)]
    ab, bc, ca, cd = [edge(qsession, x, y) for x, y in ((a, b), (b, c), (c, a), (c, d))]
    def neighborhood(**kw):
        return query.neighborhood(qsession, user_id="alice", node_ids=[a.id, a.id], on_date=date(2026, 1, 1), **kw)
    result = neighborhood(hops=9, limit=4)
    assert [e.id for e in result.edges] == [ab.id, ca.id, bc.id, cd.id]
    assert [n.id for n in result.nodes] == [a.id, b.id, c.id, d.id]
    assert not result.truncated
    assert neighborhood(hops=2, limit=4) == result
    assert [e.id for e in neighborhood(hops=1, limit=10).edges] == [ab.id, ca.id]
    assert not neighborhood(hops=1, limit=2).truncated
    assert neighborhood(hops=2, limit=2).truncated
    assert neighborhood(hops=9, limit=3).truncated
    assert neighborhood(hops=9, limit=0).truncated
    assert neighborhood(hops=0, limit=0).edges == []
    assert not neighborhood(hops=0, limit=0).truncated
    isolated = node(qsession)
    assert not query.neighborhood(qsession, user_id="alice", node_ids=[isolated.id], hops=3, limit=0).truncated
    assert query.neighborhood(qsession, user_id="alice", node_ids=[], hops=3).nodes == []


@pytest.mark.parametrize("kwargs", [{"hops": -1}, {"limit": -1}, {"hops": 1.1}, {"limit": True}])
def test_neighborhood_invalid_bounds(qsession, kwargs):
    with pytest.raises(InvalidAssertionError):
        query.neighborhood(qsession, user_id="alice", node_ids=[], **kwargs)


@pytest.mark.parametrize("temporal", ["expired", "future", "unrecorded", "ended"])
def test_neighborhood_applies_time_at_every_hop(qsession, temporal):
    a, b, c = [node(qsession) for _ in range(3)]
    visible = edge(qsession, a, b)
    kwargs = {"expired": {"expired": T2}, "future": {"start": date(2026, 5, 1)},
              "unrecorded": {"recorded": T3}, "ended": {"end": date(2026, 4, 1)}}[temporal]
    edge(qsession, b, c, **kwargs)
    result = query.neighborhood(qsession, user_id="alice", node_ids=[a.id], on_date=date(2026, 4, 1), known_at=T2, hops=5)
    assert result.edges == [visible]
    assert [n.id for n in result.nodes] == [a.id, b.id]
    assert not result.truncated


@pytest.mark.parametrize("cents,money", [(700000, "$7,000.00"), (0, "$0.00"), (-123, "-$1.23")])
def test_triples_value_validity_and_provenance(qsession, cents, money):
    private = "PRIVATE_SENTINEL_8392"
    a = node(qsession, owner=None, label="Roth IRA", key=private)
    b = node(qsession, owner=None, label="annual contribution", key=private + "dst")
    a.props_json = {"secret": private}
    fact = edge(qsession, a, b, owner=None, rel="HAS_LIMIT", source="kb_extract",
                value={"cents": cents, "condition": "under 50", "tax_year": 2025, "secret": private, "user_id": private},
                start=date(2025, 1, 1), end=date(2026, 1, 1),
                evidence={"source_label": "irs-ira-contribution-limits", "private": private, "account_id": private})
    snapshot = GraphSnapshot([b, a], [fact])
    assert query.triples(snapshot) == [
        f"Roth IRA —HAS_LIMIT→ annual contribution: {money}; condition: under 50; tax year 2025; valid 2025-01-01 to 2026-01-01 (end exclusive) [kb_extract; irs-ira-contribution-limits]"
    ]
    assert private not in "\n".join(query.triples(snapshot))
    assert query.triples(GraphSnapshot([a], [fact])) == []
    a.label = None
    assert query.triples(snapshot)[0].startswith("concept —HAS_LIMIT→")


def test_query_validation(qsession):
    with pytest.raises(InvalidAssertionError):
        query.as_of(qsession, user_id="alice", known_at=datetime(2026, 1, 1))
    for start, end in ((date(2026, 2, 1), date(2026, 1, 1)), (date(2026, 1, 1), date(2026, 1, 1))):
        with pytest.raises(InvalidIntervalError):
            query.changes(qsession, user_id="alice", start=start, end=end)
    with pytest.raises(InvalidIntervalError):
        query.as_of(qsession, user_id="alice", on_date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(InvalidIntervalError):
        query.same_month_last_year(qsession, user_id="alice", category="fitness", on_date=date(1, 1, 1))
    account = node(qsession, kind="account")
    with pytest.raises(InvalidAssertionError):
        query.months_active(qsession, user_id="alice", merchant_id=account.id, start=date(2026, 1, 1), end=date(2027, 1, 1))
    with pytest.raises(InvalidAssertionError):
        query.history(qsession, user_id="alice", node_id=account.id, rel="UNKNOWN")
