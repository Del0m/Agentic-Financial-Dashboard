from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.budget_engine.groups import default_group, default_name
from app.budget_engine.fit import FIXED_DEFAULT, HISTORY_MONTHS, FitResult, FittedLine, fit_budget
from app.budget_engine.txns import (SAVINGS, SAVINGS_DETAILED, Txn, add_months, month_end, month_start,
                                    normalize_merchant)
from app.db.models import BudgetLine, BudgetPeriod, CategoryRule, Transaction

# the db side of the initial budget (architecture 6.3): load history, fit it with budget_engine (which
# stays pure), and store the result with the evidence the agent needs to explain it


# one place decides a transaction's category, so fit, pace, close and the api agree (tpc-22): the user's
# correction, then their merchant rule, then the llm's category, then a transfer to savings, then plaid's.
# source is what the transactions list shows: "user", "rule", or "plaid" for everything automatic
def resolve_category(row: Transaction, rules: dict[str, str]) -> tuple[str, str]:
    if row.user_category:
        return row.user_category, "user"
    rule = rules.get(normalize_merchant(row.merchant_name)) if row.merchant_name else None
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
    txns = load_txns(session, user_id, add_months(current, -HISTORY_MONTHS), current - timedelta(days=1))
    fit = fit_budget(txns, today, fixed)
    if not fit.lines:
        return None

    period = BudgetPeriod(user_id=user_id, start_date=current, end_date=month_end(today), status="open",
                          fit_json=_period_json(fit))
    period.lines = [BudgetLine(category=ln.category, name=default_name(ln.category), group=default_group(ln.category),
                               kind=ln.kind, limit_cents=ln.limit_cents, source="init",
                               fit_json=_line_json(ln)) for ln in fit.lines]
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
