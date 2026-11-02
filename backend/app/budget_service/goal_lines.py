from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.budget_engine.goals import CommittedGoal
from app.db.models import AgentRun, Goal, utcnow

# the goals a budget has to know about (tpc-25). close and the first budget read them here, and so does the goal
# graph, so this module can't import app.agents


def committed_goals(session: Session, user_id: str) -> list[CommittedGoal]:
    rows = session.scalars(select(Goal).where(Goal.user_id == user_id, Goal.status == "accepted")
                           .order_by(Goal.priority, Goal.id))
    return [CommittedGoal(g.id, g.kind, g.monthly_cents, g.start_date, g.target_date) for g in rows]


# proposals a budget for `month` has passed by: the surplus behind them is stale, and a row with no start can't be
# accepted at all
def stale_proposals(session: Session, user_id: str, month: date) -> list[int]:
    return list(session.scalars(select(Goal.id).where(
        Goal.user_id == user_id, Goal.status == "proposed", or_(Goal.start_date.is_(None), Goal.start_date <= month))
        .order_by(Goal.id)))


# marks the goals superseded and the goal runs paused on them expired; the caller commits. the status is checked
# again here, in the caller's own transaction: a user who answered in the meantime keeps their answer. returns the
# ids actually expired
def expire_goal_proposals(session: Session, goal_ids: list[int]) -> list[int]:
    if not goal_ids:
        return []
    goals = session.scalars(select(Goal).where(Goal.id.in_(goal_ids), Goal.status == "proposed")).all()
    for goal in goals:
        goal.status = "superseded"
    runs = session.scalars(select(AgentRun).where(
        AgentRun.id.in_({g.run_id for g in goals if g.run_id is not None}),
        AgentRun.graph == "goals", AgentRun.status == "awaiting_user")).all()
    for run in runs:
        waited = sorted(g.id for g in goals if g.run_id == run.id)
        run.status, run.finished_at = "expired", utcnow()
        run.summary_json = {**(run.summary_json or {}), "expired_goal_ids": waited}
    return sorted(g.id for g in goals)
