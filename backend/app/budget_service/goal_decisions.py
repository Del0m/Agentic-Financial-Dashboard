import logging
from typing import Literal, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import exists, select, update
from sqlalchemy.orm import aliased

from app.agents.goal_graph import finish_paused_run, goal_waiting_for, resume_goal_run
from app.db.models import AgentRun, Goal

logger = logging.getLogger(__name__)

# the user's side of the goal agent (tpc-25): accept, dismiss or stop a goal, and continue a run that was paused on it.
# the same shape as alerts.decide_alert

Decision = Literal["accepted", "dismissed"]


class GoalNotFound(Exception):
    pass


class GoalAlreadyDecided(Exception):
    pass


# a proposal that went stale before the user answered; GoalAlreadyDecided would blame a decision they never made
class GoalExpired(GoalAlreadyDecided):
    pass


class GoalNotAcceptable(Exception):
    pass


def _paused_runs(session_factory, user_id: str) -> list[int]:
    with session_factory() as s:
        return s.scalars(select(AgentRun.id).where(AgentRun.user_id == user_id, AgentRun.graph == "goals",
                                                   AgentRun.status == "awaiting_user")).all()


# a paused run whose goals were decided without a resume reaching it (the resume failed, or two decisions raced) is
# continued here. the run reads every decision from the goal rows, so one resume is enough. one whose checkpoint is gone
# can't resume, so its rows finish it once none is still proposed
def _resume_decided(session_factory, checkpointer: BaseCheckpointSaver, run_id: int) -> None:
    try:
        waiting = goal_waiting_for(session_factory, checkpointer, run_id)
    except Exception:
        # a checkpoint that can't be read (deleted file, older build) counts as gone
        logger.warning("goal run %s checkpoint unreadable", run_id, exc_info=True)
        waiting = []
    try:
        if not waiting:
            finish_paused_run(session_factory, run_id)
            return
        with session_factory() as s:
            decided = s.execute(select(Goal.id, Goal.status).where(Goal.id.in_(waiting), Goal.status != "proposed")
                                .order_by(Goal.id)).first()
        if decided is not None:
            resume_goal_run(session_factory, run_id, decided.id, decided.status, checkpointer=checkpointer)
    except Exception:
        logger.warning("goal run %s resume failed", run_id, exc_info=True)


def finish_decided_runs(session_factory, user_id: str, *, checkpointer: BaseCheckpointSaver) -> None:
    for run_id in _paused_runs(session_factory, user_id):
        _resume_decided(session_factory, checkpointer, run_id)


# the decision is committed before any paused run is resumed, so a failing resume never loses the user's click.
# accepting is one conditional update, so two requests can't both pass a status check and two goals of a kind can't
# both be accepted: the budget holds one line per kind. a null date would make every later close record an error run
def decide_goal(session_factory, user_id: str, goal_id: int, decision: Decision, *,
                checkpointer: Optional[BaseCheckpointSaver] = None) -> Goal:
    with session_factory() as s:
        goal = s.get(Goal, goal_id)
        if goal is None or goal.user_id != user_id:
            raise GoalNotFound(goal_id)
        if decision == "accepted":
            other = aliased(Goal)
            changed = s.execute(update(Goal).where(
                Goal.id == goal_id, Goal.status == "proposed", Goal.start_date.is_not(None),
                Goal.target_date.is_not(None),
                ~exists().where(other.user_id == user_id, other.kind == goal.kind, other.status == "accepted"),
            ).values(status="accepted")).rowcount
        else:
            changed = s.execute(update(Goal).where(Goal.id == goal_id, Goal.status.in_(("proposed", "accepted")))
                                .values(status="dismissed")).rowcount
        if not changed:
            s.rollback()
            # nothing changed, so the row now says why
            status = s.scalar(select(Goal.status).where(Goal.id == goal_id))
            if decision == "accepted" and status == "superseded":
                raise GoalExpired(goal_id)
            if decision == "dismissed" or status != "proposed":
                raise GoalAlreadyDecided(goal_id)
            raise GoalNotAcceptable(goal_id)
        s.commit()
        s.refresh(goal)
    if checkpointer is not None:
        finish_decided_runs(session_factory, user_id, checkpointer=checkpointer)
    return goal
