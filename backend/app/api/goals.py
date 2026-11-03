import logging
import threading
from datetime import date as Date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from ..agents.goal_graph import run_goals_locked
from ..budget_service.daily import running_elsewhere, sim_today, user_lock
from ..budget_service.goal_decisions import (Decision, GoalAlreadyDecided, GoalExpired, GoalNotAcceptable, GoalNotFound,
                                             decide_goal, finish_decided_runs)
from ..db.models import AgentRun, Goal, User
from . import budget_hooks
from .deps import get_current_user

logger = logging.getLogger(__name__)

# the goals the agent proposed (tpc-23), generating them from the app and deciding on them (tpc-25). a run takes tens of
# seconds and the next.js proxy drops a request after 30 s of silence, so it runs in a background thread and the page
# polls GET /goals/runs/{id}, as it does for the budget agent

router = APIRouter(tags=["goals"])
START_WAIT_SECONDS = 5.0


def _today() -> Date:
    return Date.today()


# how a run is started; tests run it inline, since their in-memory db is one connection threads can't share
def _spawn(work) -> None:
    threading.Thread(target=work, daemon=True).start()


class GoalOut(BaseModel):
    id: int
    kind: str
    priority: int
    monthly_cents: int
    target_cents: int
    target_date: Optional[Date]
    start_date: Optional[Date]
    strategy: Optional[str]
    rationale: str
    status: str


class GenerateRequest(BaseModel):
    date: Optional[Date] = None


class IssueOut(BaseModel):
    message: str
    index: Optional[int] = None
    kind: Optional[str] = None


class GoalRunOut(BaseModel):
    run_id: int
    status: str
    outcome: Optional[str] = None
    reason: Optional[str] = None
    goal_ids: list[int]
    waiting_for: list[int]
    attempts: Optional[int] = None
    issues: list[IssueOut]
    error: Optional[str] = None
    surplus_source: Optional[str] = None


def _out(g: Goal) -> GoalOut:
    return GoalOut(id=g.id, kind=g.kind, priority=g.priority, monthly_cents=g.monthly_cents,
                   target_cents=g.target_cents, target_date=g.target_date, start_date=g.start_date,
                   strategy=g.strategy, rationale=g.rationale, status=g.status)


# a click whose resume failed leaves its run paused; opening the page finishes it. the list matters more than the
# healing, so a checkpointer that won't open doesn't take the page down
def _heal(user_id: str) -> None:
    try:
        finish_decided_runs(budget_hooks.session_factory, user_id, checkpointer=budget_hooks.get_checkpointer())
    except Exception:
        logger.warning("finishing decided goal runs failed", exc_info=True)


# default proposed, so tpc-23's contract holds. current is what the user can act on or lives with: proposals first
@router.get("/goals", response_model=list[GoalOut])
def list_goals(status: Literal["proposed", "accepted", "current"] = "proposed",
               user: User = Depends(get_current_user)):
    _heal(user.id)
    wanted = ("proposed", "accepted") if status == "current" else (status,)
    with budget_hooks.session_factory() as s:
        rows = s.scalars(select(Goal).where(Goal.user_id == user.id, Goal.status.in_(wanted))).all()
    return [_out(g) for g in sorted(rows, key=lambda g: (g.status != "proposed", g.priority, g.id))]


@router.post("/goals/generate", status_code=202)
def generate(req: GenerateRequest, user: User = Depends(get_current_user)):
    factory = budget_hooks.session_factory
    lock = user_lock(user.id)       # shared with the scheduler, the budget agent and simulated days
    if not lock.acquire(blocking=False, session_factory=factory):
        return JSONResponse({"detail": "A budget or goal run is already in progress."}, status_code=409)
    started, run_ids, failed = threading.Event(), [], []

    def on_start(run_id: int) -> None:
        run_ids.append(run_id)
        started.set()

    def work() -> None:
        try:
            run_goals_locked(factory, user.id, today, model=budget_hooks.make_model(), claim=lock,
                             checkpointer=budget_hooks.get_checkpointer(), on_start=on_start)
        except Exception:
            logger.warning("goal run for a user failed", exc_info=True)     # a started run's row says error
            failed.append(True)
        finally:
            started.set()
            lock.release()

    # until the thread owns the lock, this request does: any failure here must hand it back, or every later
    # run would get 409 until a restart
    try:
        if running_elsewhere(factory, user.id):
            lock.release()
            return JSONResponse({"detail": "A budget or goal run is already in progress."}, status_code=409)
        with factory() as s:
            today = req.date or sim_today(s, user.id) or _today()
        _spawn(work)
    except Exception:
        lock.release()
        logger.warning("goal run couldn't start", exc_info=True)
        return JSONResponse({"detail": "The goal run couldn't start."}, status_code=500)
    started.wait(START_WAIT_SECONDS)
    if run_ids:
        return {"run_id": run_ids[0]}
    if failed:      # it died before writing its run row, so there's nothing to poll
        return JSONResponse({"detail": "The goal run couldn't start."}, status_code=500)
    return {"run_id": None}


# the summary of a run closed on restart holds only an error, and a running row's is empty
@router.get("/goals/runs/{run_id}", response_model=GoalRunOut)
def get_run(run_id: int, user: User = Depends(get_current_user)):
    with budget_hooks.session_factory() as s:
        run = s.get(AgentRun, run_id)
        if run is None or run.user_id != user.id or run.graph != "goals":
            raise HTTPException(404, "Run not found.")
        status, summary = run.status, run.summary_json or {}
        goal_ids = list(summary.get("goal_ids") or [])
        # from the rows, so a lost checkpoint doesn't break the page
        waiting = sorted(s.scalars(select(Goal.id).where(Goal.id.in_(goal_ids), Goal.status == "proposed")).all()) \
            if status == "awaiting_user" and goal_ids else []
    # code-written messages only: the model's own text never reaches the response
    issues = [IssueOut(message=str(i.get("message") or ""), index=i.get("index"), kind=i.get("kind"))
              for i in summary.get("validation_issues") or []]
    return GoalRunOut(run_id=run_id, status=status, outcome=summary.get("outcome"), reason=summary.get("reason"),
                      goal_ids=goal_ids, waiting_for=waiting, attempts=summary.get("attempts"), issues=issues,
                      error=summary.get("error"), surplus_source=summary.get("surplus_source"))


def _decide(goal_id: int, decision: Decision, user: User) -> GoalOut:
    # the decision commits before any resume, so a checkpointer that won't open must not stop it: without one it
    # still commits, and GET /goals finishes the run later
    try:
        checkpointer = budget_hooks.get_checkpointer()
    except Exception:
        logger.warning("opening the goal checkpointer failed", exc_info=True)
        checkpointer = None
    try:
        goal = decide_goal(budget_hooks.session_factory, user.id, goal_id, decision, checkpointer=checkpointer)
    except GoalNotFound:
        raise HTTPException(404, "Goal not found.")
    except GoalExpired:
        raise HTTPException(409, "This proposal expired. Generate goals again.")
    except GoalAlreadyDecided:
        raise HTTPException(409, "This goal was already decided.")
    except GoalNotAcceptable:
        raise HTTPException(409, "This proposal is older, or a goal of that kind is already accepted. "
                                 "Generate goals again.")
    return _out(goal)


@router.post("/goals/{goal_id}/accept", response_model=GoalOut)
def accept_goal(goal_id: int, user: User = Depends(get_current_user)):
    return _decide(goal_id, "accepted", user)


# stops an accepted goal too
@router.post("/goals/{goal_id}/dismiss", response_model=GoalOut)
def dismiss_goal(goal_id: int, user: User = Depends(get_current_user)):
    return _decide(goal_id, "dismissed", user)
