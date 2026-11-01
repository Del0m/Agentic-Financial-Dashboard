from datetime import date as Date
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..budget_engine.payoff import Plan, compare
from ..budget_engine.txns import add_months, month_start
from ..budget_service.debts import load_debts
from ..db.models import User
from ..db.session import get_session
from .deps import get_current_user

# the dashboard's payoff comparison (tpc-32): avalanche and snowball with the user's extra, and each without it.
# the user's own data in their own ui, so names aren't scrubbed

router = APIRouter(tags=["debt-plan"])


def _today() -> Date:
    return Date.today()


class DebtPlanRequest(BaseModel):
    extra_monthly_cents: int = Field(ge=0, le=100_000_000)
    include_mortgage: bool = False


class PayoffOut(BaseModel):
    account_id: int
    name: str
    month: int
    date: Date
    interest_cents: int


class PlanOut(BaseModel):
    strategy: str
    extra_monthly_cents: int
    months: Optional[int]
    debt_free: Optional[Date]
    total_interest_cents: int
    order: list[PayoffOut]
    never: list[int]


class BaselineOut(BaseModel):
    avalanche: PlanOut
    snowball: PlanOut


class ExcludedOut(BaseModel):
    account_id: int
    name: str
    reason: str


class DebtPlanResponse(BaseModel):
    avalanche: PlanOut
    snowball: PlanOut
    baseline: BaselineOut
    excluded: list[ExcludedOut]
    start: Date


def _plan(plan: Plan) -> PlanOut:
    return PlanOut(strategy=plan.strategy, extra_monthly_cents=plan.extra_monthly_cents, months=plan.months,
                   debt_free=plan.debt_free, total_interest_cents=plan.total_interest_cents,
                   order=[PayoffOut(account_id=p.key, name=p.name, month=p.month, date=p.date,
                                    interest_cents=p.interest_cents) for p in plan.order],
                   never=list(plan.never))


@router.post("/debt-plan", response_model=DebtPlanResponse)
def debt_plan(body: DebtPlanRequest, user: User = Depends(get_current_user),
              session: Session = Depends(get_session)):
    debts = load_debts(session, user.id)
    start = add_months(month_start(_today()), 1)
    plans = compare(debts, body.extra_monthly_cents, start=start, include_mortgage=body.include_mortgage)
    names = {d.key: d.name for d in debts}
    return DebtPlanResponse(
        avalanche=_plan(plans["avalanche"]), snowball=_plan(plans["snowball"]),
        baseline=BaselineOut(avalanche=_plan(plans["baseline_avalanche"]),
                             snowball=_plan(plans["baseline_snowball"])),
        excluded=[ExcludedOut(account_id=key, name=names[key], reason=reason)
                  for key, reason in plans["avalanche"].excluded],
        start=start,
    )
