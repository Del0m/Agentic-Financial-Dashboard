"""The user's half of the graph, from app rows (tpc-36, architecture 6b.2). no model is involved.

The first half is pure: rows in, Assertions out. The second writes them through the store in the caller's session and
never commits. Monthly totals and budgets are corrected in place, so a later run keeps what was believed earlier as
expired versions. Everything here is safe to run again.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.budget_engine.txns import Txn, add_months, month_end, month_start, normalize_merchant, spending
from app.budget_service.initialize import load_txns
from app.db.models import (Account, AdviceLog, AgentRun, Alert, BudgetLine, BudgetPeriod, CategoryRule, KGEdge,
                           KGNode, RecurringStream, Transaction, User, utcnow)

from .store import assert_edge, close_edge, upsert_node
from .types import KGError

logger = logging.getLogger(__name__)

ALWAYS = date(2000, 1, 1)       # bridge edges hold for all time; the store needs a real date
# fixed bridges into the guidance half. keys are the concept keys in kb/concepts.json (tpc-38)
SUBTYPE_CONCEPTS = {"roth": "roth-ira", "ira": "traditional-ira", "401k": "401k", "roth 401k": "401k",
                    "savings": "emergency-fund", "hsa": "hsa", "credit card": "credit-card", "student": "student-loan"}
CATEGORY_CONCEPTS = {"LOAN_PAYMENTS": "debt-avalanche", "BANK_FEES": "credit-card", "SAVINGS_TRANSFER": "emergency-fund"}


@dataclass(frozen=True)
class Assertion:
    src: tuple[str, str]        # (kind, key)
    rel: str
    dst: tuple[str, str]
    value: Any
    valid_from: date
    valid_to: Optional[date] = None
    source: str = "projection"
    dst_shared: bool = False
    src_label: Optional[str] = None
    dst_label: Optional[str] = None


def next_month(month: date) -> date:
    return add_months(month_start(month), 1)


# one edge per merchant per month. refunds net down, the way budget totals do. the category is the one holding most of
# the merchant's month, by the size of its purchases
def spends_at(user_id: str, txns: Iterable[Txn], month: date) -> list[Assertion]:
    start, by_merchant = month_start(month), {}
    for t in spending(txns):
        if t.merchant and normalize_merchant(t.merchant) and start <= t.date <= month_end(start):
            by_merchant.setdefault(normalize_merchant(t.merchant), []).append(t)
    out = []
    for merchant, rows in sorted(by_merchant.items()):
        sizes: dict[str, int] = {}
        for t in rows:
            sizes[t.category] = sizes.get(t.category, 0) + abs(t.amount_cents)
        category = sorted(sizes, key=lambda c: (-sizes[c], c))[0]
        value = {"cents": sum(t.amount_cents for t in rows), "count": len(rows), "category": category}
        out.append(Assertion(("user", user_id), "SPENDS_AT", ("merchant", merchant), value, start, next_month(start),
                             dst_label=rows[0].merchant.strip()))
    return out


def budgets(user_id: str, period_start: date, lines: Sequence[BudgetLine]) -> list[Assertion]:
    start = month_start(period_start)
    return [Assertion(("user", user_id), "BUDGETS", ("category", ln.category),
                      {"limit_cents": ln.limit_cents, "group": ln.group, "source": ln.source}, start, next_month(start),
                      dst_label=ln.name) for ln in sorted(lines, key=lambda ln: ln.category)]


def accounts(user_id: str, rows: Sequence[Account], first_days: dict[int, date], today: date) -> list[Assertion]:
    out = []
    for a in sorted(rows, key=lambda a: a.plaid_account_id):
        since = first_days.get(a.id, today)
        out.append(Assertion(("user", user_id), "HAS_ACCOUNT", ("account", a.plaid_account_id), {"subtype": a.subtype}, since))
        concept = SUBTYPE_CONCEPTS.get((a.subtype or "").lower())
        if concept:
            out.append(Assertion(("account", a.plaid_account_id), "IS_A", ("concept", concept), None, ALWAYS, dst_shared=True))
    return out


def category_bridges(categories: Iterable[str]) -> list[Assertion]:
    return [Assertion(("category", c), "RELATES_TO", ("concept", CATEGORY_CONCEPTS[c]), None, ALWAYS, dst_shared=True)
            for c in sorted(set(categories)) if c in CATEGORY_CONCEPTS]


def stream_key(stream: RecurringStream) -> str:
    return f"{stream.direction}|{stream.merchant_key}"


# the detector's typical amount lags a step: after one raised charge it's still the old price, while previous_cents
# already marks the step. the graph has to show the rise on the charge that made it
def _stream_value(stream: RecurringStream) -> dict:
    stepped = stream.previous_cents is not None and stream.last_cents != stream.previous_cents
    return {"frequency": stream.frequency, "typical_cents": stream.last_cents if stepped else stream.typical_cents}


# ---- writing ----

@dataclass
class Result:
    asserted: int = 0
    corrected: int = 0
    skipped: list[str] = field(default_factory=list)    # facts the store refused, like a backdated insert


def _node(session: Session, user_id: str, ref: tuple[str, str], *, shared: bool = False, label: Optional[str] = None) -> KGNode:
    return upsert_node(session, user_id=None if shared else user_id, kind=ref[0], key=ref[1], label=label)


def _current(session: Session, user_id: str, src_id: int, rel: str, *, dst_id: Optional[int] = None,
             valid_from: Optional[date] = None) -> list[KGEdge]:
    stmt = select(KGEdge).where(KGEdge.user_id == user_id, KGEdge.src_id == src_id, KGEdge.rel == rel,
                                KGEdge.expired_at.is_(None))
    if dst_id is not None:
        stmt = stmt.where(KGEdge.dst_id == dst_id)
    if valid_from is not None:
        stmt = stmt.where(KGEdge.valid_from == valid_from)
    return list(session.scalars(stmt.order_by(KGEdge.valid_from, KGEdge.id)))


def apply(session: Session, user_id: str, assertions: Iterable[Assertion], *, historical: bool = False) -> Result:
    result = Result()
    for a in assertions:
        src = _node(session, user_id, a.src, label=a.src_label)
        dst = _node(session, user_id, a.dst, shared=a.dst_shared, label=a.dst_label)
        try:
            same_start = _current(session, user_id, src.id, a.rel, dst_id=dst.id, valid_from=a.valid_from)
            if same_start and a.valid_to is not None:
                # a month's total or limit that changed: the earlier belief stays as an expired version
                edge = same_start[0]
                if edge.value_json != a.value:
                    assert_edge(session, user_id=user_id, src_id=src.id, rel=a.rel, dst_id=dst.id, value=a.value,
                                valid_from=edge.valid_from, valid_to=edge.valid_to, source=a.source, correct=True,
                                evidence=edge.evidence_json, target_edge_id=edge.id)
                    result.corrected += 1
                continue
            assert_edge(session, user_id=user_id, src_id=src.id, rel=a.rel, dst_id=dst.id, value=a.value,
                        valid_from=a.valid_from, valid_to=a.valid_to, source=a.source, historical=historical)
            result.asserted += 1
        except KGError as exc:
            logger.warning("graph projection skipped %s: %s", a.rel, type(exc).__name__)
            result.skipped.append(f"{a.rel}:{a.dst[1]}:{a.valid_from}")
    return result


def project_month(session: Session, user_id: str, month: date, today: Optional[date] = None, *, historical: bool = False) -> Result:
    start = month_start(month)
    end = min(month_end(start), today) if today else month_end(start)
    wanted = spends_at(user_id, load_txns(session, user_id, start, end), start)
    result = apply(session, user_id, wanted, historical=historical)
    # a merchant with nothing left this month, after a change of category or a removed transaction, goes to zero
    user = _node(session, user_id, ("user", user_id))
    kept = {a.dst[1].strip().casefold() for a in wanted}
    edges = session.execute(select(KGEdge, KGNode).join(KGNode, KGNode.id == KGEdge.dst_id).where(
        KGEdge.user_id == user_id, KGEdge.src_id == user.id, KGEdge.rel == "SPENDS_AT", KGEdge.expired_at.is_(None),
        KGEdge.valid_from == start)).all()
    for edge, merchant in edges:
        if merchant.key not in kept and (edge.value_json or {}).get("count"):
            assert_edge(session, user_id=user_id, src_id=user.id, rel="SPENDS_AT", dst_id=merchant.id, correct=True,
                        value={**edge.value_json, "cents": 0, "count": 0}, valid_from=edge.valid_from,
                        valid_to=edge.valid_to, target_edge_id=edge.id, evidence=edge.evidence_json)
            result.corrected += 1
    return result


def project_period(session: Session, user_id: str, period: BudgetPeriod, *, historical: bool = False) -> Result:
    result = apply(session, user_id, budgets(user_id, period.start_date, period.lines), historical=historical)
    bridges = apply(session, user_id, category_bridges(ln.category for ln in period.lines))
    result.asserted += bridges.asserted
    result.corrected += bridges.corrected
    result.skipped += bridges.skipped
    return result


# the graph's rule edges follow the rules table: the user's decision, dated when it was made. a same-day change of mind
# corrects the edge, and past months keep the category they were recorded with
def project_rules(session: Session, user_id: str, today: date) -> Result:
    result = Result()
    simulated = _sim_day(session, user_id) is not None
    rules = {r.merchant_name: r for r in session.scalars(select(CategoryRule).where(CategoryRule.user_id == user_id))}
    for merchant, rule in sorted(rules.items()):
        src = _node(session, user_id, ("merchant", merchant), label=merchant)
        dst = _node(session, user_id, ("category", rule.category))
        current = [e for e in _current(session, user_id, src.id, "CATEGORIZED_AS") if e.valid_to is None]
        if current and current[0].dst_id == dst.id:
            continue
        # Keep the edit date through projection retries. Legacy rows without it
        # use the caller's date for changes and creation time for their first fact.
        day = rule.effective_from or (today if simulated or current else rule.created_at.date())
        if current and day <= current[0].valid_from:
            assert_edge(session, user_id=user_id, src_id=src.id, rel="CATEGORIZED_AS", dst_id=dst.id, value=None,
                        valid_from=current[0].valid_from, correct=True, target_edge_id=current[0].id, source="user")
            result.corrected += 1
            continue
        assert_edge(session, user_id=user_id, src_id=src.id, rel="CATEGORIZED_AS", dst_id=dst.id, value=None,
                    valid_from=day, source="user")
        result.asserted += 1
    # a rule that's gone ends its edge. the store needs an end after the start, so a same-day delete lasts one day
    open_edges = session.execute(select(KGEdge, KGNode).join(KGNode, KGNode.id == KGEdge.src_id).where(
        KGEdge.user_id == user_id, KGEdge.rel == "CATEGORIZED_AS", KGEdge.expired_at.is_(None), KGEdge.valid_to.is_(None))).all()
    for edge, merchant in open_edges:
        if merchant.key not in rules:
            close_edge(session, user_id=user_id, target_edge_id=edge.id,
                       valid_to=max(today, edge.valid_from + timedelta(days=1)))
            result.corrected += 1
    return result


def project_streams(session: Session, user_id: str) -> Result:
    result = Result()
    user = _node(session, user_id, ("user", user_id))
    rows = session.scalars(select(RecurringStream).where(RecurringStream.user_id == user_id,
                                                         RecurringStream.direction == "out")
                           .order_by(RecurringStream.merchant_key)).all()
    for s in rows:
        stream = _node(session, user_id, ("stream", stream_key(s)), label=s.merchant_name)
        merchant = _node(session, user_id, ("merchant", s.merchant_key), label=s.merchant_name)
        edges = _current(session, user_id, user.id, "SUBSCRIBES", dst_id=stream.id)
        if not edges:
            assert_edge(session, user_id=user_id, src_id=stream.id, rel="SUBSCRIBES", dst_id=merchant.id, value=None,
                        valid_from=s.first_date)
        ongoing = next((e for e in edges if e.valid_to is None), None)
        value = _stream_value(s)
        if s.status == "stale":
            if ongoing is not None:
                close_edge(session, user_id=user_id, target_edge_id=ongoing.id,
                           valid_to=max(s.last_date, ongoing.valid_from) + timedelta(days=1))
                result.corrected += 1
            elif not edges:
                assert_edge(session, user_id=user_id, src_id=user.id, rel="SUBSCRIBES", dst_id=stream.id, value=value,
                            valid_from=s.first_date, valid_to=max(s.last_date, s.first_date) + timedelta(days=1))
                result.asserted += 1
            continue
        latest_end = max((e.valid_to for e in edges if e.valid_to), default=None)
        if ongoing is None:
            start = s.first_date if not edges else max(s.last_date, latest_end)
            # first seen on the charge that raised the price, as on a backfill: record the old price too
            if not edges and value["typical_cents"] != s.typical_cents and s.last_date > s.first_date:
                assert_edge(session, user_id=user_id, src_id=user.id, rel="SUBSCRIBES", dst_id=stream.id,
                            value={**value, "typical_cents": s.previous_cents}, valid_from=s.first_date)
                start = s.last_date
            assert_edge(session, user_id=user_id, src_id=user.id, rel="SUBSCRIBES", dst_id=stream.id, value=value, valid_from=start)
            result.asserted += 1
        elif ongoing.value_json != value and s.last_date > ongoing.valid_from:
            # a price change closes the edge and opens a new one, on the charge that showed it
            assert_edge(session, user_id=user_id, src_id=user.id, rel="SUBSCRIBES", dst_id=stream.id, value=value,
                        valid_from=s.last_date)
            result.corrected += 1
    return result


def _advice_day(session: Session, row: AdviceLog, run_days: Optional[dict[int, date]]) -> date:
    if run_days and row.run_id in run_days:
        return run_days[row.run_id]
    if row.run_id is not None:
        summary = session.scalar(select(AgentRun.summary_json).where(AgentRun.id == row.run_id)) or {}
        if summary.get("as_of"):
            return date.fromisoformat(summary["as_of"])
    return row.created_at.date()


# one edge per piece of advice, on its date. a decided outcome corrects the edge, so the pending belief stays in
# transaction-time history. advice with no category, like a low-balance warning, has nothing to be about
def project_advice(session: Session, user_id: str, run_days: Optional[dict[int, date]] = None) -> Result:
    result = Result()
    rows = session.execute(select(AdviceLog, Alert).outerjoin(Alert, Alert.id == AdviceLog.alert_id).where(
        AdviceLog.user_id == user_id, AdviceLog.category.is_not(None)).order_by(AdviceLog.id)).all()
    for row, alert in rows:
        value = {"cause": alert.cause if alert else None, "action": alert.action if alert else None, "outcome": row.outcome}
        advice = _node(session, user_id, ("advice", f"advice-{row.id}"), label=row.advice_text)
        category = _node(session, user_id, ("category", row.category))
        edges = _current(session, user_id, advice.id, "ABOUT", dst_id=category.id)
        if not edges:
            day = _advice_day(session, row, run_days)
            assert_edge(session, user_id=user_id, src_id=advice.id, rel="ABOUT", dst_id=category.id, value=value,
                        valid_from=day, valid_to=day + timedelta(days=1))
            result.asserted += 1
        elif edges[0].value_json != value:
            assert_edge(session, user_id=user_id, src_id=advice.id, rel="ABOUT", dst_id=category.id, value=value,
                        valid_from=edges[0].valid_from, valid_to=edges[0].valid_to, correct=True,
                        target_edge_id=edges[0].id)
            result.corrected += 1
    return result


def project_accounts(session: Session, user_id: str, today: date) -> Result:
    rows = session.scalars(select(Account).where(Account.user_id == user_id)).all()
    first_days = dict(session.execute(select(Transaction.account_id, func.min(Transaction.date)).where(
        Transaction.user_id == user_id, Transaction.removed.is_(False), Transaction.date <= today)
        .group_by(Transaction.account_id)).all())
    return apply(session, user_id, accounts(user_id, rows, first_days, today))


# Everything up to today, oldest first. Bounded monthly facts may fill gaps left
# by an earlier close or partial run. Today's rules apply to older spending rows.
def backfill(session: Session, user_id: str, today: date) -> Result:
    total = Result()

    def add(result: Result) -> None:
        total.asserted += result.asserted
        total.corrected += result.corrected
        total.skipped += result.skipped

    user = _node(session, user_id, ("user", user_id))
    user.props_json = {**user.props_json, "backfill_complete": False}
    add(project_accounts(session, user_id, today))
    first = session.scalar(select(func.min(Transaction.date)).where(Transaction.user_id == user_id,
                                                                    Transaction.removed.is_(False), Transaction.date <= today))
    month = month_start(first) if first else month_start(today)
    while month <= month_start(today):
        add(project_month(session, user_id, month, today, historical=True))
        month = next_month(month)
    periods = session.scalars(select(BudgetPeriod).where(BudgetPeriod.user_id == user_id, BudgetPeriod.start_date <= today)
                              .order_by(BudgetPeriod.start_date)).all()
    for period in periods:
        add(project_period(session, user_id, period, historical=True))
    add(project_rules(session, user_id, today))
    add(project_streams(session, user_id))
    add(project_advice(session, user_id))
    if not total.skipped:
        user.props_json = {**user.props_json, "backfill_complete": True}
    session.flush()
    return total


def has_graph(session: Session, user_id: str) -> bool:
    return session.scalar(select(KGNode.id).where(KGNode.user_id == user_id, KGNode.kind == "user").limit(1)) is not None


def backfill_complete(session: Session, user_id: str) -> bool:
    node = session.scalar(select(KGNode).where(KGNode.user_id == user_id, KGNode.kind == "user"))
    return node is not None and node.props_json.get("backfill_complete") is True


# the daily run: the first one builds the user's history, and later ones re-project what can have changed
def project_run(session: Session, user_id: str, today: date) -> Result:
    if not backfill_complete(session, user_id):
        return backfill(session, user_id, today)
    total = project_month(session, user_id, today, today)
    for result in (project_accounts(session, user_id, today), project_rules(session, user_id, today),
                   project_streams(session, user_id), project_advice(session, user_id)):
        total.asserted += result.asserted
        total.corrected += result.corrected
        total.skipped += result.skipped
    period = session.scalar(select(BudgetPeriod).where(BudgetPeriod.user_id == user_id,
                                                       BudgetPeriod.start_date == month_start(today)))
    if period is not None:
        project_period(session, user_id, period)
    return total


# the graph must never stop the work around it: a failed projection rolls back only itself, and the next run heals it
def safely(session: Session, what: str, fn: Callable[[], Any]) -> bool:
    try:
        with session.begin_nested():
            fn()
        return True
    except Exception as exc:
        logger.warning("graph projection failed in %s: %s", what, type(exc).__name__)
        return False


def _sim_day(session: Session, user_id: str) -> Optional[date]:
    user = session.get(User, user_id)
    sim = (user.prefs_json or {}).get("sim") if user else None
    return date.fromisoformat(sim["date"]) if sim else None


def current_day(session: Session, user_id: str) -> date:
    return _sim_day(session, user_id) or utcnow().date()
