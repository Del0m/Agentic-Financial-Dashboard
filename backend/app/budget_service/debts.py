from sqlalchemy import select
from sqlalchemy.orm import Session

from app.budget_engine.payoff import Debt
from app.db.models import Account, Liability

# the planner's debts (tpc-32), one loader for the api and the gateway so they never plan from different numbers.
# the balance is the account's current one, like the dashboard and the debts purpose (review fix on #6)


def load_debts(session: Session, user_id: str) -> list[Debt]:
    rows = session.execute(
        select(Liability, Account)
        .join(Account, Liability.account_id == Account.id)
        .where(Liability.user_id == user_id)
        .order_by(Account.id)
    ).all()
    return [Debt(key=a.id, name=a.name, kind=l.kind, apr=l.apr, balance_cents=a.current_balance_cents or 0,
                 min_payment_cents=l.min_payment_cents) for l, a in rows]
