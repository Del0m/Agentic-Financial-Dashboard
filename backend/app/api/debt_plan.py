from datetime import date as Date
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..budget_engine.payoff import Plan, compare
from ..budget_engine.txns import add_months, month_start
from ..budget_service.debts import debt_goal_contributions, goal_payment_schedule, load_debts
from ..budget_service.daily import sim_today
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


class DebtRef(BaseModel):
    account_id: int
    name: str


class PlanOut(BaseModel):
    strategy: str
    extra_monthly_cents: int
    months: Optional[int]
    debt_free: Optional[Date]
    total_interest_cents: Optional[int]   # None when never: 600 months of unpaid interest isn't a total
    order: list[PayoffOut]
    never: list[DebtRef]          # a debt never paid off isn't in order, so it carries its name here


class BaselineOut(BaseModel):
    avalanche: PlanOut
    snowball: PlanOut


class ExcludedOut(BaseModel):
    account_id: int
    name: str
    reason: str


class DebtGoalOut(BaseModel):
    goal_id: int
    monthly_cents: int
    start_date: Date
    target_date: Date


class DebtPlanResponse(BaseModel):
    avalanche: PlanOut
    snowball: PlanOut
    baseline: BaselineOut
    excluded: list[ExcludedOut]
    start: Date
    goal_contributions: list[DebtGoalOut]
    goal_monthly_cents: int              # accepted contributions in the first planned month
    minimum_monthly_cents: int           # included debts only
    additional_extra_monthly_cents: int  # entered extra, on top of goals


def _plan(plan: Plan, names: dict[int, str]) -> PlanOut:
    return PlanOut(strategy=plan.strategy, extra_monthly_cents=plan.extra_monthly_cents, months=plan.months,
                   debt_free=plan.debt_free,
                   total_interest_cents=plan.total_interest_cents if plan.months is not None else None,
                   order=[PayoffOut(account_id=p.key, name=p.name, month=p.month, date=p.date,
                                    interest_cents=p.interest_cents) for p in plan.order],
                   never=[DebtRef(account_id=key, name=names[key]) for key in plan.never])


@router.post("/debt-plan", response_model=DebtPlanResponse)
def debt_plan(body: DebtPlanRequest, user: User = Depends(get_current_user),
              session: Session = Depends(get_session)):
    debts = load_debts(session, user.id)
    start = add_months(month_start(sim_today(session, user.id) or _today()), 1)
    goals = debt_goal_contributions(session, user.id, start)
    plans = compare(debts, body.extra_monthly_cents, start=start, include_mortgage=body.include_mortgage,
                    scheduled_extras=goal_payment_schedule(goals))
    names = {d.key: d.name for d in debts}
    return DebtPlanResponse(
        avalanche=_plan(plans["avalanche"], names), snowball=_plan(plans["snowball"], names),
        baseline=BaselineOut(avalanche=_plan(plans["baseline_avalanche"], names),
                             snowball=_plan(plans["baseline_snowball"], names)),
        excluded=[ExcludedOut(account_id=key, name=names[key], reason=reason)
                  for key, reason in plans["avalanche"].excluded],
        start=start,
        goal_contributions=[DebtGoalOut(goal_id=g.id, monthly_cents=g.monthly_cents,
                                       start_date=g.start_date, target_date=g.target_date) for g in goals],
        goal_monthly_cents=sum(g.monthly_cents for g in goals if g.start_date <= start),
        minimum_monthly_cents=sum(d.min_payment_cents or 0 for d in debts if d.balance_cents > 0
                                 and d.key not in {k for k, _ in plans["avalanche"].excluded}),
        additional_extra_monthly_cents=body.extra_monthly_cents,
    )
