from dataclasses import asdict
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, StrictInt
from sqlalchemy.orm import Session

from ..budget_engine.goals import goal_cap
from ..budget_service.daily import sim_today
from ..budget_service.forecast_data import forecast_accounts, start_balance
from ..budget_service.forecast_prefs import DEFAULT_BUFFER_CENTS, set_user_buffer
from ..budget_service.forecast_service import snapshot
from ..budget_service.goal_prefs import user_goal_prefs
from ..db.models import User
from ..db.session import get_session
from .deps import get_current_user

router = APIRouter(tags=["forecast"])


def _today() -> date:
    return date.today()


class ForecastDayOut(BaseModel):
    date: date
    expected_cents: int
    low_cents: int
    high_cents: int


class ForecastEventOut(BaseModel):
    date: date
    label: str
    cents: int
    kind: str


class ForecastDipOut(BaseModel):
    date: date
    days_ahead: int
    shortfall_cents: int
    bill_label: Optional[str]


class ForecastOut(BaseModel):
    today: date
    status: str
    start_cents: Optional[int]
    buffer_cents: int
    horizon_days: int
    income: str
    days: list[ForecastDayOut]
    events: list[ForecastEventOut]
    dip: Optional[ForecastDipOut]
    monthly_surplus_cents: Optional[int]
    surplus_source: str
    suggested_extra_cents: Optional[int]
    notes: list[str]


@router.get("/forecast", response_model=ForecastOut)
def get_forecast(days: Optional[int] = Query(default=None, ge=1, le=90), user: User = Depends(get_current_user),
                 session: Session = Depends(get_session)):
    today = sim_today(session, user.id) or _today()
    snap = snapshot(session, user.id, today)
    r = snap.result
    horizon = min(days, r.horizon_days) if days is not None else r.horizon_days
    path = r.days[:horizon]
    events = [e for e in r.events if path and e.date <= path[-1].date]
    notes = ["No paycheck found, so no income is assumed."] if r.income == "none" else []
    if any(a.startswith("card_due_date_unknown:") for a in r.assumptions):
        notes.append("A card minimum has no due date; it counts in the monthly surplus but has no scheduled date.")
    if any(a.startswith("bill_overdue:") for a in r.assumptions):
        notes.append("An overdue bill was rolled to its next scheduled date.")
    return {"today": today, "status": r.status, "start_cents": start_balance(forecast_accounts(session, user.id)),
        "buffer_cents": snap.buffer_cents, "horizon_days": horizon, "income": r.income,
        "days": [asdict(d) for d in path], "events": [{"date": e.date, "label": e.name, "cents": e.cents,
            "kind": "income" if e.cents > 0 else "bill" if e.source == "stream" else e.source} for e in events],
        "dip": None if r.dip is None else {"date": r.dip.date, "days_ahead": r.dip.days_ahead,
            "shortfall_cents": r.dip.shortfall_cents, "bill_label": r.dip.cause.name if r.dip.cause else None},
        "monthly_surplus_cents": r.monthly_surplus_cents,
        "surplus_source": "forecast" if r.surplus_fallback is None else "measured",
        "suggested_extra_cents": None if r.monthly_surplus_cents is None else goal_cap(r.monthly_surplus_cents,
            r.committed_goal_cents, user_goal_prefs(session, user.id).max_share, net_of_goals=True), "notes": notes}


class BufferRequest(BaseModel):
    buffer_cents: StrictInt


@router.put("/forecast/buffer")
def put_buffer(body: BufferRequest, user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    try:
        set_user_buffer(session, user.id, body.buffer_cents)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    session.commit()
    return {"buffer_cents": body.buffer_cents, "default_cents": DEFAULT_BUFFER_CENTS}
