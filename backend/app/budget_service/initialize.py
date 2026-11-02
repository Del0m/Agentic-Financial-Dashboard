from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.budget_engine.groups import default_group, default_name
from app.budget_engine.pace import BudgetLine as EngineLine
from app.budget_engine.styles import apply_style
from app.budget_engine.fit import FIXED_DEFAULT, HISTORY_MONTHS, FitResult, FittedLine, fit_budget
from app.budget_engine.txns import (FREE, SAVINGS, SAVINGS_DETAILED, Txn, add_months, month_end, month_start,
                                    normalize_merchant, spending)
from app.budget_service.settings import user_style
from app.db.models import BudgetLine, BudgetPeriod, CategoryRule, Transaction

# the db side of the initial budget (architecture 6.3): load history, fit it with budget_engine (which
# stays pure), and store the result with the evidence the agent needs to explain it


# one place decides a transaction's category, so fit, pace, close and the api agree (tpc-22): the user's
# correction, then their merchant rule, then the llm's category, then a transfer to savings, then plaid's.
# source is what the transactions list shows: "user", "rule", or "plaid" for everything automatic
def resolve_category(row: Transaction, rules: dict[str, str]) -> tuple[str, str]:
    if row.user_category:
        return row.user_category, "user"
    # a rule is about the merchant's purchases: its paychecks, transfers and card payments keep their category
    plain = Txn(row.date, row.amount_cents, row.pf_category_primary or "UNCATEGORIZED", row.pf_category_detailed)
    rule = rules.get(normalize_merchant(row.merchant_name)) if row.merchant_name and spending([plain]) else None
    if rule:
        return rule, "rule"
    if row.our_category:
        return row.our_category, "plaid"
    if row.pf_category_primary == "TRANSFER_OUT" and row.pf_category_detailed == SAVINGS_DETAILED:
        return SAVINGS, "plaid"
    return row.pf_category_primary or "UNCATEGORIZED", "plaid"


def category_rules(session: Session, user_id: str) -> dict[str, str]:
    return {r.merchant_name: r.category
            for r in session.scalars(select(CategoryRule).where(CategoryRule.user_id == user_id))}


# the user's spending history as budget_engine Txns
def history_start(session: Session, user_id: str, end: date) -> Optional[date]:
    """Read the real history boundary even when the month before the window is quiet."""
    return session.scalar(select(func.min(Transaction.date)).where(
        Transaction.user_id == user_id, Transaction.removed.is_(False), Transaction.date <= end))


def load_txns(session: Session, user_id: str, start: date, end: date) -> list[Txn]:
    rules = category_rules(session, user_id)
    rows = session.scalars(select(Transaction).where(
        Transaction.user_id == user_id, Transaction.removed.is_(False),
        Transaction.date >= start, Transaction.date <= end))
    txns = []
    for r in rows:
        category, source = resolve_category(r, rules)
        txns.append(Txn(date=r.date, amount_cents=r.amount_cents, category=category, detailed=r.pf_category_detailed,
                        merchant=r.merchant_name, overridden=source != "plaid"))
    return txns


def _latest_period(session: Session, user_id: str) -> Optional[BudgetPeriod]:
    return session.scalars(select(BudgetPeriod).where(BudgetPeriod.user_id == user_id)
                           .order_by(BudgetPeriod.start_date.desc()).limit(1)).first()


# runs only when the user has no budget at all; opening later months is period close's job.
# returns None (and writes nothing) until there's a complete month to fit, so the next run retries.
# it commits, like sync_transactions, so call it with nothing else pending in the session
def initialize_budget(session: Session, user_id: str, today: date,
                      fixed: frozenset[str] = FIXED_DEFAULT) -> Optional[BudgetPeriod]:
    existing = _latest_period(session, user_id)
    if existing is not None:
        return existing
    current = month_start(today)
    # a month further back than it fits, so the window's edge doesn't look like the start of the history
    txns = load_txns(session, user_id, add_months(current, -HISTORY_MONTHS - 1), current - timedelta(days=1))
    fit = fit_budget(txns, today, fixed, history_start=history_start(session, user_id, current - timedelta(days=1)))
    if not fit.lines:
        return None

    style, targets = user_style(session, user_id)
    groups = {ln.category: default_group(ln.category) for ln in fit.lines}
    result = apply_style(style, [EngineLine(ln.category, ln.kind, ln.limit_cents, groups[ln.category])
                                 for ln in fit.lines], fit.income_cents, fit.spending_total_cents, targets)

    period = BudgetPeriod(user_id=user_id, start_date=current, end_date=month_end(today), status="open",
                          fit_json=_period_json(fit), income_cents=fit.income_cents, total_cents=result.total_cents)
    period.lines = [BudgetLine(category=ln.category, name=default_name(ln.category), group=groups[ln.category],
                               kind=ln.kind, limit_cents=result.limits[ln.category], source="init",
                               fit_json=_line_json(ln)) for ln in fit.lines]
    # lines the style adds: the free line always, a saving line when 50/30/20 sets one the history didn't show
    for category, rule in ((SAVINGS, "target"), (FREE, "remainder")):
        if category in result.limits and category not in groups:
            period.lines.append(BudgetLine(category=category, name=default_name(category),
                                           group=default_group(category),
                                           kind="fixed" if category == SAVINGS else "flexible",
                                           limit_cents=result.limits[category], source="init",
                                           fit_json={"rule": rule}))
    session.add(period)
    try:
        session.commit()
    except IntegrityError:
        # another run opened this month's period between our check and our commit. with no period to
        # show for it, the error was something else and must not pass as "not enough history yet"
        session.rollback()
        winner = _latest_period(session, user_id)
        if winner is None:
            raise
        return winner
    return period


def _period_json(fit: FitResult) -> dict:
    return {"window": [m.isoformat() for m in fit.window], "skipped": list(fit.skipped)}


def _line_json(line: FittedLine) -> dict:
    return {"monthly_totals": list(line.monthly_totals),
            "outliers": [{"month": o.month.isoformat(), "total_cents": o.total_cents,
                          "median_cents": o.median_cents,
                          "top_txns": [{"date": t.date.isoformat(), "amount_cents": t.amount_cents,
                                        "merchant": t.merchant} for t in o.top_txns]}
                         for o in line.outliers]}
