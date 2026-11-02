from datetime import date as Date
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Goal, User
from ..db.session import get_session
from .deps import get_current_user

# the goals the agent proposed (tpc-23). generating them from the app and deciding on them come with tpc-25

router = APIRouter(tags=["goals"])


class GoalOut(BaseModel):
    id: int
    kind: str
    priority: int
    monthly_cents: int
    target_cents: int
    target_date: Optional[Date]
    strategy: Optional[str]
    rationale: str
    status: str


@router.get("/goals", response_model=list[GoalOut])
def list_goals(user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    rows = session.scalars(select(Goal).where(Goal.user_id == user.id, Goal.status == "proposed")
                           .order_by(Goal.priority, Goal.id)).all()
    return [GoalOut(id=g.id, kind=g.kind, priority=g.priority, monthly_cents=g.monthly_cents,
                    target_cents=g.target_cents, target_date=g.target_date, strategy=g.strategy,
                    rationale=g.rationale, status=g.status) for g in rows]
