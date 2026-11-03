import calendar
import logging
import threading
from datetime import date as Date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from ..agents.budget_graph import waiting_for
from ..budget_engine.pace import BudgetLine as EngineLine, evaluate, history_start
from ..budget_engine.txns import add_months, month_start
from ..budget_service.daily import run_daily_locked, sim_today, user_lock
from ..budget_service.initialize import load_txns
from ..budget_service.settings import set_user_style, user_style
from ..db.models import AdviceLog, AgentRun, BudgetLine, BudgetPeriod, User
from . import budget_hooks
from .deps import get_current_user

logger = logging.getLogger(__name__)

# the budget for the dashboard's pace bars, and running the budget agent from the ui (tpc-20). a run takes
# tens of seconds and the next.js proxy drops a request after 30 s of silence, so it runs in a background
# thread and the page polls GET /budget/runs/{id}

router = APIRouter(tags=["budget"])
START_WAIT_SECONDS = 5.0

def _today() -> Date:
    return Date.today()


# how a run is started; tests run it inline, since their in-memory db is one connection threads can't share
def _spawn(work) -> None:
    threading.Thread(target=work, daemon=True).start()


class PeriodOut(BaseModel):
    start: Date
    end: Date

class LineOut(BaseModel):
    id: int
    category: str
    name: str
    group: str
    kind: str
    limit_cents: int
    spent_cents: int
    pace: Optional[float]
    flagged: bool
    reason: Optional[str]

class UnbudgetedOut(BaseModel):
    category: str
    spent_cents: int

class BudgetOut(BaseModel):
    period: Optional[PeriodOut]
    ended_period: Optional[PeriodOut]   # the latest budget, when today is past it (close hasn't opened the next)
    days_elapsed: int
    days_in_period: int
    lines: list[LineOut]
    unbudgeted: list[UnbudgetedOut]
    income_cents: Optional[int] = None
    total_cents: Optional[int] = None
    style: str = "caps"

class LineEdit(BaseModel):
    name: Optional[str] = None
    group: Optional[str] = None

class LineInfo(BaseModel):
    id: int
    category: str
    name: str
    group: str
    kind: str
    limit_cents: int

class StyleIn(BaseModel):
    style: str
    targets: Optional[dict[str, int]] = None

class StyleOut(BaseModel):
    style: str
    targets: dict[str, int]
    effective_from: Date

class RunRequest(BaseModel):
    date: Optional[Date] = None

class RunOut(BaseModel):
    run_id: int
    status: str
    alert_ids: list[int]
    waiting_for: list[int]
    error: Optional[str]


# live pace, nothing written: the bars are current right after a sync, without waiting for the daily run
@router.get("/budget", response_model=BudgetOut)
def get_budget(user: User = Depends(get_current_user)):
    with budget_hooks.session_factory() as s:
        today = sim_today(s, user.id) or _today()      # a simulated user's dashboard follows the simulation
        days = calendar.monthrange(today.year, today.month)[1]
        style, _ = user_style(s, user.id)
        period = s.scalars(select(BudgetPeriod).where(BudgetPeriod.user_id == user.id)
                           .order_by(BudgetPeriod.start_date.desc()).limit(1)).first()
        if period is None or not (period.start_date <= today <= period.end_date):
            ended = PeriodOut(start=period.start_date, end=period.end_date) \
                if period is not None and period.end_date < today else None
            return BudgetOut(period=None, ended_period=ended, days_elapsed=today.day, days_in_period=days, lines=[],
                             unbudgeted=[], style=style)
        results = evaluate([EngineLine(ln.category, ln.kind, ln.limit_cents, ln.group) for ln in period.lines],
                           load_txns(s, user.id, period.start_date, today), today,
                           load_txns(s, user.id, history_start(period.start_date),
                                     period.start_date - timedelta(days=1)))
        stored = {ln.category: ln for ln in period.lines}
        out = PeriodOut(start=period.start_date, end=period.end_date)
        income, total = period.income_cents, period.total_cents
    return BudgetOut(
        period=out, ended_period=None, days_elapsed=today.day, days_in_period=days,
        lines=[LineOut(id=stored[r.category].id, category=r.category, name=stored[r.category].name,
                       group=stored[r.category].group, kind=r.kind, limit_cents=r.limit_cents,
                       spent_cents=r.spent_cents, pace=r.pace, flagged=r.flagged, reason=r.reason)
               for r in results if r.kind != "unbudgeted"],
        unbudgeted=[UnbudgetedOut(category=r.category, spent_cents=r.spent_cents)
                    for r in results if r.kind == "unbudgeted"],
        income_cents=income, total_cents=total, style=style)


# rename a line, or move it between needs and wants (tpc-22). only the open period's lines; close carries the
# change into the next month. saving and free lines keep their group, so the style rules always find them
@router.patch("/budget/lines/{line_id}", response_model=LineInfo)
def edit_line(line_id: int, edit: LineEdit, user: User = Depends(get_current_user)):
    with budget_hooks.session_factory() as s:
        line = s.get(BudgetLine, line_id)
        if line is None or line.period.user_id != user.id or line.period.status != "open":
            raise HTTPException(404, "Not found.")
        if edit.name is not None:
            name = edit.name.strip()
            if not 1 <= len(name) <= 40:
                raise HTTPException(422, "Names are 1 to 40 characters.")
            line.name = name
        if edit.group is not None and edit.group != line.group:
            if {line.group, edit.group} - {"needs", "wants"}:
                raise HTTPException(422, "Only needs and wants lines can change group.")
            line.group = edit.group
        s.commit()
        return LineInfo(id=line.id, category=line.category, name=line.name, group=line.group, kind=line.kind,
                        limit_cents=line.limit_cents)


# the budget style; it applies from the next close (or the first budget), never to the month under way
@router.put("/budget/style", response_model=StyleOut)
def put_style(req: StyleIn, user: User = Depends(get_current_user)):
    with budget_hooks.session_factory() as s:
        try:
            set_user_style(s, user.id, req.style, req.targets)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        s.commit()
        style, targets = user_style(s, user.id)
        today = sim_today(s, user.id) or _today()
    return StyleOut(style=style, targets=targets, effective_from=add_months(month_start(today), 1))


@router.post("/budget/run", status_code=202)
def start_run(req: RunRequest, user: User = Depends(get_current_user)):
    lock = user_lock(user.id)       # shared with the scheduler and simulated days
    if not lock.acquire(blocking=False, session_factory=budget_hooks.session_factory):
        return JSONResponse({"detail": "A budget run is already in progress."}, status_code=409)
    factory = budget_hooks.session_factory
    started, run_ids, failed = threading.Event(), [], []

    def on_start(run_id: int) -> None:
        run_ids.append(run_id)
        started.set()

    def work() -> None:
        try:
            run_daily_locked(factory, user.id, today, model=budget_hooks.make_model(),
                             checkpointer=budget_hooks.get_checkpointer(), on_start=on_start)
        except Exception:
            logger.warning("budget run for a user failed", exc_info=True)   # a started run's row says error
            failed.append(True)
        finally:
            started.set()
            lock.release()

    # until the thread owns the lock, this request does: any failure here must hand it back, or every later
    # run would get 409 until a restart
    try:
        with factory() as s:
            today = req.date or sim_today(s, user.id) or _today()
        _spawn(work)
    except Exception:
        lock.release()
        logger.warning("budget run couldn't start", exc_info=True)
        return JSONResponse({"detail": "The budget run couldn't start."}, status_code=500)
    started.wait(START_WAIT_SECONDS)
    if run_ids:
        return {"run_id": run_ids[0]}
    if failed:      # it died before writing its run row, so there's nothing to poll
        return JSONResponse({"detail": "The budget run couldn't start."}, status_code=500)
    return {"run_id": None}


@router.get("/budget/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, user: User = Depends(get_current_user)):
    with budget_hooks.session_factory() as s:
        run = s.get(AgentRun, run_id)
        if run is None or run.user_id != user.id or run.graph != "budget":
            raise HTTPException(404, "Run not found.")
        status, summary = run.status, run.summary_json or {}
        alert_ids = sorted(a for a in s.scalars(select(AdviceLog.alert_id).where(AdviceLog.run_id == run_id))
                           if a is not None)
    waiting, error = [], summary.get("error") if status == "error" else None
    if status == "awaiting_user":
        try:
            waiting = waiting_for(budget_hooks.session_factory, budget_hooks.get_checkpointer(), run_id)
        except Exception:
            logger.warning("budget run %s checkpoint unreadable", run_id, exc_info=True)
        # a paused run with no readable checkpoint can never be continued; saying "waiting on 0" would be wrong
        if not waiting:
            status, error = "error", "The paused run's checkpoint is missing."
    return RunOut(run_id=run_id, status=status, alert_ids=alert_ids, waiting_for=waiting, error=error)
