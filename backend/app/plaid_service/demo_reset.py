"""Sandbox demo cleanup shared by the CLI reset and confirmed UI disconnect.

Callers must exclude active writers before resetting. Backups are mandatory.
"""
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select

from app import config
from app.budget_service.alerts import SEED_TOKEN
from app.budget_service.run_lock import now
from app.db.models import (Account, AdviceLog, AgentRun, Alert, BudgetLine, BudgetPeriod, CategoryRule,
                           Goal, KGEdge, KGNode, Liability, PlaidItem, RecurringStream, Transaction,
                           User, UserRunClaim)
from app.security import decrypt

EMAIL = "dev@localhost"


def checked_user(session, *, check_claim=True):
    if config.PLAID_ENV != "sandbox":
        raise ValueError("Reset is only available with PLAID_ENV=sandbox.")
    user = session.scalar(select(User).where(User.email == EMAIL))
    if user is None:
        return None
    if (user.prefs_json or {}).get("sim"):
        raise ValueError("Refusing to reset a simulated user.")
    claim = session.get(UserRunClaim, user.id)
    if check_claim and claim and claim.expires_at.replace(tzinfo=timezone.utc) > now():
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


def reset_user(session, *, check_claim=True):
    # API callers may skip the claim check only while holding/asserting their own claim.
    user = checked_user(session, check_claim=check_claim)
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
