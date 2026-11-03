"""Scoped, serialized bitemporal graph mutations in a caller-owned transaction.

The source-row UPDATE is a real database lock (including on SQLite). Do not
commit here: source projections and these assertions must succeed together.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from typing import get_args

from sqlalchemy import select, update

from app.db.models import KGEdge, KGNode, User, utcnow
from .types import (
    ClockOrderError, EdgeSource, InvalidAssertionError, InvalidIntervalError,
    NodeKind, OverlapError, Relation, ScopeError, StaleTargetError,
)

_PAIRS = {
    'HAS_ACCOUNT': {('user', 'account')},
    'CONNECTED': {('user', 'institution')},
    'SPENDS_AT': {('user', 'merchant')},
    'CATEGORIZED_AS': {('merchant', 'category')},
    'BUDGETS': {('user', 'category')},
    'SUBSCRIBES': {('user', 'stream'), ('stream', 'merchant')},
    'ABOUT': {('advice', 'category')},
    'IS_A': {('account', 'concept'), ('concept', 'concept')},
    'RELATES_TO': {('category', 'concept')},
    'HAS_LIMIT': {('concept', 'concept')},
    'HAS_RULE': {('concept', 'concept')},
    'ALTERNATIVE_TO': {('concept', 'concept')},
    'MENTIONED_IN': {('concept', 'source')},
}


def _node_scope(kind, user_id):
    """The ontology assigns guidance identities to the shared graph only."""
    if (kind in {'concept', 'source'}) != (user_id is None):
        raise ScopeError('concept/source nodes are shared; other node kinds are owned')


def _json(value):
    """Validate JSON and detach nested mutable values from the caller."""
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise InvalidAssertionError('value must be finite JSON data') from exc


def _time(now):
    now = utcnow() if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ClockOrderError('recording time must be timezone-aware')
    return now.astimezone(timezone.utc)


def _interval(start, end):
    if type(start) is not date or (end is not None and type(end) is not date):
        raise InvalidIntervalError('valid bounds must be calendar dates')
    if end is not None and end <= start:
        raise InvalidIntervalError('valid end must be later than valid start')


def _owner(session, user_id):
    if user_id is not None and session.get(User, user_id) is None:
        raise ScopeError('unknown user scope')


def upsert_node(session, *, user_id, kind, key, label=None, props=None):
    """Upsert stable scoped identity; labels and props are current metadata only."""
    if kind not in get_args(NodeKind):
        raise InvalidAssertionError('unknown node kind')
    _node_scope(kind, user_id)
    if not isinstance(key, str) or not key.strip():
        raise InvalidAssertionError('node key must be nonempty')
    key = key.strip().casefold() if kind == 'merchant' else key.strip()
    if props is not None and not isinstance(props, dict):
        raise InvalidAssertionError('node props must be an object')
    props = _json(props) if props is not None else None
    _owner(session, user_id)
    node = session.scalar(select(KGNode).where(
        KGNode.user_id == user_id, KGNode.kind == kind, KGNode.key == key))
    if node is None:
        node = KGNode(user_id=user_id, kind=kind, key=key,
                      label=label, props_json=props or {})
        session.add(node)
    else:
        if label is not None:
            node.label = label
        if props is not None:
            node.props_json = props
    session.flush()
    return node


def _lock_source(session, user_id, src_id):
    # Every owned relation has an owned source; guidance has shared sources.
    # Scope the UPDATE itself so user writes never mutate a shared source row.
    result = session.execute(update(KGNode).where(
        KGNode.id == src_id, KGNode.user_id == user_id)
                             .values(id=KGNode.id),
                             execution_options={'synchronize_session': False})
    if result.rowcount != 1:
        raise ScopeError('inaccessible source node')


def _endpoints(session, user_id, src_id, rel, dst_id):
    if rel not in _PAIRS:
        raise InvalidAssertionError('unknown relation')
    nodes = {n.id: n for n in session.scalars(select(KGNode).where(
        KGNode.id.in_([src_id, dst_id])).execution_options(populate_existing=True))}
    if len(nodes) != len({src_id, dst_id}):
        raise ScopeError('inaccessible endpoint')
    src, dst = nodes[src_id], nodes[dst_id]
    for node in nodes.values():
        if node.kind not in get_args(NodeKind):
            raise InvalidAssertionError('unknown endpoint kind')
        _node_scope(node.kind, node.user_id)
    if any(n.user_id is not None and n.user_id != user_id for n in nodes.values()):
        raise ScopeError('inaccessible endpoint')
    if user_id is not None and not any(n.user_id == user_id for n in nodes.values()):
        raise ScopeError('user writes require an owned endpoint')
    if (src.kind, dst.kind) not in _PAIRS[rel]:
        raise InvalidAssertionError('invalid endpoint kinds for relation')


def _rows(session, user_id, src_id, rel, dst_id):
    stmt = select(KGEdge).where(KGEdge.user_id == user_id,
        KGEdge.src_id == src_id, KGEdge.rel == rel, KGEdge.expired_at.is_(None))
    if rel != 'CATEGORIZED_AS':
        stmt = stmt.where(KGEdge.dst_id == dst_id)
    return list(session.scalars(stmt.order_by(KGEdge.valid_from, KGEdge.id)
                .execution_options(populate_existing=True)))


def _content(edge, dst_id, value, source, evidence):
    return (edge.dst_id == dst_id and _same_json(edge.value_json, value)
            and edge.source == source and _same_json(edge.evidence_json, evidence))


def _same_json(left, right):
    # Python equality aliases True with 1, including inside nested dictionaries.
    # Exact assertion identity preserves JSON types (and numeric representations)
    # while ignoring only object key order and serialization whitespace.
    return json.dumps(left, sort_keys=True, separators=(',', ':')) == json.dumps(
        right, sort_keys=True, separators=(',', ':'))


def _overlap(a, b, c, d):
    return (d is None or a < d) and (b is None or c < b)


def _replacement(edge, now, **changes):
    fields = {name: deepcopy(getattr(edge, name)) for name in (
        'user_id', 'src_id', 'rel', 'dst_id', 'value_json', 'valid_from',
        'valid_to', 'source', 'evidence_json')}
    fields.update(changes)
    return KGEdge(**fields, recorded_at=now, expired_at=None)


def _replace(session, target, now, replacements):
    if now <= target.recorded_at:
        raise ClockOrderError('replacement time must follow target recording time')
    # The source lock already started a physical transaction before SAVEPOINT.
    # A failed flush rolls back this entire version transition, not caller work.
    with session.begin_nested():
        target.expired_at = now
        session.flush()
        session.add_all(replacements)
        session.flush()


def assert_edge(session, *, user_id, src_id, rel, dst_id, value, valid_from,
                valid_to=None, source='projection', evidence=None, correct=False,
                target_edge_id=None, now=None):
    """Assert an ordered fact, or correct one explicitly named current version."""
    if rel not in get_args(Relation) or source not in get_args(EdgeSource):
        raise InvalidAssertionError('unknown relation or source')
    _interval(valid_from, valid_to)
    if evidence is not None and not isinstance(evidence, dict):
        raise InvalidAssertionError('evidence must be an object')
    value, evidence = _json(value), _json(evidence or {})
    stamp = _time(now)
    if correct and target_edge_id is None:
        raise StaleTargetError('correction requires target_edge_id')
    if not correct and target_edge_id is not None:
        raise InvalidAssertionError('target_edge_id requires correct=True')
    _lock_source(session, user_id, src_id)
    _endpoints(session, user_id, src_id, rel, dst_id)
    rows = _rows(session, user_id, src_id, rel, dst_id)
    if correct:
        target = session.scalar(select(KGEdge).where(KGEdge.id == target_edge_id)
                                .execution_options(populate_existing=True))
        if target is None or target.user_id != user_id:
            raise ScopeError('inaccessible correction target')
        if target.expired_at is not None:
            raise StaleTargetError('correction target is expired')
        if target.src_id != src_id or target.rel != rel:
            raise InvalidAssertionError('correction must preserve source and relation')
        if target.dst_id != dst_id and rel != 'CATEGORIZED_AS':
            raise InvalidAssertionError('only category correction may change destination')
        if (target.valid_from, target.valid_to) != (valid_from, valid_to):
            raise InvalidIntervalError('correction must preserve valid bounds')
        if _content(target, dst_id, value, source, evidence):
            return target
        if any(r.id != target.id and _overlap(valid_from, valid_to, r.valid_from, r.valid_to)
               for r in rows):
            raise OverlapError('correction conflicts with another interval')
        replacement = _replacement(target, stamp, dst_id=dst_id, value_json=value,
                                   source=source, evidence_json=evidence)
        _replace(session, target, stamp, [replacement])
        return replacement
    # Exact bounded retries must be recognized before chronological ordering.
    for row in rows:
        if (row.valid_from, row.valid_to) == (valid_from, valid_to) and _content(
                row, dst_id, value, source, evidence):
            return row
    ongoing = next((r for r in rows if r.valid_to is None), None)
    if ongoing is not None and valid_to is None and valid_from >= ongoing.valid_from:
        if _content(ongoing, dst_id, value, source, evidence):
            return ongoing
        if ongoing.dst_id == dst_id and _same_json(ongoing.value_json, value):
            raise InvalidAssertionError('provenance-only changes require correction')
        if valid_from <= ongoing.valid_from:
            raise OverlapError('same-day change requires explicit correction')
        if any(r.id != ongoing.id and _overlap(valid_from, None, r.valid_from, r.valid_to)
               for r in rows):
            raise OverlapError('new fact conflicts with another interval')
        closed = _replacement(ongoing, stamp, valid_to=valid_from)
        changed = KGEdge(user_id=user_id, src_id=src_id, rel=rel, dst_id=dst_id,
                         value_json=value, valid_from=valid_from, valid_to=None,
                         recorded_at=stamp, expired_at=None, source=source,
                         evidence_json=evidence)
        _replace(session, ongoing, stamp, [closed, changed])
        return changed
    if any(_overlap(valid_from, valid_to, r.valid_from, r.valid_to) for r in rows):
        raise OverlapError('assertion overlaps an existing interval')
    if rows and valid_from <= max(r.valid_from for r in rows):
        raise OverlapError('new intervals must be inserted chronologically')
    edge = KGEdge(user_id=user_id, src_id=src_id, rel=rel, dst_id=dst_id,
                  value_json=value, valid_from=valid_from, valid_to=valid_to,
                  recorded_at=stamp, expired_at=None, source=source,
                  evidence_json=evidence)
    with session.begin_nested():
        session.add(edge)
        session.flush()
    return edge


def close_edge(session, *, user_id, target_edge_id, valid_to, now=None):
    """Version an open fact into an ended fact without rewriting past belief."""
    if type(valid_to) is not date:
        raise InvalidIntervalError('close requires a calendar end date')
    stamp = _time(now)
    # Read only the source identity, then lock and reload before making decisions.
    identity = session.execute(select(KGEdge.src_id, KGEdge.user_id).where(
        KGEdge.id == target_edge_id)).first()
    if identity is None or identity.user_id != user_id:
        raise ScopeError('inaccessible close target')
    _lock_source(session, user_id, identity.src_id)
    target = session.scalar(select(KGEdge).where(KGEdge.id == target_edge_id)
                            .execution_options(populate_existing=True))
    _endpoints(session, user_id, target.src_id, target.rel, target.dst_id)
    if target.expired_at is not None:
        raise StaleTargetError('close target is expired')
    _interval(target.valid_from, valid_to)
    if target.valid_to is not None:
        if target.valid_to == valid_to:
            return target
        raise InvalidIntervalError('target already has a different valid end')
    replacement = _replacement(target, stamp, valid_to=valid_to)
    _replace(session, target, stamp, [replacement])
    return replacement
