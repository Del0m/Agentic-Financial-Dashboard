"""Scoped, bitemporal graph reads; no agent or projection dependencies.

Node labels are current display metadata. Historical replay applies to edge
facts. All database reads check both the edge owner and *both* endpoint owners.
"""
from datetime import date, datetime, timezone
from typing import get_args

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from app.db.models import KGEdge, KGNode, User
from .types import (
    GraphSnapshot, InvalidAssertionError, InvalidIntervalError,
    NeighborhoodResult, Relation, ScopeError,
)


def _today() -> date:
    # Match the API's established local-calendar date convention.
    return date.today()


def _date(value: date) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise InvalidIntervalError("valid time must be a calendar date")
    return value


def _known(known_at: datetime | None) -> datetime | None:
    if known_at is None:
        return None
    if not isinstance(known_at, datetime) or known_at.utcoffset() is None:
        raise InvalidAssertionError("known_at must be a timezone-aware datetime")
    return known_at.astimezone(timezone.utc)


def _on_date(session: Session, user_id: str | None, on_date: date | None) -> date:
    if on_date is not None:
        return _date(on_date)
    user = session.get(User, user_id) if user_id is not None else None
    sim = (user.prefs_json or {}).get("sim") if user else None
    if isinstance(sim, dict) and sim.get("date"):
        return date.fromisoformat(sim["date"])
    return _today()


def _owner(column, user_id):
    return column.is_(None) if user_id is None else or_(column == user_id, column.is_(None))


def _edges(user_id, known_at=None, *, audit=False):
    src, dst = aliased(KGNode), aliased(KGNode)
    stmt = select(KGEdge).join(src, KGEdge.src_id == src.id).join(dst, KGEdge.dst_id == dst.id).where(
        _owner(KGEdge.user_id, user_id), _owner(src.user_id, user_id), _owner(dst.user_id, user_id),
    )
    known_at = _known(known_at)
    if known_at is not None:
        stmt = stmt.where(KGEdge.recorded_at <= known_at)
        if not audit:
            stmt = stmt.where(or_(KGEdge.expired_at.is_(None), known_at < KGEdge.expired_at))
    elif not audit:
        stmt = stmt.where(KGEdge.expired_at.is_(None))
    return stmt


def _valid(stmt, on_date):
    return stmt.where(KGEdge.valid_from <= on_date, or_(KGEdge.valid_to.is_(None), on_date < KGEdge.valid_to))


def _node(session, user_id, node_id):
    node = session.scalar(select(KGNode).where(KGNode.id == node_id, _owner(KGNode.user_id, user_id)))
    if node is None:
        # Deliberately identical for nonexistent and foreign IDs.
        raise ScopeError("node is unavailable in this scope")
    return node


def _snapshot(session, edges, *, roots=()):
    ids = {node.id for node in roots}
    ids.update(endpoint for edge in edges for endpoint in (edge.src_id, edge.dst_id))
    nodes = list(session.scalars(select(KGNode).where(KGNode.id.in_(ids)).order_by(KGNode.id))) if ids else []
    return GraphSnapshot(nodes=nodes, edges=edges)


def _window(start, end):
    _date(start)
    _date(end)
    if end <= start:
        raise InvalidIntervalError("window end must follow its start")


def as_of(session: Session, *, user_id: str | None, on_date: date | None = None,
          known_at: datetime | None = None) -> GraphSnapshot:
    """Visible assertions and their endpoints at valid date and knowledge time."""
    on_date = _on_date(session, user_id, on_date)
    edges = list(session.scalars(_valid(_edges(user_id, known_at), on_date).order_by(KGEdge.id)))
    return _snapshot(session, edges)


def history(session: Session, *, user_id: str | None, node_id: int, rel: str | None = None,
            known_at: datetime | None = None, audit: bool = False) -> list[KGEdge]:
    """Incident valid intervals; audit explicitly includes superseded beliefs.

    In audit mode known_at is a recording cutoff, not an active-version filter.
    """
    _node(session, user_id, node_id)
    stmt = _edges(user_id, known_at, audit=audit).where(or_(KGEdge.src_id == node_id, KGEdge.dst_id == node_id))
    if rel is not None:
        if rel not in get_args(Relation):
            raise InvalidAssertionError("unknown relation")
        stmt = stmt.where(KGEdge.rel == rel)
    return list(session.scalars(stmt.order_by(KGEdge.valid_from, KGEdge.recorded_at, KGEdge.id)))


def months_active(session: Session, *, user_id: str | None, merchant_id: int, start: date,
                  end: date, known_at: datetime | None = None) -> list[date]:
    """Month starts in [start, end) whose monthly SPENDS_AT total is positive.

    Spending facts are monthly aggregates, assigned by their valid_from month.
    Net all visible facts in that month before deciding whether it is active.
    """
    merchant = _node(session, user_id, merchant_id)
    if merchant.kind != "merchant":
        raise InvalidAssertionError("months_active requires a merchant")
    _window(start, end)
    first_month = start.replace(day=1)
    stmt = _edges(user_id, known_at).where(
        KGEdge.rel == "SPENDS_AT", KGEdge.dst_id == merchant_id,
        KGEdge.valid_from >= first_month, KGEdge.valid_from < end,
    )
    totals = {}
    for edge in session.scalars(stmt):
        month = edge.valid_from.replace(day=1)
        value = edge.value_json
        cents = value.get("cents") if isinstance(value, dict) else None
        if isinstance(cents, int) and not isinstance(cents, bool):
            totals[month] = totals.get(month, 0) + cents
    return sorted(month for month, total in totals.items() if start <= month < end and total > 0)


def same_month_last_year(session: Session, *, user_id: str | None, category: str,
                         on_date: date | None = None, known_at: datetime | None = None) -> list[KGEdge]:
    """Same calendar month in the prior year, using each fact's category."""
    on_date = _on_date(session, user_id, on_date)
    if on_date.year == 1:
        raise InvalidIntervalError("reference date has no representable prior year")
    start = date(on_date.year - 1, on_date.month, 1)
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    stmt = _edges(user_id, known_at).where(KGEdge.rel == "SPENDS_AT", KGEdge.valid_from >= start, KGEdge.valid_from < end)
    return [edge for edge in session.scalars(stmt.order_by(KGEdge.valid_from, KGEdge.id))
            if isinstance(edge.value_json, dict) and edge.value_json.get("category") == category]


def changes(session: Session, *, user_id: str | None, start: date, end: date,
            known_at: datetime | None = None) -> list[KGEdge]:
    """Each assertion once if either valid boundary falls within [start, end)."""
    _window(start, end)
    stmt = _edges(user_id, known_at).where(or_(
        (KGEdge.valid_from >= start) & (KGEdge.valid_from < end),
        (KGEdge.valid_to >= start) & (KGEdge.valid_to < end),
    ))
    return list(session.scalars(stmt.order_by(KGEdge.valid_from, KGEdge.recorded_at, KGEdge.id)))


def neighborhood(session: Session, *, user_id: str | None, node_ids: list[int],
                 on_date: date | None = None, known_at: datetime | None = None,
                 hops: int = 1, limit: int = 25) -> NeighborhoodResult:
    """Stable breadth-first, bidirectional traversal, including requested roots.

    Truncated means the edge limit omitted an edge within the requested hop
    horizon; stopping at the requested horizon alone is not truncation.
    """
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in (hops, limit)):
        raise InvalidAssertionError("hops and limit must be nonnegative integers")
    roots = [_node(session, user_id, node_id) for node_id in sorted(set(node_ids))]
    on_date = _on_date(session, user_id, on_date)
    base = _valid(_edges(user_id, known_at), on_date)
    visited = {node.id for node in roots}
    frontier, selected, seen = set(visited), [], set()
    truncated = False
    for _ in range(hops):
        if not frontier:
            break
        remaining = limit - len(selected)
        stmt = base.where(or_(KGEdge.src_id.in_(frontier), KGEdge.dst_id.in_(frontier)))
        if seen:
            stmt = stmt.where(KGEdge.id.not_in(seen))
        candidates = list(session.scalars(stmt.order_by(KGEdge.id).limit(remaining + 1)))
        accepted = candidates[:remaining]
        selected.extend(accepted)
        seen.update(edge.id for edge in accepted)
        if len(candidates) > remaining:
            truncated = True
            break
        endpoints = {node_id for edge in accepted for node_id in (edge.src_id, edge.dst_id)}
        frontier = endpoints - visited
        visited.update(endpoints)
    snapshot = _snapshot(session, selected, roots=roots)
    return NeighborhoodResult(nodes=snapshot.nodes, edges=snapshot.edges, truncated=truncated)


def _text(value):
    return " ".join(value.split()) if isinstance(value, str) else None


def _money(cents):
    amount = abs(cents)
    return f"{'-' if cents < 0 else ''}${amount // 100:,}.{amount % 100:02d}"


def triples(snapshot: GraphSnapshot) -> list[str]:
    """Readable scoped facts with a small explicit display-field whitelist.

    Never serialize keys, IDs, props, raw evidence, or arbitrary value fields.
    Missing labels fall back to node kinds, never stable external identity keys.
    """
    nodes = {node.id: node for node in snapshot.nodes}
    lines = []
    for edge in sorted(snapshot.edges, key=lambda e: (e.src_id, e.rel, e.dst_id, e.valid_from, e.recorded_at, e.id)):
        if edge.src_id not in nodes or edge.dst_id not in nodes:
            continue
        src, dst = nodes[edge.src_id], nodes[edge.dst_id]
        details = []
        value = edge.value_json if isinstance(edge.value_json, dict) else {}
        for key in ("cents", "limit_cents", "typical_cents"):
            cents = value.get(key)
            if isinstance(cents, int) and not isinstance(cents, bool):
                details.append(_money(cents))
        for key in ("category", "frequency", "condition", "text", "status", "subtype", "cause", "action", "outcome"):
            text = _text(value.get(key))
            if text:
                details.append(f"{key}: {text}")
        evidence = edge.evidence_json if isinstance(edge.evidence_json, dict) else {}
        tax_year = value.get("tax_year", evidence.get("tax_year"))
        if isinstance(tax_year, int) and not isinstance(tax_year, bool):
            details.append(f"tax year {tax_year}")
        provenance = [edge.source] if edge.source in ("projection", "user", "kb_extract") else []
        for key in ("label", "source_label", "title", "document_title"):
            text = _text(evidence.get(key))
            if text and text not in provenance:
                provenance.append(text)
        validity = f"valid {edge.valid_from.isoformat()} to {edge.valid_to.isoformat() if edge.valid_to else 'open'} (end exclusive)"
        detail = f": {'; '.join(details)}" if details else ""
        source = f" [{'; '.join(provenance)}]" if provenance else ""
        lines.append(f"{_text(src.label) or src.kind} —{edge.rel}→ {_text(dst.label) or dst.kind}{detail}; {validity}{source}")
    return lines
