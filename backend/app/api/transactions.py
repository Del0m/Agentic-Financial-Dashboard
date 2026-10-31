from datetime import date as Date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..budget_engine.txns import FREE, SAVINGS, Txn, month_end, month_start, normalize_merchant, spending
from ..budget_service.daily import sim_today
from ..budget_service.initialize import category_rules, resolve_category
from ..db.models import BudgetLine, BudgetPeriod, CategoryRule, Transaction, User
from . import budget_hooks
from .deps import get_current_user

# the dashboard's transactions table (tpc-22): each row with the budget line it counts against, and the
# category button that corrects it. plaid's category applies on its own; a correction wins over it, and
# "remember" makes it a rule for that merchant

router = APIRouter(tags=["transactions"])


def _today() -> Date:
    return Date.today()


class TxnOut(BaseModel):
    id: int
    date: Date
    merchant: Optional[str]
    amount_cents: int
    category: str
    line_name: Optional[str]        # null when it counts against no line (income, transfers, no budget that month)
    source: str                     # plaid | rule | user

class TxnEdit(BaseModel):
    category: str
    remember: bool = False


def _period_lines(session: Session, user_id: str, day: Date) -> dict[str, BudgetLine]:
    period = session.scalars(select(BudgetPeriod).where(
        BudgetPeriod.user_id == user_id, BudgetPeriod.start_date <= day, BudgetPeriod.end_date >= day)).first()
    return {ln.category: ln for ln in period.lines} if period else {}


# the line a transaction counts against: its own category's line, the saving line for a transfer to savings, or
# the free line for any other spending without a line
def _out(row: Transaction, rules: dict[str, str], lines: dict[str, BudgetLine]) -> TxnOut:
    category, source = resolve_category(row, rules)
    line = lines.get(category)
    if line is None and category != SAVINGS and spending([Txn(row.date, row.amount_cents, category,
                                                              row.pf_category_detailed, row.merchant_name,
                                                              overridden=source != "plaid")]):
        line = lines.get(FREE)
    return TxnOut(id=row.id, date=row.date, merchant=row.merchant_name, amount_cents=row.amount_cents,
                  category=category, line_name=line.name if line else None, source=source)


@router.get("/transactions", response_model=list[TxnOut])
def list_transactions(month: Optional[str] = None, user: User = Depends(get_current_user)):
    with budget_hooks.session_factory() as s:
        try:
            start = Date.fromisoformat(f"{month}-01") if month else month_start(sim_today(s, user.id) or _today())
        except ValueError:
            raise HTTPException(422, "Month must be YYYY-MM.")
        rows = s.scalars(select(Transaction).where(
            Transaction.user_id == user.id, Transaction.removed.is_(False), Transaction.date >= start,
            Transaction.date <= month_end(start)).order_by(Transaction.date.desc(), Transaction.id.desc())).all()
        rules, lines = category_rules(s, user.id), _period_lines(s, user.id, start)
        return [_out(r, rules, lines) for r in rows]


# the choice is one of the user's open-month lines, by category key. a correction to a closed month is saved but
# doesn't reopen it
@router.patch("/transactions/{txn_id}", response_model=TxnOut)
def correct_transaction(txn_id: int, edit: TxnEdit, user: User = Depends(get_current_user)):
    with budget_hooks.session_factory() as s:
        row = s.get(Transaction, txn_id)
        if row is None or row.user_id != user.id:
            raise HTTPException(404, "Not found.")
        open_period = s.scalars(select(BudgetPeriod).where(BudgetPeriod.user_id == user.id,
                                                           BudgetPeriod.status == "open")
                                .order_by(BudgetPeriod.start_date.desc()).limit(1)).first()
        if open_period is None or edit.category not in {ln.category for ln in open_period.lines} | {FREE}:
            raise HTTPException(422, "Pick one of your budget lines.")
        if edit.remember and not (row.merchant_name or "").strip():
            raise HTTPException(422, "This transaction has no merchant to remember.")
        row.user_category = edit.category
        if edit.remember:
            key = normalize_merchant(row.merchant_name)
            rule = s.scalars(select(CategoryRule).where(CategoryRule.user_id == user.id,
                                                        CategoryRule.merchant_name == key)).first()
            if rule is None:
                s.add(CategoryRule(user_id=user.id, merchant_name=key, category=edit.category))
            else:
                rule.category = edit.category
        s.commit()
        return _out(row, category_rules(s, user.id), _period_lines(s, user.id, row.date))
