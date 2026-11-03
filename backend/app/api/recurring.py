from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..budget_service.recurring import recurring_snapshot
from ..db.models import User
from ..db.session import get_session
from .deps import get_current_user

router = APIRouter(tags=["recurring"])


class RecurringStreamOut(BaseModel):
    id: int
    direction: Literal["in", "out"]
    label: str
    frequency: str
    amount_kind: Literal["fixed", "varies"]
    typical_cents: int
    last_cents: int
    last_date: date
    next_date: date
    status: str
    change: Optional[Literal["new", "price_increase", "income_change"]]
    previous_cents: Optional[int]
    first_seen: date


class RecurringOut(BaseModel):
    as_of: Optional[date]
    streams: list[RecurringStreamOut]


@router.get("/recurring", response_model=RecurringOut)
def get_recurring(user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    return recurring_snapshot(session, user.id)
