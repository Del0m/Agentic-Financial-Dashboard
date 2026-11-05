from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.budget_engine.forecast import CardIn
from app.budget_engine.recurring import merchant_key
from app.budget_engine.txns import Txn, spending
from app.budget_service.initialize import category_rules, resolve_category
from app.budget_service.recurring import current_streams, stored_streams
from app.db.models import Account, Liability, Transaction


def forecast_accounts(session: Session, user_id: str) -> list[Account]:
    return list(session.scalars(select(Account).where(Account.user_id == user_id, Account.type == "depository",
                                                       (Account.subtype == "checking") | Account.subtype.is_(None))))


def start_balance(accounts: list[Account]) -> Optional[int]:
    values = [a.available_balance_cents if a.available_balance_cents is not None else a.current_balance_cents for a in accounts]
    return sum(v or 0 for v in values) if values and all(v is not None for v in values) else None


# one entry per day of history, up to 90: a day before the first transaction isn't a day with no spending
def everyday_series(session: Session, user_id: str, today: date, accounts: list[Account]) -> list[int]:
    since = today - timedelta(days=89)
    keys = {(s.direction, s.merchant_key) for s in [*current_streams(session, user_id, today), *stored_streams(session, user_id)]}
    rules, totals = category_rules(session, user_id), [0]*90
    for row in session.scalars(select(Transaction).where(
            Transaction.user_id == user_id, Transaction.account_id.in_([a.id for a in accounts]),
            Transaction.removed.is_(False), Transaction.pending.is_(False),
            Transaction.date >= since, Transaction.date <= today, Transaction.amount_cents > 0)):
        category, source = resolve_category(row, rules)
        txn = Txn(row.date, row.amount_cents, category, row.pf_category_detailed, row.merchant_name, source != "plaid")
        if ("out", merchant_key(row.merchant_name)) not in keys and spending([txn]):
            totals[(row.date - since).days] += row.amount_cents
    observed = min(history_days(session, user_id, today), 90)
    return totals[-observed:] if observed else []


def load_card_obligations(session: Session, user_id: str) -> list[CardIn]:
    return [CardIn(a.id, a.name, l.min_payment_cents, l.next_due_date) for a, l in session.execute(
        select(Account, Liability).join(Liability, Liability.account_id == Account.id).where(
            Account.user_id == user_id, Liability.user_id == user_id, Liability.kind == "credit",
            (Account.current_balance_cents > 0) | Account.current_balance_cents.is_(None)))]


def inflow_count(session: Session, user_id: str, today: date) -> int:
    rules = category_rules(session, user_id)
    return sum(resolve_category(t, rules)[0] == "INCOME" for t in session.scalars(select(Transaction).where(
        Transaction.user_id == user_id, Transaction.amount_cents < 0, Transaction.date <= today,
        Transaction.date >= today - timedelta(days=180), Transaction.removed.is_(False), Transaction.pending.is_(False))))


def history_days(session: Session, user_id: str, today: date) -> int:
    first = session.scalar(select(func.min(Transaction.date)).where(Transaction.user_id == user_id,
        Transaction.date <= today, Transaction.removed.is_(False), Transaction.pending.is_(False)))
    return (today - first).days + 1 if first else 0
