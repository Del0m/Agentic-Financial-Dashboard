"""What the graph adds to one overspent category's diagnosis (tpc-37, architecture 6b.3).

Code works out every number here, and the model only reads it. Amounts are cents and names are raw: the privacy
gateway formats and scrubs them on the way into a prompt.
"""
from datetime import date
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.budget_engine.recurring import merchant_key
from app.budget_engine.txns import add_months, month_start
from app.db.models import KGEdge, KGNode

from . import query
from .project import CATEGORY_CONCEPTS
from .types import GraphSnapshot

MERCHANT_MONTHS = 13
PRICE_CHANGE_MONTHS = 3
ADVICE_MONTHS = 3
RELATED_TRIPLES = 3
CUT_DISMISSALS = 2      # a cut the user has dismissed this many times isn't proposed again


def _find(session: Session, user_id: str, kind: str, key: Optional[str]) -> Optional[KGNode]:
    if not key:
        return None
    key = key.strip().casefold() if kind == "merchant" else key
    return session.scalar(select(KGNode).where(KGNode.user_id == user_id, KGNode.kind == kind, KGNode.key == key))


def _merchant(session: Session, user_id: str, name: str, period_start: date) -> Optional[dict]:
    node = _find(session, user_id, "merchant", name)
    if node is None:
        return None
    window = add_months(period_start, -MERCHANT_MONTHS)
    months = query.months_active(session, user_id=user_id, merchant_id=node.id, start=window, end=period_start)
    last_year = add_months(period_start, -12)
    return {"merchant": name, "months_active": len(months), "first_seen": months[0].isoformat() if months else None,
            "active_same_month_last_year": last_year in months}


def _rule_moves(session: Session, user_id: str, category: str, period_start: date, today: date) -> list[dict]:
    node = _find(session, user_id, "category", category)
    if node is None:
        return []
    merchant = aliased(KGNode)
    rows = session.execute(select(KGEdge, merchant).join(merchant, merchant.id == KGEdge.src_id).where(
        KGEdge.user_id == user_id, KGEdge.rel == "CATEGORIZED_AS", KGEdge.dst_id == node.id, KGEdge.expired_at.is_(None),
        KGEdge.valid_from >= period_start, KGEdge.valid_from <= today).order_by(KGEdge.valid_from, KGEdge.id)).all()
    user = _find(session, user_id, "user", user_id)
    out = []
    for edge, m in rows:
        spent = session.scalar(select(KGEdge).where(
            KGEdge.user_id == user_id, KGEdge.src_id == user.id, KGEdge.rel == "SPENDS_AT", KGEdge.dst_id == m.id,
            KGEdge.valid_from == period_start, KGEdge.expired_at.is_(None)))
        out.append({"merchant": m.label or m.key, "since": edge.valid_from.isoformat(),
                    "moved_cents": (spent.value_json or {}).get("cents", 0) if spent else 0})
    return out


def _price_changes(session: Session, user_id: str, names: Iterable[str], period_start: date) -> list[dict]:
    user = _find(session, user_id, "user", user_id)
    since, out = add_months(period_start, -PRICE_CHANGE_MONTHS), []
    for name in names:
        stream = _find(session, user_id, "stream", f"out|{merchant_key(name)}")
        if stream is None or user is None:
            continue
        edges = session.scalars(select(KGEdge).where(
            KGEdge.user_id == user_id, KGEdge.src_id == user.id, KGEdge.rel == "SUBSCRIBES", KGEdge.dst_id == stream.id,
            KGEdge.expired_at.is_(None)).order_by(KGEdge.valid_from, KGEdge.id)).all()
        for before, after in zip(edges, edges[1:]):
            old, new = before.value_json["typical_cents"], after.value_json["typical_cents"]
            if new > old and after.valid_from >= since:
                out.append({"merchant": name, "from_cents": old, "to_cents": new, "since": after.valid_from.isoformat()})
    return out


def _advice(session: Session, user_id: str, category: str, period_start: date, today: date) -> list[dict]:
    node = _find(session, user_id, "category", category)
    if node is None:
        return []
    edges = session.scalars(select(KGEdge).where(
        KGEdge.user_id == user_id, KGEdge.rel == "ABOUT", KGEdge.dst_id == node.id, KGEdge.expired_at.is_(None),
        KGEdge.valid_from >= add_months(period_start, -ADVICE_MONTHS), KGEdge.valid_from <= today)
        .order_by(KGEdge.valid_from, KGEdge.id)).all()
    return [{"month": month_start(e.valid_from).isoformat(), "action": e.value_json.get("action"),
             "outcome": e.value_json.get("outcome")} for e in edges]


def _related(session: Session, user_id: str, category: str, today: date) -> list[str]:
    concept = CATEGORY_CONCEPTS.get(category)
    node = session.scalar(select(KGNode).where(KGNode.user_id.is_(None), KGNode.kind == "concept", KGNode.key == concept)) if concept else None
    if node is None:
        return []
    snapshot = query.neighborhood(session, user_id=user_id, node_ids=[node.id], on_date=today, limit=RELATED_TRIPLES * 4)
    guidance = [e for e in snapshot.edges if e.user_id is None][:RELATED_TRIPLES]
    return query.triples(GraphSnapshot(snapshot.nodes, guidance))


def graph_facts(session: Session, user_id: str, category: str, merchants: Iterable[Optional[str]], period_start: date,
                today: date) -> dict:
    names = [m for m in merchants if m]
    # a merchant with no earlier months says nothing the facts don't already. stated as a zero, it read as evidence of a
    # new purchase and pushed the model to one_time on spending spread across new shops (tpc-46)
    seen = [fact for fact in (_merchant(session, user_id, name, period_start) for name in names) if fact and fact["months_active"]]
    last_year = query.same_month_last_year(session, user_id=user_id, category=category, on_date=today)
    return {"merchants": seen,
            "same_month_last_year_cents": sum(e.value_json["cents"] for e in last_year) if last_year else None,
            "rule_moves": _rule_moves(session, user_id, category, period_start, today),
            "price_changes": _price_changes(session, user_id, names, period_start),
            "advice_history": _advice(session, user_id, category, period_start, today),
            "related_guidance": _related(session, user_id, category, today)}


def has_content(facts: Optional[dict]) -> bool:
    return bool(facts) and any(facts[k] for k in ("merchants", "same_month_last_year_cents", "rule_moves", "price_changes",
                                                   "advice_history", "related_guidance"))


def cut_was_dismissed(facts: Optional[dict]) -> bool:
    history = (facts or {}).get("advice_history", [])
    return sum(1 for a in history if a["action"] == "suggest_cuts" and a["outcome"] == "dismissed") >= CUT_DISMISSALS
