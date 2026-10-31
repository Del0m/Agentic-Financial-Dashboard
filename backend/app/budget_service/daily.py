import logging
import threading
from datetime import date
from typing import Optional

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.orm import Session

from app.agents.budget_graph import plaid_sync, run_budget
from app.budget_service.alerts import has_real_plaid_item
from app.budget_service.close import close_due
from app.db.models import User

logger = logging.getLogger(__name__)

# one budget run per user per day, whoever starts it (tpc-21): the scheduler, the run button, or a simulated day.
# they share one lock per user, so they can never overlap; in-process, so the app runs one uvicorn worker

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def user_lock(user_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(user_id, threading.Lock())


# a simulated user's "today" (prefs_json["sim"]["date"]); None for everyone living on the real date
def sim_today(session: Session, user_id: str) -> Optional[date]:
    user = session.get(User, user_id)
    sim = (user.prefs_json or {}).get("sim") if user else None
    return date.fromisoformat(sim["date"]) if sim else None


# the caller holds the user's lock. any month that ended is closed first (tpc-22), so a missed month-end heals on
# the next run; a close that fails is recorded by the close graph, and the budget run still goes (ending
# period_ended). users without a real plaid item (seeded ones) have nothing to sync
def run_daily_locked(session_factory, user_id: str, today: date, *, model: Optional[BaseChatModel],
                     checkpointer: Optional[BaseCheckpointSaver], on_start=None) -> dict:
    try:
        closed = close_due(session_factory, user_id, today)
    except Exception:
        logger.warning("period close failed for one user", exc_info=True)
        closed = []
    with session_factory() as s:
        sync_fn = plaid_sync if has_real_plaid_item(s, user_id) else None
    state = run_budget(session_factory, user_id, today, model=model, sync_fn=sync_fn, checkpointer=checkpointer,
                       on_start=on_start)
    return {**state, "close_run_ids": [c["run_id"] for c in closed]}


# None when another run for this user is going; the caller decides whether that's a skip or a 409
def run_daily(session_factory, user_id: str, today: date, *, model: Optional[BaseChatModel],
              checkpointer: Optional[BaseCheckpointSaver]) -> Optional[dict]:
    lock = user_lock(user_id)
    if not lock.acquire(blocking=False):
        return None
    try:
        return run_daily_locked(session_factory, user_id, today, model=model, checkpointer=checkpointer)
    finally:
        lock.release()
