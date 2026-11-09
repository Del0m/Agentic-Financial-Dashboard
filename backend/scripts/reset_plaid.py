"""Reset the local Plaid Sandbox demo user, leaving it ready for a new Link session.

Stop the backend first. Preview: python scripts/reset_plaid.py
Apply: python scripts/reset_plaid.py --yes
Only dev@localhost is reset. A SQLite backup is required and created before deletion.
Old Sandbox Items are not revoked at Plaid; a new Link session starts fresh history.
"""
import argparse
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from sqlalchemy import delete, select

from app import config
from app.budget_service.alerts import SEED_TOKEN
from app.budget_service.run_lock import now
from app.db.models import (Account, AdviceLog, AgentRun, Alert, BudgetLine, BudgetPeriod, CategoryRule,
                           Goal, KGEdge, KGNode, Liability, PlaidItem, RecurringStream, Transaction,
                           User, UserRunClaim)
from app.security import decrypt

EMAIL = "dev@localhost"


def checked_user(session):
    if config.PLAID_ENV != "sandbox":
        raise ValueError("Reset is only available with PLAID_ENV=sandbox.")
    user = session.scalar(select(User).where(User.email == EMAIL))
    if user is None:
        return None
    if (user.prefs_json or {}).get("sim"):
        raise ValueError("Refusing to reset a simulated user.")
    claim = session.get(UserRunClaim, user.id)
    if claim and claim.expires_at.replace(tzinfo=timezone.utc) > now():
        raise ValueError("A run still owns this user. Stop the backend and wait for its claim to expire.")
    if session.scalar(select(AgentRun.id).where(AgentRun.user_id == user.id, AgentRun.status == "running").limit(1)):
        raise ValueError("A run is marked running. Let it finish before stopping the backend and resetting.")
    for item in session.scalars(select(PlaidItem).where(PlaidItem.user_id == user.id)):
        if item.access_token_enc == SEED_TOKEN or not decrypt(item.access_token_enc).startswith("access-sandbox-"):
            raise ValueError("A bank token is not a readable Sandbox token; reset refused.")
    return user


def backup_database(engine) -> Path:
    if engine.url.get_backend_name() != "sqlite" or not engine.url.database or engine.url.database == ":memory:":
        raise ValueError("This demo reset requires a file-backed SQLite database for its backup.")
    source = Path(engine.url.database).resolve()
    backup = source.with_name(source.stem + ".before-plaid-reset-" +
                              datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6] + ".db")
    # Restrict access before copying encrypted tokens and financial history.
    fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(backup) as dst:
            src.backup(dst)
    except Exception:
        backup.unlink(missing_ok=True)
        raise
    return backup


def reset_user(session):
    user = checked_user(session)
    if user:
        uid = user.id
        periods = select(BudgetPeriod.id).where(BudgetPeriod.user_id == uid)
        for model in (KGEdge, KGNode, AdviceLog, Alert, Goal, RecurringStream):
            session.execute(delete(model).where(model.user_id == uid))
        session.execute(delete(BudgetLine).where(BudgetLine.period_id.in_(periods)))
        for model in (BudgetPeriod, Transaction, CategoryRule, Liability, Account, PlaidItem,
                      AgentRun, UserRunClaim):
            session.execute(delete(model).where(model.user_id == uid))
        session.execute(delete(User).where(User.id == uid))
    # A new identity also separates Chat from the old user's checkpoint threads.
    fresh = User(id=str(uuid.uuid4()), email=EMAIL, password_hash="!", prefs_json={})
    session.add(fresh)
    return fresh.id


def main(argv=None, session_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="apply reset after backing up the database")
    args = parser.parse_args(argv)
    if session_factory is None:
        from app.db.session import SessionLocal
        session_factory = SessionLocal
    try:
        with session_factory() as s:
            user = checked_user(s)
            print(f"Target: {EMAIL}; existing user: {'yes' if user else 'no'}")
            print("Clears this user's local bank links, accounts, transactions, budgets, alerts, goals and memory.")
            print("Stop the backend first. The seeded Demo user is preserved. No Plaid Items are revoked remotely.")
            if not args.yes:
                print("Preview only. Pass --yes to back up and reset.")
                return 0
            backup = backup_database(s.get_bind())
            print(f"Database backup: {backup}", flush=True)
            reset_user(s)
            s.commit()
    except Exception as exc:
        # Never echo tokens or raw database errors.
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f"Reset failed: {message}")
        return 1
    print("Reset complete. Restart the backend, reload Dashboard, select Plaid bank and Connect a bank.")
    print("Or reconnect automatically: docker compose exec backend python scripts/sandbox_link.py --dynamic")
    print("Plaid uses today's real date. Run a budget check after the fresh bank history has loaded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
