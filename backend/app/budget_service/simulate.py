import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import select

from app.budget_service.daily import run_daily_locked, sim_prefs
from app.db.models import Account, AgentRun, Transaction, User

# simulated days for synthetic users (tpc-21): each advanced day injects that day's generated transactions,
# runs the month-end slot on the 1st, then the real budget run. the caller holds the user's run lock


class NotSimulated(Exception):
    pass


# the synthetic profiles live in eval/ at the repo root, outside the backend package; only the simulation needs
# them, so they're imported here, when a simulation actually runs
def _profiles():
    repo = str(Path(__file__).resolve().parents[3])
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from eval import budget_users
    return budget_users


# that day's generated transactions (index, row), skipping any already in (stable ids), so a retried day can't
# double up
def _inject(session_factory, user_id: str, users, profile, todays: list) -> int:
    if not todays:
        return 0
    with session_factory() as s:
        account_id = s.scalar(select(Account.id).where(Account.user_id == user_id).order_by(Account.id).limit(1))
        have = set(s.scalars(select(Transaction.plaid_txn_id).where(
            Transaction.plaid_txn_id.in_([users.txn_id(profile, n) for n, _ in todays]))))
        added = 0
        for n, (when, cents, category, merchant) in todays:
            if users.txn_id(profile, n) in have:
                continue
            s.add(Transaction(user_id=user_id, account_id=account_id, plaid_txn_id=users.txn_id(profile, n),
                              date=when, amount_cents=cents, merchant_name=merchant, pf_category_primary=category,
                              pf_category_detailed=users.detailed_for(category, merchant)))
            added += 1
            # a profile with an opening balance has one that moves with its transactions (plaid-signed: positive is out)
            if profile.opening_cents is not None:
                account = s.get(Account, account_id)
                account.current_balance_cents -= cents
                account.available_balance_cents -= cents
        s.commit()
    return added


def _sim(session_factory, user_id: str) -> dict:
    with session_factory() as s:
        sim = sim_prefs(s, user_id)
    from app.budget_service.alerts import has_real_plaid_item
    with session_factory() as s:
        linked = has_real_plaid_item(s, user_id)
    if not sim or linked:
        raise NotSimulated("Only synthetic users without bank links can be advanced.")
    return sim


def _save_date(session_factory, user_id: str, day: date) -> None:
    with session_factory() as s:
        user = s.get(User, user_id)
        prefs = dict(user.prefs_json or {})
        prefs["sim"] = {**prefs["sim"], "date": day.isoformat()}
        user.prefs_json = prefs           # a new dict, so the json column sees the change
        s.commit()


def _save_progress(session_factory, job_id: int, progress: dict) -> None:
    with session_factory() as s:
        s.get(AgentRun, job_id).summary_json = dict(progress)
        s.commit()


def advance_days(session_factory, user_id: str, days: int, *, model: Optional[BaseChatModel],
                 checkpointer: Optional[BaseCheckpointSaver], job_id: Optional[int] = None,
                 on_day: Optional[Callable[[dict], None]] = None) -> dict:
    sim = _sim(session_factory, user_id)
    users = _profiles()
    every = users.PROFILES + users.PLANTED + users.GOAL_USERS + users.RECURRING_USERS + users.FORECAST_USERS
    profile = next(p for p in every if p.id == sim["profile"])
    # the whole generated history once, grouped by day, instead of regenerating it for every simulated day
    by_day: dict[date, list] = {}
    for n, row in enumerate(users.transactions(profile)):
        by_day.setdefault(row[0], []).append((n, row))
    day = date.fromisoformat(sim["date"])
    progress = {"days_requested": days, "days_done": 0, "start_date": day.isoformat(), "date": day.isoformat(),
                "run_ids": [], "alerts_written": 0}
    for _ in range(days):
        day += timedelta(days=1)
        injected = _inject(session_factory, user_id, users, profile, by_day.get(day, []))
        state = run_daily_locked(session_factory, user_id, day, model=model, checkpointer=checkpointer)
        _save_date(session_factory, user_id, day)        # only after the run, so a failed day can be retried
        alerts = len(state.get("alert_ids") or [])
        progress.update(days_done=progress["days_done"] + 1, date=day.isoformat(),
                        run_ids=progress["run_ids"] + [state["run_id"]],
                        alerts_written=progress["alerts_written"] + alerts)
        if job_id is not None:
            _save_progress(session_factory, job_id, progress)
        if on_day is not None:
            on_day({"date": day.isoformat(), "injected": injected, "status": state["status"], "run_id": state["run_id"],
                    "flags": [f["category"] for f in state.get("flags") or []], "alerts": alerts})
    return progress
