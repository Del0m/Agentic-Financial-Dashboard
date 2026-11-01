import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import config
from .api import budget_hooks
from .budget_service.alerts import SEED_TOKEN
from .budget_service.close import close_due
from .budget_service.daily import run_daily, user_lock
from .db.models import AgentRun, BudgetPeriod, PlaidItem, User

logger = logging.getLogger(__name__)

# the agent's own schedule (tpc-21): every morning a budget check for each real user, and on the 1st the
# month-end slot before it. jobs are plain functions so tests call them directly instead of waiting for a clock


def _today() -> date:
    return datetime.now(ZoneInfo(config.SCHEDULER_TZ)).date()


# real users: a budget or a real plaid item. simulated users live on their own date and are advanced by hand, and a
# seeded user (only the placeholder item) isn't real: its synthetic history would be checked against today's date
def eligible_users(session: Session) -> list[str]:
    has_budget = select(BudgetPeriod.user_id)
    has_item = select(PlaidItem.user_id).where(PlaidItem.access_token_enc != SEED_TOKEN)
    seeded = select(PlaidItem.user_id).where(PlaidItem.access_token_enc == SEED_TOKEN)
    users = session.scalars(select(User).where(or_(User.id.in_(has_budget), User.id.in_(has_item)),
                                               or_(User.id.in_(has_item), User.id.not_in(seeded)))).all()
    return sorted(u.id for u in users if not (u.prefs_json or {}).get("sim"))


# one user at a time (there's one local model). a user already running (the button, a simulation) is skipped
# today; one user's failure is on their run row and doesn't stop the rest
def _ran_today(session, user_id: str, today: date) -> bool:
    # the scheduler's day as utc bounds, so the database finds the one row instead of python reading them all
    tz = ZoneInfo(config.SCHEDULER_TZ)
    start = datetime.combine(today, time(), tz).astimezone(timezone.utc)
    end = datetime.combine(today + timedelta(days=1), time(), tz).astimezone(timezone.utc)
    return session.scalar(select(AgentRun.id).where(AgentRun.user_id == user_id, AgentRun.graph == "budget",
                                                    AgentRun.started_at >= start, AgentRun.started_at < end)
                          .limit(1)) is not None


# skip_done (the startup catch-up): leave out users whose budget already ran today, by any route
def daily_budget_job(session_factory, *, today: Optional[date] = None, model=None, checkpointer=None,
                     skip_done: bool = False) -> dict:
    today = today or _today()
    with session_factory() as s:
        user_ids = eligible_users(s)
        done = {u for u in user_ids if skip_done and _ran_today(s, u, today)}
    result = {"ran": [], "skipped": sorted(done), "failed": []}
    for user_id in user_ids:
        if user_id in done:
            continue
        try:
            state = run_daily(session_factory, user_id, today, model=model or budget_hooks.make_model(),
                              checkpointer=checkpointer or budget_hooks.get_checkpointer())
        except Exception:
            logger.warning("scheduled budget run failed for one user", exc_info=True)
            result["failed"].append(user_id)
            continue
        result["skipped" if state is None else "ran"].append(user_id)
    logger.info("daily budget job: %d ran, %d skipped, %d failed",
                len(result["ran"]), len(result["skipped"]), len(result["failed"]))
    return result


# closes the month that just ended for every real user. close_due does nothing for a month already closed, so the
# startup catch-up can call it again. a user whose run is going is skipped: the 06:00 daily run closes first
def month_end_job(session_factory, *, today: Optional[date] = None) -> dict:
    today = today or _today()
    with session_factory() as s:
        user_ids = eligible_users(s)
    result = {"closed": [], "skipped": [], "failed": []}
    for user_id in user_ids:
        lock = user_lock(user_id)
        if not lock.acquire(blocking=False):
            result["skipped"].append(user_id)
            continue
        try:
            close_due(session_factory, user_id, today)
        except Exception:
            logger.warning("month-end close failed for one user", exc_info=True)
            result["failed"].append(user_id)
            continue
        finally:
            lock.release()
        result["closed"].append(user_id)
    logger.info("month-end job: %d closed, %d skipped, %d failed",
                len(result["closed"]), len(result["skipped"]), len(result["failed"]))
    return result


def _at(hhmm: str) -> dict:
    hour, minute = hhmm.split(":")
    return {"hour": int(hour), "minute": int(minute)}


# not started here: the app's startup starts it when ENABLE_SCHEDULER is on. the cron slots only fire while the
# server is up (the misfire grace covers a busy server, not one that was down); add_catch_up_jobs covers today
def build_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=config.SCHEDULER_TZ)
    options = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 3600}
    sched.add_job(daily_budget_job, CronTrigger(**_at(config.DAILY_RUN_TIME), timezone=config.SCHEDULER_TZ),
                  args=[budget_hooks.session_factory], id="daily_budget", **options)
    sched.add_job(month_end_job, CronTrigger(day=1, **_at(config.CLOSE_RUN_TIME), timezone=config.SCHEDULER_TZ),
                  args=[budget_hooks.session_factory], id="month_end", **options)
    return sched


# the startup catch-up as one job, so on the 1st the month closes before that day's budget runs (the order the cron
# slots and simulated days keep)
def catch_up(session_factory, *, today: date, close: bool, daily: bool) -> None:
    if close:
        month_end_job(session_factory, today=today)
    if daily:
        daily_budget_job(session_factory, today=today, skip_done=True)


# a server started after today's slot would otherwise wait for tomorrow (the in-memory job store forgets missed
# fires). run today's slot once, now, for users it hasn't covered yet. earlier days are never replayed
def add_catch_up_jobs(sched: BackgroundScheduler, now: datetime) -> list[str]:
    now = now.astimezone(ZoneInfo(config.SCHEDULER_TZ))
    passed = lambda hhmm: (now.hour, now.minute) >= (_at(hhmm)["hour"], _at(hhmm)["minute"])
    close, daily = now.day == 1 and passed(config.CLOSE_RUN_TIME), passed(config.DAILY_RUN_TIME)
    if not (close or daily):
        return []
    sched.add_job(catch_up, DateTrigger(run_date=now), args=[budget_hooks.session_factory],
                  kwargs={"today": now.date(), "close": close, "daily": daily}, id="catch_up", misfire_grace_time=3600)
    return ["catch_up"]
